"""Raycast — macOS-native aesthetic, ultra-clean system font, subtle gradients."""

from ._base import (
    ColorScale,
    ThemeTokens,
    ThemePreset,
    TypographyStyle,
    LayoutStyle,
    SurfaceStyle,
    IconStyle,
    AnimationStyle,
    InteractionStyle,
    DesignSystem,
    ThemePack,
    PATTERN_MINIMAL,
    ILLUST_LINE,
)


# =============================================================================
# Color Preset
# =============================================================================
# Colors from Raycast design:
# - Light: pure white bg, near-black fg
# - Dark: #141414 bg, #f2f2f2 fg
# - Purple #7c5cfc (primary/brand)
# - Cool grays, macOS-inspired

LIGHT = ThemeTokens(
    background=ColorScale(0, 0, 100),  # Pure white
    foreground=ColorScale(0, 0, 10),  # Near-black
    card=ColorScale(0, 0, 100),
    card_foreground=ColorScale(0, 0, 10),
    popover=ColorScale(0, 0, 100),
    popover_foreground=ColorScale(0, 0, 10),
    primary=ColorScale(262, 83, 58),  # Raycast purple
    primary_foreground=ColorScale(0, 0, 100),
    secondary=ColorScale(0, 0, 96),
    secondary_foreground=ColorScale(0, 0, 10),
    muted=ColorScale(220, 5, 91),
    muted_foreground=ColorScale(220, 5, 45),
    accent=ColorScale(0, 0, 96),  # Subtle hover
    accent_foreground=ColorScale(0, 0, 10),
    destructive=ColorScale(0, 72, 51),  # Red
    destructive_foreground=ColorScale(0, 0, 100),
    success=ColorScale(142, 70, 45),  # Green
    success_foreground=ColorScale(0, 0, 100),
    warning=ColorScale(38, 92, 50),  # Amber
    warning_foreground=ColorScale(0, 0, 10),
    info=ColorScale(262, 83, 58),  # Purple doubles as info
    info_foreground=ColorScale(0, 0, 100),
    link=ColorScale(262, 83, 58),  # Purple
    link_hover=ColorScale(262, 83, 48),
    code=ColorScale(0, 0, 8),  # Dark code bg
    code_foreground=ColorScale(262, 60, 72),  # Purple code text
    selection=ColorScale(262, 70, 92),  # Pale purple selection
    selection_foreground=ColorScale(0, 0, 10),
    brand=ColorScale(262, 83, 58),  # Raycast purple
    brand_foreground=ColorScale(0, 0, 100),
    border=ColorScale(220, 5, 90),
    input=ColorScale(220, 5, 94),
    ring=ColorScale(262, 83, 58),
    surface_1=ColorScale(0, 0, 100),
    surface_2=ColorScale(220, 5, 97),
    surface_3=ColorScale(220, 5, 94),
)

DARK = ThemeTokens(
    background=ColorScale(0, 0, 8),  # #141414
    foreground=ColorScale(0, 0, 95),  # #f2f2f2
    card=ColorScale(0, 0, 12),
    card_foreground=ColorScale(0, 0, 95),
    popover=ColorScale(0, 0, 12),
    popover_foreground=ColorScale(0, 0, 95),
    primary=ColorScale(262, 83, 58),  # Raycast purple
    primary_foreground=ColorScale(0, 0, 100),
    secondary=ColorScale(0, 0, 15),
    secondary_foreground=ColorScale(0, 0, 95),
    muted=ColorScale(220, 5, 18),
    muted_foreground=ColorScale(220, 5, 55),
    accent=ColorScale(0, 0, 15),  # Subtle dark hover
    accent_foreground=ColorScale(0, 0, 95),
    destructive=ColorScale(0, 72, 56),  # Red brightened
    destructive_foreground=ColorScale(0, 0, 100),
    success=ColorScale(142, 70, 50),  # Green brightened
    success_foreground=ColorScale(0, 0, 100),
    warning=ColorScale(38, 92, 55),  # Amber brightened
    warning_foreground=ColorScale(0, 0, 8),
    info=ColorScale(262, 83, 65),  # Purple brightened
    info_foreground=ColorScale(0, 0, 100),
    link=ColorScale(262, 83, 68),  # Purple brightened
    link_hover=ColorScale(262, 83, 76),
    code=ColorScale(0, 0, 5),  # Very dark code bg
    code_foreground=ColorScale(262, 60, 72),  # Purple code text
    selection=ColorScale(262, 50, 20),  # Deep purple selection
    selection_foreground=ColorScale(0, 0, 95),
    brand=ColorScale(262, 83, 65),  # Purple brightened
    brand_foreground=ColorScale(0, 0, 100),
    border=ColorScale(220, 5, 18),
    input=ColorScale(220, 5, 18),
    ring=ColorScale(262, 83, 58),
    surface_1=ColorScale(0, 0, 5),
    surface_2=ColorScale(0, 0, 8),
    surface_3=ColorScale(0, 0, 12),
)

PRESET = ThemePreset(
    name="raycast",
    display_name="Raycast",
    description="macOS-native aesthetic — ultra-clean system font, purple accents",
    light=LIGHT,
    dark=DARK,
    radius=0.625,  # 10px — macOS rounded
)


# =============================================================================
# Design System
# =============================================================================

TYPOGRAPHY = TypographyStyle(
    name="raycast",
    heading_font='"-apple-system", "BlinkMacSystemFont", system-ui, sans-serif',
    body_font='"-apple-system", "BlinkMacSystemFont", system-ui, sans-serif',
    base_size="16px",
    heading_scale=1.25,
    line_height="1.5",
    body_line_height="1.6",
    heading_weight="600",
    section_heading_weight="600",
    body_weight="400",
    letter_spacing="-0.01em",
    prose_max_width="42rem",
    badge_radius="9999px",
)

LAYOUT = LayoutStyle(
    name="raycast",
    space_unit="1rem",
    space_scale=1.5,
    border_radius_sm="8px",
    border_radius_md="10px",
    border_radius_lg="14px",
    button_shape="rounded",
    card_shape="rounded",
    input_shape="rounded",
    container_width="1100px",
    grid_gap="1.5rem",
    section_spacing="5rem",
    hero_padding_top="8rem",
    hero_padding_bottom="5rem",
    hero_line_height="1.1",
    hero_max_width="52rem",
)

SURFACE = SurfaceStyle(
    name="raycast",
    shadow_sm="0 1px 2px rgba(0, 0, 0, 0.04), 0 1px 3px rgba(0, 0, 0, 0.06)",
    shadow_md="0 4px 8px rgba(0, 0, 0, 0.06), 0 2px 4px rgba(0, 0, 0, 0.04)",
    shadow_lg="0 8px 24px rgba(0, 0, 0, 0.08), 0 4px 8px rgba(0, 0, 0, 0.04)",
    border_width="1px",
    border_style="solid",
    surface_treatment="gradient",
    backdrop_blur="0px",
    noise_opacity=0.0,
)

ICON = IconStyle(
    name="raycast",
    style="outlined",
    weight="regular",
    size_scale=1.0,
    stroke_width="1.5",
    corner_rounding="3px",
)

ANIMATION = AnimationStyle(
    name="raycast",
    entrance_effect="fade",
    exit_effect="fade",
    hover_effect="lift",
    hover_scale=1.0,
    hover_translate_y="-1px",
    click_effect="pulse",
    loading_style="spinner",
    transition_style="smooth",
    duration_fast="0.12s",
    duration_normal="0.2s",
    duration_slow="0.3s",
    easing="cubic-bezier(0.25, 0.1, 0.25, 1)",  # ease-out
)

INTERACTION = InteractionStyle(
    name="raycast",
    button_hover="lift",
    link_hover="color",
    card_hover="shadow",
    focus_style="ring",
    focus_ring_width="2px",
)

DESIGN_SYSTEM = DesignSystem(
    name="raycast",
    display_name="Raycast",
    description="macOS-native — system fonts, large radius, gradient surfaces, ease-out motion",
    category="professional",
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
    name="raycast",
    display_name="Raycast",
    description="macOS-native aesthetic — ultra-clean system font, purple accents",
    category="professional",
    design_theme="raycast",
    color_preset="raycast",
    icon_style=ICON,
    animation_style=ANIMATION,
    pattern_style=PATTERN_MINIMAL,
    interaction_style=INTERACTION,
    illustration_style=ILLUST_LINE,
)
