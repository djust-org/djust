"""
DEPRECATED: This module is deprecated in favor of theme_packs.py

Please use theme_packs.py instead, which provides:
- DesignSystem objects (color-independent design systems)
- Better separation between design systems and color presets
- More flexible mix-and-match capabilities

This module is kept for backward compatibility only.

---

Complete theming system with design system variants.

Each theme defines a complete design system including:
- Typography (fonts, sizes, weights)
- Spacing (margins, padding)
- Border radius
- Shadows
- Animations
- Component styles
"""

import warnings
from dataclasses import dataclass
from typing import Dict, Optional

_DEPRECATION_MSG = (
    "{name} is deprecated. Use DESIGN_SYSTEMS from djust.theming.theme_packs instead."
)


class _DeprecatedThemesDict(dict):
    """Dict wrapper that emits DeprecationWarning on access."""

    def __getitem__(self, key):
        warnings.warn(
            _DEPRECATION_MSG.format(name="THEMES"),
            DeprecationWarning,
            stacklevel=2,
        )
        return super().__getitem__(key)

    def __contains__(self, key):
        warnings.warn(
            _DEPRECATION_MSG.format(name="THEMES"),
            DeprecationWarning,
            stacklevel=2,
        )
        return super().__contains__(key)

    def get(self, key, default=None):
        warnings.warn(
            _DEPRECATION_MSG.format(name="THEMES"),
            DeprecationWarning,
            stacklevel=2,
        )
        return super().get(key, default)

    def items(self):
        warnings.warn(
            _DEPRECATION_MSG.format(name="THEMES"),
            DeprecationWarning,
            stacklevel=2,
        )
        return super().items()

    def keys(self):
        warnings.warn(
            _DEPRECATION_MSG.format(name="THEMES"),
            DeprecationWarning,
            stacklevel=2,
        )
        return super().keys()

    def values(self):
        warnings.warn(
            _DEPRECATION_MSG.format(name="THEMES"),
            DeprecationWarning,
            stacklevel=2,
        )
        return super().values()

    def __iter__(self):
        warnings.warn(
            _DEPRECATION_MSG.format(name="THEMES"),
            DeprecationWarning,
            stacklevel=2,
        )
        return super().__iter__()

    def __len__(self):
        warnings.warn(
            _DEPRECATION_MSG.format(name="THEMES"),
            DeprecationWarning,
            stacklevel=2,
        )
        return super().__len__()


@dataclass
class Typography:
    """Typography configuration for a theme."""

    font_sans: str
    font_mono: str
    font_display: Optional[str] = None

    # Font sizes
    text_xs: str = "0.75rem"
    text_sm: str = "0.875rem"
    text_base: str = "1rem"
    text_lg: str = "1.125rem"
    text_xl: str = "1.25rem"
    text_2xl: str = "1.5rem"
    text_3xl: str = "1.875rem"
    text_4xl: str = "2.25rem"
    text_5xl: str = "3rem"

    # Font weights
    font_normal: int = 400
    font_medium: int = 500
    font_semibold: int = 600
    font_bold: int = 700

    # Line heights
    leading_tight: float = 1.25
    leading_normal: float = 1.5
    leading_relaxed: float = 1.75
    leading_loose: float = 2.0


@dataclass
class Spacing:
    """Spacing configuration for a theme."""

    scale: str  # "tight", "normal", "loose"

    # Base spacing unit (in rem)
    base: float = 0.25

    # Spacing values (multipliers of base)
    space_0: int = 0
    space_1: int = 1  # 0.25rem
    space_2: int = 2  # 0.5rem
    space_3: int = 3  # 0.75rem
    space_4: int = 4  # 1rem
    space_5: int = 5  # 1.25rem
    space_6: int = 6  # 1.5rem
    space_8: int = 8  # 2rem
    space_10: int = 10  # 2.5rem
    space_12: int = 12  # 3rem
    space_16: int = 16  # 4rem
    space_20: int = 20  # 5rem
    space_24: int = 24  # 6rem


@dataclass
class BorderRadius:
    """Border radius configuration for a theme."""

    style: str  # "sharp", "rounded", "pill"

    radius_sm: str = "0.125rem"
    radius: str = "0.25rem"
    radius_md: str = "0.375rem"
    radius_lg: str = "0.5rem"
    radius_xl: str = "0.75rem"
    radius_2xl: str = "1rem"
    radius_3xl: str = "1.5rem"
    radius_full: str = "9999px"


@dataclass
class Shadows:
    """Shadow configuration for a theme."""

    style: str  # "flat", "subtle", "material", "elevated"

    shadow_xs: str = "0 1px 2px 0 rgb(0 0 0 / 0.05)"
    shadow_sm: str = "0 1px 3px 0 rgb(0 0 0 / 0.1), 0 1px 2px -1px rgb(0 0 0 / 0.1)"
    shadow: str = "0 4px 6px -1px rgb(0 0 0 / 0.1), 0 2px 4px -2px rgb(0 0 0 / 0.1)"
    shadow_md: str = "0 10px 15px -3px rgb(0 0 0 / 0.1), 0 4px 6px -4px rgb(0 0 0 / 0.1)"
    shadow_lg: str = "0 20px 25px -5px rgb(0 0 0 / 0.1), 0 8px 10px -6px rgb(0 0 0 / 0.1)"
    shadow_xl: str = "0 25px 50px -12px rgb(0 0 0 / 0.25)"
    shadow_2xl: str = "0 25px 50px -12px rgb(0 0 0 / 0.25)"
    shadow_inner: str = "inset 0 2px 4px 0 rgb(0 0 0 / 0.05)"


@dataclass
class Animations:
    """Animation configuration for a theme."""

    style: str  # "instant", "snappy", "smooth", "playful"

    # Durations
    duration_fast: str = "0.1s"
    duration_normal: str = "0.2s"
    duration_slow: str = "0.3s"

    # Easing curves
    ease_in: str = "cubic-bezier(0.4, 0, 1, 1)"
    ease_out: str = "cubic-bezier(0, 0, 0.2, 1)"
    ease_in_out: str = "cubic-bezier(0.4, 0, 0.2, 1)"
    ease_bounce: str = "cubic-bezier(0.68, -0.55, 0.265, 1.55)"


@dataclass
class ComponentStyles:
    """Component style configuration for a theme."""

    button_style: str  # "solid", "outlined", "ghost", "minimal"
    card_style: str  # "elevated", "outlined", "flat"
    input_style: str  # "outlined", "filled", "underlined"


@dataclass
class Theme:
    """Complete theme definition."""

    name: str
    display_name: str
    description: str

    # Design system components
    typography: Typography
    spacing: Spacing
    border_radius: BorderRadius
    shadows: Shadows
    animations: Animations
    component_styles: ComponentStyles

    # Color preset (existing system)
    color_preset: str = "default"


# ============================================
# Predefined Themes
# ============================================

# Material Design Theme
MATERIAL_THEME = Theme(
    name="material",
    display_name="Material Design",
    description="Google's Material Design system",
    typography=Typography(
        font_sans="Roboto, -apple-system, system-ui, sans-serif",
        font_mono="Roboto Mono, monospace",
        text_base="1rem",
        leading_normal=1.5,
    ),
    spacing=Spacing(
        scale="normal",
        base=0.25,  # 4px base (8dp grid)
    ),
    border_radius=BorderRadius(
        style="rounded",
        radius_sm="0.25rem",  # 4px
        radius="0.25rem",  # 4px
        radius_md="0.5rem",  # 8px
        radius_lg="0.75rem",  # 12px
    ),
    shadows=Shadows(
        style="material",
        shadow_sm="0 2px 4px rgba(0,0,0,0.14), 0 3px 4px rgba(0,0,0,0.12), 0 1px 5px rgba(0,0,0,0.2)",
        shadow="0 4px 8px rgba(0,0,0,0.14), 0 6px 10px rgba(0,0,0,0.12), 0 2px 16px rgba(0,0,0,0.2)",
        shadow_lg="0 12px 17px rgba(0,0,0,0.14), 0 5px 22px rgba(0,0,0,0.12), 0 7px 8px rgba(0,0,0,0.2)",
    ),
    animations=Animations(
        style="smooth",
        duration_fast="0.1s",
        duration_normal="0.2s",
        duration_slow="0.3s",
        ease_out="cubic-bezier(0.0, 0.0, 0.2, 1)",
        ease_in_out="cubic-bezier(0.4, 0.0, 0.2, 1)",
    ),
    component_styles=ComponentStyles(
        button_style="solid",
        card_style="elevated",
        input_style="filled",
    ),
    color_preset="default",
)

# iOS/Apple Theme
IOS_THEME = Theme(
    name="ios",
    display_name="iOS",
    description="Apple's iOS design language",
    typography=Typography(
        font_sans="-apple-system, BlinkMacSystemFont, 'SF Pro Text', sans-serif",
        font_mono="'SF Mono', Monaco, monospace",
        font_display="'SF Pro Display', sans-serif",
        leading_tight=1.2,
        leading_normal=1.4,
    ),
    spacing=Spacing(
        scale="tight",
        base=0.25,
    ),
    border_radius=BorderRadius(
        style="rounded",
        radius_sm="0.5rem",  # 8px
        radius="0.625rem",  # 10px
        radius_md="0.75rem",  # 12px
        radius_lg="1rem",  # 16px
        radius_xl="1.25rem",  # 20px
    ),
    shadows=Shadows(
        style="subtle",
        shadow_xs="0 1px 1px rgba(0,0,0,0.04)",
        shadow_sm="0 2px 4px rgba(0,0,0,0.06)",
        shadow="0 4px 8px rgba(0,0,0,0.08)",
        shadow_lg="0 8px 16px rgba(0,0,0,0.1)",
    ),
    animations=Animations(
        style="snappy",
        duration_fast="0.15s",
        duration_normal="0.25s",
        duration_slow="0.35s",
        ease_in_out="cubic-bezier(0.42, 0, 0.58, 1)",
    ),
    component_styles=ComponentStyles(
        button_style="solid",
        card_style="elevated",
        input_style="outlined",
    ),
    color_preset="blue",
)

# Fluent Design (Windows) Theme
FLUENT_THEME = Theme(
    name="fluent",
    display_name="Fluent Design",
    description="Microsoft's Fluent Design System",
    typography=Typography(
        font_sans="'Segoe UI', -apple-system, system-ui, sans-serif",
        font_mono="'Cascadia Code', Consolas, monospace",
        leading_normal=1.5,
    ),
    spacing=Spacing(
        scale="normal",
        base=0.25,
    ),
    border_radius=BorderRadius(
        style="rounded",
        radius_sm="0.125rem",  # 2px
        radius="0.25rem",  # 4px
        radius_md="0.375rem",  # 6px
        radius_lg="0.5rem",  # 8px
    ),
    shadows=Shadows(
        style="elevated",
        shadow_sm="0 1.6px 3.6px rgba(0,0,0,0.13), 0 0.3px 0.9px rgba(0,0,0,0.11)",
        shadow="0 3.2px 7.2px rgba(0,0,0,0.13), 0 0.6px 1.8px rgba(0,0,0,0.11)",
        shadow_lg="0 6.4px 14.4px rgba(0,0,0,0.13), 0 1.2px 3.6px rgba(0,0,0,0.11)",
    ),
    animations=Animations(
        style="smooth",
        duration_fast="0.167s",
        duration_normal="0.25s",
        duration_slow="0.367s",
        ease_out="cubic-bezier(0.1, 0.9, 0.2, 1)",
    ),
    component_styles=ComponentStyles(
        button_style="solid",
        card_style="elevated",
        input_style="outlined",
    ),
    color_preset="blue",
)

# Minimalist/Brutalist Theme
MINIMALIST_THEME = Theme(
    name="minimalist",
    display_name="Minimalist",
    description="Clean, minimal, brutalist design",
    typography=Typography(
        font_sans="'Inter', -apple-system, system-ui, sans-serif",
        font_mono="'JetBrains Mono', monospace",
        leading_tight=1.2,
        leading_normal=1.4,
    ),
    spacing=Spacing(
        scale="loose",
        base=0.25,
    ),
    border_radius=BorderRadius(
        style="sharp",
        radius_sm="0",
        radius="0",
        radius_md="0",
        radius_lg="0.125rem",  # Slight rounding only for large elements
        radius_xl="0.25rem",
    ),
    shadows=Shadows(
        style="flat",
        shadow_xs="none",
        shadow_sm="0 1px 0 rgba(0,0,0,0.1)",
        shadow="0 2px 0 rgba(0,0,0,0.1)",
        shadow_lg="0 4px 0 rgba(0,0,0,0.1)",
    ),
    animations=Animations(
        style="instant",
        duration_fast="0.05s",
        duration_normal="0.1s",
        duration_slow="0.15s",
        ease_out="linear",
        ease_in_out="linear",
    ),
    component_styles=ComponentStyles(
        button_style="outlined",
        card_style="outlined",
        input_style="underlined",
    ),
    color_preset="default",
)

# Modern/Playful Theme
PLAYFUL_THEME = Theme(
    name="playful",
    display_name="Playful",
    description="Modern, friendly, with personality",
    typography=Typography(
        font_sans="'DM Sans', 'Inter', sans-serif",
        font_mono="'Fira Code', monospace",
        font_display="'DM Sans', sans-serif",
        leading_relaxed=1.75,
    ),
    spacing=Spacing(
        scale="normal",
        base=0.25,
    ),
    border_radius=BorderRadius(
        style="pill",
        radius_sm="0.5rem",
        radius="1rem",
        radius_md="1.5rem",
        radius_lg="2rem",
        radius_xl="2.5rem",
        radius_full="9999px",
    ),
    shadows=Shadows(
        style="elevated",
        shadow_sm="0 2px 8px rgba(0,0,0,0.08)",
        shadow="0 4px 16px rgba(0,0,0,0.1)",
        shadow_lg="0 8px 32px rgba(0,0,0,0.12)",
        shadow_xl="0 16px 48px rgba(0,0,0,0.15)",
    ),
    animations=Animations(
        style="playful",
        duration_fast="0.2s",
        duration_normal="0.3s",
        duration_slow="0.5s",
        ease_bounce="cubic-bezier(0.68, -0.55, 0.265, 1.55)",
        ease_out="cubic-bezier(0.34, 1.56, 0.64, 1)",
    ),
    component_styles=ComponentStyles(
        button_style="solid",
        card_style="elevated",
        input_style="filled",
    ),
    color_preset="purple",
)

# Corporate/Professional Theme
CORPORATE_THEME = Theme(
    name="corporate",
    display_name="Corporate",
    description="Professional, clean, business-focused",
    typography=Typography(
        font_sans="'IBM Plex Sans', -apple-system, sans-serif",
        font_mono="'IBM Plex Mono', monospace",
        leading_normal=1.6,
        leading_relaxed=1.8,
    ),
    spacing=Spacing(
        scale="normal",
        base=0.25,
    ),
    border_radius=BorderRadius(
        style="rounded",
        radius_sm="0.125rem",
        radius="0.25rem",
        radius_md="0.375rem",
        radius_lg="0.5rem",
    ),
    shadows=Shadows(
        style="subtle",
        shadow_sm="0 1px 3px rgba(0,0,0,0.08)",
        shadow="0 2px 6px rgba(0,0,0,0.1)",
        shadow_lg="0 4px 12px rgba(0,0,0,0.12)",
    ),
    animations=Animations(
        style="smooth",
        duration_fast="0.15s",
        duration_normal="0.2s",
        duration_slow="0.3s",
        ease_in_out="cubic-bezier(0.4, 0, 0.2, 1)",
    ),
    component_styles=ComponentStyles(
        button_style="solid",
        card_style="outlined",
        input_style="outlined",
    ),
    color_preset="blue",
)

# Retro theme - Classic web 1.0 aesthetic
THEME_RETRO = Theme(
    name="retro",
    display_name="Retro",
    description="Classic web 1.0 with system fonts and sharp edges",
    typography=Typography(
        font_sans="'MS Sans Serif', 'Geneva', 'Verdana', sans-serif",
        font_mono="'Courier New', 'Courier', monospace",
        text_base="0.875rem",  # 14px
        text_sm="0.75rem",  # 12px
        text_lg="1rem",  # 16px
        text_xl="1.25rem",  # 20px
        text_2xl="1.5rem",  # 24px
        font_normal=400,
        font_bold=700,
        leading_tight=1.2,
        leading_normal=1.4,
        leading_relaxed=1.6,
    ),
    spacing=Spacing(
        scale="normal",
        base=0.5,  # 8px base
    ),
    border_radius=BorderRadius(
        style="sharp",
        radius_sm="0px",
        radius="0px",
        radius_md="0px",
        radius_lg="0px",
    ),
    shadows=Shadows(
        style="basic",
        shadow_sm="2px 2px 0 rgba(0,0,0,0.3)",
        shadow="3px 3px 0 rgba(0,0,0,0.4)",
        shadow_lg="4px 4px 0 rgba(0,0,0,0.5)",
    ),
    animations=Animations(
        style="instant",
        duration_fast="0.05s",
        duration_normal="0.1s",
        duration_slow="0.15s",
        ease_in_out="linear",
    ),
    component_styles=ComponentStyles(
        button_style="solid",
        card_style="outlined",
        input_style="outlined",
    ),
    color_preset="default",
)

# Elegant theme - Premium, sophisticated design
THEME_ELEGANT = Theme(
    name="elegant",
    display_name="Elegant",
    description="Premium design with serif fonts and generous spacing",
    typography=Typography(
        font_sans="'Crimson Pro', 'Cormorant', 'Playfair Display', serif",
        font_mono="'IBM Plex Mono', 'Courier New', monospace",
        text_base="1.0625rem",  # 17px
        text_sm="0.9375rem",  # 15px
        text_lg="1.1875rem",  # 19px
        text_xl="1.5rem",  # 24px
        text_2xl="2rem",  # 32px
        font_normal=400,
        font_medium=500,
        font_semibold=600,
        leading_tight=1.4,
        leading_normal=1.7,
        leading_relaxed=1.9,
    ),
    spacing=Spacing(
        scale="generous",
        base=0.75,  # 12px base
    ),
    border_radius=BorderRadius(
        style="subtle",
        radius_sm="0.125rem",
        radius="0.25rem",
        radius_md="0.375rem",
        radius_lg="0.5rem",
    ),
    shadows=Shadows(
        style="elegant",
        shadow_sm="0 1px 3px rgba(0,0,0,0.04)",
        shadow="0 4px 12px rgba(0,0,0,0.06)",
        shadow_lg="0 12px 28px rgba(0,0,0,0.08)",
    ),
    animations=Animations(
        style="refined",
        duration_fast="0.25s",
        duration_normal="0.4s",
        duration_slow="0.6s",
        ease_in_out="cubic-bezier(0.25, 0.46, 0.45, 0.94)",
    ),
    component_styles=ComponentStyles(
        button_style="ghost",
        card_style="flat",
        input_style="underlined",
    ),
    color_preset="default",
)

# Neo-Brutalist theme - Bold, dramatic, high-contrast
THEME_NEO_BRUTALIST = Theme(
    name="neo_brutalist",
    display_name="Neo-Brutalist",
    description="Bold, dramatic design with thick borders and high contrast",
    typography=Typography(
        font_sans="'Space Grotesk', 'Archivo Black', 'Bebas Neue', sans-serif",
        font_mono="'Space Mono', 'Courier', monospace",
        text_base="1.125rem",  # 18px
        text_sm="1rem",  # 16px
        text_lg="1.375rem",  # 22px
        text_xl="2rem",  # 32px
        text_2xl="3rem",  # 48px
        font_normal=500,
        font_medium=700,
        font_bold=900,
        leading_tight=1.1,
        leading_normal=1.3,
        leading_relaxed=1.5,
    ),
    spacing=Spacing(
        scale="normal",
        base=0.5,  # 8px base
    ),
    border_radius=BorderRadius(
        style="sharp",
        radius_sm="0px",
        radius="0px",
        radius_md="0px",
        radius_lg="0px",
    ),
    shadows=Shadows(
        style="dramatic",
        shadow_sm="4px 4px 0 rgba(0,0,0,1)",
        shadow="8px 8px 0 rgba(0,0,0,1)",
        shadow_lg="12px 12px 0 rgba(0,0,0,1)",
    ),
    animations=Animations(
        style="snappy",
        duration_fast="0.08s",
        duration_normal="0.12s",
        duration_slow="0.18s",
        ease_in_out="cubic-bezier(0.68, -0.55, 0.265, 1.55)",
    ),
    component_styles=ComponentStyles(
        button_style="solid",
        card_style="outlined",
        input_style="outlined",
    ),
    color_preset="default",
)

# Organic theme - Soft, rounded, nature-inspired
THEME_ORGANIC = Theme(
    name="organic",
    display_name="Organic",
    description="Soft, nature-inspired design with gentle curves",
    typography=Typography(
        font_sans="'Nunito', 'Quicksand', 'Comfortaa', sans-serif",
        font_mono="'Inconsolata', monospace",
        text_base="1rem",  # 16px
        text_sm="0.875rem",  # 14px
        text_lg="1.125rem",  # 18px
        text_xl="1.5rem",  # 24px
        text_2xl="2rem",  # 32px
        font_normal=400,
        font_medium=600,
        font_bold=700,
        leading_tight=1.4,
        leading_normal=1.6,
        leading_relaxed=1.8,
    ),
    spacing=Spacing(
        scale="comfortable",
        base=0.625,  # 10px base
    ),
    border_radius=BorderRadius(
        style="pill",
        radius_sm="1rem",
        radius="1.5rem",
        radius_md="2rem",
        radius_lg="3rem",
    ),
    shadows=Shadows(
        style="soft",
        shadow_sm="0 2px 8px rgba(0,0,0,0.08)",
        shadow="0 4px 16px rgba(0,0,0,0.1)",
        shadow_lg="0 8px 32px rgba(0,0,0,0.12)",
    ),
    animations=Animations(
        style="gentle",
        duration_fast="0.3s",
        duration_normal="0.5s",
        duration_slow="0.8s",
        ease_in_out="cubic-bezier(0.34, 1.56, 0.64, 1)",
    ),
    component_styles=ComponentStyles(
        button_style="solid",
        card_style="elevated",
        input_style="filled",
    ),
    color_preset="green",
)

# Dense theme - Compact, efficient, information-dense
THEME_DENSE = Theme(
    name="dense",
    display_name="Dense",
    description="Compact design for maximum information density",
    typography=Typography(
        font_sans="'Roboto Condensed', 'Arial Narrow', sans-serif",
        font_mono="'Monaco', 'Consolas', monospace",
        text_base="0.8125rem",  # 13px
        text_sm="0.6875rem",  # 11px
        text_lg="0.9375rem",  # 15px
        text_xl="1.125rem",  # 18px
        text_2xl="1.375rem",  # 22px
        font_normal=400,
        font_medium=500,
        font_bold=700,
        leading_tight=1.2,
        leading_normal=1.35,
        leading_relaxed=1.5,
    ),
    spacing=Spacing(
        scale="tight",
        base=0.25,  # 4px base
    ),
    border_radius=BorderRadius(
        style="minimal",
        radius_sm="0.125rem",
        radius="0.125rem",
        radius_md="0.25rem",
        radius_lg="0.25rem",
    ),
    shadows=Shadows(
        style="minimal",
        shadow_sm="0 1px 2px rgba(0,0,0,0.06)",
        shadow="0 1px 3px rgba(0,0,0,0.08)",
        shadow_lg="0 2px 6px rgba(0,0,0,0.1)",
    ),
    animations=Animations(
        style="immediate",
        duration_fast="0.05s",
        duration_normal="0.1s",
        duration_slow="0.15s",
        ease_in_out="ease-out",
    ),
    component_styles=ComponentStyles(
        button_style="ghost",
        card_style="flat",
        input_style="outlined",
    ),
    color_preset="default",
)

# djust.org Theme
DJUST_THEME = Theme(
    name="djust",
    display_name="djust.org",
    description="djust.org brand — dark with rust orange and Django green accents",
    typography=Typography(
        font_sans="Inter, -apple-system, BlinkMacSystemFont, sans-serif",
        font_mono="JetBrains Mono, monospace",
        text_base="1rem",
        text_xs="0.75rem",
        text_sm="0.875rem",
        text_lg="1.125rem",
        text_xl="1.25rem",
        text_2xl="1.5rem",
        text_3xl="1.875rem",
        text_4xl="2.25rem",
        text_5xl="3rem",
        font_normal=400,
        font_medium=500,
        font_semibold=600,
        font_bold=700,
        leading_tight=1.25,
        leading_normal=1.625,
        leading_relaxed=1.75,
    ),
    spacing=Spacing(
        scale="normal",
        base=0.25,
    ),
    border_radius=BorderRadius(
        style="rounded",
        radius_sm="0.25rem",
        radius="0.25rem",
        radius_md="0.375rem",
        radius_lg="0.5rem",
        radius_xl="0.75rem",
        radius_2xl="1rem",
        radius_3xl="1.5rem",
        radius_full="9999px",
    ),
    shadows=Shadows(
        style="subtle",
        shadow_xs="0 1px 2px rgba(0,0,0,0.2)",
        shadow_sm="0 1px 3px rgba(0,0,0,0.3)",
        shadow="0 4px 6px rgba(0,0,0,0.3)",
        shadow_md="0 10px 15px rgba(0,0,0,0.35)",
        shadow_lg="0 20px 25px rgba(0,0,0,0.4)",
        shadow_xl="0 25px 50px rgba(0,0,0,0.45)",
        shadow_2xl="0 25px 50px rgba(0,0,0,0.5)",
        shadow_inner="inset 0 2px 4px rgba(0,0,0,0.1)",
    ),
    animations=Animations(
        style="smooth",
        duration_fast="0.15s",
        duration_normal="0.2s",
        duration_slow="0.3s",
        ease_in="cubic-bezier(0.4, 0, 1, 1)",
        ease_out="cubic-bezier(0, 0, 0.2, 1)",
        ease_in_out="cubic-bezier(0.4, 0, 0.2, 1)",
        ease_bounce="cubic-bezier(0.68, -0.55, 0.265, 1.55)",
    ),
    component_styles=ComponentStyles(
        button_style="solid",
        card_style="elevated",
        input_style="outlined",
    ),
    color_preset="djust",
)


# Theme Registry (deprecated — use DESIGN_SYSTEMS from theme_packs.py)
# Bauhaus theme — geometric modernist, Itten's triad, Bayer's type
BAUHAUS_THEME = Theme(
    name="bauhaus",
    display_name="Bauhaus",
    description="Itten's primary triad, Bayer's geometric type, zero decoration",
    typography=Typography(
        font_sans="'DM Sans', 'Inter', system-ui, sans-serif",
        font_mono="'DM Mono', 'JetBrains Mono', monospace",
        font_display="'DM Sans', 'Inter', system-ui, sans-serif",
        text_base="1rem",
        text_sm="0.875rem",
        text_lg="1.125rem",
        text_xl="1.5rem",
        text_2xl="2rem",
        text_3xl="2.5rem",
        text_4xl="3.5rem",  # Large poster-scale headings
        text_5xl="4.5rem",
        font_normal=400,
        font_medium=500,
        font_semibold=700,
        font_bold=900,  # Black weight headings
        leading_tight=1.0,  # Ultra-tight for poster headlines
        leading_normal=1.4,  # Controlled grid
        leading_relaxed=1.6,
        leading_loose=1.8,
    ),
    spacing=Spacing(
        scale="normal",
        base=0.25,
    ),
    border_radius=BorderRadius(
        style="sharp",
        radius_sm="0px",  # Zero radius everywhere
        radius="0px",
        radius_md="0px",
        radius_lg="0px",
        radius_xl="0px",
        radius_2xl="0px",
        radius_3xl="0px",
    ),
    shadows=Shadows(
        style="dramatic",
        shadow_xs="2px 2px 0 rgba(0,0,0,0.9)",
        shadow_sm="3px 3px 0 rgba(0,0,0,0.9)",  # Hard offset, no blur
        shadow="6px 6px 0 rgba(0,0,0,0.9)",
        shadow_md="6px 6px 0 rgba(0,0,0,0.9)",
        shadow_lg="10px 10px 0 rgba(0,0,0,0.9)",  # Dramatic hard offset
        shadow_xl="14px 14px 0 rgba(0,0,0,0.9)",
        shadow_2xl="18px 18px 0 rgba(0,0,0,0.9)",
        shadow_inner="inset 3px 3px 0 rgba(0,0,0,0.2)",
    ),
    animations=Animations(
        style="instant",
        duration_fast="0.05s",  # Near-instant — poster, not app
        duration_normal="0.08s",
        duration_slow="0.12s",
        ease_in="linear",  # No easing curves — geometric = linear
        ease_out="linear",
        ease_in_out="linear",
    ),
    component_styles=ComponentStyles(
        button_style="solid",  # Solid filled buttons
        card_style="outlined",  # Heavy border cards
        input_style="outlined",  # Heavy border inputs
    ),
    color_preset="bauhaus",
)


THEMES: Dict[str, Theme] = _DeprecatedThemesDict(
    {
        "material": MATERIAL_THEME,
        "ios": IOS_THEME,
        "fluent": FLUENT_THEME,
        "minimalist": MINIMALIST_THEME,
        "playful": PLAYFUL_THEME,
        "corporate": CORPORATE_THEME,
        "retro": THEME_RETRO,
        "elegant": THEME_ELEGANT,
        "neo_brutalist": THEME_NEO_BRUTALIST,
        "organic": THEME_ORGANIC,
        "dense": THEME_DENSE,
        "djust": DJUST_THEME,
        "bauhaus": BAUHAUS_THEME,
    }
)


def get_theme(name: str) -> Optional[Theme]:
    """Get a theme by name.

    .. deprecated::
        Use ``get_design_system`` from ``djust_theming.theme_packs`` instead.
    """
    warnings.warn(
        _DEPRECATION_MSG.format(name="get_theme()"),
        DeprecationWarning,
        stacklevel=2,
    )
    # Bypass the _DeprecatedThemesDict warning to avoid double-warning
    return dict.get(THEMES, name)


def list_themes() -> Dict[str, Theme]:
    """Get all available themes.

    .. deprecated::
        Use ``get_all_design_systems`` from ``djust_theming.theme_packs`` instead.
    """
    warnings.warn(
        _DEPRECATION_MSG.format(name="list_themes()"),
        DeprecationWarning,
        stacklevel=2,
    )
    # Bypass the _DeprecatedThemesDict warning to avoid double-warning
    return dict.copy(THEMES)
