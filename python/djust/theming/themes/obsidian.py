"""Obsidian -- volcanic glass: near-black gloss, magma orange, ember gold."""

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
    PATTERN_GLASS,
)

# --- Color Preset ---

LIGHT = ThemeTokens(
    background=ColorScale(30, 10, 96),
    foreground=ColorScale(240, 10, 12),
    card=ColorScale(30, 9, 92),
    card_foreground=ColorScale(240, 10, 12),
    popover=ColorScale(30, 9, 92),
    popover_foreground=ColorScale(240, 10, 12),
    primary=ColorScale(18, 90, 40),
    primary_foreground=ColorScale(0, 0, 100),
    secondary=ColorScale(30, 8, 87),
    secondary_foreground=ColorScale(240, 10, 12),
    muted=ColorScale(30, 8, 88),
    muted_foreground=ColorScale(240, 8, 40),
    accent=ColorScale(40, 90, 42),
    accent_foreground=ColorScale(0, 0, 10),
    destructive=ColorScale(0, 78, 46),
    destructive_foreground=ColorScale(0, 0, 100),
    success=ColorScale(150, 45, 34),
    success_foreground=ColorScale(0, 0, 100),
    warning=ColorScale(45, 95, 45),
    warning_foreground=ColorScale(0, 0, 10),
    info=ColorScale(210, 55, 44),
    info_foreground=ColorScale(0, 0, 100),
    link=ColorScale(18, 88, 42),
    link_hover=ColorScale(18, 92, 34),
    code=ColorScale(30, 9, 90),
    code_foreground=ColorScale(18, 80, 38),
    selection=ColorScale(18, 85, 86),
    selection_foreground=ColorScale(18, 30, 14),
    brand=ColorScale(18, 88, 44),
    brand_foreground=ColorScale(0, 0, 100),
    border=ColorScale(30, 8, 80),
    input=ColorScale(30, 8, 80),
    ring=ColorScale(18, 88, 44),
    surface_1=ColorScale(30, 9, 94),
    surface_2=ColorScale(30, 9, 91),
    surface_3=ColorScale(30, 9, 88),
)

DARK = ThemeTokens(
    background=ColorScale(240, 8, 6),
    foreground=ColorScale(30, 8, 92),
    card=ColorScale(240, 7, 9),
    card_foreground=ColorScale(30, 8, 92),
    popover=ColorScale(240, 7, 9),
    popover_foreground=ColorScale(30, 8, 92),
    primary=ColorScale(18, 95, 56),
    primary_foreground=ColorScale(240, 10, 5),
    secondary=ColorScale(240, 7, 13),
    secondary_foreground=ColorScale(30, 8, 92),
    muted=ColorScale(240, 7, 13),
    muted_foreground=ColorScale(30, 6, 58),
    accent=ColorScale(40, 92, 56),
    accent_foreground=ColorScale(240, 10, 5),
    destructive=ColorScale(0, 75, 50),
    destructive_foreground=ColorScale(0, 0, 100),
    success=ColorScale(150, 48, 48),
    success_foreground=ColorScale(240, 10, 5),
    warning=ColorScale(45, 95, 56),
    warning_foreground=ColorScale(0, 0, 10),
    info=ColorScale(210, 60, 58),
    info_foreground=ColorScale(240, 10, 5),
    link=ColorScale(18, 95, 60),
    link_hover=ColorScale(40, 92, 62),
    code=ColorScale(240, 7, 11),
    code_foreground=ColorScale(18, 90, 62),
    selection=ColorScale(18, 70, 24),
    selection_foreground=ColorScale(30, 10, 92),
    brand=ColorScale(18, 95, 56),
    brand_foreground=ColorScale(240, 10, 5),
    border=ColorScale(240, 7, 17),
    input=ColorScale(240, 7, 17),
    ring=ColorScale(18, 95, 56),
    surface_1=ColorScale(240, 8, 4),
    surface_2=ColorScale(240, 8, 8),
    surface_3=ColorScale(240, 8, 12),
)

PRESET = ThemePreset(
    name="obsidian",
    display_name="Obsidian",
    description="Volcanic glass — near-black gloss with magma and ember accents",
    light=LIGHT,
    dark=DARK,
    default_mode="dark",
)


# =============================================================================
# Design System
# =============================================================================

TYPOGRAPHY = TypographyStyle(
    name="obsidian",
    heading_font="system-ui, sans-serif",
    body_font="system-ui, sans-serif",
    base_size="16px",
    heading_scale=1.3,
    line_height="1.35",
    body_line_height="1.6",
    heading_weight="700",
    section_heading_weight="700",
    body_weight="400",
    letter_spacing="-0.01em",
    prose_max_width="42rem",
    badge_radius="6px",
)

LAYOUT = LayoutStyle(
    name="obsidian",
    space_unit="1rem",
    space_scale=1.5,
    border_radius_sm="4px",
    border_radius_md="6px",
    border_radius_lg="10px",
    button_shape="rounded",
    card_shape="rounded",
    input_shape="rounded",
    container_width="1120px",
    grid_gap="1.5rem",
    section_spacing="4.5rem",
    hero_padding_top="7rem",
    hero_padding_bottom="4rem",
    hero_line_height="1.1",
    hero_max_width="52rem",
)

SURFACE = SurfaceStyle(
    name="obsidian",
    shadow_sm="0 2px 6px rgba(0, 0, 0, 0.35)",
    shadow_md="0 6px 16px rgba(0, 0, 0, 0.45)",
    shadow_lg="0 14px 40px rgba(0, 0, 0, 0.55)",
    border_width="1px",
    border_style="solid",
    surface_treatment="flat",
    backdrop_blur="8px",
    noise_opacity=0.0,
)

ICON = IconStyle(
    name="obsidian",
    style="sharp",
    weight="regular",
    size_scale=1.0,
    stroke_width="1.75",
    corner_rounding="2px",
)

ANIMATION = AnimationStyle(
    name="obsidian",
    entrance_effect="fade",
    exit_effect="fade",
    hover_effect="glow",
    hover_scale=1.0,
    hover_translate_y="0px",
    click_effect="pulse",
    loading_style="pulse",
    transition_style="smooth",
    duration_fast="0.12s",
    duration_normal="0.2s",
    duration_slow="0.35s",
    easing="cubic-bezier(0.4, 0, 0.2, 1)",
)

INTERACTION = InteractionStyle(
    name="obsidian",
    button_hover="glow",
    link_hover="color",
    card_hover="glow",
    focus_style="ring",
    focus_ring_width="2px",
)

DESIGN_SYSTEM = DesignSystem(
    name="obsidian",
    display_name="Obsidian",
    description="Volcanic glass -- near-black gloss with magma accents",
    category="bold",
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
    name="obsidian",
    display_name="Obsidian",
    description="Volcanic glass -- near-black gloss with magma accents",
    category="bold",
    design_theme="obsidian",
    color_preset="obsidian",
    icon_style=ICON,
    animation_style=ANIMATION,
    pattern_style=PATTERN_GLASS,
    interaction_style=INTERACTION,
    illustration_style=ILLUST_FLAT,
)
