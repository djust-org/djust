"""
Theme preset definitions using HSL color tokens.

Based on shadcn/ui theming system with CSS custom properties.
Each preset is defined in its own file under themes/.
"""

from dataclasses import dataclass
from typing import Tuple


@dataclass
class ColorScale:
    """HSL color representation for CSS custom properties."""

    h: int  # Hue 0-360
    s: int  # Saturation 0-100
    lightness: int  # Lightness 0-100

    def to_hsl(self) -> str:
        """Return HSL values for CSS variable (without hsl() wrapper)."""
        return f"{self.h} {self.s}% {self.lightness}%"

    def to_hsl_func(self) -> str:
        """Return complete hsl() function."""
        return f"hsl({self.h}, {self.s}%, {self.lightness}%)"

    def to_hex(self) -> str:
        """Return hex color string, e.g. '#3b82f6'."""
        from .colors import hsl_to_hex

        return hsl_to_hex(self.h, self.s, self.lightness)

    def to_rgb(self) -> Tuple[int, int, int]:
        """Return RGB tuple (0-255 each)."""
        from .colors import hsl_to_rgb

        return hsl_to_rgb(self.h, self.s, self.lightness)

    def to_rgb_func(self) -> str:
        """Return complete rgb() CSS function string, e.g. 'rgb(59, 130, 246)'."""
        r, g, b = self.to_rgb()
        return f"rgb({r}, {g}, {b})"

    @classmethod
    def from_hex(cls, hex_str: str) -> "ColorScale":
        """Create ColorScale from hex string (#RRGGBB or #RGB)."""
        from .colors import hex_to_hsl

        h, s, l = hex_to_hsl(hex_str)
        return cls(h, s, l)

    @classmethod
    def from_rgb(cls, r: int, g: int, b: int) -> "ColorScale":
        """Create ColorScale from RGB values (0-255 each)."""
        from .colors import rgb_to_hsl

        h, s, l = rgb_to_hsl(r, g, b)
        return cls(h, s, l)

    def with_lightness(self, new_lightness: int) -> "ColorScale":
        """Return a new ColorScale with modified lightness."""
        return ColorScale(self.h, self.s, new_lightness)

    def with_saturation(self, new_saturation: int) -> "ColorScale":
        """Return a new ColorScale with modified saturation."""
        return ColorScale(self.h, new_saturation, self.lightness)


@dataclass
class ThemeTokens:
    """
    Complete token set for a theme mode.

    Follows shadcn/ui naming conventions with extensions for
    success, warning, info, and additional semantic states.
    """

    # Backgrounds
    background: ColorScale
    foreground: ColorScale

    # Card surfaces
    card: ColorScale
    card_foreground: ColorScale

    # Popover surfaces
    popover: ColorScale
    popover_foreground: ColorScale

    # Primary action color
    primary: ColorScale
    primary_foreground: ColorScale

    # Secondary/muted action
    secondary: ColorScale
    secondary_foreground: ColorScale

    # Muted backgrounds
    muted: ColorScale
    muted_foreground: ColorScale

    # Accent for highlights
    accent: ColorScale
    accent_foreground: ColorScale

    # Destructive/error
    destructive: ColorScale
    destructive_foreground: ColorScale

    # Success state (extension)
    success: ColorScale
    success_foreground: ColorScale

    # Warning state (extension)
    warning: ColorScale
    warning_foreground: ColorScale

    # Info state (extension)
    info: ColorScale
    info_foreground: ColorScale

    # Link color (extension)
    link: ColorScale
    link_hover: ColorScale

    # Code/mono background (extension)
    code: ColorScale
    code_foreground: ColorScale

    # Selection/highlight (extension)
    selection: ColorScale
    selection_foreground: ColorScale

    # Brand/signature color (extension)
    # The distinctive identity color beyond primary. Dracula Pink, Nord Frost,
    # Catppuccin Rosewater, Itten Blue, etc. Themes without a distinct brand
    # color should set this to match primary.
    brand: ColorScale
    brand_foreground: ColorScale

    # UI elements
    border: ColorScale
    input: ColorScale
    ring: ColorScale

    # Surface levels for complex dark layouts (e.g., landing pages)
    # surface_1: darkest (ultra-dark background)
    # surface_2: mid-level (panels, navbar)
    # surface_3: elevated (cards, elevated elements)
    surface_1: ColorScale
    surface_2: ColorScale
    surface_3: ColorScale


@dataclass
class SurfaceTreatment:
    """Surface styling treatments for glass panels, gradients, and noise effects."""

    style: str = "glass"  # "glass" | "gradient" | "noise"

    # Glass surface properties
    glass_background: str = "rgba(21, 27, 43, 0.7)"
    glass_border: str = "rgba(255, 255, 255, 0.1)"
    glass_blur: str = "12px"
    surface_radius: str | None = None  # None means use --radius

    # Gradient surface properties
    gradient_direction: str = "180deg"
    gradient_from: str = "#1e293b"
    gradient_to: str = "#0f172a"

    # Noise surface properties
    noise_opacity: float = 0.03


@dataclass
class ThemePreset:
    """A complete theme with light and dark mode tokens."""

    name: str
    display_name: str
    light: ThemeTokens
    dark: ThemeTokens
    description: str = ""
    radius: float = 0.5  # Border radius multiplier (output as --radius: {val}rem)

    # Which mode is the default (emitted in :root)?
    # "light" = :root gets light tokens (standard shadcn behavior)
    # "dark" = :root gets dark tokens (for dark-first themes like djust.org)
    default_mode: str = "light"

    # Extra CSS custom properties beyond the standard shadcn set.
    # Use this for brand-specific variables like --color-brand-rust,
    # --background-image-grid-pattern, --animation-pulse-slow, etc.
    # These are emitted in the base :root block.
    extra_css_vars: dict | None = None

    # Per-mode brand CSS variables for light and dark modes.
    # Use these for brand surface colors that need to differ between modes
    # (e.g., --color-brand-dark: #0B0F19 in dark, #f8fafc in light).
    # If None, extra_css_vars is used for both modes.
    extra_css_vars_light: dict | None = None
    extra_css_vars_dark: dict | None = None

    # Surface treatment for glass panels, gradients, etc.
    surface: SurfaceTreatment | None = None


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
