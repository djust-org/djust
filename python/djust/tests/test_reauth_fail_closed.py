"""``reauth_on_event`` re-check fails CLOSED (#1777 follow-up).

``recheck_event_auth`` on both transports used to swallow any non-
``PermissionDenied`` exception from the view's auth check (``Http404`` /
``DoesNotExist`` out of ``check_permissions``, a failing session backend, a
custom auth backend raising) and return ``True``, so the event ran for a
principal whose access had been revoked. It now denies: the exception TYPE and
view class are logged at WARNING (never the message), and the existing refusal
path runs (WS: navigate + ``close(4403)``; SSE: auth-error frame + close).

Clean ``True`` / ``False`` results are unchanged.
"""

from __future__ import annotations

import logging
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from django.http import Http404
from django.test import RequestFactory, override_settings

from djust import LiveView
from djust.config import config
from djust.runtime import SSESessionTransport, WSConsumerTransport

SECRET = "s3cret-object-title-do-not-log"


class _User:
    is_authenticated = True
    is_active = True

    def has_perms(self, perms):
        return True


class _DoesNotExist(Exception):
    """Stands in for ``Model.DoesNotExist``."""


def _view_cls(exc):
    class _GuardedView(LiveView):
        login_required = True
        template = "<div dj-root></div>"

        def check_permissions(self, request):
            if exc is None:
                return True
            raise exc

    return _GuardedView


def _request():
    request = RequestFactory().get("/")
    request.user = _User()
    return request


def _ws(view):
    consumer = MagicMock()
    consumer.scope = {"session": {}}
    consumer.send_json = AsyncMock()
    consumer.close = AsyncMock()
    view.request = _request()
    return WSConsumerTransport(consumer), consumer


def _sse(view):
    session = MagicMock()
    session._event_request = _request()
    session.send_error = AsyncMock()
    session.close = AsyncMock()
    return SSESessionTransport(session), session


async def _recheck(kind, view):
    if kind == "ws":
        transport, handle = _ws(view)
        with patch("channels.auth.get_user", AsyncMock(return_value=_User())):
            return await transport.recheck_event_auth(view), handle
    transport, handle = _sse(view)
    return await transport.recheck_event_auth(view), handle


def _assert_refused(kind, handle):
    if kind == "ws":
        navigate = handle.send_json.await_args.args[0]
        assert navigate["type"] == "navigate"
        assert handle.close.await_args.kwargs.get("code") == 4403
    else:
        handle.send_error.assert_awaited_once()
        # The error frame carries the structured refusal code (#3323); the
        # stream/close code stays 4403.
        assert handle.send_error.await_args.kwargs.get("code") == "permission_denied"
        handle.close.assert_awaited_once()
        assert handle.close.await_args.kwargs.get("code") == 4403


def _assert_not_refused(kind, handle):
    if kind == "ws":
        handle.send_json.assert_not_awaited()
        handle.close.assert_not_awaited()
    else:
        handle.send_error.assert_not_awaited()
        handle.close.assert_not_awaited()


@pytest.fixture(autouse=True)
def _reauth_on():
    with override_settings(LIVEVIEW_CONFIG={"reauth_on_event": True}):
        config.reset()
        yield
    config.reset()


RAISERS = [
    pytest.param(Http404(SECRET), id="Http404"),
    pytest.param(_DoesNotExist(SECRET), id="DoesNotExist"),
    pytest.param(RuntimeError(SECRET), id="RuntimeError"),
]


@pytest.mark.parametrize("kind", ["ws", "sse"])
class TestRecheckFailsClosed:
    @pytest.mark.asyncio
    @pytest.mark.parametrize("exc", RAISERS)
    async def test_check_permissions_raising_denies(self, kind, exc):
        view = _view_cls(exc)()
        ok, handle = await _recheck(kind, view)
        assert ok is False, "a raising re-check must deny, not allow the event"
        _assert_refused(kind, handle)

    @pytest.mark.asyncio
    async def test_failing_check_helper_denies(self, kind):
        """The whole check failing (e.g. a custom backend) denies too."""
        view = _view_cls(None)()
        with patch(
            "djust.auth.core.check_view_auth_lightweight",
            side_effect=RuntimeError(SECRET),
        ):
            ok, handle = await _recheck(kind, view)
        assert ok is False
        _assert_refused(kind, handle)

    @pytest.mark.asyncio
    async def test_clean_true_still_allows(self, kind):
        view = _view_cls(None)()
        ok, handle = await _recheck(kind, view)
        assert ok is True
        _assert_not_refused(kind, handle)

    @pytest.mark.asyncio
    async def test_clean_false_still_denies(self, kind):
        from django.core.exceptions import PermissionDenied

        view = _view_cls(PermissionDenied())()
        ok, handle = await _recheck(kind, view)
        assert ok is False
        _assert_refused(kind, handle)

    @pytest.mark.asyncio
    async def test_default_off_never_rechecks(self, kind):
        with override_settings(LIVEVIEW_CONFIG={"reauth_on_event": False}):
            config.reset()
            view = _view_cls(RuntimeError(SECRET))()
            ok, handle = await _recheck(kind, view)
        assert ok is True
        _assert_not_refused(kind, handle)

    @pytest.mark.asyncio
    async def test_refusal_frame_failure_still_denies(self, kind):
        """A dead peer during the refusal must not turn the denial into an allow."""
        view = _view_cls(RuntimeError(SECRET))()
        if kind == "ws":
            transport, handle = _ws(view)
            handle.send_json.side_effect = RuntimeError("peer gone")
            handle.close.side_effect = RuntimeError("peer gone")
            with patch("channels.auth.get_user", AsyncMock(return_value=_User())):
                assert await transport.recheck_event_auth(view) is False
        else:
            transport, handle = _sse(view)
            handle.send_error.side_effect = RuntimeError("peer gone")
            handle.close.side_effect = RuntimeError("peer gone")
            assert await transport.recheck_event_auth(view) is False


@pytest.mark.parametrize("kind", ["ws", "sse"])
class TestFailClosedLogging:
    @pytest.mark.asyncio
    @pytest.mark.parametrize("exc", RAISERS)
    async def test_warning_has_type_and_view_class_but_no_message(self, kind, exc, caplog):
        view = _view_cls(exc)()
        with caplog.at_level(logging.DEBUG, logger="djust.runtime"):
            await _recheck(kind, view)
        records = [r for r in caplog.records if "reauth_on_event" in r.getMessage()]
        assert len(records) == 1, [r.getMessage() for r in caplog.records]
        record = records[0]
        assert record.levelno == logging.WARNING
        text = record.getMessage()
        assert type(exc).__name__ in text
        assert "_GuardedView" in text
        assert SECRET not in text
        assert SECRET not in record.getMessage() + str(record.args)
        assert not record.exc_info, "no traceback: it carries the exception message"
        assert SECRET not in caplog.text

    @pytest.mark.asyncio
    async def test_clean_results_log_nothing(self, kind, caplog):
        view = _view_cls(None)()
        with caplog.at_level(logging.DEBUG, logger="djust.runtime"):
            await _recheck(kind, view)
        assert not [r for r in caplog.records if "reauth_on_event" in r.getMessage()]
