"""
Tailwind CSS integration for djust-theming.

Provides utilities to:
- Generate tailwind.config.js with theme CSS variable mappings
- Export presets as Tailwind color configs
- Enable @apply with theme colors
- Generate Tailwind v4 CSS-first @theme blocks
"""

from typing import Dict, Any
from functools import lru_cache

from .presets import THEME_PRESETS, ThemePreset, ThemeTokens, get_preset


def generate_tailwind_config(
    preset_name: str = "default",
    extend_colors: bool = True,
    include_all_presets: bool = False,
) -> str:
    """
    Generate a tailwind.config.js file that uses djust-theming CSS variables.

    Args:
        preset_name: Name of the preset to use for color generation
        extend_colors: If True, extends Tailwind's default colors (recommended)
        include_all_presets: If True, includes all presets as separate color scales

    Returns:
        Complete tailwind.config.js file content as a string

    Example:
        >>> from djust.theming.tailwind import generate_tailwind_config
        >>> config = generate_tailwind_config('blue')
        >>> with open('tailwind.config.js', 'w') as f:
        ...     f.write(config)
    """
    preset = THEME_PRESETS.get(preset_name)
    if not preset:
        raise ValueError(f"Unknown preset: {preset_name}")

    # Generate color mappings for CSS variables
    colors_config = _generate_color_config(preset, extend_colors)

    # Optionally include all presets
    if include_all_presets:
        all_presets_config = _generate_all_presets_config()
        colors_config.update(all_presets_config)

    # Generate the full config
    config_content = f"""/** @type {{import('tailwindcss').Config}} */
module.exports = {{
  content: [
    './templates/**/*.html',
    './static/**/*.js',
    './**/*.py',  // For class names in Python code
  ],
  theme: {{
    {"extend: {" if extend_colors else "{"}
      colors: {{
{_format_colors_config(colors_config, indent=8)}
      }},
      borderRadius: {{
        lg: 'var(--radius)',
        md: 'calc(var(--radius) - 2px)',
        sm: 'calc(var(--radius) - 4px)',
      }},
    }},
  }},
  plugins: [],
}}
"""
    return config_content


def _generate_color_config(preset: ThemePreset, extend: bool = True) -> Dict[str, Any]:
    """Generate Tailwind color config from a preset."""
    colors = {
        "border": "hsl(var(--border))",
        "input": "hsl(var(--input))",
        "ring": "hsl(var(--ring))",
        "background": "hsl(var(--background))",
        "foreground": "hsl(var(--foreground))",
        "primary": {
            "DEFAULT": "hsl(var(--primary))",
            "foreground": "hsl(var(--primary-foreground))",
        },
        "secondary": {
            "DEFAULT": "hsl(var(--secondary))",
            "foreground": "hsl(var(--secondary-foreground))",
        },
        "destructive": {
            "DEFAULT": "hsl(var(--destructive))",
            "foreground": "hsl(var(--destructive-foreground))",
        },
        "muted": {
            "DEFAULT": "hsl(var(--muted))",
            "foreground": "hsl(var(--muted-foreground))",
        },
        "accent": {
            "DEFAULT": "hsl(var(--accent))",
            "foreground": "hsl(var(--accent-foreground))",
        },
        "popover": {
            "DEFAULT": "hsl(var(--popover))",
            "foreground": "hsl(var(--popover-foreground))",
        },
        "card": {
            "DEFAULT": "hsl(var(--card))",
            "foreground": "hsl(var(--card-foreground))",
        },
        "success": {
            "DEFAULT": "hsl(var(--success))",
            "foreground": "hsl(var(--success-foreground))",
        },
        "warning": {
            "DEFAULT": "hsl(var(--warning))",
            "foreground": "hsl(var(--warning-foreground))",
        },
    }

    return colors


def _generate_all_presets_config() -> Dict[str, Any]:
    """Generate Tailwind configs for all presets with preset-prefixed names."""
    all_configs = {}

    for name, preset in THEME_PRESETS.items():
        # Create direct color values for each preset (not CSS variables)
        # These are useful for static generation or preset-specific colors
        all_configs[f"preset-{name}"] = {
            "primary": preset.light.primary.to_hsl_func(),
            "secondary": preset.light.secondary.to_hsl_func(),
            "accent": preset.light.accent.to_hsl_func(),
            "muted": preset.light.muted.to_hsl_func(),
        }

    return all_configs


def _format_colors_config(colors: Dict[str, Any], indent: int = 8) -> str:
    """Format the colors config dict as JavaScript object notation."""
    lines = []
    indent_str = " " * indent

    for key, value in colors.items():
        if isinstance(value, dict):
            lines.append(f"{indent_str}{key}: {{")
            for sub_key, sub_value in value.items():
                # Handle DEFAULT specially (no quotes needed)
                if sub_key == "DEFAULT":
                    lines.append(f"{indent_str}  DEFAULT: '{sub_value}',")
                else:
                    lines.append(f"{indent_str}  {sub_key}: '{sub_value}',")
            lines.append(f"{indent_str}}},")
        else:
            lines.append(f"{indent_str}{key}: '{value}',")

    return "\n".join(lines)


def export_preset_as_tailwind_colors(preset_name: str = "default") -> Dict[str, str]:
    """
    Export a preset's colors in Tailwind-compatible format.

    This generates static color values (not CSS variables) that can be
    used directly in Tailwind config or for generating color palettes.

    Args:
        preset_name: Name of the preset to export

    Returns:
        Dictionary of color names to HSL values

    Example:
        >>> from djust.theming.tailwind import export_preset_as_tailwind_colors
        >>> colors = export_preset_as_tailwind_colors('blue')
        >>> colors['primary']
        'hsl(221, 83%, 53%)'
    """
    preset = THEME_PRESETS.get(preset_name)
    if not preset:
        raise ValueError(f"Unknown preset: {preset_name}")

    light = preset.light
    dark = preset.dark

    return {
        # Light mode colors
        "light-background": light.background.to_hsl_func(),
        "light-foreground": light.foreground.to_hsl_func(),
        "light-primary": light.primary.to_hsl_func(),
        "light-primary-foreground": light.primary_foreground.to_hsl_func(),
        "light-secondary": light.secondary.to_hsl_func(),
        "light-muted": light.muted.to_hsl_func(),
        "light-accent": light.accent.to_hsl_func(),
        "light-destructive": light.destructive.to_hsl_func(),
        "light-border": light.border.to_hsl_func(),
        # Dark mode colors
        "dark-background": dark.background.to_hsl_func(),
        "dark-foreground": dark.foreground.to_hsl_func(),
        "dark-primary": dark.primary.to_hsl_func(),
        "dark-primary-foreground": dark.primary_foreground.to_hsl_func(),
        "dark-secondary": dark.secondary.to_hsl_func(),
        "dark-muted": dark.muted.to_hsl_func(),
        "dark-accent": dark.accent.to_hsl_func(),
        "dark-destructive": dark.destructive.to_hsl_func(),
        "dark-border": dark.border.to_hsl_func(),
    }


def generate_tailwind_apply_examples() -> str:
    """
    Generate example CSS showing how to use @apply with theme colors.

    Returns:
        CSS code with @apply examples

    Example:
        >>> from djust.theming.tailwind import generate_tailwind_apply_examples
        >>> print(generate_tailwind_apply_examples())
    """
    return """/* Using @apply with djust-theming colors */

/* Button styles */
.btn-primary {
  @apply bg-primary text-primary-foreground px-4 py-2 rounded-md;
  @apply hover:opacity-90 transition-opacity;
}

.btn-secondary {
  @apply bg-secondary text-secondary-foreground px-4 py-2 rounded-md;
  @apply hover:bg-secondary/80;
}

.btn-destructive {
  @apply bg-destructive text-destructive-foreground px-4 py-2 rounded-md;
  @apply hover:bg-destructive/90;
}

/* Card styles */
.card {
  @apply bg-card text-card-foreground rounded-lg border border-border;
  @apply shadow-sm p-6;
}

/* Input styles */
.input {
  @apply bg-background border border-input rounded-md px-3 py-2;
  @apply text-foreground placeholder:text-muted-foreground;
  @apply focus:outline-none focus:ring-2 focus:ring-ring;
}

/* Muted text */
.text-muted {
  @apply text-muted-foreground;
}

/* Alert variants */
.alert-success {
  @apply bg-success/10 text-success border border-success/20;
  @apply rounded-md p-4;
}

.alert-warning {
  @apply bg-warning/10 text-warning border border-warning/20;
  @apply rounded-md p-4;
}

.alert-destructive {
  @apply bg-destructive/10 text-destructive border border-destructive/20;
  @apply rounded-md p-4;
}
"""


# =============================================================================
# Tailwind v4 CSS-First Theme Generation
# =============================================================================


def _tokens_to_theme_vars(tokens: ThemeTokens, prefix: str = "") -> list[tuple[str, str]]:
    """Convert ThemeTokens fields to (name, css_value) pairs for @theme.

    Returns (css-variable-name, css-value) tuples ready to be written as:
        --color-primary: hsl(28 80% 53%);
    """
    lines = []

    # Color mappings: field_name -> (theme-var-name, css-value)
    color_mappings = [
        ("background", f"hsl({tokens.background.to_hsl()})"),
        ("foreground", f"hsl({tokens.foreground.to_hsl()})"),
        ("card", f"hsl({tokens.card.to_hsl()})"),
        ("card-foreground", f"hsl({tokens.card_foreground.to_hsl()})"),
        ("popover", f"hsl({tokens.popover.to_hsl()})"),
        ("popover-foreground", f"hsl({tokens.popover_foreground.to_hsl()})"),
        ("primary", f"hsl({tokens.primary.to_hsl()})"),
        ("primary-foreground", f"hsl({tokens.primary_foreground.to_hsl()})"),
        ("secondary", f"hsl({tokens.secondary.to_hsl()})"),
        ("secondary-foreground", f"hsl({tokens.secondary_foreground.to_hsl()})"),
        ("muted", f"hsl({tokens.muted.to_hsl()})"),
        ("muted-foreground", f"hsl({tokens.muted_foreground.to_hsl()})"),
        ("accent", f"hsl({tokens.accent.to_hsl()})"),
        ("accent-foreground", f"hsl({tokens.accent_foreground.to_hsl()})"),
        ("destructive", f"hsl({tokens.destructive.to_hsl()})"),
        ("destructive-foreground", f"hsl({tokens.destructive_foreground.to_hsl()})"),
        ("success", f"hsl({tokens.success.to_hsl()})"),
        ("success-foreground", f"hsl({tokens.success_foreground.to_hsl()})"),
        ("warning", f"hsl({tokens.warning.to_hsl()})"),
        ("warning-foreground", f"hsl({tokens.warning_foreground.to_hsl()})"),
        ("info", f"hsl({tokens.info.to_hsl()})"),
        ("info-foreground", f"hsl({tokens.info_foreground.to_hsl()})"),
        ("link", f"hsl({tokens.link.to_hsl()})"),
        ("link-hover", f"hsl({tokens.link_hover.to_hsl()})"),
        ("code", f"hsl({tokens.code.to_hsl()})"),
        ("code-foreground", f"hsl({tokens.code_foreground.to_hsl()})"),
        ("selection", f"hsl({tokens.selection.to_hsl()})"),
        ("selection-foreground", f"hsl({tokens.selection_foreground.to_hsl()})"),
        ("border", f"hsl({tokens.border.to_hsl()})"),
        ("input", f"hsl({tokens.input.to_hsl()})"),
        ("ring", f"hsl({tokens.ring.to_hsl()})"),
        ("surface_1", f"hsl({tokens.surface_1.to_hsl()})"),
        ("surface_2", f"hsl({tokens.surface_2.to_hsl()})"),
        ("surface_3", f"hsl({tokens.surface_3.to_hsl()})"),
    ]

    for name, value in color_mappings:
        var_name = f"--color{'-' + prefix if prefix else '-'}{name}"
        lines.append((var_name, value))

    return lines


def _extra_vars_to_theme_vars(extra_vars: dict | None) -> list[tuple[str, str]]:
    """Convert extra_css_vars dict to (name, css_value) pairs for @theme.

    Filters to only vars that look like Tailwind theme values (colors, fonts,
    spacing, shadows, animations, etc.).
    """
    if not extra_vars:
        return []

    lines = []
    for name, value in extra_vars.items():
        # Convert Python dict key style (color-brand-rust) to CSS (--color-brand-rust)
        if name.startswith(
            ("color-", "font-", "spacing-", "radius-", "shadow-", "animate-", "background-")
        ):
            css_name = f"--{name}"
            lines.append((css_name, value))
        elif name.startswith("animation-"):
            css_name = f"--{name}"
            lines.append((css_name, value))

    return lines


def generate_tailwindv4_theme_block(preset_name: str = "default") -> str:
    """
    Generate a Tailwind v4 @theme { } block for CSS-first configuration.

    This generates CSS custom properties for all preset tokens that Tailwind v4
    will automatically convert to utilities:
        --color-primary: hsl(28 80% 53%)   →  bg-primary, text-primary, ...
        --color-brand-rust: #E57324          →  bg-brand-rust, text-brand-rust, ...
        --radius-lg: 0.5rem                  →  rounded-lg, rounded-[0.5rem], ...
        --font-sans: 'Inter', sans-serif     →  font-sans, ...

    Usage:
        /* In your main CSS file (CSS-first Tailwind v4): */
        @import "tailwindcss";
        @import "djust-theming/tailwindv4-theme.css";

        /* Then use Tailwind utilities directly: */
        <div class="bg-brand-rust text-white rounded-lg">Hello</div>

    Dark mode works automatically with djust-theming's dark-first / light-first
    approach — html[data-theme="light"] and html[data-theme="dark"] selectors
    from the CSS generator override the CSS variables, so Tailwind utilities
    automatically reflect the active theme without needing @media dark-mode.

    Args:
        preset_name: Name of the preset (e.g., "default", "djust", "nord")

    Returns:
        CSS @theme block as a string, ready to include in a Tailwind v4 CSS file.
    """
    preset = get_preset(preset_name)
    if not preset:
        raise ValueError(f"Unknown preset: {preset_name}")

    parts = [
        "/* djust-theming - Tailwind v4 @theme block */",
        "/* Generated from preset:",
        f"   {preset_name} ({preset.display_name})",
        "   DO NOT EDIT - regenerate with generate_tailwindv4_theme_block()",
        "*/",
        "",
    ]

    parts.append("@theme {")

    # ---- Brand colors from extra_css_vars ----
    extra_vars = _extra_vars_to_theme_vars(preset.extra_css_vars)
    if extra_vars:
        parts.append("  /* Brand colors */")
        for name, value in extra_vars:
            parts.append(f"  {name}: {value};")
        parts.append("")

    # ---- Semantic colors — use the preset's default (root-level) tokens ----
    # For dark-first: preset.dark tokens are the :root defaults
    # For light-first: preset.light tokens are the :root defaults
    # This ensures @theme vars match what djust-theming's CSS generator puts in :root
    default_tokens = preset.dark if preset.default_mode == "dark" else preset.light
    mode_label = "dark" if preset.default_mode == "dark" else "light"
    theme_vars = _tokens_to_theme_vars(default_tokens)
    if theme_vars:
        parts.append(f"  /* Semantic colors ({mode_label} — preset default) */")
        for name, value in theme_vars:
            parts.append(f"  {name}: {value};")
        parts.append("")

    # ---- Border radius ----
    parts.append("  /* Border radius */")
    parts.append(f"  --radius-sm: calc({preset.radius}rem - 2px);")
    parts.append(f"  --radius-md: {preset.radius}rem;")
    parts.append(f"  --radius-lg: calc({preset.radius}rem + 2px);")
    parts.append(f"  --radius-xl: calc({preset.radius}rem + 4px);")
    parts.append("")

    # ---- Shadows ----
    # These are approximate mappings - adjust based on theme design system
    parts.append("  /* Shadows */")
    parts.append("  --shadow-sm: 0 1px 2px 0 rgb(0 0 0 / 0.05);")
    parts.append("  --shadow-md: 0 4px 6px -1px rgb(0 0 0 / 0.1);")
    parts.append("  --shadow-lg: 0 10px 15px -3px rgb(0 0 0 / 0.1);")
    parts.append("  --shadow-xl: 0 20px 25px -5px rgb(0 0 0 / 0.1);")
    parts.append("")

    # ---- Font families ----
    parts.append("  /* Font families */")
    parts.append(
        "  --font-sans: 'Inter', -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;"
    )
    parts.append("  --font-mono: 'JetBrains Mono', 'Fira Code', ui-monospace, monospace;")
    parts.append("")

    # ---- Font sizes ----
    parts.append("  /* Font sizes */")
    parts.append("  --text-xs: 0.75rem;")
    parts.append("  --text-sm: 0.875rem;")
    parts.append("  --text-base: 1rem;")
    parts.append("  --text-lg: 1.125rem;")
    parts.append("  --text-xl: 1.25rem;")
    parts.append("  --text-2xl: 1.5rem;")
    parts.append("  --text-3xl: 1.875rem;")
    parts.append("  --text-4xl: 2.25rem;")
    parts.append("  --text-5xl: 3rem;")
    parts.append("")

    # ---- Spacing ----
    parts.append("  /* Spacing */")
    spacing_values = {
        "1": "0.25rem",
        "2": "0.5rem",
        "3": "0.75rem",
        "4": "1rem",
        "5": "1.25rem",
        "6": "1.5rem",
        "8": "2rem",
        "10": "2.5rem",
        "12": "3rem",
        "16": "4rem",
        "20": "5rem",
        "24": "6rem",
    }
    for key, val in spacing_values.items():
        parts.append(f"  --spacing-{key}: {val};")
    parts.append("")

    # ---- Animations ----
    if preset.extra_css_vars:
        anim_vars = [(k, v) for k, v in preset.extra_css_vars.items() if k.startswith("animation-")]
        if anim_vars:
            parts.append("  /* Animations */")
            for name, value in anim_vars:
                parts.append(f"  --{name}: {value};")
            parts.append("")

    parts.append("}")

    return "\n".join(parts)


@lru_cache(maxsize=32)
def generate_tailwindv4_theme_block_cached(preset_name: str = "default") -> str:
    """Cached version of generate_tailwindv4_theme_block."""
    return generate_tailwindv4_theme_block(preset_name)
