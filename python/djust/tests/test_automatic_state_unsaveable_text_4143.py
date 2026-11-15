"""A value the automatic save cannot hold never breaks the turn (#4143).

Two inputs used to raise out of the save instead of being handled:

* a ``str`` holding a lone surrogate (``"\\ud800"``): the encoder accepted it, and the size
  computation and the per-name digest then called ``.encode("utf-8")`` and raised
  ``UnicodeEncodeError``;
* a view whose ``__dict__`` holds a key that is not a ``str``: the attribute walks called
  ``name.startswith`` on it and raised ``AttributeError``.

Each is now handled like any other value the save cannot hold, identically on the WebSocket, the
SSE transport and the stateless HTTP fallback: the response is served, one warning names the view
and the field (never the value), and the next request runs ``mount()`` again. A non-string
``__dict__`` key is not an attribute name, so the name rule skips it.
"""

from __future__ import annotations

import json
import logging
import uuid
from typing import Any, Dict, List

import pytest
from asgiref.sync import sync_to_async
from django.contrib.sessions.backends.db import SessionStore
from django.test import RequestFactory

from djust import LiveView, _automatic_state, event_handler
from djust.tests import _per_turn_harness as harness
from djust.tests.test_automatic_state_4107 import (
    _Base,
    _probe_of,
    _request,
    _saved_envelope,
    _stored,
)

MODULE = __name__
LONE = "bad \ud800 text"  # a lone high surrogate: not encodable as UTF-8
CALLS: List[str] = []


@pytest.fixture(autouse=True)
def _one_tab(monkeypatch):
    monkeypatch.setattr(_automatic_state, "new_tab_id", lambda: harness.TAB)
    _automatic_state._NOTICED.clear()
    CALLS.clear()


class SurrogateOnEvent(_Base):
    """A handler stores a lone surrogate; the count beside it is saved as usual."""

    exposure_policy = "explicit"  # as test_automatic_state_4107._Base, which it subclasses

    shown = ("count",)

    def mount(self, request, **kwargs):
        CALLS.append("mount")
        self.count = 0
        self.note = "fine"

    @event_handler()
    def inc(self, **kwargs):
        self.count += 1
        self.note = LONE


class SurrogateInMount(_Base):
    """The page GET saves it (``mount()`` set it); nested and private shapes too."""

    exposure_policy = "explicit"  # as test_automatic_state_4107._Base, which it subclasses

    shown = ("count",)

    def mount(self, request, **kwargs):
        CALLS.append("mount")
        self.count = 0
        self.note = LONE
        self.rows = [{"label": "ok"}, {"label": LONE}]
        self.by_name = {LONE: 1}
        self._scratch = LONE

    @event_handler()
    def inc(self, **kwargs):
        self.count += 1


class NonStringKey(_Base):
    """``__dict__`` holds keys that are not strings: not attribute names, so skipped."""

    exposure_policy = "explicit"  # as test_automatic_state_4107._Base, which it subclasses

    shown = ("count",)

    def mount(self, request, **kwargs):
        CALLS.append("mount")
        self.count = 0
        vars(self)[1] = "one"
        vars(self)[("a", "b")] = 2

    @event_handler()
    def inc(self, **kwargs):
        self.count += 1
        vars(self)[3.5] = "three"


class Plain(_Base):
    """The control: NonStringKey without its non-string keys."""

    exposure_policy = "explicit"  # as test_automatic_state_4107._Base, which it subclasses

    shown = ("count",)

    def mount(self, request, **kwargs):
        CALLS.append("mount")
        self.count = 0

    @event_handler()
    def inc(self, **kwargs):
        self.count += 1


VIEWS = [SurrogateOnEvent, SurrogateInMount, NonStringKey, Plain]

#: Field -> the view whose warning names it. NonStringKey warns about nothing.
WARNED = {
    SurrogateOnEvent: ["note"],
    SurrogateInMount: ["note", "rows", "by_name"],
    NonStringKey: [],
    Plain: [],
}


def _warnings(caplog) -> List[str]:
    return [
        r.getMessage()
        for r in caplog.records
        if r.levelno >= logging.WARNING and r.name.startswith("djust")
    ]


def _assert_warned_once(view_class: type, caplog) -> None:
    lines = _warnings(caplog)
    text = "\n".join(lines)
    assert "\ud800" not in text and "bad " not in text, text  # never the value
    assert "Traceback" not in text and "UnicodeEncodeError" not in text, text
    for name in WARNED[view_class]:
        hits = [line for line in lines if "%s.%s " % (view_class.__name__, name) in line]
        assert len(hits) == 1, (name, lines)
        assert "is not saved between requests" in hits[0], hits[0]
    if not WARNED[view_class]:
        # Other warnings (DJE-053 on a socket event) are the Plain control's too.
        assert not [line for line in lines if "is not saved between requests" in line], lines


def _fallback(view_class: type, events: int) -> Any:
    first = _request("GET", None)
    response = view_class().get(first)
    assert response.status_code == 200, response.content
    key = first.session.session_key
    view = None
    for _ in range(events):
        view = view_class()
        answer = view.post(_request("POST", key, "inc"))
        assert answer.status_code == 200, answer.content
    return view, key


@pytest.mark.django_db
@pytest.mark.parametrize("view_class", VIEWS, ids=lambda c: c.__name__)
def test_over_the_fallback_the_turn_is_served_and_warned_once(view_class, caplog):
    with caplog.at_level(logging.WARNING, logger="djust"):
        view, key = _fallback(view_class, 3)
    assert view.count == 3  # what could be saved was saved
    envelope = _saved_envelope(key)
    assert envelope["values"]["count"] == 3
    for name in WARNED[view_class]:
        assert name not in envelope["values"], envelope["values"]
    assert "_scratch" not in envelope["private"]
    if WARNED[view_class]:
        # Starts fresh next time: every request after the unsaveable value ran mount() again.
        assert envelope["remount"] is True
        expected = 4 if view_class is SurrogateInMount else 3
        assert CALLS == ["mount"] * expected, CALLS
    else:
        assert CALLS == ["mount"], CALLS  # the GET only: nothing was left out
    assert "\ud800" not in json.dumps(_stored(key), ensure_ascii=False)
    _assert_warned_once(view_class, caplog)


@pytest.mark.asyncio
@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("view_class", VIEWS, ids=lambda c: c.__name__)
async def test_over_the_websocket_the_turn_is_served_and_warned_once(view_class, caplog):
    key = await harness.new_session_key_async()
    with caplog.at_level(logging.WARNING, logger="djust"), harness.ws_settings(MODULE):
        comm, _mount = await harness.mount_view(view_class, key)
        try:
            for _ in range(3):
                frame = await harness.event(comm, "inc")
                assert frame["type"] != "error", frame
        finally:
            await comm.disconnect()
        again, mount = await harness.mount_view(view_class, key)
        frame = await harness.event(again, "inc")
        await again.disconnect()
    assert frame["type"] != "error", frame
    assert _probe_of(mount["html"]) == {"count": 3}
    stored = await sync_to_async(_saved_envelope)(key)
    assert stored["values"]["count"] == 4
    for name in WARNED[view_class]:
        assert name not in stored["values"], stored["values"]
    assert stored["remount"] is bool(WARNED[view_class])
    _assert_warned_once(view_class, caplog)


@pytest.mark.asyncio
@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("view_class", VIEWS, ids=lambda c: c.__name__)
async def test_over_sse_the_turn_is_served_and_warned_once(view_class, caplog):
    from django.contrib.auth import get_user
    from django.utils.functional import SimpleLazyObject

    from djust.sse import DjustSSEMessageView, DjustSSEStreamView, _sse_sessions

    url = f"/auto-{view_class.__name__}/"

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
    with (
        caplog.at_level(logging.WARNING, logger="djust"),
        harness.ws_settings(MODULE, ROOT_URLCONF=MODULE),
    ):
        first = await sync_to_async(request_for)(
            "GET",
            f"/djust/sse/{sid}/",
            {
                "view": f"{MODULE}.{view_class.__name__}",
                "_djust_url": url,
                "_djust_tab": harness.TAB,
            },
        )
        response = await DjustSSEStreamView().get(first, session_id=sid)
        assert response.status_code == 200
        key = first.session.session_key
        try:
            for _ in range(3):
                body = {"type": "event", "event": "inc", "params": {}}
                posted = await sync_to_async(request_for)(
                    "POST", f"/djust/sse/{sid}/message/", body, key
                )
                answer = await DjustSSEMessageView().post(posted, session_id=sid)
                assert answer.status_code in (200, 202, 204), answer.content
        finally:
            _sse_sessions.pop(sid, None)
    stored: Dict[str, Any] = await sync_to_async(_saved_envelope)(key)
    assert stored["values"]["count"] == 3
    for name in WARNED[view_class]:
        assert name not in stored["values"], stored["values"]
    if WARNED[view_class]:
        assert stored["remount"] is True
    # (Without one, ``remount`` is whatever the Plain control's SSE save records.)
    _assert_warned_once(view_class, caplog)


def test_the_encoder_bytes_for_values_that_were_saved_before_are_unchanged():
    """Only the failure path is new: a projection of ordinary values is the same text."""
    view = SurrogateOnEvent()
    view.count = 7
    view.note = "café ☃ \U0001f600"  # astral and BMP text encode as before
    projection = _automatic_state.project(view)
    assert projection.remount is False
    assert projection.text == ('[{"count":7,"note":"café ☃ \U0001f600"},{},false,{}]')


from django.urls import path as _path  # noqa: E402

urlpatterns = [_path(f"auto-{view.__name__}/", view.as_view()) for view in VIEWS]


# ---------------------------------------------------------------------------
# The legacy exposure policy: no automatic save, but the same turn paths walk ``__dict__``
# ---------------------------------------------------------------------------


class LegacyOdd(LiveView):
    exposure_policy = "legacy"
    template = "<div dj-root><pre>{{ count }}</pre></div>"

    def mount(self, request, **kwargs):
        self.count = 0
        self.note = LONE
        vars(self)[1] = "one"

    @event_handler()
    def inc(self, **kwargs):
        self.count += 1
        self.note = LONE
        vars(self)[3.5] = "three"


@pytest.mark.django_db
def test_a_legacy_view_with_odd_state_is_served_over_the_fallback():
    first = _request("GET", None)
    assert LegacyOdd().get(first).status_code == 200
    key = first.session.session_key
    for _ in range(2):
        answer = LegacyOdd().post(_request("POST", key, "inc"))
        assert answer.status_code == 200, answer.content


@pytest.mark.asyncio
@pytest.mark.django_db(transaction=True)
async def test_a_legacy_view_with_odd_state_is_served_over_the_websocket():
    key = await harness.new_session_key_async()
    with harness.ws_settings(MODULE):
        comm, _mount = await harness.mount_view(LegacyOdd, key)
        try:
            for _ in range(2):
                frame = await harness.event(comm, "inc")
                assert frame["type"] != "error", frame
        finally:
            await comm.disconnect()


# ---------------------------------------------------------------------------
# Every walk over a view's ``__dict__`` skips a key that is not a string: one test per site
# ---------------------------------------------------------------------------


def _odd(view: Any) -> Any:
    vars(view)[1] = "one"
    vars(view)[("a",)] = 2
    return view


def _explicit_view() -> Any:
    view = _odd(Plain())
    view.count = 1
    view._cursor = 2
    return view


def _legacy_view() -> Any:
    view = _odd(LegacyOdd())
    view.count = 1
    view.note = "fine"
    return view


def test_site_automatic_state_candidates():
    assert _automatic_state._candidates(_explicit_view()) == {"count": 1}


def test_site_automatic_state_held_components():
    assert _automatic_state._held_components(_explicit_view()) == {}


def test_site_automatic_state_component_values():
    class Holder:
        pass

    component = Holder()
    component.n = 3
    vars(component)[2] = "two"
    assert _automatic_state._component_values(component)[1] == {"n": 3}


def test_site_parameter_metadata_event_methods():
    from djust._parameter_metadata import _event_methods

    assert "inc" in _event_methods(_explicit_view())


def test_site_websocket_snapshot_assigns():
    from djust.websocket import _snapshot_assigns

    snapshot = _snapshot_assigns(_explicit_view())
    assert "count" in snapshot and all(type(key) is str for key in snapshot)


def test_site_live_view_dirty_fingerprint():
    fingerprint = _explicit_view()._dirty_fingerprint()
    assert "count" in fingerprint and all(type(key) is str for key in fingerprint)


def test_site_live_view_snapshot_user_private_attrs():
    view = _explicit_view()
    view._snapshot_user_private_attrs()
    assert "_cursor" in view._user_private_keys
    assert all(type(key) is str for key in view._user_private_keys)


def test_site_live_view_capture_snapshot_state_legacy():
    assert _legacy_view()._capture_snapshot_state() == {"count": 1, "note": "fine"}


def test_site_live_view_get_state_legacy():
    state = _legacy_view().get_state()
    assert state["count"] == 1 and all(type(key) is str for key in state)


def test_site_component_mixin_assign_component_ids():
    from djust.components.base import Component

    class Badge(Component):
        template = "<b>x</b>"

    view = _explicit_view()
    view.badge = Badge()
    vars(view)[7] = Badge()  # a component under a key that is not a name
    view._assign_component_ids()
    assert view.badge._auto_id == "badge"
    assert getattr(vars(view)[7], "_auto_id", None) is None


@pytest.mark.django_db
def test_site_context_mixin_get_context_data_legacy():
    view = _legacy_view()
    view.request = RequestFactory().get("/")
    context = view.get_context_data()
    assert context["count"] == 1 and all(type(key) is str for key in context)


def test_site_time_travel_restore_snapshot():
    from djust.time_travel import EventSnapshot, restore_snapshot

    view = _legacy_view()
    snapshot = EventSnapshot("inc", {}, None, 0.0, state_before={"count": 5, "note": "fine"})
    assert restore_snapshot(view, snapshot) is True
    assert view.count == 5 and vars(view)[1] == "one"
