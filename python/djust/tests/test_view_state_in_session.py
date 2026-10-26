"""``view_state_in_session=False`` keeps view state out of the Django session.

With the option off djust never writes or reads the ``liveview_<path>`` key or
its ``__private`` / ``__private_unsaved`` / ``_components`` siblings: not on
the first GET, not on the page-POST fallback, not on a WebSocket / SSE turn.
Every fallback POST then mounts again, applies the event and renders. The
default (``True``) keeps the long-standing behaviour, pinned here as the
gate-off pair of each test.
"""

from __future__ import annotations

import json
import sys

import pytest
from asgiref.sync import sync_to_async
from django.test import RequestFactory, override_settings

from djust import LiveView
from djust.decorators import event_handler

pytestmark = pytest.mark.django_db

STATELESS = override_settings(LIVEVIEW_CONFIG={"view_state_in_session": False})
PATH = "/stateless/"


def _view_keys(session) -> list:
    return sorted(k for k in session.keys() if k.startswith("liveview_"))


class _Client:
    """A non-JSON private attribute (the session could never hold it)."""

    def greet(self):
        return "hi"


class CounterView(LiveView):
    """Public, JSON private and non-JSON private state, all set in mount()."""

    enable_state_snapshot = True  # the socket save is opt-in; prove it is still skipped
    template = '<div dj-root dj-id="0"><p>{{ count }}</p><p>{{ greeting }}</p></div>'
    mounts = 0

    def mount(self, request, **kwargs):
        type(self).mounts += 1
        self.count = 0
        self.greeting = ""
        self._seen = []
        self._client = _Client()

    @event_handler()
    def bump(self, **kwargs):
        self.count += 1
        self._seen.append(self.count)
        # A socket restore does not run mount(), so the default-path test
        # restores ``_client`` as None from its planted private state.
        self.greeting = (self._client or _Client()).greet()

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["count"] = self.count
        ctx["greeting"] = self.greeting
        return ctx


setattr(sys.modules[__name__], "CounterView", CounterView)
VIEW_PATH = f"{__name__}.CounterView"


def _get_request():
    from django.contrib.auth.models import AnonymousUser
    from django.contrib.sessions.backends.db import SessionStore

    request = RequestFactory().get(PATH)
    request.user = AnonymousUser()
    request.tenant = None
    request.session = SessionStore()
    request.session.create()
    return request


def _post(initial, event):
    request = RequestFactory().post(
        PATH, data=json.dumps({}), content_type="application/json", HTTP_X_DJUST_EVENT=event
    )
    request.user, request.session, request.tenant = initial.user, initial.session, None
    view = CounterView()
    return view, view.post(request)


# --------------------------------------------------------------------------- #
# HTTP: first GET and page-POST fallback
# --------------------------------------------------------------------------- #


def test_stateless_get_and_fallback_post_write_no_view_keys():
    with STATELESS:
        initial = _get_request()
        CounterView.mounts = 0
        response = CounterView().get(initial)
        assert response.status_code == 200
        assert _view_keys(initial.session) == []

        view, first = _post(initial, "bump")
        assert first.status_code == 200, first.content
        assert view.count == 1 and view.greeting == "hi"
        assert _view_keys(initial.session) == []

        # Every POST mounts again: the count does not accumulate.
        view, second = _post(initial, "bump")
        assert second.status_code == 200, second.content
        assert view.count == 1 and view._seen == [1]
        assert CounterView.mounts == 3  # GET + two POSTs
        assert _view_keys(initial.session) == []


def test_stateless_fallback_post_ignores_view_keys_already_in_the_session():
    """A key left from before the switch is never read back."""
    with STATELESS:
        initial = _get_request()
        initial.session[f"liveview_{PATH}"] = {"count": 41, "greeting": "stale"}
        initial.session[f"liveview_{PATH}__private"] = {"_seen": [9]}
        view, response = _post(initial, "bump")
        assert response.status_code == 200, response.content
        assert view.count == 1 and view._seen == [1]


def test_default_keeps_saving_view_state_in_the_session():
    initial = _get_request()
    CounterView.mounts = 0
    CounterView().get(initial)
    assert f"liveview_{PATH}" in _view_keys(initial.session)

    _post(initial, "bump")
    view, _ = _post(initial, "bump")
    assert view.count == 2  # restored from the session
    assert initial.session[f"liveview_{PATH}"]["count"] == 2
    assert f"liveview_{PATH}__private" in _view_keys(initial.session)


def test_djust_config_is_read_too():
    with override_settings(DJUST_CONFIG={"view_state_in_session": False}):
        initial = _get_request()
        CounterView().get(initial)
        _post(initial, "bump")
        assert _view_keys(initial.session) == []


# --------------------------------------------------------------------------- #
# WebSocket / SSE: the runtime mount and event turn
# --------------------------------------------------------------------------- #


async def _socket_mount_and_event(session):
    from djust.tests.test_stale_baseline_restore_1977 import MockTransport
    from djust.runtime import ViewRuntime

    def _request():
        from django.contrib.auth.models import AnonymousUser

        request = RequestFactory().get(PATH)
        request.user = AnonymousUser()
        request.session = session
        return request

    request = await sync_to_async(_request)()
    transport = MockTransport(request)
    runtime = ViewRuntime(transport, scope=None)
    with override_settings(LIVEVIEW_ALLOWED_MODULES=["djust"]):
        await runtime.dispatch_mount({"type": "mount", "view": VIEW_PATH, "url": PATH})
        await runtime.dispatch_event({"type": "event", "event": "bump", "params": {}, "ref": 1})
    return runtime, transport


def _db_session():
    from django.contrib.sessions.backends.db import SessionStore

    s = SessionStore()
    s.create()
    return s


@pytest.mark.asyncio
async def test_stateless_socket_turn_writes_and_reads_no_view_keys(generous_save_timeout):
    with STATELESS:
        session = await sync_to_async(_db_session)()
        await session.aset(f"liveview_{PATH}", {"count": 41, "greeting": "stale"})
        runtime, transport = await _socket_mount_and_event(session)

        view = runtime.view_instance
        assert view._mounted_from_restore is False, "the saved key must not be read"
        assert view.count == 1 and view.greeting == "hi"
        assert not transport.errors, transport.errors
        assert transport.frames_of("patch", "html_update"), transport.sent
        # Only the planted key; the turn wrote nothing (it would have been count=1).
        keys = await sync_to_async(_view_keys)(session)
        assert keys == [f"liveview_{PATH}"]
        assert (await session.aget(f"liveview_{PATH}"))["count"] == 41


@pytest.mark.asyncio
async def test_default_socket_turn_saves_and_restores(generous_save_timeout):
    session = await sync_to_async(_db_session)()
    await session.aset(f"liveview_{PATH}", {"count": 41, "greeting": "x"})
    await session.aset(f"liveview_{PATH}__private", {"_seen": [41], "_client": None})
    runtime, _transport = await _socket_mount_and_event(session)

    view = runtime.view_instance
    assert view._mounted_from_restore is True
    assert view.count == 42
    assert (await session.aget(f"liveview_{PATH}"))["count"] == 42


# --------------------------------------------------------------------------- #
# System check C027
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("value", ["false", 0, "no", 1])
def test_c027_reports_a_value_that_is_not_a_bool(value):
    from djust.checks.configuration import _check_view_state_in_session
    from djust.config import view_state_in_session

    with override_settings(LIVEVIEW_CONFIG={"view_state_in_session": value}):
        errors: list = []
        _check_view_state_in_session(errors)
        assert [e.id for e in errors] == ["djust.C027"]
        assert view_state_in_session() is True  # only False turns it off


@pytest.mark.parametrize("value", [True, False])
def test_c027_accepts_a_bool(value):
    from djust.checks.configuration import _check_view_state_in_session
    from djust.config import view_state_in_session

    with override_settings(LIVEVIEW_CONFIG={"view_state_in_session": value}):
        errors: list = []
        _check_view_state_in_session(errors)
        assert errors == []
        assert view_state_in_session() is value


def test_c027_is_silent_when_unset():
    from djust.checks.configuration import _check_view_state_in_session

    errors: list = []
    _check_view_state_in_session(errors)
    assert errors == []
