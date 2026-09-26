"""``djust serve --loops N`` / ``djust.multiloop.serve`` (#3128).

N uvicorn servers, each on its own event loop in its own thread, share one
listening socket. One loop is plain ``uvicorn.run``. The end-to-end tests run
the launcher in a subprocess, because it owns the process's signal handlers.
"""

from __future__ import annotations

import os
import signal
import socket
import subprocess
import sys
import textwrap
import threading
import time
import urllib.request
from pathlib import Path

import pytest

from djust import multiloop
from djust.multiloop import MultiLoopError, serve

pytest.importorskip("uvicorn")

REPO_PYTHON = str(Path(__file__).resolve().parents[2])


async def _app(scope, receive, send):  # pragma: no cover - never served
    pass


def test_one_loop_is_plain_uvicorn_run(monkeypatch):
    import uvicorn

    calls = []
    monkeypatch.setattr(uvicorn, "run", lambda app, **kw: calls.append((app, kw)))
    monkeypatch.setattr(multiloop, "free_threaded", lambda: False)  # irrelevant for 1 loop
    assert serve("mod:app", port=8123, ws="websockets") == 0
    assert calls == [("mod:app", {"port": 8123, "ws": "websockets"})]
    assert multiloop.loop_count() == 0 and not multiloop.is_multi_loop()


@pytest.mark.parametrize("bad", [0, -1, "two", None])
def test_loops_must_be_a_positive_integer(bad):
    with pytest.raises(ValueError, match="loops"):
        serve(_app, loops=bad)


def test_several_loops_are_refused_with_the_gil(monkeypatch):
    monkeypatch.setattr(multiloop, "free_threaded", lambda: False)
    with pytest.raises(MultiLoopError, match="GIL is enabled"):
        serve(_app, loops=2, port=0)


@pytest.mark.parametrize("kwargs", [{"reload": True}, {"workers": 2}])
def test_reload_and_workers_are_refused(monkeypatch, kwargs):
    monkeypatch.setattr(multiloop, "free_threaded", lambda: True)
    with pytest.raises(MultiLoopError):
        serve(_app, loops=2, **kwargs)


def _use_layer(settings, backend):
    from channels.layers import channel_layers

    settings.CHANNEL_LAYERS = {"default": {"BACKEND": backend}}
    channel_layers.backends.pop("default", None)


@pytest.fixture
def restore_layers():
    from channels.layers import channel_layers

    yield
    channel_layers.backends.pop("default", None)


@pytest.mark.parametrize(
    "backend", ["channels.layers.InMemoryChannelLayer", "djust.layers.InMemoryChannelLayer"]
)
def test_a_loop_bound_channel_layer_is_refused_before_binding(
    settings, monkeypatch, restore_layers, backend
):
    _use_layer(settings, backend)
    monkeypatch.setattr(multiloop, "free_threaded", lambda: True)
    bound = []
    import uvicorn

    monkeypatch.setattr(uvicorn.Config, "bind_socket", lambda self: bound.append(1))
    with pytest.raises(MultiLoopError, match="MultiLoopInMemoryChannelLayer"):
        serve(_app, loops=2, lifespan="off", log_level="warning")
    assert bound == [], "the socket was bound before the layer check"


class _MyInMemoryLayer(__import__("djust.layers", fromlist=["x"]).InMemoryChannelLayer):
    """An app's subclass of a loop-bound layer."""


class _RedisCoreLookalike:
    """Stands in for channels_redis.core.RedisChannelLayer (not installed here)."""

    def __init__(self, **kwargs):
        pass


_RedisCoreLookalike.__module__ = "channels_redis.core"
_RedisCoreLookalike.__qualname__ = "RedisChannelLayer"


def test_a_subclass_of_a_loop_bound_layer_and_channels_redis_core_are_refused(
    settings, restore_layers
):
    from channels.layers import channel_layers

    _use_layer(settings, "djust.tests.test_multiloop_serve_3128._MyInMemoryLayer")
    assert len(multiloop.check_channel_layers()) == 1
    settings.CHANNEL_LAYERS = {"default": {"BACKEND": "x.y"}}
    channel_layers.backends["default"] = _RedisCoreLookalike()
    unsafe = multiloop.check_channel_layers()
    assert unsafe == ["CHANNEL_LAYERS['default'] = channels_redis.core.RedisChannelLayer"]


def test_the_multi_loop_layer_passes_the_check(settings, restore_layers):
    _use_layer(settings, "djust.layers.MultiLoopInMemoryChannelLayer")
    assert multiloop.check_channel_layers() == []


def test_an_unknown_layer_is_a_warning(settings, restore_layers, caplog):
    _use_layer(settings, "djust.tests.test_multiloop_serve_3128._OtherLayer")
    with caplog.at_level("WARNING", logger="djust.multiloop"):
        assert multiloop.check_channel_layers() == []
    assert "not known to be safe" in caplog.text


class _OtherLayer:
    def __init__(self, **kwargs):
        pass


def test_the_check_creates_each_layer_once_before_the_loops_start(settings, restore_layers):
    """Two loops creating the alias on first use could each get a layer."""
    from channels.layers import channel_layers

    _use_layer(settings, "djust.layers.MultiLoopInMemoryChannelLayer")
    multiloop.check_channel_layers()
    first = channel_layers.backends["default"]
    multiloop.check_channel_layers()
    assert channel_layers.backends["default"] is first


# ---------------------------------------------------------------------------
# End to end, in a subprocess
# ---------------------------------------------------------------------------

_APP = textwrap.dedent(
    """
    import asyncio, os, threading, time

    STARTS = []
    # The loops start concurrently on separate threads: the append and the
    # count must be one step, or two loops can both see 3 and none fails (#3215).
    _STARTS_LOCK = threading.Lock()
    FAIL_ON = int(os.environ.get("FAIL_STARTUP_ON", "0"))

    async def app(scope, receive, send):
        if scope["type"] == "lifespan":
            while True:
                m = await receive()
                if m["type"] == "lifespan.startup":
                    with _STARTS_LOCK:
                        STARTS.append(threading.current_thread().name)
                        n = len(STARTS)
                    print("startup on", threading.current_thread().name, flush=True)
                    if FAIL_ON and n == FAIL_ON:
                        await send({"type": "lifespan.startup.failed", "message": "no"})
                        return
                    await send({"type": "lifespan.startup.complete"})
                elif m["type"] == "lifespan.shutdown":
                    print("shutdown on", threading.current_thread().name, flush=True)
                    await send({"type": "lifespan.shutdown.complete"})
                    return
        if scope["path"] == "/block":
            time.sleep(1.5)  # blocks THIS loop only
        if scope["path"] == "/hang":
            await asyncio.sleep(60)  # an in-flight request that never ends
        body = threading.current_thread().name.encode()
        await send({"type": "http.response.start", "status": 200,
                    "headers": [(b"content-type", b"text/plain")]})
        await send({"type": "http.response.body", "body": body})
    """
)


def _free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _start(tmp_path, loops, extra_env=None, extra_args=()):
    (tmp_path / "mlapp.py").write_text(_APP)
    port = _free_port()
    env = {**os.environ, "PYTHONPATH": REPO_PYTHON, **(extra_env or {})}
    env.pop("DJANGO_SETTINGS_MODULE", None)
    proc = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "djust",
            "serve",
            "mlapp:app",
            "--loops",
            str(loops),
            "--port",
            str(port),
            "--app-dir",
            str(tmp_path),
            "--lifespan",
            "on",
            "--log-level",
            "info",
            "--allow-gil",
            *extra_args,
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        env=env,
    )
    return proc, port


def _get(port, path="/", timeout=5.0):
    with urllib.request.urlopen(f"http://127.0.0.1:{port}{path}", timeout=timeout) as r:
        return r.read().decode()


def _wait_up(proc, port, timeout=20.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if proc.poll() is not None:
            raise AssertionError(f"server exited early:\n{proc.stdout.read()}")
        try:
            return _get(port, timeout=1)
        except OSError:
            time.sleep(0.1)
    proc.kill()
    raise AssertionError(f"server did not come up:\n{proc.stdout.read()}")


def _stop(proc, sig=signal.SIGTERM, timeout=20):
    proc.send_signal(sig)
    try:
        out, _ = proc.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        proc.kill()
        out, _ = proc.communicate()
        raise AssertionError(f"server did not stop on {sig!r}:\n{out}")
    return proc.returncode, out


@pytest.mark.parametrize("sig", [signal.SIGTERM, signal.SIGINT])
def test_two_loops_share_one_socket_run_lifespan_each_and_stop_gracefully(tmp_path, sig):
    proc, port = _start(tmp_path, 2)
    _wait_up(proc, port)
    # A request that blocks its loop for 1.5 s; while it does, the other loop
    # must still answer on the same port.
    result = {}

    def blocked():
        result["blocked"] = _get(port, "/block")

    t = threading.Thread(target=blocked)
    t.start()
    time.sleep(0.3)
    started = time.monotonic()
    other = _get(port, timeout=5)
    elapsed = time.monotonic() - started
    t.join(10)
    assert {result["blocked"], other} == {"djust-loop-0", "djust-loop-1"}, (result, other)
    assert elapsed < 1.0, "the second loop did not accept while the first was blocked"

    code, out = _stop(proc, sig)
    assert code == 0, out
    assert out.count("startup on djust-loop-") == 2, out
    assert out.count("shutdown on djust-loop-") == 2, out


def test_a_second_signal_forces_the_exit_while_a_request_hangs(tmp_path):
    proc, port = _start(tmp_path, 2)
    _wait_up(proc, port)

    def hang():
        try:
            _get(port, "/hang", timeout=70)
        except OSError:
            pass

    threading.Thread(target=hang, daemon=True).start()
    time.sleep(0.5)
    proc.send_signal(signal.SIGTERM)
    time.sleep(1.5)
    assert proc.poll() is None, "the first signal did not wait for the in-flight request"
    code, out = _stop(proc, signal.SIGTERM, timeout=15)
    assert code == 0, out
    assert "Waiting for connections to close" in out, out


def test_one_loop_reaching_limit_max_requests_stops_the_process(tmp_path):
    proc, port = _start(tmp_path, 2, extra_args=("--limit-max-requests", "1"))
    _wait_up(proc, port)  # one request: its loop reaches the limit
    try:
        out, _ = proc.communicate(timeout=20)
    except subprocess.TimeoutExpired:
        proc.kill()
        out, _ = proc.communicate()
        raise AssertionError(f"the other loop kept the process running:\n{out}")
    assert proc.returncode == 0, out
    assert "Maximum request limit" in out, out
    assert out.count("shutdown on djust-loop-") == 2, out


def test_a_unix_socket_is_removed_on_exit_so_a_restart_can_bind(tmp_path):
    import tempfile

    (tmp_path / "mlapp.py").write_text(_APP)
    # AF_UNIX paths are short on macOS: keep the socket out of tmp_path.
    uds = os.path.join(tempfile.mkdtemp(prefix="ml"), "s.sock")
    env = {**os.environ, "PYTHONPATH": REPO_PYTHON}
    env.pop("DJANGO_SETTINGS_MODULE", None)
    cmd = [
        sys.executable, "-m", "djust", "serve", "mlapp:app", "--loops", "2",
        "--uds", uds, "--app-dir", str(tmp_path), "--lifespan", "off", "--allow-gil",
    ]  # fmt: skip
    for _ in range(2):  # the second start must bind the same path
        proc = subprocess.Popen(
            cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, env=env
        )
        deadline = time.monotonic() + 20
        while not os.path.exists(uds) and time.monotonic() < deadline:
            if proc.poll() is not None:
                raise AssertionError(proc.stdout.read())
            time.sleep(0.1)
        code, out = _stop(proc)
        assert code == 0, out
        assert not os.path.exists(uds), "the UNIX socket file was left behind"


def test_the_test_app_fails_exactly_one_startup_under_concurrent_loops(monkeypatch):
    """#3215: the test app itself must pick exactly one failing loop.

    Three loops run lifespan startup at once. A barrier in ``print`` holds
    all three threads after their append, the worst interleaving the race
    allows. The counted startups must still fail exactly one of them.
    Without the lock around append-plus-count, all three read 3 and none
    fails, which is how the test below timed out in CI."""
    import asyncio

    monkeypatch.setenv("FAIL_STARTUP_ON", "2")
    namespace: dict = {}
    exec(compile(_APP, "mlapp.py", "exec"), namespace)
    barrier = threading.Barrier(3, timeout=5)

    def held_print(*args, **kwargs):
        if args[:1] != ("startup on",):
            return
        try:
            barrier.wait()
        except threading.BrokenBarrierError:
            pass

    namespace["print"] = held_print
    outcomes: list = []

    def one_loop():
        async def run():
            messages = iter([{"type": "lifespan.startup"}, {"type": "lifespan.shutdown"}])

            async def receive():
                return next(messages)

            async def send(message):
                outcomes.append(message["type"])

            await namespace["app"]({"type": "lifespan"}, receive, send)

        asyncio.run(run())

    threads = [threading.Thread(target=one_loop) for _ in range(3)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(10)
    assert not any(t.is_alive() for t in threads), "a loop's lifespan startup hung"
    startups = sorted(o for o in outcomes if o.startswith("lifespan.startup"))
    assert startups == [
        "lifespan.startup.complete",
        "lifespan.startup.complete",
        "lifespan.startup.failed",
    ], outcomes


def test_one_failed_startup_stops_every_loop_with_exit_code_3(tmp_path):
    proc, port = _start(tmp_path, 3, {"FAIL_STARTUP_ON": "2"})
    try:
        out, _ = proc.communicate(timeout=30)
    except subprocess.TimeoutExpired:
        proc.kill()
        out, _ = proc.communicate()
        raise AssertionError(f"the other loops kept running:\n{out}")
    assert proc.returncode == multiloop.STARTUP_FAILURE, out


def test_the_cli_reports_a_refusal_without_a_traceback(tmp_path):
    (tmp_path / "mlapp.py").write_text(_APP)
    env = {**os.environ, "PYTHONPATH": REPO_PYTHON}
    env.pop("DJANGO_SETTINGS_MODULE", None)
    run = subprocess.run(
        [
            sys.executable,
            "-m",
            "djust",
            "serve",
            "mlapp:app",
            "--loops",
            "0",
            "--app-dir",
            str(tmp_path),
        ],
        capture_output=True,
        text=True,
        env=env,
        timeout=60,
    )
    assert run.returncode == 2
    assert "loops must be an integer" in run.stderr and "Traceback" not in run.stderr
