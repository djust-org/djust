"""
Advanced CSS generator supporting design systems + color presets.

This extends the basic CSS generator to support the full flexibility
of design system + color preset combinations.
"""

from functools import lru_cache
from typing import Dict
from .presets import ThemeTokens, get_preset
from .theme_packs import (
    get_design_system,
    get_all_design_systems,
    TypographyStyle,
    LayoutStyle,
    SurfaceStyle,
    AnimationStyle,
)
from .css_generator import ThemeCSSGenerator


class DesignSystemCSSGenerator:
    """Generate CSS from design systems combined with color presets."""

    def __init__(
        self,
        design_system_name: str = "minimal",
        color_preset_name: str = "default",
        include_base_styles: bool = True,
        include_utilities: bool = True,
        css_prefix: str = "",
    ):
        """
        Initialize design system CSS generator.

        Args:
            design_system_name: Name of design system (typography, layout, etc.)
            color_preset_name: Name of color preset
            include_base_styles: Include base body/element styles
            include_utilities: Include utility classes
            css_prefix: Optional CSS custom property prefix
        """
        self.design_system = get_design_system(design_system_name)
        self.color_preset = get_preset(color_preset_name)
        self.include_base_styles = include_base_styles
        self.include_utilities = include_utilities
        self.css_prefix = css_prefix

        if not self.design_system:
            raise ValueError(f"Design system '{design_system_name}' not found")

    def _get_css_var_name(self, name: str) -> str:
        """Get CSS variable name with optional prefix."""
        if self.css_prefix:
            return f"--{self.css_prefix}-{name}"
        return f"--{name}"

    def _typography_to_css_vars(self, typography: TypographyStyle, indent: str = "  ") -> str:
        """Convert typography style to CSS variables."""
        lines = []
        lines.append(f"{indent}{self._get_css_var_name('font-sans')}: {typography.body_font};")
        lines.append(
            f"{indent}{self._get_css_var_name('font-display')}: {typography.heading_font};"
        )
        lines.append(f"{indent}{self._get_css_var_name('text-base')}: {typography.base_size};")
        lines.append(
            f"{indent}{self._get_css_var_name('leading-normal')}: {typography.line_height};"
        )
        return "\n".join(lines)

    def _layout_to_css_vars(self, layout: LayoutStyle, indent: str = "  ") -> str:
        """Convert layout style to CSS variables."""
        lines = []
        lines.append(f"{indent}{self._get_css_var_name('radius-sm')}: {layout.border_radius_sm};")
        lines.append(f"{indent}{self._get_css_var_name('radius')}: {layout.border_radius_md};")
        lines.append(f"{indent}{self._get_css_var_name('radius-lg')}: {layout.border_radius_lg};")

        # Also keep component specific ones if needed by custom styles
        shape_map = {
            "sharp": "0px",
            "rounded": "var(--radius)",
            "pill": "9999px",
            "organic": "var(--radius-lg)",
        }

        button_radius = shape_map.get(layout.button_shape, layout.border_radius_md)
        lines.append(f"{indent}{self._get_css_var_name('button-radius')}: {button_radius};")

        return "\n".join(lines)

    def _surface_to_css_vars(self, surface: SurfaceStyle, indent: str = "  ") -> str:
        """Convert surface style to CSS variables."""
        lines = []
        lines.append(f"{indent}{self._get_css_var_name('shadow-sm')}: {surface.shadow_sm};")
        lines.append(f"{indent}{self._get_css_var_name('shadow')}: {surface.shadow_md};")
        lines.append(f"{indent}{self._get_css_var_name('shadow-lg')}: {surface.shadow_lg};")
        return "\n".join(lines)

    def _animation_to_css_vars(self, animation: AnimationStyle, indent: str = "  ") -> str:
        """Convert animation style to CSS variables."""
        lines = []
        lines.append(
            f"{indent}{self._get_css_var_name('duration-fast')}: {animation.duration_fast};"
        )
        lines.append(
            f"{indent}{self._get_css_var_name('duration-normal')}: {animation.duration_normal};"
        )
        lines.append(
            f"{indent}{self._get_css_var_name('duration-slow')}: {animation.duration_slow};"
        )
        lines.append(f"{indent}{self._get_css_var_name('easing')}: {animation.easing};")

        # Hover effects
        if animation.hover_scale != 1.0:
            lines.append(
                f"{indent}{self._get_css_var_name('hover-scale')}: {animation.hover_scale};"
            )
        if animation.hover_translate_y != "0px":
            lines.append(
                f"{indent}{self._get_css_var_name('hover-translate-y')}: {animation.hover_translate_y};"
            )

        return "\n".join(lines)

    def _color_preset_to_css_vars(self, tokens: ThemeTokens, indent: str = "  ") -> str:
        """Convert color preset to CSS variables (reuse existing logic)."""
        # Use the existing color CSS generation from ThemeCSSGenerator
        generator = ThemeCSSGenerator()
        return generator._tokens_to_css_vars(tokens, indent)

    def _generate_design_system_root(self) -> str:
        """Generate :root variables for design system."""
        if not self.design_system:
            return ""

        lines = []
        lines.append(":root {")

        # Typography variables
        lines.append(self._typography_to_css_vars(self.design_system.typography))
        lines.append("")

        # Layout variables
        lines.append(self._layout_to_css_vars(self.design_system.layout))
        lines.append("")

        # Surface variables
        lines.append(self._surface_to_css_vars(self.design_system.surface))
        lines.append("")

        # Animation variables
        lines.append(self._animation_to_css_vars(self.design_system.animation))
        lines.append("")

        # Color variables (light mode)
        lines.append(self._color_preset_to_css_vars(self.color_preset.light))

        lines.append("}")

        return "\n".join(lines)

    def _generate_dark_mode_colors(self) -> str:
        """Generate dark mode color overrides."""
        return f""".dark,
[data-theme="dark"] {{
{self._color_preset_to_css_vars(self.color_preset.dark)}
}}"""

    def _generate_system_preference_colors(self) -> str:
        """Generate system preference media query for colors only."""
        return f"""@media (prefers-color-scheme: dark) {{
  :root:not([data-theme="light"]) {{
{self._color_preset_to_css_vars(self.color_preset.dark, indent="    ")}
  }}
}}"""

    def _generate_design_system_styles(self) -> str:
        """Generate styles that use design system variables."""
        if not self.design_system:
            return ""

        # Generate typography styles
        typography_css = self._generate_typography_styles()

        # Generate layout styles
        layout_css = self._generate_layout_styles()

        # Generate surface styles
        surface_css = self._generate_surface_styles()

        # Generate animation styles
        animation_css = self._generate_animation_styles()

        return f"""
/* Design System Styles */

{typography_css}

{layout_css}

{surface_css}

{animation_css}
"""

    def _generate_typography_styles(self) -> str:
        """Generate typography-related styles."""
        return """/* Typography */
body {
  font-family: var(--font-sans);
  font-size: var(--text-base);
  line-height: var(--leading-normal);
}

h1, h2, h3, h4, h5, h6 {
  font-family: var(--font-display);
}

h1 { font-size: 2.5rem; }
h2 { font-size: 2rem; }
h3 { font-size: 1.75rem; }
h4 { font-size: 1.5rem; }
h5 { font-size: 1.25rem; }
h6 { font-size: 1rem; }"""

    def _generate_layout_styles(self) -> str:
        """Generate layout-related styles."""
        return """/* Layout */
.container {
  max-width: 1200px;
  margin: 0 auto;
}

/* Component shapes */
.btn, button {
  border-radius: var(--radius);
}

.card {
  border-radius: var(--radius-lg);
}

.input, input, textarea, select {
  border-radius: var(--radius-sm);
}"""

    def _generate_surface_styles(self) -> str:
        """Generate surface-related styles."""
        return """/* Surfaces */
.shadow-sm { box-shadow: var(--shadow-sm); }
.shadow-md { box-shadow: var(--shadow-md); }
.shadow-lg { box-shadow: var(--shadow-lg); }

.border {
  border-width: var(--border-width);
  border-style: var(--border-style);
}

.surface-glass {
  backdrop-filter: blur(var(--backdrop-blur, 0));
  background: rgba(255, 255, 255, 0.1);
}

.surface-noise {
  position: relative;
}

.surface-noise::before {
  content: '';
  position: absolute;
  top: 0;
  left: 0;
  width: 100%;
  height: 100%;
  opacity: var(--noise-opacity, 0);
  background-image: url("data:image/svg+xml,%3Csvg viewBox='0 0 256 256' xmlns='http://www.w3.org/2000/svg'%3E%3Cfilter id='noiseFilter'%3E%3CfeTurbulence type='fractalNoise' baseFrequency='0.9' numOctaves='4' stitchTiles='stitch'/%3E%3C/filter%3E%3Crect width='100%25' height='100%25' filter='url(%23noiseFilter)'/%3E%3C/svg%3E");
  pointer-events: none;
}"""

    def _generate_animation_styles(self) -> str:
        """Generate animation-related styles."""
        return """/* Animations */
.transition {
  transition-duration: var(--duration-normal);
  transition-timing-function: var(--easing);
}

.transition-fast {
  transition-duration: var(--duration-fast);
  transition-timing-function: var(--easing);
}

.transition-slow {
  transition-duration: var(--duration-slow);
  transition-timing-function: var(--easing);
}

.hover-lift:hover {
  transform: translateY(var(--hover-translate-y, -2px)) scale(var(--hover-scale, 1.02));
}

.hover-scale:hover {
  transform: scale(var(--hover-scale, 1.05));
}

.hover-glow:hover {
  box-shadow: 0 0 20px rgba(var(--primary) / 0.3);
}

/* Respect reduced motion preferences */
@media (prefers-reduced-motion: reduce) {
  *, *::before, *::after {
    animation-duration: 0.01ms !important;
    animation-iteration-count: 1 !important;
    transition-duration: 0.01ms !important;
  }

  .hover-lift:hover,
  .hover-scale:hover {
    transform: none;
  }
}"""

    def generate_css(self) -> str:
        """Generate complete CSS for design system + color preset combination."""
        sections = [
            "/* djust-theming - Design System CSS */",
            f"/* Design System: {self.design_system.name if self.design_system else 'none'} */",
            f"/* Color Preset: {self.color_preset.name} */",
            "",
            self._generate_design_system_root(),
            "",
            self._generate_dark_mode_colors(),
            "",
            self._generate_system_preference_colors(),
        ]

        if self.include_base_styles:
            sections.extend(["", self._generate_design_system_styles()])

        if self.include_utilities:
            # Reuse existing color utilities from ThemeCSSGenerator
            generator = ThemeCSSGenerator()
            sections.extend(["", generator._generate_utilities()])

        return "\n".join(sections)

    def generate_for_combination(self, design_system_name: str, color_preset_name: str) -> str:
        """Generate CSS for a specific design system + color preset combination."""
        old_design = self.design_system
        old_preset = self.color_preset

        self.design_system = get_design_system(design_system_name)
        self.color_preset = get_preset(color_preset_name)

        css = self.generate_css()

        self.design_system = old_design
        self.color_preset = old_preset

        return css


@lru_cache(maxsize=256)
def generate_design_system_css(
    design_system_name: str = "minimal",
    color_preset_name: str = "default",
    include_base_styles: bool = True,
    include_utilities: bool = True,
) -> str:
    """
    Convenience function to generate CSS for design system + color preset (cached).

    Results are cached by all parameters. Use ``clear_css_cache()``
    to invalidate during development.

    Args:
        design_system_name: Name of the design system
        color_preset_name: Name of the color preset
        include_base_styles: Include base styles
        include_utilities: Include utility classes

    Returns:
        Complete CSS string
    """
    generator = DesignSystemCSSGenerator(
        design_system_name=design_system_name,
        color_preset_name=color_preset_name,
        include_base_styles=include_base_styles,
        include_utilities=include_utilities,
    )
    return generator.generate_css()


def generate_all_combinations_css() -> Dict[str, str]:
    """Generate CSS for all design system + color preset combinations."""
    from .presets import THEME_PRESETS

    combinations = {}
    design_systems = get_all_design_systems()

    for design_name in design_systems.keys():
        for preset_name in THEME_PRESETS.keys():
            key = f"{design_name}-{preset_name}"
            combinations[key] = generate_design_system_css(design_name, preset_name)

    return combinations
