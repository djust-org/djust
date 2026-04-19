"""
Design Systems - Pure visual design without color dependency.

A DesignSystem defines the non-color aspects of UI:
- Typography (fonts, sizes, spacing)
- Layout (grid systems, component shapes)
- Visual patterns (borders, shadows, textures)
- Animation behaviors
- Interaction feedback
"""

from dataclasses import dataclass
from typing import Dict, Optional


@dataclass
class TypographyStyle:
    """Typography configuration."""

    name: str

    # Font families
    heading_font: str = "system-ui"  # "system-ui", "serif", "mono", "display"
    body_font: str = "system-ui"

    # Scale and sizing
    base_size: str = "16px"
    heading_scale: float = 1.25  # Multiplier between heading levels
    line_height: str = "1.5"
    body_line_height: str = "1.6"  # Relaxed line-height for body/paragraph text

    # Weight and style
    heading_weight: str = "600"  # "300", "400", "500", "600", "700", "800", "900"
    section_heading_weight: str = "700"  # Weight for section h2s (often lighter than hero)
    body_weight: str = "400"
    letter_spacing: str = "normal"  # CSS value: "normal", "-0.025em", "0.025em"

    # Measure
    prose_max_width: str = "42rem"  # Max width for readable text blocks
    badge_radius: str = "9999px"  # Badge border-radius (pill by default)


@dataclass
class LayoutStyle:
    """Layout and spacing configuration."""

    name: str

    # Spacing system
    space_unit: str = "1rem"  # Base unit for spacing
    space_scale: float = 1.5  # Ratio between spacing levels

    # Border radius system
    border_radius_sm: str = "0.25rem"
    border_radius_md: str = "0.5rem"
    border_radius_lg: str = "1rem"

    # Component shapes
    button_shape: str = "rounded"  # "sharp", "rounded", "pill", "organic"
    card_shape: str = "rounded"
    input_shape: str = "rounded"

    # Grid and layout
    container_width: str = "1200px"
    grid_gap: str = "1.5rem"
    section_spacing: str = "3rem"

    # Hero section
    hero_padding_top: str = "8rem"
    hero_padding_bottom: str = "5rem"
    hero_line_height: str = "1.1"
    hero_max_width: str = "64rem"  # Content width within hero


@dataclass
class SurfaceStyle:
    """Surface treatments and visual depth."""

    name: str

    # Shadow system
    shadow_sm: str = "0 1px 2px rgba(0,0,0,0.1)"
    shadow_md: str = "0 4px 6px rgba(0,0,0,0.1)"
    shadow_lg: str = "0 10px 15px rgba(0,0,0,0.1)"

    # Border system
    border_width: str = "1px"
    border_style: str = "solid"  # "solid", "dashed", "dotted", "none"

    # Background treatments
    surface_treatment: str = "flat"  # "flat", "glass", "textured", "gradient"
    backdrop_blur: str = "0px"
    noise_opacity: float = 0.0


@dataclass
class IconStyle:
    """Icon styling configuration."""

    name: str
    style: str  # "outlined", "filled", "rounded", "sharp", "duotone"
    weight: str  # "thin", "regular", "bold"
    size_scale: float = 1.0  # Multiplier for icon sizes

    # CSS properties
    stroke_width: str = "2"
    corner_rounding: str = "0"  # For rounded style


@dataclass
class AnimationStyle:
    """Animation and motion configuration."""

    name: str

    # Entrance/Exit
    entrance_effect: str = "fade"  # "fade", "slide", "scale", "bounce", "none"
    exit_effect: str = "fade"

    # Hover behaviors
    hover_effect: str = "lift"  # "lift", "scale", "glow", "none"
    hover_scale: float = 1.02
    hover_translate_y: str = "-2px"

    # Click feedback
    click_effect: str = "ripple"  # "ripple", "pulse", "bounce", "none"

    # Loading states
    loading_style: str = "spinner"  # "spinner", "skeleton", "progress", "pulse"

    # Transition characteristics
    transition_style: str = "smooth"  # "smooth", "snappy", "bouncy", "instant"
    duration_fast: str = "0.15s"
    duration_normal: str = "0.3s"
    duration_slow: str = "0.5s"
    easing: str = "cubic-bezier(0.4, 0, 0.2, 1)"


@dataclass
class InteractionStyle:
    """User interaction feedback."""

    name: str

    # Hover effects
    button_hover: str = "lift"  # "lift", "scale", "glow", "darken", "none"
    link_hover: str = "underline"  # "underline", "color", "background", "none"
    card_hover: str = "lift"  # "lift", "scale", "border", "shadow", "none"

    # Focus effects
    focus_style: str = "ring"  # "ring", "outline", "glow", "underline"
    focus_ring_width: str = "2px"


@dataclass
class DesignSystem:
    """
    Complete design system - all visual aspects EXCEPT colors.

    This allows any design system to be combined with any color preset.
    """

    name: str
    display_name: str
    description: str
    category: str  # "minimal", "bold", "elegant", "playful", "industrial"

    # Core styling components
    typography: TypographyStyle
    layout: LayoutStyle
    surface: SurfaceStyle
    icons: IconStyle
    animation: AnimationStyle
    interaction: InteractionStyle


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


# =============================================================================
# Icon Style Presets
# =============================================================================

ICON_MATERIAL = IconStyle(
    name="material",
    style="filled",
    weight="regular",
    size_scale=1.0,
    stroke_width="2",
    corner_rounding="0px",
)

ICON_IOS = IconStyle(
    name="ios",
    style="outlined",
    weight="thin",
    size_scale=1.0,
    stroke_width="1.5",
    corner_rounding="4px",
)

ICON_FLUENT = IconStyle(
    name="fluent",
    style="outlined",
    weight="regular",
    size_scale=1.0,
    stroke_width="2",
    corner_rounding="0px",
)

ICON_PLAYFUL = IconStyle(
    name="playful",
    style="rounded",
    weight="regular",
    size_scale=1.1,
    stroke_width="2",
    corner_rounding="8px",
)

ICON_CORPORATE = IconStyle(
    name="corporate",
    style="outlined",
    weight="regular",
    size_scale=1.0,
    stroke_width="2",
    corner_rounding="0px",
)

ICON_DENSE = IconStyle(
    name="dense",
    style="outlined",
    weight="thin",
    size_scale=0.85,
    stroke_width="1.5",
    corner_rounding="0px",
)

ICON_MINIMAL = IconStyle(
    name="minimal",
    style="outlined",
    weight="thin",
    size_scale=0.9,  # Smaller icons
    stroke_width="1.5",
    corner_rounding="2px",
)

ICON_BRUTALIST = IconStyle(
    name="brutalist",
    style="filled",
    weight="bold",
    size_scale=1.2,  # Larger, bold icons
    stroke_width="3",
    corner_rounding="0px",
)

ICON_ELEGANT = IconStyle(
    name="elegant",
    style="outlined",
    weight="thin",
    size_scale=1.0,
    stroke_width="1",  # Very thin strokes
    corner_rounding="4px",
)

ICON_RETRO = IconStyle(
    name="retro",
    style="filled",
    weight="regular",
    size_scale=1.0,
    stroke_width="2",
    corner_rounding="0px",  # Sharp pixels
)

ICON_ORGANIC = IconStyle(
    name="organic",
    style="rounded",
    weight="regular",
    size_scale=1.1,
    stroke_width="2",
    corner_rounding="8px",  # Very rounded
)

ICON_DJUST = IconStyle(
    name="djust",
    style="outlined",
    weight="regular",
    size_scale=1.0,
    stroke_width="2",
    corner_rounding="0px",
)


# =============================================================================
# Animation Style Presets
# =============================================================================

ANIM_MATERIAL = AnimationStyle(
    name="material",
    entrance_effect="fade",
    exit_effect="fade",
    hover_effect="lift",
    hover_scale=1.02,
    hover_translate_y="-2px",
    click_effect="ripple",
    loading_style="spinner",
    transition_style="smooth",
    duration_fast="0.1s",
    duration_normal="0.2s",
    duration_slow="0.3s",
    easing="cubic-bezier(0.4, 0, 0.2, 1)",
)

ANIM_IOS = AnimationStyle(
    name="ios",
    entrance_effect="fade",
    exit_effect="fade",
    hover_effect="scale",
    hover_scale=1.05,
    hover_translate_y="0px",
    click_effect="none",
    loading_style="spinner",
    transition_style="snappy",
    duration_fast="0.15s",
    duration_normal="0.25s",
    duration_slow="0.35s",
    easing="cubic-bezier(0.42, 0, 0.58, 1)",
)

ANIM_FLUENT = AnimationStyle(
    name="fluent",
    entrance_effect="fade",
    exit_effect="fade",
    hover_effect="lift",
    hover_scale=1.02,
    hover_translate_y="-2px",
    click_effect="ripple",
    loading_style="progress",
    transition_style="smooth",
    duration_fast="0.167s",
    duration_normal="0.25s",
    duration_slow="0.367s",
    easing="cubic-bezier(0.1, 0.9, 0.2, 1)",
)

ANIM_PLAYFUL = AnimationStyle(
    name="playful",
    entrance_effect="bounce",
    exit_effect="scale",
    hover_effect="scale",
    hover_scale=1.05,
    hover_translate_y="0px",
    click_effect="bounce",
    loading_style="pulse",
    transition_style="bouncy",
    duration_fast="0.2s",
    duration_normal="0.3s",
    duration_slow="0.5s",
    easing="cubic-bezier(0.68, -0.55, 0.265, 1.55)",
)

ANIM_CORPORATE = AnimationStyle(
    name="corporate",
    entrance_effect="fade",
    exit_effect="fade",
    hover_effect="lift",
    hover_scale=1.01,
    hover_translate_y="-1px",
    click_effect="none",
    loading_style="progress",
    transition_style="smooth",
    duration_fast="0.15s",
    duration_normal="0.2s",
    duration_slow="0.3s",
    easing="cubic-bezier(0.4, 0, 0.2, 1)",
)

ANIM_DENSE = AnimationStyle(
    name="dense",
    entrance_effect="fade",
    exit_effect="fade",
    hover_effect="none",
    hover_scale=1.0,
    hover_translate_y="0px",
    click_effect="none",
    loading_style="progress",
    transition_style="instant",
    duration_fast="0.05s",
    duration_normal="0.1s",
    duration_slow="0.15s",
    easing="linear",
)

ANIM_MINIMAL = AnimationStyle(
    name="minimal",
    entrance_effect="fade",
    exit_effect="fade",
    hover_effect="none",  # No hover effects
    hover_scale=1.0,
    hover_translate_y="0px",
    click_effect="none",
    loading_style="progress",
    transition_style="smooth",
    duration_fast="0.2s",
    duration_normal="0.3s",
    duration_slow="0.4s",
    easing="ease-out",
)

ANIM_BRUTALIST = AnimationStyle(
    name="brutalist",
    entrance_effect="none",  # Instant appearance
    exit_effect="none",
    hover_effect="scale",
    hover_scale=1.05,  # Bold scale
    hover_translate_y="0px",
    click_effect="pulse",
    loading_style="spinner",
    transition_style="instant",
    duration_fast="0.05s",  # Very fast
    duration_normal="0.1s",
    duration_slow="0.15s",
    easing="linear",  # No easing curves
)

ANIM_ELEGANT = AnimationStyle(
    name="elegant",
    entrance_effect="fade",
    exit_effect="fade",
    hover_effect="lift",
    hover_scale=1.02,  # Subtle
    hover_translate_y="-4px",  # Gentle lift
    click_effect="none",  # No aggressive feedback
    loading_style="skeleton",
    transition_style="smooth",
    duration_fast="0.4s",  # Slower, more graceful
    duration_normal="0.6s",
    duration_slow="0.8s",
    easing="cubic-bezier(0.25, 0.46, 0.45, 0.94)",  # Elegant curve
)

ANIM_RETRO = AnimationStyle(
    name="retro",
    entrance_effect="slide",  # Old-school slide
    exit_effect="slide",
    hover_effect="glow",
    hover_scale=1.0,  # No scaling
    hover_translate_y="0px",
    click_effect="bounce",  # Arcade-style
    loading_style="progress",
    transition_style="snappy",
    duration_fast="0.1s",
    duration_normal="0.2s",
    duration_slow="0.3s",
    easing="cubic-bezier(0.68, -0.55, 0.265, 1.55)",  # Bouncy
)

ANIM_ORGANIC = AnimationStyle(
    name="organic",
    entrance_effect="scale",  # Organic growth
    exit_effect="scale",
    hover_effect="glow",
    hover_scale=1.03,
    hover_translate_y="-2px",
    click_effect="ripple",  # Natural ripple
    loading_style="pulse",
    transition_style="bouncy",
    duration_fast="0.3s",
    duration_normal="0.5s",
    duration_slow="0.8s",
    easing="cubic-bezier(0.34, 1.56, 0.64, 1)",  # Organic bounce
)

ANIM_DJUST = AnimationStyle(
    name="djust",
    entrance_effect="fade",
    exit_effect="fade",
    hover_effect="lift",
    hover_scale=1.02,
    hover_translate_y="-2px",
    click_effect="ripple",
    loading_style="spinner",
    transition_style="smooth",
    duration_fast="0.15s",
    duration_normal="0.2s",
    duration_slow="0.3s",
    easing="cubic-bezier(0.4, 0, 0.2, 1)",
)


# =============================================================================
# Interaction Style Presets
# =============================================================================

INTERACT_MATERIAL = InteractionStyle(
    name="material",
    button_hover="lift",
    link_hover="underline",
    card_hover="lift",
    focus_style="ring",
    focus_ring_width="2px",
)

INTERACT_IOS = InteractionStyle(
    name="ios",
    button_hover="scale",
    link_hover="color",
    card_hover="shadow",
    focus_style="ring",
    focus_ring_width="2px",
)

INTERACT_FLUENT = InteractionStyle(
    name="fluent",
    button_hover="lift",
    link_hover="underline",
    card_hover="shadow",
    focus_style="ring",
    focus_ring_width="2px",
)

INTERACT_PLAYFUL = InteractionStyle(
    name="playful",
    button_hover="glow",
    link_hover="background",
    card_hover="lift",
    focus_style="glow",
    focus_ring_width="3px",
)

INTERACT_CORPORATE = InteractionStyle(
    name="corporate",
    button_hover="darken",
    link_hover="underline",
    card_hover="border",
    focus_style="ring",
    focus_ring_width="2px",
)

INTERACT_DENSE = InteractionStyle(
    name="dense",
    button_hover="darken",
    link_hover="underline",
    card_hover="none",
    focus_style="outline",
    focus_ring_width="1px",
)

INTERACT_MINIMAL = InteractionStyle(
    name="minimal",
    button_hover="darken",  # Subtle color change
    link_hover="underline",
    card_hover="none",  # No card hover
    focus_style="underline",
    focus_ring_width="1px",
)

INTERACT_BRUTALIST = InteractionStyle(
    name="brutalist",
    button_hover="glow",  # Bold glow effect
    link_hover="background",
    card_hover="shadow",  # Hard shadow change
    focus_style="outline",
    focus_ring_width="4px",  # Thick focus ring
)

INTERACT_ELEGANT = InteractionStyle(
    name="elegant",
    button_hover="lift",
    link_hover="color",  # Subtle color shift
    card_hover="shadow",  # Soft shadow lift
    focus_style="glow",
    focus_ring_width="2px",
)

INTERACT_RETRO = InteractionStyle(
    name="retro",
    button_hover="scale",  # Arcade-style scale
    link_hover="background",
    card_hover="border",  # Pixel border change
    focus_style="outline",
    focus_ring_width="2px",
)

INTERACT_ORGANIC = InteractionStyle(
    name="organic",
    button_hover="glow",  # Soft organic glow
    link_hover="color",
    card_hover="lift",  # Natural lift
    focus_style="glow",
    focus_ring_width="3px",
)

INTERACT_DJUST = InteractionStyle(
    name="djust",
    button_hover="lift",
    link_hover="underline",
    card_hover="shadow",
    focus_style="ring",
    focus_ring_width="2px",
)


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
    interaction=INTERACT_PLAYFUL,
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
    interaction=INTERACT_MINIMAL,
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


# =============================================================================
# Legacy Theme Packs (for backward compatibility)
# =============================================================================


@dataclass
class PatternStyle:
    """Background patterns and textures."""

    name: str

    # Pattern types
    background_pattern: str = "none"  # "dots", "grid", "noise", "gradient", "geometric", "none"
    pattern_opacity: float = 0.05
    pattern_scale: str = "1rem"

    # Surface treatment
    surface_style: str = "flat"  # "flat", "glass", "neumorphic", "elevated"

    # Blur/frosting for glassmorphism
    backdrop_blur: str = "0px"

    # Noise for texture
    noise_intensity: float = 0.0


@dataclass
class InteractionStyle:
    """User interaction feedback."""

    name: str

    # Hover effects
    button_hover: str = "lift"  # "lift", "scale", "glow", "darken", "none"
    link_hover: str = "underline"  # "underline", "color", "background", "none"
    card_hover: str = "lift"  # "lift", "scale", "border", "shadow", "none"

    # Click effects
    button_click: str = "scale"  # "scale", "ripple", "pulse", "none"

    # Focus effects
    focus_style: str = "ring"  # "ring", "outline", "glow", "underline"
    focus_ring_width: str = "2px"
    focus_ring_offset: str = "2px"

    # Cursor
    cursor_style: str = "pointer"  # "pointer", "default", "custom"


@dataclass
class IllustrationStyle:
    """Illustration and imagery treatment."""

    name: str

    # Illustration style
    illustration_type: str = (
        "flat"  # "flat", "isometric", "3d", "line-art", "hand-drawn", "abstract"
    )

    # Image treatment
    image_border_radius: str = "0.5rem"
    image_filter: str = "none"  # "none", "grayscale", "sepia", "vibrant", "duotone"

    # Aspect ratios preference
    preferred_aspect: str = "16:9"  # "1:1", "16:9", "4:3", "3:4"


@dataclass
class ThemePack:
    """
    Complete design system combining all styling dimensions.

    A ThemePack provides a cohesive design experience by bundling:
    - Core design (typography, spacing, shadows)
    - Color palette
    - Icon styling
    - Animation behavior
    - Background patterns
    - Interaction feedback
    - Illustration style
    """

    name: str
    display_name: str
    description: str
    category: str  # "professional", "playful", "minimal", "bold", "elegant", "retro"

    # Core components
    design_theme: str  # Reference to Theme name (e.g., "material", "elegant")
    color_preset: str  # Reference to ColorPreset name (e.g., "blue", "purple")

    # Style dimensions
    icon_style: IconStyle
    animation_style: AnimationStyle
    pattern_style: PatternStyle
    interaction_style: InteractionStyle
    illustration_style: IllustrationStyle


# ============================================
# Icon Style Presets
# ============================================

ICON_OUTLINED = IconStyle(
    name="outlined",
    style="outlined",
    weight="regular",
    stroke_width="2",
    corner_rounding="0",
)

ICON_FILLED = IconStyle(
    name="filled",
    style="filled",
    weight="regular",
    stroke_width="0",
    corner_rounding="0",
)

ICON_ROUNDED = IconStyle(
    name="rounded",
    style="rounded",
    weight="regular",
    stroke_width="2",
    corner_rounding="4px",
)

ICON_SHARP = IconStyle(
    name="sharp",
    style="sharp",
    weight="bold",
    stroke_width="2.5",
    corner_rounding="0",
)

ICON_THIN = IconStyle(
    name="thin",
    style="outlined",
    weight="thin",
    stroke_width="1",
    corner_rounding="0",
)


# ============================================
# Animation Style Presets
# ============================================

ANIM_SMOOTH = AnimationStyle(
    name="smooth",
    entrance_effect="fade",
    exit_effect="fade",
    hover_effect="lift",
    hover_scale=1.02,
    hover_translate_y="-2px",
    click_effect="ripple",
    loading_style="spinner",
    transition_style="smooth",
    duration_fast="0.15s",
    duration_normal="0.3s",
    duration_slow="0.5s",
    easing="cubic-bezier(0.4, 0, 0.2, 1)",
)

ANIM_SNAPPY = AnimationStyle(
    name="snappy",
    entrance_effect="scale",
    exit_effect="scale",
    hover_effect="scale",
    hover_scale=1.05,
    hover_translate_y="0px",
    click_effect="pulse",
    loading_style="progress",
    transition_style="snappy",
    duration_fast="0.08s",
    duration_normal="0.12s",
    duration_slow="0.2s",
    easing="cubic-bezier(0.68, -0.55, 0.265, 1.55)",
)

ANIM_BOUNCY = AnimationStyle(
    name="bouncy",
    entrance_effect="bounce",
    exit_effect="scale",
    hover_effect="scale",
    hover_scale=1.1,
    hover_translate_y="0px",
    click_effect="bounce",
    loading_style="pulse",
    transition_style="bouncy",
    duration_fast="0.2s",
    duration_normal="0.4s",
    duration_slow="0.6s",
    easing="cubic-bezier(0.34, 1.56, 0.64, 1)",
)

ANIM_INSTANT = AnimationStyle(
    name="instant",
    entrance_effect="none",
    exit_effect="none",
    hover_effect="none",
    hover_scale=1.0,
    hover_translate_y="0px",
    click_effect="none",
    loading_style="spinner",
    transition_style="instant",
    duration_fast="0.05s",
    duration_normal="0.1s",
    duration_slow="0.15s",
    easing="linear",
)

ANIM_GENTLE = AnimationStyle(
    name="gentle",
    entrance_effect="fade",
    exit_effect="fade",
    hover_effect="glow",
    hover_scale=1.0,
    hover_translate_y="0px",
    click_effect="none",
    loading_style="skeleton",
    transition_style="smooth",
    duration_fast="0.3s",
    duration_normal="0.5s",
    duration_slow="0.8s",
    easing="cubic-bezier(0.25, 0.46, 0.45, 0.94)",
)


# ============================================
# Pattern Style Presets
# ============================================

PATTERN_MINIMAL = PatternStyle(
    name="minimal",
    background_pattern="none",
    pattern_opacity=0.0,
    pattern_scale="1rem",
    surface_style="flat",
    backdrop_blur="0px",
    noise_intensity=0.0,
)

PATTERN_DOTS = PatternStyle(
    name="dots",
    background_pattern="dots",
    pattern_opacity=0.05,
    pattern_scale="1.5rem",
    surface_style="flat",
    backdrop_blur="0px",
    noise_intensity=0.0,
)

PATTERN_GRID = PatternStyle(
    name="grid",
    background_pattern="grid",
    pattern_opacity=0.03,
    pattern_scale="2rem",
    surface_style="flat",
    backdrop_blur="0px",
    noise_intensity=0.0,
)

PATTERN_NOISE = PatternStyle(
    name="noise",
    background_pattern="noise",
    pattern_opacity=0.02,
    pattern_scale="1rem",
    surface_style="flat",
    backdrop_blur="0px",
    noise_intensity=0.15,
)

PATTERN_GLASS = PatternStyle(
    name="glass",
    background_pattern="none",
    pattern_opacity=0.0,
    pattern_scale="1rem",
    surface_style="glass",
    backdrop_blur="12px",
    noise_intensity=0.0,
)

PATTERN_GRADIENT = PatternStyle(
    name="gradient",
    background_pattern="gradient",
    pattern_opacity=0.1,
    pattern_scale="100%",
    surface_style="flat",
    backdrop_blur="0px",
    noise_intensity=0.0,
)


# ============================================
# Interaction Style Presets
# ============================================

INTERACT_SUBTLE = InteractionStyle(
    name="subtle",
    button_hover="lift",
    link_hover="underline",
    card_hover="shadow",
    button_click="scale",
    focus_style="ring",
    focus_ring_width="2px",
    focus_ring_offset="2px",
    cursor_style="pointer",
)

INTERACT_BOLD = InteractionStyle(
    name="bold",
    button_hover="scale",
    link_hover="background",
    card_hover="lift",
    button_click="pulse",
    focus_style="outline",
    focus_ring_width="3px",
    focus_ring_offset="0px",
    cursor_style="pointer",
)

INTERACT_MINIMAL = InteractionStyle(
    name="minimal",
    button_hover="darken",
    link_hover="color",
    card_hover="border",
    button_click="none",
    focus_style="underline",
    focus_ring_width="1px",
    focus_ring_offset="0px",
    cursor_style="default",
)

INTERACT_PLAYFUL = InteractionStyle(
    name="playful",
    button_hover="glow",
    link_hover="background",
    card_hover="lift",
    button_click="ripple",
    focus_style="glow",
    focus_ring_width="3px",
    focus_ring_offset="3px",
    cursor_style="pointer",
)


# ============================================
# Illustration Style Presets
# ============================================

ILLUST_FLAT = IllustrationStyle(
    name="flat",
    illustration_type="flat",
    image_border_radius="0.5rem",
    image_filter="none",
    preferred_aspect="16:9",
)

ILLUST_3D = IllustrationStyle(
    name="3d",
    illustration_type="3d",
    image_border_radius="1rem",
    image_filter="vibrant",
    preferred_aspect="1:1",
)

ILLUST_LINE = IllustrationStyle(
    name="line-art",
    illustration_type="line-art",
    image_border_radius="0.25rem",
    image_filter="none",
    preferred_aspect="4:3",
)

ILLUST_HAND_DRAWN = IllustrationStyle(
    name="hand-drawn",
    illustration_type="hand-drawn",
    image_border_radius="1.5rem",
    image_filter="none",
    preferred_aspect="16:9",
)

ILLUST_RETRO = IllustrationStyle(
    name="retro",
    illustration_type="flat",
    image_border_radius="0px",
    image_filter="none",
    preferred_aspect="4:3",
)


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
