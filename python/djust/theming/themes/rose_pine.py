"""Rosé Pine -- Elegant, muted pastels inspired by natural pine forests."""

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
    foreground=ColorScale(248, 25, 18),
    card=ColorScale(245, 50, 97),
    card_foreground=ColorScale(248, 25, 18),
    popover=ColorScale(245, 50, 97),
    popover_foreground=ColorScale(248, 25, 18),
    primary=ColorScale(343, 76, 68),
    primary_foreground=ColorScale(0, 0, 100),
    secondary=ColorScale(245, 22, 91),
    secondary_foreground=ColorScale(248, 25, 18),
    muted=ColorScale(245, 22, 91),
    muted_foreground=ColorScale(257, 9, 48),
    accent=ColorScale(245, 22, 91),
    accent_foreground=ColorScale(267, 57, 78),
    destructive=ColorScale(343, 76, 68),
    destructive_foreground=ColorScale(0, 0, 100),
    success=ColorScale(197, 49, 38),
    success_foreground=ColorScale(0, 0, 100),
    warning=ColorScale(35, 88, 72),
    warning_foreground=ColorScale(0, 0, 10),
    info=ColorScale(189, 43, 73),
    info_foreground=ColorScale(0, 0, 10),
    link=ColorScale(267, 57, 78),
    link_hover=ColorScale(267, 57, 68),
    code=ColorScale(245, 50, 97),
    code_foreground=ColorScale(248, 25, 18),
    selection=ColorScale(343, 76, 88),
    selection_foreground=ColorScale(248, 25, 18),
    brand=ColorScale(267, 57, 78),
    brand_foreground=ColorScale(0, 0, 100),
    border=ColorScale(245, 22, 91),
    input=ColorScale(0, 0, 100),
    ring=ColorScale(343, 76, 68),
    surface_1=ColorScale(249, 18, 98),
    surface_2=ColorScale(249, 14, 96),
    surface_3=ColorScale(249, 10, 93),
)

DARK = ThemeTokens(
    background=ColorScale(249, 22, 12),
    foreground=ColorScale(245, 50, 91),
    card=ColorScale(250, 23, 17),
    card_foreground=ColorScale(245, 50, 91),
    popover=ColorScale(250, 23, 17),
    popover_foreground=ColorScale(245, 50, 91),
    primary=ColorScale(343, 76, 68),
    primary_foreground=ColorScale(0, 0, 100),
    secondary=ColorScale(247, 23, 20),
    secondary_foreground=ColorScale(245, 50, 91),
    muted=ColorScale(247, 23, 20),
    muted_foreground=ColorScale(249, 15, 56),
    accent=ColorScale(247, 23, 20),
    accent_foreground=ColorScale(267, 57, 78),
    destructive=ColorScale(343, 76, 68),
    destructive_foreground=ColorScale(0, 0, 100),
    success=ColorScale(197, 49, 38),
    success_foreground=ColorScale(0, 0, 100),
    warning=ColorScale(35, 88, 72),
    warning_foreground=ColorScale(0, 0, 10),
    info=ColorScale(189, 43, 73),
    info_foreground=ColorScale(0, 0, 10),
    link=ColorScale(267, 57, 78),
    link_hover=ColorScale(267, 57, 88),
    code=ColorScale(248, 24, 10),
    code_foreground=ColorScale(245, 50, 91),
    selection=ColorScale(267, 57, 78),
    selection_foreground=ColorScale(0, 0, 100),
    brand=ColorScale(267, 57, 78),
    brand_foreground=ColorScale(0, 0, 100),
    border=ColorScale(247, 23, 20),
    input=ColorScale(250, 23, 17),
    ring=ColorScale(343, 76, 68),
    surface_1=ColorScale(249, 20, 5),
    surface_2=ColorScale(249, 16, 8),
    surface_3=ColorScale(249, 12, 12),
)

PRESET = ThemePreset(
    name="rose_pine",
    display_name="Rosé Pine",
    description="Elegant, muted pastels inspired by natural pine forests",
    light=LIGHT,
    dark=DARK,
)


# =============================================================================
# Design System
# =============================================================================

TYPOGRAPHY = TypographyStyle(
    name="rose_pine",
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
    name="rose_pine",
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
    name="rose_pine",
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
    name="rose_pine",
    style="outlined",
    weight="regular",
    size_scale=1.0,
    stroke_width="1.5",
    corner_rounding="2px",
)

ANIMATION = AnimationStyle(
    name="rose_pine",
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
    name="rose_pine",
    button_hover="lift",
    link_hover="color",
    card_hover="shadow",
    focus_style="ring",
    focus_ring_width="2px",
)

DESIGN_SYSTEM = DesignSystem(
    name="rose_pine",
    display_name="Rosé Pine",
    description="Elegant muted pastels -- dev classic",
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
    name="rose_pine",
    display_name="Rosé Pine",
    description="Elegant muted pastels -- dev classic",
    category="elegant",
    design_theme="rose_pine",
    color_preset="rose_pine",
    icon_style=ICON,
    animation_style=ANIMATION,
    pattern_style=PATTERN_MINIMAL,
    interaction_style=INTERACTION,
    illustration_style=ILLUST_LINE,
)
