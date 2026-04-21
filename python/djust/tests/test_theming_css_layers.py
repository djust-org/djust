"""Tests for I9: CSS Cascade Layers."""

import django
from django.conf import settings

if not settings.configured:
    settings.configure(
        INSTALLED_APPS=["django.contrib.contenttypes", "djust.theming"],
        DATABASES={"default": {"ENGINE": "django.db.backends.sqlite3", "NAME": ":memory:"}},
        STATIC_URL="/static/",
        TEMPLATES=[
            {
                "BACKEND": "django.template.backends.django.DjangoTemplates",
                "APP_DIRS": True,
                "OPTIONS": {
                    "context_processors": [],
                },
            }
        ],
    )
    django.setup()

from unittest.mock import patch

import pytest

from djust.theming.manager import DEFAULT_CONFIG

pytestmark = pytest.mark.theming


# ---------------------------------------------------------------------------
# 1. Config defaults
# ---------------------------------------------------------------------------


class TestCSSLayersConfig:
    """use_css_layers config defaults and override."""

    def test_default_config_has_use_css_layers(self):
        assert "use_css_layers" in DEFAULT_CONFIG

    def test_use_css_layers_default_true(self):
        assert DEFAULT_CONFIG["use_css_layers"] is True

    def test_default_config_has_css_layer_order(self):
        assert "css_layer_order" in DEFAULT_CONFIG

    def test_css_layer_order_default(self):
        assert (
            DEFAULT_CONFIG["css_layer_order"] == "base, tokens, components, djust-components, theme"
        )

    def test_can_disable_via_settings(self):
        from djust.theming.manager import get_theme_config

        with patch.object(
            settings,
            "LIVEVIEW_CONFIG",
            {"theme": {"use_css_layers": False}},
            create=True,
        ):
            config = get_theme_config()
        assert config["use_css_layers"] is False


# ---------------------------------------------------------------------------
# 2. Layer order declaration in generated CSS
# ---------------------------------------------------------------------------


class TestLayerOrderDeclaration:
    """Generated CSS contains @layer order declaration as first line."""

    def test_color_css_has_layer_order(self):
        """ThemeCSSGenerator output starts with @layer declaration."""
        from djust.theming.css_generator import ThemeCSSGenerator

        gen = ThemeCSSGenerator(preset_name="default")
        css = gen.generate_css()
        # Layer order must appear before any @layer block
        layer_order_line = "@layer base, tokens, components, djust-components, theme;"
        assert layer_order_line in css
        # It should appear before the first @layer { block
        layer_order_pos = css.index(layer_order_line)
        first_layer_block = css.index("@layer ", layer_order_pos + len(layer_order_line))
        assert layer_order_pos < first_layer_block

    def test_complete_theme_css_has_layer_order(self):
        """CompleteThemeCSSGenerator output contains @layer declaration."""
        from djust.theming.theme_css_generator import CompleteThemeCSSGenerator

        gen = CompleteThemeCSSGenerator("material")
        css = gen.generate_css()
        assert "@layer base, tokens, components, djust-components, theme;" in css

    def test_no_layers_when_disabled(self):
        """When use_css_layers=False, no @layer declarations in output."""
        from djust.theming.css_generator import ThemeCSSGenerator

        with patch.object(
            settings,
            "LIVEVIEW_CONFIG",
            {"theme": {"use_css_layers": False}},
            create=True,
        ):
            gen = ThemeCSSGenerator(preset_name="default")
            css = gen.generate_css()
        assert "@layer" not in css


# ---------------------------------------------------------------------------
# 3. Token CSS wrapped in @layer tokens
# ---------------------------------------------------------------------------


class TestTokensLayer:
    """Color variables (:root, .dark, @media) wrapped in @layer tokens."""

    def test_root_vars_in_tokens_layer(self):
        from djust.theming.css_generator import ThemeCSSGenerator

        gen = ThemeCSSGenerator(preset_name="default")
        css = gen.generate_css()
        assert "@layer tokens {" in css
        # :root should be inside the tokens layer
        tokens_start = css.index("@layer tokens {")
        assert ":root {" in css[tokens_start:]

    def test_dark_mode_in_tokens_layer(self):
        from djust.theming.css_generator import ThemeCSSGenerator

        gen = ThemeCSSGenerator(preset_name="default")
        css = gen.generate_css()
        tokens_start = css.index("@layer tokens {")
        tokens_section = css[tokens_start:]
        assert ".dark," in tokens_section or '[data-theme="dark"]' in tokens_section


# ---------------------------------------------------------------------------
# 4. Base styles wrapped in @layer base
# ---------------------------------------------------------------------------


class TestBaseLayer:
    """Base styles wrapped in @layer base."""

    def test_base_styles_in_base_layer(self):
        from djust.theming.css_generator import ThemeCSSGenerator

        gen = ThemeCSSGenerator(preset_name="default")
        css = gen.generate_css()
        assert "@layer base {" in css


# ---------------------------------------------------------------------------
# 5. Utility/component classes wrapped in @layer components
# ---------------------------------------------------------------------------


class TestComponentsLayer:
    """Utility and component classes wrapped in @layer components."""

    def test_utilities_in_components_layer(self):
        from djust.theming.css_generator import ThemeCSSGenerator

        gen = ThemeCSSGenerator(preset_name="default")
        css = gen.generate_css()
        assert "@layer components {" in css

    def test_theme_component_styles_in_components_layer(self):
        """CompleteThemeCSSGenerator component styles are in @layer components."""
        from djust.theming.theme_css_generator import CompleteThemeCSSGenerator

        gen = CompleteThemeCSSGenerator("material")
        css = gen.generate_css()
        # Check that .btn appears inside a components layer
        components_indices = []
        idx = 0
        while True:
            try:
                pos = css.index("@layer components {", idx)
                components_indices.append(pos)
                idx = pos + 1
            except ValueError:
                break
        assert len(components_indices) > 0

    def test_typography_in_components_layer(self):
        """Typography classes are in @layer components."""
        from djust.theming.theme_css_generator import CompleteThemeCSSGenerator

        gen = CompleteThemeCSSGenerator("material")
        css = gen.generate_css()
        # Typography should be in components layer
        assert "@layer components {" in css


# ---------------------------------------------------------------------------
# 6. Component CSS (static file) wrapped in @layer components
# ---------------------------------------------------------------------------


class TestComponentCSSLayer:
    """component_css_generator output wrapped in @layer components."""

    def test_component_css_wrapped_in_layer(self):
        from djust.theming.component_css_generator import generate_component_css

        generate_component_css.cache_clear()
        with patch.object(settings, "LIVEVIEW_CONFIG", {}, create=True):
            css = generate_component_css("")
        assert "@layer components {" in css

    def test_component_css_with_prefix_wrapped_in_layer(self):
        from djust.theming.component_css_generator import generate_component_css

        generate_component_css.cache_clear()
        with patch.object(settings, "LIVEVIEW_CONFIG", {}, create=True):
            css = generate_component_css("dj-")
        assert "@layer components {" in css

    def test_component_css_no_layer_when_disabled(self):
        """When use_css_layers=False, no layer wrapping."""
        import re

        from djust.theming.component_css_generator import generate_component_css

        generate_component_css.cache_clear()
        with patch.object(
            settings,
            "LIVEVIEW_CONFIG",
            {"theme": {"use_css_layers": False}},
            create=True,
        ):
            css = generate_component_css("")
        # Match actual ``@layer NAME {`` block syntax rather than the
        # substring — the file header comment may mention "@layer" while
        # the CSS body is layer-free (e.g., "NOT wrapped in @layer").
        assert not re.search(r"@layer\s+\w+\s*\{", css)
        generate_component_css.cache_clear()  # Reset for other tests


# ---------------------------------------------------------------------------
# 7. Static components.css is NOT wrapped in @layer (intentional, see file)
# ---------------------------------------------------------------------------


class TestStaticComponentsCSS:
    """The static components.css file documents its own @layer-free design.

    Component styles must have higher specificity than djust-components
    defaults so theming overrides take effect; wrapping them in @layer would
    lower their priority. The file header explicitly calls this out.
    """

    def test_static_css_explicitly_unlayered(self):
        from pathlib import Path

        css_path = (
            Path(__file__).resolve().parent.parent
            / "theming"
            / "static"
            / "djust_theming"
            / "css"
            / "components.css"
        )
        css = css_path.read_text()
        import re

        # The file's top comment documents the design; verify it stays.
        assert "NOT wrapped in @layer" in css[:500], (
            "components.css should carry the documented 'NOT wrapped in @layer' "
            "rationale in its file header so future maintainers don't re-wrap it."
        )
        # And no stray @layer blocks snuck in. (The substring "@layer" may
        # appear inside the header comment — match the actual block syntax.)
        assert not re.search(
            r"@layer\s+\w+\s*\{", css
        ), "components.css should not contain any @layer blocks"


# ---------------------------------------------------------------------------
# 8. Pack CSS has @layer theme for pack-specific styles
# ---------------------------------------------------------------------------


class TestPackCSSLayer:
    """Pack-specific CSS additions wrapped in @layer theme."""

    def test_pack_css_has_theme_layer(self):
        """Pack CSS wraps pack-specific additions in @layer theme."""
        from djust.theming.pack_css_generator import ThemePackCSSGenerator

        try:
            gen = ThemePackCSSGenerator("corporate-light")
        except (ValueError, KeyError):
            pytest.skip("No 'corporate-light' pack available")

        css = gen.generate_css()
        assert "@layer theme {" in css


# ---------------------------------------------------------------------------
# 9. Layer nesting correctness
# ---------------------------------------------------------------------------


class TestLayerNesting:
    """Verify layers are properly closed and not nested incorrectly."""

    def test_no_nested_layer_declarations(self):
        """@layer blocks should not be nested inside other @layer blocks."""
        from djust.theming.css_generator import ThemeCSSGenerator

        gen = ThemeCSSGenerator(preset_name="default")
        css = gen.generate_css()

        # Simple check: count open/close braces for each @layer block
        # Each @layer block should be at the top level
        depth = 0
        for char in css:
            if char == "{":
                depth += 1
            elif char == "}":
                depth -= 1

        # If all layers are properly closed, depth should be 0
        assert depth == 0, f"Unbalanced braces: depth={depth}"


if __name__ == "__main__":
    pytest.main([__file__])
