"""One Dark — Atom's iconic warm dark theme with vibrant syntax colors."""

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
# Colors from Atom One Dark:
# - Background #282c34 (H=220 S=13 L=18)
# - Foreground #abb2bf (H=219 S=14 L=71)
# - Blue #61afef, Magenta #c678dd, Green #98c379
# - Yellow #e5c07b, Red #e06c75, Cyan #56b6c2

LIGHT = ThemeTokens(
    background=ColorScale(220, 10, 98),  # Very light cool gray
    foreground=ColorScale(220, 13, 18),  # Dark bg as fg
    card=ColorScale(0, 0, 100),
    card_foreground=ColorScale(220, 13, 18),
    popover=ColorScale(0, 0, 100),
    popover_foreground=ColorScale(220, 13, 18),
    primary=ColorScale(207, 82, 66),  # Blue #61afef
    primary_foreground=ColorScale(0, 0, 100),
    secondary=ColorScale(220, 10, 95),
    secondary_foreground=ColorScale(220, 13, 18),
    muted=ColorScale(220, 10, 90),
    muted_foreground=ColorScale(221, 14, 45),
    accent=ColorScale(220, 10, 95),  # Subtle hover surface
    accent_foreground=ColorScale(220, 13, 18),
    destructive=ColorScale(355, 65, 65),  # Red #e06c75
    destructive_foreground=ColorScale(0, 0, 100),
    success=ColorScale(95, 38, 62),  # Green #98c379
    success_foreground=ColorScale(220, 13, 18),
    warning=ColorScale(39, 67, 69),  # Yellow #e5c07b
    warning_foreground=ColorScale(220, 13, 18),
    info=ColorScale(187, 47, 55),  # Cyan #56b6c2
    info_foreground=ColorScale(0, 0, 100),
    link=ColorScale(207, 82, 66),  # Blue
    link_hover=ColorScale(207, 82, 55),
    code=ColorScale(220, 13, 18),  # Dark code bg
    code_foreground=ColorScale(95, 38, 62),  # Green code text
    selection=ColorScale(207, 82, 90),  # Pale blue selection
    selection_foreground=ColorScale(220, 13, 18),
    brand=ColorScale(286, 60, 67),  # Magenta #c678dd
    brand_foreground=ColorScale(0, 0, 100),
    border=ColorScale(220, 10, 88),
    input=ColorScale(220, 10, 92),
    ring=ColorScale(207, 82, 66),
    surface_1=ColorScale(220, 10, 98),
    surface_2=ColorScale(220, 10, 95),
    surface_3=ColorScale(220, 10, 92),
)

DARK = ThemeTokens(
    background=ColorScale(220, 13, 18),  # #282c34
    foreground=ColorScale(219, 14, 71),  # #abb2bf
    card=ColorScale(220, 13, 21),
    card_foreground=ColorScale(219, 14, 71),
    popover=ColorScale(220, 13, 21),
    popover_foreground=ColorScale(219, 14, 71),
    primary=ColorScale(207, 82, 66),  # Blue #61afef
    primary_foreground=ColorScale(220, 13, 18),
    secondary=ColorScale(220, 13, 24),
    secondary_foreground=ColorScale(219, 14, 71),
    muted=ColorScale(220, 13, 26),
    muted_foreground=ColorScale(221, 14, 45),
    accent=ColorScale(220, 13, 24),  # Subtle hover surface
    accent_foreground=ColorScale(219, 14, 71),
    destructive=ColorScale(355, 65, 65),  # Red #e06c75
    destructive_foreground=ColorScale(0, 0, 100),
    success=ColorScale(95, 38, 62),  # Green #98c379
    success_foreground=ColorScale(220, 13, 18),
    warning=ColorScale(39, 67, 69),  # Yellow #e5c07b
    warning_foreground=ColorScale(220, 13, 18),
    info=ColorScale(187, 47, 55),  # Cyan #56b6c2
    info_foreground=ColorScale(220, 13, 18),
    link=ColorScale(207, 82, 66),  # Blue
    link_hover=ColorScale(207, 82, 75),
    code=ColorScale(220, 13, 12),  # Very dark code bg
    code_foreground=ColorScale(95, 38, 70),  # Green code text
    selection=ColorScale(207, 60, 25),  # Blue-tinted selection
    selection_foreground=ColorScale(219, 14, 85),
    brand=ColorScale(286, 60, 67),  # Magenta #c678dd
    brand_foreground=ColorScale(0, 0, 100),
    border=ColorScale(220, 13, 28),
    input=ColorScale(220, 13, 28),
    ring=ColorScale(207, 82, 66),
    surface_1=ColorScale(220, 13, 14),
    surface_2=ColorScale(220, 13, 18),
    surface_3=ColorScale(220, 13, 22),
)

PRESET = ThemePreset(
    name="one_dark",
    display_name="One Dark",
    description="Atom's iconic warm dark theme with vibrant syntax colors",
    light=LIGHT,
    dark=DARK,
    radius=0.375,
    default_mode="dark",
)


# =============================================================================
# Design System
# =============================================================================

TYPOGRAPHY = TypographyStyle(
    name="one_dark",
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
    name="one_dark",
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
    hero_padding_bottom="5rem",
    hero_line_height="1.15",
    hero_max_width="52rem",
)

SURFACE = SurfaceStyle(
    name="one_dark",
    shadow_sm="0 1px 3px rgba(0, 0, 0, 0.12)",
    shadow_md="0 4px 8px rgba(0, 0, 0, 0.15)",
    shadow_lg="0 8px 20px rgba(0, 0, 0, 0.18)",
    border_width="1px",
    border_style="solid",
    surface_treatment="flat",
    backdrop_blur="0px",
    noise_opacity=0.0,
)

ICON = IconStyle(
    name="one_dark",
    style="outlined",
    weight="regular",
    size_scale=1.0,
    stroke_width="1.5",
    corner_rounding="2px",
)

ANIMATION = AnimationStyle(
    name="one_dark",
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
    name="one_dark",
    button_hover="lift",
    link_hover="color",
    card_hover="shadow",
    focus_style="ring",
    focus_ring_width="2px",
)

DESIGN_SYSTEM = DesignSystem(
    name="one_dark",
    display_name="One Dark",
    description="Minimalist dev — clean typography, subtle shadows, smooth transitions",
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
    name="one_dark",
    display_name="One Dark",
    description="Atom's iconic warm dark theme with vibrant syntax colors",
    category="developer",
    design_theme="one_dark",
    color_preset="one_dark",
    icon_style=ICON,
    animation_style=ANIMATION,
    pattern_style=PATTERN_MINIMAL,
    interaction_style=INTERACTION,
    illustration_style=ILLUST_LINE,
)
