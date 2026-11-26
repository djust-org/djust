"""#3658 (TC9): one ``render`` frame per turn result, with ``effects[]``.

Per wire (WebSocket, the runtime that SSE uses, HTTP):

* an event turn is one frame, its DOM part and every queued effect, in the
  canonical order (push_event, flash, page_metadata, layout, navigation), FIFO
  within a kind;
* a skip-render turn still sends one frame (no DOM part, the effects);
* ``defer()`` callbacks run after the frame, and what they queue goes out in
  one effects-only ``source: "deferred"`` frame (HTTP: in the same answer);
* ``flush_push_events()`` sends an effects-only frame of push events only;
* a refused event is one frame with its ``ref``;
* the broadcast splice parses to exactly the frame ``render_frame`` builds;
* the same turn yields equal ``effects`` on all three wires.
"""

from __future__ import annotations

import json
from typing import Any, Dict, List

import pytest
from asgiref.sync import sync_to_async
from channels.testing import WebsocketCommunicator
from django.test import RequestFactory, override_settings

from djust import LiveView, event_handler, state
from djust._protocol import PROTOCOL_VERSION
from djust._render_frame import adrain_effects, dom_part, drain_effects, render_frame
from djust.mixins import layout as layout_module
from djust.runtime import ViewRuntime
from djust.websocket import LiveViewConsumer, broadcast_render_text

from ._frames import effects_of, is_ack
from ._ws_frames import has_frame, receive_until
from .test_exposure_runtime import make_request
from .test_runtime_state_save_tt_1894 import MockTransport

MOD = __name__

#: The effects of ``Effects.everything``, in the order the client applies them.
EVERYTHING = [
    {"kind": "push_event", "event": "first", "payload": {"n": 1}},
    {"kind": "push_event", "event": "second", "payload": {}},
    {"kind": "flash", "action": "put", "level": "info", "message": "Saved"},
    {"kind": "page_metadata", "action": "title", "value": "Inbox (3)"},
    {"kind": "layout", "path": "layouts/full.html", "html": "<body>LAYOUT</body>"},
    {"kind": "navigation", "action": "live_patch", "replace": False, "params": {"page": 2}},
]


class Effects(LiveView):
    count = state(0)
    template = "<div dj-root><b>{{ count }}</b></div>"

    def _queue_all(self) -> None:
        # Queued against the rank order: the drain sorts them.
        self.live_patch(params={"page": 2})
        self.set_layout("layouts/other.html")
        self.page_title = "Inbox (3)"
        self.put_flash("info", "Saved")
        self.push_event("first", {"n": 1})
        self.set_layout("layouts/full.html")
        self.push_event("second")

    @event_handler()
    def everything(self, **kwargs):
        self.count += 1
        self._queue_all()

    @event_handler()
    def quiet(self, **kwargs):
        self._queue_all()

    @event_handler()
    def skipped(self, **kwargs):
        self.count += 1
        self._queue_all()
        self.skip_render()

    @event_handler()
    def deferring(self, **kwargs):
        self.count += 1
        self.put_flash("info", "now")
        self.defer(self._later)

    def _later(self) -> None:
        self.push_event("later")
        self.live_patch(params={"later": 1})


@pytest.fixture(autouse=True)
def _layout(monkeypatch):
    monkeypatch.setattr(
        layout_module, "render_pending_layout", lambda view, path: "<body>LAYOUT</body>"
    )
    monkeypatch.setattr(LiveView, "_validate_exposure_configuration", lambda self: None)


# ---------------------------------------------------------------------------
# The builder
# ---------------------------------------------------------------------------


class TestBuilder:
    def test_render_frame_type_first_effects_last(self):
        frame = render_frame(effects=[{"kind": "flash"}], version=3, patches=[], ref=1)
        assert list(frame) == ["type", "version", "patches", "ref", "effects"]
        assert frame["type"] == "render"
        assert render_frame()["effects"] == []

    def test_render_frame_checks_the_dom_part(self):
        with pytest.raises(ValueError):
            render_frame(type="patch")
        with pytest.raises(ValueError):
            render_frame(view_id="c", patches=[])
        with pytest.raises(ValueError):
            render_frame(view_id="c")
        assert dom_part(render_frame(view_id="c", html="<i></i>")) == "embedded"
        assert dom_part(render_frame(patches=[], html="x")) == "patches"
        assert dom_part(render_frame(html="x")) == "html"
        assert dom_part(render_frame()) is None

    def test_drain_root_then_child_and_rank_order(self):
        root, child = Effects(), Effects()
        child.push_event("child")
        root.live_patch(params={"a": 1})
        root.push_event("root")
        assert drain_effects(root, child) == [
            {"kind": "push_event", "event": "root", "payload": {}},
            {"kind": "navigation", "action": "live_patch", "replace": False, "params": {"a": 1}},
            {"kind": "push_event", "event": "child", "payload": {}},
        ]
        assert drain_effects(root, child) == []

    async def test_sync_and_async_drains_agree(self):
        one, two = Effects(), Effects()
        one._queue_all()
        two._queue_all()
        assert drain_effects(one) == await adrain_effects(two) == EVERYTHING

    def test_a_missing_layout_is_dropped(self, monkeypatch):
        from django.template.exceptions import TemplateDoesNotExist

        def missing(view, path):
            raise TemplateDoesNotExist(path)

        monkeypatch.setattr(layout_module, "render_pending_layout", missing)
        view = Effects()
        view.set_layout("nope.html")
        view.put_flash("info", "x")
        assert [e["kind"] for e in drain_effects(view)] == ["flash"]

    @pytest.mark.parametrize("target", [None, "slot-3"])
    @pytest.mark.parametrize("effects", [[], EVERYTHING])
    def test_broadcast_splice_parses_to_the_built_frame(self, target, effects):
        patches = [{"type": "SetText", "path": [0], "d": "1", "text": "2"}]
        text = broadcast_render_text(json.dumps(patches), 9, target, effects)
        fields: Dict[str, Any] = {
            "patches": patches,
            "version": 9,
            "broadcast": True,
            "source": "broadcast",
        }
        if target:
            fields["target_id"] = target
        assert json.loads(text) == render_frame(effects=effects, **fields)


# ---------------------------------------------------------------------------
# WebSocket
# ---------------------------------------------------------------------------


class _Socket:
    def __init__(self):
        self.settings = override_settings(
            LIVEVIEW_ALLOWED_MODULES=["djust", MOD],
            DEBUG=False,
            DJUST_TENANTS=None,
            DJUST_CONFIG={},
        )

    async def __aenter__(self):
        self.settings.enable()
        request = await sync_to_async(make_request)()
        self.socket = WebsocketCommunicator(LiveViewConsumer.as_asgi(), "/ws/")
        self.socket.scope.update(session=request.session, user=request.user, tenant=None)
        assert (await self.socket.connect())[0]
        await self.socket.receive_json_from()
        await self.socket.send_json_to(
            {
                "type": "mount",
                "view": MOD + ".Effects",
                "url": "/effects/",
                "protocol": PROTOCOL_VERSION,
            }
        )
        await receive_until(self.socket, has_frame("mount"), what="the mount")
        return self

    async def event(self, name: str, ref: int) -> List[Dict[str, Any]]:
        await self.socket.send_json_to({"type": "event", "event": name, "params": {}, "ref": ref})
        return await receive_until(self.socket, has_frame("render", ref=ref), what="the answer")

    async def __aexit__(self, *exc):
        try:
            await self.socket.disconnect()
        finally:
            self.settings.disable()


@pytest.mark.asyncio
@pytest.mark.django_db(transaction=True)
class TestWebSocket:
    async def test_an_event_turn_is_one_frame_with_every_effect_in_order(self):
        async with _Socket() as ws:
            frames = await ws.event("everything", 1)
        assert len(frames) == 1, frames
        (frame,) = frames
        assert frame["type"] == "render" and frame["ref"] == 1
        assert dom_part(frame) in ("patches", "html")
        assert frame["effects"] == EVERYTHING

    async def test_a_skip_render_turn_still_sends_one_frame(self):
        async with _Socket() as ws:
            frames = await ws.event("quiet", 2)
        assert len(frames) == 1, frames
        assert is_ack(frames[0])
        assert frames[0]["effects"] == EVERYTHING

    async def test_deferred_callbacks_follow_in_one_effects_only_frame(self):
        async with _Socket() as ws:
            frames = await ws.event("deferring", 3)
            frames += await receive_until(
                ws.socket, has_frame("render", source="deferred"), what="the deferred frame"
            )
        answer, deferred = frames
        assert effects_of(answer) == [
            {"kind": "flash", "action": "put", "level": "info", "message": "now"}
        ]
        assert deferred == render_frame(
            source="deferred",
            effects=[
                {"kind": "push_event", "event": "later", "payload": {}},
                {
                    "kind": "navigation",
                    "action": "live_patch",
                    "replace": False,
                    "params": {"later": 1},
                },
            ],
        )
        assert "ref" not in deferred and "version" not in deferred


@pytest.mark.asyncio
@pytest.mark.django_db
class TestFlushPushEvents:
    async def test_ws_sends_an_effects_only_push_frame(self):
        sent: List[Dict[str, Any]] = []
        consumer = LiveViewConsumer()

        async def send_json(frame):
            sent.append(frame)

        consumer.send_json = send_json  # type: ignore[method-assign]
        view = Effects()
        consumer.view_instance = view
        view._ws_consumer = consumer
        view.put_flash("info", "stays queued")
        view.push_event("step", {"n": 1})
        await view.flush_push_events()
        assert sent == [
            render_frame(
                source="async",
                effects=[{"kind": "push_event", "event": "step", "payload": {"n": 1}}],
            )
        ]
        assert view._pending("flash")


# ---------------------------------------------------------------------------
# The runtime (the SSE transport's turn code)
# ---------------------------------------------------------------------------


async def _runtime() -> "tuple[ViewRuntime, MockTransport]":
    request = await sync_to_async(make_request)()
    transport = MockTransport()
    transport.build_request = lambda: request

    async def fresh_event_request(view):
        return await sync_to_async(make_request)(request.session.session_key)

    transport.explicit_event_request = fresh_event_request
    runtime = ViewRuntime(transport)
    with override_settings(LIVEVIEW_ALLOWED_MODULES=["djust", MOD]):
        await runtime.dispatch_mount(
            {"type": "mount", "view": MOD + ".Effects", "url": "/effects/"}
        )
    assert runtime.view_instance is not None, transport.sent
    transport.sent.clear()
    return runtime, transport


@pytest.mark.asyncio
@pytest.mark.django_db(transaction=True)
class TestRuntime:
    async def test_an_event_turn_is_one_frame_with_every_effect_in_order(self):
        runtime, transport = await _runtime()
        await runtime.dispatch_event(
            {"type": "event", "event": "everything", "params": {}, "ref": 1}
        )
        assert len(transport.sent) == 1, transport.sent
        (frame,) = transport.sent
        assert frame["type"] == "render" and frame["ref"] == 1
        assert dom_part(frame) in ("patches", "html")
        assert frame["effects"] == EVERYTHING

    async def test_a_skip_render_turn_still_sends_one_frame(self):
        runtime, transport = await _runtime()
        await runtime.dispatch_event({"type": "event", "event": "quiet", "params": {}, "ref": 2})
        assert len(transport.sent) == 1, transport.sent
        assert is_ack(transport.sent[0]) and transport.sent[0]["ref"] == 2
        assert transport.sent[0]["effects"] == EVERYTHING

    async def test_deferred_callbacks_follow_in_one_effects_only_frame(self):
        runtime, transport = await _runtime()
        await runtime.dispatch_event(
            {"type": "event", "event": "deferring", "params": {}, "ref": 3}
        )
        answer, deferred = transport.sent
        assert answer["ref"] == 3
        assert deferred["source"] == "deferred" and "ref" not in deferred
        assert [e["kind"] for e in deferred["effects"]] == ["push_event", "navigation"]

    async def test_a_refused_event_is_one_frame_with_its_ref(self):
        from djust.turns import Refusal, Turn

        runtime, transport = await _runtime()
        turn = Turn("event", "sse", source="client", view=runtime.view_instance, event="x", ref=7)
        await runtime.turn_refused(turn, Refusal("flash", level="error", message="No."))
        assert transport.sent == [
            render_frame(
                source="refusal",
                event_name="x",
                ref=7,
                effects=[{"kind": "flash", "action": "put", "level": "error", "message": "No."}],
            )
        ]


# ---------------------------------------------------------------------------
# HTTP
# ---------------------------------------------------------------------------


def _post(event: str) -> Dict[str, Any]:
    request = RequestFactory().post(
        "/effects/",
        data=json.dumps({"event": event, "params": {}}),
        content_type="application/json",
        HTTP_X_DJUST_PROTOCOL=str(PROTOCOL_VERSION),
    )
    initial = make_request()
    request.user, request.session, request.tenant = initial.user, initial.session, None
    with override_settings(LIVEVIEW_ALLOWED_MODULES=["djust", MOD]):
        response = Effects.as_view()(request)
    assert response.status_code == 200, response.content
    return json.loads(response.content)


@pytest.mark.django_db
class TestHTTP:
    def test_the_answer_is_one_render_frame_with_every_effect(self):
        answer = _post("everything")
        assert answer["type"] == "render"
        assert dom_part(answer) in ("patches", "html")
        assert answer["effects"] == EVERYTHING
        for old in ("_flash", "_page_metadata", "_push_events", "_layout", "_navigation"):
            assert old not in answer

    def test_a_skip_render_answer_is_a_render_with_no_dom_part(self):
        answer = _post("skipped")
        assert is_ack(answer)
        assert "version" not in answer and "patches" not in answer
        assert answer["effects"] == EVERYTHING

    def test_deferred_effects_join_the_same_answer(self):
        answer = _post("deferring")
        assert [e["kind"] for e in answer["effects"]] == ["flash", "push_event", "navigation"]


# ---------------------------------------------------------------------------
# Parity (TC1): the same turn, the same effects, on every wire
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.django_db(transaction=True)
async def test_the_same_turn_yields_equal_effects_on_all_three_wires():
    async with _Socket() as ws:
        (ws_frame,) = await ws.event("everything", 1)
    runtime, transport = await _runtime()
    await runtime.dispatch_event({"type": "event", "event": "everything", "params": {}, "ref": 1})
    http = await sync_to_async(_post)("everything")
    assert ws_frame["effects"] == transport.sent[0]["effects"] == http["effects"] == EVERYTHING
