"""
Regression tests for #762 — framework-internal attrs leak into LiveView state.

Before this fix, ``LiveView.get_state()``, ``_snapshot_assigns`` and the
``state_sizes`` observability payload surfaced ~30 framework-set attrs
(``template_name``, ``http_method_names``, ``login_required``, ``on_mount_count``,
``page_meta``, etc.) as if they were reactive user state.

The non-breaking fix introduces ``live_view._FRAMEWORK_INTERNAL_ATTRS``, a
frozenset consulted by each of those code paths. Attribute names are unchanged.

These tests make sure:

* User-set assigns (``self.count = 5`` in ``mount``) still surface.
* Framework-set attrs do NOT surface in any of the three code paths.
"""

from djust import LiveView
from djust.live_view import _FRAMEWORK_INTERNAL_ATTRS
from djust.websocket import _snapshot_assigns


class TestGetStateFiltersFrameworkAttrs:
    """``LiveView.get_state()`` should return only user-owned reactive state."""

    def test_get_state_excludes_framework_attrs(self):
        """No framework-internal attr name appears in get_state() output."""

        class EmptyView(LiveView):
            template_name = "test.html"

            def mount(self, request, **kwargs):
                pass  # no user state — only framework-set attrs should exist

        view = EmptyView()
        view.mount(None)

        state = view.get_state()
        leaked = set(state) & _FRAMEWORK_INTERNAL_ATTRS
        assert not leaked, (
            f"get_state() leaked framework-internal attrs: {sorted(leaked)}. "
            f"These must be filtered via _FRAMEWORK_INTERNAL_ATTRS."
        )

    def test_get_state_includes_user_assigns(self):
        """User-set public attrs MUST still surface — this is the whole point."""

        class CounterView(LiveView):
            template_name = "test.html"

            def mount(self, request, **kwargs):
                self.count = 5
                self.name = "hello"

        view = CounterView()
        view.mount(None)

        state = view.get_state()
        assert state.get("count") == 5
        assert state.get("name") == "hello"

    def test_get_state_filters_django_view_inherited_attrs(self):
        """Django ``View``-inherited attrs (http_method_names, args, kwargs) filtered."""

        class FilteredView(LiveView):
            template_name = "test.html"

            def mount(self, request, **kwargs):
                self.user_attr = "visible"

        view = FilteredView()
        # Simulate what View.as_view() does — set framework attrs on __dict__
        view.http_method_names = ["get", "post"]
        view.args = ()
        view.kwargs = {}
        view.mount(None)

        state = view.get_state()
        assert "http_method_names" not in state
        assert "args" not in state
        assert "kwargs" not in state
        assert state.get("user_attr") == "visible"

    def test_get_state_filters_djust_config_attrs(self):
        """djust LiveView config attrs (sync_safe, login_required, template_name, ...) filtered."""

        class ConfigView(LiveView):
            template_name = "test.html"

            def mount(self, request, **kwargs):
                self.count = 1

        view = ConfigView()
        # Set the typical framework attrs that normally land on __dict__
        view.template_name = "test.html"
        view.sync_safe = True
        view.login_required = False
        view.use_actors = False
        view.view_is_async = False
        view.tick_interval = 0
        view.on_mount_count = 0
        view.page_meta = {}
        view.static_assigns = []
        view.temporary_assigns = {}
        view.mount(None)

        state = view.get_state()
        for framework_key in (
            "template_name",
            "sync_safe",
            "login_required",
            "use_actors",
            "view_is_async",
            "tick_interval",
            "on_mount_count",
            "page_meta",
            "static_assigns",
            "temporary_assigns",
        ):
            assert framework_key not in state, (
                f"get_state() still leaks framework attr '{framework_key}'"
            )
        assert state.get("count") == 1


class TestSnapshotAssignsFiltersFrameworkAttrs:
    """``_snapshot_assigns`` (WS change-detection) must also filter these."""

    def test_snapshot_assigns_skips_framework_attrs(self):
        class SnapshotView(LiveView):
            template_name = "test.html"

            def mount(self, request, **kwargs):
                self.visible = "user-state"

        view = SnapshotView()
        # Set framework attrs the same way as-view() or a mixin would
        view.template_name = "test.html"
        view.login_required = False
        view.http_method_names = ["get"]
        view.on_mount_count = 0
        view.mount(None)

        snapshot = _snapshot_assigns(view)
        leaked = set(snapshot) & _FRAMEWORK_INTERNAL_ATTRS
        assert not leaked, f"_snapshot_assigns leaked framework attrs: {sorted(leaked)}"
        assert "visible" in snapshot

    def test_snapshot_assigns_includes_user_assigns(self):
        """Non-underscore user-set attrs still show up in snapshot."""

        class UserView(LiveView):
            template_name = "test.html"

            def mount(self, request, **kwargs):
                self.count = 7
                self.items = [1, 2, 3]

        view = UserView()
        view.mount(None)

        snapshot = _snapshot_assigns(view)
        assert "count" in snapshot
        assert "items" in snapshot


class TestDebugStateSizesFiltersFrameworkAttrs:
    """The ``state_sizes`` observability payload (#762 signal source) must also filter."""

    def test_state_sizes_debug_payload_skips_framework_attrs(self):
        class DebugView(LiveView):
            template_name = "test.html"

            def mount(self, request, **kwargs):
                self.visible = "user"

        view = DebugView()
        # Simulate framework-set attrs ending up on __dict__
        view.template_name = "test.html"
        view.login_required = False
        view.http_method_names = ["get"]
        view.on_mount_count = 0
        view.page_meta = {}
        view.mount(None)

        sizes = view._debug_state_sizes()
        leaked = set(sizes) & _FRAMEWORK_INTERNAL_ATTRS
        assert not leaked, (
            f"_debug_state_sizes leaked framework attrs: {sorted(leaked)}. "
            f"Observability payloads must show user-reactive state only."
        )
        assert "visible" in sizes
