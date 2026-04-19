"""High Contrast -- Accessibility-first with maximum contrast ratios."""

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
    ILLUST_FLAT,
    PATTERN_MINIMAL,
)

# --- Color Preset ---

LIGHT = ThemeTokens(
    background=ColorScale(0, 0, 100),
    foreground=ColorScale(0, 0, 0),
    card=ColorScale(0, 0, 100),
    card_foreground=ColorScale(0, 0, 0),
    popover=ColorScale(0, 0, 100),
    popover_foreground=ColorScale(0, 0, 0),
    primary=ColorScale(220, 100, 40),
    primary_foreground=ColorScale(0, 0, 100),
    secondary=ColorScale(0, 0, 95),
    secondary_foreground=ColorScale(0, 0, 0),
    muted=ColorScale(0, 0, 95),
    muted_foreground=ColorScale(0, 0, 30),
    accent=ColorScale(0, 0, 90),
    accent_foreground=ColorScale(0, 0, 0),
    destructive=ColorScale(0, 100, 40),
    destructive_foreground=ColorScale(0, 0, 100),
    success=ColorScale(120, 100, 25),
    success_foreground=ColorScale(0, 0, 100),
    warning=ColorScale(40, 100, 40),
    warning_foreground=ColorScale(0, 0, 0),
    info=ColorScale(220, 100, 40),
    info_foreground=ColorScale(0, 0, 100),
    link=ColorScale(220, 100, 35),
    link_hover=ColorScale(220, 100, 25),
    code=ColorScale(0, 0, 93),
    code_foreground=ColorScale(0, 100, 40),
    selection=ColorScale(220, 80, 85),
    selection_foreground=ColorScale(220, 10, 15),
    brand=ColorScale(220, 100, 40),
    brand_foreground=ColorScale(0, 0, 100),
    border=ColorScale(0, 0, 0),
    input=ColorScale(0, 0, 0),
    ring=ColorScale(220, 100, 40),
    surface_1=ColorScale(220, 5, 96),
    surface_2=ColorScale(220, 5, 93),
    surface_3=ColorScale(220, 5, 90),
)

DARK = ThemeTokens(
    background=ColorScale(0, 0, 0),
    foreground=ColorScale(0, 0, 100),
    card=ColorScale(0, 0, 5),
    card_foreground=ColorScale(0, 0, 100),
    popover=ColorScale(0, 0, 5),
    popover_foreground=ColorScale(0, 0, 100),
    primary=ColorScale(210, 100, 60),
    primary_foreground=ColorScale(0, 0, 0),
    secondary=ColorScale(0, 0, 12),
    secondary_foreground=ColorScale(0, 0, 100),
    muted=ColorScale(0, 0, 12),
    muted_foreground=ColorScale(0, 0, 70),
    accent=ColorScale(0, 0, 15),
    accent_foreground=ColorScale(0, 0, 100),
    destructive=ColorScale(0, 100, 55),
    destructive_foreground=ColorScale(0, 0, 0),
    success=ColorScale(120, 100, 45),
    success_foreground=ColorScale(0, 0, 0),
    warning=ColorScale(45, 100, 50),
    warning_foreground=ColorScale(0, 0, 0),
    info=ColorScale(210, 100, 60),
    info_foreground=ColorScale(0, 0, 0),
    link=ColorScale(210, 100, 65),
    link_hover=ColorScale(210, 100, 80),
    code=ColorScale(0, 0, 10),
    code_foreground=ColorScale(120, 100, 50),
    selection=ColorScale(210, 80, 25),
    selection_foreground=ColorScale(210, 10, 90),
    brand=ColorScale(210, 100, 60),
    brand_foreground=ColorScale(0, 0, 0),
    border=ColorScale(0, 0, 100),
    input=ColorScale(0, 0, 100),
    ring=ColorScale(210, 100, 60),
    surface_1=ColorScale(210, 5, 6),
    surface_2=ColorScale(210, 5, 10),
    surface_3=ColorScale(210, 5, 14),
)

PRESET = ThemePreset(
    name="high_contrast",
    display_name="High Contrast",
    description="Accessibility-first with maximum contrast ratios",
    light=LIGHT,
    dark=DARK,
)


# =============================================================================
# Design System
# =============================================================================

TYPOGRAPHY = TypographyStyle(
    name="high_contrast",
    heading_font="system-ui, sans-serif",
    body_font="system-ui, sans-serif",
    base_size="16px",
    heading_scale=1.3,
    line_height="1.7",
    body_line_height="1.7",
    heading_weight="700",
    section_heading_weight="700",
    body_weight="400",
    letter_spacing="normal",
    prose_max_width="42rem",
    badge_radius="4px",
)

LAYOUT = LayoutStyle(
    name="high_contrast",
    space_unit="1rem",
    space_scale=1.5,
    border_radius_sm="2px",
    border_radius_md="4px",
    border_radius_lg="6px",
    button_shape="rounded",
    card_shape="rounded",
    input_shape="rounded",
    container_width="1200px",
    grid_gap="1.5rem",
    section_spacing="4rem",
    hero_padding_top="7rem",
    hero_padding_bottom="4rem",
    hero_line_height="1.15",
    hero_max_width="52rem",
)

SURFACE = SurfaceStyle(
    name="high_contrast",
    shadow_sm="0 2px 4px rgba(0, 0, 0, 0.15)",
    shadow_md="0 4px 8px rgba(0, 0, 0, 0.2)",
    shadow_lg="0 8px 16px rgba(0, 0, 0, 0.25)",
    border_width="2px",
    border_style="solid",
    surface_treatment="flat",
    backdrop_blur="0px",
    noise_opacity=0.0,
)

ICON = IconStyle(
    name="high_contrast",
    style="sharp",
    weight="bold",
    size_scale=1.05,
    stroke_width="2.5",
    corner_rounding="0px",
)

ANIMATION = AnimationStyle(
    name="high_contrast",
    entrance_effect="fade",
    exit_effect="fade",
    hover_effect="none",
    hover_scale=1.0,
    hover_translate_y="0px",
    click_effect="none",
    loading_style="spinner",
    transition_style="snappy",
    duration_fast="0.08s",
    duration_normal="0.15s",
    duration_slow="0.25s",
    easing="cubic-bezier(0.4, 0, 0.2, 1)",
)

INTERACTION = InteractionStyle(
    name="high_contrast",
    button_hover="darken",
    link_hover="underline",
    card_hover="border",
    focus_style="outline",
    focus_ring_width="3px",
)

DESIGN_SYSTEM = DesignSystem(
    name="high_contrast",
    display_name="High Contrast",
    description="Accessibility-first -- bold weight, thick borders, strong depth cues",
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
    name="high_contrast",
    display_name="High Contrast",
    description="Accessibility-first -- bold weight, thick borders, strong depth cues",
    category="minimal",
    design_theme="high_contrast",
    color_preset="high_contrast",
    icon_style=ICON,
    animation_style=ANIMATION,
    pattern_style=PATTERN_MINIMAL,
    interaction_style=INTERACTION,
    illustration_style=ILLUST_FLAT,
)
