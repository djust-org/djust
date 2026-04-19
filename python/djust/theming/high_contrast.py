"""
High contrast theme variants for enhanced accessibility.

Provides WCAG AAA-compliant high contrast versions of all color presets,
designed for users with visual impairments or low vision conditions.
"""

from typing import Dict

from .presets import ColorScale, ThemeTokens, ThemePreset


class HighContrastPresets:
    """High contrast theme presets for accessibility."""

    @staticmethod
    def create_high_contrast_tokens(base_preset: ThemePreset, mode: str = "light") -> ThemeTokens:
        """Create high contrast version of theme tokens."""

        base_tokens = getattr(base_preset, mode)

        if mode == "light":
            # High contrast light mode - very dark text on very light backgrounds
            return ThemeTokens(
                background=ColorScale(0, 0, 100),  # Pure white
                foreground=ColorScale(0, 0, 0),  # Pure black
                card=ColorScale(0, 0, 98),  # Near white
                card_foreground=ColorScale(0, 0, 0),
                popover=ColorScale(0, 0, 100),
                popover_foreground=ColorScale(0, 0, 0),
                # High contrast primary - keep base hue but max saturation/contrast
                primary=ColorScale(base_tokens.primary.h, 100, 25),  # Very dark, saturated
                primary_foreground=ColorScale(0, 0, 100),  # White text
                # High contrast secondary
                secondary=ColorScale(0, 0, 10),  # Very dark gray
                secondary_foreground=ColorScale(0, 0, 100),
                # Muted with higher contrast
                muted=ColorScale(0, 0, 90),  # Light gray background
                muted_foreground=ColorScale(0, 0, 15),  # Very dark text
                # Accent with high contrast
                accent=ColorScale(base_tokens.accent.h, 100, 20),
                accent_foreground=ColorScale(0, 0, 100),
                # Status colors with maximum contrast
                destructive=ColorScale(0, 100, 30),  # Dark red
                destructive_foreground=ColorScale(0, 0, 100),
                success=ColorScale(120, 100, 25),  # Dark green
                success_foreground=ColorScale(0, 0, 100),
                warning=ColorScale(45, 100, 30),  # Dark amber
                warning_foreground=ColorScale(0, 0, 0),  # Black for better contrast
                info=ColorScale(199, 100, 25),  # Dark blue
                info_foreground=ColorScale(0, 0, 100),
                link=ColorScale(base_tokens.primary.h, 100, 25),
                link_hover=ColorScale(base_tokens.primary.h, 100, 15),
                code=ColorScale(0, 0, 90),
                code_foreground=ColorScale(0, 0, 0),
                selection=ColorScale(base_tokens.primary.h, 100, 80),
                selection_foreground=ColorScale(0, 0, 0),
                brand=ColorScale(base_tokens.primary.h, 100, 25),
                brand_foreground=ColorScale(0, 0, 100),
                # UI elements with high contrast
                border=ColorScale(0, 0, 20),  # Very dark borders
                input=ColorScale(0, 0, 95),  # Light input background
                ring=ColorScale(base_tokens.primary.h, 100, 30),  # High contrast focus
                surface_1=ColorScale(0, 0, 99),
                surface_2=ColorScale(0, 0, 97),
                surface_3=ColorScale(0, 0, 95),
            )
        else:
            # High contrast dark mode - very light text on very dark backgrounds
            return ThemeTokens(
                background=ColorScale(0, 0, 0),  # Pure black
                foreground=ColorScale(0, 0, 100),  # Pure white
                card=ColorScale(0, 0, 3),  # Near black
                card_foreground=ColorScale(0, 0, 100),
                popover=ColorScale(0, 0, 0),
                popover_foreground=ColorScale(0, 0, 100),
                # High contrast primary
                primary=ColorScale(base_tokens.primary.h, 100, 75),  # Bright, saturated
                primary_foreground=ColorScale(0, 0, 0),  # Black text
                # High contrast secondary
                secondary=ColorScale(0, 0, 90),  # Very light gray
                secondary_foreground=ColorScale(0, 0, 0),
                # Muted with higher contrast
                muted=ColorScale(0, 0, 10),  # Dark background
                muted_foreground=ColorScale(0, 0, 85),  # Light text
                # Accent with high contrast
                accent=ColorScale(base_tokens.accent.h, 100, 80),
                accent_foreground=ColorScale(0, 0, 0),
                # Status colors with maximum contrast
                destructive=ColorScale(0, 100, 70),  # Bright red
                destructive_foreground=ColorScale(0, 0, 0),
                success=ColorScale(120, 100, 75),  # Bright green
                success_foreground=ColorScale(0, 0, 0),
                warning=ColorScale(45, 100, 70),  # Bright amber
                warning_foreground=ColorScale(0, 0, 0),
                info=ColorScale(199, 100, 75),  # Bright blue
                info_foreground=ColorScale(0, 0, 0),
                link=ColorScale(base_tokens.primary.h, 100, 75),
                link_hover=ColorScale(base_tokens.primary.h, 100, 85),
                code=ColorScale(0, 0, 10),
                code_foreground=ColorScale(0, 0, 100),
                selection=ColorScale(base_tokens.primary.h, 100, 30),
                selection_foreground=ColorScale(0, 0, 100),
                brand=ColorScale(base_tokens.primary.h, 100, 75),
                brand_foreground=ColorScale(0, 0, 0),
                # UI elements with high contrast
                border=ColorScale(0, 0, 80),  # Very light borders
                input=ColorScale(0, 0, 5),  # Dark input background
                ring=ColorScale(base_tokens.primary.h, 100, 70),  # High contrast focus
                surface_1=ColorScale(0, 0, 3),
                surface_2=ColorScale(0, 0, 6),
                surface_3=ColorScale(0, 0, 10),
            )


def generate_high_contrast_presets() -> Dict[str, ThemePreset]:
    """Generate high contrast versions of all existing presets."""

    from .presets import THEME_PRESETS

    high_contrast_presets = {}

    for preset_name, base_preset in THEME_PRESETS.items():
        hc_name = f"{preset_name}_hc"

        high_contrast_presets[hc_name] = ThemePreset(
            name=hc_name,
            display_name=f"{base_preset.display_name} (High Contrast)",
            description=f"High contrast version of {base_preset.display_name} for enhanced accessibility",
            light=HighContrastPresets.create_high_contrast_tokens(base_preset, "light"),
            dark=HighContrastPresets.create_high_contrast_tokens(base_preset, "dark"),
            radius=base_preset.radius,
        )

    return high_contrast_presets


def get_high_contrast_preset(name: str) -> ThemePreset:
    """Get a specific high contrast preset."""
    presets = generate_high_contrast_presets()

    if name in presets:
        return presets[name]

    # Try adding _hc suffix if not found
    hc_name = f"{name}_hc"
    if hc_name in presets:
        return presets[hc_name]

    raise ValueError(f"High contrast preset '{name}' not found")


# Pre-defined high contrast presets for common use cases
HIGH_CONTRAST_PRESETS = {
    # Monochrome high contrast (maximum contrast)
    "monochrome_hc": ThemePreset(
        name="monochrome_hc",
        display_name="Monochrome High Contrast",
        description="Maximum contrast black and white theme for severe visual impairments",
        light=ThemeTokens(
            background=ColorScale(0, 0, 100),  # Pure white
            foreground=ColorScale(0, 0, 0),  # Pure black
            card=ColorScale(0, 0, 100),
            card_foreground=ColorScale(0, 0, 0),
            popover=ColorScale(0, 0, 100),
            popover_foreground=ColorScale(0, 0, 0),
            primary=ColorScale(0, 0, 0),  # Black
            primary_foreground=ColorScale(0, 0, 100),  # White
            secondary=ColorScale(0, 0, 20),  # Very dark gray
            secondary_foreground=ColorScale(0, 0, 100),
            muted=ColorScale(0, 0, 95),  # Light gray
            muted_foreground=ColorScale(0, 0, 0),
            accent=ColorScale(0, 0, 0),
            accent_foreground=ColorScale(0, 0, 100),
            destructive=ColorScale(0, 0, 0),  # Black (no red for colorblind)
            destructive_foreground=ColorScale(0, 0, 100),
            success=ColorScale(0, 0, 0),
            success_foreground=ColorScale(0, 0, 100),
            warning=ColorScale(0, 0, 0),
            warning_foreground=ColorScale(0, 0, 100),
            info=ColorScale(0, 0, 0),
            info_foreground=ColorScale(0, 0, 100),
            link=ColorScale(0, 0, 0),
            link_hover=ColorScale(0, 0, 20),
            code=ColorScale(0, 0, 95),
            code_foreground=ColorScale(0, 0, 0),
            selection=ColorScale(0, 0, 0),
            selection_foreground=ColorScale(0, 0, 100),
            brand=ColorScale(0, 0, 0),
            brand_foreground=ColorScale(0, 0, 100),
            border=ColorScale(0, 0, 0),  # Black borders
            input=ColorScale(0, 0, 100),  # White inputs
            ring=ColorScale(0, 0, 0),  # Black focus
            surface_1=ColorScale(0, 0, 99),
            surface_2=ColorScale(0, 0, 97),
            surface_3=ColorScale(0, 0, 95),
        ),
        dark=ThemeTokens(
            background=ColorScale(0, 0, 0),  # Pure black
            foreground=ColorScale(0, 0, 100),  # Pure white
            card=ColorScale(0, 0, 0),
            card_foreground=ColorScale(0, 0, 100),
            popover=ColorScale(0, 0, 0),
            popover_foreground=ColorScale(0, 0, 100),
            primary=ColorScale(0, 0, 100),  # White
            primary_foreground=ColorScale(0, 0, 0),  # Black
            secondary=ColorScale(0, 0, 80),  # Very light gray
            secondary_foreground=ColorScale(0, 0, 0),
            muted=ColorScale(0, 0, 5),  # Very dark gray
            muted_foreground=ColorScale(0, 0, 100),
            accent=ColorScale(0, 0, 100),
            accent_foreground=ColorScale(0, 0, 0),
            destructive=ColorScale(0, 0, 100),  # White (no red for colorblind)
            destructive_foreground=ColorScale(0, 0, 0),
            success=ColorScale(0, 0, 100),
            success_foreground=ColorScale(0, 0, 0),
            warning=ColorScale(0, 0, 100),
            warning_foreground=ColorScale(0, 0, 0),
            info=ColorScale(0, 0, 100),
            info_foreground=ColorScale(0, 0, 0),
            link=ColorScale(0, 0, 100),
            link_hover=ColorScale(0, 0, 80),
            code=ColorScale(0, 0, 5),
            code_foreground=ColorScale(0, 0, 100),
            selection=ColorScale(0, 0, 100),
            selection_foreground=ColorScale(0, 0, 0),
            brand=ColorScale(0, 0, 100),
            brand_foreground=ColorScale(0, 0, 0),
            border=ColorScale(0, 0, 100),  # White borders
            input=ColorScale(0, 0, 0),  # Black inputs
            ring=ColorScale(0, 0, 100),  # White focus
            surface_1=ColorScale(0, 0, 3),
            surface_2=ColorScale(0, 0, 6),
            surface_3=ColorScale(0, 0, 10),
        ),
        radius=8,
    ),
    # Yellow on black (classic high contrast)
    "yellow_black_hc": ThemePreset(
        name="yellow_black_hc",
        display_name="Yellow on Black",
        description="Classic high contrast theme with yellow text on black background",
        light=ThemeTokens(
            background=ColorScale(0, 0, 0),  # Black background in light mode
            foreground=ColorScale(60, 100, 50),  # Yellow text
            card=ColorScale(0, 0, 5),
            card_foreground=ColorScale(60, 100, 50),
            popover=ColorScale(0, 0, 0),
            popover_foreground=ColorScale(60, 100, 50),
            primary=ColorScale(60, 100, 50),  # Yellow
            primary_foreground=ColorScale(0, 0, 0),  # Black
            secondary=ColorScale(60, 50, 30),  # Darker yellow
            secondary_foreground=ColorScale(0, 0, 0),
            muted=ColorScale(0, 0, 10),
            muted_foreground=ColorScale(60, 80, 60),
            accent=ColorScale(60, 100, 50),
            accent_foreground=ColorScale(0, 0, 0),
            destructive=ColorScale(60, 100, 50),  # Yellow for errors too
            destructive_foreground=ColorScale(0, 0, 0),
            success=ColorScale(60, 100, 50),
            success_foreground=ColorScale(0, 0, 0),
            warning=ColorScale(60, 100, 50),
            warning_foreground=ColorScale(0, 0, 0),
            info=ColorScale(60, 100, 50),
            info_foreground=ColorScale(0, 0, 0),
            link=ColorScale(60, 100, 50),
            link_hover=ColorScale(60, 100, 60),
            code=ColorScale(0, 0, 5),
            code_foreground=ColorScale(60, 100, 50),
            selection=ColorScale(60, 100, 50),
            selection_foreground=ColorScale(0, 0, 0),
            brand=ColorScale(60, 100, 50),
            brand_foreground=ColorScale(0, 0, 0),
            border=ColorScale(60, 100, 50),
            input=ColorScale(0, 0, 5),
            ring=ColorScale(60, 100, 50),
            surface_1=ColorScale(0, 0, 3),
            surface_2=ColorScale(0, 0, 6),
            surface_3=ColorScale(0, 0, 10),
        ),
        dark=ThemeTokens(
            background=ColorScale(0, 0, 0),  # Still black
            foreground=ColorScale(60, 100, 50),  # Still yellow
            card=ColorScale(0, 0, 5),
            card_foreground=ColorScale(60, 100, 50),
            popover=ColorScale(0, 0, 0),
            popover_foreground=ColorScale(60, 100, 50),
            primary=ColorScale(60, 100, 50),
            primary_foreground=ColorScale(0, 0, 0),
            secondary=ColorScale(60, 50, 30),
            secondary_foreground=ColorScale(0, 0, 0),
            muted=ColorScale(0, 0, 10),
            muted_foreground=ColorScale(60, 80, 60),
            accent=ColorScale(60, 100, 50),
            accent_foreground=ColorScale(0, 0, 0),
            destructive=ColorScale(60, 100, 50),
            destructive_foreground=ColorScale(0, 0, 0),
            success=ColorScale(60, 100, 50),
            success_foreground=ColorScale(0, 0, 0),
            warning=ColorScale(60, 100, 50),
            warning_foreground=ColorScale(0, 0, 0),
            info=ColorScale(60, 100, 50),
            info_foreground=ColorScale(0, 0, 0),
            link=ColorScale(60, 100, 50),
            link_hover=ColorScale(60, 100, 60),
            code=ColorScale(0, 0, 5),
            code_foreground=ColorScale(60, 100, 50),
            selection=ColorScale(60, 100, 50),
            selection_foreground=ColorScale(0, 0, 0),
            brand=ColorScale(60, 100, 50),
            brand_foreground=ColorScale(0, 0, 0),
            border=ColorScale(60, 100, 50),
            input=ColorScale(0, 0, 5),
            ring=ColorScale(60, 100, 50),
            surface_1=ColorScale(0, 0, 3),
            surface_2=ColorScale(0, 0, 6),
            surface_3=ColorScale(0, 0, 10),
        ),
        radius=0,  # Sharp corners for high contrast
    ),
}


def get_all_high_contrast_presets() -> Dict[str, ThemePreset]:
    """Get all high contrast presets (generated + pre-defined)."""
    presets = generate_high_contrast_presets()
    presets.update(HIGH_CONTRAST_PRESETS)
    return presets


if __name__ == "__main__":
    import logging

    logging.basicConfig(level=logging.INFO, format="%(message)s")
    _logger = logging.getLogger(__name__)

    # Test high contrast generation
    hc_presets = get_all_high_contrast_presets()

    _logger.info("Generated %d high contrast presets:", len(hc_presets))
    for name, preset in sorted(hc_presets.items()):
        _logger.info("  %s: %s", name, preset.display_name)

    # Test accessibility of high contrast themes
    from .accessibility import validate_accessibility

    _logger.info("\nTesting accessibility of high contrast themes:")
    test_themes = ["default_hc", "monochrome_hc", "yellow_black_hc"]

    for theme_name in test_themes:
        if theme_name in hc_presets:
            try:
                report = validate_accessibility("minimal", theme_name)
                _logger.info(
                    "  %s: %.1f%% (issues: %d)",
                    theme_name,
                    report.overall_score,
                    len(report.issues),
                )
            except Exception as e:
                _logger.info("  %s: Error - %s", theme_name, e)
