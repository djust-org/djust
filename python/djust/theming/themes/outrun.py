"""Outrun -- 80s retro racing with hot pink, purple, and sunset gradients."""

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
    foreground=ColorScale(240, 100, 15),
    card=ColorScale(240, 50, 95),
    card_foreground=ColorScale(240, 100, 15),
    popover=ColorScale(240, 50, 95),
    popover_foreground=ColorScale(240, 100, 15),
    primary=ColorScale(329, 100, 71),
    primary_foreground=ColorScale(0, 0, 100),
    secondary=ColorScale(240, 50, 88),
    secondary_foreground=ColorScale(240, 100, 15),
    muted=ColorScale(240, 50, 88),
    muted_foreground=ColorScale(240, 50, 45),
    accent=ColorScale(285, 61, 66),
    accent_foreground=ColorScale(0, 0, 100),
    destructive=ColorScale(0, 84, 60),
    destructive_foreground=ColorScale(0, 0, 100),
    success=ColorScale(187, 47, 55),
    success_foreground=ColorScale(0, 0, 100),
    warning=ColorScale(48, 100, 50),
    warning_foreground=ColorScale(0, 0, 10),
    info=ColorScale(187, 47, 55),
    info_foreground=ColorScale(0, 0, 100),
    link=ColorScale(329, 100, 71),
    link_hover=ColorScale(329, 100, 61),
    code=ColorScale(240, 50, 95),
    code_foreground=ColorScale(240, 100, 15),
    selection=ColorScale(329, 100, 81),
    selection_foreground=ColorScale(240, 100, 15),
    brand=ColorScale(180, 100, 50),
    brand_foreground=ColorScale(0, 0, 10),
    border=ColorScale(240, 50, 88),
    input=ColorScale(0, 0, 100),
    ring=ColorScale(329, 100, 71),
    surface_1=ColorScale(280, 20, 98),
    surface_2=ColorScale(280, 15, 96),
    surface_3=ColorScale(280, 12, 93),
)

DARK = ThemeTokens(
    background=ColorScale(240, 100, 8),
    foreground=ColorScale(329, 100, 95),
    card=ColorScale(240, 100, 12),
    card_foreground=ColorScale(329, 100, 95),
    popover=ColorScale(240, 100, 12),
    popover_foreground=ColorScale(329, 100, 95),
    primary=ColorScale(329, 100, 71),
    primary_foreground=ColorScale(0, 0, 100),
    secondary=ColorScale(240, 100, 18),
    secondary_foreground=ColorScale(329, 100, 95),
    muted=ColorScale(240, 100, 18),
    muted_foreground=ColorScale(285, 61, 66),
    accent=ColorScale(285, 61, 66),
    accent_foreground=ColorScale(0, 0, 100),
    destructive=ColorScale(0, 84, 60),
    destructive_foreground=ColorScale(0, 0, 100),
    success=ColorScale(187, 47, 55),
    success_foreground=ColorScale(0, 0, 100),
    warning=ColorScale(48, 100, 50),
    warning_foreground=ColorScale(0, 0, 10),
    info=ColorScale(187, 47, 55),
    info_foreground=ColorScale(0, 0, 100),
    link=ColorScale(329, 100, 71),
    link_hover=ColorScale(329, 100, 81),
    code=ColorScale(240, 100, 6),
    code_foreground=ColorScale(329, 100, 95),
    selection=ColorScale(329, 100, 71),
    selection_foreground=ColorScale(0, 0, 100),
    brand=ColorScale(180, 100, 50),
    brand_foreground=ColorScale(0, 0, 10),
    border=ColorScale(240, 100, 18),
    input=ColorScale(240, 100, 12),
    ring=ColorScale(329, 100, 71),
    surface_1=ColorScale(240, 25, 4),
    surface_2=ColorScale(240, 20, 7),
    surface_3=ColorScale(240, 15, 11),
)

PRESET = ThemePreset(
    name="outrun",
    display_name="Outrun",
    description="80s retro racing with hot pink, purple, and sunset gradients",
    light=LIGHT,
    dark=DARK,
    radius=0.75,
)


# =============================================================================
# Design System
# =============================================================================

TYPOGRAPHY = TypographyStyle(
    name="outrun",
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
    name="outrun",
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
    name="outrun",
    shadow_sm="0 2px 8px rgba(255, 80, 180, 0.15)",
    shadow_md="0 6px 16px rgba(255, 80, 180, 0.2)",
    shadow_lg="0 12px 32px rgba(255, 80, 180, 0.25), 0 0 16px rgba(255, 80, 180, 0.1)",
    border_width="1px",
    border_style="solid",
    surface_treatment="glass",
    backdrop_blur="8px",
    noise_opacity=0.0,
)

ICON = IconStyle(
    name="outrun",
    style="sharp",
    weight="bold",
    size_scale=1.0,
    stroke_width="2",
    corner_rounding="0px",
)

ANIMATION = AnimationStyle(
    name="outrun",
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
    name="outrun",
    button_hover="glow",
    link_hover="color",
    card_hover="shadow",
    focus_style="ring",
    focus_ring_width="2px",
)

DESIGN_SYSTEM = DesignSystem(
    name="outrun",
    display_name="Outrun",
    description="80s retro racing -- hot pink, purple, sunset glow",
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
    name="outrun",
    display_name="Outrun",
    description="80s retro racing -- hot pink, purple, sunset glow",
    category="bold",
    design_theme="outrun",
    color_preset="outrun",
    icon_style=ICON,
    animation_style=ANIMATION,
    pattern_style=PATTERN_GRID,
    interaction_style=INTERACTION,
    illustration_style=ILLUST_FLAT,
)
