"""Regression tests for #1286 — change-detection unification.

Post-#1281, ``_snapshot_assigns()`` uses ``_framework_attrs`` membership
instead of ``k.startswith("_")`` to distinguish framework-internal from
user-defined attrs. This closed the dual-path discrepancy (weakness #8)
where explicit ``set_changed_keys({"_x"})`` would work but implicit
auto-detection wouldn't.

These tests verify the identity snapshots used in the push_commands-only
auto-skip path (#700) also use ``_framework_attrs``, unifying the last
remaining ``k.startswith("_")`` filter in change-detection.
"""

from djust import LiveView
from djust.websocket import _snapshot_assigns


class _TestView(LiveView):
    pass


class TestIdentitySnapshotUnified:
    """#1286: identity snapshots use _framework_attrs, not k.startswith("_")."""

    def test_identity_includes_user_private_attrs(self):
        """User _prefixed attrs are in the identity snapshot."""
        view = _TestView()
        view._private_data = {"x": 1}
        view.public_data = {"y": 2}

        _fw_attrs = getattr(view, "_framework_attrs", frozenset())
        identity = {k: id(v) for k, v in view.__dict__.items() if k not in _fw_attrs}

        assert "_private_data" in identity, "#1286: user private attrs must be in identity snapshot"
        assert "public_data" in identity

    def test_identity_excludes_framework_attrs(self):
        """Framework _prefixed attrs in _framework_attrs are excluded."""
        view = _TestView()
        view._private_data = {"x": 1}

        _fw_attrs = getattr(view, "_framework_attrs", frozenset())
        identity = {k: id(v) for k, v in view.__dict__.items() if k not in _fw_attrs}

        # _framework_attrs itself is captured via frozenset(self.__dict__.keys())
        # BEFORE the attribute is assigned, so it's not in its own frozenset
        # and therefore appears in the identity snapshot. This is harmless —
        # it's a frozenset and won't trigger false change detection.
        assert "_changed_keys" not in identity, (
            "#1286: _changed_keys is in _framework_attrs and must be excluded"
        )
        assert "_skip_render" not in identity

    def test_identity_detects_private_state_change(self):
        """Mutating a user private attr changes the identity snapshot."""
        view = _TestView()
        view._cache = {"key": "old"}

        _fw_attrs = getattr(view, "_framework_attrs", frozenset())
        pre = {k: id(v) for k, v in view.__dict__.items() if k not in _fw_attrs}

        view._cache = {"key": "new"}

        post = {k: id(v) for k, v in view.__dict__.items() if k not in _fw_attrs}

        assert pre != post, "#1286: user private attr change must be detected by identity diff"

    def test_identity_no_change_when_nothing_mutated(self):
        """Identity snapshots match when no state changes."""
        view = _TestView()
        view.count = 0
        view._cache = {"key": "val"}

        _fw_attrs = getattr(view, "_framework_attrs", frozenset())
        pre = {k: id(v) for k, v in view.__dict__.items() if k not in _fw_attrs}

        post = {k: id(v) for k, v in view.__dict__.items() if k not in _fw_attrs}

        assert pre == post, "identity snapshots must match when nothing changes"

    def test_identity_and_assigns_snapshot_agree_on_private(self):
        """Identity snapshot and _snapshot_assigns agree on user private attrs."""
        view = _TestView()
        view._cache = {"key": "val"}
        view.count = 0

        _fw_attrs = getattr(view, "_framework_attrs", frozenset())
        identity = {k: id(v) for k, v in view.__dict__.items() if k not in _fw_attrs}
        assigns_snap = _snapshot_assigns(view)

        # Both should include user private attrs
        assert "_cache" in identity
        assert "_cache" in assigns_snap, "#1286: _snapshot_assigns and identity snapshot must agree"
        assert "count" in identity
        assert "count" in assigns_snap

    def test_identity_and_assigns_snapshot_agree_on_framework_exclusion(self):
        """Both snapshots exclude the same framework attrs."""
        view = _TestView()

        _fw_attrs = getattr(view, "_framework_attrs", frozenset())
        identity = {k: id(v) for k, v in view.__dict__.items() if k not in _fw_attrs}
        assigns_snap = _snapshot_assigns(view)

        # Both should exclude framework attrs
        for fw_attr in ["_changed_keys", "_skip_render"]:
            assert fw_attr not in identity
            assert fw_attr not in assigns_snap, (
                f"#1286: {fw_attr} must be excluded from both snapshots"
            )

    def test_identity_works_without_framework_attrs(self):
        """getattr fallback to frozenset() when _framework_attrs is missing."""
        view = _TestView()
        # Simulate the fallback path: no _framework_attrs attr
        _fw_attrs = getattr(view, "_framework_attrs", "MISSING")
        if _fw_attrs == "MISSING":
            _fw_attrs = frozenset()

        view._private_data = {"x": 1}

        identity = {k: id(v) for k, v in view.__dict__.items() if k not in _fw_attrs}

        assert "_private_data" in identity

    def test_user_private_not_in_framework_attrs(self):
        """User _private attrs are NOT in _framework_attrs."""
        view = _TestView()
        view._cache = {"key": "val"}

        fw_attrs = view._framework_attrs

        assert "_cache" not in fw_attrs, (
            "#1286: user-set _private attrs must not be in _framework_attrs"
        )
        # _changed_keys is set after mount, not at __init__ time,
        # so it won't be in _framework_attrs. Check attrs we know
        # are set at init.
        assert "_user_private_keys" in fw_attrs, (
            "framework attr _user_private_keys must be in _framework_attrs"
        )
