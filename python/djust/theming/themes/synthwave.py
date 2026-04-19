"""Synthwave '84 -- Iconic 1980s synthwave with glowing neon pink and cyan."""

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
    foreground=ColorScale(255, 26, 25),
    card=ColorScale(255, 26, 95),
    card_foreground=ColorScale(255, 26, 25),
    popover=ColorScale(255, 26, 95),
    popover_foreground=ColorScale(255, 26, 25),
    primary=ColorScale(320, 100, 74),
    primary_foreground=ColorScale(0, 0, 100),
    secondary=ColorScale(255, 26, 88),
    secondary_foreground=ColorScale(255, 26, 25),
    muted=ColorScale(255, 26, 88),
    muted_foreground=ColorScale(255, 26, 45),
    accent=ColorScale(154, 83, 70),
    accent_foreground=ColorScale(0, 0, 10),
    destructive=ColorScale(0, 84, 60),
    destructive_foreground=ColorScale(0, 0, 100),
    success=ColorScale(154, 83, 70),
    success_foreground=ColorScale(0, 0, 10),
    warning=ColorScale(45, 100, 70),
    warning_foreground=ColorScale(0, 0, 10),
    info=ColorScale(267, 41, 69),
    info_foreground=ColorScale(0, 0, 100),
    link=ColorScale(320, 100, 74),
    link_hover=ColorScale(320, 100, 64),
    code=ColorScale(255, 26, 95),
    code_foreground=ColorScale(255, 26, 25),
    selection=ColorScale(320, 100, 84),
    selection_foreground=ColorScale(255, 26, 25),
    brand=ColorScale(154, 83, 70),
    brand_foreground=ColorScale(0, 0, 10),
    border=ColorScale(255, 26, 88),
    input=ColorScale(0, 0, 100),
    ring=ColorScale(320, 100, 74),
    surface_1=ColorScale(280, 20, 98),
    surface_2=ColorScale(280, 15, 96),
    surface_3=ColorScale(280, 12, 93),
)

DARK = ThemeTokens(
    background=ColorScale(255, 26, 17),
    foreground=ColorScale(320, 100, 95),
    card=ColorScale(255, 26, 22),
    card_foreground=ColorScale(320, 100, 95),
    popover=ColorScale(255, 26, 22),
    popover_foreground=ColorScale(320, 100, 95),
    primary=ColorScale(320, 100, 74),
    primary_foreground=ColorScale(0, 0, 100),
    secondary=ColorScale(255, 26, 30),
    secondary_foreground=ColorScale(320, 100, 95),
    muted=ColorScale(255, 26, 30),
    muted_foreground=ColorScale(267, 41, 69),
    accent=ColorScale(154, 83, 70),
    accent_foreground=ColorScale(0, 0, 10),
    destructive=ColorScale(0, 84, 60),
    destructive_foreground=ColorScale(0, 0, 100),
    success=ColorScale(154, 83, 70),
    success_foreground=ColorScale(0, 0, 10),
    warning=ColorScale(45, 100, 70),
    warning_foreground=ColorScale(0, 0, 10),
    info=ColorScale(267, 41, 69),
    info_foreground=ColorScale(0, 0, 100),
    link=ColorScale(320, 100, 74),
    link_hover=ColorScale(320, 100, 84),
    code=ColorScale(255, 26, 14),
    code_foreground=ColorScale(320, 100, 95),
    selection=ColorScale(320, 100, 74),
    selection_foreground=ColorScale(0, 0, 100),
    brand=ColorScale(154, 83, 70),
    brand_foreground=ColorScale(0, 0, 10),
    border=ColorScale(255, 26, 30),
    input=ColorScale(255, 26, 22),
    ring=ColorScale(320, 100, 74),
    surface_1=ColorScale(255, 25, 6),
    surface_2=ColorScale(255, 20, 10),
    surface_3=ColorScale(255, 15, 14),
)

PRESET = ThemePreset(
    name="synthwave",
    display_name="Synthwave '84",
    description="Iconic 1980s synthwave with glowing neon pink and cyan",
    light=LIGHT,
    dark=DARK,
    radius=0.75,
)


# =============================================================================
# Design System
# =============================================================================

TYPOGRAPHY = TypographyStyle(
    name="synthwave",
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
    name="synthwave",
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
    name="synthwave",
    shadow_sm="0 2px 8px rgba(255, 100, 200, 0.15)",
    shadow_md="0 6px 16px rgba(255, 100, 200, 0.2)",
    shadow_lg="0 12px 32px rgba(255, 100, 200, 0.25), 0 0 16px rgba(255, 100, 200, 0.1)",
    border_width="1px",
    border_style="solid",
    surface_treatment="glass",
    backdrop_blur="8px",
    noise_opacity=0.0,
)

ICON = IconStyle(
    name="synthwave",
    style="sharp",
    weight="bold",
    size_scale=1.0,
    stroke_width="2",
    corner_rounding="0px",
)

ANIMATION = AnimationStyle(
    name="synthwave",
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
    name="synthwave",
    button_hover="glow",
    link_hover="color",
    card_hover="shadow",
    focus_style="ring",
    focus_ring_width="2px",
)

DESIGN_SYSTEM = DesignSystem(
    name="synthwave",
    display_name="Synthwave '84",
    description="1980s neon -- glowing pink and cyan, dark atmospheric",
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
    name="synthwave",
    display_name="Synthwave '84",
    description="1980s neon -- glowing pink and cyan, dark atmospheric",
    category="bold",
    design_theme="synthwave",
    color_preset="synthwave",
    icon_style=ICON,
    animation_style=ANIMATION,
    pattern_style=PATTERN_GRID,
    interaction_style=INTERACTION,
    illustration_style=ILLUST_FLAT,
)
