"""Regression: handler-level @permission_required check must run via
sync_to_async (sibling of #1638) — #1648.

`_validate_event_security` (async) called `check_handler_permission` bare-sync.
For a handler decorated with `@permission_required`, that calls
`user.has_perms(...)`, which under Django's default `ModelBackend` queries the
DB for a non-superuser — raising `SynchronousOnlyOperation` in the event loop.
Unlike the object-permission path, this call site had no fail-closed catch, so
the exception propagated out of `_validate_event_security`.

The fix wraps the call in `sync_to_async`, mirroring the #1638 object-permission
fix and the mount path.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest
from asgiref.sync import sync_to_async

from djust import LiveView
from djust.decorators import event_handler, permission_required


def _mock_ws():
    ws = MagicMock()
    ws.send_error = AsyncMock()
    ws.send_json = AsyncMock()
    ws.close = AsyncMock()
    ws._client_ip = "127.0.0.1"
    return ws


@pytest.mark.django_db
@pytest.mark.asyncio
async def test_permission_required_handler_does_not_raise_sync_only(rf):
    """A @permission_required handler whose perm check hits the DB (real
    non-superuser) must not raise SynchronousOnlyOperation on the per-event
    path."""
    from django.contrib.auth.models import Permission, User

    from djust.websocket_utils import _validate_event_security

    # Real, saved, non-superuser → has_perms() goes through ModelBackend → DB.
    user = await sync_to_async(User.objects.create_user)(username="perm-1648")
    perm = await sync_to_async(lambda: Permission.objects.get(codename="view_user"))()
    await sync_to_async(user.user_permissions.add)(perm)
    # Reload so the perm cache is clean (has_perms still queries the backend).
    user = await sync_to_async(User.objects.get)(pk=user.pk)

    class _PermView(LiveView):
        template = "<div dj-root>{{ x }}</div>"

        def mount(self, request, **kwargs):
            self.x = 1
            type(self)._ran = False

        @permission_required("auth.view_user")
        @event_handler()
        def do_thing(self, **kwargs):
            type(self)._ran = True

    view = _PermView()
    request = rf.get("/")
    request.user = user
    view.request = request
    view.mount(request)

    ws = _mock_ws()
    rl = MagicMock()
    rl.check_handler = MagicMock(return_value=True)
    rl.should_disconnect = MagicMock(return_value=False)

    # Pre-fix: bare-sync has_perms() in async → SynchronousOnlyOperation raised
    # straight out of _validate_event_security (no fail-closed catch here).
    handler = await _validate_event_security(ws, "do_thing", view, rl)

    # User HAS the perm → handler returned, no permission-denied frame.
    assert handler is not None, (
        "user with the required permission must pass the handler-level check "
        f"without raising; send_error calls: {ws.send_error.call_args_list}"
    )
    assert not ws.send_error.called


@pytest.mark.django_db
@pytest.mark.asyncio
async def test_permission_required_handler_denies_without_perm(rf):
    """Control: a non-superuser LACKING the perm is denied (no raise)."""
    from django.contrib.auth.models import User

    from djust.websocket_utils import _validate_event_security

    user = await sync_to_async(User.objects.create_user)(username="noperm-1648")

    class _PermView2(LiveView):
        template = "<div dj-root>{{ x }}</div>"

        def mount(self, request, **kwargs):
            self.x = 1
            type(self)._ran = False

        @permission_required("auth.view_user")
        @event_handler()
        def do_thing(self, **kwargs):
            type(self)._ran = True

    view = _PermView2()
    request = rf.get("/")
    request.user = user
    view.request = request
    view.mount(request)

    ws = _mock_ws()
    rl = MagicMock()
    rl.check_handler = MagicMock(return_value=True)
    rl.should_disconnect = MagicMock(return_value=False)

    handler = await _validate_event_security(ws, "do_thing", view, rl)
    assert handler is None
    assert ws.send_error.called
    assert _PermView2._ran is False
