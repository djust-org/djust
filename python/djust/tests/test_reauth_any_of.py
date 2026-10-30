"""``reauth_on_event`` re-checks a view whose ``permission_required`` is ``AnyOf``.

The re-check runs only for views that declare ``login_required`` or
``permission_required``; an "any of" rule written in ``check_permissions()``
alone was never re-checked. Declared as ``AnyOf``, it is: losing every one of
the permissions mid-session refuses the next event on both transports.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from django.test import RequestFactory, override_settings

from djust import LiveView
from djust.auth import AnyOf
from djust.config import config
from djust.runtime import SSESessionTransport, WSConsumerTransport


class _User:
    is_authenticated = True
    is_active = True

    def __init__(self, perms):
        self.perms = set(perms)

    def has_perm(self, perm):
        return perm in self.perms

    def has_perms(self, perms):
        return all(p in self.perms for p in perms)


class _View(LiveView):
    permission_required = AnyOf("runs.agent_run", "chat.chat_general")
    template = "<div dj-root></div>"


def _request(user):
    request = RequestFactory().get("/")
    request.user = user
    return request


async def _recheck(kind, user):
    view = _View()
    if kind == "ws":
        consumer = MagicMock()
        consumer.scope = {"session": {}}
        consumer.send_json = AsyncMock()
        consumer.close = AsyncMock()
        view.request = _request(user)
        with patch("channels.auth.get_user", AsyncMock(return_value=user)):
            ok = await WSConsumerTransport(consumer).recheck_event_auth(view)
        return ok, consumer.close
    session = MagicMock()
    session._event_request = _request(user)
    session.send_error = AsyncMock()
    session.close = AsyncMock()
    ok = await SSESessionTransport(session).recheck_event_auth(view)
    return ok, session.close


@pytest.fixture(autouse=True)
def _reauth_on():
    with override_settings(LIVEVIEW_CONFIG={"reauth_on_event": True}):
        config.reset()
        yield
    config.reset()


@pytest.mark.parametrize("kind", ["ws", "sse"])
@pytest.mark.asyncio
async def test_one_remaining_permission_allows(kind):
    ok, close = await _recheck(kind, _User(["chat.chat_general"]))
    assert ok is True
    close.assert_not_awaited()


@pytest.mark.parametrize("kind", ["ws", "sse"])
@pytest.mark.asyncio
async def test_losing_every_permission_refuses(kind):
    ok, close = await _recheck(kind, _User([]))
    assert ok is False
    assert close.await_args.kwargs.get("code") == 4403
