"""Tests for Phase 3.1 + 3.2 + 3.4: Layout Templates, Standard Blocks, Responsive Tokens."""

import os

import pytest

from djust.tests.theming_conftest import *  # noqa: F401, F403 — triggers Django setup

from django.template.loader import get_template
from django.test import RequestFactory

from djust.theming.design_tokens import get_layout_tokens, generate_design_tokens_root_css
from djust.theming.template_resolver import (
    _get_layout_candidates,
    resolve_layout_template,
)

pytestmark = pytest.mark.theming


# ---------------------------------------------------------------------------
# Responsive tokens
# ---------------------------------------------------------------------------


class TestLayoutTokens:
    """Tests for responsive/layout design tokens (Phase 3.4)."""

    def test_get_layout_tokens_returns_string(self):
        result = get_layout_tokens()
        assert isinstance(result, str)

    def test_breakpoint_sm(self):
        assert "--breakpoint-sm" in get_layout_tokens()

    def test_breakpoint_md(self):
        assert "--breakpoint-md" in get_layout_tokens()

    def test_breakpoint_lg(self):
        assert "--breakpoint-lg" in get_layout_tokens()

    def test_breakpoint_xl(self):
        assert "--breakpoint-xl" in get_layout_tokens()

    def test_sidebar_width(self):
        assert "--sidebar-width" in get_layout_tokens()

    def test_sidebar_collapsed_width(self):
        assert "--sidebar-collapsed-width" in get_layout_tokens()

    def test_topbar_height(self):
        assert "--topbar-height" in get_layout_tokens()

    def test_layout_tokens_included_in_root_css(self):
        root_css = generate_design_tokens_root_css()
        assert "--breakpoint-sm" in root_css
        assert "--sidebar-width" in root_css
        assert "--topbar-height" in root_css


# ---------------------------------------------------------------------------
# Template resolver — layout candidates
# ---------------------------------------------------------------------------


class TestLayoutResolver:
    """Tests for layout template resolution."""

    def test_get_layout_candidates_default(self):
        candidates = _get_layout_candidates("material", "base")
        assert candidates == [
            "djust_theming/themes/material/layouts/base.html",
            "djust_theming/layouts/base.html",
        ]

    def test_get_layout_candidates_custom_theme(self):
        candidates = _get_layout_candidates("ios", "sidebar")
        assert candidates[0] == "djust_theming/themes/ios/layouts/sidebar.html"
        assert candidates[1] == "djust_theming/layouts/sidebar.html"

    def test_resolve_layout_template_returns_default(self):
        """resolve_layout_template falls back to the default layout."""
        factory = RequestFactory()
        request = factory.get("/")
        request.session = {}
        request.COOKIES = {}
        template = resolve_layout_template(request, "base")
        assert "layouts/base.html" in template.origin.name


# ---------------------------------------------------------------------------
# Template existence — all 7 layouts loadable
# ---------------------------------------------------------------------------


LAYOUT_NAMES = ["base", "sidebar", "topbar", "sidebar_topbar", "centered", "dashboard", "split"]


class TestLayoutTemplateExistence:
    """Each layout template must be loadable by Django's template engine."""

    @pytest.mark.parametrize("name", LAYOUT_NAMES)
    def test_layout_template_exists(self, name):
        tpl = get_template(f"djust_theming/layouts/{name}.html")
        assert tpl is not None


# ---------------------------------------------------------------------------
# Template inheritance chains
# ---------------------------------------------------------------------------


class TestLayoutInheritance:
    """Verify extends relationships."""

    def _read_template_source(self, name):
        tpl = get_template(f"djust_theming/layouts/{name}.html")
        return open(tpl.origin.name).read()

    def test_sidebar_extends_base(self):
        src = self._read_template_source("sidebar")
        assert "djust_theming/layouts/base.html" in src

    def test_topbar_extends_base(self):
        src = self._read_template_source("topbar")
        assert "djust_theming/layouts/base.html" in src

    def test_sidebar_topbar_extends_base(self):
        src = self._read_template_source("sidebar_topbar")
        assert "djust_theming/layouts/base.html" in src

    def test_centered_extends_base(self):
        src = self._read_template_source("centered")
        assert "djust_theming/layouts/base.html" in src

    def test_dashboard_extends_sidebar_topbar(self):
        src = self._read_template_source("dashboard")
        assert "djust_theming/layouts/sidebar_topbar.html" in src

    def test_split_extends_base(self):
        src = self._read_template_source("split")
        assert "djust_theming/layouts/base.html" in src


# ---------------------------------------------------------------------------
# Block presence — each layout defines its documented blocks
# ---------------------------------------------------------------------------


class TestLayoutBlocks:
    """Verify each layout defines its documented blocks."""

    def _read_template_source(self, name):
        tpl = get_template(f"djust_theming/layouts/{name}.html")
        return open(tpl.origin.name).read()

    # base.html blocks
    @pytest.mark.parametrize(
        "block",
        ["page_title", "head_extra", "body_class", "content", "footer", "extra_css", "extra_js"],
    )
    def test_base_blocks(self, block):
        src = self._read_template_source("base")
        assert f"{{% block {block} %}}" in src or f"{{% block {block} %}} " in src.replace(
            "\n", " "
        )

    # sidebar.html blocks
    @pytest.mark.parametrize("block", ["sidebar", "sidebar_content"])
    def test_sidebar_blocks(self, block):
        src = self._read_template_source("sidebar")
        assert f"block {block}" in src

    # topbar.html blocks
    @pytest.mark.parametrize("block", ["topbar", "topbar_content"])
    def test_topbar_blocks(self, block):
        src = self._read_template_source("topbar")
        assert f"block {block}" in src

    # sidebar_topbar.html blocks
    @pytest.mark.parametrize("block", ["sidebar", "topbar", "sidebar_topbar_content"])
    def test_sidebar_topbar_blocks(self, block):
        src = self._read_template_source("sidebar_topbar")
        assert f"block {block}" in src

    # centered.html blocks
    def test_centered_content_block(self):
        src = self._read_template_source("centered")
        assert "block centered_content" in src

    # dashboard.html blocks
    def test_dashboard_content_block(self):
        src = self._read_template_source("dashboard")
        assert "block dashboard_content" in src

    # split.html blocks
    @pytest.mark.parametrize("block", ["panel_left", "panel_right"])
    def test_split_blocks(self, block):
        src = self._read_template_source("split")
        assert f"block {block}" in src


# ---------------------------------------------------------------------------
# CSS class application — each layout applies .layout-* body class
# ---------------------------------------------------------------------------


class TestLayoutCSSClasses:
    """Verify each layout applies its .layout-* CSS class."""

    def _read_template_source(self, name):
        tpl = get_template(f"djust_theming/layouts/{name}.html")
        return open(tpl.origin.name).read()

    def test_base_layout_class(self):
        src = self._read_template_source("base")
        assert "layout-base" in src

    def test_sidebar_layout_class(self):
        src = self._read_template_source("sidebar")
        assert "layout-sidebar" in src

    def test_topbar_layout_class(self):
        src = self._read_template_source("topbar")
        assert "layout-topbar" in src

    def test_sidebar_topbar_layout_class(self):
        src = self._read_template_source("sidebar_topbar")
        assert "layout-sidebar-topbar" in src

    def test_centered_layout_class(self):
        src = self._read_template_source("centered")
        assert "layout-centered" in src

    def test_dashboard_layout_class(self):
        src = self._read_template_source("dashboard")
        assert "layout-dashboard" in src

    def test_split_layout_class(self):
        src = self._read_template_source("split")
        assert "layout-split" in src


# ---------------------------------------------------------------------------
# Layouts CSS file
# ---------------------------------------------------------------------------


class TestLayoutsCSS:
    """Verify the layouts.css static file."""

    @staticmethod
    def _css_path():
        base = os.path.dirname(os.path.dirname(__file__))
        return os.path.join(base, "theming", "static", "djust_theming", "css", "layouts.css")

    def test_layouts_css_exists(self):
        assert os.path.isfile(self._css_path())

    def test_layouts_css_uses_layer_base(self):
        css = open(self._css_path()).read()
        assert "@layer base" in css

    @pytest.mark.parametrize(
        "selector",
        [
            ".layout-base",
            ".layout-sidebar",
            ".layout-topbar",
            ".layout-sidebar-topbar",
            ".layout-centered",
            ".layout-dashboard",
            ".layout-split",
        ],
    )
    def test_layouts_css_contains_selector(self, selector):
        css = open(self._css_path()).read()
        assert selector in css

    def test_layouts_css_responsive_media_queries(self):
        css = open(self._css_path()).read()
        assert "@media" in css


# ---------------------------------------------------------------------------
# Theme head inclusion — base.html includes theme_head
# ---------------------------------------------------------------------------


class TestBaseTemplateThemeHead:
    """Verify base.html loads theme_tags and calls theme_head."""

    def test_base_loads_theme_tags(self):
        tpl = get_template("djust_theming/layouts/base.html")
        src = open(tpl.origin.name).read()
        assert "{% load theme_tags %}" in src or "{% load static theme_tags %}" in src

    def test_base_calls_theme_head(self):
        tpl = get_template("djust_theming/layouts/base.html")
        src = open(tpl.origin.name).read()
        assert "{% theme_head %}" in src

    def test_base_includes_layouts_css(self):
        tpl = get_template("djust_theming/layouts/base.html")
        src = open(tpl.origin.name).read()
        assert "layouts.css" in src
