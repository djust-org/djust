"""Medical — Clean clinical, calming blue, generous readability, form-focused."""

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
# Clean clinical palette with calming blue primary and teal brand.
# Designed for medical forms, patient portals, healthcare dashboards.

LIGHT = ThemeTokens(
    background=ColorScale(200, 20, 98),  # Cool white
    foreground=ColorScale(210, 15, 15),  # Dark gray
    card=ColorScale(0, 0, 100),  # White cards
    card_foreground=ColorScale(210, 15, 15),
    popover=ColorScale(0, 0, 100),
    popover_foreground=ColorScale(210, 15, 15),
    primary=ColorScale(200, 70, 50),  # Calming blue
    primary_foreground=ColorScale(0, 0, 100),
    secondary=ColorScale(200, 15, 95),  # Very pale cool bg
    secondary_foreground=ColorScale(210, 15, 15),
    muted=ColorScale(200, 12, 92),  # Cool gray muted
    muted_foreground=ColorScale(210, 10, 45),  # Mid gray text
    accent=ColorScale(200, 15, 95),  # Cool hover surface
    accent_foreground=ColorScale(210, 15, 15),
    destructive=ColorScale(0, 60, 50),  # Muted red
    destructive_foreground=ColorScale(0, 0, 100),
    success=ColorScale(145, 60, 40),  # Medical green
    success_foreground=ColorScale(0, 0, 100),
    warning=ColorScale(40, 80, 50),  # Soft amber
    warning_foreground=ColorScale(210, 15, 15),
    info=ColorScale(200, 70, 50),  # Same calming blue
    info_foreground=ColorScale(0, 0, 100),
    link=ColorScale(200, 70, 45),  # Blue links
    link_hover=ColorScale(200, 70, 35),  # Darker blue hover
    code=ColorScale(210, 15, 12),  # Dark code bg
    code_foreground=ColorScale(200, 50, 75),  # Light blue code text
    selection=ColorScale(200, 50, 90),  # Pale blue selection
    selection_foreground=ColorScale(210, 15, 15),
    brand=ColorScale(175, 50, 45),  # Teal
    brand_foreground=ColorScale(0, 0, 100),
    border=ColorScale(200, 15, 87),  # Cool gray border
    input=ColorScale(200, 12, 90),  # Cool gray input
    ring=ColorScale(200, 70, 50),  # Blue focus ring
    surface_1=ColorScale(200, 20, 98),  # Cool white
    surface_2=ColorScale(200, 15, 96),  # Slightly deeper
    surface_3=ColorScale(200, 12, 93),  # Even deeper
)

DARK = ThemeTokens(
    background=ColorScale(210, 20, 12),  # Dark blue-gray
    foreground=ColorScale(200, 10, 90),  # Cool white
    card=ColorScale(210, 18, 15),  # Dark card
    card_foreground=ColorScale(200, 10, 90),
    popover=ColorScale(210, 18, 15),
    popover_foreground=ColorScale(200, 10, 90),
    primary=ColorScale(200, 70, 58),  # Blue brightened
    primary_foreground=ColorScale(0, 0, 100),
    secondary=ColorScale(210, 15, 18),  # Dark panel
    secondary_foreground=ColorScale(200, 10, 90),
    muted=ColorScale(210, 12, 22),  # Dark muted
    muted_foreground=ColorScale(200, 8, 55),  # Muted text
    accent=ColorScale(210, 15, 18),  # Dark hover
    accent_foreground=ColorScale(200, 10, 90),
    destructive=ColorScale(0, 60, 55),  # Red brightened
    destructive_foreground=ColorScale(0, 0, 100),
    success=ColorScale(145, 60, 48),  # Green brightened
    success_foreground=ColorScale(0, 0, 100),
    warning=ColorScale(40, 80, 55),  # Amber brightened
    warning_foreground=ColorScale(210, 15, 12),
    info=ColorScale(200, 70, 58),  # Blue brightened
    info_foreground=ColorScale(0, 0, 100),
    link=ColorScale(200, 70, 62),  # Blue brightened
    link_hover=ColorScale(200, 70, 72),  # Lighter blue
    code=ColorScale(210, 20, 8),  # Deepest dark code
    code_foreground=ColorScale(175, 50, 60),  # Teal code text
    selection=ColorScale(200, 50, 22),  # Deep blue selection
    selection_foreground=ColorScale(200, 10, 90),
    brand=ColorScale(175, 50, 52),  # Teal brightened
    brand_foreground=ColorScale(0, 0, 100),
    border=ColorScale(210, 15, 22),  # Dark border
    input=ColorScale(210, 12, 18),  # Dark input
    ring=ColorScale(200, 70, 58),  # Blue focus ring
    surface_1=ColorScale(210, 20, 10),  # Deepest
    surface_2=ColorScale(210, 18, 13),  # Mid dark
    surface_3=ColorScale(210, 16, 16),  # Elevated
)

PRESET = ThemePreset(
    name="medical",
    display_name="Medical",
    description="Clean clinical — calming blue, generous readability, form-focused",
    light=LIGHT,
    dark=DARK,
    radius=0.375,  # 6px — subtle rounding
)


# =============================================================================
# Design System
# =============================================================================

TYPOGRAPHY = TypographyStyle(
    name="medical",
    heading_font="system-ui, sans-serif",
    body_font="system-ui, sans-serif",
    base_size="16px",
    heading_scale=1.25,
    line_height="1.5",
    body_line_height="1.7",  # Generous for readability
    heading_weight="600",
    section_heading_weight="600",
    body_weight="400",
    letter_spacing="0em",  # No extra tracking
    prose_max_width="44rem",  # Wide prose for forms
    badge_radius="9999px",  # Pill badges
)

LAYOUT = LayoutStyle(
    name="medical",
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
    section_spacing="4rem",
    hero_padding_top="6rem",
    hero_padding_bottom="4rem",
    hero_line_height="1.2",
    hero_max_width="50rem",
)

SURFACE = SurfaceStyle(
    name="medical",
    shadow_sm="0 1px 3px 0 rgba(0, 0, 0, 0.06)",
    shadow_md="0 4px 8px -1px rgba(0, 0, 0, 0.08)",
    shadow_lg="0 10px 20px -3px rgba(0, 0, 0, 0.1)",
    border_width="1px",
    border_style="solid",
    surface_treatment="flat",
    backdrop_blur="0px",
    noise_opacity=0.0,
)

ICON = IconStyle(
    name="medical",
    style="outlined",
    weight="regular",
    size_scale=1.0,
    stroke_width="1.5",
    corner_rounding="2px",
)

ANIMATION = AnimationStyle(
    name="medical",
    entrance_effect="fade",
    exit_effect="fade",
    hover_effect="none",
    hover_scale=1.0,
    hover_translate_y="0px",
    click_effect="none",
    loading_style="spinner",
    transition_style="smooth",
    duration_fast="0.1s",
    duration_normal="0.2s",
    duration_slow="0.3s",
    easing="cubic-bezier(0.4, 0, 0.2, 1)",
)

INTERACTION = InteractionStyle(
    name="medical",
    button_hover="color",
    link_hover="color",
    card_hover="shadow",
    focus_style="ring",
    focus_ring_width="2px",
)

DESIGN_SYSTEM = DesignSystem(
    name="medical",
    display_name="Medical",
    description="Clean clinical — calming blue, generous readability, form-focused",
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
    name="medical",
    display_name="Medical",
    description="Clean clinical — calming blue, generous readability, form-focused",
    category="professional",
    design_theme="medical",
    color_preset="medical",
    icon_style=ICON,
    animation_style=ANIMATION,
    pattern_style=PATTERN_MINIMAL,
    interaction_style=INTERACTION,
    illustration_style=ILLUST_LINE,
)
