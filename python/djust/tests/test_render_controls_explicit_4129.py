"""#4129 review: the render controls on an explicit-exposure view (the 2.0 default).

* ``reset_submitted_form()``: the reset rides on the frame of the turn that
  asked for it, a render or (with ``skip_render()``) a ``noop``, and never
  waits for a later, unrelated render.
* ``skip_render()`` followed by an event that renders: the skipped turn left
  the server's diff baseline alone, so the next frame must describe the page as
  the browser has it (the patch carries the new value, the version follows on).

Driven over a real ``WebsocketCommunicator``, the SSE endpoints and stateless
fallback POSTs.
"""

from __future__ import annotations

import json
import uuid
from typing import Any, List

import pytest
from asgiref.sync import sync_to_async
from django.contrib.auth.models import AnonymousUser
from django.contrib.sessions.backends.db import SessionStore
from django.test import RequestFactory

from djust import LiveView, _automatic_state
from djust.decorators import event_handler, state
from djust.tests import _per_turn_harness as harness

MODULE = __name__


class Composer(LiveView):
    exposure_policy = "explicit"
    template = '<div dj-root><p id="n">{{ n }}</p><form><input name="t"></form></div>'
    n = state(0)

    @event_handler()
    def save(self, **kwargs):
        # A database write would go here: no declared state changes.
        self.reset_submitted_form()

    @event_handler()
    def save_quietly(self, **kwargs):
        self.reset_submitted_form()
        self.skip_render()

    @event_handler()
    def bump(self, **kwargs):
        self.n += 1

    @event_handler()
    def bump_quietly(self, **kwargs):
        self.n += 1
        self.skip_render()


@pytest.fixture(autouse=True)
def _one_tab(monkeypatch):
    monkeypatch.setattr(_automatic_state, "new_tab_id", lambda: harness.TAB)


def _request(method: str, key: str | None, event: str = "") -> Any:
    factory = RequestFactory()
    if method == "GET":
        request = factory.get(harness.PATH)
    else:
        request = factory.post(
            harness.PATH,
            data="{}",
            content_type="application/json",
            HTTP_X_DJUST_EVENT=event,
            HTTP_X_DJUST_TAB=harness.TAB,
        )
    request.user = AnonymousUser()
    request.tenant = None
    request.session = SessionStore(key)
    if key is None:
        request.session.create()
    return request


def _fallback(events: List[str]) -> List[dict]:
    first = _request("GET", None)
    assert Composer().get(first).status_code == 200
    key = first.session.session_key
    answers = []
    for name in events:
        response = Composer().post(_request("POST", key, name))
        assert response.status_code == 200, response.content
        answers.append(json.loads(response.content))
    return answers


async def _websocket(events: List[str]) -> List[dict]:
    key = await sync_to_async(harness.new_session_key)()
    with harness.ws_settings(MODULE):
        comm, _mount = await harness.mount_view(Composer, key)
        try:
            return [await harness.event(comm, name) for name in events]
        finally:
            await comm.disconnect()


async def _sse(events: List[str]) -> List[dict]:
    from django.contrib.auth import get_user
    from django.utils.functional import SimpleLazyObject

    from djust.sse import DjustSSEMessageView, DjustSSEStreamView, _sse_sessions

    def request_for(method: str, target: str, body: Any, key: str | None = None) -> Any:
        factory = RequestFactory()
        if method == "GET":
            made = factory.get(target, data=body)
        else:
            made = factory.post(target, data=json.dumps(body), content_type="application/json")
        made.session = SessionStore(key)
        if key is None:
            made.session.create()
        made.session = SessionStore(made.session.session_key)
        made.user = SimpleLazyObject(lambda: get_user(made))
        made.tenant = None
        return made

    sid = str(uuid.uuid4())
    frames: List[dict] = []
    with harness.ws_settings(MODULE):
        first = await sync_to_async(request_for)(
            "GET",
            f"/djust/sse/{sid}/",
            {"view": f"{MODULE}.Composer", "_djust_url": harness.PATH, "_djust_tab": harness.TAB},
        )
        response = await DjustSSEStreamView().get(first, session_id=sid)
        assert response.status_code == 200
        key = first.session.session_key
        session = _sse_sessions[sid]
        sent: List[dict] = []
        try:
            for name in events:
                before = _drain(session, sent)
                body = {"type": "event", "event": name, "params": {}}
                posted = await sync_to_async(request_for)(
                    "POST", f"/djust/sse/{sid}/message/", body, key
                )
                answer = await DjustSSEMessageView().post(posted, session_id=sid)
                assert answer.status_code in (200, 202, 204), answer.content
                _drain(session, sent)
                replies = [f for f in sent[before:] if f.get("type") in harness.REPLY_TYPES]
                assert replies, sent[before:]
                frames.append(replies[-1])
        finally:
            _sse_sessions.pop(sid, None)
    return frames


def _drain(session: Any, sent: List[dict]) -> int:
    """Move every frame the SSE session has queued into *sent*; its length before."""
    before = len(sent)
    queue = session.queue
    while not queue.empty():
        sent.append(queue.get_nowait())
    return before


def _check_reset(frames: List[dict]) -> None:
    """``save``, ``save_quietly``, ``bump``: each reset arrives on its own turn's
    frame (a render for ``save``, a ``noop`` for the skipped ``save_quietly``)
    and none reaches the turn after."""
    save, quiet, after = frames
    assert save.get("reset_form") is True, save
    assert quiet.get("type") == "noop" or not (quiet.get("patches") or quiet.get("html")), quiet
    assert quiet.get("reset_form") is True, quiet
    assert "reset_form" not in after, after


def _check_skip_then_render(frames: List[dict]) -> None:
    quiet, loud = frames
    assert quiet["type"] == "noop", quiet
    assert loud["type"] in ("patch", "html_update"), loud
    shown = json.dumps(loud.get("patches") or loud.get("html"))
    assert "3" in shown, loud  # bump, bump_quietly, bump: the browser must show 3


@pytest.mark.asyncio
@pytest.mark.django_db(transaction=True)
async def test_websocket_reset_rides_on_the_noop_and_does_not_leak():
    _check_reset(await _websocket(["save", "save_quietly", "bump"]))


@pytest.mark.asyncio
@pytest.mark.django_db(transaction=True)
async def test_sse_reset_rides_on_the_noop_and_does_not_leak():
    _check_reset(await _sse(["save", "save_quietly", "bump"]))


@pytest.mark.django_db
def test_fallback_reset_rides_on_the_noop_and_does_not_leak():
    _check_reset(_fallback(["save", "save_quietly", "bump"]))


@pytest.mark.asyncio
@pytest.mark.django_db(transaction=True)
async def test_websocket_skip_render_then_a_render_patches_from_the_shown_page():
    frames = await _websocket(["bump", "bump_quietly", "bump"])
    first, quiet, loud = frames
    _check_skip_then_render([quiet, loud])
    if "version" in first and "version" in loud:
        assert loud["version"] == first["version"] + 1, (first, loud)
    assert "SetText" in json.dumps(loud.get("patches")) or loud["type"] == "html_update"


@pytest.mark.django_db
def test_fallback_skip_render_then_a_render_patches_from_the_shown_page():
    _first, quiet, loud = _fallback(["bump", "bump_quietly", "bump"])
    assert quiet.get("type") == "noop" or not (quiet.get("patches") or quiet.get("html")), quiet
    assert "3" in json.dumps(loud.get("patches") or loud.get("html")), loud
