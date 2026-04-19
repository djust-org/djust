"""
djust_theming - A shadcn/ui-inspired theming system for Django.

Provides CSS custom properties-based theming with light/dark mode support,
multiple theme presets, and seamless Django/djust integration.
"""

from .cache import clear_css_cache
from .colors import hex_to_hsl, hex_to_rgb, hsl_to_hex, hsl_to_rgb, rgb_to_hex, rgb_to_hsl
from .components import ThemeSwitcher
from .css_generator import ThemeCSSGenerator
from .manifest import ThemeManifest
from .manager import (
    ThemeManager,
    ThemeState,
    generate_critical_css_for_state,
    generate_css_for_state,
    generate_deferred_css_for_state,
    get_css_prefix,
    get_theme_manager,
)
from .registry import (
    ThemeRegistry,
    get_registry,
    register_preset,
    register_design_system,
    register_theme_pack,
)
from .mixins import ThemeMixin
from .palette import PaletteGenerator
from .tailwind import (
    generate_tailwind_config,
    generate_tailwindv4_theme_block,
    generate_tailwindv4_theme_block_cached,
    export_preset_as_tailwind_colors,
)
from .presets import (
    BLUE_THEME,
    DEFAULT_THEME,
    GREEN_THEME,
    ORANGE_THEME,
    PURPLE_THEME,
    ROSE_THEME,
    THEME_PRESETS,
    ColorScale,
    ThemePreset,
    ThemeTokens,
)

__all__ = [
    # Cache
    "clear_css_cache",
    # Color utilities
    "hsl_to_rgb",
    "rgb_to_hsl",
    "hex_to_rgb",
    "rgb_to_hex",
    "hex_to_hsl",
    "hsl_to_hex",
    # Presets
    "ColorScale",
    "ThemeTokens",
    "ThemePreset",
    "THEME_PRESETS",
    "DEFAULT_THEME",
    "BLUE_THEME",
    "GREEN_THEME",
    "PURPLE_THEME",
    "ORANGE_THEME",
    "ROSE_THEME",
    # Manifest
    "ThemeManifest",
    # Registry & extension API
    "ThemeRegistry",
    "get_registry",
    "register_preset",
    "register_design_system",
    "register_theme_pack",
    # Core
    "ThemeCSSGenerator",
    "ThemeManager",
    "generate_critical_css_for_state",
    "generate_css_for_state",
    "generate_deferred_css_for_state",
    "get_css_prefix",
    "get_theme_manager",
    "ThemeState",
    "ThemeMixin",
    "ThemeSwitcher",
    # Palette generator
    "PaletteGenerator",
    # Tailwind CSS integration
    "generate_tailwind_config",
    "generate_tailwindv4_theme_block",
    "generate_tailwindv4_theme_block_cached",
    "export_preset_as_tailwind_colors",
]

__version__ = "0.4.0rc4"

default_app_config = "djust.theming.apps.DjustThemingConfig"
