"""Midnight — Ultra-dark with purple gradients, glass surfaces, ethereal glows."""

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
    PATTERN_GLASS,
    ILLUST_3D,
)


# =============================================================================
# Color Preset
# =============================================================================
# Ultra-dark purple with electric accents and glowing shadows.
# Think: midnight sky, aurora borealis, neon on glass.

LIGHT = ThemeTokens(
    background=ColorScale(260, 20, 98),  # Very pale purple
    foreground=ColorScale(260, 30, 15),  # Deep purple
    card=ColorScale(0, 0, 100),  # White cards
    card_foreground=ColorScale(260, 30, 15),
    popover=ColorScale(0, 0, 100),
    popover_foreground=ColorScale(260, 30, 15),
    primary=ColorScale(265, 80, 60),  # Electric purple
    primary_foreground=ColorScale(0, 0, 100),
    secondary=ColorScale(260, 15, 95),  # Pale purple bg
    secondary_foreground=ColorScale(260, 30, 15),
    muted=ColorScale(260, 12, 91),  # Soft purple muted
    muted_foreground=ColorScale(260, 15, 45),  # Muted text
    accent=ColorScale(260, 15, 95),  # Pale purple hover
    accent_foreground=ColorScale(260, 30, 15),
    destructive=ColorScale(350, 70, 50),  # Bright red
    destructive_foreground=ColorScale(0, 0, 100),
    success=ColorScale(155, 60, 42),  # Teal green
    success_foreground=ColorScale(0, 0, 100),
    warning=ColorScale(40, 80, 52),  # Amber
    warning_foreground=ColorScale(260, 30, 15),
    info=ColorScale(190, 80, 50),  # Cyan info
    info_foreground=ColorScale(0, 0, 100),
    link=ColorScale(190, 80, 45),  # Cyan links
    link_hover=ColorScale(265, 80, 55),  # Purple on hover
    code=ColorScale(260, 40, 10),  # Deepest purple code bg
    code_foreground=ColorScale(190, 80, 70),  # Cyan code text
    selection=ColorScale(265, 60, 90),  # Pale purple selection
    selection_foreground=ColorScale(260, 30, 15),
    brand=ColorScale(190, 80, 60),  # Cyan brand
    brand_foreground=ColorScale(0, 0, 100),
    border=ColorScale(260, 15, 85),  # Purple-tinted border
    input=ColorScale(260, 12, 90),  # Pale purple input
    ring=ColorScale(265, 80, 60),  # Purple focus ring
    surface_1=ColorScale(260, 20, 98),  # Palest
    surface_2=ColorScale(260, 15, 96),  # Mid
    surface_3=ColorScale(260, 12, 93),  # Deeper
)

DARK = ThemeTokens(
    background=ColorScale(260, 40, 6),  # Near-black purple
    foreground=ColorScale(260, 15, 88),  # Pale lavender
    card=ColorScale(260, 35, 10),  # Dark purple card
    card_foreground=ColorScale(260, 15, 88),
    popover=ColorScale(260, 35, 10),
    popover_foreground=ColorScale(260, 15, 88),
    primary=ColorScale(265, 80, 65),  # Electric purple brightened
    primary_foreground=ColorScale(0, 0, 100),
    secondary=ColorScale(260, 30, 12),  # Dark purple panel
    secondary_foreground=ColorScale(260, 15, 88),
    muted=ColorScale(260, 25, 16),  # Dark muted
    muted_foreground=ColorScale(260, 12, 55),  # Muted text
    accent=ColorScale(260, 30, 10),  # Dark purple hover
    accent_foreground=ColorScale(260, 15, 88),
    destructive=ColorScale(350, 70, 55),  # Red brightened
    destructive_foreground=ColorScale(0, 0, 100),
    success=ColorScale(155, 60, 50),  # Teal brightened
    success_foreground=ColorScale(0, 0, 100),
    warning=ColorScale(40, 80, 58),  # Amber brightened
    warning_foreground=ColorScale(260, 40, 8),
    info=ColorScale(190, 80, 58),  # Cyan brightened
    info_foreground=ColorScale(0, 0, 100),
    link=ColorScale(190, 80, 60),  # Cyan brightened
    link_hover=ColorScale(190, 80, 72),  # Lighter cyan
    code=ColorScale(260, 40, 4),  # Deepest purple code bg
    code_foreground=ColorScale(190, 80, 65),  # Cyan code text
    selection=ColorScale(265, 60, 20),  # Deep purple selection
    selection_foreground=ColorScale(260, 15, 88),
    brand=ColorScale(190, 80, 65),  # Cyan brightened
    brand_foreground=ColorScale(0, 0, 100),
    border=ColorScale(260, 25, 18),  # Dark purple border
    input=ColorScale(260, 22, 15),  # Dark purple input
    ring=ColorScale(265, 80, 65),  # Purple focus ring
    surface_1=ColorScale(260, 40, 5),  # Deepest
    surface_2=ColorScale(260, 38, 8),  # Mid dark
    surface_3=ColorScale(260, 35, 11),  # Elevated
)

PRESET = ThemePreset(
    name="midnight",
    display_name="Midnight",
    description="Ultra-dark purple with glass surfaces and ethereal glows",
    light=LIGHT,
    dark=DARK,
    radius=0.5,  # 8px
)


# =============================================================================
# Design System
# =============================================================================

TYPOGRAPHY = TypographyStyle(
    name="midnight",
    heading_font='"Inter", system-ui, sans-serif',
    body_font='"Inter", system-ui, sans-serif',
    base_size="16px",
    heading_scale=1.25,
    line_height="1.5",
    body_line_height="1.5",
    heading_weight="500",  # Medium weight
    section_heading_weight="500",
    body_weight="400",
    letter_spacing="-0.01em",  # Slightly tight
    prose_max_width="42rem",
    badge_radius="9999px",  # Pill badges
)

LAYOUT = LayoutStyle(
    name="midnight",
    space_unit="1rem",
    space_scale=1.5,
    border_radius_sm="6px",
    border_radius_md="8px",
    border_radius_lg="12px",
    button_shape="rounded",
    card_shape="rounded",
    input_shape="rounded",
    container_width="1200px",
    grid_gap="1.5rem",
    section_spacing="5rem",
    hero_padding_top="8rem",
    hero_padding_bottom="5rem",
    hero_line_height="1.15",
    hero_max_width="52rem",
)

SURFACE = SurfaceStyle(
    name="midnight",
    # Purple-tinted glow shadows
    shadow_sm="0 2px 8px 0 rgba(120, 80, 200, 0.12)",
    shadow_md="0 6px 16px -2px rgba(120, 80, 200, 0.2)",
    shadow_lg="0 12px 32px -4px rgba(120, 80, 200, 0.28), 0 0 20px rgba(120, 80, 200, 0.1)",
    border_width="1px",
    border_style="solid",
    surface_treatment="glass",  # Glass surface treatment
    backdrop_blur="12px",  # Frosted glass
    noise_opacity=0.0,
)

ICON = IconStyle(
    name="midnight",
    style="outlined",
    weight="regular",
    size_scale=1.0,
    stroke_width="1.5",
    corner_rounding="2px",
)

ANIMATION = AnimationStyle(
    name="midnight",
    entrance_effect="fade",
    exit_effect="fade",
    hover_effect="glow",  # Ethereal glow on hover
    hover_scale=1.0,
    hover_translate_y="-1px",
    click_effect="pulse",
    loading_style="pulse",
    transition_style="smooth",
    duration_fast="0.15s",
    duration_normal="0.3s",
    duration_slow="0.5s",
    easing="cubic-bezier(0.4, 0, 0.2, 1)",
)

INTERACTION = InteractionStyle(
    name="midnight",
    button_hover="glow",  # Glow effect on buttons
    link_hover="color",
    card_hover="shadow",  # Shadow glow on cards
    focus_style="ring",
    focus_ring_width="2px",
)

DESIGN_SYSTEM = DesignSystem(
    name="midnight",
    display_name="Midnight",
    description="Ultra-dark purple — glass surfaces, ethereal glows, electric accents",
    category="dark",
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
    name="midnight",
    display_name="Midnight",
    description="Ultra-dark purple with glass surfaces and ethereal glows",
    category="dark",
    design_theme="midnight",
    color_preset="midnight",
    icon_style=ICON,
    animation_style=ANIMATION,
    pattern_style=PATTERN_GLASS,
    interaction_style=INTERACTION,
    illustration_style=ILLUST_3D,
)
