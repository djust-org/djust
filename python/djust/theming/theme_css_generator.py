"""
CSS generator for complete themes.

Generates CSS custom properties for all aspects of a theme:
- Typography
- Spacing
- Border radius
- Shadows
- Animations
- Component styles

Sources design data from DesignSystem objects in theme_packs.py.
"""

from functools import lru_cache

from .manager import get_theme_config
from .theme_packs import DesignSystem, get_design_system
from .css_generator import ThemeCSSGenerator as ColorCSSGenerator


def _parse_size_to_px(size: str) -> float:
    """Parse a CSS size string to pixels. Assumes 1rem = 16px."""
    size = size.strip()
    if size.endswith("px"):
        return float(size[:-2])
    if size.endswith("rem"):
        return float(size[:-3]) * 16
    if size.endswith("em"):
        return float(size[:-2]) * 16
    try:
        return float(size)
    except ValueError:
        return 16.0


def _px_to_rem(px: float) -> str:
    """Convert pixels to rem string, rounded to 3 decimal places."""
    rem = px / 16
    # Clean up: 1.000rem -> 1rem, 0.875rem stays
    if rem == int(rem):
        return f"{int(rem)}rem"
    return (
        f"{rem:.3f}rem".rstrip("0").rstrip(".") + "rem"
        if "." in f"{rem:.3f}".rstrip("0")
        else f"{rem:.3f}rem"
    )


def _compute_type_scale(base_size: str, heading_scale: float) -> dict:
    """Derive text-xs through text-5xl from base_size and heading_scale.

    The heading_scale is only used for sizes *above* base. Sizes below base
    use fixed standard web ratios (xs=0.75, sm=0.875 of base) since
    aggressive downscaling makes small text unreadable.
    """
    base_px = _parse_size_to_px(base_size)

    # Small sizes: fixed ratios (standard web convention, not scale-dependent)
    sm_px = base_px * 0.875
    xs_px = base_px * 0.75

    # Heading sizes: apply heading_scale progressively upward
    lg_px = base_px * heading_scale
    xl_px = lg_px * heading_scale
    xxl_px = xl_px * heading_scale
    xxxl_px = xxl_px * heading_scale
    xxxxl_px = xxxl_px * heading_scale
    xxxxxl_px = xxxxl_px * heading_scale

    def fmt(px):
        rem = px / 16
        if rem == int(rem):
            return f"{int(rem)}rem"
        return f"{rem:.3f}rem"

    return {
        "text_xs": fmt(xs_px),
        "text_sm": fmt(sm_px),
        "text_base": fmt(base_px),
        "text_lg": fmt(lg_px),
        "text_xl": fmt(xl_px),
        "text_2xl": fmt(xxl_px),
        "text_3xl": fmt(xxxl_px),
        "text_4xl": fmt(xxxxl_px),
        "text_5xl": fmt(xxxxxl_px),
    }


def _compute_spacing_scale(space_unit: str) -> dict:
    """Generate space-0 through space-24 from space_unit.

    Uses a fixed 0.25rem base step (4px, the standard Tailwind convention)
    with the standard multiplier sequence (0,1,2,3,4,5,6,8,10,12,16,20,24).

    The space_unit from DesignSystem controls the grid unit conceptually,
    but the CSS output uses the universal 4px step to stay compatible with
    component CSS that expects --space-4 = 1rem.
    """
    base = 0.25  # 4px — standard Tailwind/shadcn base step

    multipliers = {
        "space_0": 0,
        "space_1": 1,
        "space_2": 2,
        "space_3": 3,
        "space_4": 4,
        "space_5": 5,
        "space_6": 6,
        "space_8": 8,
        "space_10": 10,
        "space_12": 12,
        "space_16": 16,
        "space_20": 20,
        "space_24": 24,
    }

    result = {"base": base}
    for name, mult in multipliers.items():
        result[name] = base * mult
    return result


def _derive_easing_variants(easing: str) -> dict:
    """Derive ease-in, ease-out, ease-in-out from a single easing value."""
    # If it's a simple keyword, map to standard curves
    keyword_map = {
        "linear": {
            "ease_in": "linear",
            "ease_out": "linear",
            "ease_in_out": "linear",
        },
        "ease": {
            "ease_in": "cubic-bezier(0.4, 0, 1, 1)",
            "ease_out": "cubic-bezier(0, 0, 0.2, 1)",
            "ease_in_out": "cubic-bezier(0.4, 0, 0.2, 1)",
        },
        "ease-out": {
            "ease_in": "cubic-bezier(0.4, 0, 1, 1)",
            "ease_out": "ease-out",
            "ease_in_out": "cubic-bezier(0.4, 0, 0.2, 1)",
        },
    }

    if easing in keyword_map:
        return keyword_map[easing]

    # For cubic-bezier values, use the provided value as ease-in-out
    # and derive reasonable in/out variants
    return {
        "ease_in": "cubic-bezier(0.4, 0, 1, 1)",
        "ease_out": "cubic-bezier(0, 0, 0.2, 1)",
        "ease_in_out": easing,
    }


def _infer_component_styles(ds: DesignSystem) -> dict:
    """Infer button/card/input styles from DesignSystem properties."""
    layout = ds.layout
    surface = ds.surface

    # Button style: sharp + thick borders -> outlined; pill -> solid; default -> solid
    if layout.button_shape == "sharp" and surface.border_width not in ("0px", "1px"):
        button_style = "outlined"
    elif ds.category == "elegant":
        button_style = "ghost"
    else:
        button_style = "solid"

    # Card style: no borders -> elevated (shadow-based); glass/gradient -> flat; default -> outlined
    if surface.border_style == "none" or surface.border_width == "0px":
        card_style = "elevated"
    elif surface.surface_treatment in ("glass", "gradient"):
        card_style = "flat"
    else:
        card_style = "outlined"

    # Input style: sharp -> outlined; elegant -> underlined; default -> outlined
    if ds.category == "elegant":
        input_style = "underlined"
    elif layout.input_shape == "pill":
        input_style = "filled"
    else:
        input_style = "outlined"

    return {
        "button_style": button_style,
        "card_style": card_style,
        "input_style": input_style,
    }


class CompleteThemeCSSGenerator:
    """Generate complete theme CSS including colors, typography, spacing, etc."""

    def __init__(
        self, theme_name: str = "material", color_preset: str = None, css_prefix: str = ""
    ):
        """
        Initialize complete theme CSS generator.

        Args:
            theme_name: Name of the design system (material, ios, bauhaus, etc.)
            color_preset: Color preset name (default, blue, dracula, etc.)
            css_prefix: Namespace prefix for component CSS classes (e.g. "dj-")
        """
        self.ds = get_design_system(theme_name)
        if not self.ds:
            raise ValueError(f"Design system '{theme_name}' not found")

        self.color_preset = color_preset or "default"
        self.css_prefix = css_prefix

        # Initialize color generator
        self.color_generator = ColorCSSGenerator(preset_name=self.color_preset)

    def generate_css(self) -> str:
        """Generate complete CSS for the theme.

        Produces a single @layer tokens block containing both color tokens
        and theme vars, avoiding duplicate layer declarations.
        """
        config = get_theme_config()
        use_layers = config.get("use_css_layers", True)
        layer_order = config.get("css_layer_order", "base, tokens, components, theme")

        # Build raw color token parts (unwrapped — we'll wrap once)
        color_parts = [
            self.color_generator._generate_light_mode(),
            "",
            self.color_generator._generate_dark_mode(),
            "",
            self.color_generator._generate_system_preference(),
        ]

        if self.color_generator.include_design_tokens:
            from .design_tokens import generate_design_tokens_css

            color_parts.extend(["", "", generate_design_tokens_css()])

        color_tokens_css = "\n".join(color_parts)

        theme_vars = self._generate_theme_vars()
        typography_css = self._generate_typography_classes()
        component_css = self._generate_component_styles()

        base_css = (
            self.color_generator._generate_base_styles()
            if self.color_generator.include_base_styles
            else ""
        )
        utilities_css = (
            self.color_generator._generate_utilities()
            if self.color_generator.include_utilities
            else ""
        )
        surface_css = self.color_generator._generate_surface_styles()

        parts = [
            "/* djust-theming - Complete Theme CSS */",
            "",
        ]

        if use_layers:
            parts.append(f"@layer {layer_order};")
            parts.append("")
            # Color tokens go in @layer tokens (light, dark, system preference)
            parts.append(f"@layer tokens {{\n{color_tokens_css}\n}}")
            # Design system theme vars (font, shadow, spacing, radius, duration)
            # emitted OUTSIDE @layer so they override base.css static defaults
            parts.extend(["", theme_vars])
            if base_css:
                parts.extend(["", f"@layer base {{\n{base_css}\n}}"])
            if utilities_css:
                parts.extend(["", f"@layer components {{\n{utilities_css}\n}}"])
            if surface_css:
                parts.extend(["", f"@layer components {{\n{surface_css}\n}}"])
            parts.extend(
                [
                    "",
                    f"@layer components {{\n{typography_css}\n}}",
                    "",
                    f"@layer components {{\n{component_css}\n}}",
                ]
            )
        else:
            all_tokens = color_tokens_css + "\n\n" + theme_vars
            parts.append(all_tokens)
            if base_css:
                parts.extend(["", base_css])
            if utilities_css:
                parts.extend(["", utilities_css])
            if surface_css:
                parts.extend(["", surface_css])
            parts.extend(
                [
                    "",
                    typography_css,
                    "",
                    component_css,
                ]
            )

        return "\n".join(parts)

    def generate_critical_css(self) -> str:
        """Generate critical CSS for inline delivery.

        Includes color tokens (from ColorCSSGenerator) and theme-specific
        :root variables (typography, spacing, shadows, etc.). These are
        needed for first paint to avoid FOUC.

        Produces a single @layer tokens block containing both color tokens
        and theme vars, avoiding duplicate layer declarations.

        Returns:
            CSS string suitable for inlining in a <style> tag.
        """
        config = get_theme_config()
        config.get("use_css_layers", True)
        config.get("css_layer_order", "base, tokens, components, theme")

        # Build raw color token CSS (light/dark/system + design token root vars)
        color_parts = [
            self.color_generator._generate_light_mode(),
            "",
            self.color_generator._generate_dark_mode(),
            "",
            self.color_generator._generate_system_preference(),
        ]

        if self.color_generator.include_design_tokens:
            from .design_tokens import generate_design_tokens_root_css

            color_parts.extend(["", "", generate_design_tokens_root_css()])

        # Theme vars (:root with typography, spacing, shadows, etc.)
        theme_vars = self._generate_theme_vars()

        # Base element styles (body bg/color, * border-color, transitions)
        # Must be in critical CSS to prevent layout shift when deferred CSS loads.
        base_css = (
            self.color_generator._generate_base_styles()
            if self.color_generator.include_base_styles
            else ""
        )

        # Combine all token CSS
        all_tokens = "\n".join(color_parts) + "\n\n" + theme_vars

        parts = [
            "/* djust-theming - Critical CSS (inline) */",
            "",
            # CSS variables are NOT wrapped in @layer — they must have
            # highest priority since all other styles depend on them.
            all_tokens,
        ]

        if base_css:
            parts.extend(["", base_css])

        return "\n".join(parts)

    def generate_deferred_css(self) -> str:
        """Generate deferred CSS for async loading.

        Includes base styles, utility classes, design token classes,
        typography classes, and component styles. Not needed for first paint.

        Returns:
            CSS string suitable for serving from a <link> tag.
        """
        config = get_theme_config()
        use_layers = config.get("use_css_layers", True)

        # Build deferred parts directly (avoiding duplicate comment headers)
        # Note: base_css (body/border-color/transitions) is now in critical CSS
        utilities_css = (
            self.color_generator._generate_utilities()
            if self.color_generator.include_utilities
            else ""
        )

        # Design token classes (typography hierarchy, interactive, layout, animations)
        design_classes = ""
        if self.color_generator.include_design_tokens:
            from .design_tokens import generate_design_tokens_classes_css

            design_classes = generate_design_tokens_classes_css()

        typography_css = self._generate_typography_classes()
        component_css = self._generate_component_styles()

        parts = [
            "/* djust-theming - Deferred CSS */",
        ]

        if use_layers:
            if utilities_css:
                parts.extend(["", f"@layer components {{\n{utilities_css}\n}}"])
            if design_classes:
                parts.extend(["", f"@layer components {{\n{design_classes}\n}}"])
            parts.extend(
                [
                    "",
                    f"@layer components {{\n{typography_css}\n}}",
                    "",
                    f"@layer components {{\n{component_css}\n}}",
                ]
            )
        else:
            if utilities_css:
                parts.extend(["", utilities_css])
            if design_classes:
                parts.extend(["", design_classes])
            parts.extend(
                [
                    "",
                    typography_css,
                    "",
                    component_css,
                ]
            )

        return "\n".join(parts)

    def _generate_theme_vars(self) -> str:
        """Generate theme-specific CSS custom properties from DesignSystem."""
        ds = self.ds
        typo = ds.typography
        layout = ds.layout
        surface = ds.surface
        anim = ds.animation

        parts = [
            ":root {",
            "  /* ========================================",
            f"     Design System: {ds.display_name}",
            f"     {ds.description}",
            "     ======================================== */",
            "",
        ]

        # Typography
        parts.extend(
            [
                "  /* Typography */",
                f"  --font-sans: {typo.body_font};",
                "  --font-mono: ui-monospace, SFMono-Regular, monospace;",
            ]
        )
        if typo.heading_font and typo.heading_font != typo.body_font:
            parts.append(f"  --font-display: {typo.heading_font};")

        # Font sizes — derived from base_size and heading_scale
        scale = _compute_type_scale(typo.base_size, typo.heading_scale)
        parts.extend(
            [
                "",
                "  /* Font Sizes */",
                f"  --text-xs: {scale['text_xs']};",
                f"  --text-sm: {scale['text_sm']};",
                f"  --text-base: {scale['text_base']};",
                f"  --text-lg: {scale['text_lg']};",
                f"  --text-xl: {scale['text_xl']};",
                f"  --text-2xl: {scale['text_2xl']};",
                f"  --text-3xl: {scale['text_3xl']};",
                f"  --text-4xl: {scale['text_4xl']};",
                f"  --text-5xl: {scale['text_5xl']};",
            ]
        )

        # Font weights — derived from body_weight and heading_weight
        body_w = int(typo.body_weight)
        heading_w = int(typo.heading_weight)
        section_w = int(getattr(typo, "section_heading_weight", heading_w))
        parts.extend(
            [
                "",
                "  /* Font Weights */",
                f"  --font-normal: {body_w};",
                f"  --font-medium: {min(body_w + 100, 900)};",
                f"  --font-semibold: {section_w};",
                f"  --font-bold: {heading_w};",
            ]
        )

        # Line heights
        line_h = float(typo.line_height)
        body_line_h = float(getattr(typo, "body_line_height", line_h + 0.1))
        parts.extend(
            [
                "",
                "  /* Line Heights */",
                f"  --leading-tight: {max(line_h - 0.15, 1.0)};",
                f"  --leading-normal: {line_h};",
                f"  --leading-relaxed: {body_line_h};",
                f"  --leading-loose: {body_line_h + 0.2};",
            ]
        )

        # Spacing — derived from space_unit
        sp = _compute_spacing_scale(layout.space_unit)
        parts.extend(
            [
                "",
                "  /* Spacing */",
                f"  --space-base: {sp['base']}rem;",
                "  --space-0: 0;",
                f"  --space-1: {sp['space_1']}rem;",
                f"  --space-2: {sp['space_2']}rem;",
                f"  --space-3: {sp['space_3']}rem;",
                f"  --space-4: {sp['space_4']}rem;",
                f"  --space-5: {sp['space_5']}rem;",
                f"  --space-6: {sp['space_6']}rem;",
                f"  --space-8: {sp['space_8']}rem;",
                f"  --space-10: {sp['space_10']}rem;",
                f"  --space-12: {sp['space_12']}rem;",
                f"  --space-16: {sp['space_16']}rem;",
                f"  --space-20: {sp['space_20']}rem;",
                f"  --space-24: {sp['space_24']}rem;",
            ]
        )

        # Border Radius — from layout border_radius_sm/md/lg, derive the rest
        r_sm = layout.border_radius_sm
        r_md = layout.border_radius_md
        r_lg = layout.border_radius_lg
        # Derive intermediate/larger sizes
        _parse_size_to_px(r_sm)
        _parse_size_to_px(r_md)
        r_lg_px = _parse_size_to_px(r_lg)

        def fmt_radius(px):
            if px == 0:
                return "0px"
            rem = px / 16
            if rem == int(rem):
                return f"{int(rem)}rem"
            return f"{rem:.3f}rem"

        parts.extend(
            [
                "",
                "  /* Border Radius */",
                f"  --radius-sm: {r_sm};",
                f"  --radius: {r_sm};",
                f"  --radius-md: {r_md};",
                f"  --radius-lg: {r_lg};",
                f"  --radius-xl: {fmt_radius(r_lg_px * 1.5)};",
                f"  --radius-2xl: {fmt_radius(r_lg_px * 2)};",
                f"  --radius-3xl: {fmt_radius(r_lg_px * 3)};",
                "  --radius-full: 9999px;",
            ]
        )

        # Shadows — from surface shadow_sm/md/lg, derive the rest
        parts.extend(
            [
                "",
                "  /* Shadows */",
                f"  --shadow-xs: {surface.shadow_sm};",
                f"  --shadow-sm: {surface.shadow_sm};",
                f"  --shadow: {surface.shadow_md};",
                f"  --shadow-md: {surface.shadow_md};",
                f"  --shadow-lg: {surface.shadow_lg};",
                f"  --shadow-xl: {surface.shadow_lg};",
                f"  --shadow-2xl: {surface.shadow_lg};",
                "  --shadow-inner: inset 0 2px 4px 0 rgb(0 0 0 / 0.05);",
            ]
        )

        # Animations — from animation style
        easing_variants = _derive_easing_variants(anim.easing)
        parts.extend(
            [
                "",
                "  /* Animations */",
                f"  --duration-fast: {anim.duration_fast};",
                f"  --duration-normal: {anim.duration_normal};",
                f"  --duration-slow: {anim.duration_slow};",
                f"  --ease-in: {easing_variants['ease_in']};",
                f"  --ease-out: {easing_variants['ease_out']};",
                f"  --ease-in-out: {easing_variants['ease_in_out']};",
            ]
        )

        if anim.transition_style == "bouncy":
            parts.append("  --ease-bounce: cubic-bezier(0.68, -0.55, 0.265, 1.55);")

        # Animation behavior
        entrance_map = {
            "fade": "dj-fade-in",
            "slide": "dj-slide-in",
            "scale": "dj-scale-in",
            "bounce": "dj-bounce-in",
            "none": "none",
        }
        click_map = {
            "pulse": "dj-click-pulse",
            "bounce": "dj-click-bounce",
            "scale": "dj-click-pulse",  # scale uses pulse (similar feel)
            "ripple": "dj-click-pulse",  # ripple falls back to pulse (pure CSS)
            "none": "none",
        }
        entrance_anim = entrance_map.get(anim.entrance_effect, "none")
        click_anim = click_map.get(anim.click_effect, "none")

        # Glow hover — emits a colored shadow for neon/glass themes
        hover_glow = "0 0 0 transparent"
        if anim.hover_effect == "glow":
            hover_glow = "0 0 20px hsl(var(--brand, var(--primary)) / 0.3)"

        parts.extend(
            [
                "",
                "  /* Animation Behavior */",
                f"  --hover-scale: {anim.hover_scale};",
                f"  --hover-translate-y: {anim.hover_translate_y};",
                f"  --hover-glow: {hover_glow};",
                f"  --entrance-animation: {entrance_anim};",
                f"  --click-animation: {click_anim};",
            ]
        )

        # Layout
        parts.extend(
            [
                "",
                "  /* Layout */",
                f"  --container-width: {layout.container_width};",
                f"  --grid-gap: {layout.grid_gap};",
                f"  --section-spacing: {layout.section_spacing};",
                f"  --hero-padding-top: {layout.hero_padding_top};",
                f"  --hero-padding-bottom: {layout.hero_padding_bottom};",
                f"  --hero-line-height: {layout.hero_line_height};",
                f"  --hero-max-width: {layout.hero_max_width};",
            ]
        )

        # Typography extras
        parts.extend(
            [
                "",
                "  /* Typography Extras */",
                f"  --letter-spacing: {typo.letter_spacing};",
                f"  --prose-max-width: {typo.prose_max_width};",
                f"  --badge-radius: {getattr(typo, 'badge_radius', '9999px')};",
                f"  --leading-body: {body_line_h};",
            ]
        )

        # Surface treatment
        parts.extend(
            [
                "",
                "  /* Surface Treatment */",
                f"  --border-width: {surface.border_width};",
                f"  --surface-treatment: {surface.surface_treatment};",
            ]
        )
        # Glass — frosted blur effect for overlays, navbars, cards
        if surface.backdrop_blur and surface.backdrop_blur != "0px":
            parts.append(f"  --glass-blur: {surface.backdrop_blur};")
            parts.append("  --glass-bg: hsl(var(--card) / 0.7);")
            parts.append("  --glass-border: hsl(var(--border) / 0.3);")
            parts.append("  --navbar-opacity: 0.85;")
        # Gradient — derived from theme brand/primary colors
        if surface.surface_treatment == "gradient":
            parts.append("  --gradient-from: hsl(var(--brand, var(--primary)) / 0.08);")
            parts.append("  --gradient-to: transparent;")
        # Noise — grain/dither overlay on body::after
        if surface.noise_opacity and surface.noise_opacity > 0:
            parts.append(f"  --noise-opacity: {surface.noise_opacity};")

        parts.append("}")

        return "\n".join(parts)

    def _generate_typography_classes(self) -> str:
        """Generate utility classes for typography."""
        return """/* Typography Utilities */
/* Note: body font-family/size/line-height is set in critical CSS base styles
   to prevent layout shift when deferred CSS loads. */

.font-sans { font-family: var(--font-sans); }
.font-mono { font-family: var(--font-mono); }
.font-display { font-family: var(--font-display, var(--font-sans)); }

.text-xs { font-size: var(--text-xs); }
.text-sm { font-size: var(--text-sm); }
.text-base { font-size: var(--text-base); }
.text-lg { font-size: var(--text-lg); }
.text-xl { font-size: var(--text-xl); }
.text-2xl { font-size: var(--text-2xl); }
.text-3xl { font-size: var(--text-3xl); }
.text-4xl { font-size: var(--text-4xl); }
.text-5xl { font-size: var(--text-5xl); }

.font-normal { font-weight: var(--font-normal); }
.font-medium { font-weight: var(--font-medium); }
.font-semibold { font-weight: var(--font-semibold); }
.font-bold { font-weight: var(--font-bold); }

.leading-tight { line-height: var(--leading-tight); }
.leading-normal { line-height: var(--leading-normal); }
.leading-relaxed { line-height: var(--leading-relaxed); }"""

    def _generate_component_styles(self) -> str:
        """Generate component styles based on design system."""
        styles = _infer_component_styles(self.ds)
        p = self.css_prefix  # shorthand for prefix

        parts = ["/* Component Styles */"]

        # Button styles
        if styles["button_style"] == "solid":
            parts.append(f"""
.{p}btn {{
  border-radius: var(--radius-md);
  box-shadow: var(--shadow-sm);
  transition: all var(--duration-normal) var(--ease-out);
}}
.{p}btn:hover {{
  box-shadow: var(--shadow);
  transform: translateY(-1px);
}}""")
        elif styles["button_style"] == "outlined":
            parts.append(f"""
.{p}btn {{
  border-radius: var(--radius);
  border: 2px solid currentColor;
  background: transparent;
  transition: all var(--duration-fast) var(--ease-out);
}}
.{p}btn:hover {{
  background: currentColor;
  color: var(--background);
}}""")
        elif styles["button_style"] == "ghost":
            parts.append(f"""
.{p}btn {{
  border-radius: var(--radius);
  background: transparent;
  transition: background var(--duration-fast) var(--ease-out);
}}
.{p}btn:hover {{
  background: hsl(var(--accent) / 0.1);
}}""")

        # Card styles
        if styles["card_style"] == "elevated":
            parts.append(f"""
.{p}card {{
  border-radius: var(--radius-lg);
  box-shadow: var(--shadow);
  transition: box-shadow var(--duration-normal) var(--ease-out);
}}
.{p}card:hover {{
  box-shadow: var(--shadow-lg);
}}""")
        elif styles["card_style"] == "outlined":
            parts.append(f"""
.{p}card {{
  border-radius: var(--radius-md);
  border: 1px solid hsl(var(--border));
  box-shadow: none;
}}""")
        elif styles["card_style"] == "flat":
            parts.append(f"""
.{p}card {{
  border-radius: var(--radius-sm);
  background: hsl(var(--muted) / 0.3);
  box-shadow: none;
}}""")

        # Input styles
        if styles["input_style"] == "outlined":
            parts.append(f"""
.{p}form-input {{
  border-radius: var(--radius);
  border: 2px solid hsl(var(--input));
  background: transparent;
  transition: border-color var(--duration-fast) var(--ease-out);
}}
.{p}form-input:focus {{
  border-color: hsl(var(--ring));
  outline: none;
}}""")
        elif styles["input_style"] == "filled":
            parts.append(f"""
.{p}form-input {{
  border-radius: var(--radius) var(--radius) 0 0;
  border: none;
  border-bottom: 2px solid hsl(var(--input));
  background: hsl(var(--muted) / 0.5);
  transition: all var(--duration-fast) var(--ease-out);
}}
.{p}form-input:focus {{
  border-bottom-color: hsl(var(--ring));
  background: hsl(var(--muted) / 0.7);
  outline: none;
}}""")
        elif styles["input_style"] == "underlined":
            parts.append(f"""
.{p}form-input {{
  border-radius: 0;
  border: none;
  border-bottom: 1px solid hsl(var(--input));
  background: transparent;
  transition: border-color var(--duration-fast) var(--ease-out);
}}
.{p}form-input:focus {{
  border-bottom-width: 2px;
  border-bottom-color: hsl(var(--ring));
  outline: none;
}}""")

        return "\n".join(parts)


@lru_cache(maxsize=256)
def generate_theme_css(theme_name: str, color_preset: str = None, css_prefix: str = "") -> str:
    """
    Generate complete CSS for a theme (cached).

    Results are cached by (theme_name, color_preset, css_prefix). Use
    ``clear_css_cache()`` to invalidate during development.

    Args:
        theme_name: Name of the design system (material, ios, bauhaus, etc.)
        color_preset: Optional color preset override
        css_prefix: CSS class prefix for component styles (e.g. "dj-")

    Returns:
        Complete CSS string for the theme
    """
    if color_preset is None:
        color_preset = "default"

    generator = CompleteThemeCSSGenerator(theme_name, color_preset, css_prefix=css_prefix)
    return generator.generate_css()
