"""Nebula -- Deep space — dark slate & violet."""

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
    ILLUST_3D,
    PATTERN_GLASS,
)

# --- Color Preset ---

LIGHT = ThemeTokens(
    background=ColorScale(239, 50, 98),
    foreground=ColorScale(218, 47, 15),
    card=ColorScale(239, 45, 95),
    card_foreground=ColorScale(218, 47, 15),
    popover=ColorScale(239, 45, 95),
    popover_foreground=ColorScale(218, 47, 15),
    primary=ColorScale(239, 84, 67),
    primary_foreground=ColorScale(0, 0, 100),
    secondary=ColorScale(239, 40, 90),
    secondary_foreground=ColorScale(218, 47, 20),
    muted=ColorScale(239, 40, 92),
    muted_foreground=ColorScale(218, 30, 45),
    accent=ColorScale(239, 40, 90),
    accent_foreground=ColorScale(218, 47, 20),
    destructive=ColorScale(0, 84, 60),
    destructive_foreground=ColorScale(0, 0, 100),
    success=ColorScale(142, 71, 45),
    success_foreground=ColorScale(0, 0, 100),
    warning=ColorScale(38, 92, 50),
    warning_foreground=ColorScale(0, 0, 10),
    info=ColorScale(239, 84, 67),
    info_foreground=ColorScale(0, 0, 100),
    link=ColorScale(239, 84, 62),
    link_hover=ColorScale(239, 84, 52),
    code=ColorScale(239, 45, 93),
    code_foreground=ColorScale(218, 47, 15),
    selection=ColorScale(239, 84, 67),
    selection_foreground=ColorScale(0, 0, 100),
    brand=ColorScale(239, 84, 67),
    brand_foreground=ColorScale(0, 0, 100),
    border=ColorScale(239, 40, 85),
    input=ColorScale(0, 0, 100),
    ring=ColorScale(239, 84, 67),
    surface_1=ColorScale(239, 20, 98),
    surface_2=ColorScale(239, 15, 96),
    surface_3=ColorScale(239, 12, 93),
)

DARK = ThemeTokens(
    background=ColorScale(218, 47, 11),
    foreground=ColorScale(239, 50, 95),
    card=ColorScale(220, 45, 15),
    card_foreground=ColorScale(239, 50, 95),
    popover=ColorScale(220, 45, 15),
    popover_foreground=ColorScale(239, 50, 95),
    primary=ColorScale(239, 84, 67),
    primary_foreground=ColorScale(0, 0, 100),
    secondary=ColorScale(218, 45, 18),
    secondary_foreground=ColorScale(239, 50, 95),
    muted=ColorScale(218, 45, 18),
    muted_foreground=ColorScale(239, 30, 70),
    accent=ColorScale(218, 45, 18),
    accent_foreground=ColorScale(239, 84, 80),
    destructive=ColorScale(0, 84, 60),
    destructive_foreground=ColorScale(0, 0, 100),
    success=ColorScale(142, 71, 45),
    success_foreground=ColorScale(0, 0, 100),
    warning=ColorScale(38, 92, 50),
    warning_foreground=ColorScale(0, 0, 10),
    info=ColorScale(239, 84, 67),
    info_foreground=ColorScale(0, 0, 100),
    link=ColorScale(239, 84, 67),
    link_hover=ColorScale(239, 84, 77),
    code=ColorScale(218, 45, 9),
    code_foreground=ColorScale(239, 50, 95),
    selection=ColorScale(239, 84, 67),
    selection_foreground=ColorScale(0, 0, 100),
    brand=ColorScale(239, 84, 67),
    brand_foreground=ColorScale(0, 0, 100),
    border=ColorScale(218, 45, 18),
    input=ColorScale(220, 45, 15),
    ring=ColorScale(239, 84, 67),
    surface_1=ColorScale(218, 25, 4),
    surface_2=ColorScale(218, 20, 7),
    surface_3=ColorScale(218, 15, 11),
)

PRESET = ThemePreset(
    name="nebula",
    display_name="Nebula",
    description="Deep space — dark slate & violet",
    light=LIGHT,
    dark=DARK,
    radius=0.75,
)


# =============================================================================
# Design System
# =============================================================================

TYPOGRAPHY = TypographyStyle(
    name="nebula",
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
    name="nebula",
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
    name="nebula",
    shadow_sm="0 2px 8px rgba(130, 80, 220, 0.12)",
    shadow_md="0 6px 16px rgba(130, 80, 220, 0.18)",
    shadow_lg="0 12px 32px rgba(130, 80, 220, 0.24), 0 0 20px rgba(130, 80, 220, 0.08)",
    border_width="1px",
    border_style="solid",
    surface_treatment="glass",
    backdrop_blur="10px",
    noise_opacity=0.0,
)

ICON = IconStyle(
    name="nebula",
    style="outlined",
    weight="regular",
    size_scale=1.0,
    stroke_width="1.5",
    corner_rounding="2px",
)

ANIMATION = AnimationStyle(
    name="nebula",
    entrance_effect="fade",
    exit_effect="fade",
    hover_effect="glow",
    hover_scale=1.0,
    hover_translate_y="-1px",
    click_effect="pulse",
    loading_style="pulse",
    transition_style="smooth",
    duration_fast="0.15s",
    duration_normal="0.25s",
    duration_slow="0.4s",
    easing="cubic-bezier(0.4, 0, 0.2, 1)",
)

INTERACTION = InteractionStyle(
    name="nebula",
    button_hover="glow",
    link_hover="color",
    card_hover="shadow",
    focus_style="ring",
    focus_ring_width="2px",
)

DESIGN_SYSTEM = DesignSystem(
    name="nebula",
    display_name="Nebula",
    description="Space cosmic -- purple-tinted glow, glass surfaces, dark-first",
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
    name="nebula",
    display_name="Nebula",
    description="Space cosmic -- purple-tinted glow, glass surfaces, dark-first",
    category="dark",
    design_theme="nebula",
    color_preset="nebula",
    icon_style=ICON,
    animation_style=ANIMATION,
    pattern_style=PATTERN_GLASS,
    interaction_style=INTERACTION,
    illustration_style=ILLUST_3D,
)
