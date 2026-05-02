"""Regression tests for #1281.

``_snapshot_assigns()`` previously used ``k.startswith("_")`` to skip
ALL underscore-prefixed attrs from change detection. This meant a handler
that mutated only private state (e.g. ``self._orders``) would produce
identical pre/post snapshots -> ``skip_render = True`` -> client received
a noop frame instead of a render.

The fix replaces the blanket prefix check with membership in
``view_instance._framework_attrs`` — the frozenset of ALL attribute names
present at ``LiveView.__init__`` time. Framework ``_``-prefixed attrs are
still excluded (they were present at init). User ``_``-prefixed attrs set
in ``mount()`` or event handlers are now included.
"""

from djust.live_view import LiveView
from djust.websocket import _snapshot_assigns


class _PrivateStateView(LiveView):
    """Minimal view that sets private state post-init."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.title = "Home"
        self._orders = []
        self._cache = {}


class TestFrameworkPrivateAttrsExcluded:
    """Framework ``_``-prefixed attrs are still excluded via ``_framework_attrs``."""

    def test_framework_private_attrs_excluded(self):
        view = LiveView()
        view.count = 0
        view.name = "test"
        snap = _snapshot_assigns(view)
        assert "_changed_keys" not in snap
        assert "_skip_render" not in snap

    def test_framework_public_attrs_excluded(self):
        """template_name etc. are covered by _FRAMEWORK_INTERNAL_ATTRS."""
        view = LiveView()
        view.count = 0
        snap = _snapshot_assigns(view)
        assert "template_name" not in snap
        assert "http_method_names" not in snap


class TestUserPrivateAttrsIncluded:
    """User ``_``-prefixed attrs set after __init__ ARE in the snapshot."""

    def test_user_private_attrs_in_snapshot(self):
        view = _PrivateStateView()
        snap = _snapshot_assigns(view)
        assert "_orders" in snap
        assert "_cache" in snap

    def test_user_private_attrs_not_in_framework_attrs(self):
        """User-set private attrs are NOT in _framework_attrs (proving
        they're not accidentally excluded by membership check)."""
        view = _PrivateStateView()
        assert "_orders" not in view._framework_attrs
        assert "_cache" not in view._framework_attrs

    def test_framework_private_attrs_are_in_framework_attrs(self):
        """Sanity: framework _-prefixed attrs ARE in _framework_attrs.

        Note: _framework_attrs itself, _skip_render, and _changed_keys
        are assigned AFTER the frozenset capture, so they're
        self-referentially absent. But other framework attrs set during
        __init__ (before the capture) are present.
        """
        view = LiveView()
        assert "_ws_consumer" in view._framework_attrs
        assert "_session_id" in view._framework_attrs


class TestChangeDetectionWithPrivateState:
    """Pre/post snapshot comparison detects private-state mutations."""

    def test_mutating_only_private_state_changes_snapshot(self):
        view = _PrivateStateView()
        view.title = "Before"

        pre = _snapshot_assigns(view)
        view._orders.append({"id": 1})  # mutate ONLY private state
        view._cache["key"] = "value"
        post = _snapshot_assigns(view)

        assert pre != post, (
            "#1281: mutating only _-prefixed user state must change the "
            "snapshot so skip_render is NOT triggered"
        )

    def test_mutating_nothing_keeps_snapshot_equal(self):
        view = _PrivateStateView()

        pre = _snapshot_assigns(view)
        post = _snapshot_assigns(view)

        assert pre == post, "no mutation -> snapshots should be equal -> noop"

    def test_mutating_public_state_changes_snapshot(self):
        view = LiveView()
        view.count = 0

        pre = _snapshot_assigns(view)
        view.count = 42
        post = _snapshot_assigns(view)

        assert pre != post, "public attr mutation must change snapshot"

    def test_mutating_both_public_and_private_changes_snapshot(self):
        view = _PrivateStateView()
        view.title = "Before"

        pre = _snapshot_assigns(view)
        view.title = "After"
        view._orders.append({"id": 1})
        post = _snapshot_assigns(view)

        assert pre != post

    def test_reassign_private_state_changes_snapshot(self):
        """Reassigning a _private attr (new id()) is detected."""
        view = _PrivateStateView()

        pre = _snapshot_assigns(view)
        view._orders = [{"id": 1}]  # reassign, not in-place mutate
        post = _snapshot_assigns(view)

        assert pre != post
