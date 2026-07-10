"""LVN-I PR-1 gate test: Renderer Protocol + HtmlRenderer shape.

Tests for ADR-019 Iteration I, PR-1 — the renderer abstraction
foundation. Asserts the Protocol exists with the documented shape, that
``HtmlRenderer`` conforms to it, and that ``TemplateMixin.render_with_diff``
dispatches through the renderer instead of calling ``_rust_view``
directly.

The test that gates the entire PR is
:meth:`TestRendererDispatch.test_mixin_dispatches_through_html_renderer`
— it goes RED until the ``mixins/template.py`` edit lands.

See:
- ``docs/adr/019-liveview-native.md`` §"Three layers" §1
- ``python/djust/renderers/`` (this PR introduces the package)
- ``python/djust/tests/test_runtime.py`` for the analogous test shape
  used for ``ViewRuntime`` / ``Transport`` Protocol verification
"""

from __future__ import annotations

import django
from django.conf import settings

if not settings.configured:
    settings.configure(
        DATABASES={
            "default": {
                "ENGINE": "django.db.backends.sqlite3",
                "NAME": ":memory:",
            }
        },
        INSTALLED_APPS=[
            "django.contrib.contenttypes",
            "django.contrib.auth",
            "django.contrib.sessions",
        ],
        SECRET_KEY="test-secret-key-renderer-protocol",
        SESSION_ENGINE="django.contrib.sessions.backends.db",
        USE_TZ=True,
        DEFAULT_AUTO_FIELD="django.db.models.BigAutoField",
    )
    django.setup()

from unittest.mock import MagicMock, patch


class TestRendererPackageImports:
    """The Protocol and default implementation must be importable from
    the top-level ``djust.renderers`` package per ADR-019 layout.
    """

    def test_import_renderer_from_package(self):
        from djust.renderers import Renderer  # noqa: F401

    def test_import_html_renderer_from_package(self):
        from djust.renderers import HtmlRenderer  # noqa: F401

    def test_package_exports_match_all(self):
        import djust.renderers as renderers

        assert "Renderer" in renderers.__all__
        assert "HtmlRenderer" in renderers.__all__


class TestRendererProtocolShape:
    """Renderer is a runtime-checkable Protocol with the documented
    attributes: ``output_format: str`` and ``render_with_diff(...)``.
    """

    def test_renderer_is_a_protocol(self):
        from djust.renderers import Renderer

        assert getattr(Renderer, "_is_protocol", False) is True

    def test_renderer_has_output_format_attribute(self):
        from djust.renderers import Renderer

        # ``from __future__ import annotations`` stores annotations as
        # strings; check the string form OR resolve via get_type_hints.
        assert "output_format" in Renderer.__annotations__
        assert Renderer.__annotations__["output_format"] == "str"

    def test_renderer_has_render_with_diff_method(self):
        from djust.renderers import Renderer

        assert hasattr(Renderer, "render_with_diff")
        assert callable(Renderer.render_with_diff)


class TestHtmlRendererConformance:
    def test_html_renderer_declares_output_format(self):
        from djust.renderers import HtmlRenderer

        assert HtmlRenderer.output_format == "html"

    def test_html_renderer_instance_conforms_to_protocol(self):
        from djust.renderers import HtmlRenderer, Renderer

        renderer = HtmlRenderer(view=MagicMock())
        assert isinstance(renderer, Renderer)

    def test_html_renderer_binds_view_on_init(self):
        from djust.renderers import HtmlRenderer

        view = MagicMock()
        renderer = HtmlRenderer(view)
        assert renderer.view is view

    def test_html_renderer_delegates_to_rust_view(self):
        """``HtmlRenderer.render_with_diff`` must call through to the
        bound view's ``_rust_view.render_with_diff()`` and return its
        result verbatim — this is the wire-format invariant that keeps
        the browser client byte-identical.
        """
        from djust.renderers import HtmlRenderer

        view = MagicMock()
        view._rust_view.render_with_diff.return_value = (
            "<div>html</div>",
            '[{"type":"SetText","text":"x"}]',
            42,
        )

        renderer = HtmlRenderer(view)
        result = renderer.render_with_diff()

        view._rust_view.render_with_diff.assert_called_once_with()
        assert result == (
            "<div>html</div>",
            '[{"type":"SetText","text":"x"}]',
            42,
        )


class TestRendererDispatch:
    """The PR-1 gate: ``TemplateMixin.render_with_diff`` no longer calls
    ``self._rust_view.render_with_diff()`` directly; it constructs an
    ``HtmlRenderer`` and dispatches through it.

    This is the test that goes RED until the ``mixins/template.py`` edit
    lands and GREEN immediately after — the structural seam the rest of
    LVN-I builds on.
    """

    def test_mixin_dispatches_through_html_renderer(self):
        from djust.mixins.template import TemplateMixin

        # Plain MagicMock (no spec) — render_with_diff calls many helpers
        # from sibling mixins (_initialize_rust_view from rust_bridge,
        # _sync_state_to_rust, etc.) that aren't on TemplateMixin's own
        # surface. MagicMock without spec auto-stubs all of them.
        view = MagicMock()
        view._rust_view.render_with_diff.return_value = ("<div>x</div>", None, 1)
        view._rust_view.get_render_timing.return_value = {}
        view._sync_done_this_cycle = False
        view._force_full_html = False
        view._current_html_size = None
        # MagicMock auto-creates attrs as truthy children, so without this the
        # dispatch's ``getattr(self, "_djust_renderer", None) or HtmlRenderer(self)``
        # would pick the mock instead of HtmlRenderer. Real LiveViews have no
        # ``_djust_renderer`` (set only by ViewRuntime for native) → None → HtmlRenderer.
        view._djust_renderer = None
        view.__class__ = type("StubView", (), {})  # avoid 'template' property check

        with patch(
            "djust.renderers.html.HtmlRenderer.render_with_diff",
            return_value=("<div>x</div>", None, 1),
        ) as patched_render:
            TemplateMixin.render_with_diff(view)

        assert patched_render.called, (
            "TemplateMixin.render_with_diff did not dispatch through "
            "HtmlRenderer — the renderer-abstraction seam is missing. "
            "See ADR-019 §'Three layers' §1."
        )
