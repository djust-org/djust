"""Poimandres — Deep space, blueberry-inspired, easy on eyes."""

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
# Colors from Poimandres (drcmda/poimandres-theme):
# - Background #1b1e28 (H=226 S=19 L=13)
# - Foreground #a6accd (H=231 S=28 L=73)
# - Teal #5de4c7 (primary), Lavender #d0679d (brand)
# - Blue1 #89ddff, Blue2 #add7ff
# - Yellow #fffac2, Pink #fcc5e9

LIGHT = ThemeTokens(
    background=ColorScale(295, 20, 98),  # Pale pink-white
    foreground=ColorScale(226, 25, 18),  # Dark purple fg
    card=ColorScale(0, 0, 100),
    card_foreground=ColorScale(226, 25, 18),
    popover=ColorScale(0, 0, 100),
    popover_foreground=ColorScale(226, 25, 18),
    primary=ColorScale(167, 71, 53),  # Teal — slightly muted for light
    primary_foreground=ColorScale(0, 0, 100),
    secondary=ColorScale(295, 15, 95),
    secondary_foreground=ColorScale(226, 25, 18),
    muted=ColorScale(295, 12, 90),
    muted_foreground=ColorScale(231, 15, 45),
    accent=ColorScale(295, 15, 95),  # Subtle pink hover
    accent_foreground=ColorScale(226, 25, 18),
    destructive=ColorScale(329, 53, 51),  # Lavender as destructive
    destructive_foreground=ColorScale(0, 0, 100),
    success=ColorScale(167, 71, 53),  # Teal
    success_foreground=ColorScale(0, 0, 100),
    warning=ColorScale(55, 100, 78),  # Yellow muted for light
    warning_foreground=ColorScale(226, 25, 18),
    info=ColorScale(197, 100, 67),  # Blue1
    info_foreground=ColorScale(226, 25, 18),
    link=ColorScale(209, 100, 74),  # Blue2
    link_hover=ColorScale(209, 100, 60),
    code=ColorScale(226, 19, 13),  # Dark code bg
    code_foreground=ColorScale(167, 71, 63),  # Teal code text
    selection=ColorScale(167, 50, 90),  # Pale teal selection
    selection_foreground=ColorScale(226, 25, 18),
    brand=ColorScale(329, 53, 61),  # Lavender
    brand_foreground=ColorScale(0, 0, 100),
    border=ColorScale(295, 12, 88),
    input=ColorScale(295, 12, 93),
    ring=ColorScale(167, 71, 53),
    surface_1=ColorScale(295, 20, 98),
    surface_2=ColorScale(295, 15, 95),
    surface_3=ColorScale(295, 12, 92),
)

DARK = ThemeTokens(
    background=ColorScale(226, 19, 13),  # #1b1e28
    foreground=ColorScale(231, 28, 73),  # #a6accd
    card=ColorScale(226, 18, 17),
    card_foreground=ColorScale(231, 28, 73),
    popover=ColorScale(226, 18, 17),
    popover_foreground=ColorScale(231, 28, 73),
    primary=ColorScale(167, 71, 63),  # Teal #5de4c7
    primary_foreground=ColorScale(226, 19, 13),
    secondary=ColorScale(226, 16, 20),
    secondary_foreground=ColorScale(231, 28, 73),
    muted=ColorScale(226, 14, 23),
    muted_foreground=ColorScale(231, 18, 48),
    accent=ColorScale(226, 16, 20),  # Subtle dark hover
    accent_foreground=ColorScale(231, 28, 73),
    destructive=ColorScale(329, 53, 61),  # Lavender
    destructive_foreground=ColorScale(0, 0, 100),
    success=ColorScale(167, 71, 63),  # Teal
    success_foreground=ColorScale(226, 19, 13),
    warning=ColorScale(55, 100, 88),  # Yellow #fffac2
    warning_foreground=ColorScale(226, 19, 13),
    info=ColorScale(197, 100, 77),  # Blue1 #89ddff
    info_foreground=ColorScale(226, 19, 13),
    link=ColorScale(209, 100, 84),  # Blue2 #add7ff
    link_hover=ColorScale(209, 100, 90),
    code=ColorScale(226, 19, 9),  # Very dark code bg
    code_foreground=ColorScale(167, 71, 63),  # Teal code text
    selection=ColorScale(226, 30, 22),  # Purple-tinted selection
    selection_foreground=ColorScale(231, 28, 85),
    brand=ColorScale(329, 53, 61),  # Lavender #d0679d
    brand_foreground=ColorScale(0, 0, 100),
    border=ColorScale(226, 14, 24),
    input=ColorScale(226, 14, 24),
    ring=ColorScale(167, 71, 63),
    surface_1=ColorScale(226, 19, 9),
    surface_2=ColorScale(226, 19, 13),
    surface_3=ColorScale(226, 18, 17),
)

PRESET = ThemePreset(
    name="poimandres",
    display_name="Poimandres",
    description="Deep space blueberry — teal and lavender on dark cosmic blue",
    light=LIGHT,
    dark=DARK,
    radius=0.5,
    default_mode="dark",
)


# =============================================================================
# Design System
# =============================================================================

TYPOGRAPHY = TypographyStyle(
    name="poimandres",
    heading_font='"Inter", system-ui, sans-serif',
    body_font='"Inter", system-ui, sans-serif',
    base_size="16px",
    heading_scale=1.25,
    line_height="1.5",
    body_line_height="1.6",
    heading_weight="500",
    section_heading_weight="500",
    body_weight="400",
    letter_spacing="-0.01em",
    prose_max_width="42rem",
    badge_radius="9999px",
)

LAYOUT = LayoutStyle(
    name="poimandres",
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
    section_spacing="4.5rem",
    hero_padding_top="8rem",
    hero_padding_bottom="5rem",
    hero_line_height="1.15",
    hero_max_width="52rem",
)

SURFACE = SurfaceStyle(
    name="poimandres",
    shadow_sm="0 2px 4px rgba(27, 30, 40, 0.2)",
    shadow_md="0 4px 12px rgba(27, 30, 40, 0.25)",
    shadow_lg="0 8px 24px rgba(27, 30, 40, 0.3)",
    border_width="1px",
    border_style="solid",
    surface_treatment="glass",
    backdrop_blur="8px",
    noise_opacity=0.0,
)

ICON = IconStyle(
    name="poimandres",
    style="outlined",
    weight="regular",
    size_scale=1.0,
    stroke_width="1.5",
    corner_rounding="2px",
)

ANIMATION = AnimationStyle(
    name="poimandres",
    entrance_effect="fade",
    exit_effect="fade",
    hover_effect="lift",
    hover_scale=1.0,
    hover_translate_y="-2px",
    click_effect="pulse",
    loading_style="spinner",
    transition_style="smooth",
    duration_fast="0.15s",
    duration_normal="0.25s",
    duration_slow="0.35s",
    easing="cubic-bezier(0.4, 0, 0.2, 1)",
)

INTERACTION = InteractionStyle(
    name="poimandres",
    button_hover="lift",
    link_hover="color",
    card_hover="shadow",
    focus_style="ring",
    focus_ring_width="2px",
)

DESIGN_SYSTEM = DesignSystem(
    name="poimandres",
    display_name="Poimandres",
    description="Cosmic dev — glass surfaces, purple-tinted shadows, smooth transitions",
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
    name="poimandres",
    display_name="Poimandres",
    description="Deep space blueberry — teal and lavender on dark cosmic blue",
    category="developer",
    design_theme="poimandres",
    color_preset="poimandres",
    icon_style=ICON,
    animation_style=ANIMATION,
    pattern_style=PATTERN_MINIMAL,
    interaction_style=INTERACTION,
    illustration_style=ILLUST_LINE,
)
