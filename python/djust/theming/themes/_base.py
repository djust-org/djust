"""
Convenience imports for theme authors.

Usage in a theme file:
    from ._base import ColorScale, ThemeTokens, ThemePreset, ...
"""

from ..presets import ColorScale, ThemeTokens, ThemePreset, SurfaceTreatment
from ..theme_packs import (
    TypographyStyle,
    LayoutStyle,
    SurfaceStyle,
    IconStyle,
    AnimationStyle,
    InteractionStyle,
    DesignSystem,
    ThemePack,
    PatternStyle,
    IllustrationStyle,
    # Shared pattern presets
    PATTERN_GRID,
    PATTERN_DOTS,
    PATTERN_GRADIENT,
    PATTERN_NOISE,
    PATTERN_MINIMAL,
    PATTERN_GLASS,
    # Shared illustration presets
    ILLUST_LINE,
    ILLUST_FLAT,
    ILLUST_HAND_DRAWN,
    ILLUST_3D,
    ILLUST_RETRO,
    # Shared icon presets (pack-level)
    ICON_OUTLINED,
    ICON_FILLED,
    ICON_ROUNDED,
    ICON_SHARP,
    ICON_THIN,
    # Design-system-specific icon presets used by packs
    ICON_RETRO,
    ICON_ELEGANT,
    ICON_ORGANIC,
    ICON_IOS,
    ICON_MINIMAL,
    # Shared animation presets (pack-level)
    ANIM_SMOOTH,
    ANIM_SNAPPY,
    ANIM_BOUNCY,
    ANIM_INSTANT,
    ANIM_GENTLE,
    # Shared interaction presets (pack-level)
    INTERACT_SUBTLE,
    INTERACT_BOLD,
)

# Re-import the pack-level INTERACT_MINIMAL and INTERACT_PLAYFUL with distinct names
# to avoid conflict with design-system-level ones
from ..theme_packs import INTERACT_MINIMAL as INTERACT_MINIMAL_PACK
from ..theme_packs import INTERACT_PLAYFUL as INTERACT_PLAYFUL_PACK

__all__ = [
    "ColorScale",
    "ThemeTokens",
    "ThemePreset",
    "SurfaceTreatment",
    "TypographyStyle",
    "LayoutStyle",
    "SurfaceStyle",
    "IconStyle",
    "AnimationStyle",
    "InteractionStyle",
    "DesignSystem",
    "ThemePack",
    "PatternStyle",
    "IllustrationStyle",
    "PATTERN_GRID",
    "PATTERN_DOTS",
    "PATTERN_GRADIENT",
    "PATTERN_NOISE",
    "PATTERN_MINIMAL",
    "PATTERN_GLASS",
    "ILLUST_LINE",
    "ILLUST_FLAT",
    "ILLUST_HAND_DRAWN",
    "ILLUST_3D",
    "ILLUST_RETRO",
    "ICON_OUTLINED",
    "ICON_FILLED",
    "ICON_ROUNDED",
    "ICON_SHARP",
    "ICON_THIN",
    "ICON_RETRO",
    "ICON_ELEGANT",
    "ICON_ORGANIC",
    "ICON_IOS",
    "ICON_MINIMAL",
    "ANIM_SMOOTH",
    "ANIM_SNAPPY",
    "ANIM_BOUNCY",
    "ANIM_INSTANT",
    "ANIM_GENTLE",
    "INTERACT_SUBTLE",
    "INTERACT_BOLD",
    "INTERACT_MINIMAL_PACK",
    "INTERACT_PLAYFUL_PACK",
]
