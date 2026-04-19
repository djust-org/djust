"""Cyberpunk -- Dystopian future with neon yellow, magenta, and cyan."""

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
    ILLUST_FLAT,
    PATTERN_GRID,
)

# --- Color Preset ---

LIGHT = ThemeTokens(
    background=ColorScale(0, 0, 98),
    foreground=ColorScale(219, 100, 15),
    card=ColorScale(219, 50, 95),
    card_foreground=ColorScale(219, 100, 15),
    popover=ColorScale(219, 50, 95),
    popover_foreground=ColorScale(219, 100, 15),
    primary=ColorScale(54, 70, 68),
    primary_foreground=ColorScale(0, 0, 10),
    secondary=ColorScale(219, 50, 88),
    secondary_foreground=ColorScale(219, 100, 15),
    muted=ColorScale(219, 50, 88),
    muted_foreground=ColorScale(219, 50, 45),
    accent=ColorScale(330, 100, 50),
    accent_foreground=ColorScale(0, 0, 100),
    destructive=ColorScale(0, 84, 60),
    destructive_foreground=ColorScale(0, 0, 100),
    success=ColorScale(135, 94, 65),
    success_foreground=ColorScale(0, 0, 10),
    warning=ColorScale(54, 70, 68),
    warning_foreground=ColorScale(0, 0, 10),
    info=ColorScale(180, 100, 50),
    info_foreground=ColorScale(0, 0, 100),
    link=ColorScale(330, 100, 50),
    link_hover=ColorScale(330, 100, 40),
    code=ColorScale(219, 50, 95),
    code_foreground=ColorScale(219, 100, 15),
    selection=ColorScale(54, 70, 78),
    selection_foreground=ColorScale(0, 0, 10),
    brand=ColorScale(300, 100, 50),
    brand_foreground=ColorScale(0, 0, 100),
    border=ColorScale(219, 50, 88),
    input=ColorScale(0, 0, 100),
    ring=ColorScale(54, 70, 68),
    surface_1=ColorScale(219, 20, 98),
    surface_2=ColorScale(219, 15, 96),
    surface_3=ColorScale(219, 12, 93),
)

DARK = ThemeTokens(
    background=ColorScale(219, 100, 6),
    foreground=ColorScale(54, 70, 95),
    card=ColorScale(219, 100, 10),
    card_foreground=ColorScale(54, 70, 95),
    popover=ColorScale(219, 100, 10),
    popover_foreground=ColorScale(54, 70, 95),
    primary=ColorScale(54, 70, 68),
    primary_foreground=ColorScale(0, 0, 10),
    secondary=ColorScale(219, 100, 15),
    secondary_foreground=ColorScale(54, 70, 95),
    muted=ColorScale(219, 100, 15),
    muted_foreground=ColorScale(180, 100, 70),
    accent=ColorScale(330, 100, 50),
    accent_foreground=ColorScale(0, 0, 100),
    destructive=ColorScale(0, 84, 60),
    destructive_foreground=ColorScale(0, 0, 100),
    success=ColorScale(135, 94, 65),
    success_foreground=ColorScale(0, 0, 10),
    warning=ColorScale(54, 70, 68),
    warning_foreground=ColorScale(0, 0, 10),
    info=ColorScale(180, 100, 50),
    info_foreground=ColorScale(0, 0, 100),
    link=ColorScale(330, 100, 50),
    link_hover=ColorScale(330, 100, 60),
    code=ColorScale(219, 100, 8),
    code_foreground=ColorScale(54, 70, 95),
    selection=ColorScale(330, 100, 50),
    selection_foreground=ColorScale(0, 0, 100),
    brand=ColorScale(300, 100, 50),
    brand_foreground=ColorScale(0, 0, 100),
    border=ColorScale(219, 100, 15),
    input=ColorScale(219, 100, 10),
    ring=ColorScale(54, 70, 68),
    surface_1=ColorScale(219, 25, 3),
    surface_2=ColorScale(219, 20, 5),
    surface_3=ColorScale(219, 15, 8),
)

PRESET = ThemePreset(
    name="cyberpunk",
    display_name="Cyberpunk",
    description="Dystopian future with neon yellow, magenta, and cyan",
    light=LIGHT,
    dark=DARK,
)


# =============================================================================
# Design System
# =============================================================================

TYPOGRAPHY = TypographyStyle(
    name="cyberpunk",
    heading_font='"Inter", system-ui, sans-serif',
    body_font='"Inter", system-ui, sans-serif',
    base_size="16px",
    heading_scale=1.3,
    line_height="1.5",
    body_line_height="1.6",
    heading_weight="700",
    section_heading_weight="700",
    body_weight="400",
    letter_spacing="-0.01em",
    prose_max_width="42rem",
    badge_radius="9999px",
)

LAYOUT = LayoutStyle(
    name="cyberpunk",
    space_unit="1rem",
    space_scale=1.5,
    border_radius_sm="6px",
    border_radius_md="8px",
    border_radius_lg="12px",
    button_shape="rounded",
    card_shape="rounded",
    input_shape="rounded",
    container_width="1200px",
    grid_gap="1.5rem",
    section_spacing="4rem",
    hero_padding_top="7rem",
    hero_padding_bottom="4rem",
    hero_line_height="1.1",
    hero_max_width="54rem",
)

SURFACE = SurfaceStyle(
    name="cyberpunk",
    shadow_sm="0 2px 8px rgba(255, 230, 0, 0.15)",
    shadow_md="0 6px 16px rgba(255, 230, 0, 0.2)",
    shadow_lg="0 12px 32px rgba(255, 230, 0, 0.25), 0 0 16px rgba(255, 230, 0, 0.1)",
    border_width="1px",
    border_style="solid",
    surface_treatment="glass",
    backdrop_blur="8px",
    noise_opacity=0.0,
)

ICON = IconStyle(
    name="cyberpunk",
    style="sharp",
    weight="bold",
    size_scale=1.0,
    stroke_width="2",
    corner_rounding="0px",
)

ANIMATION = AnimationStyle(
    name="cyberpunk",
    entrance_effect="fade",
    exit_effect="fade",
    hover_effect="glow",
    hover_scale=1.02,
    hover_translate_y="-2px",
    click_effect="pulse",
    loading_style="pulse",
    transition_style="snappy",
    duration_fast="0.08s",
    duration_normal="0.15s",
    duration_slow="0.25s",
    easing="cubic-bezier(0.22, 1, 0.36, 1)",
)

INTERACTION = InteractionStyle(
    name="cyberpunk",
    button_hover="glow",
    link_hover="color",
    card_hover="shadow",
    focus_style="ring",
    focus_ring_width="2px",
)

DESIGN_SYSTEM = DesignSystem(
    name="cyberpunk",
    display_name="Cyberpunk",
    description="Dystopian neon -- yellow, magenta, cyan, dark atmospheric",
    category="bold",
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
    name="cyberpunk",
    display_name="Cyberpunk",
    description="Dystopian neon -- yellow, magenta, cyan, dark atmospheric",
    category="bold",
    design_theme="cyberpunk",
    color_preset="cyberpunk",
    icon_style=ICON,
    animation_style=ANIMATION,
    pattern_style=PATTERN_GRID,
    interaction_style=INTERACTION,
    illustration_style=ILLUST_FLAT,
)
