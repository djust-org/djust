"""Solarpunk -- Optimistic nature — lush greens and warm amber, organic shapes."""

from ._base import (
    AnimationStyle,
    ColorScale,
    DesignSystem,
    IconStyle,
    InteractionStyle,
    LayoutStyle,
    SurfaceStyle,
    ThemePack,
    ThemePreset,
    ThemeTokens,
    TypographyStyle,
    ILLUST_HAND_DRAWN,
    PATTERN_NOISE,
)

# --- Color Preset ---

LIGHT = ThemeTokens(
    background=ColorScale(45, 40, 96),
    foreground=ColorScale(145, 30, 15),
    card=ColorScale(45, 35, 93),
    card_foreground=ColorScale(145, 30, 15),
    popover=ColorScale(45, 35, 93),
    popover_foreground=ColorScale(145, 30, 15),
    primary=ColorScale(145, 63, 42),
    primary_foreground=ColorScale(0, 0, 100),
    secondary=ColorScale(45, 30, 88),
    secondary_foreground=ColorScale(145, 30, 15),
    muted=ColorScale(45, 30, 88),
    muted_foreground=ColorScale(145, 15, 40),
    accent=ColorScale(35, 80, 55),
    accent_foreground=ColorScale(0, 0, 10),
    destructive=ColorScale(0, 65, 50),
    destructive_foreground=ColorScale(0, 0, 100),
    success=ColorScale(145, 63, 42),
    success_foreground=ColorScale(0, 0, 100),
    warning=ColorScale(35, 80, 55),
    warning_foreground=ColorScale(0, 0, 10),
    info=ColorScale(190, 55, 45),
    info_foreground=ColorScale(0, 0, 100),
    link=ColorScale(145, 63, 38),
    link_hover=ColorScale(145, 63, 30),
    code=ColorScale(45, 30, 90),
    code_foreground=ColorScale(145, 63, 35),
    selection=ColorScale(145, 63, 85),
    selection_foreground=ColorScale(145, 10, 15),
    brand=ColorScale(145, 63, 42),
    brand_foreground=ColorScale(0, 0, 100),
    border=ColorScale(45, 20, 78),
    input=ColorScale(45, 20, 78),
    ring=ColorScale(145, 63, 42),
    surface_1=ColorScale(145, 5, 96),
    surface_2=ColorScale(145, 5, 93),
    surface_3=ColorScale(145, 5, 90),
)

DARK = ThemeTokens(
    background=ColorScale(150, 30, 10),
    foreground=ColorScale(45, 40, 90),
    card=ColorScale(150, 25, 14),
    card_foreground=ColorScale(45, 40, 90),
    popover=ColorScale(150, 25, 14),
    popover_foreground=ColorScale(45, 40, 90),
    primary=ColorScale(145, 63, 52),
    primary_foreground=ColorScale(150, 30, 10),
    secondary=ColorScale(150, 25, 18),
    secondary_foreground=ColorScale(45, 40, 90),
    muted=ColorScale(150, 25, 18),
    muted_foreground=ColorScale(120, 15, 55),
    accent=ColorScale(35, 80, 60),
    accent_foreground=ColorScale(0, 0, 10),
    destructive=ColorScale(0, 65, 55),
    destructive_foreground=ColorScale(0, 0, 100),
    success=ColorScale(145, 63, 52),
    success_foreground=ColorScale(150, 30, 10),
    warning=ColorScale(35, 80, 60),
    warning_foreground=ColorScale(0, 0, 10),
    info=ColorScale(190, 55, 55),
    info_foreground=ColorScale(150, 30, 10),
    link=ColorScale(145, 63, 52),
    link_hover=ColorScale(80, 60, 65),
    code=ColorScale(150, 25, 16),
    code_foreground=ColorScale(80, 60, 60),
    selection=ColorScale(145, 63, 25),
    selection_foreground=ColorScale(145, 10, 90),
    brand=ColorScale(145, 63, 52),
    brand_foreground=ColorScale(150, 30, 10),
    border=ColorScale(150, 25, 22),
    input=ColorScale(150, 25, 22),
    ring=ColorScale(145, 63, 52),
    surface_1=ColorScale(145, 5, 6),
    surface_2=ColorScale(145, 5, 10),
    surface_3=ColorScale(145, 5, 14),
)

PRESET = ThemePreset(
    name="solarpunk",
    display_name="Solarpunk",
    description="Optimistic nature — lush greens and warm amber, organic shapes",
    light=LIGHT,
    dark=DARK,
)


# =============================================================================
# Design System
# =============================================================================

TYPOGRAPHY = TypographyStyle(
    name="solarpunk",
    heading_font="system-ui, sans-serif",
    body_font="system-ui, sans-serif",
    base_size="16px",
    heading_scale=1.25,
    line_height="1.5",
    body_line_height="1.65",
    heading_weight="600",
    section_heading_weight="600",
    body_weight="400",
    letter_spacing="normal",
    prose_max_width="42rem",
    badge_radius="9999px",
)

LAYOUT = LayoutStyle(
    name="solarpunk",
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
    section_spacing="4.5rem",
    hero_padding_top="7rem",
    hero_padding_bottom="4rem",
    hero_line_height="1.15",
    hero_max_width="50rem",
)

SURFACE = SurfaceStyle(
    name="solarpunk",
    shadow_sm="0 2px 4px rgba(40, 100, 40, 0.08)",
    shadow_md="0 4px 12px rgba(40, 100, 40, 0.1)",
    shadow_lg="0 8px 24px rgba(40, 100, 40, 0.12)",
    border_width="1px",
    border_style="solid",
    surface_treatment="flat",
    backdrop_blur="0px",
    noise_opacity=0.0,
)

ICON = IconStyle(
    name="solarpunk",
    style="rounded",
    weight="regular",
    size_scale=1.0,
    stroke_width="1.5",
    corner_rounding="3px",
)

ANIMATION = AnimationStyle(
    name="solarpunk",
    entrance_effect="fade",
    exit_effect="fade",
    hover_effect="lift",
    hover_scale=1.0,
    hover_translate_y="-1px",
    click_effect="none",
    loading_style="pulse",
    transition_style="smooth",
    duration_fast="0.15s",
    duration_normal="0.25s",
    duration_slow="0.4s",
    easing="cubic-bezier(0.25, 0.1, 0.25, 1)",
)

INTERACTION = InteractionStyle(
    name="solarpunk",
    button_hover="lift",
    link_hover="color",
    card_hover="lift",
    focus_style="ring",
    focus_ring_width="2px",
)

DESIGN_SYSTEM = DesignSystem(
    name="solarpunk",
    display_name="Solarpunk",
    description="Optimistic nature -- lush greens and warm amber",
    category="playful",
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
    name="solarpunk",
    display_name="Solarpunk",
    description="Optimistic nature -- lush greens and warm amber",
    category="playful",
    design_theme="solarpunk",
    color_preset="solarpunk",
    icon_style=ICON,
    animation_style=ANIMATION,
    pattern_style=PATTERN_NOISE,
    interaction_style=INTERACTION,
    illustration_style=ILLUST_HAND_DRAWN,
)
