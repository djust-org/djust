"""Cross-wire parity harness (2.0 transport core, TC1).

Runs the same turns on one view over the three wires a browser can use: the
WebSocket consumer, SSE (stream GET + ``/message/`` POST) and the HTTP POST
fallback. Each wire's raw output is normalised into one *observation*: was
anything rendered, which side effects reached the client (flash, page
metadata, push events, layout, navigation), and whether ``defer()`` and
``start_async`` callbacks ran. The observations must be equal.

Where a wire differs today, the case is ``xfail(strict=True)`` with the gap
named in ``KNOWN_GAPS``. ``strict`` is deliberate: a fix makes the case
XPASS, which fails the run, so the PR that closes a gap must also delete its
entry. The transport-core PRs (docs/plans/2.0-transport-core.md §4) tick the
entries off.

Test-only: no behaviour change.
"""

from __future__ import annotations

import asyncio
import json
import sys
from typing import Any, Dict, List
from unittest.mock import MagicMock

import pytest
from asgiref.sync import sync_to_async
from channels.testing import WebsocketCommunicator
from django.contrib.auth.models import AnonymousUser
from django.contrib.sessions.middleware import SessionMiddleware
from django.test import RequestFactory, override_settings

from djust import LiveView
from djust.decorators import event_handler
from djust.sse import DjustSSEMessageView, DjustSSEStreamView, _sse_sessions
from djust.websocket import LiveViewConsumer

from .test_exposure_runtime import make_request

pytestmark = pytest.mark.django_db(transaction=True)

ORIGIN = "https://example.com"
RAN: List[str] = []


class ParityView(LiveView):
    exposure_policy = "legacy"
    template = "<div dj-root><span>{{ count }}</span></div>"

    def mount(self, request, **kwargs):
        self.count = 0

    @event_handler()
    def bump(self, **kwargs):
        self.count += 1

    @event_handler()
    def nothing(self, **kwargs):
        pass

    @event_handler()
    def flash_it(self, **kwargs):
        self.put_flash("info", "Saved")

    @event_handler()
    def title_it(self, **kwargs):
        self.page_title = "New title"

    @event_handler()
    def push_it(self, **kwargs):
        self.push_event("pong", {"n": 1})

    @event_handler()
    def layout_it(self, **kwargs):
        self.set_layout("parity/layout.html")

    @event_handler()
    def patch_it(self, **kwargs):
        self.live_patch(params={"page": "2"})

    @event_handler()
    def defer_it(self, **kwargs):
        self.defer(RAN.append, "deferred")

    @event_handler()
    def async_it(self, **kwargs):
        self.start_async(self._work)

    def _work(self):
        RAN.append("async")


VIEW_PATH = f"{__name__}.ParityView"
setattr(sys.modules[__name__], "ParityView", ParityView)

TURNS = [
    "bump",
    "nothing",
    "flash_it",
    "title_it",
    "push_it",
    "layout_it",
    "patch_it",
    "defer_it",
    "async_it",
]
WIRES = ["ws", "sse", "http"]

#: (wire, turn) -> the gap. Each closes in a transport-core PR.
KNOWN_GAPS: Dict[tuple, str] = {
    ("http", "async_it"): "HTTP never runs start_async work (transport-core open question 3)",
}


# --------------------------------------------------------------------------- #
# Normalisation
# --------------------------------------------------------------------------- #


def _observe_frames(frames: List[Dict[str, Any]]) -> Dict[str, Any]:
    """WebSocket / SSE frames → an observation."""
    obs: Dict[str, Any] = _empty()
    for f in frames:
        kind = f.get("type")
        if kind in ("patch", "html_update", "embedded_update"):
            obs["rendered"] = obs["rendered"] or bool(f.get("patches") or f.get("html"))
        elif kind == "flash":
            obs["flash"].append({k: v for k, v in f.items() if k != "type"})
        elif kind == "page_metadata":
            obs["page_metadata"].append({k: v for k, v in f.items() if k != "type"})
        elif kind == "push_event":
            obs["push_events"].append((f.get("event"), f.get("payload")))
        elif kind == "layout":
            obs["layout"] = f.get("path")
        elif kind == "navigation":
            obs["navigation"].append(f.get("action"))
    return obs


def _observe_http(answer: Dict[str, Any]) -> Dict[str, Any]:
    """An HTTP fallback JSON answer → an observation."""
    obs: Dict[str, Any] = _empty()
    obs["rendered"] = bool(answer.get("patches") or answer.get("html"))
    obs["flash"] = list(answer.get("_flash") or [])
    obs["page_metadata"] = list(answer.get("_page_metadata") or [])
    obs["push_events"] = [
        (e.get("event"), e.get("payload")) for e in answer.get("_push_events") or []
    ]
    obs["layout"] = (answer.get("_layout") or {}).get("path")
    obs["navigation"] = [f.get("action") for f in answer.get("_navigation") or []]
    return obs


def _empty() -> Dict[str, Any]:
    return {
        "rendered": False,
        "flash": [],
        "page_metadata": [],
        "push_events": [],
        "layout": None,
        "navigation": [],
    }


async def _settle(collect, expect_async: bool) -> None:
    """Give deferred and background work time to land."""
    for _ in range(100 if expect_async else 5):
        await asyncio.sleep(0.02)
        await collect()
        if not expect_async or "async" in RAN:
            return


# --------------------------------------------------------------------------- #
# Wires
# --------------------------------------------------------------------------- #


async def _run_ws(turn: str) -> Dict[str, Any]:
    request = await sync_to_async(make_request)()
    socket = WebsocketCommunicator(LiveViewConsumer.as_asgi(), "/ws/")
    socket.scope.update(session=request.session, user=request.user, tenant=None)
    assert (await socket.connect())[0]
    await socket.receive_json_from(timeout=3)  # connect ack
    try:
        await socket.send_json_to({"type": "mount", "view": VIEW_PATH, "url": "/parity/"})
        mounted = await socket.receive_json_from(timeout=3)
        assert mounted["type"] == "mount", mounted
        await socket.send_json_to({"type": "event", "event": turn, "params": {}, "ref": 1})
        frames: List[Dict[str, Any]] = []

        async def collect():
            # ``receive_nothing`` polls without cancelling the application;
            # a timed-out ``receive_json_from`` would cancel the consumer.
            while not await socket.receive_nothing(timeout=0.2):
                frames.append(await socket.receive_json_from(timeout=3))

        await collect()
        await _settle(collect, turn == "async_it")
        return _observe_frames(frames)
    finally:
        await socket.disconnect()


async def _run_sse(turn: str) -> Dict[str, Any]:
    sid = "22222222-2222-2222-2222-222222222222"
    _sse_sessions.pop(sid, None)
    rf = RequestFactory()
    get = rf.get(f"/djust/sse/{sid}/?view={VIEW_PATH}", HTTP_ORIGIN=ORIGIN)
    get.user = AnonymousUser()
    await DjustSSEStreamView().get(get, session_id=sid)
    session = _sse_sessions[sid]
    frames: List[Dict[str, Any]] = []

    async def collect():
        while not session.queue.empty():
            msg = session.queue.get_nowait()
            if msg is not None:
                frames.append(msg)

    try:
        await collect()
        frames.clear()  # the mount frame
        session._owner_user_pk = 7
        session._owner_session_key = None
        post = rf.post(
            f"/djust/sse/{sid}/message/",
            data=json.dumps({"type": "event", "event": turn, "params": {}, "ref": 1}),
            content_type="application/json",
            HTTP_ORIGIN=ORIGIN,
        )
        post.user = MagicMock(is_authenticated=True, pk=7)
        response = await DjustSSEMessageView().post(post, session_id=sid)
        assert response.status_code == 200, response.content
        await collect()
        await _settle(collect, turn == "async_it")
        return _observe_frames(frames)
    finally:
        handles = tuple(getattr(session.runtime.view_instance, "_async_task_handles", ()) or ())
        if handles:
            await asyncio.wait_for(asyncio.gather(*handles, return_exceptions=True), 3)
        session.shutdown()
        _sse_sessions.pop(sid, None)


def _run_http_sync(turn: str) -> Dict[str, Any]:
    view = ParityView()
    rf = RequestFactory()
    get = rf.get("/parity/")
    SessionMiddleware(lambda r: None).process_request(get)
    get.session.save()
    get.user = AnonymousUser()
    view.get(get)
    post = rf.post(
        "/parity/",
        data=json.dumps({"event": turn, "params": {}}),
        content_type="application/json",
    )
    post.session = get.session
    post.user = AnonymousUser()
    response = view.post(post)
    return _observe_http(json.loads(response.content.decode("utf-8")))


async def _run_http(turn: str) -> Dict[str, Any]:
    obs = await sync_to_async(_run_http_sync)(turn)
    await _settle(lambda: asyncio.sleep(0), turn == "async_it")
    return obs


RUNNERS = {"ws": _run_ws, "sse": _run_sse, "http": _run_http}


# --------------------------------------------------------------------------- #
# The harness
# --------------------------------------------------------------------------- #


@pytest.fixture
def parity_env(monkeypatch):
    from djust.mixins import layout

    monkeypatch.setattr(LiveView, "_validate_exposure_configuration", lambda self: None)
    monkeypatch.setattr(
        layout, "render_pending_layout", lambda view, path: "<main>%s</main>" % path
    )
    with override_settings(
        LIVEVIEW_ALLOWED_MODULES=[__name__],
        ALLOWED_HOSTS=["example.com", "testserver"],
        DJUST_TENANTS=None,
        DJUST_CONFIG={},
    ):
        yield


async def _observe(wire: str, turn: str) -> Dict[str, Any]:
    RAN.clear()
    obs = await RUNNERS[wire](turn)
    obs["deferred_ran"] = "deferred" in RAN
    obs["async_ran"] = "async" in RAN
    RAN.clear()
    return obs


#: What each turn must produce, on every wire.
EXPECTED: Dict[str, Dict[str, Any]] = {
    "bump": {"rendered": True},
    "nothing": {},
    "flash_it": {"flash": [{"action": "put", "level": "info", "message": "Saved"}]},
    "title_it": {"page_metadata": "non-empty"},
    "push_it": {"push_events": [("pong", {"n": 1})]},
    "layout_it": {"layout": "parity/layout.html"},
    "patch_it": {"navigation": ["live_patch"]},
    "defer_it": {"deferred_ran": True},
    "async_it": {"async_ran": True},
}


def _cases():
    for turn in TURNS:
        for wire in WIRES:
            gap = KNOWN_GAPS.get((wire, turn))
            marks = [pytest.mark.xfail(strict=True, reason=gap)] if gap else []
            yield pytest.param(wire, turn, marks=marks, id=f"{wire}-{turn}")


@pytest.mark.parametrize("wire,turn", list(_cases()))
async def test_each_wire_delivers_the_turn(parity_env, wire, turn):
    """The turn's own effect reaches the client on this wire."""
    obs = await _observe(wire, turn)
    for key, want in EXPECTED[turn].items():
        if want == "non-empty":
            assert obs[key], (key, obs)
        else:
            assert obs[key] == want, (key, obs)


def test_every_known_gap_names_a_real_case():
    """A stale entry would silently hide nothing; keep the table exact."""
    for wire, turn in KNOWN_GAPS:
        assert wire in WIRES and turn in TURNS, (wire, turn)
