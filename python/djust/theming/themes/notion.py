"""Notion — Warm minimal workspace with serif headings and generous measure."""

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
# Colors traced to Notion's UI:
# - Warm off-white #FBFBFA background
# - Brown #37352F text (warm near-black)
# - Notion brown #9F6B53 brand accent
# - Serif headings, generous line-height, minimal decoration

LIGHT = ThemeTokens(
    background=ColorScale(60, 11, 98),  # #FBFBFA — warm off-white
    foreground=ColorScale(45, 8, 20),  # #37352F — warm near-black
    card=ColorScale(0, 0, 100),  # Pure white cards
    card_foreground=ColorScale(45, 8, 20),
    popover=ColorScale(0, 0, 100),
    popover_foreground=ColorScale(45, 8, 20),
    primary=ColorScale(210, 77, 51),  # Blue primary
    primary_foreground=ColorScale(0, 0, 100),
    secondary=ColorScale(60, 5, 95),  # Warm subtle bg
    secondary_foreground=ColorScale(45, 8, 20),
    muted=ColorScale(60, 4, 93),  # Warm muted surface
    muted_foreground=ColorScale(45, 2, 46),  # Muted text
    accent=ColorScale(60, 5, 96),  # Warm hover surface
    accent_foreground=ColorScale(45, 8, 20),
    destructive=ColorScale(0, 70, 50),  # Red
    destructive_foreground=ColorScale(0, 0, 100),
    success=ColorScale(120, 50, 40),  # Green
    success_foreground=ColorScale(0, 0, 100),
    warning=ColorScale(30, 89, 45),  # Orange
    warning_foreground=ColorScale(45, 8, 20),
    info=ColorScale(210, 77, 51),  # Primary as info
    info_foreground=ColorScale(0, 0, 100),
    link=ColorScale(210, 77, 48),  # Blue link
    link_hover=ColorScale(210, 77, 40),  # Darker on hover
    code=ColorScale(60, 5, 95),  # Light warm code bg
    code_foreground=ColorScale(45, 8, 20),  # Dark text in code
    selection=ColorScale(201, 47, 88),  # Pale blue selection
    selection_foreground=ColorScale(45, 8, 20),
    brand=ColorScale(19, 31, 47),  # #9F6B53 — Notion brown
    brand_foreground=ColorScale(0, 0, 100),
    border=ColorScale(60, 4, 91),  # #E9E9E7 — warm subtle border
    input=ColorScale(60, 4, 91),
    ring=ColorScale(210, 77, 51),  # Blue focus ring
    surface_1=ColorScale(60, 11, 98),  # Lightest warm
    surface_2=ColorScale(60, 7, 96),  # Mid warm
    surface_3=ColorScale(60, 5, 94),  # Slightly deeper
)

DARK = ThemeTokens(
    background=ColorScale(0, 0, 15),  # Dark neutral
    foreground=ColorScale(60, 5, 90),  # Light warm text
    card=ColorScale(0, 0, 18),  # Slightly lighter card
    card_foreground=ColorScale(60, 5, 90),
    popover=ColorScale(0, 0, 18),
    popover_foreground=ColorScale(60, 5, 90),
    primary=ColorScale(210, 77, 58),  # Blue — brightened for dark
    primary_foreground=ColorScale(0, 0, 100),
    secondary=ColorScale(0, 0, 20),  # Dark panel
    secondary_foreground=ColorScale(60, 5, 90),
    muted=ColorScale(0, 0, 22),  # Dark muted
    muted_foreground=ColorScale(45, 3, 55),  # Muted text on dark
    accent=ColorScale(0, 0, 20),  # Dark hover surface
    accent_foreground=ColorScale(60, 5, 90),
    destructive=ColorScale(0, 70, 55),  # Red — brightened
    destructive_foreground=ColorScale(0, 0, 100),
    success=ColorScale(120, 50, 48),  # Green — brightened
    success_foreground=ColorScale(0, 0, 100),
    warning=ColorScale(30, 89, 52),  # Orange — brightened
    warning_foreground=ColorScale(0, 0, 15),
    info=ColorScale(210, 77, 58),  # Primary
    info_foreground=ColorScale(0, 0, 100),
    link=ColorScale(210, 77, 62),  # Blue — brightened
    link_hover=ColorScale(210, 77, 55),  # Lighter blue
    code=ColorScale(0, 0, 12),  # Dark code bg
    code_foreground=ColorScale(60, 5, 85),  # Light text in code
    selection=ColorScale(210, 40, 25),  # Dark blue selection
    selection_foreground=ColorScale(60, 5, 90),
    brand=ColorScale(19, 31, 55),  # Brown — brightened
    brand_foreground=ColorScale(0, 0, 100),
    border=ColorScale(0, 0, 25),  # Dark border
    input=ColorScale(0, 0, 25),
    ring=ColorScale(210, 77, 58),  # Blue focus ring
    surface_1=ColorScale(0, 0, 12),  # Deepest
    surface_2=ColorScale(0, 0, 15),  # Mid
    surface_3=ColorScale(0, 0, 18),  # Elevated
)

PRESET = ThemePreset(
    name="notion",
    display_name="Notion",
    description="Warm minimal workspace — serif headings, generous measure",
    light=LIGHT,
    dark=DARK,
    radius=0.25,  # 4px — subtle rounding
    default_mode="light",
)


# =============================================================================
# Design System
# =============================================================================

TYPOGRAPHY = TypographyStyle(
    name="notion",
    heading_font="Georgia, 'Noto Serif', serif",
    body_font="system-ui, -apple-system, sans-serif",
    base_size="16px",
    heading_scale=1.25,
    line_height="1.5",
    body_line_height="1.8",  # Very generous — book-like
    heading_weight="700",  # Bold serif headings
    section_heading_weight="700",
    body_weight="400",
    letter_spacing="normal",
    prose_max_width="38rem",  # Narrow, book-like measure
    badge_radius="3px",
)

LAYOUT = LayoutStyle(
    name="notion",
    space_unit="1rem",
    space_scale=1.5,
    border_radius_sm="3px",
    border_radius_md="4px",
    border_radius_lg="6px",
    button_shape="rounded",
    card_shape="rounded",
    input_shape="rounded",
    container_width="900px",  # Narrow container
    grid_gap="1.5rem",
    section_spacing="4rem",
    hero_padding_top="6rem",
    hero_padding_bottom="4rem",
    hero_line_height="1.2",
    hero_max_width="40rem",
)

SURFACE = SurfaceStyle(
    name="notion",
    shadow_sm="0 1px 2px rgba(0, 0, 0, 0.04)",  # Very subtle
    shadow_md="0 3px 6px rgba(0, 0, 0, 0.06)",
    shadow_lg="0 8px 16px rgba(0, 0, 0, 0.08)",
    border_width="1px",
    border_style="solid",
    surface_treatment="flat",
    backdrop_blur="0px",
    noise_opacity=0.0,
)

ICON = IconStyle(
    name="notion",
    style="outlined",
    weight="regular",
    size_scale=1.0,
    stroke_width="1.5",
    corner_rounding="2px",
)

ANIMATION = AnimationStyle(
    name="notion",
    entrance_effect="fade",
    exit_effect="fade",
    hover_effect="lift",
    hover_scale=1.0,
    hover_translate_y="-1px",
    click_effect="pulse",
    loading_style="spinner",
    transition_style="gentle",
    duration_fast="0.2s",
    duration_normal="0.3s",
    duration_slow="0.4s",
    easing="ease-out",
)

INTERACTION = InteractionStyle(
    name="notion",
    button_hover="lift",
    link_hover="underline",
    card_hover="shadow",
    focus_style="ring",
    focus_ring_width="2px",
)

DESIGN_SYSTEM = DesignSystem(
    name="notion",
    display_name="Notion",
    description="Warm minimal workspace — serif headings, generous measure, minimal decoration",
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
    name="notion",
    display_name="Notion",
    description="Warm minimal workspace — serif headings, generous measure",
    category="professional",
    design_theme="notion",
    color_preset="notion",
    icon_style=ICON,
    animation_style=ANIMATION,
    pattern_style=PATTERN_MINIMAL,
    interaction_style=INTERACTION,
    illustration_style=ILLUST_LINE,
)
