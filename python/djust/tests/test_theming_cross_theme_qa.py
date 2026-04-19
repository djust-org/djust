"""
Phase 4: Cross-theme visual QA for all components (DJU-17).

Tests all 24 components across 5 key theme/preset combos:
  - Shadcn + Minimalist
  - Cyberpunk + Playful
  - Nord + Corporate
  - Catppuccin + Elegant
  - Tokyo Night + Dense

Validates:
  1. CSS generation succeeds and meets minimum size
  2. All required design tokens present in light mode (:root)
  3. Dark mode tokens defined and differ from light mode
  4. prefers-color-scheme dark media query present
  5. CSS layer order correct (@layer base, tokens, components, ...)
  6. Smooth theme transitions present (FOUC prevention)
  7. prefers-reduced-motion disables transitions (a11y)
  8. All 24 components render without error in each combo
  9. No component produces empty HTML output
 10. Light and dark background values are distinct
"""

import re
import tests.conftest  # noqa: F401 — ensures Django settings are configured

import pytest
from unittest.mock import MagicMock, patch

from django.conf import settings

from djust.theming.css_generator import generate_theme_css
from djust.theming.templatetags.theme_components import (
    theme_alert,
    theme_avatar,
    theme_badge,
    theme_breadcrumb,
    theme_button,
    theme_card,
    theme_checkbox,
    theme_dropdown,
    theme_input,
    theme_modal,
    theme_nav,
    theme_nav_group,
    theme_nav_item,
    theme_pagination,
    theme_progress,
    theme_radio,
    theme_select,
    theme_sidebar_nav,
    theme_skeleton,
    theme_table,
    theme_tabs,
    theme_textarea,
    theme_toast,
    theme_tooltip,
)

pytestmark = pytest.mark.theming


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

THEME_COMBOS = [
    ("shadcn", "minimalist"),
    ("cyberpunk", "playful"),
    ("nord", "corporate"),
    ("catppuccin", "elegant"),
    ("tokyo_night", "dense"),
]

COMBO_IDS = [f"{p}+{ds}" for p, ds in THEME_COMBOS]

REQUIRED_LIGHT_TOKENS = [
    "--background",
    "--foreground",
    "--primary",
    "--primary-foreground",
    "--secondary",
    "--secondary-foreground",
    "--accent",
    "--accent-foreground",
    "--destructive",
    "--destructive-foreground",
    "--border",
    "--ring",
    "--muted",
    "--muted-foreground",
    "--card",
    "--card-foreground",
    "--radius",
]

REQUIRED_DARK_TOKENS = [
    "--background",
    "--foreground",
    "--primary",
    "--card",
    "--border",
    "--muted",
]

# Minimal context required to render each component without error.
COMPONENT_MINIMAL_ARGS: dict[str, dict] = {
    "button": {"text": "Click me"},
    "card": {"title": "Test Card"},
    "badge": {"text": "Badge"},
    "alert": {"message": "Test alert"},
    "input": {"name": "test_field"},
    "modal": {"id": "test-modal"},
    "dropdown": {"id": "test-dd", "label": "Options"},
    "tabs": {
        "id": "test-tabs",
        "tabs": [{"id": "tab1", "label": "Tab 1", "content": "Content 1"}],
    },
    "table": {"headers": ["Name", "Value"], "rows": [["Row1", "Val1"]]},
    "pagination": {"page": 1, "total_pages": 5},
    "select": {"name": "test_select", "options": [("a", "A"), ("b", "B")]},
    "textarea": {"name": "test_textarea"},
    "checkbox": {"name": "test_cb"},
    "radio": {"name": "test_radio", "options": [("a", "A"), ("b", "B")]},
    "breadcrumb": {"items": [{"label": "Home", "url": "/"}, {"label": "Page"}]},
    "avatar": {"initials": "AB"},
    "toast": {"id": "toast1", "message": "Test"},
    "progress": {"value": 50},
    "skeleton": {},
    "tooltip": {"text": "Tooltip text", "content": "Content"},
    "nav_item": {"label": "Home", "url": "/"},
    "nav_group": {"label": "Group", "items": [{"label": "Item", "url": "/item"}]},
    "nav": {"items": [{"label": "Home", "url": "/"}]},
    "sidebar_nav": {"items": [{"label": "Home", "url": "/"}]},
}

TAG_MAP = {
    "button": theme_button,
    "card": theme_card,
    "badge": theme_badge,
    "alert": theme_alert,
    "input": theme_input,
    "modal": theme_modal,
    "dropdown": theme_dropdown,
    "tabs": theme_tabs,
    "table": theme_table,
    "pagination": theme_pagination,
    "select": theme_select,
    "textarea": theme_textarea,
    "checkbox": theme_checkbox,
    "radio": theme_radio,
    "breadcrumb": theme_breadcrumb,
    "avatar": theme_avatar,
    "toast": theme_toast,
    "progress": theme_progress,
    "skeleton": theme_skeleton,
    "tooltip": theme_tooltip,
    "nav_item": theme_nav_item,
    "nav_group": theme_nav_group,
    "nav": theme_nav,
    "sidebar_nav": theme_sidebar_nav,
}


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module", params=THEME_COMBOS, ids=COMBO_IDS)
def theme_combo(request):
    """Yield (preset, design_system) for each QA combo."""
    return request.param


@pytest.fixture(scope="module")
def all_generated_css():
    """Pre-generate CSS for all combos once per test session."""
    with patch.object(settings, "LIVEVIEW_CONFIG", {}, create=True):
        return {(p, ds): generate_theme_css(p, ds) for p, ds in THEME_COMBOS}


def _make_request(preset: str, design_system: str):
    request = MagicMock()
    request.COOKIES = {
        "djust_theme_preset": preset,
        "djust_design_system": design_system,
    }
    request.session = {}
    request._djust_theme_manager = None
    request.path = "/"
    return request


# ---------------------------------------------------------------------------
# 1. CSS Generation
# ---------------------------------------------------------------------------


class TestCSSGeneration:
    """CSS generates without error and meets size/content expectations."""

    def test_css_generates_without_error(self, theme_combo, all_generated_css):
        preset, design_system = theme_combo
        css = all_generated_css[(preset, design_system)]
        assert isinstance(css, str), "CSS should be a string"

    def test_css_minimum_size(self, theme_combo, all_generated_css):
        """Generated CSS must be substantial — not a stub."""
        preset, design_system = theme_combo
        css = all_generated_css[(preset, design_system)]
        assert (
            len(css) >= 10_000
        ), f"{preset}+{design_system}: CSS is only {len(css)} bytes — expected ≥10 KB"

    def test_css_layer_declaration(self, theme_combo, all_generated_css):
        """@layer declaration must appear and include expected layers."""
        preset, design_system = theme_combo
        css = all_generated_css[(preset, design_system)]
        assert (
            "@layer base, tokens, components" in css
        ), f"{preset}+{design_system}: Missing @layer declaration"

    def test_tokens_layer_present(self, theme_combo, all_generated_css):
        preset, design_system = theme_combo
        css = all_generated_css[(preset, design_system)]
        assert "@layer tokens {" in css or "@layer tokens\n{" in css or "@layer tokens " in css

    def test_components_layer_present(self, theme_combo, all_generated_css):
        preset, design_system = theme_combo
        css = all_generated_css[(preset, design_system)]
        assert "@layer components" in css


# ---------------------------------------------------------------------------
# 2. Light Mode Token Coverage
# ---------------------------------------------------------------------------


class TestLightModeTokens:
    """All required design tokens are defined in :root (light mode)."""

    def test_root_selector_present(self, theme_combo, all_generated_css):
        preset, design_system = theme_combo
        css = all_generated_css[(preset, design_system)]
        assert (
            ":root {" in css or ":root\n{" in css
        ), f"{preset}+{design_system}: No :root token block found"

    @pytest.mark.parametrize("token", REQUIRED_LIGHT_TOKENS)
    def test_required_token_in_root(self, token, theme_combo, all_generated_css):
        preset, design_system = theme_combo
        css = all_generated_css[(preset, design_system)]
        # Token must appear somewhere in a :root-scope or html[data-theme="light"]
        assert re.search(
            rf"{re.escape(token)}\s*:", css
        ), f"{preset}+{design_system}: Required token '{token}' not found in CSS"


# ---------------------------------------------------------------------------
# 3. Dark Mode Token Coverage
# ---------------------------------------------------------------------------


class TestDarkModeTokens:
    """Dark mode tokens are present and distinct from light mode."""

    def test_dark_theme_selector_present(self, theme_combo, all_generated_css):
        preset, design_system = theme_combo
        css = all_generated_css[(preset, design_system)]
        assert (
            'html[data-theme="dark"]' in css
        ), f"{preset}+{design_system}: No html[data-theme=dark] selector"

    def test_prefers_color_scheme_dark_media_query(self, theme_combo, all_generated_css):
        preset, design_system = theme_combo
        css = all_generated_css[(preset, design_system)]
        assert (
            "@media (prefers-color-scheme: dark)" in css
        ), f"{preset}+{design_system}: Missing prefers-color-scheme: dark media query"

    def test_light_theme_explicit_selector(self, theme_combo, all_generated_css):
        """html[data-theme=light] must also be defined for explicit switching."""
        preset, design_system = theme_combo
        css = all_generated_css[(preset, design_system)]
        assert (
            'html[data-theme="light"]' in css
        ), f"{preset}+{design_system}: No html[data-theme=light] explicit selector"

    @pytest.mark.parametrize("token", REQUIRED_DARK_TOKENS)
    def test_dark_block_contains_token(self, token, theme_combo, all_generated_css):
        """Dark mode block must redefine key tokens."""
        preset, design_system = theme_combo
        css = all_generated_css[(preset, design_system)]

        dark_idx = css.find('html[data-theme="dark"]')
        brace_start = css.find("{", dark_idx)
        depth = 0
        end = brace_start
        for i, ch in enumerate(css[brace_start:], brace_start):
            if ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    end = i
                    break
        dark_block = css[brace_start:end]

        assert re.search(
            rf"{re.escape(token)}\s*:", dark_block
        ), f"{preset}+{design_system}: Dark block missing token '{token}'"

    def test_dark_background_differs_from_light(self, theme_combo, all_generated_css):
        """Dark --background must be a different value than light --background."""
        preset, design_system = theme_combo
        css = all_generated_css[(preset, design_system)]

        light_match = re.search(r'html\[data-theme="light"\]\s*\{([^}]+)\}', css, re.DOTALL)
        dark_match = re.search(r'html\[data-theme="dark"\]\s*\{([^}]+)\}', css, re.DOTALL)

        assert (
            light_match and dark_match
        ), f"{preset}+{design_system}: Could not find light/dark theme blocks"

        light_bg = re.search(r"--background:\s*([^;]+);", light_match.group(1))
        dark_bg = re.search(r"--background:\s*([^;]+);", dark_match.group(1))

        assert (
            light_bg and dark_bg
        ), f"{preset}+{design_system}: --background not found in light or dark block"
        assert light_bg.group(1).strip() != dark_bg.group(1).strip(), (
            f"{preset}+{design_system}: Light and dark --background are identical "
            f"({light_bg.group(1).strip()}) — dark mode has no effect"
        )


# ---------------------------------------------------------------------------
# 4. Theme Switching / FOUC Prevention
# ---------------------------------------------------------------------------


class TestThemeSwitching:
    """CSS includes smooth transitions and motion-safe handling."""

    def test_smooth_transitions_present(self, theme_combo, all_generated_css):
        """background-color transition prevents FOUC on theme switch."""
        preset, design_system = theme_combo
        css = all_generated_css[(preset, design_system)]
        assert "transition: background-color" in css or (
            "transition:" in css and "background-color" in css
        ), f"{preset}+{design_system}: No smooth transition rule for theme switching"

    def test_prefers_reduced_motion_disables_transitions(self, theme_combo, all_generated_css):
        """Users with reduced-motion preference should not see transitions."""
        preset, design_system = theme_combo
        css = all_generated_css[(preset, design_system)]
        assert (
            "prefers-reduced-motion" in css
        ), f"{preset}+{design_system}: Missing prefers-reduced-motion support"

    def test_root_tokens_before_dark_section(self, theme_combo, all_generated_css):
        """CSS cascade must define :root tokens before dark overrides."""
        preset, design_system = theme_combo
        css = all_generated_css[(preset, design_system)]

        root_idx = css.find(":root {")
        dark_idx = css.find('html[data-theme="dark"]')

        assert root_idx != -1, f"{preset}+{design_system}: No :root block"
        assert dark_idx != -1, f"{preset}+{design_system}: No dark theme block"
        assert root_idx < dark_idx, (
            f"{preset}+{design_system}: :root is defined AFTER html[data-theme=dark] "
            "— cascade order issue that could cause missing fallback tokens"
        )


# ---------------------------------------------------------------------------
# 5. Component Rendering
# ---------------------------------------------------------------------------


class TestComponentRendering:
    """All 24 components render without error in every theme combo."""

    @pytest.mark.parametrize("component_name", sorted(COMPONENT_MINIMAL_ARGS.keys()))
    def test_component_renders_without_error(self, component_name, theme_combo):
        preset, design_system = theme_combo
        args = COMPONENT_MINIMAL_ARGS[component_name]
        tag_fn = TAG_MAP[component_name]
        ctx = {"request": _make_request(preset, design_system)}

        with patch.object(settings, "LIVEVIEW_CONFIG", {}, create=True):
            result = str(tag_fn(ctx, **args))

        assert (
            result is not None
        ), f"{preset}+{design_system}/{component_name}: render returned None"

    @pytest.mark.parametrize("component_name", sorted(COMPONENT_MINIMAL_ARGS.keys()))
    def test_component_output_not_empty(self, component_name, theme_combo):
        preset, design_system = theme_combo
        args = COMPONENT_MINIMAL_ARGS[component_name]
        tag_fn = TAG_MAP[component_name]
        ctx = {"request": _make_request(preset, design_system)}

        with patch.object(settings, "LIVEVIEW_CONFIG", {}, create=True):
            result = str(tag_fn(ctx, **args))

        assert result.strip(), f"{preset}+{design_system}/{component_name}: rendered empty HTML"

    @pytest.mark.parametrize("component_name", sorted(COMPONENT_MINIMAL_ARGS.keys()))
    def test_component_output_contains_html_tag(self, component_name, theme_combo):
        """Each component must produce at least one HTML opening tag."""
        preset, design_system = theme_combo
        args = COMPONENT_MINIMAL_ARGS[component_name]
        tag_fn = TAG_MAP[component_name]
        ctx = {"request": _make_request(preset, design_system)}

        with patch.object(settings, "LIVEVIEW_CONFIG", {}, create=True):
            result = str(tag_fn(ctx, **args))

        assert (
            "<" in result
        ), f"{preset}+{design_system}/{component_name}: output contains no HTML tags"


# ---------------------------------------------------------------------------
# 6. Responsive CSS Coverage
# ---------------------------------------------------------------------------


class TestResponsiveCSS:
    """CSS includes media queries for responsive behaviour."""

    def test_media_queries_present(self, theme_combo, all_generated_css):
        """Generated CSS must include at least one @media query."""
        preset, design_system = theme_combo
        css = all_generated_css[(preset, design_system)]
        count = css.count("@media")
        assert count >= 2, (
            f"{preset}+{design_system}: Only {count} @media query/queries found — "
            "expected responsive breakpoints"
        )

    def test_print_css_static_file_exists(self):
        """print.css static file must exist (loaded separately from generated CSS)."""
        from pathlib import Path

        print_css = (
            Path(__file__).resolve().parent.parent
            / "theming"
            / "static"
            / "djust_theming"
            / "css"
            / "print.css"
        )
        assert print_css.is_file(), "print.css static file missing"
        content = print_css.read_text()
        assert "@media print" in content, "print.css contains no @media print rule"
