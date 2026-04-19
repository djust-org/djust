"""
CSS generator for theme presets.

Generates CSS custom properties from theme tokens, supporting both
light and dark modes with system preference detection.
"""

from functools import lru_cache

from .design_tokens import (
    generate_design_tokens_classes_css,
    generate_design_tokens_css,
    generate_design_tokens_root_css,
)
from .manager import get_theme_config
from .presets import ThemeTokens, get_preset


class ThemeCSSGenerator:
    """Generate CSS from theme tokens."""

    def __init__(
        self,
        preset_name: str = "default",
        custom_tokens: dict | None = None,
        include_base_styles: bool = True,
        include_utilities: bool = True,
        include_design_tokens: bool = True,
    ):
        """
        Initialize CSS generator.

        Args:
            preset_name: Name of the theme preset to use
            custom_tokens: Optional dict of token overrides
            include_base_styles: Include base body/element styles
            include_utilities: Include utility classes
            include_design_tokens: Include design system tokens (spacing, typography, etc.)
        """
        self.preset = get_preset(preset_name)
        self.custom_tokens = custom_tokens or {}
        self.include_base_styles = include_base_styles
        self.include_utilities = include_utilities
        self.include_design_tokens = include_design_tokens

    def _tokens_to_css_vars(self, tokens: ThemeTokens, indent: str = "  ") -> str:
        """Convert ThemeTokens to CSS custom property declarations."""
        lines = []

        # Color tokens
        color_mappings = [
            ("background", tokens.background),
            ("foreground", tokens.foreground),
            ("card", tokens.card),
            ("card-foreground", tokens.card_foreground),
            ("popover", tokens.popover),
            ("popover-foreground", tokens.popover_foreground),
            ("primary", tokens.primary),
            ("primary-foreground", tokens.primary_foreground),
            ("secondary", tokens.secondary),
            ("secondary-foreground", tokens.secondary_foreground),
            ("muted", tokens.muted),
            ("muted-foreground", tokens.muted_foreground),
            ("accent", tokens.accent),
            ("accent-foreground", tokens.accent_foreground),
            ("destructive", tokens.destructive),
            ("destructive-foreground", tokens.destructive_foreground),
            ("success", tokens.success),
            ("success-foreground", tokens.success_foreground),
            ("warning", tokens.warning),
            ("warning-foreground", tokens.warning_foreground),
            ("info", tokens.info),
            ("info-foreground", tokens.info_foreground),
            ("link", tokens.link),
            ("link-hover", tokens.link_hover),
            ("code", tokens.code),
            ("code-foreground", tokens.code_foreground),
            ("selection", tokens.selection),
            ("selection-foreground", tokens.selection_foreground),
            ("brand", tokens.brand),
            ("brand-foreground", tokens.brand_foreground),
            ("border", tokens.border),
            ("input", tokens.input),
            ("ring", tokens.ring),
            ("surface-1", tokens.surface_1),
            ("surface-2", tokens.surface_2),
            ("surface-3", tokens.surface_3),
        ]

        for name, color in color_mappings:
            lines.append(f"{indent}--{name}: {color.to_hsl()};")

        # shadcn/ui compatibility aliases (extended tokens)
        shadcn_mappings = [
            ("sidebar-background", tokens.background),
            ("sidebar-foreground", tokens.foreground),
            ("sidebar-primary", tokens.primary),
            ("sidebar-primary-foreground", tokens.primary_foreground),
            ("sidebar-accent", tokens.accent),
            ("sidebar-accent-foreground", tokens.accent_foreground),
            ("sidebar-border", tokens.border),
            ("sidebar-ring", tokens.ring),
            ("chart-1", tokens.primary),
            ("chart-2", tokens.secondary),
            ("chart-3", tokens.accent),
            ("chart-4", tokens.success),
            ("chart-5", tokens.warning),
            ("chart-6", tokens.info),
        ]

        for name, color in shadcn_mappings:
            lines.append(f"{indent}--{name}: {color.to_hsl()};")

        # Extra CSS custom properties (brand-specific variables)
        if self.preset.extra_css_vars:
            lines.append("")
            for name, value in self.preset.extra_css_vars.items():
                lines.append(f"{indent}--{name}: {value};")

        return "\n".join(lines)

    def _generate_light_mode(self) -> str:
        """Generate :root light mode variables.

        Always uses light tokens for :root (the web default). Dark-first
        themes get their dark values via html[data-theme="dark"] which has
        higher specificity. This prevents a color flash where dark defaults
        render before the data-theme attribute selector takes effect.
        """
        tokens = self.preset.light
        return f""":root {{
{self._tokens_to_css_vars(tokens)}
  --radius: {self.preset.radius}rem;
}}"""

    def _generate_dark_mode(self) -> str:
        """Generate dark mode variables for explicit data-theme attribute.

        Uses html[data-theme="*"] selectors to set CSS custom properties on :root
        via cascade (html sets vars, body inherits). This approach avoids
        specificity conflicts with the system-preference media query.

        The :root selector sets the DEFAULT (theme-first) values.
        html[data-theme="light"] sets LIGHT values (override for light-first themes).
        html[data-theme="dark"] sets DARK values (override for dark-first themes).
        When data-theme is absent, bare :root provides the default.
        When data-theme is present, the html[data-theme] rule overrides bare :root.
        """
        # For dark-first: light values come from html[data-theme="light"]
        # For light-first: dark values come from html[data-theme="dark"]
        light_tokens = self.preset.light
        dark_tokens = self.preset.dark

        light_extra = self._extra_vars_block(
            self.preset.extra_css_vars_light or self.preset.extra_css_vars,
            indent="  ",
            important=True,
        )
        dark_extra = self._extra_vars_block(
            self.preset.extra_css_vars_dark or self.preset.extra_css_vars,
            indent="  ",
            important=True,
        )
        return f"""html[data-theme="light"] {{
{self._tokens_to_css_vars(light_tokens, indent="  ")}
{light_extra}
}}
html[data-theme="dark"] {{
{self._tokens_to_css_vars(dark_tokens, indent="  ")}
{dark_extra}
}}"""

    def _extra_vars_block(
        self, extra_vars: dict | None, indent: str = "  ", important: bool = False
    ) -> str:
        """Generate CSS custom properties from extra_vars dict, if provided."""
        if not extra_vars:
            return ""
        suffix = " !important" if important else ""
        lines = []
        for name, value in extra_vars.items():
            lines.append(f"{indent}--{name}: {value}{suffix};")
        return "\n".join(lines)

    def _generate_system_preference(self) -> str:
        """Generate system preference media query for auto dark mode.

        For dark-first themes (default_mode="dark"): skip the media query.
        The explicit html[data-theme="*"] selectors handle both light and
        dark modes, and the anti-FOUC script sets data-theme appropriately.

        For light-first themes: media query overrides bare :root when OS
        prefers dark. The html[data-theme="dark"] explicit selector has
        higher specificity and overrides the media query when the user
        explicitly chooses dark.
        """
        if self.preset.default_mode == "dark":
            # Dark-first: explicit selectors handle everything, skip OS override
            return ""
        else:
            # Light default → system preference shows dark mode
            return """@media (prefers-color-scheme: dark) {{
  :root:not([data-theme]) {{
{vars}  }}
}}""".format(vars=self._tokens_to_css_vars(self.preset.dark, indent="    "))

    def _generate_surface_styles(self) -> str:
        """Generate surface treatment CSS (glass panels, gradients, noise)."""
        if not self.preset.surface:
            return ""

        s = self.preset.surface
        lines = ["/* Surface treatments */"]

        if s.style == "glass":
            lines.append(""".glass-panel {
  background: var(--glass-background, rgba(21, 27, 43, 0.7));
  backdrop-filter: blur(var(--glass-blur, 12px));
  -webkit-backdrop-filter: blur(var(--glass-blur, 12px));
  border: 1px solid var(--glass-border, rgba(255, 255, 255, 0.1));
  border-radius: var(--surface-radius, var(--radius, 0.5rem));
}""")

        elif s.style == "gradient":
            lines.append(f""".gradient-surface {{
  background: linear-gradient({s.gradient_direction}, var(--gradient-from, #1e293b), var(--gradient-to, #0f172a));
  border-radius: var(--surface-radius, var(--radius, 0.5rem));
}}""")

        elif s.style == "noise":
            lines.append(f""".noise-surface {{
  position: relative;
}}
.noise-surface::before {{
  content: '';
  position: absolute;
  inset: 0;
  background-image: url("data:image/svg+xml,%3Csvg viewBox='0 0 256 256' xmlns='http://www.w3.org/2000/svg'%3E%3Cfilter id='noise'%3E%3CfeTurbulence type='fractalNoise' baseFrequency='0.65' numOctaves='3' stitchTiles='stitch'/%3E%3C/filter%3E%3Crect width='100%25' height='100%25' filter='url(%23noise)' opacity='{s.noise_opacity}'/%3E%3C/svg%3E");
  pointer-events: none;
  border-radius: inherit;
}}""")

        # Add CSS custom properties for surface if extra_css_vars not used
        if s.glass_background and s.style == "glass":
            lines.append("\n:root {")
            lines.append(f"  --glass-background: {s.glass_background};")
            lines.append(f"  --glass-border: {s.glass_border};")
            lines.append(f"  --glass-blur: {s.glass_blur};")
            if s.surface_radius:
                lines.append(f"  --surface-radius: {s.surface_radius};")
            lines.append("}")

        return "\n".join(lines)

    def _generate_base_styles(self) -> str:
        """Generate base element styles using CSS variables."""
        return """/* Base styles */
* {
  border-color: hsl(var(--border));
}

body {
  background-color: hsl(var(--background));
  color: hsl(var(--foreground));
  font-family: var(--font-sans);
  font-size: var(--text-base, 1rem);
  line-height: var(--leading-body, var(--leading-normal, 1.5));
  font-feature-settings: "rlig" 1, "calt" 1;
}

/* Heading typography — uses design system tokens */
h1, h2, h3, h4, h5, h6 {
  font-family: var(--font-display, var(--font-sans));
  font-weight: var(--font-bold, 700);
  letter-spacing: var(--letter-spacing, normal);
  line-height: var(--leading-tight, 1.25);
}

h1 { font-size: var(--text-4xl, 2.25rem); }
h2 { font-size: var(--text-3xl, 1.875rem); }
h3 { font-size: var(--text-2xl, 1.5rem); }
h4 { font-size: var(--text-xl, 1.25rem); }
h5 { font-size: var(--text-lg, 1.125rem); }
h6 { font-size: var(--text-base, 1rem); }

/* Prose width for reading themes */
p {
  max-width: var(--prose-max-width, none);
}

/* Smooth theme transitions — only after initial paint to prevent FOUC */
html.theme-ready *,
html.theme-ready *::before,
html.theme-ready *::after {
  transition: background-color 0.2s ease, border-color 0.2s ease, color 0.2s ease;
}

/* Reduce motion for users who prefer it */
@media (prefers-reduced-motion: reduce) {
  *,
  *::before,
  *::after {
    transition: none !important;
    animation: none !important;
  }
}"""

    def _generate_utilities(self) -> str:
        """Generate utility classes for theme colors."""
        return """/* Theme utility classes */

/* Backgrounds */
.bg-background { background-color: hsl(var(--background)); }
.bg-foreground { background-color: hsl(var(--foreground)); }
.bg-card { background-color: hsl(var(--card)); }
.bg-popover { background-color: hsl(var(--popover)); }
.bg-primary { background-color: hsl(var(--primary)); }
.bg-secondary { background-color: hsl(var(--secondary)); }
.bg-muted { background-color: hsl(var(--muted)); }
.bg-accent { background-color: hsl(var(--accent)); }
.bg-destructive { background-color: hsl(var(--destructive)); }
.bg-success { background-color: hsl(var(--success)); }
.bg-warning { background-color: hsl(var(--warning)); }
.bg-info { background-color: hsl(var(--info)); }
.bg-code { background-color: hsl(var(--code)); }
.bg-selection { background-color: hsl(var(--selection)); }

/* Text colors */
.text-foreground { color: hsl(var(--foreground)); }
.text-card-foreground { color: hsl(var(--card-foreground)); }
.text-popover-foreground { color: hsl(var(--popover-foreground)); }
.text-primary { color: hsl(var(--primary)); }
.text-primary-foreground { color: hsl(var(--primary-foreground)); }
.text-secondary-foreground { color: hsl(var(--secondary-foreground)); }
.text-muted-foreground { color: hsl(var(--muted-foreground)); }
.text-accent-foreground { color: hsl(var(--accent-foreground)); }
.text-destructive { color: hsl(var(--destructive)); }
.text-destructive-foreground { color: hsl(var(--destructive-foreground)); }
.text-success { color: hsl(var(--success)); }
.text-success-foreground { color: hsl(var(--success-foreground)); }
.text-warning { color: hsl(var(--warning)); }
.text-warning-foreground { color: hsl(var(--warning-foreground)); }
.text-info { color: hsl(var(--info)); }
.text-info-foreground { color: hsl(var(--info-foreground)); }
.text-link { color: hsl(var(--link)); }
.text-code-foreground { color: hsl(var(--code-foreground)); }
.text-selection-foreground { color: hsl(var(--selection-foreground)); }

/* Borders */
.border-border { border-color: hsl(var(--border)); }
.border-input { border-color: hsl(var(--input)); }
.border-primary { border-color: hsl(var(--primary)); }
.border-secondary { border-color: hsl(var(--secondary)); }
.border-destructive { border-color: hsl(var(--destructive)); }
.border-success { border-color: hsl(var(--success)); }
.border-warning { border-color: hsl(var(--warning)); }
.border-info { border-color: hsl(var(--info)); }

/* Ring (focus) */
.ring-ring { --tw-ring-color: hsl(var(--ring)); }

/* Rounded corners using theme radius */
.rounded-theme { border-radius: var(--radius); }
.rounded-theme-sm { border-radius: calc(var(--radius) - 0.25rem); }
.rounded-theme-md { border-radius: calc(var(--radius) + 0.25rem); }
.rounded-theme-lg { border-radius: calc(var(--radius) + 0.5rem); }

/* Common component patterns */
.card-theme {
  background-color: hsl(var(--card));
  color: hsl(var(--card-foreground));
  border: 1px solid hsl(var(--border));
  border-radius: var(--radius);
}

.btn-primary {
  background-color: hsl(var(--primary));
  color: hsl(var(--primary-foreground));
  border-radius: var(--radius);
}

.btn-primary:hover {
  background-color: hsl(var(--primary) / 0.9);
}

.btn-secondary {
  background-color: hsl(var(--secondary));
  color: hsl(var(--secondary-foreground));
  border-radius: var(--radius);
}

.btn-secondary:hover {
  background-color: hsl(var(--secondary) / 0.8);
}

.btn-destructive {
  background-color: hsl(var(--destructive));
  color: hsl(var(--destructive-foreground));
  border-radius: var(--radius);
}

.btn-destructive:hover {
  background-color: hsl(var(--destructive) / 0.9);
}

.input-theme {
  background-color: transparent;
  border: 1px solid hsl(var(--input));
  border-radius: var(--radius);
}

.input-theme:focus {
  outline: none;
  box-shadow: 0 0 0 2px hsl(var(--ring) / 0.5);
}

/* Badge variants */
.badge-primary {
  background-color: hsl(var(--primary));
  color: hsl(var(--primary-foreground));
}

.badge-secondary {
  background-color: hsl(var(--secondary));
  color: hsl(var(--secondary-foreground));
}

.badge-destructive {
  background-color: hsl(var(--destructive));
  color: hsl(var(--destructive-foreground));
}

.badge-success {
  background-color: hsl(var(--success));
  color: hsl(var(--success-foreground));
}

.badge-warning {
  background-color: hsl(var(--warning));
  color: hsl(var(--warning-foreground));
}

.badge-info {
  background-color: hsl(var(--info));
  color: hsl(var(--info-foreground));
}

/* Link styles */
a,
.link {
  color: hsl(var(--link));
  text-decoration: none;
  transition: color 150ms ease;
}

a:hover,
.link:hover {
  color: hsl(var(--link-hover));
  text-decoration: underline;
}

/* Code blocks */
code,
.code {
  background-color: hsl(var(--code));
  color: hsl(var(--code-foreground));
  padding: 0.125rem 0.375rem;
  border-radius: calc(var(--radius) - 0.125rem);
  font-family: ui-monospace, SFMono-Regular, "SF Mono", Monaco, "Cascadia Mono",
               "Segoe UI Mono", "Roboto Mono", Menlo, Consolas, "Liberation Mono",
               monospace;
  font-size: 0.875em;
}

pre code {
  padding: 0;
  background: transparent;
}

/* Selection */
::selection {
  background-color: hsl(var(--selection));
  color: hsl(var(--selection-foreground));
}

::-moz-selection {
  background-color: hsl(var(--selection));
  color: hsl(var(--selection-foreground));
}

/* ── Entrance animations (theme-controlled via --entrance-animation) ───── */
@keyframes dj-fade-in {
  from { opacity: 0; transform: translateY(8px); }
  to { opacity: 1; transform: translateY(0); }
}
@keyframes dj-slide-in {
  from { opacity: 0; transform: translateY(20px); }
  to { opacity: 1; transform: translateY(0); }
}
@keyframes dj-scale-in {
  from { opacity: 0; transform: scale(0.95); }
  to { opacity: 1; transform: scale(1); }
}
@keyframes dj-bounce-in {
  0% { opacity: 0; transform: scale(0.9) translateY(10px); }
  60% { opacity: 1; transform: scale(1.02) translateY(-2px); }
  100% { transform: scale(1) translateY(0); }
}

/* Staggered card entrance */
.card, .dj-card {
  animation: var(--entrance-animation, none) var(--duration-normal, 0.2s) var(--ease-out, ease-out) both;
}
.card:nth-child(2), .dj-card:nth-child(2) { animation-delay: calc(var(--duration-fast, 0.1s) * 1); }
.card:nth-child(3), .dj-card:nth-child(3) { animation-delay: calc(var(--duration-fast, 0.1s) * 2); }
.card:nth-child(4), .dj-card:nth-child(4) { animation-delay: calc(var(--duration-fast, 0.1s) * 3); }

/* ── Click feedback (theme-controlled via --click-animation) ───────────── */
@keyframes dj-click-pulse {
  0% { transform: scale(1); }
  50% { transform: scale(0.97); }
  100% { transform: scale(1); }
}
@keyframes dj-click-bounce {
  0% { transform: scale(1); }
  40% { transform: scale(0.93); }
  70% { transform: scale(1.03); }
  100% { transform: scale(1); }
}

.btn:active:not(:disabled), .dj-btn:active:not(:disabled) {
  animation: var(--click-animation, none) var(--duration-fast, 0.15s) var(--ease-out, ease-out);
}"""

    def generate_css(self) -> str:
        """Generate complete CSS for the theme."""
        config = get_theme_config()
        use_layers = config.get("use_css_layers", True)
        layer_order = config.get("css_layer_order", "base, tokens, components, theme")

        # Token CSS: :root vars, dark mode, system preference, design tokens
        tokens_css_parts = [
            self._generate_light_mode(),
            "",
            self._generate_dark_mode(),
            "",
            self._generate_system_preference(),
        ]

        if self.include_design_tokens:
            tokens_css_parts.extend(["", "", generate_design_tokens_css()])

        tokens_css = "\n".join(tokens_css_parts)

        sections = [
            "/* djust-theming - Auto-generated CSS */",
            "",
        ]

        if use_layers:
            sections.append(f"@layer {layer_order};")
            sections.append("")
            sections.append(f"@layer tokens {{\n{tokens_css}\n}}")
        else:
            sections.append(tokens_css)

        if self.include_base_styles:
            base_css = self._generate_base_styles()
            if use_layers:
                sections.extend(["", f"@layer base {{\n{base_css}\n}}"])
            else:
                sections.extend(["", base_css])

        if self.include_utilities:
            utilities_css = self._generate_utilities()
            if use_layers:
                sections.extend(["", f"@layer components {{\n{utilities_css}\n}}"])
            else:
                sections.extend(["", utilities_css])

        # Surface treatments (glass panels, gradients, noise)
        surface_css = self._generate_surface_styles()
        if surface_css:
            if use_layers:
                sections.extend(["", f"@layer components {{\n{surface_css}\n}}"])
            else:
                sections.extend(["", surface_css])

        return "\n".join(sections)

    def generate_critical_css(self) -> str:
        """Generate critical CSS for inline delivery (tokens + layer declaration only).

        Critical CSS contains only the parts needed for first paint:
        - @layer order declaration
        - :root CSS custom properties (color tokens, light mode)
        - Dark mode selectors
        - System preference media query
        - Design token :root custom properties (spacing, typography scale, etc.)

        Class-based design tokens (typography classes, interactive utilities,
        layout utilities, animation keyframes) are in deferred CSS.

        Returns:
            CSS string suitable for inlining in a <style> tag.
        """
        config = get_theme_config()
        use_layers = config.get("use_css_layers", True)
        layer_order = config.get("css_layer_order", "base, tokens, components, theme")

        tokens_css_parts = [
            self._generate_light_mode(),
            "",
            self._generate_dark_mode(),
            "",
            self._generate_system_preference(),
        ]

        if self.include_design_tokens:
            tokens_css_parts.extend(["", "", generate_design_tokens_root_css()])

        tokens_css = "\n".join(tokens_css_parts)

        sections = [
            "/* djust-theming - Critical CSS (inline) */",
            "",
        ]

        if use_layers:
            sections.append(f"@layer {layer_order};")
            sections.append("")
            sections.append(f"@layer tokens {{\n{tokens_css}\n}}")
        else:
            sections.append(tokens_css)

        return "\n".join(sections)

    def generate_deferred_css(self) -> str:
        """Generate deferred CSS for async loading (base styles + utilities + design token classes).

        Deferred CSS contains parts not needed for first paint:
        - Base element styles (body resets, transitions)
        - Utility classes (.bg-*, .text-*, .btn-*, etc.)
        - Design token classes (typography hierarchy, interactive utilities,
          layout utilities, animation keyframes)

        Returns:
            CSS string suitable for serving from a <link> tag.
        """
        config = get_theme_config()
        use_layers = config.get("use_css_layers", True)

        sections = [
            "/* djust-theming - Deferred CSS */",
        ]

        if self.include_base_styles:
            base_css = self._generate_base_styles()
            if use_layers:
                sections.extend(["", f"@layer base {{\n{base_css}\n}}"])
            else:
                sections.extend(["", base_css])

        if self.include_utilities:
            utilities_css = self._generate_utilities()
            if use_layers:
                sections.extend(["", f"@layer components {{\n{utilities_css}\n}}"])
            else:
                sections.extend(["", utilities_css])

        if self.include_design_tokens:
            design_classes = generate_design_tokens_classes_css()
            if design_classes:
                if use_layers:
                    sections.extend(["", f"@layer components {{\n{design_classes}\n}}"])
                else:
                    sections.extend(["", design_classes])

        return "\n".join(sections)

    def generate_variables_only(self) -> str:
        """Generate only the CSS custom property declarations."""
        sections = [
            self._generate_light_mode(),
            "",
            self._generate_dark_mode(),
            "",
            self._generate_system_preference(),
        ]
        return "\n".join(sections)

    def generate_for_preset(self, preset_name: str) -> str:
        """Generate CSS for a specific preset."""
        old_preset = self.preset
        self.preset = get_preset(preset_name)
        css = self.generate_css()
        self.preset = old_preset
        return css


@lru_cache(maxsize=64)
def generate_theme_css(
    preset_name: str = "default",
    include_base_styles: bool = True,
    include_utilities: bool = True,
    include_design_tokens: bool = True,
) -> str:
    """
    Convenience function to generate CSS for a theme (cached).

    Results are cached by all parameters. Use ``clear_css_cache()``
    to invalidate during development.

    Args:
        preset_name: Name of the theme preset
        include_base_styles: Include base body/element styles
        include_utilities: Include utility classes
        include_design_tokens: Include design system tokens (spacing, typography, etc.)

    Returns:
        Complete CSS string
    """
    generator = ThemeCSSGenerator(
        preset_name=preset_name,
        include_base_styles=include_base_styles,
        include_utilities=include_utilities,
        include_design_tokens=include_design_tokens,
    )
    return generator.generate_css()
