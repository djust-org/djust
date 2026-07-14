"""Art Nouveau -- Mucha elegance: cream parchment, sage gold, deep plum."""

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
    PATTERN_GRADIENT,
)

# --- Color Preset ---

LIGHT = ThemeTokens(
    background=ColorScale(45, 42, 96),
    foreground=ColorScale(30, 25, 18),
    card=ColorScale(45, 40, 92),
    card_foreground=ColorScale(30, 25, 18),
    popover=ColorScale(45, 40, 92),
    popover_foreground=ColorScale(30, 25, 18),
    primary=ColorScale(75, 32, 32),
    primary_foreground=ColorScale(0, 0, 100),
    secondary=ColorScale(60, 22, 87),
    secondary_foreground=ColorScale(30, 25, 18),
    muted=ColorScale(48, 25, 89),
    muted_foreground=ColorScale(35, 18, 40),
    accent=ColorScale(330, 38, 36),
    accent_foreground=ColorScale(0, 0, 100),
    destructive=ColorScale(355, 58, 40),
    destructive_foreground=ColorScale(0, 0, 100),
    success=ColorScale(100, 32, 32),
    success_foreground=ColorScale(0, 0, 100),
    warning=ColorScale(40, 72, 44),
    warning_foreground=ColorScale(0, 0, 10),
    info=ColorScale(185, 34, 36),
    info_foreground=ColorScale(0, 0, 100),
    link=ColorScale(75, 34, 30),
    link_hover=ColorScale(330, 38, 32),
    code=ColorScale(48, 30, 90),
    code_foreground=ColorScale(75, 30, 28),
    selection=ColorScale(42, 60, 84),
    selection_foreground=ColorScale(30, 25, 16),
    brand=ColorScale(42, 58, 44),
    brand_foreground=ColorScale(0, 0, 100),
    border=ColorScale(48, 22, 80),
    input=ColorScale(48, 22, 80),
    ring=ColorScale(75, 32, 36),
    surface_1=ColorScale(45, 38, 94),
    surface_2=ColorScale(45, 36, 91),
    surface_3=ColorScale(45, 34, 88),
)

DARK = ThemeTokens(
    background=ColorScale(75, 14, 10),
    foreground=ColorScale(45, 32, 88),
    card=ColorScale(75, 12, 14),
    card_foreground=ColorScale(45, 32, 88),
    popover=ColorScale(75, 12, 14),
    popover_foreground=ColorScale(45, 32, 88),
    primary=ColorScale(70, 38, 58),
    primary_foreground=ColorScale(75, 18, 9),
    secondary=ColorScale(75, 11, 19),
    secondary_foreground=ColorScale(45, 32, 88),
    muted=ColorScale(75, 11, 19),
    muted_foreground=ColorScale(50, 15, 58),
    accent=ColorScale(330, 42, 66),
    accent_foreground=ColorScale(75, 18, 9),
    destructive=ColorScale(355, 62, 50),
    destructive_foreground=ColorScale(0, 0, 100),
    success=ColorScale(100, 32, 52),
    success_foreground=ColorScale(75, 18, 9),
    warning=ColorScale(40, 75, 56),
    warning_foreground=ColorScale(0, 0, 10),
    info=ColorScale(185, 38, 56),
    info_foreground=ColorScale(75, 18, 9),
    link=ColorScale(70, 40, 62),
    link_hover=ColorScale(330, 42, 70),
    code=ColorScale(75, 12, 16),
    code_foreground=ColorScale(70, 42, 64),
    selection=ColorScale(42, 45, 26),
    selection_foreground=ColorScale(45, 32, 90),
    brand=ColorScale(42, 60, 58),
    brand_foreground=ColorScale(75, 18, 9),
    border=ColorScale(75, 11, 23),
    input=ColorScale(75, 11, 23),
    ring=ColorScale(70, 38, 58),
    surface_1=ColorScale(75, 13, 8),
    surface_2=ColorScale(75, 13, 12),
    surface_3=ColorScale(75, 13, 16),
)

PRESET = ThemePreset(
    name="art_nouveau",
    display_name="Art Nouveau",
    description="Mucha elegance — cream parchment, sage gold, deep plum",
    light=LIGHT,
    dark=DARK,
    default_mode="light",
)


# =============================================================================
# Design System
# =============================================================================

TYPOGRAPHY = TypographyStyle(
    name="art_nouveau",
    heading_font="Georgia, 'Palatino Linotype', 'Book Antiqua', serif",
    body_font="Georgia, 'Times New Roman', serif",
    base_size="17px",
    heading_scale=1.32,
    line_height="1.35",
    body_line_height="1.8",
    heading_weight="500",
    section_heading_weight="500",
    body_weight="400",
    letter_spacing="0.015em",
    prose_max_width="38rem",
    badge_radius="9999px",
)

LAYOUT = LayoutStyle(
    name="art_nouveau",
    space_unit="1rem",
    space_scale=1.7,
    border_radius_sm="10px",
    border_radius_md="16px",
    border_radius_lg="24px",
    button_shape="pill",
    card_shape="rounded",
    input_shape="rounded",
    container_width="1000px",
    grid_gap="2rem",
    section_spacing="5.5rem",
    hero_padding_top="8rem",
    hero_padding_bottom="5rem",
    hero_line_height="1.25",
    hero_max_width="44rem",
)

SURFACE = SurfaceStyle(
    name="art_nouveau",
    shadow_sm="0 1px 3px rgba(110, 90, 40, 0.10)",
    shadow_md="0 4px 12px rgba(110, 90, 40, 0.12)",
    shadow_lg="0 10px 32px rgba(110, 90, 40, 0.16)",
    border_width="1px",
    border_style="solid",
    surface_treatment="flat",
    backdrop_blur="0px",
    noise_opacity=0.0,
)

ICON = IconStyle(
    name="art_nouveau",
    style="rounded",
    weight="light",
    size_scale=1.05,
    stroke_width="1.25",
    corner_rounding="5px",
)

ANIMATION = AnimationStyle(
    name="art_nouveau",
    entrance_effect="fade",
    exit_effect="fade",
    hover_effect="lift",
    hover_scale=1.01,
    hover_translate_y="-1px",
    click_effect="pulse",
    loading_style="pulse",
    transition_style="smooth",
    duration_fast="0.2s",
    duration_normal="0.35s",
    duration_slow="0.55s",
    easing="cubic-bezier(0.22, 0.61, 0.36, 1)",
)

INTERACTION = InteractionStyle(
    name="art_nouveau",
    button_hover="lift",
    link_hover="underline",
    card_hover="lift",
    focus_style="ring",
    focus_ring_width="2px",
)

DESIGN_SYSTEM = DesignSystem(
    name="art_nouveau",
    display_name="Art Nouveau",
    description="Mucha elegance -- cream parchment, sage gold, deep plum",
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
    name="art_nouveau",
    display_name="Art Nouveau",
    description="Mucha elegance -- cream parchment, sage gold, deep plum",
    category="elegant",
    design_theme="art_nouveau",
    color_preset="art_nouveau",
    icon_style=ICON,
    animation_style=ANIMATION,
    pattern_style=PATTERN_GRADIENT,
    interaction_style=INTERACTION,
    illustration_style=ILLUST_HAND_DRAWN,
)
