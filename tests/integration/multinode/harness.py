"""Two (or more) real server processes behind a client with no stickiness (#4000).

``Cluster`` starts N uvicorn processes of ``tests.integration.multinode.app`` that share one
session store and one channel layer. Each node serves a listening socket this
process bound and keeps, so a node can be SIGKILLed and started again on the
same port. ``Browser`` is one user agent with one cookie jar; every request
and every socket names the node it goes to, as a load balancer without session
affinity may choose any.

Nothing here waits on the wall clock: a node is ready when it says so on stdout
(redis-server when it answers PING, whatever its log says),
a request to a node that is still starting waits in the socket's backlog, and
every receive has a generous hang guard that only ends a broken run.
"""

from __future__ import annotations

import html
import http.client
import json
import os
import queue
import re
import shutil
import signal
import socket
import subprocess
import sys
import tempfile
import threading
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional

from djust._protocol import PROTOCOL_HEADER, PROTOCOL_VERSION

ROOT = Path(__file__).resolve().parents[3]
#: A hang guard, never an expectation: only a broken run waits this long.
HANG_GUARD_S = 120


def _short_tempdir(prefix: str) -> str:
    """A directory short enough for a Unix socket path (104 bytes on macOS)."""
    base = tempfile.gettempdir()
    if len(base) > 60 and os.path.isdir("/tmp"):
        base = "/tmp"
    return tempfile.mkdtemp(prefix=prefix, dir=base)


class _Lines:
    """A child's stdout, line by line, read on a thread."""

    def __init__(self, stream) -> None:
        self.queue: "queue.Queue[Optional[str]]" = queue.Queue()
        self.seen: List[str] = []
        thread = threading.Thread(target=self._pump, args=(stream,), daemon=True)
        thread.start()

    def _pump(self, stream) -> None:
        for raw in iter(stream.readline, ""):
            self.queue.put(raw.rstrip("\n"))
        self.queue.put(None)

    def wait_for(self, prefix: str, what: str) -> str:
        while True:
            try:
                line = self.queue.get(timeout=HANG_GUARD_S)
            except queue.Empty:
                raise AssertionError("hang guard: no %r line from %s" % (prefix, what)) from None
            if line is None:
                raise AssertionError(
                    "%s exited before printing %r; it printed %r" % (what, prefix, self.seen)
                )
            self.seen.append(line)
            if line.startswith(prefix):
                return line


# ---------------------------------------------------------------------------
# Redis (only when redis-server is installed)
# ---------------------------------------------------------------------------


#: How long redis-server may take to answer PING. It is ready in milliseconds; a
#: server that has not answered in this long is broken, and a process that has
#: already exited is reported at once, not after this guard.
REDIS_READY_GUARD_S = 20
#: Longest Unix socket path the kernel accepts, with room to spare (sun_path is
#: 108 bytes on Linux and 104 on macOS, including the terminating NUL).
MAX_UNIX_SOCKET_PATH = 100


class RedisStartError(AssertionError):
    """redis-server did not become ready; the message carries everything it said."""


def _redis_socket_dir(prefix: str = "mnr") -> str:
    """A directory whose ``r.sock`` fits a Unix socket path, under ``/tmp`` if needed."""
    directory = _short_tempdir(prefix)
    if len(os.path.join(directory, "r.sock").encode()) <= MAX_UNIX_SOCKET_PATH:
        return directory
    shutil.rmtree(directory, ignore_errors=True)
    return tempfile.mkdtemp(prefix=prefix, dir="/tmp")


def _free_tcp_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


def _redis_pong(family: int, address: Any) -> bool:
    """One PING; ``True`` when the server answered ``+PONG``."""
    try:
        with socket.socket(family, socket.SOCK_STREAM) as conn:
            conn.settimeout(1.0)
            conn.connect(address)
            conn.sendall(b"PING\r\n")
            return conn.recv(64).startswith(b"+PONG")
    except OSError:
        return False


class _RedisProcess:
    """One redis-server child: its argv, its output files, and readiness by PING."""

    def __init__(self, argv: List[str], directory: str) -> None:
        self.argv = argv
        self.stdout_path = os.path.join(directory, "stdout.txt")
        self.stderr_path = os.path.join(directory, "stderr.txt")
        with open(self.stdout_path, "wb") as out, open(self.stderr_path, "wb") as err:
            # stdin is /dev/null: an inherited, never-closed stdin is one thing a
            # server can block on without saying a word. The output goes to files,
            # not pipes, so a full pipe can never stall it either.
            self.process = subprocess.Popen(argv, stdin=subprocess.DEVNULL, stdout=out, stderr=err)

    def _read(self, path: str) -> str:
        try:
            with open(path, "rb") as handle:
                return handle.read().decode("utf-8", "replace")
        except OSError as exc:
            return "<unreadable: %s>" % exc

    def report(self, why: str) -> str:
        return "redis-server %s\n  argv: %r\n  returncode: %r\n  stdout:\n%s\n  stderr:\n%s" % (
            why,
            self.argv,
            self.process.poll(),
            self._read(self.stdout_path) or "    <empty>",
            self._read(self.stderr_path) or "    <empty>",
        )

    def wait_ready(self, family: int, address: Any, guard_s: float) -> None:
        """Until PING answers; raises at once if the process exits, else after ``guard_s``."""
        deadline = time.monotonic() + guard_s
        while True:
            if self.process.poll() is not None:
                raise RedisStartError(self.report("exited before it answered PING"))
            if _redis_pong(family, address):
                return
            if time.monotonic() >= deadline:
                raise RedisStartError(self.report("did not answer PING within %ss" % guard_s))
            time.sleep(0.05)

    def stop(self) -> None:
        if self.process.poll() is None:
            self.process.kill()
        self.process.wait()


def _redis_argv(executable: str, directory: str) -> List[str]:
    # --logfile "" logs to stdout and --daemonize no keeps it in the foreground,
    # whatever a packaged default would say; nothing persists to disk.
    return [
        executable,
        "--save",
        "",
        "--appendonly",
        "no",
        "--dir",
        directory,
        "--logfile",
        "",
        "--daemonize",
        "no",
    ]


@contextmanager
def redis_server(
    executable: Optional[str] = None, guard_s: float = REDIS_READY_GUARD_S
) -> Iterator[str]:
    """A private redis-server; yields its URL.

    A Unix socket first (``unix://...``); if that server never answers, a TCP
    port on 127.0.0.1 (``redis://...``), so a runner that confines Unix sockets
    still runs the rows. The URL's scheme says which transport was used.
    Readiness is a PING that answers, never a log line, so it does not depend on
    the wording of a Redis version's startup log.
    """
    executable = executable or shutil.which("redis-server")
    if executable is None:
        raise RuntimeError("redis-server is not installed")
    directory = _redis_socket_dir()
    path = os.path.join(directory, "r.sock")
    attempts: List[str] = []
    server: Optional[_RedisProcess] = None
    try:
        server = _RedisProcess(
            _redis_argv(executable, directory) + ["--port", "0", "--unixsocket", path], directory
        )
        try:
            server.wait_ready(socket.AF_UNIX, path, guard_s)
            url = "unix://" + path
        except RedisStartError as unix_failure:
            server.stop()
            attempts.append(str(unix_failure))
            port = _free_tcp_port()
            server = _RedisProcess(
                _redis_argv(executable, directory) + ["--port", str(port), "--bind", "127.0.0.1"],
                directory,
            )
            try:
                server.wait_ready(socket.AF_INET, ("127.0.0.1", port), guard_s)
            except RedisStartError as tcp_failure:
                attempts.append(str(tcp_failure))
                raise RedisStartError(
                    "redis-server answered PING on neither a Unix socket nor TCP:\n\n"
                    + "\n\n".join(attempts)
                ) from None
            url = "redis://127.0.0.1:%d/0" % port
        yield url
    finally:
        if server is not None:
            server.stop()
        shutil.rmtree(directory, ignore_errors=True)


# ---------------------------------------------------------------------------
# Nodes and clusters
# ---------------------------------------------------------------------------


def _child_env(config: Dict[str, Any], name: str, extra: Optional[Dict[str, str]] = None):
    env = os.environ.copy()
    env.pop("DJANGO_SETTINGS_MODULE", None)
    env["DJANGO_SETTINGS_MODULE"] = "tests.integration.multinode.app.settings"
    env["DJUST_MN_CONFIG"] = json.dumps(config)
    env["DJUST_MN_NODE"] = name
    # The parent's own path entries stay (a Django overlay, for one), after ours.
    inherited = [p for p in env.get("PYTHONPATH", "").split(os.pathsep) if p]
    env["PYTHONPATH"] = os.pathsep.join([str(ROOT), str(ROOT / "python"), *inherited])
    env["PYTHONUNBUFFERED"] = "1"
    env.pop("PYTEST_CURRENT_TEST", None)
    env.pop("PYTEST_XDIST_WORKER", None)
    if extra:
        env.update(extra)
    return env


class Node:
    """One server process on a socket this process keeps."""

    def __init__(self, name: str, config: Dict[str, Any], log_dir: str) -> None:
        self.name = name
        self.config = config
        self.log_dir = log_dir
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.sock.bind(("127.0.0.1", 0))
        self.sock.listen(128)
        self.sock.set_inheritable(True)
        self.port = self.sock.getsockname()[1]
        self.process: Optional[subprocess.Popen] = None
        self.lines: Optional[_Lines] = None
        self.pids: List[int] = []
        self.env_extra: Dict[str, str] = {}

    @property
    def pid(self) -> int:
        assert self.process is not None
        return self.process.pid

    def start(self) -> "Node":
        fd = self.sock.fileno()
        stderr = open(os.path.join(self.log_dir, "%s.%d.log" % (self.name, len(self.pids))), "w")
        self.process = subprocess.Popen(
            [sys.executable, "-m", "tests.integration.multinode.app.serve", "--fd", str(fd)],
            cwd=str(ROOT),
            env=_child_env(self.config, self.name, self.env_extra),
            pass_fds=(fd,),
            stdin=subprocess.PIPE,  # closed when this process dies: the node exits
            stdout=subprocess.PIPE,
            stderr=stderr,
            text=True,
            start_new_session=True,
        )
        stderr.close()
        self.pids.append(self.process.pid)
        self.lines = _Lines(self.process.stdout)
        ready = self.lines.wait_for("READY", "node %s" % self.name)
        import django

        # A node runs the Django the test runs (an overlay must reach it too).
        assert ready.split()[-1] == django.get_version(), ready
        return self

    def wait_line(self, prefix: str) -> str:
        assert self.lines is not None
        return self.lines.wait_for(prefix, "node %s" % self.name)

    def terminate(self, timeout: float = 30.0) -> None:
        """SIGTERM, then wait: the graceful stop of a deploy. uvicorn closes
        its sockets (the application sees each disconnect) and exits."""
        if self.process is not None and self.process.poll() is None:
            os.kill(self.process.pid, signal.SIGTERM)
            try:
                self.process.wait(timeout=timeout)
            except subprocess.TimeoutExpired:
                self.kill()
                raise
        if self.process is not None and self.process.stdin is not None:
            self.process.stdin.close()

    def kill(self) -> None:
        """SIGKILL: no shutdown code runs, as in an OOM kill or a lost host."""
        if self.process is not None and self.process.poll() is None:
            os.kill(self.process.pid, signal.SIGKILL)
            self.process.wait()
        if self.process is not None and self.process.stdin is not None:
            self.process.stdin.close()

    def close(self) -> None:
        self.kill()
        self.sock.close()

    def log(self) -> str:
        out = []
        for i in range(len(self.pids)):
            path = os.path.join(self.log_dir, "%s.%d.log" % (self.name, i))
            if os.path.exists(path):
                with open(path) as fh:
                    out.append(fh.read())
        return "\n".join(out)


class Cluster:
    """N nodes sharing one session store and one channel layer."""

    def __init__(
        self,
        session: str,
        layer: str,
        redis: Optional[str],
        n: int = 2,
        node_env: Optional[Dict[str, Dict[str, str]]] = None,
        store: Optional[str] = None,
        save_interval: float = 0,
        server: str = "uvicorn",
        lifespan: bool = False,
    ) -> None:
        self.node_env = node_env or {}
        self.dir = _short_tempdir("mnc")
        self.config = {
            "session": session,
            "layer": layer,
            "db": os.path.join(self.dir, "db.sqlite3"),
            "cache_dir": os.path.join(self.dir, "cache"),
            "redis": redis,
            # The state store every node names (#4132): None, "sealed" or "file".
            "store": store,
            # DJUST_STATE_SAVE_INTERVAL of every node (0: save every turn).
            "save_interval": save_interval,
            # The ASGI server of every node (uvicorn, daphne, hypercorn), and
            # whether the application answers lifespan (djust.lifespan).
            "server": server,
            "lifespan": lifespan,
        }
        os.makedirs(self.config["cache_dir"])
        self.names = [chr(ord("A") + i) for i in range(n)]
        self.nodes: Dict[str, Node] = {}

    def __enter__(self) -> "Cluster":
        try:
            setup = subprocess.run(
                [sys.executable, "-m", "tests.integration.multinode.app.serve", "--setup"],
                cwd=str(ROOT),
                env=_child_env(self.config, "setup"),
                capture_output=True,
                text=True,
                timeout=HANG_GUARD_S,
            )
            if setup.returncode != 0:
                raise RuntimeError("harness setup failed:\n" + setup.stderr)
            for name in self.names:
                node = Node(name, self.config, self.dir)
                node.env_extra.update(self.node_env.get(name, {}))
                self.nodes[name] = node.start()
        except BaseException:
            self.__exit__(None, None, None)
            raise
        return self

    def __exit__(self, *exc) -> None:
        for node in self.nodes.values():
            node.close()
        shutil.rmtree(self.dir, ignore_errors=True)

    def __getitem__(self, name: str) -> Node:
        return self.nodes[name]

    def logs(self) -> str:
        return "\n".join("--- %s ---\n%s" % (n, node.log()) for n, node in self.nodes.items())

    def stats(self, name: str, reset: bool = False) -> Dict[str, int]:
        status, body, _ = Browser(self).get(name, "/_mn/stats/" + ("?reset=1" if reset else ""))
        assert status == 200, body
        return json.loads(body)


# ---------------------------------------------------------------------------
# A browser that picks a node per request
# ---------------------------------------------------------------------------

_PROBE_RE = re.compile(r'<pre[^>]*id="probe"[^>]*>(.*?)</pre>', re.S)


def probe_of(markup: str) -> Dict[str, Any]:
    """The JSON snapshot a harness view rendered into ``<pre id="probe">``."""
    found = _PROBE_RE.search(markup or "")
    assert found, "no probe in %r" % (markup or "")[:500]
    return json.loads(html.unescape(found.group(1)))


class Browser:
    """One user agent (one cookie jar); each call names the node it reaches."""

    def __init__(self, cluster: Cluster, headers: Optional[Dict[str, str]] = None) -> None:
        self.cluster = cluster
        self.cookies: Dict[str, str] = {}
        # client.js names its wire protocol on every POST and the SSE GET (#3658).
        self.headers = {PROTOCOL_HEADER: str(PROTOCOL_VERSION), **dict(headers or {})}
        # The tab id each page load carried (#4106), by path: what client.js
        # reads from the page and sends back on its mount and its POSTs.
        self.tabs: Dict[str, str] = {}

    def _cookie_header(self) -> str:
        return "; ".join("%s=%s" % kv for kv in self.cookies.items())

    def _absorb(self, response: http.client.HTTPResponse) -> None:
        for header in response.headers.get_all("Set-Cookie") or []:
            pair = header.split(";", 1)[0]
            name, _, value = pair.partition("=")
            if value in ("", '""'):
                self.cookies.pop(name.strip(), None)
            else:
                self.cookies[name.strip()] = value.strip()

    def request(self, node: str, method: str, path: str, body=None, headers=None):
        port = self.cluster[node].port
        conn = http.client.HTTPConnection("127.0.0.1", port, timeout=HANG_GUARD_S)
        all_headers = dict(self.headers)
        all_headers.update(headers or {})
        if self.cookies:
            all_headers["Cookie"] = self._cookie_header()
        try:
            conn.request(method, path, body=body, headers=all_headers)
            response = conn.getresponse()
            data = response.read().decode("utf-8", "replace")
            self._absorb(response)
            return response.status, data, response
        finally:
            conn.close()

    def get(self, node: str, path: str, headers=None):
        answer = self.request(node, "GET", path, headers=headers)
        found = re.search(r'<meta name="djust-tab" content="([^"]+)">', answer[1])
        if found:
            self.tabs[path.split("?")[0]] = found.group(1)
        return answer

    def tab(self, path: str) -> str:
        """The tab id of this browser's latest load of ``path``."""
        return self.tabs.get(path.split("?")[0], "")

    def post_event(self, node: str, path: str, event: str, params=None):
        """The HTTP fallback's event POST, as client.js sends it."""
        body = json.dumps({"event": event, "params": params or {}})
        status, data, _ = self.request(
            node,
            "POST",
            path,
            body=body,
            headers={"Content-Type": "application/json", "X-Djust-Tab": self.tab(path)},
        )
        try:
            payload = json.loads(data)
        except ValueError:
            payload = {"_raw": data}
        return status, payload

    def socket(self, node: str) -> "Socket":
        return Socket(self, node)

    def sse(self, node: str, view: str, url: str) -> "SSEStream":
        return SSEStream(self, node, view, url)


class Socket:
    """A djust WebSocket to one node."""

    def __init__(self, browser: Browser, node: str) -> None:
        from websockets.sync.client import connect

        self.browser = browser
        self.node = node
        port = browser.cluster[node].port
        headers = dict(browser.headers)
        if browser.cookies:
            headers["Cookie"] = browser._cookie_header()
        headers["Origin"] = "http://127.0.0.1:%d" % port
        self.ws = connect(
            "ws://127.0.0.1:%d/ws/live/" % port,
            additional_headers=headers,
            open_timeout=HANG_GUARD_S,
            max_size=None,
        )
        self.frames: List[Dict[str, Any]] = []
        self.closed: Optional[int] = None
        self._ref = 0

    def recv(self) -> Optional[Dict[str, Any]]:
        from websockets.exceptions import ConnectionClosed

        try:
            raw = self.ws.recv(timeout=HANG_GUARD_S)
        except ConnectionClosed as exc:
            self.closed = exc.rcvd.code if exc.rcvd else 1006
            return None
        except TimeoutError:
            raise AssertionError("hang guard: no frame from node %s" % self.node) from None
        if isinstance(raw, bytes):
            raw = raw.decode("utf-8", "replace")
        frame = json.loads(raw)
        self.frames.append(frame)
        return frame

    def until(self, predicate) -> List[Dict[str, Any]]:
        """Frames up to and including the first one ``predicate`` accepts."""
        got: List[Dict[str, Any]] = []
        while True:
            frame = self.recv()
            if frame is None:
                return got
            got.append(frame)
            if predicate(frame):
                return got

    def drain(self, quiet: float) -> List[Dict[str, Any]]:
        """Every frame that arrives until none has for ``quiet`` seconds."""
        from websockets.exceptions import ConnectionClosed

        got: List[Dict[str, Any]] = []
        while True:
            try:
                raw = self.ws.recv(timeout=quiet)
            except (TimeoutError, ConnectionClosed):
                return got
            if isinstance(raw, bytes):
                raw = raw.decode("utf-8", "replace")
            frame = json.loads(raw)
            self.frames.append(frame)
            got.append(frame)

    def send(self, frame: Dict[str, Any]) -> None:
        self.ws.send(json.dumps(frame))

    def mount(
        self, view: str, url: str, *, reconnect_from: "Optional[Socket]" = None, **extra
    ) -> List[Dict[str, Any]]:
        """Mount as client.js does after a page load: ``url`` is the path,
        the query string travels as ``params``, the page is pre-rendered. With
        ``reconnect_from``, as a reconnect: the frame says so and carries the
        signed client-state token the previous socket last received."""
        from urllib.parse import parse_qsl, urlsplit

        parts = urlsplit(url)
        frame = {
            "type": "mount",
            "view": view,
            "params": dict(parse_qsl(parts.query)),
            "url": parts.path,
            "has_prerendered": True,
            "tab": self.browser.tab(parts.path),
            "protocol": PROTOCOL_VERSION,
        }
        if reconnect_from is not None:
            frame["reconnect"] = True
            token = reconnect_from.snapshot_token
            if token:
                frame["state_snapshot"] = {"view_slug": view, "state_json": token}
            # The turn serial the page was last shown (#4164), as client.js sends it.
            serial = reconnect_from.state_serial
            if serial:
                frame["state_serial"] = serial
        frame.update(extra)
        self.send(frame)
        return self.until(lambda f: f.get("type") in ("mount", "error"))

    @property
    def snapshot_token(self) -> Optional[str]:
        """The newest ``state_snapshot_signed`` this socket received."""
        token = None
        for frame in self.frames:
            if "state_snapshot_signed" in frame:
                token = frame["state_snapshot_signed"]
        return token

    @property
    def state_serial(self) -> Optional[int]:
        """The newest ``state_serial`` this socket received (#4164)."""
        serial = None
        for frame in self.frames:
            if type(frame.get("state_serial")) is int:
                serial = frame["state_serial"]
        return serial

    def event(self, name: str, params=None, **extra) -> List[Dict[str, Any]]:
        """Send one event; frames until the answer to it (matched by ``ref``)."""
        self._ref += 1
        ref = self._ref
        frame = {"type": "event", "event": name, "params": params or {}, "ref": ref}
        frame.update(extra)
        self.send(frame)
        return self.until(
            lambda f: (
                f.get("ref") == ref
                and f.get("type") not in ("push_event",)
                or f.get("type") == "error"
            )
        )

    def close(self) -> None:
        try:
            self.ws.close()
        except Exception:  # noqa: BLE001 - a socket to a killed node is already gone
            pass


class SSEStream:
    """A djust SSE stream (the client's EventSource) to one node (#3885).

    The stream GET mounts ``view`` as client.js does on the SSE fallback; its
    ``data:`` lines are the same protocol frames a socket receives.
    """

    def __init__(self, browser: Browser, node: str, view: str, url: str) -> None:
        import uuid
        from urllib.parse import urlencode

        self.node = node
        self.session_id = str(uuid.uuid4())
        port = browser.cluster[node].port
        headers = dict(browser.headers)
        if browser.cookies:
            headers["Cookie"] = browser._cookie_header()
        headers["Accept"] = "text/event-stream"
        query = urlencode(
            {
                "view": view,
                "_djust_url": url,
                "_djust_tab": browser.tab(url),
                "protocol": PROTOCOL_VERSION,
            }
        )
        self.conn = http.client.HTTPConnection("127.0.0.1", port, timeout=HANG_GUARD_S)
        self.conn.request("GET", "/djust/sse/%s/?%s" % (self.session_id, query), headers=headers)
        self.response = self.conn.getresponse()
        assert self.response.status == 200, self.response.read()[:300]
        self.frames: List[Dict[str, Any]] = []

    def recv(self) -> Optional[Dict[str, Any]]:
        """The next ``data:`` frame (keepalive comments skipped); ``None`` at the end."""
        while True:
            try:
                line = self.response.fp.readline()
            except (TimeoutError, socket.timeout):
                raise AssertionError("hang guard: no SSE frame from node %s" % self.node) from None
            if not line:
                return None
            text = line.decode("utf-8", "replace").rstrip("\r\n")
            if text.startswith("data: "):
                frame = json.loads(text[len("data: ") :])
                self.frames.append(frame)
                return frame

    def until(self, predicate) -> List[Dict[str, Any]]:
        """Frames up to and including the first one ``predicate`` accepts."""
        got: List[Dict[str, Any]] = []
        while True:
            frame = self.recv()
            if frame is None:
                return got
            got.append(frame)
            if predicate(frame):
                return got

    def close(self) -> None:
        try:
            self.conn.close()
        except Exception:  # noqa: BLE001 - a stream to a killed node is already gone
            pass


def probe_from(frames: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """The last probe a list of WebSocket frames carries: from a frame's full
    ``html``, or from the ``SetText`` patch that rewrote the probe's text."""
    found = None
    for frame in frames:
        if frame.get("html") and 'id="probe"' in frame["html"]:
            found = probe_of(frame["html"])
        for patch in frame.get("patches") or ():
            text = patch.get("text") if isinstance(patch, dict) else None
            if patch.get("type") == "SetText" and isinstance(text, str) and '"pid"' in text:
                found = json.loads(text)
    return found


def kinds(frames: List[Dict[str, Any]]) -> List[str]:
    """The ``type`` of each frame, without ``connect`` and effects-only frames.

    A ``render`` frame (#3658) is named by its DOM part's 1.x name (``patch``,
    ``html_update``, ``embedded_update``, ``noop``); one with no DOM part and
    no ``ref`` only carries effects (the old push frames) and is left out.
    """
    from djust.tests._frames import is_ack, legacy_type

    return [
        legacy_type(f)
        for f in frames
        if f.get("type") != "connect" and not (is_ack(f) and "ref" not in f)
    ]
