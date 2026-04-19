"""Magazine — Editorial B&W with thin headings, wide tracking, one red accent."""

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
    PATTERN_MINIMAL,
    ILLUST_LINE,
)


# =============================================================================
# Color Preset
# =============================================================================
# Editorial black-and-white with a single red accent.
# Inspired by classic magazine layouts: Vogue, The New Yorker, Esquire.

LIGHT = ThemeTokens(
    background=ColorScale(0, 0, 100),  # Pure white
    foreground=ColorScale(0, 0, 8),  # Near-black
    card=ColorScale(0, 0, 100),  # White cards
    card_foreground=ColorScale(0, 0, 8),
    popover=ColorScale(0, 0, 100),
    popover_foreground=ColorScale(0, 0, 8),
    primary=ColorScale(0, 85, 50),  # Editorial red
    primary_foreground=ColorScale(0, 0, 100),
    secondary=ColorScale(0, 0, 96),  # Very light gray
    secondary_foreground=ColorScale(0, 0, 8),
    muted=ColorScale(0, 0, 94),  # Light gray muted
    muted_foreground=ColorScale(0, 0, 42),  # Mid gray text
    accent=ColorScale(0, 0, 96),  # Subtle hover surface
    accent_foreground=ColorScale(0, 0, 8),
    destructive=ColorScale(0, 85, 50),  # Same red
    destructive_foreground=ColorScale(0, 0, 100),
    success=ColorScale(145, 50, 38),  # Muted green
    success_foreground=ColorScale(0, 0, 100),
    warning=ColorScale(40, 70, 50),  # Warm amber
    warning_foreground=ColorScale(0, 0, 8),
    info=ColorScale(210, 50, 50),  # Steel blue
    info_foreground=ColorScale(0, 0, 100),
    link=ColorScale(0, 85, 45),  # Red links (editorial)
    link_hover=ColorScale(0, 85, 35),  # Darker red on hover
    code=ColorScale(0, 0, 8),  # Near-black code bg
    code_foreground=ColorScale(0, 0, 90),  # Light text on dark code
    selection=ColorScale(0, 60, 92),  # Pale red selection
    selection_foreground=ColorScale(0, 0, 8),
    brand=ColorScale(0, 85, 50),  # Editorial red
    brand_foreground=ColorScale(0, 0, 100),
    border=ColorScale(0, 0, 85),  # Hairline gray
    input=ColorScale(0, 0, 90),  # Light gray input border
    ring=ColorScale(0, 85, 50),  # Red focus ring
    surface_1=ColorScale(0, 0, 100),  # White
    surface_2=ColorScale(0, 0, 97),  # Off-white
    surface_3=ColorScale(0, 0, 94),  # Light gray
)

DARK = ThemeTokens(
    background=ColorScale(0, 0, 5),  # Near-black
    foreground=ColorScale(0, 0, 95),  # Off-white
    card=ColorScale(0, 0, 8),  # Slightly lighter card
    card_foreground=ColorScale(0, 0, 95),
    popover=ColorScale(0, 0, 8),
    popover_foreground=ColorScale(0, 0, 95),
    primary=ColorScale(0, 85, 55),  # Red brightened for dark
    primary_foreground=ColorScale(0, 0, 100),
    secondary=ColorScale(0, 0, 10),  # Dark panel
    secondary_foreground=ColorScale(0, 0, 95),
    muted=ColorScale(0, 0, 15),  # Dark muted
    muted_foreground=ColorScale(0, 0, 55),  # Muted text on dark
    accent=ColorScale(0, 0, 10),  # Subtle dark hover
    accent_foreground=ColorScale(0, 0, 95),
    destructive=ColorScale(0, 85, 55),  # Red brightened
    destructive_foreground=ColorScale(0, 0, 100),
    success=ColorScale(145, 50, 48),  # Green brightened
    success_foreground=ColorScale(0, 0, 100),
    warning=ColorScale(40, 70, 55),  # Amber brightened
    warning_foreground=ColorScale(0, 0, 8),
    info=ColorScale(210, 50, 58),  # Blue brightened
    info_foreground=ColorScale(0, 0, 100),
    link=ColorScale(0, 85, 60),  # Red brightened for dark
    link_hover=ColorScale(0, 85, 70),  # Lighter red on hover
    code=ColorScale(0, 0, 3),  # Very dark code bg
    code_foreground=ColorScale(0, 60, 70),  # Muted red code text
    selection=ColorScale(0, 60, 20),  # Deep red selection
    selection_foreground=ColorScale(0, 0, 95),
    brand=ColorScale(0, 85, 55),  # Red brightened
    brand_foreground=ColorScale(0, 0, 100),
    border=ColorScale(0, 0, 18),  # Dark border
    input=ColorScale(0, 0, 15),  # Dark input
    ring=ColorScale(0, 85, 55),  # Red focus ring
    surface_1=ColorScale(0, 0, 5),  # Deepest
    surface_2=ColorScale(0, 0, 7),  # Mid dark
    surface_3=ColorScale(0, 0, 10),  # Slightly elevated
)

PRESET = ThemePreset(
    name="magazine",
    display_name="Magazine",
    description="Editorial B&W with thin headings, wide tracking, one red accent",
    light=LIGHT,
    dark=DARK,
    radius=0.0,  # 0px — sharp editorial edges
)


# =============================================================================
# Design System
# =============================================================================

TYPOGRAPHY = TypographyStyle(
    name="magazine",
    heading_font='"Playfair Display", Georgia, serif',
    body_font='"Inter", system-ui, sans-serif',
    base_size="16px",
    heading_scale=1.4,  # Dramatic editorial scale
    line_height="1.5",
    body_line_height="1.7",  # Generous reading
    heading_weight="200",  # Ultra-thin headings
    section_heading_weight="200",
    body_weight="400",
    letter_spacing="0.12em",  # Wide tracking (signature editorial)
    prose_max_width="36rem",  # Narrow prose column
    badge_radius="0px",  # Sharp badges
)

LAYOUT = LayoutStyle(
    name="magazine",
    space_unit="1rem",
    space_scale=1.5,
    border_radius_sm="0px",
    border_radius_md="0px",
    border_radius_lg="0px",
    button_shape="sharp",
    card_shape="sharp",
    input_shape="sharp",
    container_width="1000px",  # Narrow editorial width
    grid_gap="1.5rem",
    section_spacing="6rem",  # Dramatic whitespace
    hero_padding_top="8rem",
    hero_padding_bottom="6rem",
    hero_line_height="1.1",
    hero_max_width="48rem",
)

SURFACE = SurfaceStyle(
    name="magazine",
    shadow_sm="none",  # No shadows — flat editorial
    shadow_md="none",
    shadow_lg="none",
    border_width="1px",  # Hairline borders
    border_style="solid",
    surface_treatment="flat",
    backdrop_blur="0px",
    noise_opacity=0.0,
)

ICON = IconStyle(
    name="magazine",
    style="outlined",
    weight="thin",  # Thin to match headings
    size_scale=1.0,
    stroke_width="1",
    corner_rounding="0px",
)

ANIMATION = AnimationStyle(
    name="magazine",
    entrance_effect="fade",
    exit_effect="fade",
    hover_effect="none",  # Elegant — no movement
    hover_scale=1.0,
    hover_translate_y="0px",
    click_effect="none",
    loading_style="spinner",
    transition_style="smooth",
    duration_fast="0.2s",
    duration_normal="0.3s",  # Elegant, measured
    duration_slow="0.5s",
    easing="cubic-bezier(0.4, 0, 0.2, 1)",
)

INTERACTION = InteractionStyle(
    name="magazine",
    button_hover="color",  # Subtle color shift only
    link_hover="color",
    card_hover="none",  # No card hover effects
    focus_style="ring",
    focus_ring_width="1px",  # Thin ring
)

DESIGN_SYSTEM = DesignSystem(
    name="magazine",
    display_name="Magazine",
    description="Editorial B&W — thin serif headings, wide tracking, dramatic whitespace",
    category="editorial",
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
    name="magazine",
    display_name="Magazine",
    description="Editorial B&W with thin headings, wide tracking, one red accent",
    category="editorial",
    design_theme="magazine",
    color_preset="magazine",
    icon_style=ICON,
    animation_style=ANIMATION,
    pattern_style=PATTERN_MINIMAL,
    interaction_style=INTERACTION,
    illustration_style=ILLUST_LINE,
)
