"""Mission Control -- retro aerospace console: deep space navy, telemetry amber."""

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
    PATTERN_GRID,
)

# --- Color Preset ---

LIGHT = ThemeTokens(
    background=ColorScale(40, 22, 95),
    foreground=ColorScale(222, 45, 14),
    card=ColorScale(40, 20, 91),
    card_foreground=ColorScale(222, 45, 14),
    popover=ColorScale(40, 20, 91),
    popover_foreground=ColorScale(222, 45, 14),
    primary=ColorScale(35, 95, 42),
    primary_foreground=ColorScale(0, 0, 10),
    secondary=ColorScale(220, 15, 86),
    secondary_foreground=ColorScale(222, 45, 14),
    muted=ColorScale(40, 15, 88),
    muted_foreground=ColorScale(222, 25, 38),
    accent=ColorScale(135, 65, 30),
    accent_foreground=ColorScale(0, 0, 100),
    destructive=ColorScale(0, 82, 44),
    destructive_foreground=ColorScale(0, 0, 100),
    success=ColorScale(135, 65, 28),
    success_foreground=ColorScale(0, 0, 100),
    warning=ColorScale(45, 100, 42),
    warning_foreground=ColorScale(0, 0, 10),
    info=ColorScale(190, 75, 34),
    info_foreground=ColorScale(0, 0, 100),
    link=ColorScale(35, 95, 34),
    link_hover=ColorScale(35, 100, 28),
    code=ColorScale(222, 20, 90),
    code_foreground=ColorScale(135, 60, 26),
    selection=ColorScale(35, 90, 84),
    selection_foreground=ColorScale(222, 45, 12),
    brand=ColorScale(35, 95, 38),
    brand_foreground=ColorScale(0, 0, 100),
    border=ColorScale(220, 15, 78),
    input=ColorScale(220, 15, 78),
    ring=ColorScale(35, 95, 38),
    surface_1=ColorScale(40, 18, 93),
    surface_2=ColorScale(40, 18, 90),
    surface_3=ColorScale(40, 18, 87),
)

DARK = ThemeTokens(
    background=ColorScale(222, 45, 8),
    foreground=ColorScale(200, 20, 90),
    card=ColorScale(222, 40, 11),
    card_foreground=ColorScale(200, 20, 90),
    popover=ColorScale(222, 40, 11),
    popover_foreground=ColorScale(200, 20, 90),
    primary=ColorScale(38, 100, 55),
    primary_foreground=ColorScale(222, 45, 7),
    secondary=ColorScale(222, 35, 16),
    secondary_foreground=ColorScale(200, 20, 90),
    muted=ColorScale(222, 35, 16),
    muted_foreground=ColorScale(210, 15, 58),
    accent=ColorScale(135, 70, 48),
    accent_foreground=ColorScale(222, 45, 7),
    destructive=ColorScale(0, 80, 48),
    destructive_foreground=ColorScale(0, 0, 100),
    success=ColorScale(135, 70, 46),
    success_foreground=ColorScale(222, 45, 7),
    warning=ColorScale(45, 100, 55),
    warning_foreground=ColorScale(0, 0, 10),
    info=ColorScale(190, 80, 55),
    info_foreground=ColorScale(222, 45, 7),
    link=ColorScale(38, 100, 60),
    link_hover=ColorScale(38, 100, 70),
    code=ColorScale(222, 40, 13),
    code_foreground=ColorScale(135, 75, 55),
    selection=ColorScale(38, 80, 24),
    selection_foreground=ColorScale(200, 20, 92),
    brand=ColorScale(38, 100, 55),
    brand_foreground=ColorScale(222, 45, 7),
    border=ColorScale(222, 30, 20),
    input=ColorScale(222, 30, 20),
    ring=ColorScale(38, 100, 55),
    surface_1=ColorScale(222, 42, 6),
    surface_2=ColorScale(222, 42, 10),
    surface_3=ColorScale(222, 42, 14),
)

PRESET = ThemePreset(
    name="mission_control",
    display_name="Mission Control",
    description="Retro aerospace console — deep space navy, telemetry amber, signal green",
    light=LIGHT,
    dark=DARK,
    default_mode="dark",
)


# =============================================================================
# Design System
# =============================================================================

TYPOGRAPHY = TypographyStyle(
    name="mission_control",
    heading_font="'SFMono-Regular', Menlo, Consolas, 'Liberation Mono', monospace",
    body_font="system-ui, sans-serif",
    base_size="15px",
    heading_scale=1.2,
    line_height="1.3",
    body_line_height="1.6",
    heading_weight="600",
    section_heading_weight="600",
    body_weight="400",
    letter_spacing="0.03em",
    prose_max_width="46rem",
    badge_radius="3px",
)

LAYOUT = LayoutStyle(
    name="mission_control",
    space_unit="1rem",
    space_scale=1.4,
    border_radius_sm="2px",
    border_radius_md="3px",
    border_radius_lg="5px",
    button_shape="sharp",
    card_shape="sharp",
    input_shape="sharp",
    container_width="1200px",
    grid_gap="1.25rem",
    section_spacing="4rem",
    hero_padding_top="6rem",
    hero_padding_bottom="3.5rem",
    hero_line_height="1.15",
    hero_max_width="54rem",
)

SURFACE = SurfaceStyle(
    name="mission_control",
    shadow_sm="0 1px 2px rgba(0, 0, 0, 0.3)",
    shadow_md="0 2px 8px rgba(0, 0, 0, 0.4)",
    shadow_lg="0 6px 20px rgba(0, 0, 0, 0.5)",
    border_width="1px",
    border_style="solid",
    surface_treatment="flat",
    backdrop_blur="0px",
    noise_opacity=0.0,
)

ICON = IconStyle(
    name="mission_control",
    style="sharp",
    weight="regular",
    size_scale=0.95,
    stroke_width="1.5",
    corner_rounding="1px",
)

ANIMATION = AnimationStyle(
    name="mission_control",
    entrance_effect="fade",
    exit_effect="fade",
    hover_effect="none",
    hover_scale=1.0,
    hover_translate_y="0px",
    click_effect="none",
    loading_style="spinner",
    transition_style="instant",
    duration_fast="0.08s",
    duration_normal="0.15s",
    duration_slow="0.25s",
    easing="linear",
)

INTERACTION = InteractionStyle(
    name="mission_control",
    button_hover="darken",  # 2057: was "color" (undocumented, unconsumed by
    # pack_css_generator.py's button_hover dispatch — lift/scale/glow/darken
    # only; silent no-op). "darken" is the nearest consumed effect for a
    # color-shift-on-hover intent (filter: brightness(0.9), no motion).
    link_hover="underline",
    card_hover="border",
    focus_style="ring",
    focus_ring_width="2px",
)

DESIGN_SYSTEM = DesignSystem(
    name="mission_control",
    display_name="Mission Control",
    description="Retro aerospace console -- navy, telemetry amber, signal green",
    category="developer",
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
    name="mission_control",
    display_name="Mission Control",
    description="Retro aerospace console -- navy, telemetry amber, signal green",
    category="developer",
    design_theme="mission_control",
    color_preset="mission_control",
    icon_style=ICON,
    animation_style=ANIMATION,
    pattern_style=PATTERN_GRID,
    interaction_style=INTERACTION,
    illustration_style=ILLUST_LINE,
)
