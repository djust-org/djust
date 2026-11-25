"""#4201: the turn kinds the ``reauth`` built-in used to gate, on the one path left.

With the legacy arm of every turn gone, each turn of a view is authorized
against the session the server stores (ADR-038 D-l). The turn-kind matrix of
#4201 names an existing test for most cells; these are the cells no test
covered. Each case runs one turn first and asserts it ran (the control), then
revokes the authority and asserts the turn is refused:

* an SSE mount from a browser whose stored session was deleted;
* a WebSocket ``url_change`` after the stored session was deleted
  (``permission_denied``, close 4403) and an SSE ``url_change`` whose
  ``check_permissions`` now denies;
* a background task's start, refused when ``check_permissions`` denies by the
  time it starts (WebSocket);
* a background task's result, refused once the stored session is deleted
  (WebSocket and SSE);
* an event queued on a hidden activity and released by a ``db_notify`` turn,
  refused when the session is deleted before the release (WebSocket);
* the frames the server sends without running a handler: a presence frame, a
  ``push_event_to_view`` frame and a recovery render (``request_html``), each
  refused once the stored session is deleted (WebSocket). A presence turn is
  authorized at most once per ``PRESENCE_AUTH_INTERVAL_S``; SSE carries none
  of the three.
"""

import asyncio

import pytest
from asgiref.sync import sync_to_async
from channels.layers import get_channel_layer
from django.contrib.auth import get_user_model
from django.contrib.sessions.backends.db import SessionStore
from django.test import override_settings
from django.urls import include, path

from djust import LiveView, event_handler, sse, state, websocket
from djust.sse import sse_urlpatterns
from djust.testing import open_live_page

pytestmark = [pytest.mark.django_db(transaction=True)]

RAN: list = []
ALLOW: dict = {"value": True}
GATE: dict = {"gate": None}


class _Base(LiveView):
    exposure_policy = "explicit"
    login_required = True
    template = "<div dj-root><b>{{ n }}</b></div>"
    n = state(0)

    def check_permissions(self, request):
        return ALLOW["value"]


class ParamsPage(_Base):
    def handle_params(self, params, uri):
        q = params.get("q")
        if q:
            RAN.append(("params", q))
            self.n = int(q)


class AsyncPage(_Base):
    @event_handler()
    def go(self, **kwargs):
        RAN.append("go")
        self.start_async(self._work)

    @event_handler()
    def go_then_deny(self, **kwargs):
        # The event itself was authorized; the background task starts later,
        # under its own fresh authorization, which now denies.
        RAN.append("go")
        ALLOW["value"] = False
        self.start_async(self._work)

    async def _work(self):
        RAN.append("work")
        gate = GATE["gate"]
        if gate is not None:
            await gate.wait()
        return 7

    def handle_async_result(self, name, result=None, error=None):
        RAN.append("result")
        if error is None:
            self.n = result


class DeferredPage(_Base):
    _listen_channels = {"turns4201"}

    def mount(self, request, **kwargs):
        self.set_activity_visible("panel", False)

    def handle_info(self, message):
        if (message.get("payload") or {}).get("revoke"):
            # The db_notify turn itself was authorized; the session goes before
            # the queued event is released.
            SessionStore(self.request.session.session_key).delete()
        self.set_activity_visible("panel", True)

    @event_handler()
    def bump(self, **kwargs):
        RAN.append("bump")
        self.n += 1


urlpatterns = [
    path("params/", ParamsPage.as_view()),
    path("async/", AsyncPage.as_view()),
    path("deferred/", DeferredPage.as_view()),
    path("djust/", include(sse_urlpatterns)),
]

SETTINGS = {
    "ROOT_URLCONF": __name__,
    "ALLOWED_HOSTS": ["*"],
    "DEBUG": False,
    "LIVEVIEW_ALLOWED_MODULES": [__name__],
    "DJUST_TENANTS": None,
}


@pytest.fixture(autouse=True)
def _setup(monkeypatch):
    monkeypatch.setattr(sse, "_SESSION_LINGER_S", 0)
    RAN.clear()
    ALLOW["value"] = True
    GATE["gate"] = None
    with override_settings(**SETTINGS):
        yield


_COUNT = [0]


async def _page(url):
    _COUNT[0] += 1
    name = "k4201_%d" % _COUNT[0]
    user = await sync_to_async(get_user_model().objects.create_user)(name, password="x")
    return await sync_to_async(open_live_page)(url, user=user)


async def _until(predicate, what, timeout=5.0):
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while not predicate():
        assert loop.time() < deadline, "timed out waiting for %s: %r" % (what, RAN)
        await asyncio.sleep(0.02)


async def _refused(transport):
    """The ``permission_denied`` error the refusal sends."""
    frame = await transport.receive(("error",))
    assert frame.get("code") == "permission_denied", frame
    return frame


async def _closed_4403(socket):
    with pytest.raises(AssertionError, match=r"closed the socket \(code 4403\)"):
        await socket.receive(timeout=5.0)


# -- mount -------------------------------------------------------------------


async def test_an_sse_mount_from_a_revoked_session_is_refused():
    page = await _page("/params/")
    async with await page.connect_sse() as stream:
        assert stream.mount["type"] == "mount", stream.mount  # the control
    await sync_to_async(page.session.delete)()
    try:
        stream = await page.connect_sse()
    except AssertionError as refused:
        # The stream GET itself was refused (a login redirect or a 403).
        assert "answered" in str(refused), refused
        return
    async with stream:
        assert stream.mount["type"] != "mount", stream.mount


# -- params (url_change) -----------------------------------------------------


async def test_a_ws_url_change_after_the_session_is_revoked_is_denied():
    page = await _page("/params/")
    async with await page.connect() as socket:
        await socket.send({"type": "url_change", "params": {"q": "1"}, "uri": "/params/?q=1"})
        await _until(lambda: ("params", "1") in RAN, "the control url_change")
        await sync_to_async(page.session.delete)()
        await socket.send({"type": "url_change", "params": {"q": "2"}, "uri": "/params/?q=2"})
        await _refused(socket)
        await _closed_4403(socket)
    assert ("params", "2") not in RAN, "handle_params ran for a revoked session"


async def test_an_sse_url_change_whose_check_now_denies_is_refused():
    page = await _page("/params/")
    async with await page.connect_sse() as stream:
        await stream.send({"type": "url_change", "params": {"q": "1"}, "uri": "/params/?q=1"})
        await _until(lambda: ("params", "1") in RAN, "the control url_change")
        ALLOW["value"] = False
        await stream.send({"type": "url_change", "params": {"q": "2"}, "uri": "/params/?q=2"})
        await _refused(stream)
    assert ("params", "2") not in RAN, "handle_params ran after the check denied"


# -- async start + result ----------------------------------------------------


async def test_a_ws_background_task_does_not_start_once_the_check_denies():
    page = await _page("/async/")
    async with await page.connect() as socket:
        assert (await socket.send_event("go"))["type"] != "error"
        await _until(lambda: "result" in RAN, "the control task")
        RAN.clear()
        assert (await socket.send_event("go_then_deny"))["type"] != "error"
        await _refused(socket)
        await _closed_4403(socket)
    assert RAN == ["go"], "the background task started after the check denied"


@pytest.mark.parametrize("transport", ["ws", "sse"])
async def test_a_background_result_after_the_session_is_revoked_is_refused(transport):
    page = await _page("/async/")
    connect = page.connect if transport == "ws" else page.connect_sse
    async with await connect() as live:
        assert (await live.send_event("go"))["type"] != "error"
        await _until(lambda: "result" in RAN, "the control task")
        RAN.clear()
        gate = asyncio.Event()
        GATE["gate"] = gate
        assert (await live.send_event("go"))["type"] != "error"
        await _until(lambda: "work" in RAN, "the task to start")
        await sync_to_async(page.session.delete)()
        gate.set()
        await _refused(live)
        await asyncio.sleep(0.2)
    assert "result" not in RAN, "handle_async_result ran for a revoked session"


# -- deferred (activity) event -----------------------------------------------


async def _notify(payload):
    await get_channel_layer().group_send(
        "djust_db_notify_turns4201",
        {"type": "db_notify", "channel": "turns4201", "payload": payload},
    )


async def _queue_bump(socket):
    answer = await socket.send_event("bump", {"_activity": "panel"})
    assert answer["type"] != "error", answer
    assert "bump" not in RAN, "the event must be queued while the panel is hidden"


async def test_a_released_activity_event_runs_while_the_session_lives():
    page = await _page("/deferred/")
    async with await page.connect() as socket:
        await _queue_bump(socket)
        await _notify({})
        await _until(lambda: "bump" in RAN, "the released event")


async def test_a_released_activity_event_after_the_session_is_revoked_is_denied():
    """Site-local mutant: ``LiveViewConsumer._authorize_released_explicit_event``
    returning True runs ``bump`` and sends its render, so this test fails."""
    page = await _page("/deferred/")
    async with await page.connect() as socket:
        await _queue_bump(socket)
        await _notify({"revoke": True})
        frame = await socket.receive(("error",))
        assert "bump" not in RAN, "a released event ran for a revoked session"
        assert frame.get("code") == "permission_denied", frame
        await _closed_4403(socket)
    assert "bump" not in RAN, "a released event ran for a revoked session"


# -- frames sent without a handler: presence, client push, recovery render ---

_DATA_FRAME = {
    "presence_event": "presence_event",
    "client_push_event": "push_event",
    "request_html": "html_recovery",
}


async def _arm(socket):
    """An authorized event render: the control turn, and it arms recovery."""
    answer = await socket.send_event("bump")
    assert answer["type"] != "error", answer
    assert "bump" in RAN, RAN


async def _trigger(socket, kind):
    if kind == "request_html":
        await socket.send({"type": "request_html"})
    else:
        # The channel-layer message the consumer handles, delivered directly.
        await socket.communicator.send_input({"type": kind, "event": "secret", "payload": {"s": 1}})


async def _next_frame(socket, kind):
    return await socket.receive((_DATA_FRAME[kind], "error"), timeout=5.0)


@pytest.mark.parametrize("kind", sorted(_DATA_FRAME))
async def test_a_frame_without_a_handler_is_sent_while_the_session_lives(kind):
    page = await _page("/deferred/")
    async with await page.connect() as socket:
        await _arm(socket)
        await _trigger(socket, kind)
        frame = await _next_frame(socket, kind)
        assert frame["type"] == _DATA_FRAME[kind], frame


@pytest.mark.parametrize("kind", sorted(_DATA_FRAME))
async def test_a_frame_without_a_handler_is_refused_once_the_session_is_revoked(kind):
    """Site-local mutants: dropping the ``_authorize_explicit_consumer_turn``
    check in ``_request_html_turn`` or ``_deliver_client_push_event``, or in
    ``_authorize_presence_turn``, sends the data frame, so this test fails."""
    page = await _page("/deferred/")
    async with await page.connect() as socket:
        await _arm(socket)
        await sync_to_async(page.session.delete)()
        await _trigger(socket, kind)
        frame = await _next_frame(socket, kind)
        assert frame["type"] == "error", "%s was sent for a revoked session: %r" % (kind, frame)
        assert frame.get("code") == "permission_denied", frame
        await _closed_4403(socket)


async def test_a_presence_turn_is_authorized_at_most_once_per_interval(monkeypatch):
    """A presence frame within ``PRESENCE_AUTH_INTERVAL_S`` of an authorized one
    does not re-read the session; the first one after the window does."""
    now = [1000.0]
    monkeypatch.setattr(websocket, "_presence_clock", lambda: now[0])
    page = await _page("/deferred/")
    async with await page.connect() as socket:
        await _arm(socket)
        await _trigger(socket, "presence_event")  # authorized: opens the window
        assert (await _next_frame(socket, "presence_event"))["type"] == "presence_event"
        await sync_to_async(page.session.delete)()
        now[0] += websocket.PRESENCE_AUTH_INTERVAL_S - 0.5
        await _trigger(socket, "presence_event")  # inside the window: not re-read
        frame = await _next_frame(socket, "presence_event")
        assert frame["type"] == "presence_event", frame
        now[0] += 1.0
        await _trigger(socket, "presence_event")  # past the window: re-read, denied
        frame = await _next_frame(socket, "presence_event")
        assert frame["type"] == "error", frame
        assert frame.get("code") == "permission_denied", frame
        await _closed_4403(socket)
