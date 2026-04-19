"""Swiss — International Typographic Style, Helvetica, strict grid, red+black."""

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
    PATTERN_GRID,
    ILLUST_LINE,
)


# =============================================================================
# Color Preset
# =============================================================================
# Pure Swiss International Style: red, black, white.
# No warm/cool tinting — pure neutral grays throughout.

LIGHT = ThemeTokens(
    background=ColorScale(0, 0, 100),  # Pure white
    foreground=ColorScale(0, 0, 0),  # Pure black
    card=ColorScale(0, 0, 100),  # White cards
    card_foreground=ColorScale(0, 0, 0),
    popover=ColorScale(0, 0, 100),
    popover_foreground=ColorScale(0, 0, 0),
    primary=ColorScale(0, 100, 45),  # Swiss red
    primary_foreground=ColorScale(0, 0, 100),
    secondary=ColorScale(0, 0, 95),  # Neutral light gray
    secondary_foreground=ColorScale(0, 0, 0),
    muted=ColorScale(0, 0, 90),  # Pure gray muted
    muted_foreground=ColorScale(0, 0, 40),  # Mid gray text
    accent=ColorScale(0, 0, 95),  # Neutral hover surface
    accent_foreground=ColorScale(0, 0, 0),
    destructive=ColorScale(0, 100, 45),  # Same red
    destructive_foreground=ColorScale(0, 0, 100),
    success=ColorScale(120, 60, 35),  # Clean green
    success_foreground=ColorScale(0, 0, 100),
    warning=ColorScale(45, 80, 50),  # Clean yellow
    warning_foreground=ColorScale(0, 0, 0),
    info=ColorScale(210, 70, 45),  # Clean blue
    info_foreground=ColorScale(0, 0, 100),
    link=ColorScale(0, 100, 45),  # Red links
    link_hover=ColorScale(0, 100, 35),  # Darker red on hover
    code=ColorScale(0, 0, 5),  # Near-black code bg
    code_foreground=ColorScale(0, 100, 60),  # Red code text
    selection=ColorScale(0, 80, 90),  # Pale red selection
    selection_foreground=ColorScale(0, 0, 0),
    brand=ColorScale(0, 100, 45),  # Swiss red
    brand_foreground=ColorScale(0, 0, 100),
    border=ColorScale(0, 0, 0),  # Black borders
    input=ColorScale(0, 0, 88),  # Gray input border
    ring=ColorScale(0, 100, 45),  # Red focus ring
    surface_1=ColorScale(0, 0, 100),  # White
    surface_2=ColorScale(0, 0, 96),  # Light gray
    surface_3=ColorScale(0, 0, 92),  # Mid gray
)

DARK = ThemeTokens(
    background=ColorScale(0, 0, 0),  # Pure black
    foreground=ColorScale(0, 0, 100),  # Pure white
    card=ColorScale(0, 0, 6),  # Near-black card
    card_foreground=ColorScale(0, 0, 100),
    popover=ColorScale(0, 0, 6),
    popover_foreground=ColorScale(0, 0, 100),
    primary=ColorScale(0, 100, 55),  # Red brightened
    primary_foreground=ColorScale(0, 0, 100),
    secondary=ColorScale(0, 0, 10),  # Dark panel
    secondary_foreground=ColorScale(0, 0, 100),
    muted=ColorScale(0, 0, 15),  # Dark muted
    muted_foreground=ColorScale(0, 0, 60),  # Muted text
    accent=ColorScale(0, 0, 10),  # Dark hover
    accent_foreground=ColorScale(0, 0, 100),
    destructive=ColorScale(0, 100, 55),  # Red brightened
    destructive_foreground=ColorScale(0, 0, 100),
    success=ColorScale(120, 60, 45),  # Green brightened
    success_foreground=ColorScale(0, 0, 100),
    warning=ColorScale(45, 80, 55),  # Yellow brightened
    warning_foreground=ColorScale(0, 0, 0),
    info=ColorScale(210, 70, 55),  # Blue brightened
    info_foreground=ColorScale(0, 0, 100),
    link=ColorScale(0, 100, 60),  # Red brightened for dark
    link_hover=ColorScale(0, 100, 70),  # Lighter red
    code=ColorScale(0, 0, 3),  # Deepest black code bg
    code_foreground=ColorScale(0, 100, 65),  # Red code text
    selection=ColorScale(0, 80, 20),  # Deep red selection
    selection_foreground=ColorScale(0, 0, 100),
    brand=ColorScale(0, 100, 55),  # Red brightened
    brand_foreground=ColorScale(0, 0, 100),
    border=ColorScale(0, 0, 100),  # White borders on black
    input=ColorScale(0, 0, 18),  # Dark input
    ring=ColorScale(0, 100, 55),  # Red focus ring
    surface_1=ColorScale(0, 0, 0),  # Pure black
    surface_2=ColorScale(0, 0, 4),  # Near-black
    surface_3=ColorScale(0, 0, 8),  # Slightly lighter
)

PRESET = ThemePreset(
    name="swiss",
    display_name="Swiss",
    description="International Typographic Style — Helvetica, strict grid, red+black",
    light=LIGHT,
    dark=DARK,
    radius=0.0,  # 0px — strict geometric
)


# =============================================================================
# Design System
# =============================================================================

TYPOGRAPHY = TypographyStyle(
    name="swiss",
    heading_font='"Helvetica Neue", Arial, sans-serif',
    body_font='"Helvetica Neue", Arial, sans-serif',
    base_size="16px",
    heading_scale=1.3,
    line_height="1.4",
    body_line_height="1.4",
    heading_weight="700",  # Bold headings
    section_heading_weight="700",
    body_weight="400",
    letter_spacing="0.02em",  # Tight, precise
    prose_max_width="40rem",
    badge_radius="0px",  # Sharp badges
)

LAYOUT = LayoutStyle(
    name="swiss",
    space_unit="1rem",
    space_scale=1.5,
    border_radius_sm="0px",
    border_radius_md="0px",
    border_radius_lg="0px",
    button_shape="sharp",
    card_shape="sharp",
    input_shape="sharp",
    container_width="1200px",
    grid_gap="1rem",  # Tight grid gap
    section_spacing="4rem",
    hero_padding_top="6rem",
    hero_padding_bottom="4rem",
    hero_line_height="1.1",
    hero_max_width="52rem",
)

SURFACE = SurfaceStyle(
    name="swiss",
    shadow_sm="none",  # No shadows
    shadow_md="none",
    shadow_lg="none",
    border_width="2px",  # Thick geometric borders
    border_style="solid",
    surface_treatment="flat",
    backdrop_blur="0px",
    noise_opacity=0.0,
)

ICON = IconStyle(
    name="swiss",
    style="outlined",
    weight="regular",
    size_scale=1.0,
    stroke_width="2",  # Bold geometric strokes
    corner_rounding="0px",
)

ANIMATION = AnimationStyle(
    name="swiss",
    entrance_effect="none",  # No frills
    exit_effect="none",
    hover_effect="none",
    hover_scale=1.0,
    hover_translate_y="0px",
    click_effect="none",
    loading_style="spinner",
    transition_style="instant",
    duration_fast="0.05s",
    duration_normal="0.1s",  # Snappy, linear
    duration_slow="0.15s",
    easing="linear",  # Linear — no curves
)

INTERACTION = InteractionStyle(
    name="swiss",
    button_hover="color",
    link_hover="color",
    card_hover="none",  # No card hover — pure grid
    focus_style="ring",
    focus_ring_width="2px",
)

DESIGN_SYSTEM = DesignSystem(
    name="swiss",
    display_name="Swiss",
    description="International Typographic Style — strict grid, bold Helvetica, zero decoration",
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
    name="swiss",
    display_name="Swiss",
    description="International Typographic Style — Helvetica, strict grid, red+black",
    category="professional",
    design_theme="swiss",
    color_preset="swiss",
    icon_style=ICON,
    animation_style=ANIMATION,
    pattern_style=PATTERN_GRID,
    interaction_style=INTERACTION,
    illustration_style=ILLUST_LINE,
)
