"""Sakura -- Japanese spring: washi paper, blossom pink, matcha green."""

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
    background=ColorScale(35, 30, 97),
    foreground=ColorScale(340, 15, 18),
    card=ColorScale(35, 28, 94),
    card_foreground=ColorScale(340, 15, 18),
    popover=ColorScale(35, 28, 94),
    popover_foreground=ColorScale(340, 15, 18),
    primary=ColorScale(340, 65, 50),
    primary_foreground=ColorScale(0, 0, 100),
    secondary=ColorScale(345, 25, 90),
    secondary_foreground=ColorScale(340, 15, 18),
    muted=ColorScale(35, 20, 90),
    muted_foreground=ColorScale(340, 10, 42),
    accent=ColorScale(95, 38, 34),
    accent_foreground=ColorScale(0, 0, 100),
    destructive=ColorScale(355, 75, 48),
    destructive_foreground=ColorScale(0, 0, 100),
    success=ColorScale(110, 38, 36),
    success_foreground=ColorScale(0, 0, 100),
    warning=ColorScale(42, 88, 48),
    warning_foreground=ColorScale(0, 0, 10),
    info=ColorScale(250, 45, 55),
    info_foreground=ColorScale(0, 0, 100),
    link=ColorScale(340, 65, 45),
    link_hover=ColorScale(340, 70, 38),
    code=ColorScale(345, 22, 92),
    code_foreground=ColorScale(340, 55, 38),
    selection=ColorScale(340, 60, 88),
    selection_foreground=ColorScale(340, 20, 15),
    brand=ColorScale(340, 65, 50),
    brand_foreground=ColorScale(0, 0, 100),
    border=ColorScale(35, 20, 84),
    input=ColorScale(35, 20, 84),
    ring=ColorScale(340, 65, 50),
    surface_1=ColorScale(345, 20, 96),
    surface_2=ColorScale(345, 20, 93),
    surface_3=ColorScale(345, 20, 90),
)

DARK = ThemeTokens(
    background=ColorScale(255, 20, 11),
    foreground=ColorScale(345, 25, 90),
    card=ColorScale(255, 18, 15),
    card_foreground=ColorScale(345, 25, 90),
    popover=ColorScale(255, 18, 15),
    popover_foreground=ColorScale(345, 25, 90),
    primary=ColorScale(340, 75, 70),
    primary_foreground=ColorScale(255, 20, 11),
    secondary=ColorScale(255, 15, 20),
    secondary_foreground=ColorScale(345, 25, 90),
    muted=ColorScale(255, 15, 20),
    muted_foreground=ColorScale(345, 12, 60),
    accent=ColorScale(95, 32, 58),
    accent_foreground=ColorScale(255, 20, 11),
    destructive=ColorScale(355, 70, 50),
    destructive_foreground=ColorScale(0, 0, 100),
    success=ColorScale(110, 38, 55),
    success_foreground=ColorScale(255, 20, 11),
    warning=ColorScale(42, 88, 58),
    warning_foreground=ColorScale(0, 0, 10),
    info=ColorScale(250, 55, 68),
    info_foreground=ColorScale(255, 20, 11),
    link=ColorScale(340, 75, 70),
    link_hover=ColorScale(340, 80, 78),
    code=ColorScale(255, 18, 17),
    code_foreground=ColorScale(340, 70, 72),
    selection=ColorScale(340, 50, 28),
    selection_foreground=ColorScale(345, 25, 92),
    brand=ColorScale(340, 75, 70),
    brand_foreground=ColorScale(255, 20, 11),
    border=ColorScale(255, 15, 24),
    input=ColorScale(255, 15, 24),
    ring=ColorScale(340, 75, 70),
    surface_1=ColorScale(255, 18, 8),
    surface_2=ColorScale(255, 18, 12),
    surface_3=ColorScale(255, 18, 16),
)

PRESET = ThemePreset(
    name="sakura",
    display_name="Sakura",
    description="Japanese spring — washi paper, blossom pink, matcha green",
    light=LIGHT,
    dark=DARK,
    default_mode="light",
)


# =============================================================================
# Design System
# =============================================================================

TYPOGRAPHY = TypographyStyle(
    name="sakura",
    heading_font="Georgia, 'Times New Roman', 'Hiragino Mincho ProN', serif",
    body_font="system-ui, 'Hiragino Sans', sans-serif",
    base_size="16px",
    heading_scale=1.28,
    line_height="1.4",
    body_line_height="1.75",
    heading_weight="500",
    section_heading_weight="500",
    body_weight="400",
    letter_spacing="0.01em",
    prose_max_width="40rem",
    badge_radius="9999px",
)

LAYOUT = LayoutStyle(
    name="sakura",
    space_unit="1rem",
    space_scale=1.6,
    border_radius_sm="8px",
    border_radius_md="12px",
    border_radius_lg="18px",
    button_shape="rounded",
    card_shape="rounded",
    input_shape="rounded",
    container_width="1040px",
    grid_gap="1.75rem",
    section_spacing="5rem",
    hero_padding_top="7.5rem",
    hero_padding_bottom="4.5rem",
    hero_line_height="1.2",
    hero_max_width="46rem",
)

SURFACE = SurfaceStyle(
    name="sakura",
    shadow_sm="0 1px 3px rgba(190, 90, 130, 0.07)",
    shadow_md="0 4px 10px rgba(190, 90, 130, 0.09)",
    shadow_lg="0 10px 28px rgba(190, 90, 130, 0.11)",
    border_width="1px",
    border_style="solid",
    surface_treatment="flat",
    backdrop_blur="0px",
    noise_opacity=0.0,
)

ICON = IconStyle(
    name="sakura",
    style="rounded",
    weight="light",
    size_scale=1.0,
    stroke_width="1.25",
    corner_rounding="4px",
)

ANIMATION = AnimationStyle(
    name="sakura",
    entrance_effect="fade",
    exit_effect="fade",
    hover_effect="lift",
    hover_scale=1.0,
    hover_translate_y="-1px",
    click_effect="pulse",
    loading_style="pulse",
    transition_style="smooth",
    duration_fast="0.18s",
    duration_normal="0.3s",
    duration_slow="0.5s",
    easing="cubic-bezier(0.23, 1, 0.32, 1)",
)

INTERACTION = InteractionStyle(
    name="sakura",
    button_hover="lift",
    link_hover="color",
    card_hover="lift",
    focus_style="ring",
    focus_ring_width="2px",
)

DESIGN_SYSTEM = DesignSystem(
    name="sakura",
    display_name="Sakura",
    description="Japanese spring -- washi paper, blossom pink, matcha green",
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
    name="sakura",
    display_name="Sakura",
    description="Japanese spring -- washi paper, blossom pink, matcha green",
    category="elegant",
    design_theme="sakura",
    color_preset="sakura",
    icon_style=ICON,
    animation_style=ANIMATION,
    pattern_style=PATTERN_MINIMAL,
    interaction_style=INTERACTION,
    illustration_style=ILLUST_LINE,
)
