"""Foundation reproducer for v0.9.5-1a object-permission lifecycle (ADR-017, #1373).

Exercises the mount-time contract for the new
`get_object()` + `has_object_permission()` lifecycle hooks:

- `get_object()` returns the view's primary object (or None).
- `has_object_permission(request, obj)` is called when `get_object` is overridden.
- Denial raises `PermissionDenied` (caller in `websocket.py` translates that to
  close-code 4403 + "Permission denied" error frame, matching the existing
  pre-mount denial path at `websocket.py:1953-1955`).
- Allow proceeds normally; `self._object` is populated (cache slot).
- `_invalidate_object_cache()` resets `self._object` to None.
- `get_object() -> None` skips `has_object_permission` (404-shape pattern).
- Default no-override case sees zero behavior change.

These are unit-style tests against `check_object_permission(view, request)`
directly (the same pattern existing `check_permissions` tests use at
`tests/unit/test_sticky_preserve.py:902-938`). Per-event re-execution is
OUT OF SCOPE for v0.9.5-1a — that lands in v0.9.5-1b.

Expected on the parent commit (this file alone, no implementation): all
five tests FAIL with `ImportError: cannot import name 'check_object_permission'`
or `AttributeError: ... has no attribute '_object'`. Once Stage 5
implementation lands, all five must pass.
"""

import pytest
from django.contrib.auth.models import AnonymousUser
from django.core.exceptions import PermissionDenied

from djust import LiveView


# Stub object — get_object() doesn't require a Django model.
class _StubDocument:
    def __init__(self, pk: int, owner_id: int):
        self.pk = pk
        self.owner_id = owner_id


class _DenyView(LiveView):
    """Always denies via has_object_permission(...) -> False."""

    template = "<div>denied</div>"

    def mount(self, request, document_id: int = 0, **kwargs):
        self.document_id = int(document_id) or 1

    def get_object(self):
        return _StubDocument(pk=self.document_id, owner_id=999)

    def has_object_permission(self, request, obj):
        return False


class _AllowView(LiveView):
    """Always allows; should populate self._object."""

    template = "<div>{{ document_id }}</div>"

    def mount(self, request, document_id: int = 0, **kwargs):
        self.document_id = int(document_id) or 1

    def get_object(self):
        return _StubDocument(pk=self.document_id, owner_id=42)

    def has_object_permission(self, request, obj):
        return True


class _NoOverrideView(LiveView):
    """No override — control case for Decision 6 (zero behavior change)."""

    template = "<div>noop</div>"

    def mount(self, request, **kwargs):
        self.value = "ok"


class _CounterView(LiveView):
    """Counts get_object() calls so the cache-invalidate test can verify
    that get_object() actually re-runs after invalidation."""

    template = "<div>{{ value }}</div>"
    _calls: int = 0

    def mount(self, request, **kwargs):
        self.value = "ok"

    def get_object(self):
        type(self)._calls += 1
        return _StubDocument(pk=1, owner_id=42)

    def has_object_permission(self, request, obj):
        return True


class _NoneObjectView(LiveView):
    """get_object() returns None — should skip has_object_permission entirely.

    This is the OWASP IDOR-mitigation pattern: when an object doesn't exist
    OR the user can't tell whether it exists, return None and let the caller
    raise 404 (not 403, which would leak existence).
    """

    template = "<div>none</div>"

    def mount(self, request, **kwargs):
        pass

    def get_object(self):
        return None

    def has_object_permission(self, request, obj):
        # Should NEVER be called when get_object() returns None.
        raise AssertionError("has_object_permission must not be called when get_object()=None")


class _DoesNotExistView(LiveView):
    """get_object() raises Django's ObjectDoesNotExist (e.g. naive
    Model.objects.get(pk=missing)). The framework should catch it and
    treat the object as None — automating the 404-shape OWASP pattern.
    """

    template = "<div>missing</div>"

    def mount(self, request, **kwargs):
        pass

    def get_object(self):
        from django.core.exceptions import ObjectDoesNotExist

        raise ObjectDoesNotExist("simulated missing row")

    def has_object_permission(self, request, obj):
        # Should NEVER be called when get_object() raises DoesNotExist.
        raise AssertionError(
            "has_object_permission must not be called when get_object() raises ObjectDoesNotExist"
        )


# -- Helpers --------------------------------------------------------------


def _make_view(view_class, request, **mount_kwargs):
    """Instantiate + run mount, mirroring what handle_mount does."""
    view = view_class()
    view.request = request
    view.mount(request, **mount_kwargs)
    return view


# -- Tests ---------------------------------------------------------------


def test_check_object_permission_denial_raises(rf):
    """Case 1: has_object_permission()=False raises PermissionDenied.

    The websocket.py wrapper at the new post-mount call site translates
    this to a close-code 4403 + "Permission denied" error frame.
    """
    from djust.auth.core import check_object_permission

    request = rf.get("/")
    request.user = AnonymousUser()
    view = _make_view(_DenyView, request, document_id=1)

    with pytest.raises(PermissionDenied):
        check_object_permission(view, request)


def test_check_object_permission_allow_passes(rf):
    """Case 2: has_object_permission()=True passes; self._object cached."""
    from djust.auth.core import check_object_permission

    request = rf.get("/")
    request.user = AnonymousUser()
    view = _make_view(_AllowView, request, document_id=7)

    # Pre-condition: cache empty.
    assert view._object is None

    # Should not raise.
    check_object_permission(view, request)

    # Post-condition: cache populated with the object get_object() returned.
    assert view._object is not None
    assert view._object.pk == 7


def test_check_object_permission_no_override_is_noop(rf):
    """Case 3: views that don't override get_object see zero behavior change.

    Decision 6 (opt-in via override). The default `get_object() -> None`
    means `_has_custom_get_object()` short-circuits and `check_object_permission`
    returns immediately without calling either hook.
    """
    from djust.auth.core import check_object_permission

    request = rf.get("/")
    request.user = AnonymousUser()
    view = _make_view(_NoOverrideView, request)

    # Pre-condition: cache empty (and stays empty for non-overriding views).
    assert view._object is None

    # Should not raise; should not touch _object.
    check_object_permission(view, request)

    assert view._object is None
    # The default get_object() exists and returns None.
    assert view.get_object() is None
    # The default has_object_permission(...) returns True.
    assert view.has_object_permission(request, None) is True


def test_invalidate_object_cache_resets_to_none(rf):
    """Case 4: _invalidate_object_cache() resets _object; next call re-fetches."""
    from djust.auth.core import check_object_permission

    request = rf.get("/")
    request.user = AnonymousUser()
    _CounterView._calls = 0
    view = _make_view(_CounterView, request)

    # First check populates cache and runs get_object() once.
    check_object_permission(view, request)
    assert view._object is not None
    assert _CounterView._calls == 1

    # Invalidate the cache.
    view._invalidate_object_cache()
    assert view._object is None

    # Subsequent check re-runs get_object() (counter advances by 1).
    check_object_permission(view, request)
    assert view._object is not None
    assert _CounterView._calls == 2


def test_get_object_returning_none_skips_permission_check(rf):
    """Case 5: get_object() -> None does NOT call has_object_permission.

    This is the recommended OWASP IDOR-mitigation pattern: the user can't
    tell whether the object exists, so we return None (or raise 404 from
    get_object) rather than 403 from has_object_permission. The latter
    leaks existence.
    """
    from djust.auth.core import check_object_permission

    request = rf.get("/")
    request.user = AnonymousUser()
    view = _make_view(_NoneObjectView, request)

    # Should not raise. _NoneObjectView.has_object_permission raises
    # AssertionError if called — so passing here proves it wasn't called.
    check_object_permission(view, request)
    assert view._object is None


def test_get_object_raising_does_not_exist_treated_as_none(rf):
    """Case 6: get_object() raising ObjectDoesNotExist is caught and
    treated as None. The framework automates the OWASP 404-shape pattern
    rather than relying on developer discipline.

    Without this, a naive `Model.objects.get(pk=self.<x>_id)` in get_object
    would let DoesNotExist propagate to the outer Exception handler in
    websocket.handle_mount, where DEBUG=True mode would emit a traceback
    confirming the object's nonexistence.
    """
    from djust.auth.core import check_object_permission

    request = rf.get("/")
    request.user = AnonymousUser()
    view = _make_view(_DoesNotExistView, request)

    # Should not raise. _DoesNotExistView.has_object_permission raises
    # AssertionError if called — so passing here proves the exception
    # was caught and has_object_permission was NOT invoked.
    check_object_permission(view, request)
    assert view._object is None
