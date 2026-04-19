"""Terminal — Green phosphor on black, monospace everything, CRT hacker aesthetic."""

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
    ILLUST_FLAT,
)


# =============================================================================
# Color Preset
# =============================================================================
# Terminal palette: pure black, phosphor green, amber warnings,
# zero decoration, monospace everything.

LIGHT = ThemeTokens(
    # Light "terminal" = white terminal (like a paper printout)
    background=ColorScale(0, 0, 100),
    foreground=ColorScale(0, 0, 5),
    card=ColorScale(0, 0, 98),
    card_foreground=ColorScale(0, 0, 5),
    popover=ColorScale(0, 0, 98),
    popover_foreground=ColorScale(0, 0, 5),
    primary=ColorScale(120, 80, 35),  # Terminal green
    primary_foreground=ColorScale(0, 0, 100),
    secondary=ColorScale(0, 0, 95),
    secondary_foreground=ColorScale(0, 0, 5),
    muted=ColorScale(0, 0, 93),
    muted_foreground=ColorScale(0, 0, 40),
    accent=ColorScale(0, 0, 96),
    accent_foreground=ColorScale(0, 0, 5),
    destructive=ColorScale(0, 80, 50),  # Red error
    destructive_foreground=ColorScale(0, 0, 100),
    success=ColorScale(120, 80, 35),  # Green = success = terminal
    success_foreground=ColorScale(0, 0, 100),
    warning=ColorScale(38, 100, 50),  # Amber warning
    warning_foreground=ColorScale(0, 0, 5),
    info=ColorScale(200, 60, 50),  # Cyan info
    info_foreground=ColorScale(0, 0, 100),
    link=ColorScale(120, 80, 30),  # Green links
    link_hover=ColorScale(120, 80, 20),
    code=ColorScale(0, 0, 5),  # Black code bg
    code_foreground=ColorScale(120, 80, 55),  # Green code text
    selection=ColorScale(120, 80, 85),
    selection_foreground=ColorScale(0, 0, 5),
    brand=ColorScale(120, 80, 35),  # Green IS the brand
    brand_foreground=ColorScale(0, 0, 100),
    border=ColorScale(0, 0, 75),  # Gray border
    input=ColorScale(0, 0, 96),
    ring=ColorScale(120, 80, 35),
    surface_1=ColorScale(0, 0, 100),
    surface_2=ColorScale(0, 0, 98),
    surface_3=ColorScale(0, 0, 95),
)

DARK = ThemeTokens(
    background=ColorScale(0, 0, 0),  # Pure black — CRT off-state
    foreground=ColorScale(120, 80, 55),  # #33CC33-ish — phosphor green
    card=ColorScale(0, 0, 4),  # Near-black card
    card_foreground=ColorScale(120, 80, 55),
    popover=ColorScale(0, 0, 4),
    popover_foreground=ColorScale(120, 80, 55),
    primary=ColorScale(120, 80, 45),  # Bright phosphor green
    primary_foreground=ColorScale(0, 0, 0),  # Black on green
    secondary=ColorScale(0, 0, 8),
    secondary_foreground=ColorScale(120, 80, 55),
    muted=ColorScale(0, 0, 8),
    muted_foreground=ColorScale(120, 40, 35),  # Dim green
    accent=ColorScale(0, 0, 6),  # Very dark hover
    accent_foreground=ColorScale(120, 80, 55),
    destructive=ColorScale(0, 80, 50),  # Red error
    destructive_foreground=ColorScale(0, 0, 0),
    success=ColorScale(120, 80, 45),  # Bright green
    success_foreground=ColorScale(0, 0, 0),
    warning=ColorScale(38, 100, 50),  # Amber
    warning_foreground=ColorScale(0, 0, 0),
    info=ColorScale(200, 60, 55),  # Cyan
    info_foreground=ColorScale(0, 0, 0),
    link=ColorScale(120, 80, 55),  # Green links
    link_hover=ColorScale(120, 80, 70),  # Brighter on hover
    code=ColorScale(0, 0, 0),  # Pure black code
    code_foreground=ColorScale(120, 80, 55),  # Green
    selection=ColorScale(120, 80, 20),  # Dark green selection
    selection_foreground=ColorScale(120, 80, 70),
    brand=ColorScale(120, 80, 45),  # Phosphor green
    brand_foreground=ColorScale(0, 0, 0),
    border=ColorScale(120, 40, 20),  # Dark green-tinted border
    input=ColorScale(0, 0, 6),  # Near-black input
    ring=ColorScale(120, 80, 45),  # Green ring
    surface_1=ColorScale(0, 0, 0),  # Pure black
    surface_2=ColorScale(0, 0, 3),
    surface_3=ColorScale(0, 0, 6),
)

PRESET = ThemePreset(
    name="terminal",
    display_name="Terminal",
    description="Green phosphor on black — monospace everything, CRT hacker aesthetic",
    light=LIGHT,
    dark=DARK,
    radius=0,  # Zero radius — terminals don't do rounded
    default_mode="dark",
)


# =============================================================================
# Design System
# =============================================================================

TYPOGRAPHY = TypographyStyle(
    name="terminal",
    # Monospace EVERYTHING — this is a terminal
    heading_font='"JetBrains Mono", "Fira Code", "SF Mono", monospace',
    body_font='"JetBrains Mono", "Fira Code", "SF Mono", monospace',
    base_size="14px",  # Smaller — terminal density
    heading_scale=1.15,  # Flat scale — terminals don't do dramatic headings
    line_height="1.5",
    body_line_height="1.6",
    heading_weight="700",  # Bold headings
    section_heading_weight="700",
    body_weight="400",
    letter_spacing="normal",
    prose_max_width="72rem",  # Wide — terminal is wide
    badge_radius="0px",  # Sharp badges
)

LAYOUT = LayoutStyle(
    name="terminal",
    space_unit="1rem",
    space_scale=1.5,
    border_radius_sm="0px",  # Zero everything
    border_radius_md="0px",
    border_radius_lg="0px",
    button_shape="sharp",
    card_shape="sharp",
    input_shape="sharp",
    container_width="1400px",  # Wide — terminal fills the screen
    grid_gap="1rem",  # Tight grid
    section_spacing="2rem",  # Compact sections
    hero_padding_top="4rem",  # No grand entrance — just content
    hero_padding_bottom="2rem",
    hero_line_height="1.2",
    hero_max_width="72rem",  # Full width
)

SURFACE = SurfaceStyle(
    name="terminal",
    # No shadows at all — terminals are flat
    shadow_sm="none",
    shadow_md="none",
    shadow_lg="none",
    border_width="1px",
    border_style="solid",
    surface_treatment="noise",  # CRT scanline noise
    backdrop_blur="0px",
    noise_opacity=0.08,  # CRT scanline grain
)

ICON = IconStyle(
    name="terminal",
    style="sharp",
    weight="bold",
    size_scale=0.9,  # Slightly smaller — dense
    stroke_width="2",
    corner_rounding="0px",
)

ANIMATION = AnimationStyle(
    name="terminal",
    entrance_effect="none",  # No animation — instant
    exit_effect="none",
    hover_effect="none",  # No hover effects — terminals don't hover
    hover_scale=1.0,
    hover_translate_y="0px",
    click_effect="none",
    loading_style="spinner",
    transition_style="instant",  # Everything instant
    duration_fast="0s",  # Zero — instant
    duration_normal="0.05s",
    duration_slow="0.1s",
    easing="linear",  # No curves
)

INTERACTION = InteractionStyle(
    name="terminal",
    button_hover="darken",  # Simple darken — no effects
    link_hover="underline",  # Underline only
    card_hover="border",  # Border highlight
    focus_style="outline",  # Hard outline
    focus_ring_width="2px",
)

DESIGN_SYSTEM = DesignSystem(
    name="terminal",
    display_name="Terminal",
    description="Monospace everything — green phosphor, zero animation, CRT density",
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
    name="terminal",
    display_name="Terminal",
    description="Green phosphor on black — monospace everything, CRT hacker aesthetic",
    category="bold",
    design_theme="terminal",
    color_preset="terminal",
    icon_style=ICON,
    animation_style=ANIMATION,
    pattern_style=PATTERN_NOISE,
    interaction_style=INTERACTION,
    illustration_style=ILLUST_FLAT,
)
