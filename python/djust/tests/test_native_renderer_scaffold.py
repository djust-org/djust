"""LVN-II PR-2 gate test: NativeRenderer scaffold + registry entries.

The scaffold raises ``NotImplementedError`` from ``render_with_diff``;
LVN-II PR-3 will implement the widget-tree walker. This test pins the
scaffold's contract: registry entries exist, classes conform to
``Renderer``, and the error message points at the tracking issue so a
client developer who hits it knows what's going on.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest


class TestRegistryHasNativeEntries:
    def test_swiftui_registered(self):
        from djust.renderers import RENDERERS, SwiftUIRenderer

        assert RENDERERS["swiftui"] is SwiftUIRenderer

    def test_compose_registered(self):
        from djust.renderers import ComposeRenderer, RENDERERS

        assert RENDERERS["compose"] is ComposeRenderer

    def test_get_factory_resolves_swiftui(self):
        from djust.renderers import SwiftUIRenderer, get_renderer_factory

        assert get_renderer_factory("swiftui") is SwiftUIRenderer

    def test_get_factory_resolves_compose(self):
        from djust.renderers import ComposeRenderer, get_renderer_factory

        assert get_renderer_factory("compose") is ComposeRenderer


class TestNativeRendererConformance:
    def test_swiftui_renderer_conforms_to_protocol(self):
        from djust.renderers import Renderer, SwiftUIRenderer

        assert isinstance(SwiftUIRenderer(view=MagicMock()), Renderer)

    def test_compose_renderer_conforms_to_protocol(self):
        from djust.renderers import ComposeRenderer, Renderer

        assert isinstance(ComposeRenderer(view=MagicMock()), Renderer)

    def test_output_format_is_per_platform(self):
        from djust.renderers import ComposeRenderer, NativeRenderer, SwiftUIRenderer

        assert NativeRenderer.output_format == "native"
        assert SwiftUIRenderer.output_format == "swiftui"
        assert ComposeRenderer.output_format == "compose"


class TestScaffoldRaises:
    """The scaffold raises with a clear pointer to the tracking issue."""

    def test_swiftui_render_with_diff_raises_with_clear_message(self):
        from djust.renderers import SwiftUIRenderer

        r = SwiftUIRenderer(view=MagicMock())
        with pytest.raises(NotImplementedError) as exc_info:
            r.render_with_diff()
        msg = str(exc_info.value)
        assert "scaffold" in msg
        assert "1578" in msg
        assert "swiftui" in msg

    def test_compose_render_with_diff_raises(self):
        from djust.renderers import ComposeRenderer

        r = ComposeRenderer(view=MagicMock())
        with pytest.raises(NotImplementedError):
            r.render_with_diff()
