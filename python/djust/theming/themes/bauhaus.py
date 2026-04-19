"""Bauhaus -- Itten's primary triad on warm paper — geometric, functional, zero decoration."""

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
    PATTERN_GRID,
)

# --- Color Preset ---

LIGHT = ThemeTokens(
    background=ColorScale(37, 39, 94),
    foreground=ColorScale(0, 0, 8),
    card=ColorScale(0, 0, 100),
    card_foreground=ColorScale(0, 0, 8),
    popover=ColorScale(0, 0, 100),
    popover_foreground=ColorScale(0, 0, 8),
    primary=ColorScale(2, 65, 47),
    primary_foreground=ColorScale(0, 0, 100),
    secondary=ColorScale(40, 20, 90),
    secondary_foreground=ColorScale(0, 0, 8),
    muted=ColorScale(40, 15, 88),
    muted_foreground=ColorScale(0, 0, 35),
    accent=ColorScale(40, 20, 90),
    accent_foreground=ColorScale(0, 0, 8),
    destructive=ColorScale(2, 65, 47),
    destructive_foreground=ColorScale(0, 0, 100),
    success=ColorScale(120, 45, 35),
    success_foreground=ColorScale(0, 0, 100),
    warning=ColorScale(48, 82, 50),
    warning_foreground=ColorScale(0, 0, 8),
    info=ColorScale(223, 60, 29),
    info_foreground=ColorScale(0, 0, 100),
    link=ColorScale(223, 60, 40),
    link_hover=ColorScale(2, 65, 47),
    code=ColorScale(0, 0, 12),
    code_foreground=ColorScale(48, 82, 60),
    selection=ColorScale(48, 82, 80),
    selection_foreground=ColorScale(0, 0, 8),
    brand=ColorScale(223, 60, 29),
    brand_foreground=ColorScale(0, 0, 100),
    border=ColorScale(0, 0, 8),
    input=ColorScale(40, 15, 93),
    ring=ColorScale(2, 65, 47),
    surface_1=ColorScale(37, 39, 94),
    surface_2=ColorScale(40, 20, 90),
    surface_3=ColorScale(40, 15, 86),
)

DARK = ThemeTokens(
    background=ColorScale(0, 0, 5),
    foreground=ColorScale(37, 39, 94),
    card=ColorScale(0, 0, 10),
    card_foreground=ColorScale(37, 39, 94),
    popover=ColorScale(0, 0, 10),
    popover_foreground=ColorScale(37, 39, 94),
    primary=ColorScale(2, 65, 47),
    primary_foreground=ColorScale(0, 0, 100),
    secondary=ColorScale(0, 0, 15),
    secondary_foreground=ColorScale(37, 39, 94),
    muted=ColorScale(0, 0, 15),
    muted_foreground=ColorScale(0, 0, 55),
    accent=ColorScale(0, 0, 18),
    accent_foreground=ColorScale(37, 39, 94),
    destructive=ColorScale(2, 65, 52),
    destructive_foreground=ColorScale(0, 0, 100),
    success=ColorScale(120, 45, 45),
    success_foreground=ColorScale(0, 0, 5),
    warning=ColorScale(48, 82, 50),
    warning_foreground=ColorScale(0, 0, 5),
    info=ColorScale(223, 60, 50),
    info_foreground=ColorScale(0, 0, 100),
    link=ColorScale(223, 60, 62),
    link_hover=ColorScale(2, 65, 60),
    code=ColorScale(0, 0, 8),
    code_foreground=ColorScale(48, 82, 60),
    selection=ColorScale(223, 60, 25),
    selection_foreground=ColorScale(37, 39, 94),
    brand=ColorScale(223, 60, 50),
    brand_foreground=ColorScale(0, 0, 100),
    border=ColorScale(37, 39, 94),
    input=ColorScale(0, 0, 50),
    ring=ColorScale(2, 65, 47),
    surface_1=ColorScale(0, 0, 3),
    surface_2=ColorScale(0, 0, 7),
    surface_3=ColorScale(0, 0, 12),
)

PRESET = ThemePreset(
    name="bauhaus",
    display_name="Bauhaus",
    description="Itten's primary triad on warm paper — geometric, functional, zero decoration",
    light=LIGHT,
    dark=DARK,
    radius=0,
)

# --- Design System ---

TYPOGRAPHY = TypographyStyle(
    name="bauhaus",
    heading_font='"DM Sans", "Inter", system-ui',
    body_font='"DM Sans", "Inter", system-ui',
    heading_scale=1.4,
    line_height="1.4",
    heading_weight="900",
    section_heading_weight="800",
    letter_spacing="0.025em",
    prose_max_width="48rem",
    badge_radius="0px",
)

LAYOUT = LayoutStyle(
    name="bauhaus",
    space_scale=1.618,
    border_radius_sm="0px",
    border_radius_md="0px",
    border_radius_lg="0px",
    button_shape="sharp",
    card_shape="sharp",
    input_shape="sharp",
    section_spacing="4rem",
    hero_line_height="1.0",
    hero_max_width="56rem",
)

SURFACE = SurfaceStyle(
    name="bauhaus",
    shadow_sm="3px 3px 0px rgba(0,0,0,0.9)",
    shadow_md="6px 6px 0px rgba(0,0,0,0.9)",
    shadow_lg="10px 10px 0px rgba(0,0,0,0.9)",
    border_width="3px",
)

ICON = IconStyle(
    name="bauhaus",
    style="filled",
    weight="bold",
    size_scale=1.1,
    stroke_width="3",
    corner_rounding="0px",
)

ANIMATION = AnimationStyle(
    name="bauhaus",
    entrance_effect="none",
    exit_effect="none",
    hover_effect="none",
    hover_scale=1.0,
    hover_translate_y="0px",
    click_effect="none",
    transition_style="instant",
    duration_fast="0.05s",
    duration_normal="0.08s",
    duration_slow="0.12s",
    easing="linear",
)

INTERACTION = InteractionStyle(
    name="bauhaus",
    button_hover="darken",
    card_hover="border",
    focus_style="outline",
    focus_ring_width="3px",
)

DESIGN_SYSTEM = DesignSystem(
    name="bauhaus",
    display_name="Bauhaus",
    description="Geometric modernist — Itten's triad, Bayer's type, zero decoration",
    category="bold",
    typography=TYPOGRAPHY,
    layout=LAYOUT,
    surface=SURFACE,
    icons=ICON,
    animation=ANIMATION,
    interaction=INTERACTION,
)

# --- Theme Pack ---

PACK = ThemePack(
    name="bauhaus",
    display_name="Bauhaus",
    description="Itten's primary triad, Bayer's geometric type, zero decoration",
    category="bold",
    design_theme="bauhaus",
    color_preset="bauhaus",
    icon_style=ICON,
    animation_style=ANIMATION,
    pattern_style=PATTERN_GRID,
    interaction_style=INTERACTION,
    illustration_style=ILLUST_FLAT,
)
