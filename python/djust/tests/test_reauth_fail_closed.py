"""``reauth_on_event`` re-check fails CLOSED (#1777 follow-up).

The per-event re-check in ``LiveViewConsumer.handle_event`` used to swallow any
exception from the view's auth check (``Http404`` / ``DoesNotExist`` out of
``check_permissions``, a failing session or auth backend) at DEBUG and then run
the event, so a principal whose access had been revoked could still dispatch.
It now denies: the exception TYPE and view class are logged once at WARNING
(never the message or a traceback), the socket gets navigate-to-login and
``close(4403)``, and the handler does not run. The refusal frames are best
effort so a dead peer cannot turn the denial back into an allow.

Clean ``True`` / ``False`` results and the default-off behaviour are unchanged.
"""

from __future__ import annotations

import asyncio
import logging
import sys
from unittest.mock import AsyncMock, patch

import pytest

pytest.importorskip("channels")

from django.contrib.auth.models import AnonymousUser  # noqa: E402
from django.http import Http404  # noqa: E402
from django.test import override_settings  # noqa: E402

from djust import LiveView  # noqa: E402
from djust.config import config  # noqa: E402
from djust.decorators import event_handler  # noqa: E402
from djust.websocket import LiveViewConsumer  # noqa: E402

SECRET = "s3cret-object-title-do-not-log"
_STATE = {"ran": False, "exc": None}


class _DoesNotExist(Exception):
    """Stands in for ``Model.DoesNotExist``."""


class _GuardedView(LiveView):
    login_required = True
    template = '<div dj-view="djust.tests.test_reauth_fail_closed._GuardedView" dj-id="0">x</div>'

    def mount(self, request, **kwargs):
        self.count = 0

    def check_permissions(self, request):
        # Only misbehave once the test arms it, so the mount itself succeeds.
        if _STATE["exc"] is not None:
            raise _STATE["exc"]
        return True

    @event_handler
    def do_mutation(self, **kwargs):
        _STATE["ran"] = True


setattr(sys.modules[__name__], "_GuardedView", _GuardedView)
_VIEW_PATH = f"{__name__}._GuardedView"


class _User:
    is_authenticated = True
    is_active = True
    is_anonymous = False

    def has_perms(self, perms):
        return True


def _mw(app):
    async def mw(scope, receive, send):
        scope = dict(scope)
        scope["user"] = _User()
        scope.setdefault("session", {})
        return await app(scope, receive, send)

    return mw


async def _connect_and_mount():
    from channels.testing import WebsocketCommunicator

    comm = WebsocketCommunicator(_mw(LiveViewConsumer.as_asgi()), "/ws/")
    ok, _ = await comm.connect()
    assert ok
    try:
        await comm.receive_json_from(timeout=2)
    except Exception:  # noqa: BLE001
        pass
    await comm.send_json_to({"type": "mount", "view": _VIEW_PATH})
    resp = await comm.receive_json_from(timeout=2)
    assert resp.get("type") != "navigate", resp
    return comm


async def _drain(comm):
    outs = []
    for _ in range(6):
        try:
            outs.append(await comm.receive_output(timeout=1.5))
        except Exception:  # noqa: BLE001
            break
        if outs[-1]["type"] == "websocket.close":
            break
    return outs


@pytest.fixture(autouse=True)
def _reset_state():
    _STATE.update(ran=False, exc=None)
    yield
    _STATE.update(ran=False, exc=None)
    # Drop the config cached under this test's settings (reauth_on_event on), so
    # a later test in the same worker does not inherit it.
    config.reset()


def _cfg(on=True):
    return override_settings(
        LIVEVIEW_ALLOWED_MODULES=[__name__], LIVEVIEW_CONFIG={"reauth_on_event": on}
    )


RAISERS = [
    pytest.param(Http404(SECRET), id="Http404"),
    pytest.param(_DoesNotExist(SECRET), id="DoesNotExist"),
    pytest.param(RuntimeError(SECRET), id="RuntimeError"),
]


async def _fire_event(exc, caplog=None):
    comm = await _connect_and_mount()
    _STATE["exc"] = exc
    if caplog is not None:
        caplog.clear()
    await comm.send_json_to({"type": "event", "event": "do_mutation", "params": {}})
    outs = await _drain(comm)
    try:
        await comm.disconnect()
    except (Exception, asyncio.CancelledError):  # noqa: BLE001
        pass
    return outs


@pytest.mark.django_db
class TestRecheckFailsClosed:
    @pytest.mark.parametrize("exc", RAISERS)
    async def test_check_permissions_raising_denies(self, exc):
        with _cfg(), patch("channels.auth.get_user", AsyncMock(return_value=_User())):
            config.reset()
            try:
                outs = await _fire_event(exc)
            finally:
                config.reset()
        types = [o["type"] for o in outs]
        assert "websocket.close" in types, types
        assert outs[-1].get("code") == 4403
        assert any(
            o["type"] == "websocket.send" and "navigate" in (o.get("text") or "") for o in outs
        ), outs
        assert _STATE["ran"] is False, "a raising re-check must deny, not run the handler"

    async def test_get_user_raising_denies(self):
        with _cfg(), patch("channels.auth.get_user", AsyncMock(side_effect=RuntimeError(SECRET))):
            config.reset()
            try:
                outs = await _fire_event(None)
            finally:
                config.reset()
        assert outs[-1]["type"] == "websocket.close" and outs[-1].get("code") == 4403
        assert _STATE["ran"] is False

    async def test_failing_check_helper_denies(self):
        with (
            _cfg(),
            patch("channels.auth.get_user", AsyncMock(return_value=_User())),
            patch("djust.auth.core.check_view_auth_lightweight", side_effect=RuntimeError(SECRET)),
        ):
            config.reset()
            try:
                outs = await _fire_event(None)
            finally:
                config.reset()
        assert outs[-1]["type"] == "websocket.close" and outs[-1].get("code") == 4403
        assert _STATE["ran"] is False

    async def test_clean_true_still_allows(self):
        with _cfg(), patch("channels.auth.get_user", AsyncMock(return_value=_User())):
            config.reset()
            try:
                outs = await _fire_event(None)
            finally:
                config.reset()
        assert _STATE["ran"] is True
        assert not [o for o in outs if o["type"] == "websocket.close"]

    async def test_clean_false_still_denies(self):
        with _cfg(), patch("channels.auth.get_user", AsyncMock(return_value=AnonymousUser())):
            config.reset()
            try:
                outs = await _fire_event(None)
            finally:
                config.reset()
        assert outs[-1]["type"] == "websocket.close" and outs[-1].get("code") == 4403
        assert _STATE["ran"] is False

    async def test_default_off_never_rechecks(self):
        with (
            _cfg(False),
            patch("channels.auth.get_user", AsyncMock(side_effect=RuntimeError)) as gu,
        ):
            config.reset()
            try:
                await _fire_event(RuntimeError(SECRET))
            finally:
                config.reset()
        gu.assert_not_called()
        assert _STATE["ran"] is True

    async def test_refusal_frame_failure_still_denies(self):
        """A dead peer during the refusal must not turn the denial into an allow."""
        consumer = LiveViewConsumer()
        view = _GuardedView()
        view.request = type("R", (), {})()
        consumer.scope = {"session": {}}
        consumer.send_json = AsyncMock(side_effect=RuntimeError("peer gone"))
        consumer.close = AsyncMock(side_effect=RuntimeError("peer gone"))
        _STATE["exc"] = RuntimeError(SECRET)
        with patch("channels.auth.get_user", AsyncMock(return_value=_User())):
            assert await consumer._reauth_recheck(view) is False


@pytest.mark.django_db
class TestFailClosedLogging:
    @pytest.mark.parametrize("exc", RAISERS)
    async def test_warning_has_type_and_view_class_but_no_message(self, exc, caplog):
        with _cfg(), patch("channels.auth.get_user", AsyncMock(return_value=_User())):
            config.reset()
            try:
                with caplog.at_level(logging.DEBUG, logger="djust.websocket"):
                    await _fire_event(exc, caplog)
            finally:
                config.reset()
        records = [r for r in caplog.records if "reauth_on_event" in r.getMessage()]
        assert len(records) == 1, [r.getMessage() for r in caplog.records]
        record = records[0]
        assert record.levelno == logging.WARNING
        text = record.getMessage()
        assert type(exc).__name__ in text
        assert "_GuardedView" in text
        assert SECRET not in text + str(record.args)
        assert not record.exc_info, "no traceback: it carries the exception message"
        assert SECRET not in caplog.text

    async def test_clean_results_log_nothing(self, caplog):
        with _cfg(), patch("channels.auth.get_user", AsyncMock(return_value=_User())):
            config.reset()
            try:
                with caplog.at_level(logging.DEBUG, logger="djust.websocket"):
                    await _fire_event(None, caplog)
            finally:
                config.reset()
        assert not [r for r in caplog.records if "reauth_on_event" in r.getMessage()]
