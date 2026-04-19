"""Ayu Mirage — Warm dark with golden accent, the designer's choice."""

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
# Colors from Ayu Mirage:
# - Background #1f2430 (H=222 S=22 L=15)
# - Foreground #cbccc6 (H=70 S=6 L=79)
# - Orange #ffad66, Gold #ffcc66, Green #bae67e
# - Blue #73d0ff, Red #ff3333

LIGHT = ThemeTokens(
    background=ColorScale(40, 10, 99),  # Very warm light
    foreground=ColorScale(220, 15, 20),  # Warm dark fg
    card=ColorScale(0, 0, 100),
    card_foreground=ColorScale(220, 15, 20),
    popover=ColorScale(0, 0, 100),
    popover_foreground=ColorScale(220, 15, 20),
    primary=ColorScale(25, 100, 63),  # Orange accent
    primary_foreground=ColorScale(0, 0, 100),
    secondary=ColorScale(40, 10, 96),
    secondary_foreground=ColorScale(220, 15, 20),
    muted=ColorScale(40, 8, 90),
    muted_foreground=ColorScale(220, 10, 50),
    accent=ColorScale(40, 10, 96),  # Subtle warm hover
    accent_foreground=ColorScale(220, 15, 20),
    destructive=ColorScale(0, 100, 60),  # Red #ff3333
    destructive_foreground=ColorScale(0, 0, 100),
    success=ColorScale(80, 65, 57),  # Green #bae67e
    success_foreground=ColorScale(220, 15, 20),
    warning=ColorScale(40, 100, 70),  # Gold #ffcc66
    warning_foreground=ColorScale(220, 15, 20),
    info=ColorScale(205, 78, 56),  # Blue #73d0ff
    info_foreground=ColorScale(0, 0, 100),
    link=ColorScale(205, 78, 56),  # Blue
    link_hover=ColorScale(205, 78, 45),
    code=ColorScale(222, 22, 15),  # Dark code bg
    code_foreground=ColorScale(40, 100, 70),  # Gold code text
    selection=ColorScale(25, 80, 92),  # Pale orange selection
    selection_foreground=ColorScale(220, 15, 20),
    brand=ColorScale(40, 100, 70),  # Gold #ffcc66
    brand_foreground=ColorScale(220, 15, 20),
    border=ColorScale(40, 8, 88),
    input=ColorScale(40, 8, 93),
    ring=ColorScale(25, 100, 63),
    surface_1=ColorScale(40, 10, 99),
    surface_2=ColorScale(40, 10, 96),
    surface_3=ColorScale(40, 8, 93),
)

DARK = ThemeTokens(
    background=ColorScale(222, 22, 15),  # #1f2430
    foreground=ColorScale(70, 6, 79),  # #cbccc6
    card=ColorScale(222, 20, 19),
    card_foreground=ColorScale(70, 6, 79),
    popover=ColorScale(222, 20, 19),
    popover_foreground=ColorScale(70, 6, 79),
    primary=ColorScale(25, 100, 63),  # Orange accent
    primary_foreground=ColorScale(222, 22, 15),
    secondary=ColorScale(222, 18, 22),
    secondary_foreground=ColorScale(70, 6, 79),
    muted=ColorScale(222, 15, 25),
    muted_foreground=ColorScale(220, 10, 50),
    accent=ColorScale(222, 18, 22),  # Subtle dark hover
    accent_foreground=ColorScale(70, 6, 79),
    destructive=ColorScale(0, 100, 60),  # Red
    destructive_foreground=ColorScale(0, 0, 100),
    success=ColorScale(80, 65, 57),  # Green
    success_foreground=ColorScale(222, 22, 15),
    warning=ColorScale(40, 100, 70),  # Gold
    warning_foreground=ColorScale(222, 22, 15),
    info=ColorScale(205, 78, 56),  # Blue
    info_foreground=ColorScale(222, 22, 15),
    link=ColorScale(205, 78, 66),  # Blue brightened
    link_hover=ColorScale(205, 78, 75),
    code=ColorScale(222, 22, 10),  # Very dark code bg
    code_foreground=ColorScale(40, 100, 70),  # Gold code text
    selection=ColorScale(25, 60, 22),  # Dark orange selection
    selection_foreground=ColorScale(70, 6, 85),
    brand=ColorScale(40, 100, 70),  # Gold #ffcc66
    brand_foreground=ColorScale(222, 22, 15),
    border=ColorScale(222, 15, 26),
    input=ColorScale(222, 15, 26),
    ring=ColorScale(25, 100, 63),
    surface_1=ColorScale(222, 22, 11),
    surface_2=ColorScale(222, 22, 15),
    surface_3=ColorScale(222, 20, 19),
)

PRESET = ThemePreset(
    name="ayu",
    display_name="Ayu Mirage",
    description="Warm dark with golden accent, the designer's choice",
    light=LIGHT,
    dark=DARK,
    radius=0.5,
    default_mode="dark",
)


# =============================================================================
# Design System
# =============================================================================

TYPOGRAPHY = TypographyStyle(
    name="ayu",
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
    name="ayu",
    space_unit="1rem",
    space_scale=1.5,
    border_radius_sm="6px",
    border_radius_md="8px",
    border_radius_lg="12px",
    button_shape="rounded",
    card_shape="rounded",
    input_shape="rounded",
    container_width="1100px",
    grid_gap="1.5rem",
    section_spacing="4rem",
    hero_padding_top="7rem",
    hero_padding_bottom="5rem",
    hero_line_height="1.15",
    hero_max_width="52rem",
)

SURFACE = SurfaceStyle(
    name="ayu",
    shadow_sm="0 1px 3px rgba(0, 0, 0, 0.1)",
    shadow_md="0 4px 8px rgba(0, 0, 0, 0.12)",
    shadow_lg="0 8px 20px rgba(0, 0, 0, 0.15)",
    border_width="1px",
    border_style="solid",
    surface_treatment="flat",
    backdrop_blur="0px",
    noise_opacity=0.0,
)

ICON = IconStyle(
    name="ayu",
    style="outlined",
    weight="regular",
    size_scale=1.0,
    stroke_width="1.5",
    corner_rounding="2px",
)

ANIMATION = AnimationStyle(
    name="ayu",
    entrance_effect="fade",
    exit_effect="fade",
    hover_effect="lift",
    hover_scale=1.0,
    hover_translate_y="-1px",
    click_effect="pulse",
    loading_style="spinner",
    transition_style="smooth",
    duration_fast="0.12s",
    duration_normal="0.2s",
    duration_slow="0.3s",
    easing="cubic-bezier(0.4, 0, 0.2, 1)",
)

INTERACTION = InteractionStyle(
    name="ayu",
    button_hover="lift",
    link_hover="color",
    card_hover="shadow",
    focus_style="ring",
    focus_ring_width="2px",
)

DESIGN_SYSTEM = DesignSystem(
    name="ayu",
    display_name="Ayu Mirage",
    description="Designer's dev theme — softer radius, golden warmth, clean surfaces",
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
    name="ayu",
    display_name="Ayu Mirage",
    description="Warm dark with golden accent, the designer's choice",
    category="developer",
    design_theme="ayu",
    color_preset="ayu",
    icon_style=ICON,
    animation_style=ANIMATION,
    pattern_style=PATTERN_MINIMAL,
    interaction_style=INTERACTION,
    illustration_style=ILLUST_LINE,
)
