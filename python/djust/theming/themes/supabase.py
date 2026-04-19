"""Supabase — Dark emerald developer platform aesthetic."""

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
# Colors from Supabase design system:
# - Background #171717 (neutral dark), Foreground #ededed
# - Green #3ecf8e (primary), Emerald #059669 (brand)
# - Border #282828, Muted #a1a1aa

LIGHT = ThemeTokens(
    background=ColorScale(0, 0, 100),  # Pure white
    foreground=ColorScale(0, 0, 10),  # Near-black
    card=ColorScale(0, 0, 100),
    card_foreground=ColorScale(0, 0, 10),
    popover=ColorScale(0, 0, 100),
    popover_foreground=ColorScale(0, 0, 10),
    primary=ColorScale(153, 60, 53),  # Green #3ecf8e
    primary_foreground=ColorScale(0, 0, 100),
    secondary=ColorScale(0, 0, 96),
    secondary_foreground=ColorScale(0, 0, 10),
    muted=ColorScale(0, 0, 91),
    muted_foreground=ColorScale(0, 0, 45),
    accent=ColorScale(0, 0, 96),  # Subtle hover
    accent_foreground=ColorScale(0, 0, 10),
    destructive=ColorScale(0, 70, 55),  # Red
    destructive_foreground=ColorScale(0, 0, 100),
    success=ColorScale(160, 84, 39),  # Emerald #059669
    success_foreground=ColorScale(0, 0, 100),
    warning=ColorScale(40, 90, 50),  # Amber
    warning_foreground=ColorScale(0, 0, 10),
    info=ColorScale(153, 60, 53),  # Green doubles as info
    info_foreground=ColorScale(0, 0, 100),
    link=ColorScale(153, 60, 53),  # Green
    link_hover=ColorScale(153, 60, 43),
    code=ColorScale(0, 0, 9),  # Dark code bg
    code_foreground=ColorScale(153, 60, 63),  # Green code text
    selection=ColorScale(153, 50, 90),  # Pale green selection
    selection_foreground=ColorScale(0, 0, 10),
    brand=ColorScale(160, 84, 39),  # Emerald
    brand_foreground=ColorScale(0, 0, 100),
    border=ColorScale(0, 0, 89),
    input=ColorScale(0, 0, 93),
    ring=ColorScale(153, 60, 53),
    surface_1=ColorScale(0, 0, 100),
    surface_2=ColorScale(0, 0, 97),
    surface_3=ColorScale(0, 0, 94),
)

DARK = ThemeTokens(
    background=ColorScale(0, 0, 9),  # #171717
    foreground=ColorScale(0, 0, 93),  # #ededed
    card=ColorScale(0, 0, 12),
    card_foreground=ColorScale(0, 0, 93),
    popover=ColorScale(0, 0, 12),
    popover_foreground=ColorScale(0, 0, 93),
    primary=ColorScale(153, 60, 53),  # Green #3ecf8e
    primary_foreground=ColorScale(0, 0, 9),
    secondary=ColorScale(0, 0, 15),
    secondary_foreground=ColorScale(0, 0, 93),
    muted=ColorScale(0, 0, 18),
    muted_foreground=ColorScale(0, 0, 55),
    accent=ColorScale(0, 0, 15),  # Subtle dark hover
    accent_foreground=ColorScale(0, 0, 93),
    destructive=ColorScale(0, 70, 55),  # Red
    destructive_foreground=ColorScale(0, 0, 100),
    success=ColorScale(160, 84, 39),  # Emerald
    success_foreground=ColorScale(0, 0, 100),
    warning=ColorScale(40, 90, 50),  # Amber
    warning_foreground=ColorScale(0, 0, 9),
    info=ColorScale(153, 60, 53),  # Green
    info_foreground=ColorScale(0, 0, 9),
    link=ColorScale(153, 60, 63),  # Green brightened
    link_hover=ColorScale(153, 60, 72),
    code=ColorScale(0, 0, 6),  # Very dark code bg
    code_foreground=ColorScale(153, 60, 63),  # Green code text
    selection=ColorScale(153, 40, 18),  # Dark green selection
    selection_foreground=ColorScale(0, 0, 93),
    brand=ColorScale(160, 84, 39),  # Emerald
    brand_foreground=ColorScale(0, 0, 100),
    border=ColorScale(0, 0, 18),  # #282828
    input=ColorScale(0, 0, 18),
    ring=ColorScale(153, 60, 53),
    surface_1=ColorScale(0, 0, 6),
    surface_2=ColorScale(0, 0, 9),
    surface_3=ColorScale(0, 0, 13),
)

PRESET = ThemePreset(
    name="supabase",
    display_name="Supabase",
    description="Dark emerald developer platform aesthetic",
    light=LIGHT,
    dark=DARK,
    radius=0.375,
    default_mode="dark",
)


# =============================================================================
# Design System
# =============================================================================

TYPOGRAPHY = TypographyStyle(
    name="supabase",
    heading_font='"Inter", system-ui, sans-serif',
    body_font='"Inter", system-ui, sans-serif',
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
    name="supabase",
    space_unit="1rem",
    space_scale=1.5,
    border_radius_sm="4px",
    border_radius_md="6px",
    border_radius_lg="8px",
    button_shape="rounded",
    card_shape="rounded",
    input_shape="rounded",
    container_width="1200px",
    grid_gap="1.5rem",
    section_spacing="5rem",
    hero_padding_top="8rem",
    hero_padding_bottom="5rem",
    hero_line_height="1.1",
    hero_max_width="54rem",
)

SURFACE = SurfaceStyle(
    name="supabase",
    shadow_sm="0 1px 2px rgba(0, 0, 0, 0.1)",
    shadow_md="0 4px 8px rgba(0, 0, 0, 0.12)",
    shadow_lg="0 8px 20px rgba(0, 0, 0, 0.15)",
    border_width="1px",
    border_style="solid",
    surface_treatment="flat",
    backdrop_blur="0px",
    noise_opacity=0.0,
)

ICON = IconStyle(
    name="supabase",
    style="outlined",
    weight="regular",
    size_scale=1.0,
    stroke_width="1.5",
    corner_rounding="2px",
)

ANIMATION = AnimationStyle(
    name="supabase",
    entrance_effect="fade",
    exit_effect="fade",
    hover_effect="lift",
    hover_scale=1.0,
    hover_translate_y="-1px",
    click_effect="none",
    loading_style="spinner",
    transition_style="smooth",
    duration_fast="0.12s",
    duration_normal="0.2s",
    duration_slow="0.3s",
    easing="cubic-bezier(0.4, 0, 0.2, 1)",
)

INTERACTION = InteractionStyle(
    name="supabase",
    button_hover="lift",
    link_hover="color",
    card_hover="shadow",
    focus_style="ring",
    focus_ring_width="2px",
)

DESIGN_SYSTEM = DesignSystem(
    name="supabase",
    display_name="Supabase",
    description="Dev platform — semibold type, emerald accents, clean dark surfaces",
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
    name="supabase",
    display_name="Supabase",
    description="Dark emerald developer platform aesthetic",
    category="professional",
    design_theme="supabase",
    color_preset="supabase",
    icon_style=ICON,
    animation_style=ANIMATION,
    pattern_style=PATTERN_MINIMAL,
    interaction_style=INTERACTION,
    illustration_style=ILLUST_LINE,
)
