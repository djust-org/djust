"""Natural 20 -- Dark-first Bloomberg Terminal-inspired theme with cyan accents."""

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
    background=ColorScale(0, 0, 98),
    foreground=ColorScale(240, 10, 10),
    card=ColorScale(0, 0, 100),
    card_foreground=ColorScale(240, 10, 10),
    popover=ColorScale(0, 0, 100),
    popover_foreground=ColorScale(240, 10, 10),
    primary=ColorScale(211, 100, 65),
    primary_foreground=ColorScale(0, 0, 100),
    secondary=ColorScale(240, 5, 96),
    secondary_foreground=ColorScale(240, 10, 10),
    muted=ColorScale(240, 5, 96),
    muted_foreground=ColorScale(0, 0, 53),
    accent=ColorScale(211, 100, 96),
    accent_foreground=ColorScale(211, 100, 30),
    destructive=ColorScale(0, 84, 60),
    destructive_foreground=ColorScale(0, 0, 100),
    success=ColorScale(142, 76, 36),
    success_foreground=ColorScale(0, 0, 100),
    warning=ColorScale(38, 92, 50),
    warning_foreground=ColorScale(0, 0, 10),
    info=ColorScale(211, 100, 65),
    info_foreground=ColorScale(0, 0, 100),
    link=ColorScale(211, 100, 55),
    link_hover=ColorScale(211, 100, 45),
    code=ColorScale(240, 5, 96),
    code_foreground=ColorScale(240, 10, 20),
    selection=ColorScale(211, 100, 85),
    selection_foreground=ColorScale(240, 10, 10),
    brand=ColorScale(211, 100, 65),
    brand_foreground=ColorScale(0, 0, 100),
    border=ColorScale(240, 5, 90),
    input=ColorScale(0, 0, 100),
    ring=ColorScale(211, 100, 65),
    surface_1=ColorScale(240, 15, 98),
    surface_2=ColorScale(240, 12, 96),
    surface_3=ColorScale(240, 10, 93),
)

DARK = ThemeTokens(
    background=ColorScale(240, 13, 3),
    foreground=ColorScale(0, 0, 100),
    card=ColorScale(240, 15, 6),
    card_foreground=ColorScale(0, 0, 100),
    popover=ColorScale(240, 15, 6),
    popover_foreground=ColorScale(0, 0, 100),
    primary=ColorScale(211, 100, 65),
    primary_foreground=ColorScale(0, 0, 100),
    secondary=ColorScale(240, 10, 10),
    secondary_foreground=ColorScale(0, 0, 88),
    muted=ColorScale(240, 9, 13),
    muted_foreground=ColorScale(0, 0, 53),
    accent=ColorScale(240, 9, 13),
    accent_foreground=ColorScale(211, 100, 65),
    destructive=ColorScale(0, 84, 53),
    destructive_foreground=ColorScale(0, 0, 100),
    success=ColorScale(160, 84, 39),
    success_foreground=ColorScale(0, 0, 100),
    warning=ColorScale(38, 100, 59),
    warning_foreground=ColorScale(0, 0, 10),
    info=ColorScale(211, 100, 65),
    info_foreground=ColorScale(0, 0, 100),
    link=ColorScale(211, 100, 65),
    link_hover=ColorScale(211, 100, 75),
    code=ColorScale(240, 10, 8),
    code_foreground=ColorScale(0, 0, 88),
    selection=ColorScale(211, 100, 30),
    selection_foreground=ColorScale(0, 0, 100),
    brand=ColorScale(211, 100, 65),
    brand_foreground=ColorScale(0, 0, 100),
    border=ColorScale(240, 9, 13),
    input=ColorScale(240, 15, 6),
    ring=ColorScale(211, 100, 65),
    surface_1=ColorScale(240, 15, 4),
    surface_2=ColorScale(240, 12, 7),
    surface_3=ColorScale(240, 10, 11),
)

PRESET = ThemePreset(
    name="natural20",
    display_name="Natural 20",
    description="Dark-first Bloomberg Terminal-inspired theme with cyan accents",
    light=LIGHT,
    dark=DARK,
    radius=0.75,
)


# =============================================================================
# Design System
# =============================================================================

TYPOGRAPHY = TypographyStyle(
    name="natural20",
    heading_font='"Cinzel", Georgia, serif',
    body_font="system-ui, sans-serif",
    base_size="16px",
    heading_scale=1.25,
    line_height="1.5",
    body_line_height="1.65",
    heading_weight="700",
    section_heading_weight="700",
    body_weight="400",
    letter_spacing="normal",
    prose_max_width="42rem",
    badge_radius="4px",
)

LAYOUT = LayoutStyle(
    name="natural20",
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
    name="natural20",
    shadow_sm="0 2px 4px rgba(80, 50, 20, 0.08)",
    shadow_md="0 4px 12px rgba(80, 50, 20, 0.12)",
    shadow_lg="0 8px 24px rgba(80, 50, 20, 0.16)",
    border_width="1px",
    border_style="solid",
    surface_treatment="noise",
    backdrop_blur="0px",
    noise_opacity=0.03,
)

ICON = IconStyle(
    name="natural20",
    style="rounded",
    weight="regular",
    size_scale=1.0,
    stroke_width="1.5",
    corner_rounding="2px",
)

ANIMATION = AnimationStyle(
    name="natural20",
    entrance_effect="fade",
    exit_effect="fade",
    hover_effect="lift",
    hover_scale=1.0,
    hover_translate_y="-2px",
    click_effect="pulse",
    loading_style="pulse",
    transition_style="smooth",
    duration_fast="0.15s",
    duration_normal="0.3s",
    duration_slow="0.45s",
    easing="cubic-bezier(0.25, 0.1, 0.25, 1)",
)

INTERACTION = InteractionStyle(
    name="natural20",
    button_hover="lift",
    link_hover="color",
    card_hover="lift",
    focus_style="ring",
    focus_ring_width="2px",
)

DESIGN_SYSTEM = DesignSystem(
    name="natural20",
    display_name="Natural 20",
    description="D&D fantasy -- medieval serif, warm shadows, adventure feel",
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
    name="natural20",
    display_name="Natural 20",
    description="D&D fantasy -- medieval serif, warm shadows, adventure feel",
    category="playful",
    design_theme="natural20",
    color_preset="natural20",
    icon_style=ICON,
    animation_style=ANIMATION,
    pattern_style=PATTERN_NOISE,
    interaction_style=INTERACTION,
    illustration_style=ILLUST_HAND_DRAWN,
)
