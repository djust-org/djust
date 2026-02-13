"""
Security tests for LiveView mount validation.

Verifies that only LiveView subclasses can be mounted, preventing
arbitrary class instantiation attacks.
"""

import pytest
from django.test import override_settings
from channels.testing import WebsocketCommunicator

from djust import LiveView
from djust.websocket import LiveViewConsumer


class ValidView(LiveView):
    """A valid LiveView for testing."""

    template_string = (
        '<div dj-view="python.tests.test_security_mount_validation.ValidView">Valid</div>'
    )

    def mount(self, request, **kwargs):
        self.data = "safe"


@pytest.mark.django_db
@pytest.mark.asyncio
class TestMountSecurityValidation:
    """Test that only LiveView subclasses can be mounted."""

    @override_settings(LIVEVIEW_ALLOWED_MODULES=[])
    @pytest.mark.skip(reason="ValidView template rendering needs investigation")
    async def test_valid_liveview_can_mount(self):
        """Valid LiveView subclasses can be mounted."""
        communicator = WebsocketCommunicator(LiveViewConsumer.as_asgi(), "/ws/")
        connected, _ = await communicator.connect()
        assert connected

        # Receive connect message first
        connect_msg = await communicator.receive_json_from(timeout=2)
        assert connect_msg.get("type") == "connect"

        # Mount a valid LiveView
        await communicator.send_json_to(
            {"type": "mount", "view": "python.tests.test_security_mount_validation.ValidView"}
        )

        response = await communicator.receive_json_from(timeout=2)
        assert response.get("type") == "mount"
        assert "html" in response

        await communicator.disconnect()

    @override_settings(LIVEVIEW_ALLOWED_MODULES=[])
    async def test_non_liveview_class_rejected(self):
        """Non-LiveView classes are rejected."""
        communicator = WebsocketCommunicator(LiveViewConsumer.as_asgi(), "/ws/")
        connected, _ = await communicator.connect()
        assert connected

        # Receive connect message
        await communicator.receive_json_from(timeout=2)

        # Try to mount a non-LiveView class (built-in dict)
        await communicator.send_json_to({"type": "mount", "view": "builtins.dict"})

        response = await communicator.receive_json_from(timeout=2)
        assert response.get("type") == "error"
        # _safe_error() sanitizes to "Invalid view class"
        assert "invalid" in response.get("error", "").lower()

        await communicator.disconnect()

    @override_settings(LIVEVIEW_ALLOWED_MODULES=[])
    async def test_builtin_function_rejected(self):
        """Built-in functions are rejected (e.g., os.system)."""
        communicator = WebsocketCommunicator(LiveViewConsumer.as_asgi(), "/ws/")
        connected, _ = await communicator.connect()
        assert connected

        # Receive connect message
        await communicator.receive_json_from(timeout=2)

        # Try to mount os.system (security vulnerability if allowed)
        await communicator.send_json_to({"type": "mount", "view": "os.system"})

        response = await communicator.receive_json_from(timeout=2)
        assert response.get("type") == "error"
        # Should be rejected before type checking (import will fail for function)
        assert "error" in response

        await communicator.disconnect()

    @override_settings(LIVEVIEW_ALLOWED_MODULES=[])
    async def test_arbitrary_class_rejected(self):
        """Arbitrary classes (non-LiveView) are rejected."""
        communicator = WebsocketCommunicator(LiveViewConsumer.as_asgi(), "/ws/")
        connected, _ = await communicator.connect()
        assert connected

        # Receive connect message
        await communicator.receive_json_from(timeout=2)

        # Try to mount an arbitrary standard library class
        await communicator.send_json_to({"type": "mount", "view": "pathlib.Path"})

        response = await communicator.receive_json_from(timeout=2)
        assert response.get("type") == "error"
        # _safe_error() sanitizes to "Invalid view class"
        assert "invalid" in response.get("error", "").lower()

        await communicator.disconnect()

    @override_settings(LIVEVIEW_ALLOWED_MODULES=["tests.test_security_mount_validation"])
    async def test_whitelist_still_enforced_with_type_check(self):
        """Module whitelist is enforced even with type checking."""
        communicator = WebsocketCommunicator(LiveViewConsumer.as_asgi(), "/ws/")
        connected, _ = await communicator.connect()
        assert connected

        # Receive connect message
        await communicator.receive_json_from(timeout=2)

        # Try to mount a valid LiveView from a non-whitelisted module
        # (This would pass type check but fail whitelist check)
        await communicator.send_json_to({"type": "mount", "view": "other_app.views.OtherView"})

        response = await communicator.receive_json_from(timeout=2)
        assert response.get("type") == "error"
        # Should fail at whitelist check before even importing
        assert "error" in response

        await communicator.disconnect()
