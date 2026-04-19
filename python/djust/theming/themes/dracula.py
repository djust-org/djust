"""Dracula -- Pixel-perfect official palette with Alucard light mode."""

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
    background=ColorScale(45, 100, 96),
    foreground=ColorScale(0, 0, 12),
    card=ColorScale(45, 60, 93),
    card_foreground=ColorScale(0, 0, 12),
    popover=ColorScale(45, 80, 95),
    popover_foreground=ColorScale(0, 0, 12),
    primary=ColorScale(265, 89, 55),
    primary_foreground=ColorScale(0, 0, 100),
    secondary=ColorScale(45, 30, 90),
    secondary_foreground=ColorScale(0, 0, 12),
    muted=ColorScale(45, 30, 88),
    muted_foreground=ColorScale(0, 0, 40),
    accent=ColorScale(326, 100, 45),
    accent_foreground=ColorScale(0, 0, 100),
    destructive=ColorScale(0, 100, 50),
    destructive_foreground=ColorScale(0, 0, 100),
    success=ColorScale(135, 80, 35),
    success_foreground=ColorScale(0, 0, 100),
    warning=ColorScale(31, 100, 50),
    warning_foreground=ColorScale(0, 0, 100),
    info=ColorScale(191, 97, 40),
    info_foreground=ColorScale(0, 0, 100),
    link=ColorScale(326, 100, 40),
    link_hover=ColorScale(265, 89, 45),
    code=ColorScale(45, 40, 90),
    code_foreground=ColorScale(265, 89, 40),
    selection=ColorScale(240, 20, 84),
    selection_foreground=ColorScale(0, 0, 12),
    brand=ColorScale(326, 100, 74),
    brand_foreground=ColorScale(231, 15, 18),
    border=ColorScale(240, 15, 82),
    input=ColorScale(240, 15, 85),
    ring=ColorScale(265, 89, 55),
    surface_1=ColorScale(45, 60, 97),
    surface_2=ColorScale(45, 40, 94),
    surface_3=ColorScale(45, 30, 91),
)

DARK = ThemeTokens(
    background=ColorScale(231, 15, 18),
    foreground=ColorScale(60, 30, 96),
    card=ColorScale(232, 15, 15),
    card_foreground=ColorScale(60, 30, 96),
    popover=ColorScale(231, 15, 24),
    popover_foreground=ColorScale(60, 30, 96),
    primary=ColorScale(265, 89, 78),
    primary_foreground=ColorScale(231, 15, 18),
    secondary=ColorScale(231, 8, 29),
    secondary_foreground=ColorScale(60, 30, 96),
    muted=ColorScale(232, 14, 31),
    muted_foreground=ColorScale(225, 27, 51),
    accent=ColorScale(326, 100, 74),
    accent_foreground=ColorScale(231, 15, 18),
    destructive=ColorScale(0, 100, 67),
    destructive_foreground=ColorScale(60, 30, 96),
    success=ColorScale(135, 94, 65),
    success_foreground=ColorScale(0, 0, 10),
    warning=ColorScale(31, 100, 71),
    warning_foreground=ColorScale(0, 0, 10),
    info=ColorScale(191, 97, 77),
    info_foreground=ColorScale(0, 0, 10),
    link=ColorScale(326, 100, 74),
    link_hover=ColorScale(265, 89, 78),
    code=ColorScale(230, 15, 11),
    code_foreground=ColorScale(191, 97, 77),
    selection=ColorScale(232, 14, 31),
    selection_foreground=ColorScale(60, 30, 96),
    brand=ColorScale(326, 100, 74),
    brand_foreground=ColorScale(231, 15, 18),
    border=ColorScale(225, 27, 51),
    input=ColorScale(232, 14, 31),
    ring=ColorScale(265, 89, 78),
    surface_1=ColorScale(230, 15, 11),
    surface_2=ColorScale(232, 15, 15),
    surface_3=ColorScale(231, 15, 24),
)

PRESET = ThemePreset(
    name="dracula",
    display_name="Dracula",
    description="Pixel-perfect official palette with Alucard light mode",
    light=LIGHT,
    dark=DARK,
    default_mode="dark",
)


# =============================================================================
# Design System
# =============================================================================

TYPOGRAPHY = TypographyStyle(
    name="dracula",
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
    name="dracula",
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
    name="dracula",
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
    name="dracula",
    style="outlined",
    weight="regular",
    size_scale=1.0,
    stroke_width="1.5",
    corner_rounding="2px",
)

ANIMATION = AnimationStyle(
    name="dracula",
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
    name="dracula",
    button_hover="lift",
    link_hover="color",
    card_hover="shadow",
    focus_style="ring",
    focus_ring_width="2px",
)

DESIGN_SYSTEM = DesignSystem(
    name="dracula",
    display_name="Dracula",
    description="Pixel-perfect Dracula -- elegant dev classic",
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
    name="dracula",
    display_name="Dracula",
    description="Pixel-perfect Dracula -- elegant dev classic",
    category="elegant",
    design_theme="dracula",
    color_preset="dracula",
    icon_style=ICON,
    animation_style=ANIMATION,
    pattern_style=PATTERN_MINIMAL,
    interaction_style=INTERACTION,
    illustration_style=ILLUST_LINE,
)
