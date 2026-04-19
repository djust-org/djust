"""Ocean -- Deep sea gradient depth — coastal sky to ocean floor."""

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
    ILLUST_HAND_DRAWN,
    PATTERN_NOISE,
)

# --- Color Preset ---

LIGHT = ThemeTokens(
    background=ColorScale(200, 25, 97),
    foreground=ColorScale(210, 40, 15),
    card=ColorScale(200, 22, 94),
    card_foreground=ColorScale(210, 40, 15),
    popover=ColorScale(200, 22, 94),
    popover_foreground=ColorScale(210, 40, 15),
    primary=ColorScale(200, 80, 45),
    primary_foreground=ColorScale(0, 0, 100),
    secondary=ColorScale(200, 18, 88),
    secondary_foreground=ColorScale(210, 40, 15),
    muted=ColorScale(200, 18, 88),
    muted_foreground=ColorScale(210, 20, 45),
    accent=ColorScale(180, 50, 45),
    accent_foreground=ColorScale(0, 0, 100),
    destructive=ColorScale(0, 70, 55),
    destructive_foreground=ColorScale(0, 0, 100),
    success=ColorScale(160, 60, 42),
    success_foreground=ColorScale(0, 0, 100),
    warning=ColorScale(40, 80, 55),
    warning_foreground=ColorScale(0, 0, 10),
    info=ColorScale(200, 80, 45),
    info_foreground=ColorScale(0, 0, 100),
    link=ColorScale(200, 80, 40),
    link_hover=ColorScale(200, 80, 32),
    code=ColorScale(200, 18, 91),
    code_foreground=ColorScale(200, 80, 35),
    selection=ColorScale(200, 80, 85),
    selection_foreground=ColorScale(200, 10, 15),
    brand=ColorScale(200, 80, 45),
    brand_foreground=ColorScale(0, 0, 100),
    border=ColorScale(200, 15, 80),
    input=ColorScale(200, 15, 80),
    ring=ColorScale(200, 80, 45),
    surface_1=ColorScale(200, 5, 96),
    surface_2=ColorScale(200, 5, 93),
    surface_3=ColorScale(200, 5, 90),
)

DARK = ThemeTokens(
    background=ColorScale(210, 60, 8),
    foreground=ColorScale(195, 30, 88),
    card=ColorScale(210, 50, 12),
    card_foreground=ColorScale(195, 30, 88),
    popover=ColorScale(210, 50, 12),
    popover_foreground=ColorScale(195, 30, 88),
    primary=ColorScale(200, 80, 55),
    primary_foreground=ColorScale(210, 60, 8),
    secondary=ColorScale(210, 50, 16),
    secondary_foreground=ColorScale(195, 30, 88),
    muted=ColorScale(210, 50, 16),
    muted_foreground=ColorScale(200, 20, 55),
    accent=ColorScale(180, 50, 50),
    accent_foreground=ColorScale(210, 60, 8),
    destructive=ColorScale(0, 70, 55),
    destructive_foreground=ColorScale(0, 0, 100),
    success=ColorScale(160, 60, 50),
    success_foreground=ColorScale(210, 60, 8),
    warning=ColorScale(40, 80, 60),
    warning_foreground=ColorScale(210, 60, 8),
    info=ColorScale(200, 80, 55),
    info_foreground=ColorScale(210, 60, 8),
    link=ColorScale(200, 80, 55),
    link_hover=ColorScale(180, 50, 65),
    code=ColorScale(210, 50, 14),
    code_foreground=ColorScale(180, 50, 60),
    selection=ColorScale(200, 80, 25),
    selection_foreground=ColorScale(200, 10, 90),
    brand=ColorScale(200, 80, 55),
    brand_foreground=ColorScale(210, 60, 8),
    border=ColorScale(210, 50, 20),
    input=ColorScale(210, 50, 20),
    ring=ColorScale(200, 80, 55),
    surface_1=ColorScale(200, 5, 6),
    surface_2=ColorScale(200, 5, 10),
    surface_3=ColorScale(200, 5, 14),
)

PRESET = ThemePreset(
    name="ocean",
    display_name="Ocean",
    description="Deep sea gradient depth — coastal sky to ocean floor",
    light=LIGHT,
    dark=DARK,
    default_mode="dark",
)


# =============================================================================
# Design System
# =============================================================================

TYPOGRAPHY = TypographyStyle(
    name="ocean_deep",
    heading_font="system-ui, sans-serif",
    body_font="system-ui, sans-serif",
    base_size="16px",
    heading_scale=1.25,
    line_height="1.5",
    body_line_height="1.65",
    heading_weight="600",
    section_heading_weight="600",
    body_weight="400",
    letter_spacing="normal",
    prose_max_width="42rem",
    badge_radius="9999px",
)

LAYOUT = LayoutStyle(
    name="ocean_deep",
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
    section_spacing="4.5rem",
    hero_padding_top="7rem",
    hero_padding_bottom="4rem",
    hero_line_height="1.15",
    hero_max_width="50rem",
)

SURFACE = SurfaceStyle(
    name="ocean_deep",
    shadow_sm="0 2px 4px rgba(30, 60, 120, 0.08)",
    shadow_md="0 4px 12px rgba(30, 60, 120, 0.1)",
    shadow_lg="0 8px 24px rgba(30, 60, 120, 0.12)",
    border_width="1px",
    border_style="solid",
    surface_treatment="flat",
    backdrop_blur="0px",
    noise_opacity=0.0,
)

ICON = IconStyle(
    name="ocean_deep",
    style="rounded",
    weight="regular",
    size_scale=1.0,
    stroke_width="1.5",
    corner_rounding="3px",
)

ANIMATION = AnimationStyle(
    name="ocean_deep",
    entrance_effect="fade",
    exit_effect="fade",
    hover_effect="lift",
    hover_scale=1.0,
    hover_translate_y="-1px",
    click_effect="none",
    loading_style="pulse",
    transition_style="smooth",
    duration_fast="0.15s",
    duration_normal="0.25s",
    duration_slow="0.4s",
    easing="cubic-bezier(0.25, 0.1, 0.25, 1)",
)

INTERACTION = InteractionStyle(
    name="ocean_deep",
    button_hover="lift",
    link_hover="color",
    card_hover="lift",
    focus_style="ring",
    focus_ring_width="2px",
)

DESIGN_SYSTEM = DesignSystem(
    name="ocean_deep",
    display_name="Ocean Deep",
    description="Deep sea -- coastal sky to ocean floor",
    category="playful",
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
    name="ocean_deep",
    display_name="Ocean Deep",
    description="Deep sea -- coastal sky to ocean floor",
    category="playful",
    design_theme="ocean_deep",
    color_preset="ocean_deep",
    icon_style=ICON,
    animation_style=ANIMATION,
    pattern_style=PATTERN_NOISE,
    interaction_style=INTERACTION,
    illustration_style=ILLUST_HAND_DRAWN,
)
