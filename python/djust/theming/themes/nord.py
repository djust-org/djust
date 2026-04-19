"""Nord -- Cool, arctic-inspired blue/gray pastels."""

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
    foreground=ColorScale(220, 16, 22),
    card=ColorScale(219, 28, 97),
    card_foreground=ColorScale(220, 16, 22),
    popover=ColorScale(219, 28, 97),
    popover_foreground=ColorScale(220, 16, 22),
    primary=ColorScale(193, 43, 67),
    primary_foreground=ColorScale(0, 0, 100),
    secondary=ColorScale(219, 20, 92),
    secondary_foreground=ColorScale(220, 16, 22),
    muted=ColorScale(219, 20, 92),
    muted_foreground=ColorScale(220, 16, 45),
    accent=ColorScale(219, 20, 92),
    accent_foreground=ColorScale(210, 34, 63),
    destructive=ColorScale(354, 42, 56),
    destructive_foreground=ColorScale(0, 0, 100),
    success=ColorScale(92, 28, 65),
    success_foreground=ColorScale(0, 0, 10),
    warning=ColorScale(40, 71, 73),
    warning_foreground=ColorScale(0, 0, 10),
    info=ColorScale(210, 34, 63),
    info_foreground=ColorScale(0, 0, 100),
    link=ColorScale(213, 32, 52),
    link_hover=ColorScale(213, 32, 42),
    code=ColorScale(219, 28, 97),
    code_foreground=ColorScale(220, 16, 22),
    selection=ColorScale(193, 43, 77),
    selection_foreground=ColorScale(220, 16, 22),
    brand=ColorScale(193, 43, 67),
    brand_foreground=ColorScale(0, 0, 10),
    border=ColorScale(219, 20, 92),
    input=ColorScale(0, 0, 100),
    ring=ColorScale(193, 43, 67),
    surface_1=ColorScale(220, 18, 98),
    surface_2=ColorScale(220, 14, 96),
    surface_3=ColorScale(220, 10, 93),
)

DARK = ThemeTokens(
    background=ColorScale(220, 16, 22),
    foreground=ColorScale(219, 28, 88),
    card=ColorScale(220, 16, 28),
    card_foreground=ColorScale(219, 28, 88),
    popover=ColorScale(220, 16, 28),
    popover_foreground=ColorScale(219, 28, 88),
    primary=ColorScale(193, 43, 67),
    primary_foreground=ColorScale(0, 0, 100),
    secondary=ColorScale(220, 16, 32),
    secondary_foreground=ColorScale(219, 28, 88),
    muted=ColorScale(220, 16, 32),
    muted_foreground=ColorScale(219, 14, 62),
    accent=ColorScale(220, 16, 32),
    accent_foreground=ColorScale(210, 34, 63),
    destructive=ColorScale(354, 42, 56),
    destructive_foreground=ColorScale(0, 0, 100),
    success=ColorScale(92, 28, 65),
    success_foreground=ColorScale(0, 0, 10),
    warning=ColorScale(40, 71, 73),
    warning_foreground=ColorScale(0, 0, 10),
    info=ColorScale(210, 34, 63),
    info_foreground=ColorScale(0, 0, 100),
    link=ColorScale(213, 32, 52),
    link_hover=ColorScale(213, 32, 62),
    code=ColorScale(220, 17, 18),
    code_foreground=ColorScale(219, 28, 88),
    selection=ColorScale(210, 34, 63),
    selection_foreground=ColorScale(0, 0, 100),
    brand=ColorScale(193, 43, 67),
    brand_foreground=ColorScale(0, 0, 10),
    border=ColorScale(220, 16, 32),
    input=ColorScale(220, 16, 28),
    ring=ColorScale(193, 43, 67),
    surface_1=ColorScale(220, 18, 10),
    surface_2=ColorScale(220, 15, 14),
    surface_3=ColorScale(220, 12, 18),
)

PRESET = ThemePreset(
    name="nord",
    display_name="Nord",
    description="Cool, arctic-inspired blue/gray pastels",
    light=LIGHT,
    dark=DARK,
)


# =============================================================================
# Design System
# =============================================================================

TYPOGRAPHY = TypographyStyle(
    name="nord",
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
    name="nord",
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
    name="nord",
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
    name="nord",
    style="outlined",
    weight="regular",
    size_scale=1.0,
    stroke_width="1.5",
    corner_rounding="2px",
)

ANIMATION = AnimationStyle(
    name="nord",
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
    name="nord",
    button_hover="lift",
    link_hover="color",
    card_hover="shadow",
    focus_style="ring",
    focus_ring_width="2px",
)

DESIGN_SYSTEM = DesignSystem(
    name="nord",
    display_name="Nord",
    description="Cool arctic -- elegant dev classic",
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
    name="nord",
    display_name="Nord",
    description="Cool arctic -- elegant dev classic",
    category="elegant",
    design_theme="nord",
    color_preset="nord",
    icon_style=ICON,
    animation_style=ANIMATION,
    pattern_style=PATTERN_MINIMAL,
    interaction_style=INTERACTION,
    illustration_style=ILLUST_LINE,
)
