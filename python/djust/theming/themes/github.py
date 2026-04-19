"""GitHub — Developer-standard Primer design with cool blues."""

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
# Colors traced to GitHub's Primer design system:
# - #F6F8FA — the signature cool gray background
# - #1F2328 — near-black text
# - #0969DA — Primer blue for links and actions
# - #8250DF — GitHub purple for brand/sponsor/actions
# - #0D1117 — dark mode background

LIGHT = ThemeTokens(
    background=ColorScale(210, 29, 97),  # #F6F8FA — cool gray
    foreground=ColorScale(213, 13, 14),  # #1F2328 — near-black
    card=ColorScale(0, 0, 100),  # White cards
    card_foreground=ColorScale(213, 13, 14),
    popover=ColorScale(0, 0, 100),
    popover_foreground=ColorScale(213, 13, 14),
    primary=ColorScale(212, 92, 45),  # #0969DA — Primer blue
    primary_foreground=ColorScale(0, 0, 100),
    secondary=ColorScale(210, 18, 95),  # Subtle cool bg
    secondary_foreground=ColorScale(213, 13, 14),
    muted=ColorScale(210, 18, 93),  # Cool muted surface
    muted_foreground=ColorScale(212, 8, 43),  # #656D76 — muted text
    accent=ColorScale(210, 18, 96),  # Cool hover surface
    accent_foreground=ColorScale(213, 13, 14),
    destructive=ColorScale(356, 72, 47),  # GitHub red
    destructive_foreground=ColorScale(0, 0, 100),
    success=ColorScale(137, 66, 30),  # GitHub green
    success_foreground=ColorScale(0, 0, 100),
    warning=ColorScale(42, 100, 37),  # GitHub amber
    warning_foreground=ColorScale(213, 13, 14),
    info=ColorScale(212, 92, 45),  # Primary as info
    info_foreground=ColorScale(0, 0, 100),
    link=ColorScale(212, 92, 42),  # Primer link blue
    link_hover=ColorScale(212, 92, 35),  # Darker on hover
    code=ColorScale(210, 14, 93),  # Subtle code bg
    code_foreground=ColorScale(213, 13, 14),  # Dark code text
    selection=ColorScale(212, 80, 88),  # Pale blue selection
    selection_foreground=ColorScale(213, 13, 14),
    brand=ColorScale(261, 69, 59),  # #8250DF — GitHub purple
    brand_foreground=ColorScale(0, 0, 100),
    border=ColorScale(210, 18, 84),  # #D0D7DE — cool border
    input=ColorScale(210, 18, 84),
    ring=ColorScale(212, 92, 45),  # Primer blue ring
    surface_1=ColorScale(210, 29, 97),  # Lightest cool
    surface_2=ColorScale(210, 22, 95),  # Mid cool
    surface_3=ColorScale(210, 18, 93),  # Slightly deeper
)

DARK = ThemeTokens(
    background=ColorScale(215, 14, 11),  # #0D1117 — dark mode bg
    foreground=ColorScale(210, 10, 85),  # Light text
    card=ColorScale(215, 14, 14),  # Slightly lighter card
    card_foreground=ColorScale(210, 10, 85),
    popover=ColorScale(215, 14, 14),
    popover_foreground=ColorScale(210, 10, 85),
    primary=ColorScale(212, 92, 55),  # Primer blue — brightened
    primary_foreground=ColorScale(0, 0, 100),
    secondary=ColorScale(215, 10, 16),  # Dark panel
    secondary_foreground=ColorScale(210, 10, 85),
    muted=ColorScale(215, 10, 18),  # Dark muted
    muted_foreground=ColorScale(210, 10, 55),  # Muted text on dark
    accent=ColorScale(215, 10, 16),  # Dark hover surface
    accent_foreground=ColorScale(210, 10, 85),
    destructive=ColorScale(356, 72, 55),  # Red — brightened
    destructive_foreground=ColorScale(0, 0, 100),
    success=ColorScale(137, 66, 38),  # Green — brightened
    success_foreground=ColorScale(0, 0, 100),
    warning=ColorScale(42, 100, 45),  # Amber — brightened
    warning_foreground=ColorScale(215, 14, 11),
    info=ColorScale(212, 92, 55),  # Primary
    info_foreground=ColorScale(0, 0, 100),
    link=ColorScale(212, 92, 58),  # Blue — brightened
    link_hover=ColorScale(212, 92, 65),  # Lighter blue
    code=ColorScale(215, 14, 11),  # Dark code bg (same as bg)
    code_foreground=ColorScale(210, 10, 85),  # Light code text
    selection=ColorScale(212, 60, 25),  # Deep blue selection
    selection_foreground=ColorScale(210, 10, 85),
    brand=ColorScale(261, 69, 65),  # Purple — brightened
    brand_foreground=ColorScale(0, 0, 100),
    border=ColorScale(215, 10, 22),  # Dark border
    input=ColorScale(215, 10, 22),
    ring=ColorScale(212, 92, 55),  # Blue focus ring
    surface_1=ColorScale(215, 14, 8),  # Deepest
    surface_2=ColorScale(215, 14, 11),  # Mid
    surface_3=ColorScale(215, 14, 14),  # Elevated
)

PRESET = ThemePreset(
    name="github",
    display_name="GitHub",
    description="Developer-standard Primer design — cool blues, familiar density",
    light=LIGHT,
    dark=DARK,
    radius=0.375,  # 6px — standard rounding
    default_mode="light",
)


# =============================================================================
# Design System
# =============================================================================

TYPOGRAPHY = TypographyStyle(
    name="github",
    heading_font="-apple-system, BlinkMacSystemFont, 'Segoe UI', system-ui, sans-serif",
    body_font="-apple-system, BlinkMacSystemFont, 'Segoe UI', system-ui, sans-serif",
    base_size="16px",
    heading_scale=1.25,
    line_height="1.5",
    body_line_height="1.5",
    heading_weight="600",  # Semibold headings
    section_heading_weight="600",
    body_weight="400",
    letter_spacing="normal",
    prose_max_width="44rem",
    badge_radius="9999px",  # Pill badges like GitHub labels
)

LAYOUT = LayoutStyle(
    name="github",
    space_unit="1rem",
    space_scale=1.5,
    border_radius_sm="6px",  # Uniform 6px
    border_radius_md="6px",
    border_radius_lg="6px",
    button_shape="rounded",
    card_shape="rounded",
    input_shape="rounded",
    container_width="1280px",  # Wide — GitHub's signature
    grid_gap="1rem",  # Tighter grid
    section_spacing="3rem",  # Compact sections
    hero_padding_top="5rem",
    hero_padding_bottom="3rem",
    hero_line_height="1.2",
    hero_max_width="52rem",
)

SURFACE = SurfaceStyle(
    name="github",
    shadow_sm="0 1px 0 rgba(27, 31, 36, 0.04)",  # Subtle bottom shadow
    shadow_md="0 3px 6px rgba(140, 149, 159, 0.15)",  # Standard GitHub shadow
    shadow_lg="0 8px 24px rgba(140, 149, 159, 0.2)",  # Elevated shadow
    border_width="1px",
    border_style="solid",
    surface_treatment="flat",
    backdrop_blur="0px",
    noise_opacity=0.0,
)

ICON = IconStyle(
    name="github",
    style="outlined",
    weight="regular",
    size_scale=1.0,
    stroke_width="2",  # Slightly heavier — Octicons style
    corner_rounding="2px",
)

ANIMATION = AnimationStyle(
    name="github",
    entrance_effect="fade",
    exit_effect="fade",
    hover_effect="none",
    hover_scale=1.0,
    hover_translate_y="0px",
    click_effect="none",
    loading_style="spinner",
    transition_style="smooth",
    duration_fast="0.15s",
    duration_normal="0.2s",
    duration_slow="0.3s",
    easing="ease",
)

INTERACTION = InteractionStyle(
    name="github",
    button_hover="darken",
    link_hover="underline",
    card_hover="shadow",
    focus_style="ring",
    focus_ring_width="2px",
)

DESIGN_SYSTEM = DesignSystem(
    name="github",
    display_name="GitHub",
    description="Developer-standard Primer design — cool blues, readable density, familiar UI",
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
    name="github",
    display_name="GitHub",
    description="Developer-standard Primer design — cool blues, familiar density",
    category="professional",
    design_theme="github",
    color_preset="github",
    icon_style=ICON,
    animation_style=ANIMATION,
    pattern_style=PATTERN_MINIMAL,
    interaction_style=INTERACTION,
    illustration_style=ILLUST_LINE,
)
