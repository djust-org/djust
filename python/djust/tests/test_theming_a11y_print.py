"""Tests for Phase 7.4 + 7.5 + 7.6: Reduced Motion, High Contrast, Print Stylesheet."""

import os

import pytest

from djust.theming.design_tokens import (
    generate_design_tokens_classes_css,
    generate_design_tokens_css,
    get_high_contrast_css,
    get_reduced_motion_css,
)

pytestmark = pytest.mark.theming


# ---------------------------------------------------------------------------
# 7.4 Reduced Motion
# ---------------------------------------------------------------------------


class TestReducedMotionCSS:
    """@media (prefers-reduced-motion: reduce) block generation."""

    def test_reduced_motion_css_contains_media_query(self):
        css = get_reduced_motion_css()
        assert "@media (prefers-reduced-motion: reduce)" in css

    def test_reduced_motion_css_overrides_duration_tokens(self):
        css = get_reduced_motion_css()
        assert "--duration-fast: 0ms" in css
        assert "--duration-normal: 0ms" in css
        assert "--duration-slow: 0ms" in css
        assert "--duration-slower: 0ms" in css

    def test_reduced_motion_css_kills_animations(self):
        css = get_reduced_motion_css()
        assert "animation-duration: 0.01ms !important" in css
        assert "transition-duration: 0.01ms !important" in css
        assert "animation-iteration-count: 1 !important" in css
        assert "scroll-behavior: auto !important" in css

    def test_reduced_motion_in_generated_css(self):
        css = generate_design_tokens_css()
        assert "prefers-reduced-motion: reduce" in css

    def test_reduced_motion_in_classes_css(self):
        css = generate_design_tokens_classes_css()
        assert "prefers-reduced-motion: reduce" in css


# ---------------------------------------------------------------------------
# 7.5 High Contrast Media Query
# ---------------------------------------------------------------------------


class TestHighContrastCSS:
    """@media (prefers-contrast: more) block generation."""

    def test_high_contrast_css_contains_media_query(self):
        css = get_high_contrast_css()
        assert "@media (prefers-contrast: more)" in css

    def test_high_contrast_css_border_width(self):
        css = get_high_contrast_css()
        assert "--border-width: 2px" in css

    def test_high_contrast_css_ring_width(self):
        css = get_high_contrast_css()
        assert "--ring-width: 3px" in css
        assert "--ring-offset: 3px" in css

    def test_high_contrast_css_focus_visible(self):
        css = get_high_contrast_css()
        assert "focus-visible" in css
        assert "outline" in css

    def test_high_contrast_css_forced_borders(self):
        css = get_high_contrast_css()
        # Should ensure borders are solid and visible
        assert "border-style: solid" in css

    def test_high_contrast_in_generated_css(self):
        css = generate_design_tokens_css()
        assert "prefers-contrast: more" in css

    def test_high_contrast_in_classes_css(self):
        css = generate_design_tokens_classes_css()
        assert "prefers-contrast: more" in css


# ---------------------------------------------------------------------------
# 7.6 Print Stylesheet
# ---------------------------------------------------------------------------


PRINT_CSS_PATH = os.path.join(
    os.path.dirname(os.path.dirname(__file__)),
    "theming",
    "static",
    "djust_theming",
    "css",
    "print.css",
)


@pytest.fixture
def print_css():
    """Read the print.css file contents."""
    with open(PRINT_CSS_PATH) as f:
        return f.read()


class TestPrintCSS:
    """Print stylesheet static file."""

    def test_print_css_file_exists(self):
        assert os.path.isfile(PRINT_CSS_PATH), f"print.css not found at {PRINT_CSS_PATH}"

    def test_print_css_has_media_print(self, print_css):
        assert "@media print" in print_css

    def test_print_css_hides_sidebar(self, print_css):
        assert ".sidebar" in print_css
        assert "display: none" in print_css

    def test_print_css_hides_navbar(self, print_css):
        assert ".navbar" in print_css

    def test_print_css_hides_interactive(self, print_css):
        assert ".theme-switcher" in print_css
        assert ".theme-mode-btn" in print_css
        assert ".toast" in print_css

    def test_print_css_forces_colors(self, print_css):
        # Should force white background and black text
        assert "background" in print_css
        assert "color" in print_css
        # Verify white/black forcing (can be #fff, white, etc.)
        assert "#fff" in print_css or "white" in print_css
        assert "#000" in print_css or "black" in print_css

    def test_print_css_removes_shadows(self, print_css):
        assert "box-shadow: none" in print_css

    def test_print_css_shows_urls(self, print_css):
        assert "a[href]::after" in print_css or "a[href]::after" in print_css
        assert "attr(href)" in print_css

    def test_print_css_page_breaks(self, print_css):
        assert "page-break-after: avoid" in print_css or "break-after: avoid" in print_css
        assert "page-break-inside: avoid" in print_css or "break-inside: avoid" in print_css


# ---------------------------------------------------------------------------
# 7.6 Print stylesheet included in theme_head
# ---------------------------------------------------------------------------


class TestThemeHeadPrintLink:
    """Verify theme_head.html includes the print stylesheet link."""

    def test_theme_head_includes_print_link(self):
        from django.template.loader import render_to_string

        html = render_to_string(
            "djust_theming/theme_head.html",
            {
                "loading_class": True,
                "css_block": "<style>:root{}</style>",
                "deferred_css_block": "",
                "component_css_block": "",
                "include_component_link": False,
                "include_js": False,
                "direction": "ltr",
            },
        )
        assert 'media="print"' in html
        assert "print.css" in html
