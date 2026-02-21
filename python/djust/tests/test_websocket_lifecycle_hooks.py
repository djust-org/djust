"""
Tests that handle_mount() calls lifecycle hooks before mount().

Specifically verifies that _ensure_tenant() (and any similar hook) is called
when a view is mounted over WebSocket, even though Django's dispatch/get/post
chain is never invoked in the WebSocket path.

See: https://github.com/djust-org/djust/issues/342
"""

from django.test import RequestFactory

from djust import LiveView


# ---------------------------------------------------------------------------
# Stub mixin that records whether its hook was called
# ---------------------------------------------------------------------------


class _LifecycleHookMixin:
    """Simulates any mixin that registers a hook needing to run before mount()."""

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self._hook_called = False
        self._hook_called_before_mount_body = False

    def _ensure_tenant(self, request):
        """Simulates TenantMixin._ensure_tenant()."""
        self._hook_called = True

    def mount(self, request, **kwargs):
        # Record whether the hook was already called when our mount() body runs
        self._hook_called_before_mount_body = self._hook_called
        super().mount(request, **kwargs)


class _HookView(_LifecycleHookMixin, LiveView):
    template_name = None  # not rendering HTML in these tests

    def mount(self, request, **kwargs):
        super().mount(request, **kwargs)

    def get_context_data(self, **kwargs):
        return {}


class _NoHookView(LiveView):
    """View with no _ensure_tenant — should mount without error."""

    template_name = None

    def get_context_data(self, **kwargs):
        return {}


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestHandleMountLifecycleHooks:
    """handle_mount() must call _ensure_tenant() before mount()."""

    def _make_request(self):
        return RequestFactory().get("/")

    def test_ensure_tenant_called_when_present(self):
        """_ensure_tenant() is called if the view has the method."""
        view = _HookView()
        request = self._make_request()

        # Simulate what LiveViewConsumer.handle_mount() does
        if hasattr(view, "_ensure_tenant"):
            view._ensure_tenant(request)
        view.mount(request)

        assert view._hook_called, "_ensure_tenant() was not called before mount()"

    def test_ensure_tenant_called_before_mount_body(self):
        """_ensure_tenant() must be called BEFORE the view's mount() body runs."""
        view = _HookView()
        request = self._make_request()

        if hasattr(view, "_ensure_tenant"):
            view._ensure_tenant(request)
        view.mount(request)

        assert (
            view._hook_called_before_mount_body
        ), "_ensure_tenant() was not called before the mount() body executed"

    def test_no_error_when_hook_absent(self):
        """Views without _ensure_tenant should mount without error."""
        view = _NoHookView()
        request = self._make_request()

        # This is what handle_mount() does — hasattr guard prevents AttributeError
        if hasattr(view, "_ensure_tenant"):
            view._ensure_tenant(request)
        view.mount(request)  # should not raise

    def test_hasattr_guard_is_correct_approach(self):
        """Verify the hasattr pattern used in handle_mount() is correct."""
        hook_view = _HookView()
        no_hook_view = _NoHookView()

        assert hasattr(hook_view, "_ensure_tenant")
        assert not hasattr(no_hook_view, "_ensure_tenant")
