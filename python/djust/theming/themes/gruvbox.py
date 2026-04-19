"""Gruvbox -- Retro-warm earthy palette beloved by vim users."""

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
    background=ColorScale(44, 87, 94),
    foreground=ColorScale(0, 0, 16),
    card=ColorScale(47, 80, 90),
    card_foreground=ColorScale(0, 0, 16),
    popover=ColorScale(47, 80, 90),
    popover_foreground=ColorScale(0, 0, 16),
    primary=ColorScale(27, 99, 55),
    primary_foreground=ColorScale(0, 0, 100),
    secondary=ColorScale(44, 70, 85),
    secondary_foreground=ColorScale(0, 0, 16),
    muted=ColorScale(44, 70, 85),
    muted_foreground=ColorScale(24, 12, 45),
    accent=ColorScale(175, 42, 52),
    accent_foreground=ColorScale(0, 0, 100),
    destructive=ColorScale(6, 96, 59),
    destructive_foreground=ColorScale(0, 0, 100),
    success=ColorScale(106, 33, 51),
    success_foreground=ColorScale(0, 0, 100),
    warning=ColorScale(40, 73, 49),
    warning_foreground=ColorScale(0, 0, 100),
    info=ColorScale(175, 42, 52),
    info_foreground=ColorScale(0, 0, 100),
    link=ColorScale(27, 99, 55),
    link_hover=ColorScale(27, 99, 45),
    code=ColorScale(44, 70, 85),
    code_foreground=ColorScale(6, 96, 59),
    selection=ColorScale(27, 80, 85),
    selection_foreground=ColorScale(27, 10, 15),
    brand=ColorScale(27, 99, 55),
    brand_foreground=ColorScale(0, 0, 10),
    border=ColorScale(40, 30, 75),
    input=ColorScale(40, 30, 75),
    ring=ColorScale(27, 99, 55),
    surface_1=ColorScale(27, 5, 96),
    surface_2=ColorScale(27, 5, 93),
    surface_3=ColorScale(27, 5, 90),
)

DARK = ThemeTokens(
    background=ColorScale(0, 0, 16),
    foreground=ColorScale(42, 46, 82),
    card=ColorScale(20, 5, 20),
    card_foreground=ColorScale(42, 46, 82),
    popover=ColorScale(20, 5, 20),
    popover_foreground=ColorScale(42, 46, 82),
    primary=ColorScale(27, 99, 55),
    primary_foreground=ColorScale(0, 0, 16),
    secondary=ColorScale(20, 5, 24),
    secondary_foreground=ColorScale(42, 46, 82),
    muted=ColorScale(20, 5, 24),
    muted_foreground=ColorScale(30, 12, 55),
    accent=ColorScale(175, 42, 63),
    accent_foreground=ColorScale(0, 0, 16),
    destructive=ColorScale(6, 96, 59),
    destructive_foreground=ColorScale(0, 0, 100),
    success=ColorScale(106, 33, 62),
    success_foreground=ColorScale(0, 0, 16),
    warning=ColorScale(40, 73, 49),
    warning_foreground=ColorScale(0, 0, 16),
    info=ColorScale(175, 42, 63),
    info_foreground=ColorScale(0, 0, 16),
    link=ColorScale(27, 99, 55),
    link_hover=ColorScale(40, 73, 60),
    code=ColorScale(20, 5, 22),
    code_foreground=ColorScale(106, 33, 62),
    selection=ColorScale(27, 80, 25),
    selection_foreground=ColorScale(27, 10, 90),
    brand=ColorScale(27, 99, 55),
    brand_foreground=ColorScale(0, 0, 10),
    border=ColorScale(20, 5, 28),
    input=ColorScale(20, 5, 28),
    ring=ColorScale(27, 99, 55),
    surface_1=ColorScale(27, 5, 6),
    surface_2=ColorScale(27, 5, 10),
    surface_3=ColorScale(27, 5, 14),
)

PRESET = ThemePreset(
    name="gruvbox",
    display_name="Gruvbox",
    description="Retro-warm earthy palette beloved by vim users",
    light=LIGHT,
    dark=DARK,
)


# =============================================================================
# Design System
# =============================================================================

TYPOGRAPHY = TypographyStyle(
    name="gruvbox",
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
    name="gruvbox",
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
    name="gruvbox",
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
    name="gruvbox",
    style="outlined",
    weight="regular",
    size_scale=1.0,
    stroke_width="1.5",
    corner_rounding="2px",
)

ANIMATION = AnimationStyle(
    name="gruvbox",
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
    name="gruvbox",
    button_hover="lift",
    link_hover="color",
    card_hover="shadow",
    focus_style="ring",
    focus_ring_width="2px",
)

DESIGN_SYSTEM = DesignSystem(
    name="gruvbox",
    display_name="Gruvbox",
    description="Retro-warm earthy -- elegant dev classic",
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
    name="gruvbox",
    display_name="Gruvbox",
    description="Retro-warm earthy -- elegant dev classic",
    category="elegant",
    design_theme="gruvbox",
    color_preset="gruvbox",
    icon_style=ICON,
    animation_style=ANIMATION,
    pattern_style=PATTERN_MINIMAL,
    interaction_style=INTERACTION,
    illustration_style=ILLUST_LINE,
)
