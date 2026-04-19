"""Default -- Neutral zinc theme with professional aesthetics."""

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
    background=ColorScale(0, 0, 100),
    foreground=ColorScale(240, 10, 4),
    card=ColorScale(0, 0, 100),
    card_foreground=ColorScale(240, 10, 4),
    popover=ColorScale(0, 0, 100),
    popover_foreground=ColorScale(240, 10, 4),
    primary=ColorScale(240, 6, 10),
    primary_foreground=ColorScale(0, 0, 98),
    secondary=ColorScale(240, 5, 96),
    secondary_foreground=ColorScale(240, 6, 10),
    muted=ColorScale(240, 5, 96),
    muted_foreground=ColorScale(240, 5, 40),
    accent=ColorScale(240, 5, 96),
    accent_foreground=ColorScale(240, 6, 10),
    destructive=ColorScale(0, 84, 60),
    destructive_foreground=ColorScale(0, 0, 98),
    success=ColorScale(142, 76, 36),
    success_foreground=ColorScale(0, 0, 98),
    warning=ColorScale(38, 92, 50),
    warning_foreground=ColorScale(0, 0, 98),
    info=ColorScale(199, 89, 48),
    info_foreground=ColorScale(0, 0, 98),
    link=ColorScale(221, 83, 53),
    link_hover=ColorScale(221, 83, 45),
    code=ColorScale(240, 5, 94),
    code_foreground=ColorScale(240, 10, 20),
    selection=ColorScale(240, 100, 80),
    selection_foreground=ColorScale(240, 10, 4),
    brand=ColorScale(240, 6, 10),
    brand_foreground=ColorScale(0, 0, 98),
    border=ColorScale(240, 6, 90),
    input=ColorScale(240, 6, 90),
    ring=ColorScale(240, 6, 10),
    surface_1=ColorScale(240, 10, 98),
    surface_2=ColorScale(240, 8, 96),
    surface_3=ColorScale(240, 6, 93),
)

DARK = ThemeTokens(
    background=ColorScale(240, 10, 4),
    foreground=ColorScale(0, 0, 98),
    card=ColorScale(240, 10, 4),
    card_foreground=ColorScale(0, 0, 98),
    popover=ColorScale(240, 10, 4),
    popover_foreground=ColorScale(0, 0, 98),
    primary=ColorScale(0, 0, 98),
    primary_foreground=ColorScale(240, 6, 10),
    secondary=ColorScale(240, 4, 16),
    secondary_foreground=ColorScale(0, 0, 98),
    muted=ColorScale(240, 4, 16),
    muted_foreground=ColorScale(240, 5, 75),
    accent=ColorScale(240, 4, 16),
    accent_foreground=ColorScale(0, 0, 98),
    destructive=ColorScale(0, 62, 30),
    destructive_foreground=ColorScale(0, 0, 98),
    success=ColorScale(142, 69, 28),
    success_foreground=ColorScale(0, 0, 98),
    warning=ColorScale(38, 92, 40),
    warning_foreground=ColorScale(0, 0, 98),
    info=ColorScale(199, 89, 60),
    info_foreground=ColorScale(0, 0, 98),
    link=ColorScale(221, 83, 65),
    link_hover=ColorScale(221, 83, 75),
    code=ColorScale(240, 4, 12),
    code_foreground=ColorScale(240, 5, 80),
    selection=ColorScale(240, 100, 30),
    selection_foreground=ColorScale(0, 0, 98),
    brand=ColorScale(0, 0, 98),
    brand_foreground=ColorScale(240, 6, 10),
    border=ColorScale(240, 4, 16),
    input=ColorScale(240, 4, 16),
    ring=ColorScale(240, 5, 84),
    surface_1=ColorScale(240, 12, 4),
    surface_2=ColorScale(240, 10, 6),
    surface_3=ColorScale(240, 8, 10),
)

PRESET = ThemePreset(
    name="default",
    display_name="Default",
    description="Neutral zinc theme with professional aesthetics",
    light=LIGHT,
    dark=DARK,
)


# =============================================================================
# Design System
# =============================================================================

TYPOGRAPHY = TypographyStyle(
    name="default",
    heading_font='"Inter", system-ui, sans-serif',
    body_font='"Inter", system-ui, sans-serif',
    base_size="16px",
    heading_scale=1.25,
    line_height="1.5",
    body_line_height="1.6",
    heading_weight="600",
    section_heading_weight="600",
    body_weight="400",
    letter_spacing="-0.01em",
    prose_max_width="42rem",
    badge_radius="9999px",
)

LAYOUT = LayoutStyle(
    name="default",
    space_unit="1rem",
    space_scale=1.5,
    border_radius_sm="4px",
    border_radius_md="6px",
    border_radius_lg="8px",
    button_shape="rounded",
    card_shape="rounded",
    input_shape="rounded",
    container_width="1200px",
    grid_gap="1.5rem",
    section_spacing="4rem",
    hero_padding_top="7rem",
    hero_padding_bottom="4rem",
    hero_line_height="1.15",
    hero_max_width="52rem",
)

SURFACE = SurfaceStyle(
    name="default",
    shadow_sm="0 1px 3px rgba(0, 0, 0, 0.08), 0 1px 2px rgba(0, 0, 0, 0.06)",
    shadow_md="0 4px 8px -1px rgba(0, 0, 0, 0.1), 0 2px 4px -1px rgba(0, 0, 0, 0.06)",
    shadow_lg="0 10px 20px -3px rgba(0, 0, 0, 0.1), 0 4px 6px -2px rgba(0, 0, 0, 0.05)",
    border_width="1px",
    border_style="solid",
    surface_treatment="flat",
    backdrop_blur="0px",
    noise_opacity=0.0,
)

ICON = IconStyle(
    name="default",
    style="outlined",
    weight="regular",
    size_scale=1.0,
    stroke_width="1.5",
    corner_rounding="2px",
)

ANIMATION = AnimationStyle(
    name="default",
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
    name="default",
    button_hover="lift",
    link_hover="color",
    card_hover="shadow",
    focus_style="ring",
    focus_ring_width="2px",
)

DESIGN_SYSTEM = DesignSystem(
    name="default",
    display_name="Default",
    description="Neutral zinc -- clean, professional, no personality",
    category="minimal",
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
    name="default",
    display_name="Default",
    description="Neutral zinc -- clean, professional, no personality",
    category="minimal",
    design_theme="default",
    color_preset="default",
    icon_style=ICON,
    animation_style=ANIMATION,
    pattern_style=PATTERN_MINIMAL,
    interaction_style=INTERACTION,
    illustration_style=ILLUST_LINE,
)
