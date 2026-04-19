"""Aurora -- Northern lights — shifting green-to-violet feel."""

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
    background=ColorScale(210, 20, 98),
    foreground=ColorScale(220, 25, 15),
    card=ColorScale(210, 18, 95),
    card_foreground=ColorScale(220, 25, 15),
    popover=ColorScale(210, 18, 95),
    popover_foreground=ColorScale(220, 25, 15),
    primary=ColorScale(160, 84, 39),
    primary_foreground=ColorScale(0, 0, 100),
    secondary=ColorScale(210, 15, 90),
    secondary_foreground=ColorScale(220, 25, 15),
    muted=ColorScale(210, 15, 90),
    muted_foreground=ColorScale(220, 15, 45),
    accent=ColorScale(270, 60, 60),
    accent_foreground=ColorScale(0, 0, 100),
    destructive=ColorScale(0, 72, 55),
    destructive_foreground=ColorScale(0, 0, 100),
    success=ColorScale(160, 84, 39),
    success_foreground=ColorScale(0, 0, 100),
    warning=ColorScale(45, 90, 55),
    warning_foreground=ColorScale(0, 0, 10),
    info=ColorScale(200, 80, 55),
    info_foreground=ColorScale(0, 0, 100),
    link=ColorScale(160, 84, 39),
    link_hover=ColorScale(270, 60, 55),
    code=ColorScale(210, 15, 92),
    code_foreground=ColorScale(160, 84, 35),
    selection=ColorScale(160, 80, 85),
    selection_foreground=ColorScale(160, 10, 15),
    brand=ColorScale(270, 60, 65),
    brand_foreground=ColorScale(0, 0, 100),
    border=ColorScale(210, 15, 82),
    input=ColorScale(210, 15, 82),
    ring=ColorScale(160, 84, 39),
    surface_1=ColorScale(160, 5, 96),
    surface_2=ColorScale(160, 5, 93),
    surface_3=ColorScale(160, 5, 90),
)

DARK = ThemeTokens(
    background=ColorScale(230, 25, 10),
    foreground=ColorScale(180, 20, 90),
    card=ColorScale(230, 22, 14),
    card_foreground=ColorScale(180, 20, 90),
    popover=ColorScale(230, 22, 14),
    popover_foreground=ColorScale(180, 20, 90),
    primary=ColorScale(160, 84, 52),
    primary_foreground=ColorScale(230, 25, 10),
    secondary=ColorScale(230, 22, 18),
    secondary_foreground=ColorScale(180, 20, 90),
    muted=ColorScale(230, 22, 18),
    muted_foreground=ColorScale(200, 15, 55),
    accent=ColorScale(270, 60, 65),
    accent_foreground=ColorScale(0, 0, 100),
    destructive=ColorScale(0, 72, 55),
    destructive_foreground=ColorScale(0, 0, 100),
    success=ColorScale(160, 84, 52),
    success_foreground=ColorScale(230, 25, 10),
    warning=ColorScale(45, 90, 60),
    warning_foreground=ColorScale(0, 0, 10),
    info=ColorScale(200, 80, 60),
    info_foreground=ColorScale(230, 25, 10),
    link=ColorScale(160, 84, 52),
    link_hover=ColorScale(270, 60, 70),
    code=ColorScale(230, 22, 16),
    code_foreground=ColorScale(160, 84, 60),
    selection=ColorScale(160, 80, 25),
    selection_foreground=ColorScale(160, 10, 90),
    brand=ColorScale(270, 60, 65),
    brand_foreground=ColorScale(0, 0, 100),
    border=ColorScale(230, 22, 22),
    input=ColorScale(230, 22, 22),
    ring=ColorScale(160, 84, 52),
    surface_1=ColorScale(160, 5, 6),
    surface_2=ColorScale(160, 5, 10),
    surface_3=ColorScale(160, 5, 14),
)

PRESET = ThemePreset(
    name="aurora",
    display_name="Aurora",
    description="Northern lights — shifting green-to-violet feel",
    light=LIGHT,
    dark=DARK,
    default_mode="dark",
)


# =============================================================================
# Design System
# =============================================================================

TYPOGRAPHY = TypographyStyle(
    name="aurora",
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
    name="aurora",
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
    name="aurora",
    shadow_sm="0 2px 4px rgba(50, 120, 80, 0.08)",
    shadow_md="0 4px 12px rgba(50, 120, 80, 0.1)",
    shadow_lg="0 8px 24px rgba(50, 120, 80, 0.12)",
    border_width="1px",
    border_style="solid",
    surface_treatment="flat",
    backdrop_blur="0px",
    noise_opacity=0.0,
)

ICON = IconStyle(
    name="aurora",
    style="rounded",
    weight="regular",
    size_scale=1.0,
    stroke_width="1.5",
    corner_rounding="3px",
)

ANIMATION = AnimationStyle(
    name="aurora",
    entrance_effect="fade",
    exit_effect="fade",
    hover_effect="lift",
    hover_scale=1.0,
    hover_translate_y="-1px",
    click_effect="pulse",
    loading_style="pulse",
    transition_style="smooth",
    duration_fast="0.15s",
    duration_normal="0.25s",
    duration_slow="0.4s",
    easing="cubic-bezier(0.25, 0.1, 0.25, 1)",
)

INTERACTION = InteractionStyle(
    name="aurora",
    button_hover="lift",
    link_hover="color",
    card_hover="lift",
    focus_style="ring",
    focus_ring_width="2px",
)

DESIGN_SYSTEM = DesignSystem(
    name="aurora",
    display_name="Aurora",
    description="Northern lights -- shifting green-to-violet",
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
    name="aurora",
    display_name="Aurora",
    description="Northern lights -- shifting green-to-violet",
    category="playful",
    design_theme="aurora",
    color_preset="aurora",
    icon_style=ICON,
    animation_style=ANIMATION,
    pattern_style=PATTERN_NOISE,
    interaction_style=INTERACTION,
    illustration_style=ILLUST_HAND_DRAWN,
)
