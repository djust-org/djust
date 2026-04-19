"""Handcraft — Warm earth tones, organic shapes, textured surfaces, artisan feel."""

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
    PATTERN_NOISE,
    ILLUST_HAND_DRAWN,
)


# =============================================================================
# Color Preset
# =============================================================================
# Handcraft palette: warm linen/parchment, terracotta primary, sage green success,
# mustard warning, slate blue info. Everything warm, nothing cold.

LIGHT = ThemeTokens(
    background=ColorScale(33, 47, 96),  # #FAF6F1 — linen
    foreground=ColorScale(27, 20, 20),  # #3D3229 — clay brown text
    card=ColorScale(0, 0, 100),  # White cards (clean canvas)
    card_foreground=ColorScale(27, 20, 20),
    popover=ColorScale(0, 0, 100),
    popover_foreground=ColorScale(27, 20, 20),
    primary=ColorScale(13, 51, 53),  # #C4654A — terracotta
    primary_foreground=ColorScale(0, 0, 100),
    secondary=ColorScale(36, 38, 92),  # #F3EDE4 — parchment
    secondary_foreground=ColorScale(27, 20, 20),
    muted=ColorScale(36, 30, 90),  # Warm muted
    muted_foreground=ColorScale(30, 9, 48),  # Warm gray text
    accent=ColorScale(36, 30, 94),  # Warm hover surface
    accent_foreground=ColorScale(27, 20, 20),
    destructive=ColorScale(0, 55, 50),  # Warm red
    destructive_foreground=ColorScale(0, 0, 100),
    success=ColorScale(127, 16, 55),  # #7A9E7E — sage green
    success_foreground=ColorScale(0, 0, 100),
    warning=ColorScale(42, 63, 55),  # #D4A843 — mustard
    warning_foreground=ColorScale(27, 20, 20),
    info=ColorScale(206, 24, 53),  # #6B8BA4 — slate blue
    info_foreground=ColorScale(0, 0, 100),
    link=ColorScale(13, 51, 48),  # Darker terracotta links
    link_hover=ColorScale(13, 51, 38),  # Deep terracotta hover
    code=ColorScale(33, 27, 13),  # Dark earth code bg
    code_foreground=ColorScale(42, 63, 65),  # Mustard code text
    selection=ColorScale(42, 63, 85),  # Pale mustard selection
    selection_foreground=ColorScale(27, 20, 20),
    brand=ColorScale(127, 16, 55),  # Sage green — the craft/nature identity
    brand_foreground=ColorScale(0, 0, 100),
    border=ColorScale(33, 21, 81),  # #D9D0C5 — earth border
    input=ColorScale(36, 25, 92),  # Light parchment input
    ring=ColorScale(13, 51, 53),  # Terracotta focus ring
    surface_1=ColorScale(33, 47, 96),  # Linen
    surface_2=ColorScale(36, 38, 93),  # Parchment
    surface_3=ColorScale(36, 30, 90),  # Deeper parchment
)

DARK = ThemeTokens(
    background=ColorScale(33, 27, 13),  # #2A2218 — dark earth
    foreground=ColorScale(36, 38, 92),  # #F3EDE4 — parchment text
    card=ColorScale(33, 22, 17),  # Slightly lighter earth
    card_foreground=ColorScale(36, 38, 92),
    popover=ColorScale(33, 22, 17),
    popover_foreground=ColorScale(36, 38, 92),
    primary=ColorScale(13, 51, 58),  # Terracotta — brightened
    primary_foreground=ColorScale(0, 0, 100),
    secondary=ColorScale(33, 18, 20),  # Dark earth panel
    secondary_foreground=ColorScale(36, 38, 92),
    muted=ColorScale(33, 15, 22),
    muted_foreground=ColorScale(30, 9, 58),  # Warm gray
    accent=ColorScale(33, 15, 20),  # Dark hover
    accent_foreground=ColorScale(36, 38, 92),
    destructive=ColorScale(0, 55, 55),
    destructive_foreground=ColorScale(0, 0, 100),
    success=ColorScale(127, 16, 60),  # Sage — brightened
    success_foreground=ColorScale(33, 27, 13),
    warning=ColorScale(42, 63, 60),  # Mustard — brightened
    warning_foreground=ColorScale(33, 27, 13),
    info=ColorScale(206, 24, 60),  # Slate blue — brightened
    info_foreground=ColorScale(0, 0, 100),
    link=ColorScale(13, 51, 63),  # Terracotta — bright on dark
    link_hover=ColorScale(13, 51, 73),
    code=ColorScale(33, 30, 9),  # Deepest earth
    code_foreground=ColorScale(127, 16, 65),  # Sage code text
    selection=ColorScale(42, 50, 25),  # Dark mustard selection
    selection_foreground=ColorScale(36, 38, 92),
    brand=ColorScale(127, 16, 60),  # Sage — brightened
    brand_foreground=ColorScale(0, 0, 100),
    border=ColorScale(33, 15, 25),  # Dark border
    input=ColorScale(33, 15, 22),
    ring=ColorScale(13, 51, 58),
    surface_1=ColorScale(33, 30, 9),  # Deepest
    surface_2=ColorScale(33, 27, 13),  # Dark earth
    surface_3=ColorScale(33, 22, 17),  # Panel
)

PRESET = ThemePreset(
    name="handcraft",
    display_name="Handcraft",
    description="Warm earth tones — terracotta, sage, mustard, organic shapes, artisan feel",
    light=LIGHT,
    dark=DARK,
    radius=0.75,  # 12px — soft, organic, approachable
)


# =============================================================================
# Design System
# =============================================================================

TYPOGRAPHY = TypographyStyle(
    name="handcraft",
    # Rounded, friendly font for headings — craft/artisan feel
    heading_font='"Nunito", "Quicksand", system-ui, sans-serif',
    body_font='"Inter", system-ui, sans-serif',
    base_size="16px",
    heading_scale=1.25,
    line_height="1.6",  # Generous — relaxed reading
    body_line_height="1.75",  # Very generous body — unhurried
    heading_weight="700",  # Bold but friendly
    section_heading_weight="600",
    body_weight="400",
    letter_spacing="normal",  # No tracking — natural, hand-written feel
    prose_max_width="40rem",  # Comfortable measure
    badge_radius="9999px",  # Pill badges — soft and friendly
)

LAYOUT = LayoutStyle(
    name="handcraft",
    space_unit="1rem",
    space_scale=1.5,
    border_radius_sm="8px",  # Very rounded
    border_radius_md="12px",
    border_radius_lg="16px",  # Organic, soft
    button_shape="pill",  # Pill buttons — friendly
    card_shape="organic",  # Organic card corners
    input_shape="rounded",
    container_width="1000px",  # Narrower — intimate, not corporate
    grid_gap="1.5rem",
    section_spacing="4rem",
    hero_padding_top="7rem",
    hero_padding_bottom="4rem",
    hero_line_height="1.2",
    hero_max_width="50rem",
)

SURFACE = SurfaceStyle(
    name="handcraft",
    # Warm, soft shadows — not clinical
    shadow_sm="0 2px 6px rgba(61, 50, 41, 0.08)",
    shadow_md="0 6px 16px rgba(61, 50, 41, 0.1)",
    shadow_lg="0 12px 32px rgba(61, 50, 41, 0.12)",
    border_width="1px",
    border_style="solid",
    surface_treatment="noise",  # Paper/grain texture — handmade feel
    backdrop_blur="0px",
    noise_opacity=0.05,  # Canvas grain — subtle artisan texture
)

ICON = IconStyle(
    name="handcraft",
    style="rounded",  # Rounded, friendly icons
    weight="regular",
    size_scale=1.05,
    stroke_width="2",
    corner_rounding="4px",  # Soft icon corners
)

ANIMATION = AnimationStyle(
    name="handcraft",
    entrance_effect="bounce",  # Playful bounce entrance
    exit_effect="fade",
    hover_effect="scale",  # Gentle scale on hover
    hover_scale=1.03,  # Subtle, not dramatic
    hover_translate_y="0px",
    click_effect="bounce",  # Bouncy click feedback
    loading_style="pulse",  # Gentle pulse loading
    transition_style="bouncy",  # The signature — everything bounces slightly
    duration_fast="0.15s",
    duration_normal="0.3s",
    duration_slow="0.45s",
    easing="cubic-bezier(0.34, 1.56, 0.64, 1)",  # OVERSHOOT easing — bouncy!
)

INTERACTION = InteractionStyle(
    name="handcraft",
    button_hover="scale",  # Friendly scale
    link_hover="underline",  # Simple underline
    card_hover="lift",  # Lift on hover — cards feel tactile
    focus_style="ring",
    focus_ring_width="3px",  # Visible, friendly focus ring
)

DESIGN_SYSTEM = DesignSystem(
    name="handcraft",
    display_name="Handcraft",
    description="Warm earth — organic shapes, paper texture, bouncy animations, artisan feel",
    category="playful",
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
    name="handcraft",
    display_name="Handcraft",
    description="Warm earth tones — terracotta, sage, mustard, organic shapes, artisan feel",
    category="playful",
    design_theme="handcraft",
    color_preset="handcraft",
    icon_style=ICON,
    animation_style=ANIMATION,
    pattern_style=PATTERN_NOISE,
    interaction_style=INTERACTION,
    illustration_style=ILLUST_HAND_DRAWN,
)
