"""Solarized -- The OG scientifically designed contrast palette."""

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
    background=ColorScale(44, 87, 94),
    foreground=ColorScale(194, 14, 40),
    card=ColorScale(46, 42, 88),
    card_foreground=ColorScale(194, 14, 40),
    popover=ColorScale(46, 42, 88),
    popover_foreground=ColorScale(194, 14, 40),
    primary=ColorScale(205, 82, 33),
    primary_foreground=ColorScale(44, 87, 94),
    secondary=ColorScale(46, 42, 88),
    secondary_foreground=ColorScale(194, 14, 40),
    muted=ColorScale(46, 42, 88),
    muted_foreground=ColorScale(196, 13, 55),
    accent=ColorScale(175, 59, 40),
    accent_foreground=ColorScale(44, 87, 94),
    destructive=ColorScale(1, 71, 52),
    destructive_foreground=ColorScale(44, 87, 94),
    success=ColorScale(68, 100, 30),
    success_foreground=ColorScale(44, 87, 94),
    warning=ColorScale(45, 100, 35),
    warning_foreground=ColorScale(194, 14, 40),
    info=ColorScale(175, 59, 40),
    info_foreground=ColorScale(44, 87, 94),
    link=ColorScale(205, 82, 33),
    link_hover=ColorScale(237, 45, 52),
    code=ColorScale(46, 42, 85),
    code_foreground=ColorScale(1, 71, 52),
    selection=ColorScale(205, 80, 85),
    selection_foreground=ColorScale(205, 10, 15),
    brand=ColorScale(175, 74, 63),
    brand_foreground=ColorScale(0, 0, 10),
    border=ColorScale(44, 20, 78),
    input=ColorScale(44, 20, 78),
    ring=ColorScale(205, 82, 33),
    surface_1=ColorScale(205, 5, 96),
    surface_2=ColorScale(205, 5, 93),
    surface_3=ColorScale(205, 5, 90),
)

DARK = ThemeTokens(
    background=ColorScale(192, 100, 11),
    foreground=ColorScale(44, 87, 94),
    card=ColorScale(192, 81, 14),
    card_foreground=ColorScale(44, 87, 94),
    popover=ColorScale(192, 81, 14),
    popover_foreground=ColorScale(44, 87, 94),
    primary=ColorScale(205, 82, 33),
    primary_foreground=ColorScale(44, 87, 94),
    secondary=ColorScale(192, 81, 18),
    secondary_foreground=ColorScale(44, 87, 94),
    muted=ColorScale(192, 81, 18),
    muted_foreground=ColorScale(180, 9, 63),
    accent=ColorScale(175, 59, 40),
    accent_foreground=ColorScale(44, 87, 94),
    destructive=ColorScale(1, 71, 52),
    destructive_foreground=ColorScale(44, 87, 94),
    success=ColorScale(68, 100, 30),
    success_foreground=ColorScale(44, 87, 94),
    warning=ColorScale(45, 100, 35),
    warning_foreground=ColorScale(192, 100, 11),
    info=ColorScale(175, 59, 40),
    info_foreground=ColorScale(44, 87, 94),
    link=ColorScale(205, 82, 33),
    link_hover=ColorScale(237, 45, 52),
    code=ColorScale(192, 81, 16),
    code_foreground=ColorScale(68, 100, 30),
    selection=ColorScale(205, 80, 25),
    selection_foreground=ColorScale(205, 10, 90),
    brand=ColorScale(175, 74, 63),
    brand_foreground=ColorScale(0, 0, 10),
    border=ColorScale(192, 81, 20),
    input=ColorScale(192, 81, 20),
    ring=ColorScale(205, 82, 33),
    surface_1=ColorScale(205, 5, 6),
    surface_2=ColorScale(205, 5, 10),
    surface_3=ColorScale(205, 5, 14),
)

PRESET = ThemePreset(
    name="solarized",
    display_name="Solarized",
    description="The OG scientifically designed contrast palette",
    light=LIGHT,
    dark=DARK,
)


# =============================================================================
# Design System
# =============================================================================

TYPOGRAPHY = TypographyStyle(
    name="solarized",
    heading_font='"Inter", system-ui, sans-serif',
    body_font='"Inter", system-ui, sans-serif',
    base_size="16px",
    heading_scale=1.2,
    line_height="1.5",
    body_line_height="1.6",
    heading_weight="500",
    section_heading_weight="500",
    body_weight="400",
    letter_spacing="normal",
    prose_max_width="44rem",
    badge_radius="9999px",
)

LAYOUT = LayoutStyle(
    name="solarized",
    space_unit="1rem",
    space_scale=1.5,
    border_radius_sm="4px",
    border_radius_md="6px",
    border_radius_lg="8px",
    button_shape="rounded",
    card_shape="rounded",
    input_shape="rounded",
    container_width="1100px",
    grid_gap="1.5rem",
    section_spacing="4rem",
    hero_padding_top="7rem",
    hero_padding_bottom="4rem",
    hero_line_height="1.15",
    hero_max_width="50rem",
)

SURFACE = SurfaceStyle(
    name="solarized",
    shadow_sm="0 1px 2px rgba(0, 0, 0, 0.06)",
    shadow_md="0 4px 6px -1px rgba(0, 0, 0, 0.08)",
    shadow_lg="0 8px 16px -2px rgba(0, 0, 0, 0.1)",
    border_width="1px",
    border_style="solid",
    surface_treatment="flat",
    backdrop_blur="0px",
    noise_opacity=0.0,
)

ICON = IconStyle(
    name="solarized",
    style="outlined",
    weight="regular",
    size_scale=1.0,
    stroke_width="1.5",
    corner_rounding="2px",
)

ANIMATION = AnimationStyle(
    name="solarized",
    entrance_effect="fade",
    exit_effect="fade",
    hover_effect="lift",
    hover_scale=1.0,
    hover_translate_y="-1px",
    click_effect="none",
    loading_style="spinner",
    transition_style="smooth",
    duration_fast="0.1s",
    duration_normal="0.15s",
    duration_slow="0.25s",
    easing="cubic-bezier(0.4, 0, 0.2, 1)",
)

INTERACTION = InteractionStyle(
    name="solarized",
    button_hover="lift",
    link_hover="color",
    card_hover="shadow",
    focus_style="ring",
    focus_ring_width="2px",
)

DESIGN_SYSTEM = DesignSystem(
    name="solarized",
    display_name="Solarized",
    description="Scientifically designed -- elegant dev classic",
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
    name="solarized",
    display_name="Solarized",
    description="Scientifically designed -- elegant dev classic",
    category="elegant",
    design_theme="solarized",
    color_preset="solarized",
    icon_style=ICON,
    animation_style=ANIMATION,
    pattern_style=PATTERN_MINIMAL,
    interaction_style=INTERACTION,
    illustration_style=ILLUST_LINE,
)
