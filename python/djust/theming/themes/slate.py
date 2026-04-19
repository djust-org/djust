"""Slate -- Minimal monochrome — pure focus, no color noise."""

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
    foreground=ColorScale(215, 25, 10),
    card=ColorScale(0, 0, 98),
    card_foreground=ColorScale(215, 25, 10),
    popover=ColorScale(0, 0, 98),
    popover_foreground=ColorScale(215, 25, 10),
    primary=ColorScale(214, 16, 46),
    primary_foreground=ColorScale(0, 0, 100),
    secondary=ColorScale(0, 0, 94),
    secondary_foreground=ColorScale(215, 25, 15),
    muted=ColorScale(0, 0, 94),
    muted_foreground=ColorScale(215, 15, 45),
    accent=ColorScale(0, 0, 94),
    accent_foreground=ColorScale(215, 25, 15),
    destructive=ColorScale(0, 84, 60),
    destructive_foreground=ColorScale(0, 0, 100),
    success=ColorScale(142, 71, 45),
    success_foreground=ColorScale(0, 0, 100),
    warning=ColorScale(38, 92, 50),
    warning_foreground=ColorScale(0, 0, 10),
    info=ColorScale(214, 16, 46),
    info_foreground=ColorScale(0, 0, 100),
    link=ColorScale(214, 16, 40),
    link_hover=ColorScale(214, 16, 30),
    code=ColorScale(0, 0, 96),
    code_foreground=ColorScale(215, 25, 10),
    selection=ColorScale(214, 16, 46),
    selection_foreground=ColorScale(0, 0, 100),
    brand=ColorScale(214, 16, 46),
    brand_foreground=ColorScale(0, 0, 100),
    border=ColorScale(0, 0, 88),
    input=ColorScale(0, 0, 100),
    ring=ColorScale(214, 16, 46),
    surface_1=ColorScale(215, 15, 98),
    surface_2=ColorScale(215, 12, 96),
    surface_3=ColorScale(215, 10, 93),
)

DARK = ThemeTokens(
    background=ColorScale(240, 20, 5),
    foreground=ColorScale(0, 0, 92),
    card=ColorScale(240, 18, 8),
    card_foreground=ColorScale(0, 0, 92),
    popover=ColorScale(240, 18, 8),
    popover_foreground=ColorScale(0, 0, 92),
    primary=ColorScale(214, 16, 66),
    primary_foreground=ColorScale(0, 0, 10),
    secondary=ColorScale(240, 15, 12),
    secondary_foreground=ColorScale(0, 0, 92),
    muted=ColorScale(240, 15, 12),
    muted_foreground=ColorScale(0, 0, 55),
    accent=ColorScale(240, 15, 12),
    accent_foreground=ColorScale(214, 16, 80),
    destructive=ColorScale(0, 84, 60),
    destructive_foreground=ColorScale(0, 0, 100),
    success=ColorScale(142, 71, 45),
    success_foreground=ColorScale(0, 0, 100),
    warning=ColorScale(38, 92, 50),
    warning_foreground=ColorScale(0, 0, 10),
    info=ColorScale(214, 16, 66),
    info_foreground=ColorScale(0, 0, 10),
    link=ColorScale(214, 16, 66),
    link_hover=ColorScale(214, 16, 76),
    code=ColorScale(240, 18, 6),
    code_foreground=ColorScale(0, 0, 92),
    selection=ColorScale(214, 16, 66),
    selection_foreground=ColorScale(0, 0, 10),
    brand=ColorScale(214, 16, 66),
    brand_foreground=ColorScale(0, 0, 10),
    border=ColorScale(240, 15, 12),
    input=ColorScale(240, 18, 8),
    ring=ColorScale(214, 16, 66),
    surface_1=ColorScale(240, 18, 3),
    surface_2=ColorScale(240, 15, 5),
    surface_3=ColorScale(240, 12, 8),
)

PRESET = ThemePreset(
    name="slate",
    display_name="Slate",
    description="Minimal monochrome — pure focus, no color noise",
    light=LIGHT,
    dark=DARK,
    radius=0.75,
)


# =============================================================================
# Design System
# =============================================================================

TYPOGRAPHY = TypographyStyle(
    name="slate",
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
    prose_max_width="42rem",
    badge_radius="9999px",
)

LAYOUT = LayoutStyle(
    name="slate",
    space_unit="1rem",
    space_scale=1.5,
    border_radius_sm="2px",
    border_radius_md="4px",
    border_radius_lg="6px",
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
    name="slate",
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
    name="slate",
    style="outlined",
    weight="regular",
    size_scale=1.0,
    stroke_width="1.5",
    corner_rounding="2px",
)

ANIMATION = AnimationStyle(
    name="slate",
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
    name="slate",
    button_hover="lift",
    link_hover="color",
    card_hover="shadow",
    focus_style="ring",
    focus_ring_width="2px",
)

DESIGN_SYSTEM = DesignSystem(
    name="slate",
    display_name="Slate",
    description="Professional neutral -- no-nonsense, clean, focused",
    category="professional",
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
    name="slate",
    display_name="Slate",
    description="Professional neutral -- no-nonsense, clean, focused",
    category="professional",
    design_theme="slate",
    color_preset="slate",
    icon_style=ICON,
    animation_style=ANIMATION,
    pattern_style=PATTERN_MINIMAL,
    interaction_style=INTERACTION,
    illustration_style=ILLUST_LINE,
)
