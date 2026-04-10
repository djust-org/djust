"""
Tests for VDOM patch timing/performance metadata gating (#654).

``LiveViewConsumer._send_update()`` previously attached ``timing`` and
``performance`` fields to every patch response regardless of ``DEBUG``.
This test file verifies the fix: both fields are only attached when
``settings.DEBUG`` or ``settings.DJUST_EXPOSE_TIMING`` is True.

Paired with #653 (WebSocket CSWSH, shipped), this gating closes a
side-channel reconnaissance primitive — cross-origin attackers could
otherwise observe handler/phase timings and infer server state.
"""

import pytest
from django.test import override_settings

from djust.websocket import LiveViewConsumer, _should_expose_timing


# ---------------------------------------------------------------------------
# Unit tests for the helper
# ---------------------------------------------------------------------------


class TestShouldExposeTimingUnit:
    """Unit tests for ``djust.websocket._should_expose_timing``."""

    @override_settings(DEBUG=True)
    def test_debug_true_exposes(self):
        assert _should_expose_timing() is True

    @override_settings(DEBUG=False, DJUST_EXPOSE_TIMING=True)
    def test_expose_flag_exposes(self):
        assert _should_expose_timing() is True

    @override_settings(DEBUG=True, DJUST_EXPOSE_TIMING=True)
    def test_both_true_exposes(self):
        assert _should_expose_timing() is True

    @override_settings(DEBUG=False, DJUST_EXPOSE_TIMING=False)
    def test_both_false_hides(self):
        assert _should_expose_timing() is False

    @override_settings(DEBUG=False)
    def test_expose_flag_absent_defaults_false(self):
        """Missing DJUST_EXPOSE_TIMING defaults to False, so production hides."""
        # DJUST_EXPOSE_TIMING not set at all
        from django.conf import settings

        # Just make sure we didn't stick a default on the settings module.
        assert not hasattr(settings, "DJUST_EXPOSE_TIMING") or settings.DJUST_EXPOSE_TIMING is False
        assert _should_expose_timing() is False


# ---------------------------------------------------------------------------
# Integration tests — build an actual _send_update response dict and capture
# what would be emitted via send_json.
# ---------------------------------------------------------------------------


class _CapturingConsumer(LiveViewConsumer):
    """LiveViewConsumer subclass that captures sent JSON instead of emitting.

    We bypass the full Channels handshake and exercise ``_send_update`` on a
    bare instance, overriding the async send sinks and side-channel flushes
    so the call returns a captured response dict we can assert against.
    """

    def __init__(self):
        # Don't call super().__init__() — it expects an ASGI scope.
        self.scope = {}
        self.channel_name = "test-channel"
        self.use_binary = False
        self.view_instance = None
        self._sent: list = []

    async def send_json(self, content):  # type: ignore[override]
        self._sent.append(content)

    async def send(self, *args, **kwargs):  # pragma: no cover
        self._sent.append({"_binary": True, "args": args, "kwargs": kwargs})

    # Stub out side-channel flushes so _send_update doesn't try to hit
    # unconfigured mixins.
    async def _flush_push_events(self):
        return None

    async def _flush_flash(self):
        return None

    async def _flush_page_metadata(self):
        return None

    async def _flush_navigation(self):
        return None

    async def _flush_accessibility(self):
        return None

    async def _flush_i18n(self):
        return None

    def _attach_debug_payload(self, response, event_name=None, performance=None):
        """No-op — we're testing the TOP-LEVEL response fields, not _debug."""
        return None


@pytest.mark.asyncio
class TestPatchResponseTimingGating:
    """End-to-end gating tests against _send_update's response dict."""

    async def _send(self) -> dict:
        """Run _send_update with a synthetic patch + timing + performance."""
        c = _CapturingConsumer()
        await c._send_update(
            html=None,
            patches=[{"op": "setText", "path": [0, 1], "text": "hi"}],
            version=2,
            timing={"handler": 0.447, "render": 3.523, "total": 4.575},
            performance={
                "timing": {
                    "name": "Event Processing",
                    "duration_ms": 4.354,
                    "metadata": {},
                    "warnings": [],
                    "children": [],
                }
            },
        )
        assert len(c._sent) == 1, "expected exactly one send_json call"
        return c._sent[0]

    @override_settings(DEBUG=False, DJUST_EXPOSE_TIMING=False)
    async def test_production_omits_top_level_timing(self):
        """DEBUG=False, no opt-in → top-level response has no timing keys."""
        response = await self._send()
        assert (
            "timing" not in response
        ), "timing must not be in production patch responses (CWE-203, #654)"
        assert (
            "performance" not in response
        ), "performance must not be in production patch responses (CWE-215, #654)"
        # Sanity: the patches themselves still flowed.
        assert response["type"] == "patch"
        assert response["patches"]

    @override_settings(DEBUG=True)
    async def test_debug_mode_includes_timing(self):
        """DEBUG=True → top-level timing/performance visible for dev tooling."""
        response = await self._send()
        assert "timing" in response
        assert response["timing"]["handler"] == 0.447
        assert "performance" in response
        assert response["performance"]["timing"]["name"] == "Event Processing"

    @override_settings(DEBUG=False, DJUST_EXPOSE_TIMING=True)
    async def test_expose_flag_includes_timing_in_production(self):
        """DJUST_EXPOSE_TIMING=True → opt-in exposes timing without DEBUG."""
        response = await self._send()
        assert "timing" in response
        assert "performance" in response

    @override_settings(DEBUG=False, DJUST_EXPOSE_TIMING=False)
    async def test_production_empty_timing_args_still_omitted(self):
        """Even if timing/performance are empty dicts, keys stay absent."""
        c = _CapturingConsumer()
        await c._send_update(
            html=None,
            patches=[],
            version=1,
            timing={},
            performance={},
        )
        response = c._sent[0]
        assert "timing" not in response
        assert "performance" not in response
