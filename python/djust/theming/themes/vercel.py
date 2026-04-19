"""Vercel — Monochrome developer-first with single blue accent."""

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
# Colors traced to Vercel's Geist design system:
# - Pure black/white — zero color except one blue accent
# - #0070F3 — the Vercel blue (only color in the entire palette)
# - Hairline borders, no shadows, extreme minimalism

LIGHT = ThemeTokens(
    background=ColorScale(0, 0, 100),  # Pure white
    foreground=ColorScale(0, 0, 9),  # #171717 — near-black
    card=ColorScale(0, 0, 100),  # White cards
    card_foreground=ColorScale(0, 0, 9),
    popover=ColorScale(0, 0, 100),
    popover_foreground=ColorScale(0, 0, 9),
    primary=ColorScale(212, 100, 48),  # #0070F3 — Vercel blue
    primary_foreground=ColorScale(0, 0, 100),
    secondary=ColorScale(0, 0, 96),  # Near-white bg
    secondary_foreground=ColorScale(0, 0, 9),
    muted=ColorScale(0, 0, 96),  # Light gray
    muted_foreground=ColorScale(0, 0, 40),  # Mid gray text
    accent=ColorScale(0, 0, 97),  # Lightest hover
    accent_foreground=ColorScale(0, 0, 9),
    destructive=ColorScale(0, 100, 47),  # Pure red
    destructive_foreground=ColorScale(0, 0, 100),
    success=ColorScale(143, 70, 45),  # Green
    success_foreground=ColorScale(0, 0, 100),
    warning=ColorScale(37, 100, 50),  # Amber
    warning_foreground=ColorScale(0, 0, 9),
    info=ColorScale(212, 100, 48),  # Primary blue
    info_foreground=ColorScale(0, 0, 100),
    link=ColorScale(212, 100, 48),  # Same blue
    link_hover=ColorScale(212, 100, 40),  # Darker blue
    code=ColorScale(0, 0, 96),  # Light gray code bg
    code_foreground=ColorScale(0, 0, 9),  # Dark code text
    selection=ColorScale(212, 100, 90),  # Pale blue selection
    selection_foreground=ColorScale(0, 0, 9),
    brand=ColorScale(212, 100, 48),  # #0070F3 — blue IS the brand
    brand_foreground=ColorScale(0, 0, 100),
    border=ColorScale(0, 0, 92),  # #EAEAEA — hairline
    input=ColorScale(0, 0, 92),
    ring=ColorScale(212, 100, 48),  # Blue focus ring
    surface_1=ColorScale(0, 0, 100),  # Pure white
    surface_2=ColorScale(0, 0, 98),  # Barely gray
    surface_3=ColorScale(0, 0, 96),  # Light gray
)

DARK = ThemeTokens(
    background=ColorScale(0, 0, 0),  # Pure black
    foreground=ColorScale(0, 0, 98),  # #FAFAFA — near-white
    card=ColorScale(0, 0, 7),  # Very dark card
    card_foreground=ColorScale(0, 0, 98),
    popover=ColorScale(0, 0, 7),
    popover_foreground=ColorScale(0, 0, 98),
    primary=ColorScale(212, 100, 48),  # Same blue — pure on black
    primary_foreground=ColorScale(0, 0, 100),
    secondary=ColorScale(0, 0, 10),  # Near-black panel
    secondary_foreground=ColorScale(0, 0, 98),
    muted=ColorScale(0, 0, 14),  # Dark muted
    muted_foreground=ColorScale(0, 0, 63),  # Mid gray text
    accent=ColorScale(0, 0, 10),  # Dark hover
    accent_foreground=ColorScale(0, 0, 98),
    destructive=ColorScale(0, 100, 52),  # Red — slightly brightened
    destructive_foreground=ColorScale(0, 0, 100),
    success=ColorScale(143, 70, 50),  # Green — brightened
    success_foreground=ColorScale(0, 0, 100),
    warning=ColorScale(37, 100, 55),  # Amber — brightened
    warning_foreground=ColorScale(0, 0, 0),
    info=ColorScale(212, 100, 48),  # Blue
    info_foreground=ColorScale(0, 0, 100),
    link=ColorScale(212, 100, 55),  # Blue — brightened for dark
    link_hover=ColorScale(212, 100, 65),  # Lighter blue
    code=ColorScale(0, 0, 7),  # Near-black code bg
    code_foreground=ColorScale(0, 0, 92),  # Light code text
    selection=ColorScale(212, 80, 25),  # Deep blue selection
    selection_foreground=ColorScale(0, 0, 98),
    brand=ColorScale(212, 100, 48),  # Same blue — works on black
    brand_foreground=ColorScale(0, 0, 100),
    border=ColorScale(0, 0, 20),  # #333 — dark hairline
    input=ColorScale(0, 0, 20),
    ring=ColorScale(212, 100, 48),  # Blue focus ring
    surface_1=ColorScale(0, 0, 0),  # Pure black
    surface_2=ColorScale(0, 0, 4),  # Near-black
    surface_3=ColorScale(0, 0, 7),  # Slightly elevated
)

PRESET = ThemePreset(
    name="vercel",
    display_name="Vercel",
    description="Monochrome developer-first — single blue accent, zero shadows",
    light=LIGHT,
    dark=DARK,
    radius=0.375,  # 6px — uniform rounding
    default_mode="light",
)


# =============================================================================
# Design System
# =============================================================================

TYPOGRAPHY = TypographyStyle(
    name="vercel",
    heading_font='"Inter", system-ui, sans-serif',
    body_font='"Inter", system-ui, sans-serif',
    base_size="16px",
    heading_scale=1.25,
    line_height="1.5",
    body_line_height="1.6",
    heading_weight="600",
    section_heading_weight="600",
    body_weight="400",
    letter_spacing="-0.02em",  # Tight — Geist signature
    prose_max_width="44rem",
    badge_radius="9999px",  # Pill badges
)

LAYOUT = LayoutStyle(
    name="vercel",
    space_unit="1rem",
    space_scale=1.5,
    border_radius_sm="6px",  # Uniform 6px
    border_radius_md="6px",
    border_radius_lg="6px",
    button_shape="rounded",
    card_shape="rounded",
    input_shape="rounded",
    container_width="1100px",
    grid_gap="1.5rem",
    section_spacing="5rem",  # Generous whitespace
    hero_padding_top="8rem",
    hero_padding_bottom="5rem",
    hero_line_height="1.15",
    hero_max_width="50rem",
)

SURFACE = SurfaceStyle(
    name="vercel",
    shadow_sm="none",  # NO shadows — Vercel signature
    shadow_md="none",
    shadow_lg="none",
    border_width="1px",
    border_style="solid",
    surface_treatment="flat",
    backdrop_blur="0px",
    noise_opacity=0.0,
)

ICON = IconStyle(
    name="vercel",
    style="outlined",
    weight="regular",
    size_scale=1.0,
    stroke_width="1.5",
    corner_rounding="2px",
)

ANIMATION = AnimationStyle(
    name="vercel",
    entrance_effect="fade",
    exit_effect="fade",
    hover_effect="none",
    hover_scale=1.0,
    hover_translate_y="0px",
    click_effect="none",
    loading_style="spinner",
    transition_style="snappy",
    duration_fast="0.1s",
    duration_normal="0.15s",
    duration_slow="0.25s",
    easing="cubic-bezier(0.4, 0, 0.2, 1)",
)

INTERACTION = InteractionStyle(
    name="vercel",
    button_hover="darken",
    link_hover="underline",
    card_hover="border",  # Border highlight — no shadows
    focus_style="ring",
    focus_ring_width="2px",
)

DESIGN_SYSTEM = DesignSystem(
    name="vercel",
    display_name="Vercel",
    description="Monochrome developer-first — single blue accent, no shadows, hairline borders",
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
    name="vercel",
    display_name="Vercel",
    description="Monochrome developer-first — single blue accent, zero shadows",
    category="professional",
    design_theme="vercel",
    color_preset="vercel",
    icon_style=ICON,
    animation_style=ANIMATION,
    pattern_style=PATTERN_MINIMAL,
    interaction_style=INTERACTION,
    illustration_style=ILLUST_LINE,
)
