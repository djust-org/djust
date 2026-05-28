"""Built-in theme PRESET imports + the ``THEME_PRESETS`` registry dict.

Extracted from ``presets.py`` (PR #TBD, fixing the four CodeQL
``py/cyclic-import`` alerts #2352/#2351/#1900/#1883) so that the static
built-in preset dict and the ``registry.py`` discovery path can both
import it without going through ``presets.py``. The cycle prior to
this split was:

    presets.py --(top-level get_preset import below)--> registry.py
    registry.py:127 --(lazy import inside _do_discover)--> presets.py

Breaking the second edge (registry now imports ``THEME_PRESETS`` from
this module instead of ``presets``) eliminates the SCC. ``presets.py``
keeps its public API surface — it now re-exports ``THEME_PRESETS`` and
``DEFAULT_THEME`` from here.

This module deliberately has NO runtime dependency on ``presets``,
``registry``, ``manager``, or ``css_generator``. Its only imports are
the ``themes/*`` submodules, which in turn only depend on ``_types``.
"""

from ._types import ThemePreset

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
from .themes.docs import PRESET as DOCS_THEME  # noqa: E402
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
    "docs": DOCS_THEME,
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


# ``__all__`` lists every name `presets.py` re-exports via `from
# ._builtin_presets import *`, preserving back-compat for any external
# user code that does `from djust.theming.presets import BLUE_THEME` etc.
__all__ = [
    "THEME_PRESETS",
    "DEFAULT_THEME",
    "SHADCN_THEME",
    "BLUE_THEME",
    "GREEN_THEME",
    "PURPLE_THEME",
    "ORANGE_THEME",
    "ROSE_THEME",
    "NATURAL20_THEME",
    "CATPPUCCIN_THEME",
    "ROSE_PINE_THEME",
    "TOKYO_NIGHT_THEME",
    "NORD_THEME",
    "SYNTHWAVE_THEME",
    "CYBERPUNK_THEME",
    "OUTRUN_THEME",
    "FOREST_THEME",
    "AMBER_THEME",
    "SLATE_THEME",
    "NEBULA_THEME",
    "DJUST_THEME",
    "DRACULA_THEME",
    "GRUVBOX_THEME",
    "SOLARIZED_THEME",
    "HIGH_CONTRAST_THEME",
    "MONO_THEME",
    "EMBER_THEME",
    "AURORA_THEME",
    "INK_THEME",
    "SOLARPUNK_THEME",
    "BAUHAUS_THEME",
    "CYBERDECK_THEME",
    "PAPER_THEME",
    "NEON_NOIR_THEME",
    "OCEAN_THEME",
    "STRIPE_THEME",
    "LINEAR_THEME",
    "NOTION_THEME",
    "VERCEL_THEME",
    "GITHUB_THEME",
    "ART_DECO_THEME",
    "HANDCRAFT_THEME",
    "TERMINAL_THEME",
    "MAGAZINE_THEME",
    "DOCS_THEME",
    "SWISS_THEME",
    "CANDY_THEME",
    "RETRO_COMPUTING_THEME",
    "MEDICAL_THEME",
    "LEGAL_THEME",
    "MIDNIGHT_THEME",
    "SUNRISE_THEME",
    "FOREST_FLOOR_THEME",
    "DASHBOARD_THEME",
    "ONE_DARK_THEME",
    "MONOKAI_THEME",
    "AYU_THEME",
    "KANAGAWA_THEME",
    "EVERFOREST_THEME",
    "POIMANDRES_THEME",
    "TAILWIND_THEME",
    "SUPABASE_THEME",
    "RAYCAST_THEME",
    "ADAPTIVE_THEME",
]
