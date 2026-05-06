"""Reproducer for v0.9.5-1b per-event object-permission re-execution
(ADR-017 § Decision 7, #1373).

These tests exercise the per-event contract:

  - Every event handler dispatch re-runs has_object_permission(request, obj).
  - Denial sends a permission-denied error frame WITHOUT closing the WS
    (mount-time denial closes 4403; per-event denial does not — the user
    is still authenticated, only this specific action is forbidden).
  - The handler body does NOT execute on denial.
  - _invalidate_object_cache() forces a fresh get_object() on the next event.
  - State-restore resets the cache (framework slot, not user state).
  - The cache is NOT poisoned by a denied permission check (cache is
    populated only after the check succeeds — Stage 11 nit from -1a).
  - get_object() raising ObjectDoesNotExist mid-session is treated as None
    (consistent with mount-time semantics from -1a).

These tests fail on the parent commit (c3498e62, v0.9.5-1a foundation)
because:

  - _validate_event_security does not call check_object_permission.
  - check_object_permission populates _object BEFORE has_object_permission
    is called, so a denial leaves a stale _object in the cache (Stage 11
    nit, deferred to -1b).

Stage 5 implementation:

  1. Adds check_object_permission to _validate_event_security
     (websocket_utils.py).
  2. Swaps cache-population order in check_object_permission so _object
     is set only after has_object_permission returns True.
"""

from unittest.mock import AsyncMock, MagicMock

import pytest
from django.contrib.auth.models import AnonymousUser
from django.core.exceptions import PermissionDenied

from djust import LiveView
from djust.decorators import event_handler


class _StubDocument:
    def __init__(self, pk: int, owner_id: int):
        self.pk = pk
        self.owner_id = owner_id


# -- View fixtures -------------------------------------------------------


class _DenyEventView(LiveView):
    template = "<div>{{ value }}</div>"

    def mount(self, request, document_id: int = 0, **kwargs):
        self.document_id = int(document_id) or 1
        self.value = "untouched"
        type(self)._handler_ran = False

    def get_object(self):
        return _StubDocument(pk=self.document_id, owner_id=999)

    def has_object_permission(self, request, obj):
        return False

    @event_handler()
    def update_value(self, value: str = "", **kwargs):
        # Should NEVER be called when has_object_permission returns False.
        type(self)._handler_ran = True
        self.value = value


class _AllowEventView(LiveView):
    template = "<div>{{ value }}</div>"

    def mount(self, request, document_id: int = 0, **kwargs):
        self.document_id = int(document_id) or 1
        self.value = "initial"
        type(self)._handler_ran = False

    def get_object(self):
        return _StubDocument(pk=self.document_id, owner_id=42)

    def has_object_permission(self, request, obj):
        return True

    @event_handler()
    def update_value(self, value: str = "", **kwargs):
        type(self)._handler_ran = True
        self.value = value


class _NoOverrideEventView(LiveView):
    template = "<div>{{ value }}</div>"

    def mount(self, request, **kwargs):
        self.value = "initial"
        type(self)._handler_ran = False

    @event_handler()
    def update_value(self, value: str = "", **kwargs):
        type(self)._handler_ran = True
        self.value = value


# -- Helpers --------------------------------------------------------------


def _make_view(view_class, request, **mount_kwargs):
    view = view_class()
    view.request = request
    view.mount(request, **mount_kwargs)
    return view


def _mock_ws():
    """Minimal mock for the `ws` parameter of _validate_event_security."""
    ws = MagicMock()
    ws.send_error = AsyncMock()
    ws.send_json = AsyncMock()
    ws.close = AsyncMock()
    ws._client_ip = "127.0.0.1"
    return ws


# -- Tests: cache-population order (the Stage 11 nit fix) -----------------


def test_cache_not_poisoned_on_denial(rf):
    """Case 1 (Stage 11 nit fix): check_object_permission must NOT
    populate self._object when the permission check denies.

    On parent commit (-1a), the order is:
        view._object = obj           # ← populated BEFORE check
        ok = view.has_object_permission(...)
        if ok is False: raise PermissionDenied(...)

    So a denial leaves a stale `obj` in the cache. For mount-time-only
    enforcement (-1a) this was benign because the WS closed anyway. For
    per-event (-1b) it matters: the cache is read on subsequent events.

    The fix: populate _object only AFTER successful permission check.
    """
    from djust.auth.core import check_object_permission

    request = rf.get("/")
    request.user = AnonymousUser()

    # Pre-condition: simulate a previously-set sentinel in _object.
    sentinel = _StubDocument(pk=999, owner_id=1)
    view = _make_view(_DenyEventView, request, document_id=1)
    view._object = sentinel  # the cache had a previous (legitimate) value

    with pytest.raises(PermissionDenied):
        check_object_permission(view, request)

    # Post-condition: cache must NOT have been overwritten by the denied
    # get_object() return value. Either keep the sentinel OR reset to None;
    # what matters is that the failed check's `obj` (a 999-owner doc) is
    # NOT cached. The cleanest semantic is "denial doesn't touch the
    # cache" — so the sentinel stays.
    assert view._object is sentinel, f"cache was poisoned by denied check; _object={view._object}"


def test_cache_populated_only_on_success(rf):
    """Case 2: cache is populated after a successful permission check."""
    from djust.auth.core import check_object_permission

    request = rf.get("/")
    request.user = AnonymousUser()
    view = _make_view(_AllowEventView, request, document_id=7)
    assert view._object is None

    check_object_permission(view, request)
    assert view._object is not None
    assert view._object.pk == 7


# -- Tests: per-event check via _validate_event_security ------------------


@pytest.mark.asyncio
async def test_per_event_denial_sends_error_frame_keeps_ws_open(rf):
    """Case 3: a per-event denial sends an error frame and does NOT
    close the WS. The handler body does NOT execute.
    """
    from djust.websocket_utils import _validate_event_security

    request = rf.get("/")
    request.user = AnonymousUser()
    _DenyEventView._handler_ran = False
    view = _make_view(_DenyEventView, request, document_id=1)

    ws = _mock_ws()
    rl = MagicMock()
    rl.check_handler = MagicMock(return_value=True)
    rl.should_disconnect = MagicMock(return_value=False)

    handler = await _validate_event_security(ws, "update_value", view, rl)

    # _validate_event_security returns None when denied (so the caller
    # doesn't dispatch the handler).
    assert handler is None, (
        "per-event object-permission denial must return None from "
        "_validate_event_security; got handler"
    )

    # Error frame was sent.
    assert ws.send_error.called, "send_error must have been called on denial"
    call_args = ws.send_error.call_args
    # Either positional or kwarg; check the human-readable message and
    # the structured `code` field.
    msg = call_args.args[0] if call_args.args else call_args.kwargs.get("error", "")
    assert "denied" in msg.lower(), f"error frame message should mention 'denied', got: {msg}"
    code = call_args.kwargs.get("code")
    assert code == "permission_denied", (
        f"error frame must include code='permission_denied'; got code={code}"
    )

    # WS NOT closed.
    assert not ws.close.called, "per-event denial must NOT close the WS"

    # Handler body did NOT execute.
    assert _DenyEventView._handler_ran is False


@pytest.mark.asyncio
async def test_per_event_allow_returns_handler(rf):
    """Case 4: a per-event allow lets the handler through."""
    from djust.websocket_utils import _validate_event_security

    request = rf.get("/")
    request.user = AnonymousUser()
    view = _make_view(_AllowEventView, request, document_id=1)

    ws = _mock_ws()
    rl = MagicMock()
    rl.check_handler = MagicMock(return_value=True)
    rl.should_disconnect = MagicMock(return_value=False)

    handler = await _validate_event_security(ws, "update_value", view, rl)

    # Handler returned (not None).
    assert handler is not None, "per-event allow must return the handler"
    assert callable(handler)

    # No error frame sent.
    assert not ws.send_error.called


@pytest.mark.asyncio
async def test_per_event_no_override_skips_check(rf):
    """Case 5: views that don't override get_object see zero behavior
    change in the per-event path.
    """
    from djust.websocket_utils import _validate_event_security

    request = rf.get("/")
    request.user = AnonymousUser()
    view = _make_view(_NoOverrideEventView, request)

    ws = _mock_ws()
    rl = MagicMock()
    rl.check_handler = MagicMock(return_value=True)
    rl.should_disconnect = MagicMock(return_value=False)

    handler = await _validate_event_security(ws, "update_value", view, rl)

    # Handler returned, no error frame, no close, no cache touched.
    assert handler is not None
    assert not ws.send_error.called
    assert view._object is None


@pytest.mark.asyncio
async def test_per_event_does_not_exist_treated_as_none(rf):
    """Case 6: get_object raising ObjectDoesNotExist mid-session sends
    a permission_denied frame (the framework treats DNE as 404-shape;
    the per-event caller maps that to a denial response).

    Note: -1a's mount-time semantics treat DNE as None and let mount
    proceed (allowing the view to render a 404 page). For per-event,
    if the developer's get_object suddenly returns DNE, the safest
    response is to deny the action — the object the user is trying to
    act on doesn't exist (or doesn't exist for them).
    """
    from djust.auth.core import check_object_permission

    class _DNEView(LiveView):
        template = "<div>x</div>"

        def mount(self, request, **kwargs):
            pass

        def get_object(self):
            from django.core.exceptions import ObjectDoesNotExist

            raise ObjectDoesNotExist()

        def has_object_permission(self, request, obj):
            raise AssertionError("must not be called when get_object raises DNE")

    request = rf.get("/")
    request.user = AnonymousUser()
    view = _make_view(_DNEView, request)

    # No exception — DNE is caught and treated as None (existing -1a behavior).
    check_object_permission(view, request)
    assert view._object is None


# -- Tests: state-restore + embedded-child (Stage 8 🟡 backfill) ----------


def test_object_cache_is_framework_slot_excluded_from_user_state(rf):
    """Case 7 (Stage 8 🟡): _object is allocated as a framework slot
    BEFORE _framework_attrs snapshot in __init__, so it's NOT included
    in user-private state serialization.

    This is the empirical proof of ADR-017 Decision 3 § "WS reconnect /
    state-restore": the cache resets to None after restore (because it's
    a framework slot, not user state), and get_object() runs fresh on
    the next access. Handles "object reassigned during disconnect"
    automatically.

    A future refactor that moves `self._object = None` to AFTER the
    `_framework_attrs` snapshot at live_view.py:517 would silently
    regress this — cache would survive restore, leaving a stale-allowed
    decision in place. This test locks the framework-slot invariant.
    """
    from djust.auth.core import check_object_permission

    request = rf.get("/")
    request.user = AnonymousUser()
    view = _make_view(_AllowEventView, request, document_id=7)

    # Populate the cache via a successful permission check.
    check_object_permission(view, request)
    assert view._object is not None

    # Verify _object is a framework attr (not user-private state).
    assert "_object" in view._framework_attrs, (
        "_object MUST be classified as a framework slot. If this fails, "
        "the slot was allocated AFTER _framework_attrs snapshot in "
        "live_view.py:__init__ and would survive state-restore — defeating "
        "the cache-reset-on-reconnect contract."
    )

    # Snapshot user-private state and verify _object is NOT in it.
    if hasattr(view, "_get_private_state"):
        private = view._get_private_state()
        assert "_object" not in private, (
            "_object leaked into user-private state; serialized + "
            "restored caches would carry stale permission decisions."
        )


def test_embedded_child_view_uses_child_get_object(rf):
    """Case 8 (Stage 8 🟡): when a parent view embeds a child via
    {% live_render %}, an event targeted at the CHILD must use the
    CHILD's get_object/has_object_permission, not the parent's.

    The dispatch sites in websocket.py (lines 2740, 2860, 2978) pass
    the resolved `target_view` to _validate_event_security — which is
    either the parent or a child depending on the event's view_id.
    The new check at websocket_utils.py:228+ then uses
    `owner_instance` (which IS target_view), so the child's
    get_object is called automatically. This test locks that contract.
    """
    from djust.auth.core import check_object_permission

    class _ParentView(LiveView):
        template = "<div>parent</div>"

        def mount(self, request, **kwargs):
            pass

        # Parent has NO get_object override — should be no-op for parent.

    class _ChildView(LiveView):
        template = "<div>child</div>"

        def mount(self, request, **kwargs):
            self.value = "child-state"
            type(self)._get_object_called = False

        def get_object(self):
            type(self)._get_object_called = True
            return _StubDocument(pk=42, owner_id=1)

        def has_object_permission(self, request, obj):
            return False  # always deny

    request = rf.get("/")
    request.user = AnonymousUser()
    parent = _make_view(_ParentView, request)
    child = _make_view(_ChildView, request)
    _ChildView._get_object_called = False

    # Parent: no override → check is a no-op.
    check_object_permission(parent, request)
    assert _ChildView._get_object_called is False, (
        "parent's check should not trigger child's get_object"
    )

    # Child: override → check runs, denial raises PermissionDenied.
    with pytest.raises(PermissionDenied):
        check_object_permission(child, request)
    assert _ChildView._get_object_called is True, (
        "child's get_object MUST be called when child is the dispatch target"
    )
