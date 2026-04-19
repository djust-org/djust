"""
shadcn/ui theme format compatibility.

Provides utilities to:
- Import shadcn theme JSON files
- Export djust-theming presets to shadcn format
- Parse themes from themes.shadcn.com
"""

from typing import Dict, Any
import json
import re
from .presets import ThemePreset, ThemeTokens, ColorScale, THEME_PRESETS


def parse_shadcn_theme(theme_json: Dict[str, Any]) -> ThemePreset:
    """
    Parse a shadcn/ui theme JSON and convert to djust-theming ThemePreset.

    Args:
        theme_json: shadcn theme JSON (from themes.shadcn.com or custom)

    Returns:
        ThemePreset instance

    Example shadcn theme JSON structure:
    {
        "name": "custom-theme",
        "label": "Custom Theme",
        "activeColor": {
            "light": "221.2 83.2% 53.3%",
            "dark": "217.2 91.2% 59.8%"
        },
        "cssVars": {
            "light": {
                "background": "0 0% 100%",
                "foreground": "222.2 47.4% 11.2%",
                "primary": "221.2 83.2% 53.3%",
                ...
            },
            "dark": {
                "background": "224 71% 4%",
                "foreground": "213 31% 91%",
                "primary": "217.2 91.2% 59.8%",
                ...
            }
        }
    }
    """
    name = theme_json.get("name", "custom")
    display_name = theme_json.get("label", name.title())

    css_vars = theme_json.get("cssVars", {})
    light_vars = css_vars.get("light", {})
    dark_vars = css_vars.get("dark", {})

    # Parse light mode tokens
    light_tokens = _parse_shadcn_vars(light_vars)

    # Parse dark mode tokens
    dark_tokens = _parse_shadcn_vars(dark_vars)

    # Extract radius from light vars (same for both modes)
    radius_str = light_vars.get("radius", "0.5rem")
    radius = float(re.sub(r"[^\d.]", "", radius_str)) if radius_str else 0.5

    return ThemePreset(
        name=name,
        display_name=display_name,
        light=light_tokens,
        dark=dark_tokens,
        radius=radius,
    )


def _parse_shadcn_vars(vars_dict: Dict[str, str]) -> ThemeTokens:
    """Parse shadcn CSS variables into ThemeTokens."""

    def parse_hsl(hsl_str: str) -> ColorScale:
        """Parse 'H S% L%' string to ColorScale."""
        # Handle both "H S% L%" and "H S L" formats
        hsl_str = hsl_str.strip()
        parts = hsl_str.split()

        if len(parts) != 3:
            # Fallback to neutral gray
            return ColorScale(0, 0, 50)

        try:
            h = float(parts[0])
            s = float(parts[1].rstrip("%"))
            l = float(parts[2].rstrip("%"))
            return ColorScale(int(h), int(s), int(l))
        except (ValueError, IndexError):
            return ColorScale(0, 0, 50)

    # Extract all required tokens (with fallbacks)
    background = parse_hsl(vars_dict.get("background", "0 0% 100%"))
    foreground = parse_hsl(vars_dict.get("foreground", "222.2 47.4% 11.2%"))

    card = parse_hsl(vars_dict.get("card", vars_dict.get("background", "0 0% 100%")))
    card_foreground = parse_hsl(
        vars_dict.get("card-foreground", vars_dict.get("foreground", "222.2 47.4% 11.2%"))
    )

    popover = parse_hsl(vars_dict.get("popover", vars_dict.get("background", "0 0% 100%")))
    popover_foreground = parse_hsl(
        vars_dict.get("popover-foreground", vars_dict.get("foreground", "222.2 47.4% 11.2%"))
    )

    primary = parse_hsl(vars_dict.get("primary", "221.2 83.2% 53.3%"))
    primary_foreground = parse_hsl(vars_dict.get("primary-foreground", "210 40% 98%"))

    secondary = parse_hsl(vars_dict.get("secondary", "210 40% 96.1%"))
    secondary_foreground = parse_hsl(vars_dict.get("secondary-foreground", "222.2 47.4% 11.2%"))

    muted = parse_hsl(vars_dict.get("muted", "210 40% 96.1%"))
    muted_foreground = parse_hsl(vars_dict.get("muted-foreground", "215.4 16.3% 46.9%"))

    accent = parse_hsl(vars_dict.get("accent", "210 40% 96.1%"))
    accent_foreground = parse_hsl(vars_dict.get("accent-foreground", "222.2 47.4% 11.2%"))

    destructive = parse_hsl(vars_dict.get("destructive", "0 84.2% 60.2%"))
    destructive_foreground = parse_hsl(vars_dict.get("destructive-foreground", "210 40% 98%"))

    # Extensions - not in standard shadcn
    success = parse_hsl(vars_dict.get("success", "142 76% 36%"))
    success_foreground = parse_hsl(vars_dict.get("success-foreground", "0 0% 100%"))

    warning = parse_hsl(vars_dict.get("warning", "38 92% 50%"))
    warning_foreground = parse_hsl(vars_dict.get("warning-foreground", "0 0% 100%"))

    info = parse_hsl(vars_dict.get("info", "199 89% 48%"))
    info_foreground = parse_hsl(vars_dict.get("info-foreground", "0 0% 98%"))

    link = parse_hsl(vars_dict.get("link", vars_dict.get("primary", "221.2 83.2% 53.3%")))
    link_hover = parse_hsl(vars_dict.get("link-hover", vars_dict.get("primary", "221.2 83.2% 45%")))

    code = parse_hsl(vars_dict.get("code", "240 5% 94%"))
    code_foreground = parse_hsl(vars_dict.get("code-foreground", "240 10% 20%"))

    selection = parse_hsl(vars_dict.get("selection", "240 100% 80%"))
    selection_foreground = parse_hsl(vars_dict.get("selection-foreground", "240 10% 4%"))

    brand = parse_hsl(vars_dict.get("brand", vars_dict.get("primary", "221.2 83.2% 53.3%")))
    brand_foreground = parse_hsl(
        vars_dict.get("brand-foreground", vars_dict.get("primary-foreground", "210 40% 98%"))
    )

    border = parse_hsl(vars_dict.get("border", "214.3 31.8% 91.4%"))
    input_color = parse_hsl(vars_dict.get("input", "214.3 31.8% 91.4%"))
    ring = parse_hsl(vars_dict.get("ring", "221.2 83.2% 53.3%"))

    # Surface tokens - fall back to background variants
    surface_1 = parse_hsl(vars_dict.get("surface-1", vars_dict.get("background", "0 0% 99%")))
    surface_2 = parse_hsl(vars_dict.get("surface-2", vars_dict.get("background", "0 0% 97%")))
    surface_3 = parse_hsl(vars_dict.get("surface-3", vars_dict.get("background", "0 0% 95%")))

    return ThemeTokens(
        background=background,
        foreground=foreground,
        card=card,
        card_foreground=card_foreground,
        popover=popover,
        popover_foreground=popover_foreground,
        primary=primary,
        primary_foreground=primary_foreground,
        secondary=secondary,
        secondary_foreground=secondary_foreground,
        muted=muted,
        muted_foreground=muted_foreground,
        accent=accent,
        accent_foreground=accent_foreground,
        destructive=destructive,
        destructive_foreground=destructive_foreground,
        success=success,
        success_foreground=success_foreground,
        warning=warning,
        warning_foreground=warning_foreground,
        info=info,
        info_foreground=info_foreground,
        link=link,
        link_hover=link_hover,
        code=code,
        code_foreground=code_foreground,
        selection=selection,
        selection_foreground=selection_foreground,
        brand=brand,
        brand_foreground=brand_foreground,
        border=border,
        input=input_color,
        ring=ring,
        surface_1=surface_1,
        surface_2=surface_2,
        surface_3=surface_3,
    )


def export_to_shadcn_format(preset_name: str = "default") -> Dict[str, Any]:
    """
    Export a djust-theming preset to shadcn/ui theme JSON format.

    Args:
        preset_name: Name of the preset to export

    Returns:
        Dictionary in shadcn theme format

    Example:
        >>> from djust.theming.shadcn import export_to_shadcn_format
        >>> theme = export_to_shadcn_format('blue')
        >>> import json
        >>> with open('blue-theme.json', 'w') as f:
        ...     json.dump(theme, f, indent=2)
    """
    preset = THEME_PRESETS.get(preset_name)
    if not preset:
        raise ValueError(f"Unknown preset: {preset_name}")

    light = preset.light
    dark = preset.dark

    return {
        "name": preset.name,
        "label": preset.display_name,
        "activeColor": {
            "light": light.primary.to_hsl(),
            "dark": dark.primary.to_hsl(),
        },
        "cssVars": {
            "light": {
                "background": light.background.to_hsl(),
                "foreground": light.foreground.to_hsl(),
                "card": light.card.to_hsl(),
                "card-foreground": light.card_foreground.to_hsl(),
                "popover": light.popover.to_hsl(),
                "popover-foreground": light.popover_foreground.to_hsl(),
                "primary": light.primary.to_hsl(),
                "primary-foreground": light.primary_foreground.to_hsl(),
                "secondary": light.secondary.to_hsl(),
                "secondary-foreground": light.secondary_foreground.to_hsl(),
                "muted": light.muted.to_hsl(),
                "muted-foreground": light.muted_foreground.to_hsl(),
                "accent": light.accent.to_hsl(),
                "accent-foreground": light.accent_foreground.to_hsl(),
                "destructive": light.destructive.to_hsl(),
                "destructive-foreground": light.destructive_foreground.to_hsl(),
                "border": light.border.to_hsl(),
                "input": light.input.to_hsl(),
                "ring": light.ring.to_hsl(),
                "radius": f"{preset.radius}rem",
            },
            "dark": {
                "background": dark.background.to_hsl(),
                "foreground": dark.foreground.to_hsl(),
                "card": dark.card.to_hsl(),
                "card-foreground": dark.card_foreground.to_hsl(),
                "popover": dark.popover.to_hsl(),
                "popover-foreground": dark.popover_foreground.to_hsl(),
                "primary": dark.primary.to_hsl(),
                "primary-foreground": dark.primary_foreground.to_hsl(),
                "secondary": dark.secondary.to_hsl(),
                "secondary-foreground": dark.secondary_foreground.to_hsl(),
                "muted": dark.muted.to_hsl(),
                "muted-foreground": dark.muted_foreground.to_hsl(),
                "accent": dark.accent.to_hsl(),
                "accent-foreground": dark.accent_foreground.to_hsl(),
                "destructive": dark.destructive.to_hsl(),
                "destructive-foreground": dark.destructive_foreground.to_hsl(),
                "border": dark.border.to_hsl(),
                "input": dark.input.to_hsl(),
                "ring": dark.ring.to_hsl(),
                "radius": f"{preset.radius}rem",
            },
        },
    }


def import_shadcn_theme_from_file(file_path: str) -> ThemePreset:
    """
    Import a shadcn theme from a JSON file.

    Args:
        file_path: Path to the shadcn theme JSON file

    Returns:
        ThemePreset instance

    Example:
        >>> from djust.theming.shadcn import import_shadcn_theme_from_file
        >>> preset = import_shadcn_theme_from_file('my-theme.json')
        >>> from djust.theming.presets import THEME_PRESETS
        >>> THEME_PRESETS[preset.name] = preset
    """
    with open(file_path, "r") as f:
        theme_json = json.load(f)

    return parse_shadcn_theme(theme_json)


def export_shadcn_theme_to_file(preset_name: str, file_path: str):
    """
    Export a djust-theming preset to a shadcn theme JSON file.

    Args:
        preset_name: Name of the preset to export
        file_path: Path where to save the JSON file

    Example:
        >>> from djust.theming.shadcn import export_shadcn_theme_to_file
        >>> export_shadcn_theme_to_file('blue', 'blue-theme.json')
    """
    theme = export_to_shadcn_format(preset_name)

    with open(file_path, "w") as f:
        json.dump(theme, f, indent=2)
