"""Dune -- desert expanse: warm sand, terracotta, oasis teal."""

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
    PATTERN_NOISE,
)

# --- Color Preset ---

LIGHT = ThemeTokens(
    background=ColorScale(38, 45, 94),
    foreground=ColorScale(25, 40, 16),
    card=ColorScale(38, 42, 90),
    card_foreground=ColorScale(25, 40, 16),
    popover=ColorScale(38, 42, 90),
    popover_foreground=ColorScale(25, 40, 16),
    primary=ColorScale(16, 62, 44),
    primary_foreground=ColorScale(0, 0, 100),
    secondary=ColorScale(35, 35, 85),
    secondary_foreground=ColorScale(25, 40, 16),
    muted=ColorScale(38, 30, 87),
    muted_foreground=ColorScale(28, 22, 40),
    accent=ColorScale(175, 55, 30),
    accent_foreground=ColorScale(0, 0, 100),
    destructive=ColorScale(4, 70, 44),
    destructive_foreground=ColorScale(0, 0, 100),
    success=ColorScale(150, 42, 32),
    success_foreground=ColorScale(0, 0, 100),
    warning=ColorScale(40, 92, 46),
    warning_foreground=ColorScale(0, 0, 10),
    info=ColorScale(205, 60, 42),
    info_foreground=ColorScale(0, 0, 100),
    link=ColorScale(16, 62, 42),
    link_hover=ColorScale(175, 55, 28),
    code=ColorScale(38, 35, 88),
    code_foreground=ColorScale(16, 58, 36),
    selection=ColorScale(28, 70, 84),
    selection_foreground=ColorScale(25, 40, 14),
    brand=ColorScale(16, 62, 44),
    brand_foreground=ColorScale(0, 0, 100),
    border=ColorScale(35, 30, 78),
    input=ColorScale(35, 30, 78),
    ring=ColorScale(16, 62, 44),
    surface_1=ColorScale(38, 40, 92),
    surface_2=ColorScale(38, 38, 89),
    surface_3=ColorScale(38, 36, 86),
)

DARK = ThemeTokens(
    background=ColorScale(25, 28, 9),
    foreground=ColorScale(38, 35, 88),
    card=ColorScale(25, 25, 13),
    card_foreground=ColorScale(38, 35, 88),
    popover=ColorScale(25, 25, 13),
    popover_foreground=ColorScale(38, 35, 88),
    primary=ColorScale(18, 72, 58),
    primary_foreground=ColorScale(25, 30, 8),
    secondary=ColorScale(25, 22, 18),
    secondary_foreground=ColorScale(38, 35, 88),
    muted=ColorScale(25, 22, 18),
    muted_foreground=ColorScale(35, 18, 58),
    accent=ColorScale(175, 48, 52),
    accent_foreground=ColorScale(25, 30, 8),
    destructive=ColorScale(4, 70, 50),
    destructive_foreground=ColorScale(0, 0, 100),
    success=ColorScale(150, 42, 50),
    success_foreground=ColorScale(25, 30, 8),
    warning=ColorScale(40, 92, 56),
    warning_foreground=ColorScale(0, 0, 10),
    info=ColorScale(205, 58, 58),
    info_foreground=ColorScale(25, 30, 8),
    link=ColorScale(18, 72, 62),
    link_hover=ColorScale(175, 48, 58),
    code=ColorScale(25, 24, 15),
    code_foreground=ColorScale(18, 68, 64),
    selection=ColorScale(18, 55, 26),
    selection_foreground=ColorScale(38, 35, 90),
    brand=ColorScale(18, 72, 58),
    brand_foreground=ColorScale(25, 30, 8),
    border=ColorScale(25, 22, 22),
    input=ColorScale(25, 22, 22),
    ring=ColorScale(18, 72, 58),
    surface_1=ColorScale(25, 26, 7),
    surface_2=ColorScale(25, 26, 11),
    surface_3=ColorScale(25, 26, 15),
)

PRESET = ThemePreset(
    name="dune",
    display_name="Dune",
    description="Desert expanse — warm sand, terracotta, oasis teal",
    light=LIGHT,
    dark=DARK,
    default_mode="light",
)


# =============================================================================
# Design System
# =============================================================================

TYPOGRAPHY = TypographyStyle(
    name="dune",
    heading_font="system-ui, sans-serif",
    body_font="system-ui, sans-serif",
    base_size="16px",
    heading_scale=1.26,
    line_height="1.4",
    body_line_height="1.7",
    heading_weight="600",
    section_heading_weight="600",
    body_weight="400",
    letter_spacing="0.005em",
    prose_max_width="44rem",
    badge_radius="9999px",
)

LAYOUT = LayoutStyle(
    name="dune",
    space_unit="1rem",
    space_scale=1.6,
    border_radius_sm="6px",
    border_radius_md="10px",
    border_radius_lg="16px",
    button_shape="rounded",
    card_shape="rounded",
    input_shape="rounded",
    container_width="1180px",
    grid_gap="1.75rem",
    section_spacing="5.5rem",
    hero_padding_top="8rem",
    hero_padding_bottom="5rem",
    hero_line_height="1.15",
    hero_max_width="50rem",
)

SURFACE = SurfaceStyle(
    name="dune",
    shadow_sm="0 1px 3px rgba(120, 72, 30, 0.10)",
    shadow_md="0 4px 12px rgba(120, 72, 30, 0.12)",
    shadow_lg="0 10px 30px rgba(120, 72, 30, 0.15)",
    border_width="1px",
    border_style="solid",
    surface_treatment="flat",
    backdrop_blur="0px",
    noise_opacity=0.04,
)

ICON = IconStyle(
    name="dune",
    style="rounded",
    weight="regular",
    size_scale=1.0,
    stroke_width="1.5",
    corner_rounding="3px",
)

ANIMATION = AnimationStyle(
    name="dune",
    entrance_effect="fade",
    exit_effect="fade",
    hover_effect="lift",
    hover_scale=1.0,
    hover_translate_y="-2px",
    click_effect="pulse",
    loading_style="pulse",
    transition_style="smooth",
    duration_fast="0.16s",
    duration_normal="0.28s",
    duration_slow="0.45s",
    easing="cubic-bezier(0.25, 0.1, 0.25, 1)",
)

INTERACTION = InteractionStyle(
    name="dune",
    button_hover="lift",
    link_hover="color",
    card_hover="lift",
    focus_style="ring",
    focus_ring_width="2px",
)

DESIGN_SYSTEM = DesignSystem(
    name="dune",
    display_name="Dune",
    description="Desert expanse -- warm sand, terracotta, oasis teal",
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
    name="dune",
    display_name="Dune",
    description="Desert expanse -- warm sand, terracotta, oasis teal",
    category="minimal",
    design_theme="dune",
    color_preset="dune",
    icon_style=ICON,
    animation_style=ANIMATION,
    pattern_style=PATTERN_NOISE,
    interaction_style=INTERACTION,
    illustration_style=ILLUST_FLAT,
)
