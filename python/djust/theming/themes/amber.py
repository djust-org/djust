"""Amber -- Warm amber & gold — like a control room at midnight."""

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
    ILLUST_LINE,
    PATTERN_MINIMAL,
)

# --- Color Preset ---

LIGHT = ThemeTokens(
    background=ColorScale(38, 40, 98),
    foreground=ColorScale(34, 100, 15),
    card=ColorScale(38, 35, 94),
    card_foreground=ColorScale(34, 100, 15),
    popover=ColorScale(38, 35, 94),
    popover_foreground=ColorScale(34, 100, 15),
    primary=ColorScale(38, 92, 50),
    primary_foreground=ColorScale(0, 0, 10),
    secondary=ColorScale(38, 30, 88),
    secondary_foreground=ColorScale(34, 100, 20),
    muted=ColorScale(38, 30, 90),
    muted_foreground=ColorScale(34, 50, 40),
    accent=ColorScale(38, 30, 88),
    accent_foreground=ColorScale(34, 100, 20),
    destructive=ColorScale(0, 84, 60),
    destructive_foreground=ColorScale(0, 0, 100),
    success=ColorScale(142, 71, 45),
    success_foreground=ColorScale(0, 0, 100),
    warning=ColorScale(45, 100, 60),
    warning_foreground=ColorScale(0, 0, 10),
    info=ColorScale(38, 92, 50),
    info_foreground=ColorScale(0, 0, 10),
    link=ColorScale(38, 92, 45),
    link_hover=ColorScale(38, 92, 35),
    code=ColorScale(38, 35, 92),
    code_foreground=ColorScale(34, 100, 15),
    selection=ColorScale(38, 92, 50),
    selection_foreground=ColorScale(0, 0, 10),
    brand=ColorScale(38, 92, 50),
    brand_foreground=ColorScale(0, 0, 10),
    border=ColorScale(38, 30, 82),
    input=ColorScale(0, 0, 100),
    ring=ColorScale(38, 92, 50),
    surface_1=ColorScale(38, 40, 98),
    surface_2=ColorScale(38, 35, 96),
    surface_3=ColorScale(38, 30, 93),
)

DARK = ThemeTokens(
    background=ColorScale(34, 100, 5),
    foreground=ColorScale(45, 100, 95),
    card=ColorScale(36, 100, 8),
    card_foreground=ColorScale(45, 100, 95),
    popover=ColorScale(36, 100, 8),
    popover_foreground=ColorScale(45, 100, 95),
    primary=ColorScale(38, 92, 50),
    primary_foreground=ColorScale(0, 0, 10),
    secondary=ColorScale(34, 100, 10),
    secondary_foreground=ColorScale(45, 100, 95),
    muted=ColorScale(34, 100, 10),
    muted_foreground=ColorScale(45, 80, 65),
    accent=ColorScale(34, 100, 10),
    accent_foreground=ColorScale(45, 100, 75),
    destructive=ColorScale(0, 84, 60),
    destructive_foreground=ColorScale(0, 0, 100),
    success=ColorScale(142, 71, 45),
    success_foreground=ColorScale(0, 0, 100),
    warning=ColorScale(45, 100, 60),
    warning_foreground=ColorScale(0, 0, 10),
    info=ColorScale(38, 92, 50),
    info_foreground=ColorScale(0, 0, 10),
    link=ColorScale(38, 92, 50),
    link_hover=ColorScale(38, 92, 60),
    code=ColorScale(34, 100, 6),
    code_foreground=ColorScale(45, 100, 95),
    selection=ColorScale(38, 92, 50),
    selection_foreground=ColorScale(0, 0, 10),
    brand=ColorScale(38, 92, 50),
    brand_foreground=ColorScale(0, 0, 10),
    border=ColorScale(34, 100, 10),
    input=ColorScale(36, 100, 8),
    ring=ColorScale(38, 92, 50),
    surface_1=ColorScale(34, 30, 4),
    surface_2=ColorScale(34, 25, 7),
    surface_3=ColorScale(34, 20, 11),
)

PRESET = ThemePreset(
    name="amber",
    display_name="Amber",
    description="Warm amber & gold — like a control room at midnight",
    light=LIGHT,
    dark=DARK,
    radius=0.75,
)


# =============================================================================
# Design System
# =============================================================================

TYPOGRAPHY = TypographyStyle(
    name="amber",
    heading_font="system-ui, sans-serif",
    body_font="system-ui, sans-serif",
    base_size="16px",
    heading_scale=1.25,
    line_height="1.5",
    body_line_height="1.6",
    heading_weight="600",
    section_heading_weight="600",
    body_weight="400",
    letter_spacing="normal",
    prose_max_width="42rem",
    badge_radius="9999px",
)

LAYOUT = LayoutStyle(
    name="amber",
    space_unit="1rem",
    space_scale=1.5,
    border_radius_sm="4px",
    border_radius_md="6px",
    border_radius_lg="8px",
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
    name="amber",
    shadow_sm="0 2px 4px rgba(120, 80, 20, 0.08)",
    shadow_md="0 4px 10px rgba(120, 80, 20, 0.1)",
    shadow_lg="0 8px 20px rgba(120, 80, 20, 0.12)",
    border_width="1px",
    border_style="solid",
    surface_treatment="flat",
    backdrop_blur="0px",
    noise_opacity=0.0,
)

ICON = IconStyle(
    name="amber",
    style="outlined",
    weight="regular",
    size_scale=1.0,
    stroke_width="1.5",
    corner_rounding="2px",
)

ANIMATION = AnimationStyle(
    name="amber",
    entrance_effect="fade",
    exit_effect="fade",
    hover_effect="lift",
    hover_scale=1.0,
    hover_translate_y="-1px",
    click_effect="none",
    loading_style="spinner",
    transition_style="smooth",
    duration_fast="0.1s",
    duration_normal="0.2s",
    duration_slow="0.3s",
    easing="cubic-bezier(0.4, 0, 0.2, 1)",
)

INTERACTION = InteractionStyle(
    name="amber",
    button_hover="lift",
    link_hover="color",
    card_hover="shadow",
    focus_style="ring",
    focus_ring_width="2px",
)

DESIGN_SYSTEM = DesignSystem(
    name="amber",
    display_name="Amber",
    description="Warm amber and gold -- control room at midnight",
    category="elegant",
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
    name="amber",
    display_name="Amber",
    description="Warm amber and gold -- control room at midnight",
    category="elegant",
    design_theme="amber",
    color_preset="amber",
    icon_style=ICON,
    animation_style=ANIMATION,
    pattern_style=PATTERN_MINIMAL,
    interaction_style=INTERACTION,
    illustration_style=ILLUST_LINE,
)
