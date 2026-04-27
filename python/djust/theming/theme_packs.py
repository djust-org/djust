"""
Design Systems - Pure visual design without color dependency.

A DesignSystem defines the non-color aspects of UI:
- Typography (fonts, sizes, spacing)
- Layout (grid systems, component shapes)
- Visual patterns (borders, shadows, textures)
- Animation behaviors
- Interaction feedback

Note: the dataclass types and the shared style instances
(``PATTERN_*``, ``ILLUST_*``, pack-level ``ICON_*``, ``ANIM_*``, and
``INTERACT_*``) live in ``_types.py`` and ``_constants.py`` respectively.
Keeping them out of this module lets ``themes/_base.py`` import them
without forming a cycle back through this module. They are re-exported
from here for backward compatibility.
"""

from typing import Dict, Optional

from ._constants import (
    # Design-system-level (used by DESIGN_* below)
    ANIM_BRUTALIST,
    ANIM_CORPORATE,
    ANIM_DENSE,
    ANIM_DJUST,
    ANIM_ELEGANT,
    ANIM_FLUENT,
    ANIM_IOS,
    ANIM_MATERIAL,
    ANIM_MINIMAL,
    ANIM_ORGANIC,
    ANIM_PLAYFUL,
    ANIM_RETRO,
    ICON_BRUTALIST,
    ICON_CORPORATE,
    ICON_DENSE,
    ICON_DJUST,
    ICON_ELEGANT,
    ICON_FLUENT,
    ICON_IOS,
    ICON_MATERIAL,
    ICON_MINIMAL,
    ICON_ORGANIC,
    ICON_PLAYFUL,
    ICON_RETRO,
    INTERACT_BRUTALIST,
    INTERACT_CORPORATE,
    INTERACT_DENSE,
    INTERACT_DJUST,
    INTERACT_ELEGANT,
    INTERACT_FLUENT,
    INTERACT_IOS,
    INTERACT_MATERIAL,
    INTERACT_ORGANIC,
    INTERACT_RETRO,
    # Pack-level
    ANIM_BOUNCY,
    ANIM_GENTLE,
    ANIM_INSTANT,
    ANIM_SMOOTH,
    ANIM_SNAPPY,
    ICON_FILLED,
    ICON_OUTLINED,
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
    # Internal DS-level names for the two cases that the pack-level
    # INTERACT_MINIMAL / INTERACT_PLAYFUL would otherwise shadow
    _INTERACT_MINIMAL_DS,
    _INTERACT_PLAYFUL_DS,
)
from ._types import (
    AnimationStyle,
    DesignSystem,
    IconStyle,
    IllustrationStyle,
    InteractionStyle,
    LayoutStyle,
    PatternStyle,
    SurfaceStyle,
    ThemePack,
    TypographyStyle,
)


# =============================================================================
# Typography Presets
# =============================================================================

TYPO_MATERIAL = TypographyStyle(
    name="material",
    heading_font="system-ui",
    body_font="system-ui",
    base_size="16px",
    heading_scale=1.25,
    line_height="1.5",
    heading_weight="500",
    body_weight="400",
    letter_spacing="normal",
)

TYPO_IOS = TypographyStyle(
    name="ios",
    heading_font="system-ui",
    body_font="system-ui",
    base_size="17px",
    heading_scale=1.3,
    line_height="1.4",
    heading_weight="600",
    body_weight="400",
    letter_spacing="-0.025em",
)

TYPO_FLUENT = TypographyStyle(
    name="fluent",
    heading_font="system-ui",
    body_font="system-ui",
    base_size="14px",
    heading_scale=1.25,
    line_height="1.5",
    heading_weight="600",
    body_weight="400",
    letter_spacing="normal",
)

TYPO_PLAYFUL = TypographyStyle(
    name="playful",
    heading_font="display",
    body_font="system-ui",
    base_size="16px",
    heading_scale=1.3,
    line_height="1.75",
    heading_weight="700",
    body_weight="400",
    letter_spacing="normal",
)

TYPO_CORPORATE = TypographyStyle(
    name="corporate",
    heading_font="system-ui",
    body_font="system-ui",
    base_size="16px",
    heading_scale=1.2,
    line_height="1.6",
    heading_weight="600",
    body_weight="400",
    letter_spacing="normal",
)

TYPO_DENSE = TypographyStyle(
    name="dense",
    heading_font="system-ui",
    body_font="system-ui",
    base_size="13px",
    heading_scale=1.15,
    line_height="1.35",
    heading_weight="600",
    body_weight="400",
    letter_spacing="-0.025em",
)

TYPO_MINIMAL = TypographyStyle(
    name="minimal",
    heading_font="system-ui",
    body_font="system-ui",
    base_size="16px",
    heading_scale=1.2,  # Subtle scale
    line_height="1.6",
    heading_weight="500",  # Lighter weight
    body_weight="400",
    letter_spacing="normal",
)

TYPO_BRUTALIST = TypographyStyle(
    name="brutalist",
    heading_font="system-ui",
    body_font="system-ui",
    base_size="18px",  # Larger base
    heading_scale=1.4,  # Aggressive scale
    line_height="1.3",  # Tighter line height
    body_line_height="1.4",
    heading_weight="900",  # Black weight
    section_heading_weight="900",  # All headings are black
    body_weight="500",
    letter_spacing="-0.025em",
    prose_max_width="56rem",  # Wide blocks
    badge_radius="0px",  # Sharp badges
)

TYPO_ELEGANT = TypographyStyle(
    name="elegant",
    heading_font="serif",  # Serif headings
    body_font="system-ui",
    base_size="16px",
    heading_scale=1.3,
    line_height="1.7",  # Generous line height
    body_line_height="1.8",  # Extra breathing room
    heading_weight="400",  # Light serif
    section_heading_weight="500",
    body_weight="400",
    letter_spacing="0.025em",  # Spaced out
    prose_max_width="36rem",  # Narrow, book-like measure
    badge_radius="4px",  # Subtle, refined badges
)

TYPO_RETRO = TypographyStyle(
    name="retro",
    heading_font="mono",  # Monospace
    body_font="system-ui",
    base_size="14px",  # Smaller, pixel-like
    heading_scale=1.1,  # Minimal scale
    line_height="1.4",
    body_line_height="1.5",
    heading_weight="700",
    section_heading_weight="700",
    body_weight="400",
    letter_spacing="normal",
    prose_max_width="40rem",
    badge_radius="0px",  # Sharp pixel badges
)

TYPO_ORGANIC = TypographyStyle(
    name="organic",
    heading_font="system-ui",
    body_font="system-ui",
    base_size="16px",
    heading_scale=1.25,
    line_height="1.6",
    heading_weight="600",
    body_weight="400",
    letter_spacing="normal",
)

TYPO_DJUST = TypographyStyle(
    name="djust",
    heading_font="Inter, sans-serif",
    body_font="Inter, sans-serif",
    base_size="16px",
    heading_scale=1.35,  # Bold scale — hero reaches ~5.4rem at 4 levels
    line_height="1.5",
    heading_weight="800",  # Extrabold headings like djust.org
    body_weight="400",
    letter_spacing="-0.025em",  # tracking-tight
)


# =============================================================================
# Layout Presets
# =============================================================================

LAYOUT_MATERIAL = LayoutStyle(
    name="material",
    space_unit="1rem",
    space_scale=2.0,
    border_radius_sm="4px",
    border_radius_md="8px",
    border_radius_lg="12px",
    button_shape="rounded",
    card_shape="rounded",
    input_shape="rounded",
    container_width="1200px",
    grid_gap="1.5rem",
    section_spacing="3rem",
    hero_padding_top="6rem",
    hero_padding_bottom="4rem",
    hero_line_height="1.2",
    hero_max_width="56rem",
)

LAYOUT_IOS = LayoutStyle(
    name="ios",
    space_unit="1rem",
    space_scale=1.5,
    border_radius_sm="8px",
    border_radius_md="12px",
    border_radius_lg="16px",
    button_shape="rounded",
    card_shape="rounded",
    input_shape="rounded",
    container_width="1100px",
    grid_gap="1.5rem",
    section_spacing="3rem",
    hero_padding_top="7rem",
    hero_padding_bottom="4rem",
    hero_line_height="1.15",
    hero_max_width="52rem",
)

LAYOUT_FLUENT = LayoutStyle(
    name="fluent",
    space_unit="1rem",
    space_scale=1.5,
    border_radius_sm="2px",
    border_radius_md="4px",
    border_radius_lg="8px",
    button_shape="rounded",
    card_shape="rounded",
    input_shape="rounded",
    container_width="1200px",
    grid_gap="1.5rem",
    section_spacing="3rem",
    hero_padding_top="6rem",
    hero_padding_bottom="4rem",
    hero_line_height="1.2",
    hero_max_width="56rem",
)

LAYOUT_PLAYFUL = LayoutStyle(
    name="playful",
    space_unit="1rem",
    space_scale=1.5,
    border_radius_sm="8px",
    border_radius_md="16px",
    border_radius_lg="24px",
    button_shape="pill",
    card_shape="rounded",
    input_shape="pill",
    container_width="1200px",
    grid_gap="2rem",
    section_spacing="4rem",
    hero_padding_top="8rem",
    hero_padding_bottom="5rem",
    hero_line_height="1.15",
    hero_max_width="60rem",
)

LAYOUT_CORPORATE = LayoutStyle(
    name="corporate",
    space_unit="1rem",
    space_scale=1.5,
    border_radius_sm="2px",
    border_radius_md="4px",
    border_radius_lg="8px",
    button_shape="rounded",
    card_shape="rounded",
    input_shape="rounded",
    container_width="1200px",
    grid_gap="1.5rem",
    section_spacing="3rem",
    hero_padding_top="6rem",
    hero_padding_bottom="3rem",
    hero_line_height="1.25",
    hero_max_width="56rem",
)

LAYOUT_DENSE = LayoutStyle(
    name="dense",
    space_unit="0.5rem",
    space_scale=1.5,
    border_radius_sm="2px",
    border_radius_md="2px",
    border_radius_lg="4px",
    button_shape="rounded",
    card_shape="sharp",
    input_shape="rounded",
    container_width="1400px",
    grid_gap="1rem",
    section_spacing="2rem",
    hero_padding_top="4rem",
    hero_padding_bottom="2rem",
    hero_line_height="1.0",
    hero_max_width="72rem",
)

LAYOUT_MINIMAL = LayoutStyle(
    name="minimal",
    space_unit="1rem",
    space_scale=1.5,
    border_radius_sm="2px",  # Subtle radius
    border_radius_md="4px",
    border_radius_lg="8px",
    button_shape="rounded",
    card_shape="rounded",
    input_shape="rounded",
    container_width="1000px",  # Narrower
    grid_gap="2rem",  # More space
    section_spacing="4rem",
    hero_padding_top="7rem",
    hero_padding_bottom="5rem",
    hero_line_height="1.2",
    hero_max_width="48rem",  # Narrow and focused
)

LAYOUT_BRUTALIST = LayoutStyle(
    name="brutalist",
    space_unit="1rem",
    space_scale=2.0,  # Bigger jumps
    border_radius_sm="0px",  # No radius
    border_radius_md="0px",
    border_radius_lg="0px",
    button_shape="sharp",
    card_shape="sharp",
    input_shape="sharp",
    container_width="1400px",  # Wide
    grid_gap="1rem",  # Tight spacing
    section_spacing="2rem",
    hero_padding_top="5rem",
    hero_padding_bottom="3rem",
    hero_line_height="1.0",  # Ultra tight
    hero_max_width="72rem",  # Full width impact
)

LAYOUT_ELEGANT = LayoutStyle(
    name="elegant",
    space_unit="1rem",
    space_scale=1.618,  # Golden ratio
    border_radius_sm="6px",
    border_radius_md="12px",
    border_radius_lg="20px",
    button_shape="rounded",
    card_shape="rounded",
    input_shape="rounded",
    container_width="900px",  # Conservative width
    grid_gap="3rem",  # Generous spacing
    section_spacing="5rem",
    hero_padding_top="10rem",  # Grand entrance
    hero_padding_bottom="6rem",
    hero_line_height="1.15",
    hero_max_width="48rem",  # Refined, narrow
)

LAYOUT_RETRO = LayoutStyle(
    name="retro",
    space_unit="8px",  # Pixel-based
    space_scale=2.0,
    border_radius_sm="0px",  # Sharp pixels
    border_radius_md="0px",
    border_radius_lg="0px",
    button_shape="sharp",
    card_shape="sharp",
    input_shape="sharp",
    container_width="1024px",  # Old screen size
    grid_gap="16px",
    section_spacing="32px",
    hero_padding_top="64px",  # Pixel-perfect
    hero_padding_bottom="48px",
    hero_line_height="1.1",
    hero_max_width="640px",  # Compact retro screen
)

LAYOUT_ORGANIC = LayoutStyle(
    name="organic",
    space_unit="1rem",
    space_scale=1.4,
    border_radius_sm="12px",  # Very rounded
    border_radius_md="20px",
    border_radius_lg="32px",
    button_shape="pill",  # Pill shapes
    card_shape="organic",
    input_shape="pill",
    container_width="1100px",
    grid_gap="1.5rem",
    section_spacing="3rem",
    hero_padding_top="7rem",
    hero_padding_bottom="4rem",
    hero_line_height="1.2",
    hero_max_width="56rem",
)

LAYOUT_DJUST = LayoutStyle(
    name="djust",
    space_unit="1rem",
    space_scale=1.5,
    border_radius_sm="0.375rem",  # rounded-md
    border_radius_md="0.5rem",  # rounded-lg
    border_radius_lg="0.75rem",  # rounded-xl (cards, code panels)
    button_shape="rounded",
    card_shape="organic",  # Uses border-radius-lg (0.75rem)
    input_shape="rounded",
    container_width="1280px",  # max-w-7xl like djust.org
    grid_gap="1.5rem",
    section_spacing="6rem",  # py-24 = 6rem — generous like djust.org
)


# =============================================================================
# Surface Presets
# =============================================================================

SURFACE_MATERIAL = SurfaceStyle(
    name="material",
    shadow_sm="0 2px 4px rgba(0,0,0,0.14), 0 3px 4px rgba(0,0,0,0.12)",
    shadow_md="0 4px 8px rgba(0,0,0,0.14), 0 6px 10px rgba(0,0,0,0.12)",
    shadow_lg="0 12px 17px rgba(0,0,0,0.14), 0 5px 22px rgba(0,0,0,0.12)",
    border_width="1px",
    border_style="solid",
    surface_treatment="flat",
    backdrop_blur="0px",
    noise_opacity=0.0,
)

SURFACE_IOS = SurfaceStyle(
    name="ios",
    shadow_sm="0 2px 4px rgba(0,0,0,0.06)",
    shadow_md="0 4px 8px rgba(0,0,0,0.08)",
    shadow_lg="0 8px 16px rgba(0,0,0,0.1)",
    border_width="1px",
    border_style="solid",
    surface_treatment="flat",
    backdrop_blur="0px",
    noise_opacity=0.0,
)

SURFACE_FLUENT = SurfaceStyle(
    name="fluent",
    shadow_sm="0 1.6px 3.6px rgba(0,0,0,0.13), 0 0.3px 0.9px rgba(0,0,0,0.11)",
    shadow_md="0 3.2px 7.2px rgba(0,0,0,0.13), 0 0.6px 1.8px rgba(0,0,0,0.11)",
    shadow_lg="0 6.4px 14.4px rgba(0,0,0,0.13), 0 1.2px 3.6px rgba(0,0,0,0.11)",
    border_width="1px",
    border_style="solid",
    surface_treatment="flat",
    backdrop_blur="0px",
    noise_opacity=0.0,
)

SURFACE_PLAYFUL = SurfaceStyle(
    name="playful",
    shadow_sm="0 2px 8px rgba(0,0,0,0.08)",
    shadow_md="0 4px 16px rgba(0,0,0,0.1)",
    shadow_lg="0 8px 32px rgba(0,0,0,0.12)",
    border_width="0px",
    border_style="none",
    surface_treatment="flat",
    backdrop_blur="0px",
    noise_opacity=0.0,
)

SURFACE_CORPORATE = SurfaceStyle(
    name="corporate",
    shadow_sm="0 1px 3px rgba(0,0,0,0.08)",
    shadow_md="0 2px 6px rgba(0,0,0,0.1)",
    shadow_lg="0 4px 12px rgba(0,0,0,0.12)",
    border_width="1px",
    border_style="solid",
    surface_treatment="flat",
    backdrop_blur="0px",
    noise_opacity=0.0,
)

SURFACE_DENSE = SurfaceStyle(
    name="dense",
    shadow_sm="0 1px 2px rgba(0,0,0,0.06)",
    shadow_md="0 1px 3px rgba(0,0,0,0.08)",
    shadow_lg="0 2px 6px rgba(0,0,0,0.1)",
    border_width="1px",
    border_style="solid",
    surface_treatment="flat",
    backdrop_blur="0px",
    noise_opacity=0.0,
)

SURFACE_MINIMAL = SurfaceStyle(
    name="minimal",
    shadow_sm="0 1px 2px rgba(0,0,0,0.05)",  # Very subtle
    shadow_md="0 2px 4px rgba(0,0,0,0.08)",
    shadow_lg="0 4px 8px rgba(0,0,0,0.12)",
    border_width="1px",
    border_style="solid",
    surface_treatment="flat",
    backdrop_blur="0px",
    noise_opacity=0.0,
)

SURFACE_BRUTALIST = SurfaceStyle(
    name="brutalist",
    shadow_sm="4px 4px 0px rgba(0,0,0,1)",  # Hard shadows
    shadow_md="8px 8px 0px rgba(0,0,0,1)",
    shadow_lg="12px 12px 0px rgba(0,0,0,1)",
    border_width="3px",  # Thick borders
    border_style="solid",
    surface_treatment="flat",
    backdrop_blur="0px",
    noise_opacity=0.0,
)

SURFACE_ELEGANT = SurfaceStyle(
    name="elegant",
    shadow_sm="0 2px 8px rgba(0,0,0,0.08)",
    shadow_md="0 8px 24px rgba(0,0,0,0.12)",
    shadow_lg="0 16px 40px rgba(0,0,0,0.16)",  # Soft, large shadows
    border_width="1px",
    border_style="solid",
    surface_treatment="gradient",  # Subtle gradients
    backdrop_blur="0px",
    noise_opacity=0.02,  # Subtle texture
)

SURFACE_RETRO = SurfaceStyle(
    name="retro",
    shadow_sm="2px 2px 0px rgba(0,0,0,0.8)",  # Pixel shadows
    shadow_md="4px 4px 0px rgba(0,0,0,0.8)",
    shadow_lg="6px 6px 0px rgba(0,0,0,0.8)",
    border_width="2px",
    border_style="solid",
    surface_treatment="textured",  # Dithered texture
    backdrop_blur="0px",
    noise_opacity=0.15,
)

SURFACE_ORGANIC = SurfaceStyle(
    name="organic",
    shadow_sm="0 3px 6px rgba(0,0,0,0.1)",
    shadow_md="0 6px 12px rgba(0,0,0,0.15)",
    shadow_lg="0 12px 24px rgba(0,0,0,0.2)",
    border_width="0px",  # No borders
    border_style="none",
    surface_treatment="glass",  # Soft glass effect
    backdrop_blur="8px",
    noise_opacity=0.0,
)

SURFACE_DJUST = SurfaceStyle(
    name="djust",
    shadow_sm="0 1px 3px rgba(0,0,0,0.3)",
    shadow_md="0 10px 15px -3px rgba(0,0,0,0.3), 0 4px 6px -2px rgba(0,0,0,0.2)",
    shadow_lg="0 20px 25px -5px rgba(0,0,0,0.3), 0 10px 10px -5px rgba(0,0,0,0.15)",
    border_width="1px",
    border_style="solid",
    surface_treatment="glass",  # Glass panels like djust.org
    backdrop_blur="12px",
    noise_opacity=0.0,
)


# DS-level ICON_*, ANIM_*, INTERACT_* instances moved to _constants.py
# to break the themes/_base -> presets -> themes cyclic import.
# They are re-exported via the imports at the top of this file.


# =============================================================================
# Complete Design Systems (Color-Independent)
# =============================================================================

DESIGN_MATERIAL = DesignSystem(
    name="material",
    display_name="Material Design",
    description="Google's Material Design with elevation-based hierarchy",
    category="professional",
    typography=TYPO_MATERIAL,
    layout=LAYOUT_MATERIAL,
    surface=SURFACE_MATERIAL,
    icons=ICON_MATERIAL,
    animation=ANIM_MATERIAL,
    interaction=INTERACT_MATERIAL,
)

DESIGN_IOS = DesignSystem(
    name="ios",
    display_name="iOS",
    description="Apple's iOS design language with fluid animations",
    category="elegant",
    typography=TYPO_IOS,
    layout=LAYOUT_IOS,
    surface=SURFACE_IOS,
    icons=ICON_IOS,
    animation=ANIM_IOS,
    interaction=INTERACT_IOS,
)

DESIGN_FLUENT = DesignSystem(
    name="fluent",
    display_name="Fluent Design",
    description="Microsoft's Fluent Design System with depth and motion",
    category="professional",
    typography=TYPO_FLUENT,
    layout=LAYOUT_FLUENT,
    surface=SURFACE_FLUENT,
    icons=ICON_FLUENT,
    animation=ANIM_FLUENT,
    interaction=INTERACT_FLUENT,
)

DESIGN_PLAYFUL = DesignSystem(
    name="playful",
    display_name="Playful",
    description="Fun, energetic design with bouncy animations and rounded shapes",
    category="playful",
    typography=TYPO_PLAYFUL,
    layout=LAYOUT_PLAYFUL,
    surface=SURFACE_PLAYFUL,
    icons=ICON_PLAYFUL,
    animation=ANIM_PLAYFUL,
    interaction=_INTERACT_PLAYFUL_DS,
)

DESIGN_CORPORATE = DesignSystem(
    name="corporate",
    display_name="Corporate",
    description="Professional, clean design for business applications",
    category="professional",
    typography=TYPO_CORPORATE,
    layout=LAYOUT_CORPORATE,
    surface=SURFACE_CORPORATE,
    icons=ICON_CORPORATE,
    animation=ANIM_CORPORATE,
    interaction=INTERACT_CORPORATE,
)

DESIGN_DENSE = DesignSystem(
    name="dense",
    display_name="Dense",
    description="Compact, information-dense design for data-heavy interfaces",
    category="minimal",
    typography=TYPO_DENSE,
    layout=LAYOUT_DENSE,
    surface=SURFACE_DENSE,
    icons=ICON_DENSE,
    animation=ANIM_DENSE,
    interaction=INTERACT_DENSE,
)

DESIGN_MINIMAL = DesignSystem(
    name="minimal",
    display_name="Minimal Clean",
    description="Pure, distraction-free design with maximum content focus",
    category="minimal",
    typography=TYPO_MINIMAL,
    layout=LAYOUT_MINIMAL,
    surface=SURFACE_MINIMAL,
    icons=ICON_MINIMAL,
    animation=ANIM_MINIMAL,
    interaction=_INTERACT_MINIMAL_DS,
)

DESIGN_BRUTALIST = DesignSystem(
    name="brutalist",
    display_name="Neo-Brutalist",
    description="Bold, aggressive design with sharp edges and high contrast",
    category="bold",
    typography=TYPO_BRUTALIST,
    layout=LAYOUT_BRUTALIST,
    surface=SURFACE_BRUTALIST,
    icons=ICON_BRUTALIST,
    animation=ANIM_BRUTALIST,
    interaction=INTERACT_BRUTALIST,
)

DESIGN_ELEGANT = DesignSystem(
    name="elegant",
    display_name="Refined Elegance",
    description="Sophisticated typography with generous spacing and subtle details",
    category="elegant",
    typography=TYPO_ELEGANT,
    layout=LAYOUT_ELEGANT,
    surface=SURFACE_ELEGANT,
    icons=ICON_ELEGANT,
    animation=ANIM_ELEGANT,
    interaction=INTERACT_ELEGANT,
)

DESIGN_RETRO = DesignSystem(
    name="retro",
    display_name="Pixel Perfect",
    description="Nostalgic pixel-art aesthetic with sharp edges and chunky shadows",
    category="retro",
    typography=TYPO_RETRO,
    layout=LAYOUT_RETRO,
    surface=SURFACE_RETRO,
    icons=ICON_RETRO,
    animation=ANIM_RETRO,
    interaction=INTERACT_RETRO,
)

DESIGN_ORGANIC = DesignSystem(
    name="organic",
    display_name="Natural Flow",
    description="Soft, rounded design inspired by natural forms and gentle motion",
    category="playful",
    typography=TYPO_ORGANIC,
    layout=LAYOUT_ORGANIC,
    surface=SURFACE_ORGANIC,
    icons=ICON_ORGANIC,
    animation=ANIM_ORGANIC,
    interaction=INTERACT_ORGANIC,
)

DESIGN_DJUST = DesignSystem(
    name="djust",
    display_name="djust.org",
    description="djust.org brand — dark, professional, with rust orange accents",
    category="professional",
    typography=TYPO_DJUST,
    layout=LAYOUT_DJUST,
    surface=SURFACE_DJUST,
    icons=ICON_DJUST,
    animation=ANIM_DJUST,
    interaction=INTERACT_DJUST,
)

# Design System Registry
# NOTE: "bauhaus" is added lazily in _ensure_theme_imports() to avoid circular imports.
DESIGN_SYSTEMS: Dict[str, DesignSystem] = {
    "material": DESIGN_MATERIAL,
    "ios": DESIGN_IOS,
    "fluent": DESIGN_FLUENT,
    "playful": DESIGN_PLAYFUL,
    "corporate": DESIGN_CORPORATE,
    "dense": DESIGN_DENSE,
    "minimalist": DESIGN_MINIMAL,
    "neo_brutalist": DESIGN_BRUTALIST,
    "elegant": DESIGN_ELEGANT,
    "retro": DESIGN_RETRO,
    "organic": DESIGN_ORGANIC,
    "djust": DESIGN_DJUST,
}


def get_design_system(name: str) -> Optional[DesignSystem]:
    """Get a design system by name (includes user-registered systems)."""
    _ensure_theme_imports()
    from .registry import get_registry

    reg = get_registry()
    return reg.get_theme(name) or DESIGN_SYSTEMS.get(name)


def get_all_design_systems() -> Dict[str, DesignSystem]:
    """Get all available design systems (built-in + user-registered)."""
    _ensure_theme_imports()
    from .registry import get_registry

    reg = get_registry()
    result = DESIGN_SYSTEMS.copy()
    result.update(reg.list_themes())
    return result


# Legacy dataclass types (PatternStyle, InteractionStyle,
# IllustrationStyle, ThemePack) moved to _types.py and shared
# pack-level instances (ICON_*, ANIM_*, PATTERN_*, INTERACT_*,
# ILLUST_*) moved to _constants.py to break the themes/_base ->
# presets -> themes cyclic import. Re-exported via imports above.


# ============================================
# Complete Theme Packs
# ============================================

PACK_CORPORATE = ThemePack(
    name="corporate",
    display_name="Corporate Professional",
    description="Clean, professional design for business applications",
    category="professional",
    design_theme="corporate",
    color_preset="blue",
    icon_style=ICON_OUTLINED,
    animation_style=ANIM_SMOOTH,
    pattern_style=PATTERN_GRID,
    interaction_style=INTERACT_SUBTLE,
    illustration_style=ILLUST_LINE,
)

PACK_PLAYFUL = ThemePack(
    name="playful",
    display_name="Playful Startup",
    description="Fun, energetic design with personality",
    category="playful",
    design_theme="playful",
    color_preset="purple",
    icon_style=ICON_ROUNDED,
    animation_style=ANIM_BOUNCY,
    pattern_style=PATTERN_DOTS,
    interaction_style=INTERACT_PLAYFUL,
    illustration_style=ILLUST_3D,
)

PACK_RETRO = ThemePack(
    name="retro",
    display_name="Retro Nostalgia",
    description="Classic 90s web aesthetic with pixel-perfect design",
    category="retro",
    design_theme="retro",
    color_preset="default",
    icon_style=ICON_SHARP,
    animation_style=ANIM_INSTANT,
    pattern_style=PATTERN_NOISE,
    interaction_style=INTERACT_MINIMAL,
    illustration_style=ILLUST_RETRO,
)

PACK_ELEGANT = ThemePack(
    name="elegant",
    display_name="Elegant Luxury",
    description="Sophisticated, premium design with refined details",
    category="elegant",
    design_theme="elegant",
    color_preset="default",
    icon_style=ICON_THIN,
    animation_style=ANIM_GENTLE,
    pattern_style=PATTERN_GRADIENT,
    interaction_style=INTERACT_SUBTLE,
    illustration_style=ILLUST_HAND_DRAWN,
)

PACK_BRUTALIST = ThemePack(
    name="brutalist",
    display_name="Neo-Brutalist Edge",
    description="Bold, dramatic design with high contrast",
    category="bold",
    design_theme="neo_brutalist",
    color_preset="default",
    icon_style=ICON_SHARP,
    animation_style=ANIM_SNAPPY,
    pattern_style=PATTERN_MINIMAL,
    interaction_style=INTERACT_BOLD,
    illustration_style=ILLUST_FLAT,
)

PACK_NATURE = ThemePack(
    name="nature",
    display_name="Nature Organic",
    description="Soft, natural design inspired by organic forms",
    category="playful",
    design_theme="organic",
    color_preset="green",
    icon_style=ICON_ROUNDED,
    animation_style=ANIM_GENTLE,
    pattern_style=PATTERN_DOTS,
    interaction_style=INTERACT_SUBTLE,
    illustration_style=ILLUST_HAND_DRAWN,
)

PACK_SUNSET = ThemePack(
    name="sunset",
    display_name="Golden Sunset",
    description="Warm, inviting design with golden hour color palette",
    category="elegant",
    design_theme="elegant",
    color_preset="sunset",
    icon_style=ICON_ROUNDED,
    animation_style=ANIM_GENTLE,
    pattern_style=PATTERN_GRADIENT,
    interaction_style=INTERACT_SUBTLE,
    illustration_style=ILLUST_HAND_DRAWN,
)

PACK_OCEAN = ThemePack(
    name="ocean",
    display_name="Ocean Depths",
    description="Calming, fluid design with deep blue and teal tones",
    category="minimal",
    design_theme="material",
    color_preset="ocean",
    icon_style=ICON_FILLED,
    animation_style=ANIM_SMOOTH,
    pattern_style=PATTERN_MINIMAL,
    interaction_style=INTERACT_SUBTLE,
    illustration_style=ILLUST_FLAT,
)

PACK_METALLIC = ThemePack(
    name="metallic",
    display_name="Metallic Industrial",
    description="Sleek, modern design with industrial metallic aesthetics",
    category="professional",
    design_theme="corporate",
    color_preset="metallic",
    icon_style=ICON_OUTLINED,
    animation_style=ANIM_SMOOTH,
    pattern_style=PATTERN_NOISE,
    interaction_style=INTERACT_MINIMAL,
    illustration_style=ILLUST_LINE,
)

# =============================================================================
# Theme packs and design systems from per-theme files (lazy-loaded)
# =============================================================================
# Per-theme files import shared presets via themes/_base.py -> theme_packs.py,
# creating a circular import if we import them at module level. Instead, we
# populate the registries on first access.

# Theme Pack Registry — inline packs added immediately, per-theme packs lazily.
THEME_PACKS: Dict[str, ThemePack] = {
    "corporate": PACK_CORPORATE,
    "playful": PACK_PLAYFUL,
    "retro": PACK_RETRO,
    "elegant": PACK_ELEGANT,
    "brutalist": PACK_BRUTALIST,
    "nature": PACK_NATURE,
    "sunset": PACK_SUNSET,
    "ocean": PACK_OCEAN,
    "metallic": PACK_METALLIC,
}

_theme_imports_done = False


def _ensure_theme_imports() -> None:
    """Lazily import theme packs and design systems from per-theme files."""
    global _theme_imports_done
    if _theme_imports_done:
        return
    _theme_imports_done = True

    from .themes.amber import PACK as _PACK_AMBER, DESIGN_SYSTEM as _DESIGN_AMBER
    from .themes.aurora import PACK as _PACK_AURORA, DESIGN_SYSTEM as _DESIGN_AURORA
    from .themes.bauhaus import PACK as _PACK_BAUHAUS, DESIGN_SYSTEM as _DESIGN_BAUHAUS
    from .themes.blue import PACK as _PACK_BLUE, DESIGN_SYSTEM as _DESIGN_BLUE
    from .themes.catppuccin import PACK as _PACK_CATPPUCCIN, DESIGN_SYSTEM as _DESIGN_CATPPUCCIN
    from .themes.cyberdeck import PACK as _PACK_CYBERDECK, DESIGN_SYSTEM as _DESIGN_CYBERDECK
    from .themes.cyberpunk import PACK as _PACK_CYBERPUNK, DESIGN_SYSTEM as _DESIGN_CYBERPUNK
    from .themes.default import PACK as _PACK_DEFAULT, DESIGN_SYSTEM as _DESIGN_DEFAULT
    from .themes.djust import PACK as _PACK_DJUST
    from .themes.dracula import PACK as _PACK_DRACULA, DESIGN_SYSTEM as _DESIGN_DRACULA
    from .themes.ember import PACK as _PACK_EMBER, DESIGN_SYSTEM as _DESIGN_EMBER
    from .themes.forest import PACK as _PACK_FOREST, DESIGN_SYSTEM as _DESIGN_FOREST
    from .themes.green import PACK as _PACK_GREEN, DESIGN_SYSTEM as _DESIGN_GREEN
    from .themes.gruvbox import PACK as _PACK_GRUVBOX, DESIGN_SYSTEM as _DESIGN_GRUVBOX
    from .themes.high_contrast import (
        PACK as _PACK_HIGH_CONTRAST,
        DESIGN_SYSTEM as _DESIGN_HIGH_CONTRAST,
    )
    from .themes.ink import PACK as _PACK_INK, DESIGN_SYSTEM as _DESIGN_INK
    from .themes.mono import PACK as _PACK_MONO, DESIGN_SYSTEM as _DESIGN_MONO
    from .themes.natural20 import PACK as _PACK_NATURAL20, DESIGN_SYSTEM as _DESIGN_NATURAL20
    from .themes.nebula import PACK as _PACK_NEBULA, DESIGN_SYSTEM as _DESIGN_NEBULA
    from .themes.neon_noir import PACK as _PACK_NEON_NOIR, DESIGN_SYSTEM as _DESIGN_NEON_NOIR
    from .themes.nord import PACK as _PACK_NORD, DESIGN_SYSTEM as _DESIGN_NORD
    from .themes.ocean_deep import PACK as _PACK_OCEAN_DEEP, DESIGN_SYSTEM as _DESIGN_OCEAN_DEEP
    from .themes.orange import PACK as _PACK_ORANGE, DESIGN_SYSTEM as _DESIGN_ORANGE
    from .themes.outrun import PACK as _PACK_OUTRUN, DESIGN_SYSTEM as _DESIGN_OUTRUN
    from .themes.paper import PACK as _PACK_PAPER, DESIGN_SYSTEM as _DESIGN_PAPER
    from .themes.purple import PACK as _PACK_PURPLE, DESIGN_SYSTEM as _DESIGN_PURPLE
    from .themes.rose import PACK as _PACK_ROSE, DESIGN_SYSTEM as _DESIGN_ROSE
    from .themes.rose_pine import PACK as _PACK_ROSE_PINE, DESIGN_SYSTEM as _DESIGN_ROSE_PINE
    from .themes.shadcn import PACK as _PACK_SHADCN, DESIGN_SYSTEM as _DESIGN_SHADCN
    from .themes.slate import PACK as _PACK_SLATE, DESIGN_SYSTEM as _DESIGN_SLATE
    from .themes.solarized import PACK as _PACK_SOLARIZED, DESIGN_SYSTEM as _DESIGN_SOLARIZED
    from .themes.solarpunk import PACK as _PACK_SOLARPUNK, DESIGN_SYSTEM as _DESIGN_SOLARPUNK
    from .themes.stripe import PACK as _PACK_STRIPE, DESIGN_SYSTEM as _DESIGN_STRIPE
    from .themes.synthwave import PACK as _PACK_SYNTHWAVE, DESIGN_SYSTEM as _DESIGN_SYNTHWAVE
    from .themes.tokyo_night import PACK as _PACK_TOKYO_NIGHT, DESIGN_SYSTEM as _DESIGN_TOKYO_NIGHT
    from .themes.linear import PACK as _PACK_LINEAR, DESIGN_SYSTEM as _DESIGN_LINEAR
    from .themes.notion import PACK as _PACK_NOTION, DESIGN_SYSTEM as _DESIGN_NOTION
    from .themes.vercel import PACK as _PACK_VERCEL, DESIGN_SYSTEM as _DESIGN_VERCEL
    from .themes.github import PACK as _PACK_GITHUB, DESIGN_SYSTEM as _DESIGN_GITHUB
    from .themes.art_deco import PACK as _PACK_ART_DECO, DESIGN_SYSTEM as _DESIGN_ART_DECO
    from .themes.handcraft import PACK as _PACK_HANDCRAFT, DESIGN_SYSTEM as _DESIGN_HANDCRAFT
    from .themes.terminal import PACK as _PACK_TERMINAL, DESIGN_SYSTEM as _DESIGN_TERMINAL
    from .themes.docs import PACK as _PACK_DOCS, DESIGN_SYSTEM as _DESIGN_DOCS
    from .themes.magazine import PACK as _PACK_MAGAZINE, DESIGN_SYSTEM as _DESIGN_MAGAZINE
    from .themes.swiss import PACK as _PACK_SWISS, DESIGN_SYSTEM as _DESIGN_SWISS
    from .themes.candy import PACK as _PACK_CANDY, DESIGN_SYSTEM as _DESIGN_CANDY
    from .themes.retro_computing import (
        PACK as _PACK_RETRO_COMPUTING,
        DESIGN_SYSTEM as _DESIGN_RETRO_COMPUTING,
    )
    from .themes.medical import PACK as _PACK_MEDICAL, DESIGN_SYSTEM as _DESIGN_MEDICAL
    from .themes.legal import PACK as _PACK_LEGAL, DESIGN_SYSTEM as _DESIGN_LEGAL
    from .themes.midnight import PACK as _PACK_MIDNIGHT, DESIGN_SYSTEM as _DESIGN_MIDNIGHT
    from .themes.sunrise import PACK as _PACK_SUNRISE, DESIGN_SYSTEM as _DESIGN_SUNRISE
    from .themes.forest_floor import (
        PACK as _PACK_FOREST_FLOOR,
        DESIGN_SYSTEM as _DESIGN_FOREST_FLOOR,
    )
    from .themes.dashboard import PACK as _PACK_DASHBOARD, DESIGN_SYSTEM as _DESIGN_DASHBOARD
    from .themes.one_dark import PACK as _PACK_ONE_DARK, DESIGN_SYSTEM as _DESIGN_ONE_DARK
    from .themes.monokai import PACK as _PACK_MONOKAI, DESIGN_SYSTEM as _DESIGN_MONOKAI
    from .themes.ayu import PACK as _PACK_AYU, DESIGN_SYSTEM as _DESIGN_AYU
    from .themes.kanagawa import PACK as _PACK_KANAGAWA, DESIGN_SYSTEM as _DESIGN_KANAGAWA
    from .themes.everforest import PACK as _PACK_EVERFOREST, DESIGN_SYSTEM as _DESIGN_EVERFOREST
    from .themes.poimandres import PACK as _PACK_POIMANDRES, DESIGN_SYSTEM as _DESIGN_POIMANDRES
    from .themes.tailwind import PACK as _PACK_TAILWIND, DESIGN_SYSTEM as _DESIGN_TAILWIND
    from .themes.supabase import PACK as _PACK_SUPABASE, DESIGN_SYSTEM as _DESIGN_SUPABASE
    from .themes.raycast import PACK as _PACK_RAYCAST, DESIGN_SYSTEM as _DESIGN_RAYCAST
    from .themes.adaptive import PACK as _PACK_ADAPTIVE, DESIGN_SYSTEM as _DESIGN_ADAPTIVE

    THEME_PACKS.update(
        {
            "amber": _PACK_AMBER,
            "aurora": _PACK_AURORA,
            "bauhaus": _PACK_BAUHAUS,
            "blue": _PACK_BLUE,
            "catppuccin": _PACK_CATPPUCCIN,
            "cyberdeck": _PACK_CYBERDECK,
            "cyberpunk": _PACK_CYBERPUNK,
            "default": _PACK_DEFAULT,
            "djust": _PACK_DJUST,
            "dracula": _PACK_DRACULA,
            "ember": _PACK_EMBER,
            "forest": _PACK_FOREST,
            "green": _PACK_GREEN,
            "gruvbox": _PACK_GRUVBOX,
            "high_contrast": _PACK_HIGH_CONTRAST,
            "ink": _PACK_INK,
            "mono": _PACK_MONO,
            "natural20": _PACK_NATURAL20,
            "nebula": _PACK_NEBULA,
            "neon_noir": _PACK_NEON_NOIR,
            "nord": _PACK_NORD,
            "ocean_deep": _PACK_OCEAN_DEEP,
            "orange": _PACK_ORANGE,
            "outrun": _PACK_OUTRUN,
            "paper": _PACK_PAPER,
            "purple": _PACK_PURPLE,
            "rose": _PACK_ROSE,
            "rose_pine": _PACK_ROSE_PINE,
            "shadcn": _PACK_SHADCN,
            "slate": _PACK_SLATE,
            "solarized": _PACK_SOLARIZED,
            "solarpunk": _PACK_SOLARPUNK,
            "stripe": _PACK_STRIPE,
            "synthwave": _PACK_SYNTHWAVE,
            "tokyo_night": _PACK_TOKYO_NIGHT,
            "linear": _PACK_LINEAR,
            "notion": _PACK_NOTION,
            "vercel": _PACK_VERCEL,
            "github": _PACK_GITHUB,
            "art_deco": _PACK_ART_DECO,
            "handcraft": _PACK_HANDCRAFT,
            "terminal": _PACK_TERMINAL,
            "docs": _PACK_DOCS,
            "magazine": _PACK_MAGAZINE,
            "swiss": _PACK_SWISS,
            "candy": _PACK_CANDY,
            "retro_computing": _PACK_RETRO_COMPUTING,
            "medical": _PACK_MEDICAL,
            "legal": _PACK_LEGAL,
            "midnight": _PACK_MIDNIGHT,
            "sunrise": _PACK_SUNRISE,
            "forest_floor": _PACK_FOREST_FLOOR,
            "dashboard": _PACK_DASHBOARD,
            "one_dark": _PACK_ONE_DARK,
            "monokai": _PACK_MONOKAI,
            "ayu": _PACK_AYU,
            "kanagawa": _PACK_KANAGAWA,
            "everforest": _PACK_EVERFOREST,
            "poimandres": _PACK_POIMANDRES,
            "tailwind": _PACK_TAILWIND,
            "supabase": _PACK_SUPABASE,
            "raycast": _PACK_RAYCAST,
            "adaptive": _PACK_ADAPTIVE,
        }
    )

    DESIGN_SYSTEMS["amber"] = _DESIGN_AMBER
    DESIGN_SYSTEMS["aurora"] = _DESIGN_AURORA
    DESIGN_SYSTEMS["bauhaus"] = _DESIGN_BAUHAUS
    DESIGN_SYSTEMS["blue"] = _DESIGN_BLUE
    DESIGN_SYSTEMS["catppuccin"] = _DESIGN_CATPPUCCIN
    DESIGN_SYSTEMS["cyberdeck"] = _DESIGN_CYBERDECK
    DESIGN_SYSTEMS["cyberpunk"] = _DESIGN_CYBERPUNK
    DESIGN_SYSTEMS["default"] = _DESIGN_DEFAULT
    DESIGN_SYSTEMS["dracula"] = _DESIGN_DRACULA
    DESIGN_SYSTEMS["ember"] = _DESIGN_EMBER
    DESIGN_SYSTEMS["forest"] = _DESIGN_FOREST
    DESIGN_SYSTEMS["green"] = _DESIGN_GREEN
    DESIGN_SYSTEMS["gruvbox"] = _DESIGN_GRUVBOX
    DESIGN_SYSTEMS["high_contrast"] = _DESIGN_HIGH_CONTRAST
    DESIGN_SYSTEMS["ink"] = _DESIGN_INK
    DESIGN_SYSTEMS["mono"] = _DESIGN_MONO
    DESIGN_SYSTEMS["natural20"] = _DESIGN_NATURAL20
    DESIGN_SYSTEMS["nebula"] = _DESIGN_NEBULA
    DESIGN_SYSTEMS["neon_noir"] = _DESIGN_NEON_NOIR
    DESIGN_SYSTEMS["nord"] = _DESIGN_NORD
    DESIGN_SYSTEMS["ocean_deep"] = _DESIGN_OCEAN_DEEP
    DESIGN_SYSTEMS["orange"] = _DESIGN_ORANGE
    DESIGN_SYSTEMS["outrun"] = _DESIGN_OUTRUN
    DESIGN_SYSTEMS["paper"] = _DESIGN_PAPER
    DESIGN_SYSTEMS["purple"] = _DESIGN_PURPLE
    DESIGN_SYSTEMS["rose"] = _DESIGN_ROSE
    DESIGN_SYSTEMS["rose_pine"] = _DESIGN_ROSE_PINE
    DESIGN_SYSTEMS["shadcn"] = _DESIGN_SHADCN
    DESIGN_SYSTEMS["slate"] = _DESIGN_SLATE
    DESIGN_SYSTEMS["solarized"] = _DESIGN_SOLARIZED
    DESIGN_SYSTEMS["solarpunk"] = _DESIGN_SOLARPUNK
    DESIGN_SYSTEMS["stripe"] = _DESIGN_STRIPE
    DESIGN_SYSTEMS["synthwave"] = _DESIGN_SYNTHWAVE
    DESIGN_SYSTEMS["tokyo_night"] = _DESIGN_TOKYO_NIGHT
    DESIGN_SYSTEMS["linear"] = _DESIGN_LINEAR
    DESIGN_SYSTEMS["notion"] = _DESIGN_NOTION
    DESIGN_SYSTEMS["vercel"] = _DESIGN_VERCEL
    DESIGN_SYSTEMS["github"] = _DESIGN_GITHUB
    DESIGN_SYSTEMS["art_deco"] = _DESIGN_ART_DECO
    DESIGN_SYSTEMS["handcraft"] = _DESIGN_HANDCRAFT
    DESIGN_SYSTEMS["terminal"] = _DESIGN_TERMINAL
    DESIGN_SYSTEMS["docs"] = _DESIGN_DOCS
    DESIGN_SYSTEMS["magazine"] = _DESIGN_MAGAZINE
    DESIGN_SYSTEMS["swiss"] = _DESIGN_SWISS
    DESIGN_SYSTEMS["candy"] = _DESIGN_CANDY
    DESIGN_SYSTEMS["retro_computing"] = _DESIGN_RETRO_COMPUTING
    DESIGN_SYSTEMS["medical"] = _DESIGN_MEDICAL
    DESIGN_SYSTEMS["legal"] = _DESIGN_LEGAL
    DESIGN_SYSTEMS["midnight"] = _DESIGN_MIDNIGHT
    DESIGN_SYSTEMS["sunrise"] = _DESIGN_SUNRISE
    DESIGN_SYSTEMS["forest_floor"] = _DESIGN_FOREST_FLOOR
    DESIGN_SYSTEMS["dashboard"] = _DESIGN_DASHBOARD
    DESIGN_SYSTEMS["one_dark"] = _DESIGN_ONE_DARK
    DESIGN_SYSTEMS["monokai"] = _DESIGN_MONOKAI
    DESIGN_SYSTEMS["ayu"] = _DESIGN_AYU
    DESIGN_SYSTEMS["kanagawa"] = _DESIGN_KANAGAWA
    DESIGN_SYSTEMS["everforest"] = _DESIGN_EVERFOREST
    DESIGN_SYSTEMS["poimandres"] = _DESIGN_POIMANDRES
    DESIGN_SYSTEMS["tailwind"] = _DESIGN_TAILWIND
    DESIGN_SYSTEMS["supabase"] = _DESIGN_SUPABASE
    DESIGN_SYSTEMS["raycast"] = _DESIGN_RAYCAST
    DESIGN_SYSTEMS["adaptive"] = _DESIGN_ADAPTIVE


def get_theme_pack(name: str) -> Optional[ThemePack]:
    """Get a theme pack by name (includes user-registered packs)."""
    _ensure_theme_imports()
    from .registry import get_registry

    reg = get_registry()
    return reg.get_pack(name) or THEME_PACKS.get(name)


def get_all_theme_packs() -> Dict[str, ThemePack]:
    """Get all available theme packs (built-in + user-registered)."""
    _ensure_theme_imports()
    from .registry import get_registry

    reg = get_registry()
    result = THEME_PACKS.copy()
    result.update(reg.list_packs())
    return result


# Backward-compat re-exports. These names used to be defined in this module;
# they now live in ``_types.py`` / ``_constants.py`` but must remain importable
# from ``djust.theming.theme_packs`` for third-party code. Listing them in
# ``__all__`` also silences ruff's F401 on the top-level re-export imports.
__all__ = [
    # Types
    "AnimationStyle",
    "DesignSystem",
    "IconStyle",
    "IllustrationStyle",
    "InteractionStyle",
    "LayoutStyle",
    "PatternStyle",
    "SurfaceStyle",
    "ThemePack",
    "TypographyStyle",
    # Pack-level shared instances
    "ANIM_BOUNCY",
    "ANIM_GENTLE",
    "ANIM_INSTANT",
    "ANIM_SMOOTH",
    "ANIM_SNAPPY",
    "ICON_FILLED",
    "ICON_OUTLINED",
    "ICON_ROUNDED",
    "ICON_SHARP",
    "ICON_THIN",
    "ILLUST_3D",
    "ILLUST_FLAT",
    "ILLUST_HAND_DRAWN",
    "ILLUST_LINE",
    "ILLUST_RETRO",
    "INTERACT_BOLD",
    "INTERACT_MINIMAL",
    "INTERACT_PLAYFUL",
    "INTERACT_SUBTLE",
    "PATTERN_DOTS",
    "PATTERN_GLASS",
    "PATTERN_GRADIENT",
    "PATTERN_GRID",
    "PATTERN_MINIMAL",
    "PATTERN_NOISE",
    # Design-system-level shared instances
    "ANIM_BRUTALIST",
    "ANIM_CORPORATE",
    "ANIM_DENSE",
    "ANIM_DJUST",
    "ANIM_ELEGANT",
    "ANIM_FLUENT",
    "ANIM_IOS",
    "ANIM_MATERIAL",
    "ANIM_MINIMAL",
    "ANIM_ORGANIC",
    "ANIM_PLAYFUL",
    "ANIM_RETRO",
    "ICON_BRUTALIST",
    "ICON_CORPORATE",
    "ICON_DENSE",
    "ICON_DJUST",
    "ICON_ELEGANT",
    "ICON_FLUENT",
    "ICON_IOS",
    "ICON_MATERIAL",
    "ICON_MINIMAL",
    "ICON_ORGANIC",
    "ICON_PLAYFUL",
    "ICON_RETRO",
    "INTERACT_BRUTALIST",
    "INTERACT_CORPORATE",
    "INTERACT_DENSE",
    "INTERACT_DJUST",
    "INTERACT_ELEGANT",
    "INTERACT_FLUENT",
    "INTERACT_IOS",
    "INTERACT_MATERIAL",
    "INTERACT_ORGANIC",
    "INTERACT_RETRO",
    # Design systems and packs
    "DESIGN_BRUTALIST",
    "DESIGN_CORPORATE",
    "DESIGN_DENSE",
    "DESIGN_DJUST",
    "DESIGN_ELEGANT",
    "DESIGN_FLUENT",
    "DESIGN_IOS",
    "DESIGN_MATERIAL",
    "DESIGN_MINIMAL",
    "DESIGN_ORGANIC",
    "DESIGN_PLAYFUL",
    "DESIGN_RETRO",
    "DESIGN_SYSTEMS",
    "PACK_BRUTALIST",
    "PACK_CORPORATE",
    "PACK_ELEGANT",
    "PACK_METALLIC",
    "PACK_NATURE",
    "PACK_OCEAN",
    "PACK_PLAYFUL",
    "PACK_RETRO",
    "PACK_SUNSET",
    "THEME_PACKS",
    # Registry helpers
    "get_all_design_systems",
    "get_all_theme_packs",
    "get_design_system",
    "get_theme_pack",
]
