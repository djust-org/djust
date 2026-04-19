"""Ember -- Warm coal and fireplace — cozy dark theme."""

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
    background=ColorScale(30, 20, 97),
    foreground=ColorScale(15, 20, 15),
    card=ColorScale(30, 18, 94),
    card_foreground=ColorScale(15, 20, 15),
    popover=ColorScale(30, 18, 94),
    popover_foreground=ColorScale(15, 20, 15),
    primary=ColorScale(35, 95, 55),
    primary_foreground=ColorScale(0, 0, 100),
    secondary=ColorScale(30, 15, 88),
    secondary_foreground=ColorScale(15, 20, 15),
    muted=ColorScale(30, 15, 88),
    muted_foreground=ColorScale(15, 10, 45),
    accent=ColorScale(15, 80, 55),
    accent_foreground=ColorScale(0, 0, 100),
    destructive=ColorScale(0, 72, 51),
    destructive_foreground=ColorScale(0, 0, 100),
    success=ColorScale(85, 45, 45),
    success_foreground=ColorScale(0, 0, 100),
    warning=ColorScale(35, 95, 55),
    warning_foreground=ColorScale(15, 20, 15),
    info=ColorScale(200, 40, 50),
    info_foreground=ColorScale(0, 0, 100),
    link=ColorScale(15, 80, 50),
    link_hover=ColorScale(15, 80, 40),
    code=ColorScale(30, 15, 90),
    code_foreground=ColorScale(15, 80, 45),
    selection=ColorScale(35, 80, 85),
    selection_foreground=ColorScale(35, 10, 15),
    brand=ColorScale(25, 95, 55),
    brand_foreground=ColorScale(0, 0, 10),
    border=ColorScale(30, 12, 80),
    input=ColorScale(30, 12, 80),
    ring=ColorScale(35, 95, 55),
    surface_1=ColorScale(30, 20, 96),
    surface_2=ColorScale(30, 15, 93),
    surface_3=ColorScale(30, 12, 90),
)

DARK = ThemeTokens(
    background=ColorScale(15, 15, 10),
    foreground=ColorScale(35, 30, 88),
    card=ColorScale(15, 12, 14),
    card_foreground=ColorScale(35, 30, 88),
    popover=ColorScale(15, 12, 14),
    popover_foreground=ColorScale(35, 30, 88),
    primary=ColorScale(35, 95, 55),
    primary_foreground=ColorScale(15, 15, 10),
    secondary=ColorScale(15, 12, 18),
    secondary_foreground=ColorScale(35, 30, 88),
    muted=ColorScale(15, 12, 18),
    muted_foreground=ColorScale(25, 15, 55),
    accent=ColorScale(15, 80, 55),
    accent_foreground=ColorScale(15, 15, 10),
    destructive=ColorScale(0, 72, 55),
    destructive_foreground=ColorScale(0, 0, 100),
    success=ColorScale(85, 45, 55),
    success_foreground=ColorScale(15, 15, 10),
    warning=ColorScale(35, 95, 55),
    warning_foreground=ColorScale(15, 15, 10),
    info=ColorScale(200, 40, 60),
    info_foreground=ColorScale(15, 15, 10),
    link=ColorScale(35, 95, 55),
    link_hover=ColorScale(35, 95, 70),
    code=ColorScale(15, 12, 16),
    code_foreground=ColorScale(35, 95, 65),
    selection=ColorScale(35, 80, 25),
    selection_foreground=ColorScale(35, 10, 90),
    brand=ColorScale(25, 95, 55),
    brand_foreground=ColorScale(0, 0, 10),
    border=ColorScale(15, 12, 22),
    input=ColorScale(15, 12, 22),
    ring=ColorScale(35, 95, 55),
    surface_1=ColorScale(15, 20, 6),
    surface_2=ColorScale(15, 15, 10),
    surface_3=ColorScale(15, 12, 14),
)

PRESET = ThemePreset(
    name="ember",
    display_name="Ember",
    description="Warm coal and fireplace — cozy dark theme",
    light=LIGHT,
    dark=DARK,
    default_mode="dark",
)


# =============================================================================
# Design System
# =============================================================================

TYPOGRAPHY = TypographyStyle(
    name="ember",
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
    name="ember",
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
    name="ember",
    shadow_sm="0 2px 4px rgba(120, 60, 30, 0.08)",
    shadow_md="0 4px 12px rgba(120, 60, 30, 0.1)",
    shadow_lg="0 8px 24px rgba(120, 60, 30, 0.12)",
    border_width="1px",
    border_style="solid",
    surface_treatment="flat",
    backdrop_blur="0px",
    noise_opacity=0.0,
)

ICON = IconStyle(
    name="ember",
    style="rounded",
    weight="regular",
    size_scale=1.0,
    stroke_width="1.5",
    corner_rounding="3px",
)

ANIMATION = AnimationStyle(
    name="ember",
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
    name="ember",
    button_hover="lift",
    link_hover="color",
    card_hover="lift",
    focus_style="ring",
    focus_ring_width="2px",
)

DESIGN_SYSTEM = DesignSystem(
    name="ember",
    display_name="Ember",
    description="Warm coal and fireplace -- cozy warmth",
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
    name="ember",
    display_name="Ember",
    description="Warm coal and fireplace -- cozy warmth",
    category="playful",
    design_theme="ember",
    color_preset="ember",
    icon_style=ICON,
    animation_style=ANIMATION,
    pattern_style=PATTERN_NOISE,
    interaction_style=INTERACTION,
    illustration_style=ILLUST_HAND_DRAWN,
)
