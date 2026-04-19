"""Stripe — Clean fintech design with blurple brand and colored shadows."""

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
# Colors traced to Stripe's brand palette and Appearance API:
# - Blurple #635BFF — the signature Stripe brand color
# - Action Blue #0570DE — primary CTA color (from Elements API)
# - Downriver #0A2540 — dark navy for headings and emphasis
# - Cool gray-blue tones throughout (210° hue base)

LIGHT = ThemeTokens(
    background=ColorScale(207, 43, 98),  # #F6F9FC — Stripe's cool white
    foreground=ColorScale(210, 73, 15),  # #0A2540 — Downriver navy (not pure black)
    card=ColorScale(0, 0, 100),  # Pure white cards
    card_foreground=ColorScale(210, 73, 15),
    popover=ColorScale(0, 0, 100),
    popover_foreground=ColorScale(235, 12, 21),  # #30313D — Stripe text color
    primary=ColorScale(210, 96, 45),  # #0570DE — Action blue (CTAs)
    primary_foreground=ColorScale(0, 0, 100),
    secondary=ColorScale(207, 43, 96),  # #F0F5F9 — Subtle cool bg
    secondary_foreground=ColorScale(210, 73, 15),
    muted=ColorScale(213, 24, 91),  # #E3E8EE — Cool gray border/muted
    muted_foreground=ColorScale(219, 12, 47),  # #697386 — Muted text
    accent=ColorScale(207, 43, 96),  # Cool hover surface
    accent_foreground=ColorScale(210, 73, 15),
    destructive=ColorScale(348, 78, 49),  # #DF1B41 — Stripe's danger red
    destructive_foreground=ColorScale(0, 0, 100),
    success=ColorScale(168, 100, 42),  # #00D4AA — Stripe green
    success_foreground=ColorScale(0, 0, 100),
    warning=ColorScale(44, 100, 50),  # #FFBB00 — Stripe amber
    warning_foreground=ColorScale(210, 73, 15),
    info=ColorScale(210, 96, 45),  # Action blue doubles as info
    info_foreground=ColorScale(0, 0, 100),
    link=ColorScale(243, 100, 68),  # #635BFF — Blurple links (signature!)
    link_hover=ColorScale(210, 96, 40),  # Deeper action blue on hover
    code=ColorScale(210, 73, 15),  # Downriver code bg (dark)
    code_foreground=ColorScale(207, 43, 96),  # Light text on dark code
    selection=ColorScale(243, 100, 90),  # Pale blurple selection
    selection_foreground=ColorScale(210, 73, 15),
    brand=ColorScale(243, 100, 68),  # #635BFF — Blurple (the Stripe identity)
    brand_foreground=ColorScale(0, 0, 100),
    border=ColorScale(213, 24, 91),  # #E3E8EE — Subtle cool border
    input=ColorScale(213, 24, 91),  # Same subtle border for inputs
    ring=ColorScale(210, 96, 45),  # Action blue focus ring
    surface_1=ColorScale(207, 43, 98),  # Lightest cool white
    surface_2=ColorScale(207, 43, 96),  # Mid cool
    surface_3=ColorScale(213, 24, 93),  # Slightly deeper
)

DARK = ThemeTokens(
    background=ColorScale(210, 73, 15),  # #0A2540 — Downriver navy
    foreground=ColorScale(207, 43, 96),  # #F0F5F9 — Cool white text
    card=ColorScale(210, 50, 18),  # Slightly lighter navy card
    card_foreground=ColorScale(207, 43, 96),
    popover=ColorScale(210, 50, 18),
    popover_foreground=ColorScale(207, 43, 96),
    primary=ColorScale(210, 96, 55),  # Action blue — brightened for dark
    primary_foreground=ColorScale(0, 0, 100),
    secondary=ColorScale(210, 40, 22),  # Dark navy panel
    secondary_foreground=ColorScale(207, 43, 96),
    muted=ColorScale(210, 30, 25),  # Dark muted
    muted_foreground=ColorScale(213, 20, 60),  # Muted text on dark
    accent=ColorScale(210, 40, 22),  # Subtle dark hover surface
    accent_foreground=ColorScale(207, 43, 96),
    destructive=ColorScale(348, 78, 55),  # Danger red — brightened
    destructive_foreground=ColorScale(0, 0, 100),
    success=ColorScale(168, 100, 48),  # Green — brightened
    success_foreground=ColorScale(210, 73, 15),
    warning=ColorScale(44, 100, 55),  # Amber — brightened
    warning_foreground=ColorScale(210, 73, 15),
    info=ColorScale(210, 96, 55),  # Action blue
    info_foreground=ColorScale(0, 0, 100),
    link=ColorScale(243, 100, 75),  # Blurple — brightened for dark
    link_hover=ColorScale(210, 96, 65),  # Lighter action blue
    code=ColorScale(210, 60, 10),  # Very dark navy code bg
    code_foreground=ColorScale(168, 100, 60),  # Green code text
    border=ColorScale(210, 30, 28),  # Dark border
    input=ColorScale(210, 30, 28),
    ring=ColorScale(210, 96, 55),  # Action blue focus ring
    selection=ColorScale(243, 80, 30),  # Deep blurple selection
    selection_foreground=ColorScale(207, 43, 96),
    brand=ColorScale(243, 100, 75),  # Blurple — brightened for dark bg
    brand_foreground=ColorScale(0, 0, 100),
    surface_1=ColorScale(210, 73, 10),  # Deepest navy
    surface_2=ColorScale(210, 60, 14),  # Mid navy
    surface_3=ColorScale(210, 50, 18),  # Elevated navy
)

PRESET = ThemePreset(
    name="stripe",
    display_name="Stripe",
    description="Clean fintech — blurple brand, colored shadows, cool precision",
    light=LIGHT,
    dark=DARK,
    radius=0.375,  # 6px — slightly rounded, not bubbly
)


# =============================================================================
# Design System
# =============================================================================

TYPOGRAPHY = TypographyStyle(
    name="stripe",
    heading_font='"Inter", "Söhne", system-ui, sans-serif',
    body_font='"Inter", system-ui, sans-serif',
    base_size="16px",
    heading_scale=1.25,  # Clean, moderate scale
    line_height="1.5",
    body_line_height="1.6",  # Generous for readability
    heading_weight="600",  # Semibold headings (not heavy)
    section_heading_weight="600",
    body_weight="400",
    letter_spacing="-0.01em",  # Slightly tight — modern, precise
    prose_max_width="42rem",  # Optimal reading width
    badge_radius="9999px",  # Pill badges
)

LAYOUT = LayoutStyle(
    name="stripe",
    space_unit="1rem",
    space_scale=1.5,
    border_radius_sm="4px",  # Subtle rounding
    border_radius_md="6px",
    border_radius_lg="8px",
    button_shape="rounded",
    card_shape="rounded",
    input_shape="rounded",
    container_width="1200px",
    grid_gap="1.5rem",
    section_spacing="5rem",  # Generous whitespace (Stripe signature)
    hero_padding_top="8rem",
    hero_padding_bottom="5rem",
    hero_line_height="1.15",
    hero_max_width="52rem",
)

SURFACE = SurfaceStyle(
    name="stripe",
    # Colored shadows — Stripe's signature (purple-tinted, layered)
    shadow_sm="0 2px 5px 0 rgba(50, 50, 93, 0.1), 0 1px 2px 0 rgba(0, 0, 0, 0.07)",
    shadow_md="0 6px 12px -2px rgba(50, 50, 93, 0.15), 0 3px 7px -3px rgba(0, 0, 0, 0.1)",
    shadow_lg="0 15px 35px -5px rgba(50, 50, 93, 0.2), 0 5px 15px -5px rgba(0, 0, 0, 0.12)",
    border_width="1px",
    border_style="solid",
    surface_treatment="flat",  # Clean flat — no glass, no noise
    backdrop_blur="0px",
    noise_opacity=0.0,
)

ICON = IconStyle(
    name="stripe",
    style="outlined",  # Clean outlined icons
    weight="regular",
    size_scale=1.0,
    stroke_width="1.5",  # Thin, precise strokes
    corner_rounding="2px",
)

ANIMATION = AnimationStyle(
    name="stripe",
    entrance_effect="fade",  # Subtle fade-in
    exit_effect="fade",
    hover_effect="lift",  # Gentle lift on hover
    hover_scale=1.0,  # No scale
    hover_translate_y="-2px",  # Slight lift
    click_effect="pulse",
    loading_style="spinner",
    transition_style="smooth",
    duration_fast="0.15s",
    duration_normal="0.25s",  # Smooth, deliberate
    duration_slow="0.35s",
    easing="cubic-bezier(0.4, 0, 0.2, 1)",  # Standard material easing
)

INTERACTION = InteractionStyle(
    name="stripe",
    button_hover="lift",  # Lift + shadow deepen on hover
    link_hover="color",  # Subtle color shift
    card_hover="shadow",  # Shadow deepens on card hover
    focus_style="ring",  # Focus ring
    focus_ring_width="2px",
)

DESIGN_SYSTEM = DesignSystem(
    name="stripe",
    display_name="Stripe",
    description="Clean fintech — precise typography, colored shadows, generous whitespace",
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
    name="stripe",
    display_name="Stripe",
    description="Clean fintech — blurple brand, colored shadows, cool precision",
    category="professional",
    design_theme="stripe",
    color_preset="stripe",
    icon_style=ICON,
    animation_style=ANIMATION,
    pattern_style=PATTERN_MINIMAL,
    interaction_style=INTERACTION,
    illustration_style=ILLUST_LINE,
)
