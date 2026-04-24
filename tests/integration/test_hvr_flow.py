"""
Integration smoke tests for Hot View Replacement (HVR, v0.6.1).

These exercise the end-to-end pipe from a file-write on disk to the
``LiveViewConsumer.hotreload`` handler applying a class swap + emitting
``hvr-applied`` + falling back to the legacy template path for non-
LiveView files.
"""

from __future__ import annotations

import importlib
import os
import sys
import textwrap
from unittest.mock import AsyncMock, MagicMock

import pytest

from djust import LiveView


def _write_module(tmpdir: str, module_name: str, source: str) -> str:
    path = os.path.join(tmpdir, f"{module_name}.py")
    with open(path, "w") as f:
        f.write(textwrap.dedent(source))
    return os.path.abspath(path)


@pytest.mark.asyncio
async def test_hvr_module_reload_roundtrip(tmp_path):
    """Smoke test of the reload → broadcast → apply pipeline.

    Drives ``reload_module_if_liveview`` directly against a freshly
    written module and feeds the resulting ``hvr_meta`` into the
    consumer's ``hotreload`` handler. This is a **pipeline smoke test**,
    not a watchdog-level test — it does not exercise the file-watcher
    observer or the channel-layer broadcast. Those layers are covered
    in the unit tests (mocked) and by manual dev-server use.
    """
    from djust.hot_view_replacement import (
        reload_module_if_liveview,
    )
    from djust.websocket import LiveViewConsumer

    module_name = "hvr_integration_e2e"
    tmpdir = str(tmp_path)
    sys.path.insert(0, tmpdir)
    try:
        # V1: increment adds 1.
        path = _write_module(
            tmpdir,
            module_name,
            """
                from djust import LiveView
                from djust.decorators import event_handler

                class IntegrationView(LiveView):
                    template = "<div>{{ count }}</div>"

                    @event_handler
                    def increment(self, **kwargs):
                        self.count += 1
            """,
        )
        module = importlib.import_module(module_name)
        V1 = module.IntegrationView

        # Instantiate consumer and view.
        consumer = LiveViewConsumer()
        consumer.view_instance = V1()
        consumer.view_instance.count = 42

        sent: list = []
        consumer.send_json = AsyncMock(side_effect=lambda msg: sent.append(msg))
        consumer._clear_template_caches = MagicMock(return_value=0)
        consumer._send_update = AsyncMock()

        # V2: increment now adds 10.
        with open(path, "w") as f:
            f.write(
                textwrap.dedent(
                    """
                    from djust import LiveView
                    from djust.decorators import event_handler

                    class IntegrationView(LiveView):
                        template = "<div>{{ count }}</div>"

                        @event_handler
                        def increment(self, **kwargs):
                            self.count += 10
                    """
                )
            )

        result = reload_module_if_liveview(path)
        assert result is not None
        assert result.module_name == module_name

        # Simulate the broadcast-receive side: the channel-layer handler
        # fires ``hotreload`` on the consumer with the ``hvr_meta`` dict.
        event = {
            "type": "hotreload",
            "file": path,
            "hvr_meta": {
                "module": result.module_name,
                "class_names": [new.__name__ for _, new in result.class_pairs],
                "reload_id": result.reload_id,
            },
        }
        await consumer.hotreload(event)

        # Swap happened; state preserved; new increment runs new code.
        hvr_frames = [m for m in sent if m.get("type") == "hvr-applied"]
        assert len(hvr_frames) == 1
        assert consumer.view_instance.count == 42
        consumer.view_instance.increment()
        assert consumer.view_instance.count == 52, "new class body should add 10"
    finally:
        sys.modules.pop(module_name, None)
        if tmpdir in sys.path:
            sys.path.remove(tmpdir)


@pytest.mark.asyncio
async def test_hvr_non_liveview_file_triggers_template_reload():
    """Non-LiveView file (no ``hvr_meta``) runs the legacy template path."""
    from djust.websocket import LiveViewConsumer

    class TemplateView(LiveView):
        template = "<div>x</div>"

    consumer = LiveViewConsumer()
    consumer.view_instance = TemplateView()

    sent: list = []
    consumer.send_json = AsyncMock(side_effect=lambda msg: sent.append(msg))
    consumer._clear_template_caches = MagicMock(return_value=0)
    consumer._send_update = AsyncMock()

    # Event WITHOUT hvr_meta — legacy path only.
    event = {"type": "hotreload", "file": "/tmp/template.html"}
    await consumer.hotreload(event)

    # No hvr-applied frame.
    hvr_frames = [m for m in sent if m.get("type") == "hvr-applied"]
    assert hvr_frames == []
