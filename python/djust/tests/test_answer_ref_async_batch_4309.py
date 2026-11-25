"""#4309: every frame answering an event carries its ``ref``; every
``async_pending`` carries its ``async_batch``.

The client matches an answer to its pending request by ``ref`` alone, and ends
a background batch's loading state on that batch's ``async_complete`` alone; it
no longer guesses from "the last event sent". Two server paths used to rely on
the guess:

* an event queued on a hidden ``dj_activity`` and released later (the root's or
  an embedded child's, by the runtime's event turn or the consumer's
  ``db_notify`` drain) answered without the ``ref`` it was sent with;
* the consumer drain's ``async_pending`` had no ``async_batch``, and its work
  never sent an ``async_complete``.

Both drains and both targets are pinned here, with a handler that renders and
one that does not, next to a live event of each kind.
"""

import pytest
from asgiref.sync import sync_to_async
from channels.layers import get_channel_layer
from channels.testing import WebsocketCommunicator
from django.test import override_settings

from djust import LiveView, event_handler, state
from djust.websocket import LiveViewConsumer

from ._ws_frames import drain_extra, has_frame, receive_until
from .test_exposure_runtime import make_request

MOD = __name__
CHANNEL = "answer_ref_4309"
WORK: list = []


class Kid(LiveView):
    exposure_policy = "explicit"
    open = state(False)
    count = state(0)
    template = (
        '{% load live_tags %}<div>{% dj_activity "panel" visible=open %}'
        '<b dj-click="bump">{{ count }}</b>{% enddj_activity %}</div>'
    )

    def mount(self, request, **kwargs):
        self.open = False

    @event_handler()
    def bump(self, **kwargs):
        self.count += 1
        self.start_async(self._work)

    @event_handler()
    def quiet(self, **kwargs):
        self.start_async(self._work)

    def _work(self):
        WORK.append("kid")


class Host(LiveView):
    exposure_policy = "explicit"
    root_open = state(False)
    count = state(0)
    _listen_channels = frozenset({CHANNEL})
    template = (
        "{% load live_tags %}<div dj-root>"
        '{% dj_activity "rootpanel" visible=root_open %}<i>{{ count }}</i>{% enddj_activity %}'
        '{% live_render "' + MOD + '.Kid" view_id="kid" %}</div>'
    )

    def _open_all(self):
        self.root_open = True
        self.set_activity_visible("rootpanel", True)
        kid = self._get_child_view("kid")
        if kid is not None:
            kid.open = True
            kid.set_activity_visible("panel", True)

    def handle_info(self, message):
        self._open_all()

    @event_handler()
    def open_all(self, **kwargs):
        self._open_all()

    @event_handler()
    def bump(self, **kwargs):
        self.count += 1
        self.start_async(self._work)

    @event_handler()
    def quiet(self, **kwargs):
        self.start_async(self._work)

    def _work(self):
        WORK.append("root")


class _Socket:
    def __init__(self, monkeypatch):
        monkeypatch.setattr(LiveView, "_validate_exposure_configuration", lambda self: None)
        self.settings = override_settings(
            LIVEVIEW_ALLOWED_MODULES=["djust", MOD],
            DEBUG=False,
            DJUST_TENANTS=None,
            DJUST_CONFIG={},
        )

    async def __aenter__(self):
        self.settings.enable()
        WORK.clear()
        request = await sync_to_async(make_request)()
        self.socket = WebsocketCommunicator(LiveViewConsumer.as_asgi(), "/ws/")
        self.socket.scope.update(session=request.session, user=request.user, tenant=None)
        assert (await self.socket.connect())[0]
        await self.socket.receive_json_from()
        await self.socket.send_json_to({"type": "mount", "view": MOD + ".Host", "url": "/h/"})
        assert (await self.socket.receive_json_from())["type"] == "mount"
        return self

    async def __aexit__(self, *exc):
        try:
            await self.socket.disconnect()
        finally:
            self.settings.disable()

    async def send(self, event, ref, **params):
        await self.socket.send_json_to(
            {"type": "event", "event": event, "params": params, "ref": ref}
        )


def _target_params(target, activity):
    params = {"view_id": "kid"} if target == "kid" else {}
    if activity:
        params["_activity"] = "panel" if target == "kid" else "rootpanel"
    return params


def _batch_completed(ref):
    """Until the batch the answer to ``ref`` advertised has completed."""

    def done(frames):
        answers = [f for f in frames if f.get("ref") == ref and f.get("async_batch")]
        return bool(answers) and has_frame(
            "async_complete", async_batch=answers[-1]["async_batch"]
        )(frames)

    return done


def _assert_answer(frames, ref, target):
    """One answer carries ``ref``; it and every async_pending name a batch."""
    answers = [f for f in frames if f.get("ref") == ref]
    assert len(answers) == 1, frames
    (answer,) = answers
    if target == "kid":
        assert answer["type"] == "embedded_update" and answer["view_id"] == "kid", answer
    else:
        assert answer["type"] in {"noop", "patch", "html_update"}, answer
    assert answer.get("async_pending") is True, answer
    assert isinstance(answer.get("async_batch"), str) and answer["async_batch"], answer
    unbatched = [f for f in frames if f.get("async_pending") and not f.get("async_batch")]
    assert not unbatched, unbatched
    assert has_frame("async_complete", async_batch=answer["async_batch"])(frames)
    assert WORK == [target]


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
@pytest.mark.parametrize("target", ["root", "kid"])
@pytest.mark.parametrize("handler", ["bump", "quiet"])
async def test_live_answer_carries_ref_and_batch(monkeypatch, target, handler):
    async with _Socket(monkeypatch) as ws:
        await ws.send(handler, 3, **_target_params(target, activity=False))
        frames = await receive_until(ws.socket, _batch_completed(3))
        frames += await drain_extra(ws.socket)
        _assert_answer(frames, 3, target)


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
@pytest.mark.parametrize("drain", ["db_notify", "event"])
@pytest.mark.parametrize("target", ["root", "kid"])
@pytest.mark.parametrize("handler", ["bump", "quiet"])
async def test_released_answer_carries_its_ref_and_batch(monkeypatch, drain, target, handler):
    async with _Socket(monkeypatch) as ws:
        await ws.send(handler, 7, **_target_params(target, activity=True))
        queued = await receive_until(ws.socket, has_frame(ref=7))
        # The queueing noop settles the request; nothing runs yet.
        assert [f["type"] for f in queued if f.get("ref") == 7] == ["noop"]
        assert not any(f.get("async_pending") for f in queued)
        assert WORK == []

        if drain == "db_notify":
            await get_channel_layer().group_send(
                "djust_db_notify_" + CHANNEL,
                {"type": "db_notify", "channel": CHANNEL, "payload": {}},
            )
        else:
            await ws.send("open_all", 8)
        frames = await receive_until(ws.socket, _batch_completed(7))
        frames += await drain_extra(ws.socket)
        _assert_answer(frames, 7, target)
