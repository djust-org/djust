"""Tests for LayoutMixin + consumer-side layout-frame emission (v0.6.0)."""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from djust.mixins.layout import LayoutMixin


# ---------------------------------------------------------------------------
# LayoutMixin unit tests
# ---------------------------------------------------------------------------


class _View(LayoutMixin):
    """Concrete mixin host."""


class TestLayoutMixin:
    def test_init_clears_pending_layout(self):
        v = _View()
        assert v._pending_layout is None

    def test_set_layout_stores_path(self):
        v = _View()
        v.set_layout("layouts/fullscreen.html")
        assert v._pending_layout == "layouts/fullscreen.html"

    def test_drain_returns_and_resets(self):
        v = _View()
        v.set_layout("layouts/app.html")
        assert v._drain_pending_layout() == "layouts/app.html"
        assert v._pending_layout is None

    def test_drain_on_unset_returns_none(self):
        v = _View()
        assert v._drain_pending_layout() is None

    def test_set_layout_last_write_wins(self):
        """Repeated calls in the same handler overwrite — only the last
        value is flushed because the client only applies the final
        layout anyway."""
        v = _View()
        v.set_layout("a.html")
        v.set_layout("b.html")
        v.set_layout("c.html")
        assert v._drain_pending_layout() == "c.html"

    def test_drain_after_drain_returns_none(self):
        v = _View()
        v.set_layout("x.html")
        v._drain_pending_layout()
        assert v._drain_pending_layout() is None


# ---------------------------------------------------------------------------
# Consumer _flush_pending_layout
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_flush_pending_layout_emits_frame_when_pending(monkeypatch):
    """When a view has a pending layout path, the consumer renders the
    template and sends a ``{"type": "layout", "path": ..., "html": ...}``
    frame.
    """
    from djust.websocket import LiveViewConsumer

    consumer = LiveViewConsumer.__new__(LiveViewConsumer)
    view = _View()
    view.set_layout("layouts/fullscreen.html")
    consumer.view_instance = view
    consumer.send_json = AsyncMock()

    def _fake_render_to_string(path, context):
        assert path == "layouts/fullscreen.html"
        return "<html><body>FULLSCREEN</body></html>"

    monkeypatch.setattr("django.template.loader.render_to_string", _fake_render_to_string)
    await consumer._flush_pending_layout()

    consumer.send_json.assert_awaited_once()
    sent = consumer.send_json.await_args.args[0]
    assert sent["type"] == "layout"
    assert sent["path"] == "layouts/fullscreen.html"
    assert "FULLSCREEN" in sent["html"]


@pytest.mark.asyncio
async def test_flush_pending_layout_noop_when_empty():
    from djust.websocket import LiveViewConsumer

    consumer = LiveViewConsumer.__new__(LiveViewConsumer)
    consumer.view_instance = _View()
    consumer.send_json = AsyncMock()

    await consumer._flush_pending_layout()
    consumer.send_json.assert_not_awaited()


@pytest.mark.asyncio
async def test_flush_pending_layout_template_missing_logs_and_skips(monkeypatch, caplog):
    """A missing template is logged as a warning and the swap is skipped
    — the WS stays alive; no send_json call."""
    import logging

    from django.template.exceptions import TemplateDoesNotExist

    from djust.websocket import LiveViewConsumer

    consumer = LiveViewConsumer.__new__(LiveViewConsumer)
    view = _View()
    view.set_layout("layouts/missing.html")
    consumer.view_instance = view
    consumer.send_json = AsyncMock()

    def _raise(_path, _context):
        raise TemplateDoesNotExist("layouts/missing.html")

    monkeypatch.setattr("django.template.loader.render_to_string", _raise)

    with caplog.at_level(logging.WARNING, logger="djust.websocket"):
        await consumer._flush_pending_layout()

    consumer.send_json.assert_not_awaited()
    assert any("template not found" in r.message for r in caplog.records)


@pytest.mark.asyncio
async def test_flush_pending_layout_view_without_mixin_is_safe():
    """A view that doesn't inherit LayoutMixin must not break the flush —
    the consumer simply no-ops."""
    from djust.websocket import LiveViewConsumer

    class _PlainView:
        pass

    consumer = LiveViewConsumer.__new__(LiveViewConsumer)
    consumer.view_instance = _PlainView()
    consumer.send_json = AsyncMock()

    await consumer._flush_pending_layout()
    consumer.send_json.assert_not_awaited()


@pytest.mark.asyncio
async def test_flush_pending_layout_view_none_is_safe():
    from djust.websocket import LiveViewConsumer

    consumer = LiveViewConsumer.__new__(LiveViewConsumer)
    consumer.view_instance = None
    consumer.send_json = AsyncMock()

    await consumer._flush_pending_layout()
    consumer.send_json.assert_not_awaited()


@pytest.mark.asyncio
async def test_flush_pending_layout_get_context_raises_prod_swallowed(monkeypatch, caplog):
    """When get_context_data() raises in production (DEBUG=False), the
    exception is logged and the swap is skipped — the WS stays alive."""
    import logging

    from django.test import override_settings

    from djust.websocket import LiveViewConsumer

    class _BrokenContext(LayoutMixin):
        def get_context_data(self, **kwargs):
            raise AttributeError("missing_key")

    consumer = LiveViewConsumer.__new__(LiveViewConsumer)
    view = _BrokenContext()
    view.set_layout("layouts/x.html")
    consumer.view_instance = view
    consumer.send_json = AsyncMock()

    with override_settings(DEBUG=False):
        with caplog.at_level(logging.ERROR, logger="djust.websocket"):
            await consumer._flush_pending_layout()

    consumer.send_json.assert_not_awaited()
    assert any("template rendering raised" in r.message for r in caplog.records)


@pytest.mark.asyncio
async def test_flush_pending_layout_get_context_raises_debug_reraises(monkeypatch):
    """In DEBUG, the exception re-raises so programmer errors surface."""
    from django.test import override_settings

    from djust.websocket import LiveViewConsumer

    class _BrokenContext(LayoutMixin):
        def get_context_data(self, **kwargs):
            raise AttributeError("missing_key")

    consumer = LiveViewConsumer.__new__(LiveViewConsumer)
    view = _BrokenContext()
    view.set_layout("layouts/x.html")
    consumer.view_instance = view
    consumer.send_json = AsyncMock()

    with override_settings(DEBUG=True):
        with pytest.raises(AttributeError, match="missing_key"):
            await consumer._flush_pending_layout()


# ---------------------------------------------------------------------------
# LiveView composition — the mixin is reachable from a real LiveView instance
# ---------------------------------------------------------------------------


def test_liveview_exposes_set_layout():
    """Regression test: LayoutMixin is composed into the LiveView base
    class so every subclass automatically gains ``set_layout``."""
    from djust import LiveView

    assert hasattr(LiveView, "set_layout")
    assert hasattr(LiveView, "_drain_pending_layout")
