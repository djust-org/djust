"""Everforest — Comfortable green on warm dark, protects your eyes."""

from ._base import (
    ColorScale,
    ThemeTokens,
    ThemePreset,
    TypographyStyle,
    LayoutStyle,
    SurfaceStyle,
    IconStyle,
    AnimationStyle,
    InteractionStyle,
    DesignSystem,
    ThemePack,
    PATTERN_MINIMAL,
    ILLUST_LINE,
)


# =============================================================================
# Color Preset
# =============================================================================
# Colors from Everforest (sainnhe/everforest):
# Dark: bg #2d353b, fg #d3c6aa, green #a7c080
# Light: bg #fdf6e3, fg #5c6a72
# Accents: red #e67e80, orange #e69875, yellow #dbbc7f
# Blue #7fbbb3, aqua #83c092

LIGHT = ThemeTokens(
    background=ColorScale(44, 87, 94),  # #fdf6e3 — warm cream
    foreground=ColorScale(202, 11, 40),  # #5c6a72
    card=ColorScale(44, 60, 97),
    card_foreground=ColorScale(202, 11, 40),
    popover=ColorScale(44, 60, 97),
    popover_foreground=ColorScale(202, 11, 40),
    primary=ColorScale(83, 34, 63),  # Green #a7c080
    primary_foreground=ColorScale(202, 11, 20),
    secondary=ColorScale(44, 50, 91),
    secondary_foreground=ColorScale(202, 11, 40),
    muted=ColorScale(44, 35, 86),
    muted_foreground=ColorScale(202, 11, 55),
    accent=ColorScale(44, 50, 91),  # Subtle warm hover
    accent_foreground=ColorScale(202, 11, 40),
    destructive=ColorScale(359, 68, 70),  # Red #e67e80
    destructive_foreground=ColorScale(0, 0, 100),
    success=ColorScale(83, 34, 63),  # Green #a7c080
    success_foreground=ColorScale(202, 11, 20),
    warning=ColorScale(40, 56, 68),  # Yellow #dbbc7f
    warning_foreground=ColorScale(202, 11, 20),
    info=ColorScale(172, 31, 62),  # Blue #7fbbb3
    info_foreground=ColorScale(202, 11, 20),
    link=ColorScale(172, 31, 52),  # Blue darker for light bg
    link_hover=ColorScale(172, 31, 42),
    code=ColorScale(206, 13, 20),  # Dark code bg
    code_foreground=ColorScale(83, 34, 70),  # Green code text
    selection=ColorScale(83, 34, 88),  # Pale green selection
    selection_foreground=ColorScale(202, 11, 20),
    brand=ColorScale(83, 34, 63),  # Green
    brand_foreground=ColorScale(202, 11, 20),
    border=ColorScale(44, 25, 82),
    input=ColorScale(44, 35, 90),
    ring=ColorScale(83, 34, 63),
    surface_1=ColorScale(44, 87, 94),
    surface_2=ColorScale(44, 50, 91),
    surface_3=ColorScale(44, 35, 88),
)

DARK = ThemeTokens(
    background=ColorScale(206, 13, 20),  # #2d353b
    foreground=ColorScale(41, 32, 75),  # #d3c6aa
    card=ColorScale(206, 12, 24),
    card_foreground=ColorScale(41, 32, 75),
    popover=ColorScale(206, 12, 24),
    popover_foreground=ColorScale(41, 32, 75),
    primary=ColorScale(83, 34, 63),  # Green #a7c080
    primary_foreground=ColorScale(206, 13, 15),
    secondary=ColorScale(206, 11, 27),
    secondary_foreground=ColorScale(41, 32, 75),
    muted=ColorScale(206, 10, 30),
    muted_foreground=ColorScale(41, 15, 50),
    accent=ColorScale(206, 11, 27),  # Subtle dark hover
    accent_foreground=ColorScale(41, 32, 75),
    destructive=ColorScale(359, 68, 70),  # Red #e67e80
    destructive_foreground=ColorScale(206, 13, 15),
    success=ColorScale(83, 34, 63),  # Green
    success_foreground=ColorScale(206, 13, 15),
    warning=ColorScale(40, 56, 68),  # Yellow
    warning_foreground=ColorScale(206, 13, 15),
    info=ColorScale(172, 31, 62),  # Blue
    info_foreground=ColorScale(206, 13, 15),
    link=ColorScale(172, 31, 62),  # Blue
    link_hover=ColorScale(172, 31, 72),
    code=ColorScale(206, 13, 15),  # Very dark code bg
    code_foreground=ColorScale(83, 34, 70),  # Green code text
    selection=ColorScale(83, 25, 24),  # Dark green selection
    selection_foreground=ColorScale(41, 32, 82),
    brand=ColorScale(83, 34, 63),  # Green
    brand_foreground=ColorScale(206, 13, 15),
    border=ColorScale(206, 10, 30),
    input=ColorScale(206, 10, 30),
    ring=ColorScale(83, 34, 63),
    surface_1=ColorScale(206, 13, 16),
    surface_2=ColorScale(206, 13, 20),
    surface_3=ColorScale(206, 12, 24),
)

PRESET = ThemePreset(
    name="everforest",
    display_name="Everforest",
    description="Comfortable green on warm dark, protects your eyes",
    light=LIGHT,
    dark=DARK,
    radius=0.375,
)


# =============================================================================
# Design System
# =============================================================================

TYPOGRAPHY = TypographyStyle(
    name="everforest",
    heading_font="system-ui, sans-serif",
    body_font="system-ui, sans-serif",
    base_size="16px",
    heading_scale=1.25,
    line_height="1.5",
    body_line_height="1.6",
    heading_weight="500",
    section_heading_weight="500",
    body_weight="400",
    letter_spacing="normal",
    prose_max_width="42rem",
    badge_radius="9999px",
)

LAYOUT = LayoutStyle(
    name="everforest",
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
    hero_padding_bottom="5rem",
    hero_line_height="1.15",
    hero_max_width="52rem",
)

SURFACE = SurfaceStyle(
    name="everforest",
    shadow_sm="0 1px 3px rgba(0, 0, 0, 0.08)",
    shadow_md="0 3px 8px rgba(0, 0, 0, 0.1)",
    shadow_lg="0 6px 16px rgba(0, 0, 0, 0.12)",
    border_width="1px",
    border_style="solid",
    surface_treatment="flat",
    backdrop_blur="0px",
    noise_opacity=0.0,
)

ICON = IconStyle(
    name="everforest",
    style="outlined",
    weight="regular",
    size_scale=1.0,
    stroke_width="1.5",
    corner_rounding="2px",
)

ANIMATION = AnimationStyle(
    name="everforest",
    entrance_effect="fade",
    exit_effect="fade",
    hover_effect="lift",
    hover_scale=1.0,
    hover_translate_y="-1px",
    click_effect="pulse",
    loading_style="spinner",
    transition_style="smooth",
    duration_fast="0.12s",
    duration_normal="0.2s",
    duration_slow="0.3s",
    easing="cubic-bezier(0.4, 0, 0.2, 1)",
)

INTERACTION = InteractionStyle(
    name="everforest",
    button_hover="lift",
    link_hover="color",
    card_hover="shadow",
    focus_style="ring",
    focus_ring_width="2px",
)

DESIGN_SYSTEM = DesignSystem(
    name="everforest",
    display_name="Everforest",
    description="Gentle dev theme — warm subtle shadows, comfortable spacing",
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
    name="everforest",
    display_name="Everforest",
    description="Comfortable green on warm dark, protects your eyes",
    category="developer",
    design_theme="everforest",
    color_preset="everforest",
    icon_style=ICON,
    animation_style=ANIMATION,
    pattern_style=PATTERN_MINIMAL,
    interaction_style=INTERACTION,
    illustration_style=ILLUST_LINE,
)
