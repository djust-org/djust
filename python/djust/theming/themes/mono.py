"""Mono -- Pure grayscale — zero chroma, maximum discipline."""

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
    foreground=ColorScale(0, 0, 10),
    card=ColorScale(0, 0, 98),
    card_foreground=ColorScale(0, 0, 10),
    popover=ColorScale(0, 0, 98),
    popover_foreground=ColorScale(0, 0, 10),
    primary=ColorScale(0, 0, 15),
    primary_foreground=ColorScale(0, 0, 100),
    secondary=ColorScale(0, 0, 93),
    secondary_foreground=ColorScale(0, 0, 15),
    muted=ColorScale(0, 0, 93),
    muted_foreground=ColorScale(0, 0, 45),
    accent=ColorScale(0, 0, 90),
    accent_foreground=ColorScale(0, 0, 10),
    destructive=ColorScale(0, 0, 30),
    destructive_foreground=ColorScale(0, 0, 100),
    success=ColorScale(0, 0, 35),
    success_foreground=ColorScale(0, 0, 100),
    warning=ColorScale(0, 0, 40),
    warning_foreground=ColorScale(0, 0, 100),
    info=ColorScale(0, 0, 35),
    info_foreground=ColorScale(0, 0, 100),
    link=ColorScale(0, 0, 20),
    link_hover=ColorScale(0, 0, 10),
    code=ColorScale(0, 0, 94),
    code_foreground=ColorScale(0, 0, 25),
    selection=ColorScale(0, 0, 85),
    selection_foreground=ColorScale(0, 10, 15),
    brand=ColorScale(0, 0, 15),
    brand_foreground=ColorScale(0, 0, 100),
    border=ColorScale(0, 0, 82),
    input=ColorScale(0, 0, 82),
    ring=ColorScale(0, 0, 15),
    surface_1=ColorScale(0, 5, 96),
    surface_2=ColorScale(0, 5, 93),
    surface_3=ColorScale(0, 5, 90),
)

DARK = ThemeTokens(
    background=ColorScale(0, 0, 7),
    foreground=ColorScale(0, 0, 90),
    card=ColorScale(0, 0, 10),
    card_foreground=ColorScale(0, 0, 90),
    popover=ColorScale(0, 0, 10),
    popover_foreground=ColorScale(0, 0, 90),
    primary=ColorScale(0, 0, 85),
    primary_foreground=ColorScale(0, 0, 7),
    secondary=ColorScale(0, 0, 15),
    secondary_foreground=ColorScale(0, 0, 85),
    muted=ColorScale(0, 0, 15),
    muted_foreground=ColorScale(0, 0, 55),
    accent=ColorScale(0, 0, 18),
    accent_foreground=ColorScale(0, 0, 90),
    destructive=ColorScale(0, 0, 70),
    destructive_foreground=ColorScale(0, 0, 7),
    success=ColorScale(0, 0, 65),
    success_foreground=ColorScale(0, 0, 7),
    warning=ColorScale(0, 0, 60),
    warning_foreground=ColorScale(0, 0, 7),
    info=ColorScale(0, 0, 65),
    info_foreground=ColorScale(0, 0, 7),
    link=ColorScale(0, 0, 80),
    link_hover=ColorScale(0, 0, 95),
    code=ColorScale(0, 0, 13),
    code_foreground=ColorScale(0, 0, 70),
    selection=ColorScale(0, 0, 25),
    selection_foreground=ColorScale(0, 10, 90),
    brand=ColorScale(0, 0, 85),
    brand_foreground=ColorScale(0, 0, 7),
    border=ColorScale(0, 0, 20),
    input=ColorScale(0, 0, 20),
    ring=ColorScale(0, 0, 85),
    surface_1=ColorScale(0, 5, 6),
    surface_2=ColorScale(0, 5, 10),
    surface_3=ColorScale(0, 5, 14),
)

PRESET = ThemePreset(
    name="mono",
    display_name="Mono",
    description="Pure grayscale — zero chroma, maximum discipline",
    light=LIGHT,
    dark=DARK,
)


# =============================================================================
# Design System
# =============================================================================

TYPOGRAPHY = TypographyStyle(
    name="mono",
    heading_font='"Inter", system-ui, sans-serif',
    body_font='"Inter", system-ui, sans-serif',
    base_size="16px",
    heading_scale=1.2,
    line_height="1.5",
    body_line_height="1.6",
    heading_weight="300",
    section_heading_weight="300",
    body_weight="400",
    letter_spacing="0.04em",
    prose_max_width="42rem",
    badge_radius="0px",
)

LAYOUT = LayoutStyle(
    name="mono",
    space_unit="1rem",
    space_scale=1.5,
    border_radius_sm="0px",
    border_radius_md="0px",
    border_radius_lg="0px",
    button_shape="sharp",
    card_shape="sharp",
    input_shape="sharp",
    container_width="1100px",
    grid_gap="1.5rem",
    section_spacing="4rem",
    hero_padding_top="7rem",
    hero_padding_bottom="4rem",
    hero_line_height="1.1",
    hero_max_width="48rem",
)

SURFACE = SurfaceStyle(
    name="mono",
    shadow_sm="none",
    shadow_md="none",
    shadow_lg="none",
    border_width="1px",
    border_style="solid",
    surface_treatment="flat",
    backdrop_blur="0px",
    noise_opacity=0.0,
)

ICON = IconStyle(
    name="mono",
    style="outlined",
    weight="thin",
    size_scale=1.0,
    stroke_width="1",
    corner_rounding="0px",
)

ANIMATION = AnimationStyle(
    name="mono",
    entrance_effect="fade",
    exit_effect="fade",
    hover_effect="none",
    hover_scale=1.0,
    hover_translate_y="0px",
    click_effect="none",
    loading_style="spinner",
    transition_style="snappy",
    duration_fast="0.05s",
    duration_normal="0.1s",
    duration_slow="0.2s",
    easing="linear",
)

INTERACTION = InteractionStyle(
    name="mono",
    button_hover="darken",
    link_hover="underline",
    card_hover="border",
    focus_style="outline",
    focus_ring_width="1px",
)

DESIGN_SYSTEM = DesignSystem(
    name="mono",
    display_name="Mono",
    description="Pure grayscale -- zero chroma, wide tracking, typographic discipline",
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
    name="mono",
    display_name="Mono",
    description="Pure grayscale -- zero chroma, wide tracking, typographic discipline",
    category="minimal",
    design_theme="mono",
    color_preset="mono",
    icon_style=ICON,
    animation_style=ANIMATION,
    pattern_style=PATTERN_MINIMAL,
    interaction_style=INTERACTION,
    illustration_style=ILLUST_LINE,
)
