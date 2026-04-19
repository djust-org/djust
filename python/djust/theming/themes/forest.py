"""Forest -- Deep emerald greens with warm earth tones."""

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
    background=ColorScale(150, 30, 98),
    foreground=ColorScale(150, 80, 15),
    card=ColorScale(150, 25, 95),
    card_foreground=ColorScale(150, 80, 15),
    popover=ColorScale(150, 25, 95),
    popover_foreground=ColorScale(150, 80, 15),
    primary=ColorScale(142, 71, 45),
    primary_foreground=ColorScale(0, 0, 100),
    secondary=ColorScale(150, 20, 90),
    secondary_foreground=ColorScale(150, 80, 20),
    muted=ColorScale(150, 20, 92),
    muted_foreground=ColorScale(150, 30, 40),
    accent=ColorScale(150, 25, 90),
    accent_foreground=ColorScale(150, 80, 20),
    destructive=ColorScale(0, 84, 60),
    destructive_foreground=ColorScale(0, 0, 100),
    success=ColorScale(142, 71, 45),
    success_foreground=ColorScale(0, 0, 100),
    warning=ColorScale(38, 92, 50),
    warning_foreground=ColorScale(0, 0, 10),
    info=ColorScale(158, 64, 52),
    info_foreground=ColorScale(0, 0, 100),
    link=ColorScale(142, 71, 40),
    link_hover=ColorScale(142, 71, 30),
    code=ColorScale(150, 30, 94),
    code_foreground=ColorScale(150, 80, 15),
    selection=ColorScale(142, 71, 45),
    selection_foreground=ColorScale(0, 0, 100),
    brand=ColorScale(142, 71, 45),
    brand_foreground=ColorScale(0, 0, 100),
    border=ColorScale(150, 20, 85),
    input=ColorScale(0, 0, 100),
    ring=ColorScale(142, 71, 45),
    surface_1=ColorScale(150, 20, 98),
    surface_2=ColorScale(150, 15, 96),
    surface_3=ColorScale(150, 12, 93),
)

DARK = ThemeTokens(
    background=ColorScale(150, 51, 8),
    foreground=ColorScale(150, 50, 95),
    card=ColorScale(150, 48, 12),
    card_foreground=ColorScale(150, 50, 95),
    popover=ColorScale(150, 48, 12),
    popover_foreground=ColorScale(150, 50, 95),
    primary=ColorScale(142, 71, 45),
    primary_foreground=ColorScale(0, 0, 100),
    secondary=ColorScale(150, 45, 18),
    secondary_foreground=ColorScale(150, 50, 95),
    muted=ColorScale(150, 45, 18),
    muted_foreground=ColorScale(150, 40, 65),
    accent=ColorScale(150, 45, 18),
    accent_foreground=ColorScale(142, 65, 75),
    destructive=ColorScale(0, 84, 60),
    destructive_foreground=ColorScale(0, 0, 100),
    success=ColorScale(142, 71, 45),
    success_foreground=ColorScale(0, 0, 100),
    warning=ColorScale(38, 92, 50),
    warning_foreground=ColorScale(0, 0, 10),
    info=ColorScale(158, 64, 52),
    info_foreground=ColorScale(0, 0, 100),
    link=ColorScale(142, 71, 50),
    link_hover=ColorScale(142, 71, 60),
    code=ColorScale(150, 48, 10),
    code_foreground=ColorScale(150, 50, 95),
    selection=ColorScale(142, 71, 45),
    selection_foreground=ColorScale(0, 0, 100),
    brand=ColorScale(142, 71, 45),
    brand_foreground=ColorScale(0, 0, 100),
    border=ColorScale(150, 45, 18),
    input=ColorScale(150, 48, 12),
    ring=ColorScale(142, 71, 45),
    surface_1=ColorScale(150, 20, 4),
    surface_2=ColorScale(150, 15, 7),
    surface_3=ColorScale(150, 12, 11),
)

PRESET = ThemePreset(
    name="forest",
    display_name="Forest",
    description="Deep emerald greens with warm earth tones",
    light=LIGHT,
    dark=DARK,
    radius=0.75,
)


# =============================================================================
# Design System
# =============================================================================

TYPOGRAPHY = TypographyStyle(
    name="forest",
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
    name="forest",
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
    name="forest",
    shadow_sm="0 2px 4px rgba(30, 80, 30, 0.08)",
    shadow_md="0 4px 12px rgba(30, 80, 30, 0.1)",
    shadow_lg="0 8px 24px rgba(30, 80, 30, 0.12)",
    border_width="1px",
    border_style="solid",
    surface_treatment="flat",
    backdrop_blur="0px",
    noise_opacity=0.0,
)

ICON = IconStyle(
    name="forest",
    style="rounded",
    weight="regular",
    size_scale=1.0,
    stroke_width="1.5",
    corner_rounding="3px",
)

ANIMATION = AnimationStyle(
    name="forest",
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
    name="forest",
    button_hover="lift",
    link_hover="color",
    card_hover="lift",
    focus_style="ring",
    focus_ring_width="2px",
)

DESIGN_SYSTEM = DesignSystem(
    name="forest",
    display_name="Forest",
    description="Deep emerald -- warm earth tones, organic shapes",
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
    name="forest",
    display_name="Forest",
    description="Deep emerald -- warm earth tones, organic shapes",
    category="playful",
    design_theme="forest",
    color_preset="forest",
    icon_style=ICON,
    animation_style=ANIMATION,
    pattern_style=PATTERN_NOISE,
    interaction_style=INTERACTION,
    illustration_style=ILLUST_HAND_DRAWN,
)
