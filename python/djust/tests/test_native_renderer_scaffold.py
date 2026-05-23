"""LVN-II PR-2 gate test: NativeRenderer scaffold + registry entries.

The scaffold raises ``NotImplementedError`` from ``render_with_diff``;
LVN-II PR-3 will implement the widget-tree walker. This test pins the
scaffold's contract: registry entries exist, classes conform to
``Renderer``, and the error message points at the tracking issue so a
client developer who hits it knows what's going on.
"""

from __future__ import annotations

from unittest.mock import MagicMock


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


class TestNativeRendererResolverWiring:
    """LVN-II PR-4: NativeRenderer wires the template variant resolver."""

    def test_resolve_template_uses_per_renderer_output_format(self):
        from unittest.mock import MagicMock
        from djust.renderers import SwiftUIRenderer

        r = SwiftUIRenderer(view=MagicMock())
        # Unknown template → variant doesn't exist → falls back to base.
        assert r.resolve_template("definitely-not-real.html") == "definitely-not-real.html"

    def test_error_message_includes_resolved_template_name(self):
        # POC superseded: NativeRenderer no longer raises NotImplementedError;
        # it actually renders. See test_native_renderer_poc.py for the
        # current render behavior; resolve_template is still tested above.
        from unittest.mock import MagicMock
        from djust.renderers import SwiftUIRenderer

        view = MagicMock()
        view.template_name = "medicare/home.html"
        r = SwiftUIRenderer(view=view)
        # Test now only confirms resolve_template's fallback shape; the
        # render path itself is exercised in test_native_renderer_poc.py.
        assert r.resolve_template("medicare/home.html") == "medicare/home.html"
