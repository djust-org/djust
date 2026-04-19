"""Retro Computing — Amber on dark, chunky font, pixel aesthetic, hard shadows."""

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
    PATTERN_NOISE,
    ILLUST_RETRO,
)


# =============================================================================
# Color Preset
# =============================================================================
# Amber phosphor CRT aesthetic — warm beige light mode, amber-on-dark dark mode.
# Think: IBM 5151, Apple II, amber monochrome monitors.

LIGHT = ThemeTokens(
    background=ColorScale(40, 40, 94),  # Warm beige
    foreground=ColorScale(30, 30, 12),  # Dark brown
    card=ColorScale(40, 35, 96),  # Light warm card
    card_foreground=ColorScale(30, 30, 12),
    popover=ColorScale(40, 35, 96),
    popover_foreground=ColorScale(30, 30, 12),
    primary=ColorScale(38, 100, 50),  # Amber
    primary_foreground=ColorScale(30, 30, 8),
    secondary=ColorScale(40, 30, 90),  # Warm beige panel
    secondary_foreground=ColorScale(30, 30, 12),
    muted=ColorScale(40, 20, 88),  # Warm muted
    muted_foreground=ColorScale(30, 20, 40),  # Brown muted text
    accent=ColorScale(40, 30, 90),  # Warm hover surface
    accent_foreground=ColorScale(30, 30, 12),
    destructive=ColorScale(0, 70, 45),  # CRT red
    destructive_foreground=ColorScale(0, 0, 100),
    success=ColorScale(120, 60, 35),  # Terminal green
    success_foreground=ColorScale(0, 0, 100),
    warning=ColorScale(38, 100, 50),  # Amber (same as primary)
    warning_foreground=ColorScale(30, 30, 8),
    info=ColorScale(200, 50, 45),  # Steel blue
    info_foreground=ColorScale(0, 0, 100),
    link=ColorScale(38, 100, 42),  # Darker amber links
    link_hover=ColorScale(38, 100, 35),  # Even darker
    code=ColorScale(30, 30, 6),  # Very dark brown code bg
    code_foreground=ColorScale(38, 100, 60),  # Amber code text
    selection=ColorScale(38, 80, 85),  # Pale amber selection
    selection_foreground=ColorScale(30, 30, 12),
    brand=ColorScale(38, 100, 50),  # Amber
    brand_foreground=ColorScale(30, 30, 8),
    border=ColorScale(30, 25, 70),  # Brown border
    input=ColorScale(40, 25, 88),  # Warm input
    ring=ColorScale(38, 100, 50),  # Amber focus ring
    surface_1=ColorScale(40, 40, 94),  # Warm beige
    surface_2=ColorScale(40, 35, 91),  # Slightly deeper
    surface_3=ColorScale(40, 30, 88),  # Deeper still
)

DARK = ThemeTokens(
    background=ColorScale(30, 30, 8),  # Very dark brown
    foreground=ColorScale(38, 100, 60),  # Amber text
    card=ColorScale(30, 25, 11),  # Dark brown card
    card_foreground=ColorScale(38, 100, 60),
    popover=ColorScale(30, 25, 11),
    popover_foreground=ColorScale(38, 100, 60),
    primary=ColorScale(38, 100, 50),  # Amber
    primary_foreground=ColorScale(30, 30, 5),
    secondary=ColorScale(30, 25, 12),  # Dark panel
    secondary_foreground=ColorScale(38, 100, 60),
    muted=ColorScale(30, 20, 16),  # Dark muted
    muted_foreground=ColorScale(38, 60, 40),  # Dim amber text
    accent=ColorScale(30, 25, 12),  # Dark hover
    accent_foreground=ColorScale(38, 100, 60),
    destructive=ColorScale(0, 70, 50),  # CRT red
    destructive_foreground=ColorScale(0, 0, 100),
    success=ColorScale(120, 60, 40),  # Terminal green
    success_foreground=ColorScale(0, 0, 100),
    warning=ColorScale(38, 100, 55),  # Amber brightened
    warning_foreground=ColorScale(30, 30, 5),
    info=ColorScale(200, 50, 55),  # Blue brightened
    info_foreground=ColorScale(0, 0, 100),
    link=ColorScale(38, 100, 65),  # Bright amber links
    link_hover=ColorScale(38, 100, 75),  # Brighter amber
    code=ColorScale(30, 30, 6),  # Deepest brown code bg
    code_foreground=ColorScale(38, 100, 60),  # Amber code text
    selection=ColorScale(38, 80, 20),  # Deep amber selection
    selection_foreground=ColorScale(38, 100, 65),
    brand=ColorScale(38, 100, 55),  # Amber brightened
    brand_foreground=ColorScale(30, 30, 5),
    border=ColorScale(38, 40, 22),  # Amber-tinted border
    input=ColorScale(30, 20, 15),  # Dark input
    ring=ColorScale(38, 100, 50),  # Amber focus ring
    surface_1=ColorScale(30, 30, 6),  # Deepest
    surface_2=ColorScale(30, 28, 9),  # Mid dark
    surface_3=ColorScale(30, 25, 12),  # Slightly elevated
)

PRESET = ThemePreset(
    name="retro_computing",
    display_name="Retro Computing",
    description="Amber on dark, chunky monospace, pixel aesthetic, hard shadows",
    light=LIGHT,
    dark=DARK,
    radius=0.0,  # 0px — hard pixel edges
)


# =============================================================================
# Design System
# =============================================================================

TYPOGRAPHY = TypographyStyle(
    name="retro_computing",
    heading_font='"Space Mono", "Courier New", monospace',
    body_font='"Space Mono", "Courier New", monospace',
    base_size="15px",
    heading_scale=1.15,  # Flat — CRT-era
    line_height="1.5",
    body_line_height="1.5",
    heading_weight="700",
    section_heading_weight="700",
    body_weight="400",
    letter_spacing="0em",
    prose_max_width="38rem",
    badge_radius="0px",  # Sharp badges
)

LAYOUT = LayoutStyle(
    name="retro_computing",
    space_unit="1rem",
    space_scale=1.5,
    border_radius_sm="0px",
    border_radius_md="0px",
    border_radius_lg="0px",
    button_shape="sharp",
    card_shape="sharp",
    input_shape="sharp",
    container_width="1024px",  # Period-accurate
    grid_gap="16px",  # Fixed pixel grid
    section_spacing="3rem",
    hero_padding_top="4rem",
    hero_padding_bottom="3rem",
    hero_line_height="1.2",
    hero_max_width="44rem",
)

SURFACE = SurfaceStyle(
    name="retro_computing",
    # Hard offset shadows — pixel art style
    shadow_sm="2px 2px 0 rgba(0, 0, 0, 0.4)",
    shadow_md="4px 4px 0 rgba(0, 0, 0, 0.4)",
    shadow_lg="6px 6px 0 rgba(0, 0, 0, 0.5)",
    border_width="2px",  # Chunky borders
    border_style="solid",
    surface_treatment="flat",
    backdrop_blur="0px",
    noise_opacity=0.05,  # Subtle retro dither — visible but not overwhelming
)

ICON = IconStyle(
    name="retro_computing",
    style="outlined",
    weight="bold",
    size_scale=1.0,
    stroke_width="2",  # Chunky strokes
    corner_rounding="0px",
)

ANIMATION = AnimationStyle(
    name="retro_computing",
    entrance_effect="none",  # Instant — CRT snap
    exit_effect="none",
    hover_effect="none",
    hover_scale=1.0,
    hover_translate_y="0px",
    click_effect="none",
    loading_style="spinner",
    transition_style="instant",
    duration_fast="0s",
    duration_normal="0s",  # Instant
    duration_slow="0.05s",
    easing="linear",
)

INTERACTION = InteractionStyle(
    name="retro_computing",
    button_hover="color",  # Instant color swap
    link_hover="color",
    card_hover="none",
    focus_style="outline",  # Outline focus (retro)
    focus_ring_width="2px",
)

DESIGN_SYSTEM = DesignSystem(
    name="retro_computing",
    display_name="Retro Computing",
    description="Amber CRT aesthetic — monospace type, hard shadows, pixel grid",
    category="retro",
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
    name="retro_computing",
    display_name="Retro Computing",
    description="Amber on dark, chunky monospace, pixel aesthetic, hard shadows",
    category="retro",
    design_theme="retro_computing",
    color_preset="retro_computing",
    icon_style=ICON,
    animation_style=ANIMATION,
    pattern_style=PATTERN_NOISE,
    interaction_style=INTERACTION,
    illustration_style=ILLUST_RETRO,
)
