"""Cyberdeck -- Terminal hacker — matrix green on true black, CRT vibes."""

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
    background=ColorScale(0, 0, 97),
    foreground=ColorScale(120, 10, 15),
    card=ColorScale(120, 5, 94),
    card_foreground=ColorScale(120, 10, 15),
    popover=ColorScale(120, 5, 94),
    popover_foreground=ColorScale(120, 10, 15),
    primary=ColorScale(120, 100, 35),
    primary_foreground=ColorScale(0, 0, 100),
    secondary=ColorScale(120, 5, 90),
    secondary_foreground=ColorScale(120, 10, 15),
    muted=ColorScale(120, 5, 90),
    muted_foreground=ColorScale(120, 5, 45),
    accent=ColorScale(120, 100, 90),
    accent_foreground=ColorScale(120, 10, 15),
    destructive=ColorScale(0, 80, 55),
    destructive_foreground=ColorScale(0, 0, 100),
    success=ColorScale(120, 100, 35),
    success_foreground=ColorScale(0, 0, 100),
    warning=ColorScale(60, 80, 45),
    warning_foreground=ColorScale(0, 0, 10),
    info=ColorScale(180, 80, 40),
    info_foreground=ColorScale(0, 0, 100),
    link=ColorScale(120, 100, 30),
    link_hover=ColorScale(120, 100, 22),
    code=ColorScale(120, 5, 92),
    code_foreground=ColorScale(120, 100, 30),
    selection=ColorScale(120, 80, 85),
    selection_foreground=ColorScale(120, 10, 15),
    brand=ColorScale(120, 100, 35),
    brand_foreground=ColorScale(0, 0, 100),
    border=ColorScale(120, 10, 80),
    input=ColorScale(120, 10, 80),
    ring=ColorScale(120, 100, 35),
    surface_1=ColorScale(120, 5, 96),
    surface_2=ColorScale(120, 5, 93),
    surface_3=ColorScale(120, 5, 90),
)

DARK = ThemeTokens(
    background=ColorScale(0, 0, 2),
    foreground=ColorScale(120, 100, 50),
    card=ColorScale(120, 10, 5),
    card_foreground=ColorScale(120, 100, 50),
    popover=ColorScale(120, 10, 5),
    popover_foreground=ColorScale(120, 100, 50),
    primary=ColorScale(120, 100, 50),
    primary_foreground=ColorScale(0, 0, 2),
    secondary=ColorScale(120, 10, 8),
    secondary_foreground=ColorScale(120, 100, 50),
    muted=ColorScale(120, 10, 8),
    muted_foreground=ColorScale(120, 50, 35),
    accent=ColorScale(120, 100, 12),
    accent_foreground=ColorScale(120, 100, 50),
    destructive=ColorScale(0, 100, 50),
    destructive_foreground=ColorScale(0, 0, 100),
    success=ColorScale(120, 100, 50),
    success_foreground=ColorScale(0, 0, 2),
    warning=ColorScale(60, 100, 50),
    warning_foreground=ColorScale(0, 0, 2),
    info=ColorScale(180, 100, 45),
    info_foreground=ColorScale(0, 0, 2),
    link=ColorScale(120, 100, 50),
    link_hover=ColorScale(120, 100, 65),
    code=ColorScale(120, 10, 7),
    code_foreground=ColorScale(120, 100, 55),
    selection=ColorScale(120, 80, 25),
    selection_foreground=ColorScale(120, 10, 90),
    brand=ColorScale(120, 100, 50),
    brand_foreground=ColorScale(0, 0, 2),
    border=ColorScale(120, 100, 18),
    input=ColorScale(120, 100, 18),
    ring=ColorScale(120, 100, 50),
    surface_1=ColorScale(120, 5, 6),
    surface_2=ColorScale(120, 5, 10),
    surface_3=ColorScale(120, 5, 14),
)

PRESET = ThemePreset(
    name="cyberdeck",
    display_name="Cyberdeck",
    description="Terminal hacker — matrix green on true black, CRT vibes",
    light=LIGHT,
    dark=DARK,
    default_mode="dark",
)


# =============================================================================
# Design System
# =============================================================================

TYPOGRAPHY = TypographyStyle(
    name="cyberdeck",
    heading_font='"JetBrains Mono", monospace',
    body_font='"JetBrains Mono", monospace',
    base_size="14px",
    heading_scale=1.15,
    line_height="1.5",
    body_line_height="1.6",
    heading_weight="700",
    section_heading_weight="700",
    body_weight="400",
    letter_spacing="normal",
    prose_max_width="72rem",
    badge_radius="0px",
)

LAYOUT = LayoutStyle(
    name="cyberdeck",
    space_unit="1rem",
    space_scale=1.5,
    border_radius_sm="0px",
    border_radius_md="0px",
    border_radius_lg="0px",
    button_shape="sharp",
    card_shape="sharp",
    input_shape="sharp",
    container_width="1400px",
    grid_gap="1rem",
    section_spacing="2rem",
    hero_padding_top="4rem",
    hero_padding_bottom="2rem",
    hero_line_height="1.2",
    hero_max_width="72rem",
)

SURFACE = SurfaceStyle(
    name="cyberdeck",
    shadow_sm="none",
    shadow_md="none",
    shadow_lg="none",
    border_width="1px",
    border_style="solid",
    surface_treatment="noise",
    backdrop_blur="0px",
    noise_opacity=0.04,
)

ICON = IconStyle(
    name="cyberdeck",
    style="sharp",
    weight="bold",
    size_scale=0.9,
    stroke_width="2",
    corner_rounding="0px",
)

ANIMATION = AnimationStyle(
    name="cyberdeck",
    entrance_effect="none",
    exit_effect="none",
    hover_effect="none",
    hover_scale=1.0,
    hover_translate_y="0px",
    click_effect="none",
    loading_style="spinner",
    transition_style="instant",
    duration_fast="0s",
    duration_normal="0s",
    duration_slow="0.05s",
    easing="linear",
)

INTERACTION = InteractionStyle(
    name="cyberdeck",
    button_hover="darken",
    link_hover="underline",
    card_hover="border",
    focus_style="outline",
    focus_ring_width="2px",
)

DESIGN_SYSTEM = DesignSystem(
    name="cyberdeck",
    display_name="Cyberdeck",
    description="Terminal hacker -- matrix green, CRT vibes, monospace everything",
    category="bold",
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
    name="cyberdeck",
    display_name="Cyberdeck",
    description="Terminal hacker -- matrix green, CRT vibes, monospace everything",
    category="bold",
    design_theme="cyberdeck",
    color_preset="cyberdeck",
    icon_style=ICON,
    animation_style=ANIMATION,
    pattern_style=PATTERN_NOISE,
    interaction_style=INTERACTION,
    illustration_style=ILLUST_FLAT,
)
