"""Docs site — warm editorial dark with rust accent, Geist + JetBrains Mono."""

from ._base import (
    AnimationStyle,
    ColorScale,
    DesignSystem,
    IconStyle,
    ILLUST_LINE,
    InteractionStyle,
    LayoutStyle,
    PATTERN_MINIMAL,
    SurfaceStyle,
    ThemePack,
    ThemePreset,
    ThemeTokens,
    TypographyStyle,
)


# =============================================================================
# Color Preset — Warm editorial dark
# =============================================================================

# Spec: djust-docs-b1.html
# --bg: #11100d  → hsl(40, 30%, 7%)
# --fg: #ebe6d8  → hsl(40, 20%, 92%)
# --accent: #f08646 → hsl(28, 85%, 55%)
# --ink: #1a1410  → hsl(30, 25%, 8%)

DARK = ThemeTokens(
    background=ColorScale(40, 30, 7),
    foreground=ColorScale(40, 20, 92),
    card=ColorScale(40, 20, 9),  # #1a1814  slightly lifted surface
    card_foreground=ColorScale(40, 20, 92),
    popover=ColorScale(40, 20, 9),
    popover_foreground=ColorScale(40, 20, 92),
    primary=ColorScale(28, 85, 55),  # #f08646 rust orange
    primary_foreground=ColorScale(30, 25, 8),  # ink
    secondary=ColorScale(40, 20, 12),
    secondary_foreground=ColorScale(40, 20, 92),
    muted=ColorScale(40, 15, 15),  # dark warm gray
    muted_foreground=ColorScale(40, 15, 50),  # muted text
    accent=ColorScale(28, 85, 55),
    accent_foreground=ColorScale(30, 25, 8),
    destructive=ColorScale(350, 75, 55),
    destructive_foreground=ColorScale(0, 0, 100),
    success=ColorScale(160, 60, 40),
    success_foreground=ColorScale(0, 0, 100),
    warning=ColorScale(38, 90, 50),
    warning_foreground=ColorScale(0, 0, 8),
    info=ColorScale(210, 60, 55),
    info_foreground=ColorScale(0, 0, 100),
    link=ColorScale(28, 85, 55),
    link_hover=ColorScale(28, 85, 65),
    code=ColorScale(40, 20, 12),
    code_foreground=ColorScale(40, 20, 92),
    selection=ColorScale(28, 80, 55),
    selection_foreground=ColorScale(30, 25, 8),
    brand=ColorScale(28, 85, 55),
    brand_foreground=ColorScale(30, 25, 8),
    border=ColorScale(0, 0, 16),  # rgba(255,255,255,0.16)
    input=ColorScale(0, 0, 16),
    ring=ColorScale(28, 85, 55),
    surface_1=ColorScale(40, 30, 7),
    surface_2=ColorScale(40, 20, 9),
    surface_3=ColorScale(40, 20, 12),
)

LIGHT = ThemeTokens(
    background=ColorScale(40, 10, 98),
    foreground=ColorScale(40, 30, 8),
    card=ColorScale(40, 10, 96),
    card_foreground=ColorScale(40, 30, 8),
    popover=ColorScale(40, 10, 96),
    popover_foreground=ColorScale(40, 30, 8),
    primary=ColorScale(28, 85, 50),
    primary_foreground=ColorScale(0, 0, 100),
    secondary=ColorScale(40, 10, 92),
    secondary_foreground=ColorScale(40, 30, 8),
    muted=ColorScale(40, 8, 88),
    muted_foreground=ColorScale(40, 10, 45),
    accent=ColorScale(28, 85, 50),
    accent_foreground=ColorScale(0, 0, 100),
    destructive=ColorScale(350, 75, 55),
    destructive_foreground=ColorScale(0, 0, 100),
    success=ColorScale(160, 60, 40),
    success_foreground=ColorScale(0, 0, 100),
    warning=ColorScale(38, 90, 50),
    warning_foreground=ColorScale(0, 0, 8),
    info=ColorScale(210, 60, 55),
    info_foreground=ColorScale(0, 0, 100),
    link=ColorScale(28, 85, 45),
    link_hover=ColorScale(28, 85, 55),
    code=ColorScale(40, 20, 92),
    code_foreground=ColorScale(40, 30, 8),
    selection=ColorScale(28, 80, 92),
    selection_foreground=ColorScale(28, 85, 8),
    brand=ColorScale(28, 85, 50),
    brand_foreground=ColorScale(0, 0, 100),
    border=ColorScale(40, 15, 80),
    input=ColorScale(40, 15, 80),
    ring=ColorScale(28, 85, 50),
    surface_1=ColorScale(40, 10, 98),
    surface_2=ColorScale(40, 10, 96),
    surface_3=ColorScale(40, 10, 92),
)

PRESET = ThemePreset(
    name="docs",
    display_name="docs.djust.org",
    description="Warm editorial dark — Geist headings, JetBrains Mono labels, rust orange accent",
    light=LIGHT,
    dark=DARK,
    default_mode="dark",
    extra_css_vars={
        # Brand tokens consumed by docs.djust.org Tailwind utilities
        "color-brand-dark": "#11100d",
        "color-brand-panel": "#1a1814",
        "color-brand-rust": "#f08646",
        "color-brand-text": "#ebe6d8",
        "color-brand-muted": "rgba(235,230,216,0.7)",
        "color-brand-border": "rgba(255,255,255,0.16)",
        "color-brand-success": "#10B981",
        "color-brand-danger": "#F43F5E",
    },
    extra_css_vars_light={
        "color-brand-dark": "#faf8f4",
        "color-brand-panel": "#f3f0eb",
        "color-brand-rust": "#e06726",
        "color-brand-text": "#1a1410",
        "color-brand-muted": "rgba(26,20,16,0.65)",
        "color-brand-border": "rgba(26,20,16,0.15)",
    },
)


# =============================================================================
# Design System
# =============================================================================

TYPOGRAPHY = TypographyStyle(
    name="docs",
    heading_font='"Geist", ui-sans-serif, system-ui, -apple-system, sans-serif',
    body_font='"Geist", ui-sans-serif, system-ui, -apple-system, sans-serif',
    base_size="17px",
    heading_scale=1.35,
    line_height="1.5",
    body_line_height="1.65",
    heading_weight="700",
    section_heading_weight="700",
    body_weight="400",
    letter_spacing="-0.02em",
    prose_max_width="72ch",
    badge_radius="0px",
)

LAYOUT = LayoutStyle(
    name="docs",
    space_unit="1rem",
    space_scale=1.5,
    border_radius_sm="0px",
    border_radius_md="0px",
    border_radius_lg="0px",
    button_shape="sharp",
    card_shape="sharp",
    input_shape="sharp",
    container_width="1280px",
    grid_gap="2rem",
    section_spacing="5rem",
    hero_padding_top="6rem",
    hero_padding_bottom="5rem",
    hero_line_height="0.96",
    hero_max_width="60rem",
)

SURFACE = SurfaceStyle(
    name="docs",
    shadow_sm="none",
    shadow_md="none",
    shadow_lg="none",
    border_width="1px",
    border_style="solid",
    surface_treatment="flat",
    backdrop_blur="0px",
    noise_opacity=0.0,
)

ICON = IconStyle(
    name="docs",
    style="outlined",
    weight="regular",
    size_scale=1.0,
    stroke_width="1.5",
    corner_rounding="0px",
)

ANIMATION = AnimationStyle(
    name="docs",
    entrance_effect="fade",
    exit_effect="fade",
    hover_effect="none",
    hover_scale=1.0,
    hover_translate_y="0px",
    click_effect="none",
    loading_style="spinner",
    transition_style="smooth",
    duration_fast="0.15s",
    duration_normal="0.25s",
    duration_slow="0.4s",
    easing="cubic-bezier(0.4, 0, 0.2, 1)",
)

INTERACTION = InteractionStyle(
    name="docs",
    button_hover="color",
    link_hover="color",
    card_hover="none",
    focus_style="ring",
    focus_ring_width="1px",
)

DESIGN_SYSTEM = DesignSystem(
    name="docs",
    display_name="docs.djust.org",
    description="Warm editorial dark — sharp borders, rust orange accent, Geist + JetBrains Mono",
    category="editorial",
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
    name="docs",
    display_name="docs.djust.org",
    description="Warm editorial dark — Geist headings, JetBrains Mono labels, rust orange accent",
    category="editorial",
    design_theme="docs",
    color_preset="docs",
    icon_style=ICON,
    animation_style=ANIMATION,
    pattern_style=PATTERN_MINIMAL,
    interaction_style=INTERACTION,
    illustration_style=ILLUST_LINE,
)
