"""Tokyo Night -- Vibrant yet soft neon colors inspired by Tokyo at night."""

from ._base import (
    AnimationStyle,
    ColorScale,
    DesignSystem,
    IconStyle,
    InteractionStyle,
    LayoutStyle,
    SurfaceStyle,
    ThemePack,
    ThemePreset,
    ThemeTokens,
    TypographyStyle,
    ILLUST_LINE,
    PATTERN_MINIMAL,
)

# --- Color Preset ---

LIGHT = ThemeTokens(
    background=ColorScale(0, 0, 100),
    foreground=ColorScale(224, 18, 20),
    card=ColorScale(219, 28, 97),
    card_foreground=ColorScale(224, 18, 20),
    popover=ColorScale(219, 28, 97),
    popover_foreground=ColorScale(224, 18, 20),
    primary=ColorScale(217, 89, 72),
    primary_foreground=ColorScale(0, 0, 100),
    secondary=ColorScale(219, 20, 92),
    secondary_foreground=ColorScale(224, 18, 20),
    muted=ColorScale(219, 20, 92),
    muted_foreground=ColorScale(224, 18, 45),
    accent=ColorScale(219, 20, 92),
    accent_foreground=ColorScale(267, 85, 78),
    destructive=ColorScale(355, 89, 72),
    destructive_foreground=ColorScale(0, 0, 100),
    success=ColorScale(89, 59, 64),
    success_foreground=ColorScale(0, 0, 10),
    warning=ColorScale(41, 70, 65),
    warning_foreground=ColorScale(0, 0, 10),
    info=ColorScale(189, 100, 74),
    info_foreground=ColorScale(0, 0, 10),
    link=ColorScale(267, 85, 78),
    link_hover=ColorScale(267, 85, 68),
    code=ColorScale(219, 28, 97),
    code_foreground=ColorScale(224, 18, 20),
    selection=ColorScale(217, 89, 82),
    selection_foreground=ColorScale(224, 18, 20),
    brand=ColorScale(190, 80, 55),
    brand_foreground=ColorScale(0, 0, 10),
    border=ColorScale(219, 20, 92),
    input=ColorScale(0, 0, 100),
    ring=ColorScale(217, 89, 72),
    surface_1=ColorScale(234, 18, 98),
    surface_2=ColorScale(234, 14, 96),
    surface_3=ColorScale(234, 10, 93),
)

DARK = ThemeTokens(
    background=ColorScale(234, 16, 13),
    foreground=ColorScale(219, 72, 85),
    card=ColorScale(233, 15, 18),
    card_foreground=ColorScale(219, 72, 85),
    popover=ColorScale(233, 15, 18),
    popover_foreground=ColorScale(219, 72, 85),
    primary=ColorScale(217, 89, 72),
    primary_foreground=ColorScale(0, 0, 100),
    secondary=ColorScale(234, 13, 20),
    secondary_foreground=ColorScale(219, 72, 85),
    muted=ColorScale(234, 13, 20),
    muted_foreground=ColorScale(225, 12, 68),
    accent=ColorScale(234, 13, 20),
    accent_foreground=ColorScale(267, 85, 78),
    destructive=ColorScale(355, 89, 72),
    destructive_foreground=ColorScale(0, 0, 100),
    success=ColorScale(89, 59, 64),
    success_foreground=ColorScale(0, 0, 10),
    warning=ColorScale(41, 70, 65),
    warning_foreground=ColorScale(0, 0, 10),
    info=ColorScale(189, 100, 74),
    info_foreground=ColorScale(0, 0, 10),
    link=ColorScale(267, 85, 78),
    link_hover=ColorScale(267, 85, 88),
    code=ColorScale(233, 17, 10),
    code_foreground=ColorScale(219, 72, 85),
    selection=ColorScale(267, 85, 78),
    selection_foreground=ColorScale(0, 0, 100),
    brand=ColorScale(190, 80, 70),
    brand_foreground=ColorScale(0, 0, 10),
    border=ColorScale(234, 13, 20),
    input=ColorScale(233, 15, 18),
    ring=ColorScale(217, 89, 72),
    surface_1=ColorScale(234, 18, 5),
    surface_2=ColorScale(234, 15, 8),
    surface_3=ColorScale(234, 12, 12),
)

PRESET = ThemePreset(
    name="tokyo_night",
    display_name="Tokyo Night",
    description="Vibrant yet soft neon colors inspired by Tokyo at night",
    light=LIGHT,
    dark=DARK,
)


# =============================================================================
# Design System
# =============================================================================

TYPOGRAPHY = TypographyStyle(
    name="tokyo_night",
    heading_font='"Inter", system-ui, sans-serif',
    body_font='"Inter", system-ui, sans-serif',
    base_size="16px",
    heading_scale=1.2,
    line_height="1.5",
    body_line_height="1.6",
    heading_weight="500",
    section_heading_weight="500",
    body_weight="400",
    letter_spacing="normal",
    prose_max_width="44rem",
    badge_radius="9999px",
)

LAYOUT = LayoutStyle(
    name="tokyo_night",
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
    hero_padding_bottom="4rem",
    hero_line_height="1.15",
    hero_max_width="50rem",
)

SURFACE = SurfaceStyle(
    name="tokyo_night",
    shadow_sm="0 1px 2px rgba(0, 0, 0, 0.06)",
    shadow_md="0 4px 6px -1px rgba(0, 0, 0, 0.08)",
    shadow_lg="0 8px 16px -2px rgba(0, 0, 0, 0.1)",
    border_width="1px",
    border_style="solid",
    surface_treatment="flat",
    backdrop_blur="0px",
    noise_opacity=0.0,
)

ICON = IconStyle(
    name="tokyo_night",
    style="outlined",
    weight="regular",
    size_scale=1.0,
    stroke_width="1.5",
    corner_rounding="2px",
)

ANIMATION = AnimationStyle(
    name="tokyo_night",
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
    name="tokyo_night",
    button_hover="lift",
    link_hover="color",
    card_hover="shadow",
    focus_style="ring",
    focus_ring_width="2px",
)

DESIGN_SYSTEM = DesignSystem(
    name="tokyo_night",
    display_name="Tokyo Night",
    description="Vibrant neon -- elegant dev classic",
    category="elegant",
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
    name="tokyo_night",
    display_name="Tokyo Night",
    description="Vibrant neon -- elegant dev classic",
    category="elegant",
    design_theme="tokyo_night",
    color_preset="tokyo_night",
    icon_style=ICON,
    animation_style=ANIMATION,
    pattern_style=PATTERN_MINIMAL,
    interaction_style=INTERACTION,
    illustration_style=ILLUST_LINE,
)
