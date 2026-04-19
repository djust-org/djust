"""Monokai — The legendary Sublime Text palette, vibrant on warm dark."""

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
# Colors from Monokai:
# - Background #272822 (H=70 S=8 L=15)
# - Foreground #f8f8f2 (H=60 S=30 L=96)
# - Pink #f92672, Green #a6e22e, Yellow #e6db74
# - Cyan #66d9ef, Purple #ae81ff, Orange #fd971f

LIGHT = ThemeTokens(
    background=ColorScale(60, 15, 98),  # Warm off-white
    foreground=ColorScale(70, 8, 15),  # Warm near-black
    card=ColorScale(0, 0, 100),
    card_foreground=ColorScale(70, 8, 15),
    popover=ColorScale(0, 0, 100),
    popover_foreground=ColorScale(70, 8, 15),
    primary=ColorScale(338, 95, 56),  # Pink #f92672
    primary_foreground=ColorScale(0, 0, 100),
    secondary=ColorScale(60, 12, 94),
    secondary_foreground=ColorScale(70, 8, 15),
    muted=ColorScale(55, 10, 89),
    muted_foreground=ColorScale(50, 11, 41),
    accent=ColorScale(60, 12, 94),  # Subtle warm hover
    accent_foreground=ColorScale(70, 8, 15),
    destructive=ColorScale(338, 95, 56),  # Pink same as primary
    destructive_foreground=ColorScale(0, 0, 100),
    success=ColorScale(80, 76, 53),  # Green #a6e22e
    success_foreground=ColorScale(70, 8, 15),
    warning=ColorScale(54, 70, 68),  # Yellow #e6db74
    warning_foreground=ColorScale(70, 8, 15),
    info=ColorScale(190, 81, 67),  # Cyan #66d9ef
    info_foreground=ColorScale(70, 8, 15),
    link=ColorScale(261, 100, 75),  # Purple #ae81ff
    link_hover=ColorScale(261, 100, 65),
    code=ColorScale(70, 8, 15),  # Dark code bg
    code_foreground=ColorScale(80, 76, 60),  # Green code text
    selection=ColorScale(338, 80, 92),  # Pale pink selection
    selection_foreground=ColorScale(70, 8, 15),
    brand=ColorScale(80, 76, 53),  # Green #a6e22e
    brand_foreground=ColorScale(70, 8, 15),
    border=ColorScale(55, 10, 87),
    input=ColorScale(55, 10, 92),
    ring=ColorScale(338, 95, 56),
    surface_1=ColorScale(60, 15, 98),
    surface_2=ColorScale(60, 12, 95),
    surface_3=ColorScale(55, 10, 92),
)

DARK = ThemeTokens(
    background=ColorScale(70, 8, 15),  # #272822
    foreground=ColorScale(60, 30, 96),  # #f8f8f2
    card=ColorScale(70, 8, 19),
    card_foreground=ColorScale(60, 30, 96),
    popover=ColorScale(70, 8, 19),
    popover_foreground=ColorScale(60, 30, 96),
    primary=ColorScale(338, 95, 56),  # Pink #f92672
    primary_foreground=ColorScale(0, 0, 100),
    secondary=ColorScale(70, 8, 22),
    secondary_foreground=ColorScale(60, 30, 96),
    muted=ColorScale(70, 6, 25),
    muted_foreground=ColorScale(50, 11, 41),
    accent=ColorScale(70, 8, 22),  # Subtle dark hover
    accent_foreground=ColorScale(60, 30, 96),
    destructive=ColorScale(338, 95, 56),  # Pink
    destructive_foreground=ColorScale(0, 0, 100),
    success=ColorScale(80, 76, 53),  # Green #a6e22e
    success_foreground=ColorScale(70, 8, 15),
    warning=ColorScale(54, 70, 68),  # Yellow
    warning_foreground=ColorScale(70, 8, 15),
    info=ColorScale(190, 81, 67),  # Cyan #66d9ef
    info_foreground=ColorScale(70, 8, 15),
    link=ColorScale(261, 100, 75),  # Purple #ae81ff
    link_hover=ColorScale(261, 100, 82),
    code=ColorScale(70, 8, 10),  # Very dark code bg
    code_foreground=ColorScale(80, 76, 60),  # Green code text
    selection=ColorScale(338, 60, 25),  # Dark pink selection
    selection_foreground=ColorScale(60, 30, 96),
    brand=ColorScale(80, 76, 53),  # Green #a6e22e
    brand_foreground=ColorScale(70, 8, 15),
    border=ColorScale(70, 6, 26),
    input=ColorScale(70, 6, 26),
    ring=ColorScale(338, 95, 56),
    surface_1=ColorScale(70, 8, 11),
    surface_2=ColorScale(70, 8, 15),
    surface_3=ColorScale(70, 8, 20),
)

PRESET = ThemePreset(
    name="monokai",
    display_name="Monokai",
    description="The legendary Sublime Text palette, vibrant on warm dark",
    light=LIGHT,
    dark=DARK,
    radius=0.375,
    default_mode="dark",
)


# =============================================================================
# Design System
# =============================================================================

TYPOGRAPHY = TypographyStyle(
    name="monokai",
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
    name="monokai",
    space_unit="1rem",
    space_scale=1.5,
    border_radius_sm="4px",
    border_radius_md="6px",
    border_radius_lg="8px",
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
    name="monokai",
    shadow_sm="0 1px 3px rgba(0, 0, 0, 0.12)",
    shadow_md="0 4px 8px rgba(0, 0, 0, 0.15)",
    shadow_lg="0 8px 20px rgba(0, 0, 0, 0.18)",
    border_width="1px",
    border_style="solid",
    surface_treatment="flat",
    backdrop_blur="0px",
    noise_opacity=0.0,
)

ICON = IconStyle(
    name="monokai",
    style="outlined",
    weight="regular",
    size_scale=1.0,
    stroke_width="1.5",
    corner_rounding="2px",
)

ANIMATION = AnimationStyle(
    name="monokai",
    entrance_effect="fade",
    exit_effect="fade",
    hover_effect="lift",
    hover_scale=1.0,
    hover_translate_y="-1px",
    click_effect="pulse",
    loading_style="spinner",
    transition_style="smooth",
    duration_fast="0.1s",
    duration_normal="0.15s",
    duration_slow="0.25s",
    easing="cubic-bezier(0.4, 0, 0.2, 1)",
)

INTERACTION = InteractionStyle(
    name="monokai",
    button_hover="lift",
    link_hover="color",
    card_hover="shadow",
    focus_style="ring",
    focus_ring_width="2px",
)

DESIGN_SYSTEM = DesignSystem(
    name="monokai",
    display_name="Monokai",
    description="Warm minimalist dev — vibrant accents on warm dark surfaces",
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
    name="monokai",
    display_name="Monokai",
    description="The legendary Sublime Text palette, vibrant on warm dark",
    category="developer",
    design_theme="monokai",
    color_preset="monokai",
    icon_style=ICON,
    animation_style=ANIMATION,
    pattern_style=PATTERN_MINIMAL,
    interaction_style=INTERACTION,
    illustration_style=ILLUST_LINE,
)
