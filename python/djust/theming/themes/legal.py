"""Legal — Conservative, traditional serif, muted tones, maximum trust."""

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
# Conservative palette: navy primary, burgundy brand, warm off-whites.
# Designed for law firms, financial advisors, institutions.

LIGHT = ThemeTokens(
    background=ColorScale(40, 15, 98),  # Warm off-white
    foreground=ColorScale(220, 30, 15),  # Dark navy
    card=ColorScale(0, 0, 100),  # White cards
    card_foreground=ColorScale(220, 30, 15),
    popover=ColorScale(0, 0, 100),
    popover_foreground=ColorScale(220, 30, 15),
    primary=ColorScale(220, 50, 35),  # Conservative navy
    primary_foreground=ColorScale(0, 0, 100),
    secondary=ColorScale(40, 10, 95),  # Warm pale bg
    secondary_foreground=ColorScale(220, 30, 15),
    muted=ColorScale(40, 10, 91),  # Warm gray muted
    muted_foreground=ColorScale(220, 15, 45),  # Muted text
    accent=ColorScale(40, 10, 95),  # Warm hover surface
    accent_foreground=ColorScale(220, 30, 15),
    destructive=ColorScale(0, 55, 45),  # Muted red
    destructive_foreground=ColorScale(0, 0, 100),
    success=ColorScale(145, 40, 38),  # Conservative green
    success_foreground=ColorScale(0, 0, 100),
    warning=ColorScale(40, 60, 48),  # Muted amber
    warning_foreground=ColorScale(220, 30, 15),
    info=ColorScale(220, 50, 35),  # Navy info
    info_foreground=ColorScale(0, 0, 100),
    link=ColorScale(345, 40, 35),  # Burgundy links
    link_hover=ColorScale(345, 40, 28),  # Darker burgundy
    code=ColorScale(220, 30, 12),  # Dark navy code bg
    code_foreground=ColorScale(40, 15, 85),  # Warm light code text
    selection=ColorScale(220, 30, 88),  # Pale navy selection
    selection_foreground=ColorScale(220, 30, 15),
    brand=ColorScale(345, 40, 35),  # Burgundy
    brand_foreground=ColorScale(0, 0, 100),
    border=ColorScale(40, 10, 85),  # Warm gray border
    input=ColorScale(40, 8, 90),  # Warm input border
    ring=ColorScale(220, 50, 35),  # Navy focus ring
    surface_1=ColorScale(40, 15, 98),  # Warm off-white
    surface_2=ColorScale(40, 12, 96),  # Slightly deeper
    surface_3=ColorScale(40, 10, 93),  # Even deeper
)

DARK = ThemeTokens(
    background=ColorScale(220, 30, 12),  # Dark navy
    foreground=ColorScale(40, 10, 90),  # Warm off-white
    card=ColorScale(220, 25, 15),  # Dark card
    card_foreground=ColorScale(40, 10, 90),
    popover=ColorScale(220, 25, 15),
    popover_foreground=ColorScale(40, 10, 90),
    primary=ColorScale(220, 50, 50),  # Navy brightened
    primary_foreground=ColorScale(0, 0, 100),
    secondary=ColorScale(220, 20, 18),  # Dark panel
    secondary_foreground=ColorScale(40, 10, 90),
    muted=ColorScale(220, 15, 22),  # Dark muted
    muted_foreground=ColorScale(40, 8, 55),  # Muted text
    accent=ColorScale(220, 20, 18),  # Dark hover
    accent_foreground=ColorScale(40, 10, 90),
    destructive=ColorScale(0, 55, 52),  # Red brightened
    destructive_foreground=ColorScale(0, 0, 100),
    success=ColorScale(145, 40, 45),  # Green brightened
    success_foreground=ColorScale(0, 0, 100),
    warning=ColorScale(40, 60, 55),  # Amber brightened
    warning_foreground=ColorScale(220, 30, 12),
    info=ColorScale(220, 50, 50),  # Navy brightened
    info_foreground=ColorScale(0, 0, 100),
    link=ColorScale(345, 40, 50),  # Burgundy brightened
    link_hover=ColorScale(345, 40, 60),  # Lighter burgundy
    code=ColorScale(220, 30, 8),  # Deepest navy code
    code_foreground=ColorScale(40, 20, 72),  # Warm code text
    selection=ColorScale(220, 30, 22),  # Deep navy selection
    selection_foreground=ColorScale(40, 10, 90),
    brand=ColorScale(345, 40, 48),  # Burgundy brightened
    brand_foreground=ColorScale(0, 0, 100),
    border=ColorScale(220, 15, 22),  # Dark border
    input=ColorScale(220, 12, 18),  # Dark input
    ring=ColorScale(220, 50, 50),  # Navy focus ring
    surface_1=ColorScale(220, 30, 10),  # Deepest
    surface_2=ColorScale(220, 25, 13),  # Mid dark
    surface_3=ColorScale(220, 22, 16),  # Elevated
)

PRESET = ThemePreset(
    name="legal",
    display_name="Legal",
    description="Conservative traditional serif — muted tones, maximum trust",
    light=LIGHT,
    dark=DARK,
    radius=0.1875,  # 3px — barely rounded
)


# =============================================================================
# Design System
# =============================================================================

TYPOGRAPHY = TypographyStyle(
    name="legal",
    heading_font='"Georgia", "Cambria", serif',
    body_font='"Georgia", "Cambria", serif',
    base_size="16px",
    heading_scale=1.2,  # Conservative
    line_height="1.5",
    body_line_height="1.7",
    heading_weight="700",
    section_heading_weight="700",
    body_weight="400",
    letter_spacing="0em",
    prose_max_width="40rem",
    badge_radius="3px",  # Subtle badge rounding
)

LAYOUT = LayoutStyle(
    name="legal",
    space_unit="1rem",
    space_scale=1.5,
    border_radius_sm="2px",
    border_radius_md="3px",
    border_radius_lg="4px",
    button_shape="rounded",
    card_shape="rounded",
    input_shape="rounded",
    container_width="1000px",  # Narrow, traditional
    grid_gap="1.5rem",
    section_spacing="4rem",
    hero_padding_top="6rem",
    hero_padding_bottom="4rem",
    hero_line_height="1.2",
    hero_max_width="44rem",
)

SURFACE = SurfaceStyle(
    name="legal",
    shadow_sm="0 1px 2px 0 rgba(0, 0, 0, 0.04)",
    shadow_md="0 2px 6px -1px rgba(0, 0, 0, 0.06)",
    shadow_lg="0 6px 16px -3px rgba(0, 0, 0, 0.08)",
    border_width="1px",
    border_style="solid",
    surface_treatment="flat",
    backdrop_blur="0px",
    noise_opacity=0.0,
)

ICON = IconStyle(
    name="legal",
    style="outlined",
    weight="regular",
    size_scale=1.0,
    stroke_width="1.5",
    corner_rounding="1px",
)

ANIMATION = AnimationStyle(
    name="legal",
    entrance_effect="fade",
    exit_effect="fade",
    hover_effect="none",
    hover_scale=1.0,
    hover_translate_y="0px",
    click_effect="none",
    loading_style="spinner",
    transition_style="smooth",
    duration_fast="0.1s",
    duration_normal="0.2s",
    duration_slow="0.3s",
    easing="ease",
)

INTERACTION = InteractionStyle(
    name="legal",
    button_hover="color",
    link_hover="color",
    card_hover="shadow",
    focus_style="ring",
    focus_ring_width="2px",
)

DESIGN_SYSTEM = DesignSystem(
    name="legal",
    display_name="Legal",
    description="Conservative traditional serif — muted tones, maximum trust",
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
    name="legal",
    display_name="Legal",
    description="Conservative traditional serif — muted tones, maximum trust",
    category="professional",
    design_theme="legal",
    color_preset="legal",
    icon_style=ICON,
    animation_style=ANIMATION,
    pattern_style=PATTERN_MINIMAL,
    interaction_style=INTERACTION,
    illustration_style=ILLUST_LINE,
)
