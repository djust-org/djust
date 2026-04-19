"""Catppuccin Mocha -- Soothing pastel theme with warm, muted colors."""

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
    foreground=ColorScale(240, 23, 15),
    card=ColorScale(220, 23, 97),
    card_foreground=ColorScale(240, 23, 15),
    popover=ColorScale(220, 23, 97),
    popover_foreground=ColorScale(240, 23, 15),
    primary=ColorScale(217, 92, 76),
    primary_foreground=ColorScale(0, 0, 100),
    secondary=ColorScale(220, 17, 93),
    secondary_foreground=ColorScale(240, 23, 15),
    muted=ColorScale(220, 17, 93),
    muted_foreground=ColorScale(233, 16, 49),
    accent=ColorScale(220, 17, 93),
    accent_foreground=ColorScale(217, 92, 76),
    destructive=ColorScale(343, 81, 75),
    destructive_foreground=ColorScale(0, 0, 100),
    success=ColorScale(115, 54, 76),
    success_foreground=ColorScale(0, 0, 10),
    warning=ColorScale(35, 77, 49),
    warning_foreground=ColorScale(0, 0, 10),
    info=ColorScale(189, 100, 74),
    info_foreground=ColorScale(0, 0, 10),
    link=ColorScale(267, 84, 81),
    link_hover=ColorScale(267, 84, 71),
    code=ColorScale(220, 23, 97),
    code_foreground=ColorScale(240, 23, 15),
    selection=ColorScale(217, 92, 86),
    selection_foreground=ColorScale(240, 23, 15),
    brand=ColorScale(10, 56, 91),
    brand_foreground=ColorScale(0, 0, 10),
    border=ColorScale(220, 17, 93),
    input=ColorScale(0, 0, 100),
    ring=ColorScale(217, 92, 76),
    surface_1=ColorScale(232, 18, 98),
    surface_2=ColorScale(232, 14, 96),
    surface_3=ColorScale(232, 10, 93),
)

DARK = ThemeTokens(
    background=ColorScale(240, 21, 15),
    foreground=ColorScale(226, 64, 88),
    card=ColorScale(240, 21, 19),
    card_foreground=ColorScale(226, 64, 88),
    popover=ColorScale(240, 21, 19),
    popover_foreground=ColorScale(226, 64, 88),
    primary=ColorScale(217, 92, 76),
    primary_foreground=ColorScale(0, 0, 100),
    secondary=ColorScale(240, 21, 26),
    secondary_foreground=ColorScale(226, 64, 88),
    muted=ColorScale(240, 21, 26),
    muted_foreground=ColorScale(227, 27, 72),
    accent=ColorScale(240, 21, 26),
    accent_foreground=ColorScale(267, 84, 81),
    destructive=ColorScale(343, 81, 75),
    destructive_foreground=ColorScale(0, 0, 100),
    success=ColorScale(115, 54, 76),
    success_foreground=ColorScale(0, 0, 10),
    warning=ColorScale(23, 92, 75),
    warning_foreground=ColorScale(0, 0, 10),
    info=ColorScale(189, 100, 74),
    info_foreground=ColorScale(0, 0, 10),
    link=ColorScale(267, 84, 81),
    link_hover=ColorScale(267, 84, 71),
    code=ColorScale(240, 21, 12),
    code_foreground=ColorScale(226, 64, 88),
    selection=ColorScale(267, 84, 81),
    selection_foreground=ColorScale(0, 0, 100),
    brand=ColorScale(10, 56, 91),
    brand_foreground=ColorScale(0, 0, 10),
    border=ColorScale(240, 21, 26),
    input=ColorScale(240, 21, 19),
    ring=ColorScale(217, 92, 76),
    surface_1=ColorScale(240, 20, 7),
    surface_2=ColorScale(240, 16, 11),
    surface_3=ColorScale(240, 12, 15),
)

PRESET = ThemePreset(
    name="catppuccin",
    display_name="Catppuccin Mocha",
    description="Soothing pastel theme with warm, muted colors",
    light=LIGHT,
    dark=DARK,
    radius=0.75,
)


# =============================================================================
# Design System
# =============================================================================

TYPOGRAPHY = TypographyStyle(
    name="catppuccin",
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
    name="catppuccin",
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
    name="catppuccin",
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
    name="catppuccin",
    style="outlined",
    weight="regular",
    size_scale=1.0,
    stroke_width="1.5",
    corner_rounding="2px",
)

ANIMATION = AnimationStyle(
    name="catppuccin",
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
    name="catppuccin",
    button_hover="lift",
    link_hover="color",
    card_hover="shadow",
    focus_style="ring",
    focus_ring_width="2px",
)

DESIGN_SYSTEM = DesignSystem(
    name="catppuccin",
    display_name="Catppuccin",
    description="Soothing pastel -- elegant dev classic",
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
    name="catppuccin",
    display_name="Catppuccin",
    description="Soothing pastel -- elegant dev classic",
    category="elegant",
    design_theme="catppuccin",
    color_preset="catppuccin",
    icon_style=ICON,
    animation_style=ANIMATION,
    pattern_style=PATTERN_MINIMAL,
    interaction_style=INTERACTION,
    illustration_style=ILLUST_LINE,
)
