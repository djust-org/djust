"""Neon Noir -- Film noir meets neon — piercing hot pink on true black."""

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
    foreground=ColorScale(300, 10, 15),
    card=ColorScale(300, 5, 95),
    card_foreground=ColorScale(300, 10, 15),
    popover=ColorScale(300, 5, 95),
    popover_foreground=ColorScale(300, 10, 15),
    primary=ColorScale(330, 100, 60),
    primary_foreground=ColorScale(0, 0, 100),
    secondary=ColorScale(300, 5, 90),
    secondary_foreground=ColorScale(300, 10, 15),
    muted=ColorScale(300, 5, 90),
    muted_foreground=ColorScale(300, 5, 45),
    accent=ColorScale(300, 5, 88),
    accent_foreground=ColorScale(300, 10, 15),
    destructive=ColorScale(0, 85, 55),
    destructive_foreground=ColorScale(0, 0, 100),
    success=ColorScale(160, 60, 45),
    success_foreground=ColorScale(0, 0, 100),
    warning=ColorScale(45, 85, 55),
    warning_foreground=ColorScale(0, 0, 10),
    info=ColorScale(210, 70, 55),
    info_foreground=ColorScale(0, 0, 100),
    link=ColorScale(330, 100, 55),
    link_hover=ColorScale(330, 100, 45),
    code=ColorScale(300, 5, 92),
    code_foreground=ColorScale(330, 100, 50),
    selection=ColorScale(330, 80, 85),
    selection_foreground=ColorScale(330, 10, 15),
    brand=ColorScale(330, 100, 60),
    brand_foreground=ColorScale(0, 0, 100),
    border=ColorScale(300, 5, 82),
    input=ColorScale(300, 5, 82),
    ring=ColorScale(330, 100, 60),
    surface_1=ColorScale(330, 5, 96),
    surface_2=ColorScale(330, 5, 93),
    surface_3=ColorScale(330, 5, 90),
)

DARK = ThemeTokens(
    background=ColorScale(0, 0, 3),
    foreground=ColorScale(0, 0, 85),
    card=ColorScale(300, 5, 6),
    card_foreground=ColorScale(0, 0, 85),
    popover=ColorScale(300, 5, 6),
    popover_foreground=ColorScale(0, 0, 85),
    primary=ColorScale(330, 100, 60),
    primary_foreground=ColorScale(0, 0, 3),
    secondary=ColorScale(300, 5, 10),
    secondary_foreground=ColorScale(0, 0, 85),
    muted=ColorScale(300, 5, 10),
    muted_foreground=ColorScale(0, 0, 45),
    accent=ColorScale(300, 5, 12),
    accent_foreground=ColorScale(0, 0, 85),
    destructive=ColorScale(0, 85, 55),
    destructive_foreground=ColorScale(0, 0, 100),
    success=ColorScale(160, 60, 50),
    success_foreground=ColorScale(0, 0, 3),
    warning=ColorScale(45, 85, 55),
    warning_foreground=ColorScale(0, 0, 3),
    info=ColorScale(210, 70, 60),
    info_foreground=ColorScale(0, 0, 3),
    link=ColorScale(330, 100, 60),
    link_hover=ColorScale(330, 100, 75),
    code=ColorScale(300, 5, 8),
    code_foreground=ColorScale(330, 100, 65),
    selection=ColorScale(330, 80, 25),
    selection_foreground=ColorScale(330, 10, 90),
    brand=ColorScale(330, 100, 60),
    brand_foreground=ColorScale(0, 0, 100),
    border=ColorScale(300, 5, 12),
    input=ColorScale(300, 5, 12),
    ring=ColorScale(330, 100, 60),
    surface_1=ColorScale(330, 5, 6),
    surface_2=ColorScale(330, 5, 10),
    surface_3=ColorScale(330, 5, 14),
)

PRESET = ThemePreset(
    name="neon_noir",
    display_name="Neon Noir",
    description="Film noir meets neon — piercing hot pink on true black",
    light=LIGHT,
    dark=DARK,
    default_mode="dark",
)


# =============================================================================
# Design System
# =============================================================================

TYPOGRAPHY = TypographyStyle(
    name="neon_noir",
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
    name="neon_noir",
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
    name="neon_noir",
    shadow_sm="0 2px 8px rgba(255, 50, 150, 0.15)",
    shadow_md="0 6px 16px rgba(255, 50, 150, 0.2)",
    shadow_lg="0 12px 32px rgba(255, 50, 150, 0.25), 0 0 16px rgba(255, 50, 150, 0.1)",
    border_width="1px",
    border_style="solid",
    surface_treatment="glass",
    backdrop_blur="8px",
    noise_opacity=0.0,
)

ICON = IconStyle(
    name="neon_noir",
    style="sharp",
    weight="bold",
    size_scale=1.0,
    stroke_width="2",
    corner_rounding="0px",
)

ANIMATION = AnimationStyle(
    name="neon_noir",
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
    name="neon_noir",
    button_hover="glow",
    link_hover="color",
    card_hover="shadow",
    focus_style="ring",
    focus_ring_width="2px",
)

DESIGN_SYSTEM = DesignSystem(
    name="neon_noir",
    display_name="Neon Noir",
    description="Film noir meets neon -- piercing pink on true black",
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
    name="neon_noir",
    display_name="Neon Noir",
    description="Film noir meets neon -- piercing pink on true black",
    category="bold",
    design_theme="neon_noir",
    color_preset="neon_noir",
    icon_style=ICON,
    animation_style=ANIMATION,
    pattern_style=PATTERN_GRID,
    interaction_style=INTERACTION,
    illustration_style=ILLUST_FLAT,
)
