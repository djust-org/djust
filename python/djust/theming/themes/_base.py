"""
Convenience imports for theme authors.

Usage in a theme file:
    from ._base import ColorScale, ThemeTokens, ThemePreset, ...

Pulls types from ``..._types`` and shared style instances from
``..._constants``, both of which are dependency-free. Previously this
module imported directly from ``..presets`` and ``..theme_packs``, both
of which import this module's siblings under ``themes/*`` — a cycle that
CodeQL's ``py/unsafe-cyclic-import`` rule flagged across every theme
file. Routing through the dependency-free modules breaks the cycle
without changing the public surface.
"""

from .._constants import (
    ANIM_BOUNCY,
    ANIM_GENTLE,
    ANIM_INSTANT,
    ANIM_SMOOTH,
    ANIM_SNAPPY,
    ICON_ELEGANT,
    ICON_FILLED,
    ICON_IOS,
    ICON_MINIMAL,
    ICON_ORGANIC,
    ICON_OUTLINED,
    ICON_RETRO,
    ICON_ROUNDED,
    ICON_SHARP,
    ICON_THIN,
    ILLUST_3D,
    ILLUST_FLAT,
    ILLUST_HAND_DRAWN,
    ILLUST_LINE,
    ILLUST_RETRO,
    INTERACT_BOLD,
    INTERACT_MINIMAL,
    INTERACT_PLAYFUL,
    INTERACT_SUBTLE,
    PATTERN_DOTS,
    PATTERN_GLASS,
    PATTERN_GRADIENT,
    PATTERN_GRID,
    PATTERN_MINIMAL,
    PATTERN_NOISE,
)
from .._types import (
    AnimationStyle,
    ColorScale,
    DesignSystem,
    IconStyle,
    IllustrationStyle,
    InteractionStyle,
    LayoutStyle,
    PatternStyle,
    SurfaceStyle,
    SurfaceTreatment,
    ThemePack,
    ThemePreset,
    ThemeTokens,
    TypographyStyle,
)

# Pack-level INTERACT_MINIMAL/PLAYFUL aliases. These preserve backward compat
# for any theme file that reached for the "pack" names — they point to the
# same (pack-level) instances that already live in _constants.py.
INTERACT_MINIMAL_PACK = INTERACT_MINIMAL
INTERACT_PLAYFUL_PACK = INTERACT_PLAYFUL

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
