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

# The theming domain model. Defined in `_types` (a private module holding
# public types) and never re-exported, so `from djust.theming import
# ThemePack` raised ImportError even though `register_theme_pack` accepts one
# and `_types.__all__` advertises it. The documented model was unusable.
from ._types import (  # noqa: E402
    AnimationStyle,
    DesignSystem,
    IconStyle,
    IllustrationStyle,
    InteractionStyle,
    LayoutStyle,
    PatternStyle,
    SurfaceStyle,
    SurfaceTreatment,
    ThemePack,
    TypographyStyle,
)

# Request-scoped helpers — the documented way to read and change the active
# theme from a view. Thin wrappers over ThemeManager.
from .api import (  # noqa: E402
    get_active_mode,
    get_active_pack,
    get_theme_css_url,
    reset_to_defaults,
    set_active_mode,
    set_active_pack,
)

# Named style constants the pack-authoring docs import, and the pack CSS
# generator. Same class of gap as the types above: defined, documented, and
# not reachable from the package.
from ._constants import ILLUST_LINE, PATTERN_MINIMAL  # noqa: E402
from .pack_css_generator import generate_pack_css  # noqa: E402

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
    # Theming domain model
    "ThemePack",
    "DesignSystem",
    "SurfaceTreatment",
    "TypographyStyle",
    "LayoutStyle",
    "SurfaceStyle",
    "IconStyle",
    "AnimationStyle",
    "InteractionStyle",
    "PatternStyle",
    "IllustrationStyle",
    # Request-scoped helpers
    "get_active_pack",
    "set_active_pack",
    "get_active_mode",
    "set_active_mode",
    "reset_to_defaults",
    "get_theme_css_url",
    # Named style constants + pack CSS generation
    "PATTERN_MINIMAL",
    "ILLUST_LINE",
    "generate_pack_css",
]

__version__ = "0.4.0rc4"
