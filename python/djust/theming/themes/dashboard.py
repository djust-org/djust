"""Dashboard — Data-dense, small type, neutral grays, single blue accent."""

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
# Neutral cool grays with a single dashboard blue accent.
# Designed for data-dense admin panels, analytics, monitoring.

LIGHT = ThemeTokens(
    background=ColorScale(220, 10, 98),  # Cool gray
    foreground=ColorScale(220, 10, 12),  # Dark gray
    card=ColorScale(0, 0, 100),  # White cards
    card_foreground=ColorScale(220, 10, 12),
    popover=ColorScale(0, 0, 100),
    popover_foreground=ColorScale(220, 10, 12),
    primary=ColorScale(215, 80, 50),  # Dashboard blue
    primary_foreground=ColorScale(0, 0, 100),
    secondary=ColorScale(220, 8, 95),  # Very pale gray
    secondary_foreground=ColorScale(220, 10, 12),
    muted=ColorScale(220, 8, 92),  # Cool muted
    muted_foreground=ColorScale(220, 6, 45),  # Mid gray text
    accent=ColorScale(220, 8, 95),  # Subtle hover surface
    accent_foreground=ColorScale(220, 10, 12),
    destructive=ColorScale(0, 65, 50),  # Alert red
    destructive_foreground=ColorScale(0, 0, 100),
    success=ColorScale(145, 60, 40),  # Data green
    success_foreground=ColorScale(0, 0, 100),
    warning=ColorScale(38, 85, 52),  # Amber
    warning_foreground=ColorScale(220, 10, 12),
    info=ColorScale(215, 80, 50),  # Same blue
    info_foreground=ColorScale(0, 0, 100),
    link=ColorScale(215, 80, 45),  # Blue links
    link_hover=ColorScale(215, 80, 38),  # Darker blue
    code=ColorScale(220, 10, 10),  # Dark code bg
    code_foreground=ColorScale(215, 60, 70),  # Light blue code text
    selection=ColorScale(215, 60, 90),  # Pale blue selection
    selection_foreground=ColorScale(220, 10, 12),
    brand=ColorScale(215, 80, 50),  # Dashboard blue
    brand_foreground=ColorScale(0, 0, 100),
    border=ColorScale(220, 8, 87),  # Subtle border
    input=ColorScale(220, 6, 90),  # Light input
    ring=ColorScale(215, 80, 50),  # Blue focus ring
    surface_1=ColorScale(220, 10, 98),  # Lightest
    surface_2=ColorScale(220, 8, 96),  # Mid
    surface_3=ColorScale(220, 6, 93),  # Deeper
)

DARK = ThemeTokens(
    background=ColorScale(220, 15, 8),  # Very dark gray
    foreground=ColorScale(220, 5, 85),  # Light gray
    card=ColorScale(220, 12, 11),  # Dark card
    card_foreground=ColorScale(220, 5, 85),
    popover=ColorScale(220, 12, 11),
    popover_foreground=ColorScale(220, 5, 85),
    primary=ColorScale(215, 80, 55),  # Blue brightened
    primary_foreground=ColorScale(0, 0, 100),
    secondary=ColorScale(220, 10, 13),  # Dark panel
    secondary_foreground=ColorScale(220, 5, 85),
    muted=ColorScale(220, 8, 18),  # Dark muted
    muted_foreground=ColorScale(220, 5, 52),  # Muted text
    accent=ColorScale(220, 10, 13),  # Dark hover
    accent_foreground=ColorScale(220, 5, 85),
    destructive=ColorScale(0, 65, 55),  # Red brightened
    destructive_foreground=ColorScale(0, 0, 100),
    success=ColorScale(145, 60, 48),  # Green brightened
    success_foreground=ColorScale(0, 0, 100),
    warning=ColorScale(38, 85, 58),  # Amber brightened
    warning_foreground=ColorScale(220, 15, 8),
    info=ColorScale(215, 80, 55),  # Blue brightened
    info_foreground=ColorScale(0, 0, 100),
    link=ColorScale(215, 80, 60),  # Blue brightened
    link_hover=ColorScale(215, 80, 70),  # Lighter blue
    code=ColorScale(220, 15, 5),  # Deepest dark code
    code_foreground=ColorScale(215, 60, 65),  # Blue code text
    selection=ColorScale(215, 60, 20),  # Deep blue selection
    selection_foreground=ColorScale(220, 5, 85),
    brand=ColorScale(215, 80, 55),  # Blue brightened
    brand_foreground=ColorScale(0, 0, 100),
    border=ColorScale(220, 8, 18),  # Dark border
    input=ColorScale(220, 8, 15),  # Dark input
    ring=ColorScale(215, 80, 55),  # Blue focus ring
    surface_1=ColorScale(220, 15, 6),  # Deepest
    surface_2=ColorScale(220, 12, 9),  # Mid dark
    surface_3=ColorScale(220, 10, 12),  # Elevated
)

PRESET = ThemePreset(
    name="dashboard",
    display_name="Dashboard",
    description="Data-dense, small type, neutral grays, single blue accent",
    light=LIGHT,
    dark=DARK,
    radius=0.25,  # 4px — compact rounding
)


# =============================================================================
# Design System
# =============================================================================

TYPOGRAPHY = TypographyStyle(
    name="dashboard",
    heading_font='"Inter", system-ui, sans-serif',
    body_font='"Inter", system-ui, sans-serif',
    base_size="13px",  # Small — data-dense
    heading_scale=1.15,  # Flat — data UIs need uniform sizing
    line_height="1.4",  # Tight
    body_line_height="1.4",
    heading_weight="600",
    section_heading_weight="600",
    body_weight="400",
    letter_spacing="-0.01em",
    prose_max_width="44rem",
    badge_radius="3px",  # Not pill — data labels
)

LAYOUT = LayoutStyle(
    name="dashboard",
    space_unit="1rem",
    space_scale=1.25,  # Compact spacing
    border_radius_sm="3px",
    border_radius_md="4px",
    border_radius_lg="6px",
    button_shape="rounded",
    card_shape="rounded",
    input_shape="rounded",
    container_width="1400px",  # Wide — dashboard
    grid_gap="0.75rem",  # Tight grid
    section_spacing="2rem",  # Compact sections
    hero_padding_top="3rem",
    hero_padding_bottom="2rem",
    hero_line_height="1.2",
    hero_max_width="56rem",
)

SURFACE = SurfaceStyle(
    name="dashboard",
    shadow_sm="0 1px 2px 0 rgba(0, 0, 0, 0.04)",
    shadow_md="0 2px 4px -1px rgba(0, 0, 0, 0.06)",
    shadow_lg="0 4px 8px -2px rgba(0, 0, 0, 0.08)",
    border_width="1px",
    border_style="solid",
    surface_treatment="flat",
    backdrop_blur="0px",
    noise_opacity=0.0,
)

ICON = IconStyle(
    name="dashboard",
    style="outlined",
    weight="regular",
    size_scale=0.9,  # Slightly smaller — dense UI
    stroke_width="1.5",
    corner_rounding="1px",
)

ANIMATION = AnimationStyle(
    name="dashboard",
    entrance_effect="none",  # Instant — data UIs need speed
    exit_effect="none",
    hover_effect="none",
    hover_scale=1.0,
    hover_translate_y="0px",
    click_effect="none",
    loading_style="spinner",
    transition_style="instant",
    duration_fast="0.05s",
    duration_normal="0.1s",  # Snappy
    duration_slow="0.15s",
    easing="cubic-bezier(0.4, 0, 1, 1)",
)

INTERACTION = InteractionStyle(
    name="dashboard",
    button_hover="color",
    link_hover="color",
    card_hover="shadow",
    focus_style="ring",
    focus_ring_width="2px",
)

DESIGN_SYSTEM = DesignSystem(
    name="dashboard",
    display_name="Dashboard",
    description="Data-dense — small type, neutral grays, compact layout, single blue accent",
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
    name="dashboard",
    display_name="Dashboard",
    description="Data-dense, small type, neutral grays, single blue accent",
    category="professional",
    design_theme="dashboard",
    color_preset="dashboard",
    icon_style=ICON,
    animation_style=ANIMATION,
    pattern_style=PATTERN_MINIMAL,
    interaction_style=INTERACTION,
    illustration_style=ILLUST_LINE,
)
