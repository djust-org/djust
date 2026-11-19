"""The real browser client against the real server, for ``dj-auto-recover`` (#4178).

``tests/js/auto_recover_bridge_helper.mjs`` evaluates the built ``client.js`` in
a jsdom page parsed from the server's real GET document. Its socket, event
stream and fetch only relay: every frame the client sends comes here, goes to
the real transport (a ``WebsocketCommunicator`` on ``LiveViewConsumer``, the
SSE stream and message views, or the view's own page POST), and every frame
that transport answers goes back to the page. Nothing in between builds an
event payload or a server frame.

A reconnect is the transport's own: the server side of the socket closes and
the client reconnects on its timer; the SSE stream drops and the client's
EventSource opens again; the page POST has no reconnect, so recovery is fired
by hand there (the client never runs it on that transport).
"""

from __future__ import annotations

import asyncio
import json
import shutil
import tempfile
from pathlib import Path
from typing import Any, Callable, Optional
from urllib.parse import parse_qs, urlsplit

import pytest
from asgiref.sync import sync_to_async
from channels.testing import WebsocketCommunicator
from django.contrib.auth.models import AnonymousUser
from django.test import RequestFactory

REPO = Path(__file__).resolve().parents[3]
HELPER = "tests/js/auto_recover_bridge_helper.mjs"
ORIGIN = "https://example.com"
WAIT_S = 20.0


def node_or_skip() -> str:
    node = shutil.which("node")
    if not node or not (REPO / "node_modules" / "jsdom").is_dir():
        pytest.skip("node and jsdom are needed for the real-client bridge")
    return node


class Browser:
    """One jsdom page running the built client, driven line by line."""

    def __init__(self, process: asyncio.subprocess.Process, page: Path) -> None:
        self.process = process
        self.page = page
        self.logs: list[dict[str, Any]] = []
        self.backlog: list[dict[str, Any]] = []

    @classmethod
    async def open(cls, html: str, transport: str, url: Optional[str] = None) -> "Browser":
        """``url``: the page's address (``http://testserver/4178/`` by default)."""
        directory = Path(tempfile.mkdtemp(prefix="djust-4178-"))
        page = directory / "page.html"
        page.write_text(html, encoding="utf-8")
        process = await asyncio.create_subprocess_exec(
            node_or_skip(),
            HELPER,
            str(page),
            transport,
            *([url] if url else []),
            cwd=str(REPO),
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            limit=16 * 1024 * 1024,
        )
        browser = cls(process, page)
        await browser.expect(lambda m: m["op"] == "ready", "the client to initialise")
        return browser

    async def _read(self) -> dict[str, Any]:
        assert self.process.stdout is not None
        line = await asyncio.wait_for(self.process.stdout.readline(), WAIT_S)
        if not line:
            stderr = await self.process.stderr.read() if self.process.stderr else b""
            raise AssertionError("the browser exited: %s" % stderr.decode()[-2000:])
        message = json.loads(line)
        if message["op"] == "log":
            self.logs.append(message)
        return message

    async def expect(self, match: Callable[[dict[str, Any]], bool], what: str) -> dict[str, Any]:
        """The next client message ``match`` accepts; others are kept in order."""
        for index, message in enumerate(self.backlog):
            if match(message):
                del self.backlog[index]
                return message
        while True:
            try:
                message = await self._read()
            except asyncio.TimeoutError:
                raise AssertionError("timed out waiting for %s; logs: %r" % (what, self.logs))
            if message["op"] in ("log", "idle"):
                continue
            if match(message):
                return message
            self.backlog.append(message)

    async def tell(self, **command: Any) -> None:
        assert self.process.stdin is not None
        self.process.stdin.write((json.dumps(command) + "\n").encode())
        await self.process.stdin.drain()
        # Each command is acknowledged once the client's microtasks ran.
        while True:
            message = await self._read()
            if message["op"] == "idle":
                return
            if message["op"] != "log":
                self.backlog.append(message)

    async def eval(self, code: str) -> Any:
        await self.tell(op="eval", code=code)
        for index, message in enumerate(self.backlog):
            if message["op"] == "eval_result":
                del self.backlog[index]
                return message["value"]
        raise AssertionError("no eval result")

    async def close(self) -> None:
        if self.process.returncode is None:
            try:
                await self.tell(op="quit")
            except (AssertionError, BrokenPipeError, ConnectionResetError):
                pass
            try:
                await asyncio.wait_for(self.process.wait(), WAIT_S)
            except asyncio.TimeoutError:
                self.process.kill()
        shutil.rmtree(self.page.parent, ignore_errors=True)


# --- WebSocket ---------------------------------------------------------------


async def _pump_socket(browser: Browser, socket_id: int, socket: WebsocketCommunicator) -> None:
    """Every frame the server has queued, to the page, in order."""
    while not await socket.receive_nothing(timeout=0.2, interval=0.01):
        output = await socket.receive_output()
        if output.get("type") == "websocket.close":
            await browser.tell(op="ws_close", id=socket_id, code=output.get("code", 1000))
            return
        await browser.tell(op="ws_message", id=socket_id, data=output["text"])


async def ws_connect(
    browser: Browser, session: Any, consumer: Any
) -> tuple[int, WebsocketCommunicator]:
    """Answer the client's next socket with a real consumer, through its mount."""
    connect = await browser.expect(lambda m: m["op"] == "ws_connect", "the client's socket")
    socket = WebsocketCommunicator(consumer.as_asgi(), "/ws/")
    socket.scope.update(session=session, user=AnonymousUser(), tenant=None)
    assert (await socket.connect())[0]
    await browser.tell(op="ws_open", id=connect["id"])
    await _pump_socket(browser, connect["id"], socket)
    mount = await browser.expect(
        lambda m: m["op"] == "ws_send" and json.loads(m["data"]).get("type") == "mount",
        "the client's mount frame",
    )
    await socket.send_to(text_data=mount["data"])
    await _pump_socket(browser, connect["id"], socket)
    return connect["id"], socket


async def ws_forward(
    browser: Browser, socket_id: int, socket: WebsocketCommunicator, frame: dict
) -> None:
    await socket.send_to(text_data=frame["data"])
    await _pump_socket(browser, socket_id, socket)


# --- SSE -----------------------------------------------------------------------


def _sse_request(method: str, url: str, session: Any, body: Optional[str] = None) -> Any:
    factory = RequestFactory()
    if method == "GET":
        parts = urlsplit(url)
        request = factory.get(
            parts.path,
            data={k: v if len(v) > 1 else v[0] for k, v in parse_qs(parts.query).items()},
            HTTP_ORIGIN=ORIGIN,
            HTTP_HOST="example.com",
            secure=True,
        )
    else:
        request = factory.post(
            url,
            data=body or "{}",
            content_type="application/json",
            HTTP_ORIGIN=ORIGIN,
            HTTP_HOST="example.com",
            secure=True,
        )
    request.user = AnonymousUser()
    request.session = session
    request.tenant = None
    return request


class SSEServer:
    """The real SSE stream and message views for one page."""

    def __init__(self, browser: Browser, session: Any) -> None:
        self.browser = browser
        self.session = session
        self.url: Optional[str] = None
        self.sid: Optional[str] = None
        self.stream: Any = None
        self.reader: Optional[asyncio.Task] = None
        self.inbox: "asyncio.Queue[str]" = asyncio.Queue()

    async def _read(self, stream: Any) -> None:
        while True:
            try:
                chunk = await stream.__anext__()
            except StopAsyncIteration:
                return
            for line in chunk.splitlines():
                if line.startswith("data: "):
                    await self.inbox.put(line[len("data: ") :])

    async def open_stream(self) -> None:
        """The stream GET the page's EventSource makes (again, on a reconnect)."""
        from djust.sse import DjustSSEStreamView

        if self.url is None:
            connect = await self.browser.expect(lambda m: m["op"] == "sse_connect", "the stream")
            self.url = connect["url"]
            self.sid = urlsplit(self.url).path.rstrip("/").rsplit("/", 1)[-1]
        request = await sync_to_async(_sse_request)("GET", self.url, self.session)
        response = await DjustSSEStreamView().get(request, session_id=self.sid)
        assert response.status_code == 200, response.content
        self.stream = response._iterator
        self.reader = asyncio.ensure_future(self._read(self.stream))
        await self.browser.tell(op="sse_open")
        await self.deliver()

    async def deliver(self) -> None:
        """Stream frames the server sent, to the page, until it is quiet."""
        while True:
            # A quiet window over the stream reader's queue: the generator is
            # read by its own task, so nothing here cancels it.
            for _ in range(30):
                if not self.inbox.empty():
                    break
                await asyncio.sleep(0.01)
            if self.inbox.empty():
                return
            await self.browser.tell(op="sse_message", data=self.inbox.get_nowait())

    async def answer_posts(self, until: Callable[[dict], bool]) -> dict:
        """Answer the page's message POSTs until one ``until`` accepts (returned)."""
        from djust.sse import DjustSSEMessageView

        while True:
            post = await self.browser.expect(lambda m: m["op"] == "fetch", "a message POST")
            request = await sync_to_async(_sse_request)(
                "POST", post["url"], self.session, post["body"]
            )
            response = await DjustSSEMessageView().post(request, session_id=self.sid)
            await self.browser.tell(
                op="fetch_reply",
                id=post["id"],
                status=response.status_code,
                body=response.content.decode(),
            )
            await self.deliver()
            body = json.loads(post["body"] or "{}")
            if until(body):
                return body

    async def drop(self) -> None:
        """The stream goes away; the page's EventSource will open it again."""
        if self.reader is not None:
            self.reader.cancel()
            await asyncio.gather(self.reader, return_exceptions=True)
            self.reader = None
        if self.stream is not None:
            await self.stream.aclose()
            self.stream = None
        await self.browser.tell(op="sse_drop")


# --- page POST (HTTP fallback) -------------------------------------------------


def _page_post(view_class: Any, fetch: dict, session: Any) -> tuple[int, str]:
    headers = {
        "HTTP_" + name.upper().replace("-", "_"): value
        for name, value in (fetch.get("headers") or {}).items()
        if name.lower() != "content-type"
    }
    request = RequestFactory().post(
        urlsplit(fetch["url"]).path,
        data=fetch["body"] or "{}",
        content_type="application/json",
        **headers,
    )
    request.user, request.session, request.tenant = AnonymousUser(), session, None
    response = view_class.as_view()(request)
    return response.status_code, response.content.decode()


async def http_answer(browser: Browser, view_class: Any, session: Any) -> dict:
    """Answer the page's next fallback POST with the view's own ``post()``."""
    fetch = await browser.expect(lambda m: m["op"] == "fetch", "the page POST")
    status, body = await sync_to_async(_page_post)(view_class, fetch, session)
    await browser.tell(op="fetch_reply", id=fetch["id"], status=status, body=body)
    return fetch
