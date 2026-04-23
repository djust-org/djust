"""
Theme preset definitions using HSL color tokens.

Based on shadcn/ui theming system with CSS custom properties.
Each preset is defined in its own file under themes/.

Note: the dataclass types (``ColorScale``, ``ThemeTokens``, ``SurfaceTreatment``,
``ThemePreset``) now live in ``_types.py`` so that ``themes/_base.py`` can
import them without going back through this module — avoiding the cyclic
import that CodeQL's ``py/unsafe-cyclic-import`` flagged across ~55 theme
files. They are re-exported from this module for backward compatibility so
existing consumers like ``from djust.theming.presets import ColorScale``
keep working unchanged.
"""

from ._types import ColorScale, SurfaceTreatment, ThemePreset, ThemeTokens

# =============================================================================
# Theme Imports — each theme is defined in its own file under themes/
# =============================================================================

from .themes.default import PRESET as DEFAULT_THEME  # noqa: E402
from .themes.shadcn import PRESET as SHADCN_THEME  # noqa: E402
from .themes.blue import PRESET as BLUE_THEME  # noqa: E402
from .themes.green import PRESET as GREEN_THEME  # noqa: E402
from .themes.purple import PRESET as PURPLE_THEME  # noqa: E402
from .themes.orange import PRESET as ORANGE_THEME  # noqa: E402
from .themes.rose import PRESET as ROSE_THEME  # noqa: E402
from .themes.natural20 import PRESET as NATURAL20_THEME  # noqa: E402
from .themes.catppuccin import PRESET as CATPPUCCIN_THEME  # noqa: E402
from .themes.rose_pine import PRESET as ROSE_PINE_THEME  # noqa: E402
from .themes.tokyo_night import PRESET as TOKYO_NIGHT_THEME  # noqa: E402
from .themes.nord import PRESET as NORD_THEME  # noqa: E402
from .themes.synthwave import PRESET as SYNTHWAVE_THEME  # noqa: E402
from .themes.cyberpunk import PRESET as CYBERPUNK_THEME  # noqa: E402
from .themes.outrun import PRESET as OUTRUN_THEME  # noqa: E402
from .themes.forest import PRESET as FOREST_THEME  # noqa: E402
from .themes.amber import PRESET as AMBER_THEME  # noqa: E402
from .themes.slate import PRESET as SLATE_THEME  # noqa: E402
from .themes.nebula import PRESET as NEBULA_THEME  # noqa: E402
from .themes.djust import PRESET as DJUST_THEME  # noqa: E402
from .themes.dracula import PRESET as DRACULA_THEME  # noqa: E402
from .themes.gruvbox import PRESET as GRUVBOX_THEME  # noqa: E402
from .themes.solarized import PRESET as SOLARIZED_THEME  # noqa: E402
from .themes.high_contrast import PRESET as HIGH_CONTRAST_THEME  # noqa: E402
from .themes.mono import PRESET as MONO_THEME  # noqa: E402
from .themes.ember import PRESET as EMBER_THEME  # noqa: E402
from .themes.aurora import PRESET as AURORA_THEME  # noqa: E402
from .themes.ink import PRESET as INK_THEME  # noqa: E402
from .themes.solarpunk import PRESET as SOLARPUNK_THEME  # noqa: E402
from .themes.bauhaus import PRESET as BAUHAUS_THEME  # noqa: E402
from .themes.cyberdeck import PRESET as CYBERDECK_THEME  # noqa: E402
from .themes.paper import PRESET as PAPER_THEME  # noqa: E402
from .themes.neon_noir import PRESET as NEON_NOIR_THEME  # noqa: E402
from .themes.ocean_deep import PRESET as OCEAN_THEME  # noqa: E402
from .themes.stripe import PRESET as STRIPE_THEME  # noqa: E402
from .themes.linear import PRESET as LINEAR_THEME  # noqa: E402
from .themes.notion import PRESET as NOTION_THEME  # noqa: E402
from .themes.vercel import PRESET as VERCEL_THEME  # noqa: E402
from .themes.github import PRESET as GITHUB_THEME  # noqa: E402
from .themes.art_deco import PRESET as ART_DECO_THEME  # noqa: E402
from .themes.handcraft import PRESET as HANDCRAFT_THEME  # noqa: E402
from .themes.terminal import PRESET as TERMINAL_THEME  # noqa: E402
from .themes.magazine import PRESET as MAGAZINE_THEME  # noqa: E402
from .themes.swiss import PRESET as SWISS_THEME  # noqa: E402
from .themes.candy import PRESET as CANDY_THEME  # noqa: E402
from .themes.retro_computing import PRESET as RETRO_COMPUTING_THEME  # noqa: E402
from .themes.medical import PRESET as MEDICAL_THEME  # noqa: E402
from .themes.legal import PRESET as LEGAL_THEME  # noqa: E402
from .themes.midnight import PRESET as MIDNIGHT_THEME  # noqa: E402
from .themes.sunrise import PRESET as SUNRISE_THEME  # noqa: E402
from .themes.forest_floor import PRESET as FOREST_FLOOR_THEME  # noqa: E402
from .themes.dashboard import PRESET as DASHBOARD_THEME  # noqa: E402
from .themes.one_dark import PRESET as ONE_DARK_THEME  # noqa: E402
from .themes.monokai import PRESET as MONOKAI_THEME  # noqa: E402
from .themes.ayu import PRESET as AYU_THEME  # noqa: E402
from .themes.kanagawa import PRESET as KANAGAWA_THEME  # noqa: E402
from .themes.everforest import PRESET as EVERFOREST_THEME  # noqa: E402
from .themes.poimandres import PRESET as POIMANDRES_THEME  # noqa: E402
from .themes.tailwind import PRESET as TAILWIND_THEME  # noqa: E402
from .themes.supabase import PRESET as SUPABASE_THEME  # noqa: E402
from .themes.raycast import PRESET as RAYCAST_THEME  # noqa: E402
from .themes.adaptive import PRESET as ADAPTIVE_THEME  # noqa: E402


# =============================================================================
# Preset Registry
# =============================================================================

THEME_PRESETS: dict[str, ThemePreset] = {
    "default": DEFAULT_THEME,
    "shadcn": SHADCN_THEME,
    "blue": BLUE_THEME,
    "green": GREEN_THEME,
    "purple": PURPLE_THEME,
    "orange": ORANGE_THEME,
    "rose": ROSE_THEME,
    "natural20": NATURAL20_THEME,
    "catppuccin": CATPPUCCIN_THEME,
    "rose_pine": ROSE_PINE_THEME,
    "tokyo_night": TOKYO_NIGHT_THEME,
    "nord": NORD_THEME,
    "synthwave": SYNTHWAVE_THEME,
    "cyberpunk": CYBERPUNK_THEME,
    "outrun": OUTRUN_THEME,
    "forest": FOREST_THEME,
    "amber": AMBER_THEME,
    "slate": SLATE_THEME,
    "nebula": NEBULA_THEME,
    "djust": DJUST_THEME,
    "dracula": DRACULA_THEME,
    "gruvbox": GRUVBOX_THEME,
    "solarized": SOLARIZED_THEME,
    "high_contrast": HIGH_CONTRAST_THEME,
    "mono": MONO_THEME,
    "ember": EMBER_THEME,
    "aurora": AURORA_THEME,
    "ink": INK_THEME,
    "solarpunk": SOLARPUNK_THEME,
    "bauhaus": BAUHAUS_THEME,
    "cyberdeck": CYBERDECK_THEME,
    "paper": PAPER_THEME,
    "neon_noir": NEON_NOIR_THEME,
    "ocean_deep": OCEAN_THEME,
    "stripe": STRIPE_THEME,
    "linear": LINEAR_THEME,
    "notion": NOTION_THEME,
    "vercel": VERCEL_THEME,
    "github": GITHUB_THEME,
    "art_deco": ART_DECO_THEME,
    "handcraft": HANDCRAFT_THEME,
    "terminal": TERMINAL_THEME,
    "magazine": MAGAZINE_THEME,
    "swiss": SWISS_THEME,
    "candy": CANDY_THEME,
    "retro_computing": RETRO_COMPUTING_THEME,
    "medical": MEDICAL_THEME,
    "legal": LEGAL_THEME,
    "midnight": MIDNIGHT_THEME,
    "sunrise": SUNRISE_THEME,
    "forest_floor": FOREST_FLOOR_THEME,
    "dashboard": DASHBOARD_THEME,
    "one_dark": ONE_DARK_THEME,
    "monokai": MONOKAI_THEME,
    "ayu": AYU_THEME,
    "kanagawa": KANAGAWA_THEME,
    "everforest": EVERFOREST_THEME,
    "poimandres": POIMANDRES_THEME,
    "tailwind": TAILWIND_THEME,
    "supabase": SUPABASE_THEME,
    "raycast": RAYCAST_THEME,
    "adaptive": ADAPTIVE_THEME,
}


def get_preset(name: str) -> ThemePreset:
    """Get a theme preset by name, with fallback to default."""
    return THEME_PRESETS.get(name, DEFAULT_THEME)


def list_presets() -> list[dict]:
    """Return list of available presets with metadata."""
    return [
        {
            "name": preset.name,
            "display_name": preset.display_name,
            "description": preset.description,
        }
        for preset in THEME_PRESETS.values()
    ]


__all__ = [
    # Re-exported types (back-compat)
    "ColorScale",
    "ThemeTokens",
    "SurfaceTreatment",
    "ThemePreset",
    # Registry / helpers
    "THEME_PRESETS",
    "get_preset",
    "list_presets",
]
