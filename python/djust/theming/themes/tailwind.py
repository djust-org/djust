"""Tailwind — Cool slate and sky blue, the modern utility aesthetic."""

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
# Colors from Tailwind CSS design system:
# - Sky 500 #0ea5e9 (primary), Slate 900 #0f172a (dark bg)
# - Slate 50 #f8fafc (light bg), Slate 400 #94a3b8 (muted)
# - Emerald 600 #059669, Amber 500 #f59e0b, Rose 500 #f43f5e

LIGHT = ThemeTokens(
    background=ColorScale(210, 40, 98),  # Slate 50
    foreground=ColorScale(222, 47, 11),  # Slate 900
    card=ColorScale(0, 0, 100),
    card_foreground=ColorScale(222, 47, 11),
    popover=ColorScale(0, 0, 100),
    popover_foreground=ColorScale(222, 47, 11),
    primary=ColorScale(199, 89, 48),  # Sky 500 #0ea5e9
    primary_foreground=ColorScale(0, 0, 100),
    secondary=ColorScale(210, 40, 96),
    secondary_foreground=ColorScale(222, 47, 11),
    muted=ColorScale(214, 32, 91),  # Slate 200
    muted_foreground=ColorScale(215, 20, 65),  # Slate 400
    accent=ColorScale(210, 40, 96),  # Subtle hover
    accent_foreground=ColorScale(222, 47, 11),
    destructive=ColorScale(0, 84, 60),  # Rose 500
    destructive_foreground=ColorScale(0, 0, 100),
    success=ColorScale(160, 84, 39),  # Emerald 600
    success_foreground=ColorScale(0, 0, 100),
    warning=ColorScale(38, 92, 50),  # Amber 500
    warning_foreground=ColorScale(222, 47, 11),
    info=ColorScale(199, 89, 48),  # Sky 500
    info_foreground=ColorScale(0, 0, 100),
    link=ColorScale(199, 89, 48),  # Sky 500
    link_hover=ColorScale(199, 89, 38),  # Sky 600
    code=ColorScale(222, 47, 11),  # Slate 900 code bg
    code_foreground=ColorScale(199, 89, 65),  # Sky code text
    selection=ColorScale(199, 80, 90),  # Pale sky selection
    selection_foreground=ColorScale(222, 47, 11),
    brand=ColorScale(199, 89, 48),  # Sky 500
    brand_foreground=ColorScale(0, 0, 100),
    border=ColorScale(214, 32, 91),  # Slate 200
    input=ColorScale(214, 32, 91),
    ring=ColorScale(199, 89, 48),
    surface_1=ColorScale(210, 40, 98),
    surface_2=ColorScale(210, 40, 96),
    surface_3=ColorScale(214, 32, 93),
)

DARK = ThemeTokens(
    background=ColorScale(222, 47, 11),  # Slate 900
    foreground=ColorScale(210, 40, 98),  # Slate 50
    card=ColorScale(217, 33, 17),  # Slate 800
    card_foreground=ColorScale(210, 40, 98),
    popover=ColorScale(217, 33, 17),
    popover_foreground=ColorScale(210, 40, 98),
    primary=ColorScale(199, 89, 48),  # Sky 500
    primary_foreground=ColorScale(0, 0, 100),
    secondary=ColorScale(217, 33, 20),
    secondary_foreground=ColorScale(210, 40, 98),
    muted=ColorScale(217, 25, 24),
    muted_foreground=ColorScale(215, 20, 65),  # Slate 400
    accent=ColorScale(217, 33, 20),  # Subtle dark hover
    accent_foreground=ColorScale(210, 40, 98),
    destructive=ColorScale(0, 84, 60),  # Rose 500
    destructive_foreground=ColorScale(0, 0, 100),
    success=ColorScale(160, 84, 39),  # Emerald 600
    success_foreground=ColorScale(0, 0, 100),
    warning=ColorScale(38, 92, 50),  # Amber 500
    warning_foreground=ColorScale(222, 47, 11),
    info=ColorScale(199, 89, 48),  # Sky 500
    info_foreground=ColorScale(222, 47, 11),
    link=ColorScale(199, 89, 58),  # Sky brightened
    link_hover=ColorScale(199, 89, 68),
    code=ColorScale(222, 47, 7),  # Very dark code bg
    code_foreground=ColorScale(199, 89, 65),  # Sky code text
    selection=ColorScale(199, 60, 20),  # Deep sky selection
    selection_foreground=ColorScale(210, 40, 95),
    brand=ColorScale(199, 89, 48),  # Sky 500
    brand_foreground=ColorScale(0, 0, 100),
    border=ColorScale(217, 25, 24),
    input=ColorScale(217, 25, 24),
    ring=ColorScale(199, 89, 48),
    surface_1=ColorScale(222, 47, 7),
    surface_2=ColorScale(222, 47, 11),
    surface_3=ColorScale(217, 33, 17),
)

PRESET = ThemePreset(
    name="tailwind",
    display_name="Tailwind",
    description="Cool slate and sky blue, the modern utility aesthetic",
    light=LIGHT,
    dark=DARK,
    radius=0.375,
)


# =============================================================================
# Design System
# =============================================================================

TYPOGRAPHY = TypographyStyle(
    name="tailwind",
    heading_font='"Inter", system-ui, sans-serif',
    body_font='"Inter", system-ui, sans-serif',
    base_size="16px",
    heading_scale=1.25,
    line_height="1.5",
    body_line_height="1.6",
    heading_weight="600",
    section_heading_weight="600",
    body_weight="400",
    letter_spacing="-0.01em",
    prose_max_width="42rem",
    badge_radius="9999px",
)

LAYOUT = LayoutStyle(
    name="tailwind",
    space_unit="1rem",
    space_scale=1.5,
    border_radius_sm="4px",
    border_radius_md="6px",
    border_radius_lg="8px",
    button_shape="rounded",
    card_shape="rounded",
    input_shape="rounded",
    container_width="1280px",
    grid_gap="1.5rem",
    section_spacing="5rem",
    hero_padding_top="8rem",
    hero_padding_bottom="5rem",
    hero_line_height="1.1",
    hero_max_width="56rem",
)

SURFACE = SurfaceStyle(
    name="tailwind",
    shadow_sm="0 1px 2px 0 rgba(0, 0, 0, 0.05)",
    shadow_md="0 4px 6px -1px rgba(0, 0, 0, 0.1), 0 2px 4px -2px rgba(0, 0, 0, 0.1)",
    shadow_lg="0 10px 15px -3px rgba(0, 0, 0, 0.1), 0 4px 6px -4px rgba(0, 0, 0, 0.1)",
    border_width="1px",
    border_style="solid",
    surface_treatment="flat",
    backdrop_blur="0px",
    noise_opacity=0.0,
)

ICON = IconStyle(
    name="tailwind",
    style="outlined",
    weight="regular",
    size_scale=1.0,
    stroke_width="1.5",
    corner_rounding="2px",
)

ANIMATION = AnimationStyle(
    name="tailwind",
    entrance_effect="fade",
    exit_effect="fade",
    hover_effect="lift",
    hover_scale=1.0,
    hover_translate_y="-1px",
    click_effect="none",
    loading_style="spinner",
    transition_style="smooth",
    duration_fast="0.1s",
    duration_normal="0.15s",
    duration_slow="0.25s",
    easing="cubic-bezier(0.4, 0, 0.2, 1)",
)

INTERACTION = InteractionStyle(
    name="tailwind",
    button_hover="lift",
    link_hover="color",
    card_hover="shadow",
    focus_style="ring",
    focus_ring_width="2px",
)

DESIGN_SYSTEM = DesignSystem(
    name="tailwind",
    display_name="Tailwind",
    description="Modern utility — semibold headings, subtle shadows, generous spacing",
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
    name="tailwind",
    display_name="Tailwind",
    description="Cool slate and sky blue, the modern utility aesthetic",
    category="professional",
    design_theme="tailwind",
    color_preset="tailwind",
    icon_style=ICON,
    animation_style=ANIMATION,
    pattern_style=PATTERN_MINIMAL,
    interaction_style=INTERACTION,
    illustration_style=ILLUST_LINE,
)
