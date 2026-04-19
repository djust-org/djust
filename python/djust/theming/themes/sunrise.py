"""Sunrise — Warm gradient coral to gold, light and airy, optimistic energy."""

from ._base import (
    ColorScale,
    ThemeTokens,
    ThemePreset,
    TypographyStyle,
    LayoutStyle,
    SurfaceStyle,
    IconStyle,
    AnimationStyle,
    InteractionStyle,
    DesignSystem,
    ThemePack,
    PATTERN_GRADIENT,
    ILLUST_FLAT,
)


# =============================================================================
# Color Preset
# =============================================================================
# Warm coral-to-gold palette — sunrise over open water.
# Optimistic, energetic, inviting.

LIGHT = ThemeTokens(
    background=ColorScale(30, 40, 98),  # Warm white
    foreground=ColorScale(20, 20, 18),  # Warm dark
    card=ColorScale(0, 0, 100),  # White cards
    card_foreground=ColorScale(20, 20, 18),
    popover=ColorScale(0, 0, 100),
    popover_foreground=ColorScale(20, 20, 18),
    primary=ColorScale(12, 80, 60),  # Coral
    primary_foreground=ColorScale(0, 0, 100),
    secondary=ColorScale(30, 30, 95),  # Warm pale bg
    secondary_foreground=ColorScale(20, 20, 18),
    muted=ColorScale(30, 20, 91),  # Warm muted
    muted_foreground=ColorScale(20, 15, 42),  # Warm gray text
    accent=ColorScale(30, 30, 95),  # Warm hover surface
    accent_foreground=ColorScale(20, 20, 18),
    destructive=ColorScale(0, 70, 50),  # Red
    destructive_foreground=ColorScale(0, 0, 100),
    success=ColorScale(140, 50, 45),  # Warm green
    success_foreground=ColorScale(0, 0, 100),
    warning=ColorScale(42, 90, 55),  # Golden yellow
    warning_foreground=ColorScale(20, 20, 18),
    info=ColorScale(210, 60, 55),  # Warm blue
    info_foreground=ColorScale(0, 0, 100),
    link=ColorScale(12, 80, 52),  # Coral links
    link_hover=ColorScale(42, 90, 48),  # Golden on hover
    code=ColorScale(20, 20, 12),  # Dark warm code bg
    code_foreground=ColorScale(12, 80, 72),  # Coral code text
    selection=ColorScale(12, 60, 90),  # Pale coral selection
    selection_foreground=ColorScale(20, 20, 18),
    brand=ColorScale(42, 90, 55),  # Golden yellow
    brand_foreground=ColorScale(20, 20, 18),
    border=ColorScale(30, 20, 86),  # Warm border
    input=ColorScale(30, 15, 90),  # Warm input
    ring=ColorScale(12, 80, 60),  # Coral focus ring
    surface_1=ColorScale(30, 40, 98),  # Warm white
    surface_2=ColorScale(30, 30, 96),  # Slightly deeper
    surface_3=ColorScale(30, 25, 93),  # Deeper still
)

DARK = ThemeTokens(
    background=ColorScale(20, 30, 10),  # Deep warm
    foreground=ColorScale(30, 30, 90),  # Warm light
    card=ColorScale(20, 25, 13),  # Dark warm card
    card_foreground=ColorScale(30, 30, 90),
    popover=ColorScale(20, 25, 13),
    popover_foreground=ColorScale(30, 30, 90),
    primary=ColorScale(12, 80, 65),  # Coral brightened
    primary_foreground=ColorScale(0, 0, 100),
    secondary=ColorScale(20, 20, 15),  # Dark warm panel
    secondary_foreground=ColorScale(30, 30, 90),
    muted=ColorScale(20, 18, 20),  # Dark muted
    muted_foreground=ColorScale(30, 15, 55),  # Muted warm text
    accent=ColorScale(20, 20, 15),  # Dark warm hover
    accent_foreground=ColorScale(30, 30, 90),
    destructive=ColorScale(0, 70, 55),  # Red brightened
    destructive_foreground=ColorScale(0, 0, 100),
    success=ColorScale(140, 50, 50),  # Green brightened
    success_foreground=ColorScale(0, 0, 100),
    warning=ColorScale(42, 90, 60),  # Gold brightened
    warning_foreground=ColorScale(20, 20, 10),
    info=ColorScale(210, 60, 60),  # Blue brightened
    info_foreground=ColorScale(0, 0, 100),
    link=ColorScale(12, 80, 68),  # Coral brightened
    link_hover=ColorScale(42, 90, 62),  # Golden hover
    code=ColorScale(20, 30, 6),  # Deepest warm code
    code_foreground=ColorScale(42, 90, 65),  # Gold code text
    selection=ColorScale(12, 60, 22),  # Deep coral selection
    selection_foreground=ColorScale(30, 30, 90),
    brand=ColorScale(42, 90, 60),  # Gold brightened
    brand_foreground=ColorScale(20, 20, 10),
    border=ColorScale(20, 18, 22),  # Dark warm border
    input=ColorScale(20, 15, 18),  # Dark input
    ring=ColorScale(12, 80, 65),  # Coral focus ring
    surface_1=ColorScale(20, 30, 8),  # Deepest
    surface_2=ColorScale(20, 25, 11),  # Mid dark
    surface_3=ColorScale(20, 22, 14),  # Elevated
)

PRESET = ThemePreset(
    name="sunrise",
    display_name="Sunrise",
    description="Warm gradient coral to gold — light and airy, optimistic energy",
    light=LIGHT,
    dark=DARK,
    radius=0.5,  # 8px
)


# =============================================================================
# Design System
# =============================================================================

TYPOGRAPHY = TypographyStyle(
    name="sunrise",
    heading_font='"DM Sans", system-ui, sans-serif',
    body_font='"DM Sans", system-ui, sans-serif',
    base_size="16px",
    heading_scale=1.3,
    line_height="1.5",
    body_line_height="1.65",
    heading_weight="600",
    section_heading_weight="600",
    body_weight="400",
    letter_spacing="-0.01em",
    prose_max_width="42rem",
    badge_radius="9999px",  # Pill badges
)

LAYOUT = LayoutStyle(
    name="sunrise",
    space_unit="1rem",
    space_scale=1.5,
    border_radius_sm="6px",
    border_radius_md="8px",
    border_radius_lg="12px",
    button_shape="rounded",
    card_shape="rounded",
    input_shape="rounded",
    container_width="1100px",
    grid_gap="1.5rem",
    section_spacing="4rem",
    hero_padding_top="7rem",
    hero_padding_bottom="4rem",
    hero_line_height="1.15",
    hero_max_width="50rem",
)

SURFACE = SurfaceStyle(
    name="sunrise",
    # Warm coral-tinted soft shadows
    shadow_sm="0 2px 6px 0 rgba(180, 80, 50, 0.08)",
    shadow_md="0 4px 12px -2px rgba(180, 80, 50, 0.12)",
    shadow_lg="0 10px 28px -4px rgba(180, 80, 50, 0.16)",
    border_width="1px",
    border_style="solid",
    surface_treatment="gradient",  # Gradient surface
    backdrop_blur="0px",
    noise_opacity=0.0,
)

ICON = IconStyle(
    name="sunrise",
    style="rounded",
    weight="regular",
    size_scale=1.0,
    stroke_width="1.5",
    corner_rounding="3px",
)

ANIMATION = AnimationStyle(
    name="sunrise",
    entrance_effect="fade",
    exit_effect="fade",
    hover_effect="lift",
    hover_scale=1.0,
    hover_translate_y="-2px",
    click_effect="pulse",
    loading_style="spinner",
    transition_style="smooth",
    duration_fast="0.12s",
    duration_normal="0.25s",
    duration_slow="0.4s",
    easing="cubic-bezier(0.4, 0, 0.2, 1)",
)

INTERACTION = InteractionStyle(
    name="sunrise",
    button_hover="lift",
    link_hover="color",
    card_hover="lift",
    focus_style="ring",
    focus_ring_width="2px",
)

DESIGN_SYSTEM = DesignSystem(
    name="sunrise",
    display_name="Sunrise",
    description="Warm gradient coral to gold — light, airy, optimistic energy",
    category="warm",
    typography=TYPOGRAPHY,
    layout=LAYOUT,
    surface=SURFACE,
    icons=ICON,
    animation=ANIMATION,
    interaction=INTERACTION,
)


# =============================================================================
# Theme Pack
# =============================================================================

PACK = ThemePack(
    name="sunrise",
    display_name="Sunrise",
    description="Warm gradient coral to gold — light and airy, optimistic energy",
    category="warm",
    design_theme="sunrise",
    color_preset="sunrise",
    icon_style=ICON,
    animation_style=ANIMATION,
    pattern_style=PATTERN_GRADIENT,
    interaction_style=INTERACTION,
    illustration_style=ILLUST_FLAT,
)
