"""Forest Floor — Deep mossy greens, bark browns, dappled-light texture."""

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
    ILLUST_HAND_DRAWN,
)


# =============================================================================
# Color Preset
# =============================================================================
# Deep forest palette: moss, fern, bark, lichen.
# Think: walking through old-growth forest, dappled light.

LIGHT = ThemeTokens(
    background=ColorScale(120, 15, 96),  # Pale sage
    foreground=ColorScale(30, 25, 12),  # Deep bark
    card=ColorScale(100, 10, 99),  # Almost white with green tint
    card_foreground=ColorScale(30, 25, 12),
    popover=ColorScale(100, 10, 99),
    popover_foreground=ColorScale(30, 25, 12),
    primary=ColorScale(140, 45, 35),  # Moss green
    primary_foreground=ColorScale(0, 0, 100),
    secondary=ColorScale(120, 12, 93),  # Pale sage panel
    secondary_foreground=ColorScale(30, 25, 12),
    muted=ColorScale(120, 10, 90),  # Sage muted
    muted_foreground=ColorScale(100, 10, 42),  # Green-gray text
    accent=ColorScale(120, 12, 93),  # Sage hover surface
    accent_foreground=ColorScale(30, 25, 12),
    destructive=ColorScale(350, 50, 45),  # Berry red
    destructive_foreground=ColorScale(0, 0, 100),
    success=ColorScale(140, 50, 38),  # Deep forest green
    success_foreground=ColorScale(0, 0, 100),
    warning=ColorScale(40, 60, 50),  # Amber mushroom
    warning_foreground=ColorScale(30, 25, 12),
    info=ColorScale(200, 40, 48),  # Stream blue
    info_foreground=ColorScale(0, 0, 100),
    link=ColorScale(120, 40, 38),  # Fern green links
    link_hover=ColorScale(140, 45, 30),  # Deeper moss on hover
    code=ColorScale(140, 30, 10),  # Dark forest code bg
    code_foreground=ColorScale(100, 35, 70),  # Lichen green code text
    selection=ColorScale(140, 30, 88),  # Pale moss selection
    selection_foreground=ColorScale(30, 25, 12),
    brand=ColorScale(120, 40, 45),  # Fern green
    brand_foreground=ColorScale(0, 0, 100),
    border=ColorScale(100, 12, 83),  # Sage border
    input=ColorScale(120, 10, 90),  # Sage input
    ring=ColorScale(140, 45, 35),  # Moss focus ring
    surface_1=ColorScale(120, 15, 96),  # Pale sage
    surface_2=ColorScale(120, 12, 94),  # Slightly deeper
    surface_3=ColorScale(120, 10, 91),  # Deeper still
)

DARK = ThemeTokens(
    background=ColorScale(140, 30, 7),  # Deep forest
    foreground=ColorScale(100, 10, 80),  # Lichen gray
    card=ColorScale(140, 25, 10),  # Dark forest card
    card_foreground=ColorScale(100, 10, 80),
    popover=ColorScale(140, 25, 10),
    popover_foreground=ColorScale(100, 10, 80),
    primary=ColorScale(140, 45, 42),  # Moss brightened
    primary_foreground=ColorScale(0, 0, 100),
    secondary=ColorScale(140, 20, 12),  # Dark forest panel
    secondary_foreground=ColorScale(100, 10, 80),
    muted=ColorScale(140, 15, 16),  # Dark muted
    muted_foreground=ColorScale(100, 8, 50),  # Muted lichen text
    accent=ColorScale(140, 20, 12),  # Dark forest hover
    accent_foreground=ColorScale(100, 10, 80),
    destructive=ColorScale(350, 50, 52),  # Berry red brightened
    destructive_foreground=ColorScale(0, 0, 100),
    success=ColorScale(140, 50, 45),  # Green brightened
    success_foreground=ColorScale(0, 0, 100),
    warning=ColorScale(40, 60, 55),  # Mushroom brightened
    warning_foreground=ColorScale(30, 25, 10),
    info=ColorScale(200, 40, 55),  # Stream blue brightened
    info_foreground=ColorScale(0, 0, 100),
    link=ColorScale(120, 40, 52),  # Fern brightened
    link_hover=ColorScale(120, 45, 62),  # Lighter fern
    code=ColorScale(140, 30, 5),  # Deepest forest code
    code_foreground=ColorScale(100, 35, 62),  # Lichen code text
    selection=ColorScale(140, 30, 18),  # Deep moss selection
    selection_foreground=ColorScale(100, 10, 80),
    brand=ColorScale(120, 40, 52),  # Fern brightened
    brand_foreground=ColorScale(0, 0, 100),
    border=ColorScale(140, 15, 18),  # Dark forest border
    input=ColorScale(140, 12, 15),  # Dark input
    ring=ColorScale(140, 45, 42),  # Moss focus ring
    surface_1=ColorScale(140, 30, 5),  # Deepest
    surface_2=ColorScale(140, 28, 8),  # Mid dark
    surface_3=ColorScale(140, 25, 11),  # Elevated
)

PRESET = ThemePreset(
    name="forest_floor",
    display_name="Forest Floor",
    description="Deep mossy greens, bark browns, dappled-light texture",
    light=LIGHT,
    dark=DARK,
    radius=0.375,  # 6px
)


# =============================================================================
# Design System
# =============================================================================

TYPOGRAPHY = TypographyStyle(
    name="forest_floor",
    heading_font='"Merriweather", Georgia, serif',
    body_font='"Inter", system-ui, sans-serif',
    base_size="16px",
    heading_scale=1.25,
    line_height="1.5",
    body_line_height="1.7",
    heading_weight="700",
    section_heading_weight="700",
    body_weight="400",
    letter_spacing="0em",
    prose_max_width="38rem",  # Narrow — nature path feel
    badge_radius="9999px",  # Pill badges
)

LAYOUT = LayoutStyle(
    name="forest_floor",
    space_unit="1rem",
    space_scale=1.5,
    border_radius_sm="4px",
    border_radius_md="6px",
    border_radius_lg="8px",
    button_shape="rounded",
    card_shape="rounded",
    input_shape="rounded",
    container_width="1000px",
    grid_gap="1.5rem",
    section_spacing="4rem",
    hero_padding_top="6rem",
    hero_padding_bottom="4rem",
    hero_line_height="1.2",
    hero_max_width="46rem",
)

SURFACE = SurfaceStyle(
    name="forest_floor",
    # Brown-tinted earthy shadows
    shadow_sm="0 2px 6px 0 rgba(60, 40, 20, 0.1)",
    shadow_md="0 4px 12px -2px rgba(60, 40, 20, 0.14)",
    shadow_lg="0 10px 24px -4px rgba(60, 40, 20, 0.18)",
    border_width="1px",
    border_style="solid",
    surface_treatment="flat",
    backdrop_blur="0px",
    noise_opacity=0.05,  # Dappled light texture
)

ICON = IconStyle(
    name="forest_floor",
    style="rounded",
    weight="regular",
    size_scale=1.0,
    stroke_width="1.5",
    corner_rounding="3px",
)

ANIMATION = AnimationStyle(
    name="forest_floor",
    entrance_effect="fade",
    exit_effect="fade",
    hover_effect="none",
    hover_scale=1.0,
    hover_translate_y="0px",
    click_effect="pulse",
    loading_style="spinner",
    transition_style="smooth",
    duration_fast="0.15s",
    duration_normal="0.3s",  # Gentle, natural
    duration_slow="0.5s",
    easing="cubic-bezier(0.4, 0, 0.2, 1)",
)

INTERACTION = InteractionStyle(
    name="forest_floor",
    button_hover="color",
    link_hover="color",
    card_hover="shadow",
    focus_style="ring",
    focus_ring_width="2px",
)

DESIGN_SYSTEM = DesignSystem(
    name="forest_floor",
    display_name="Forest Floor",
    description="Deep mossy greens, bark browns, dappled-light texture",
    category="nature",
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
    name="forest_floor",
    display_name="Forest Floor",
    description="Deep mossy greens, bark browns, dappled-light texture",
    category="nature",
    design_theme="forest_floor",
    color_preset="forest_floor",
    icon_style=ICON,
    animation_style=ANIMATION,
    pattern_style=PATTERN_NOISE,
    interaction_style=INTERACTION,
    illustration_style=ILLUST_HAND_DRAWN,
)
