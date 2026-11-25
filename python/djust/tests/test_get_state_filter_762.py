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
from djust.decorators import state
from djust.live_view import _FRAMEWORK_INTERNAL_ATTRS
from djust.websocket import _snapshot_assigns


class TestGetStateFiltersFrameworkAttrs:
    """``LiveView.get_state()`` exports only the ``client=True`` fields (explicit exposure)."""

    def test_get_state_exports_only_client_fields_never_framework_attrs(self):
        class CounterView(LiveView):
            exposure_policy = "explicit"
            template_name = "test.html"
            shown = state("visible", client=True)

            def mount(self, request, **kwargs):
                self.count = 5  # an ordinary attribute: not exported
                self.name = "hello"

        view = CounterView()
        # Simulate what View.as_view() and a mixin do: framework attrs on __dict__.
        view.http_method_names = ["get", "post"]
        view.args = ()
        view.kwargs = {}
        view.sync_safe = True
        view.login_required = False
        view.on_mount_count = 0
        view.page_meta = {}
        view.mount(None)

        exported = view.get_state()
        assert exported == {"shown": "visible"}
        assert not set(exported) & _FRAMEWORK_INTERNAL_ATTRS


class TestSnapshotAssignsFiltersFrameworkAttrs:
    """``_snapshot_assigns`` (WS change-detection) must also filter these."""

    def test_snapshot_assigns_skips_framework_attrs(self):
        class SnapshotView(LiveView):
            exposure_policy = "explicit"
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
            exposure_policy = "explicit"
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
            exposure_policy = "explicit"
            template_name = "test.html"
            visible = state("user", client=True)

            def mount(self, request, **kwargs):
                self.hidden = "not exported"

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
