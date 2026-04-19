"""
Per-theme modules — one file per built-in theme.

Each theme file (e.g., bauhaus.py, dracula.py) contains all pieces for
that theme: color preset (PRESET), design system (DESIGN_SYSTEM), and
theme pack (PACK).

This package also re-exports the deprecated Theme/THEMES API from
_legacy.py for backward compatibility.
"""

# Re-export legacy themes.py API so that existing code like
# `from djust.theming.themes import Theme, get_theme, THEMES`
# continues to work now that themes/ is a package.
from ._legacy import (  # noqa: F401
    Theme,
    Typography,
    Spacing,
    BorderRadius,
    Shadows,
    Animations,
    ComponentStyles,
    THEMES,
    get_theme,
    list_themes,
)

# Import all per-theme modules for convenient access
from . import (  # noqa: F401
    default,
    shadcn,
    blue,
    green,
    purple,
    orange,
    rose,
    natural20,
    catppuccin,
    rose_pine,
    tokyo_night,
    nord,
    synthwave,
    cyberpunk,
    outrun,
    forest,
    amber,
    slate,
    nebula,
    djust,
    dracula,
    gruvbox,
    solarized,
    high_contrast,
    mono,
    ember,
    aurora,
    ink,
    solarpunk,
    bauhaus,
    cyberdeck,
    paper,
    neon_noir,
    ocean_deep,
    stripe,
    linear,
    notion,
    vercel,
    github,
    art_deco,
    handcraft,
    terminal,
    magazine,
    swiss,
    candy,
    retro_computing,
    medical,
    legal,
    midnight,
    sunrise,
    forest_floor,
    dashboard,
    one_dark,
    monokai,
    ayu,
    kanagawa,
    everforest,
    poimandres,
    tailwind,
    supabase,
    raycast,
    adaptive,
)
