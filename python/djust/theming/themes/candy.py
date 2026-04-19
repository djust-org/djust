"""Candy — Bright pastels, pill shapes, thick borders, bouncy animations."""

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
    PATTERN_DOTS,
    ILLUST_FLAT,
)


# =============================================================================
# Color Preset
# =============================================================================
# Bright pastels with hot pink primary and lavender brand.
# Think: bubblegum, frosting, candy wrappers.

LIGHT = ThemeTokens(
    background=ColorScale(330, 50, 97),  # Pale pink
    foreground=ColorScale(280, 40, 20),  # Deep purple
    card=ColorScale(0, 0, 100),  # White cards
    card_foreground=ColorScale(280, 40, 20),
    popover=ColorScale(0, 0, 100),
    popover_foreground=ColorScale(280, 40, 20),
    primary=ColorScale(330, 80, 60),  # Hot pink
    primary_foreground=ColorScale(0, 0, 100),
    secondary=ColorScale(270, 40, 95),  # Pale lavender
    secondary_foreground=ColorScale(280, 40, 20),
    muted=ColorScale(160, 50, 95),  # Pale mint accent
    muted_foreground=ColorScale(280, 30, 40),  # Purple-gray text
    accent=ColorScale(270, 30, 95),  # Lavender hover
    accent_foreground=ColorScale(280, 40, 20),
    destructive=ColorScale(0, 70, 55),  # Candy red
    destructive_foreground=ColorScale(0, 0, 100),
    success=ColorScale(160, 60, 50),  # Mint
    success_foreground=ColorScale(0, 0, 100),
    warning=ColorScale(30, 90, 65),  # Peach
    warning_foreground=ColorScale(280, 40, 20),
    info=ColorScale(200, 80, 60),  # Sky blue
    info_foreground=ColorScale(0, 0, 100),
    link=ColorScale(270, 60, 55),  # Lavender-purple links
    link_hover=ColorScale(270, 70, 45),  # Deeper purple hover
    code=ColorScale(280, 40, 15),  # Deep purple code bg
    code_foreground=ColorScale(330, 80, 75),  # Pink code text
    selection=ColorScale(330, 60, 90),  # Pale pink selection
    selection_foreground=ColorScale(280, 40, 20),
    brand=ColorScale(270, 60, 70),  # Lavender
    brand_foreground=ColorScale(0, 0, 100),
    border=ColorScale(330, 40, 82),  # Pink-tinted border
    input=ColorScale(330, 30, 92),  # Pale pink input
    ring=ColorScale(330, 80, 60),  # Hot pink focus ring
    surface_1=ColorScale(330, 50, 97),  # Pale pink
    surface_2=ColorScale(330, 40, 95),  # Slightly deeper
    surface_3=ColorScale(270, 35, 93),  # Lavender tint
)

DARK = ThemeTokens(
    background=ColorScale(280, 40, 12),  # Deep purple
    foreground=ColorScale(330, 30, 92),  # Pale pink
    card=ColorScale(280, 35, 16),  # Purple card
    card_foreground=ColorScale(330, 30, 92),
    popover=ColorScale(280, 35, 16),
    popover_foreground=ColorScale(330, 30, 92),
    primary=ColorScale(330, 80, 65),  # Hot pink brightened
    primary_foreground=ColorScale(0, 0, 100),
    secondary=ColorScale(280, 30, 18),  # Dark purple panel
    secondary_foreground=ColorScale(330, 30, 92),
    muted=ColorScale(280, 25, 22),  # Dark muted purple
    muted_foreground=ColorScale(330, 20, 60),  # Muted pink text
    accent=ColorScale(280, 30, 18),  # Dark purple hover
    accent_foreground=ColorScale(330, 30, 92),
    destructive=ColorScale(0, 70, 60),  # Candy red brightened
    destructive_foreground=ColorScale(0, 0, 100),
    success=ColorScale(160, 60, 55),  # Mint brightened
    success_foreground=ColorScale(0, 0, 100),
    warning=ColorScale(30, 90, 70),  # Peach brightened
    warning_foreground=ColorScale(280, 40, 15),
    info=ColorScale(200, 80, 65),  # Sky blue brightened
    info_foreground=ColorScale(0, 0, 100),
    link=ColorScale(270, 60, 72),  # Lavender brightened
    link_hover=ColorScale(270, 70, 80),  # Lighter lavender
    code=ColorScale(280, 40, 8),  # Deepest purple code bg
    code_foreground=ColorScale(330, 80, 75),  # Pink code text
    selection=ColorScale(330, 60, 25),  # Deep pink selection
    selection_foreground=ColorScale(330, 30, 92),
    brand=ColorScale(270, 60, 75),  # Lavender brightened
    brand_foreground=ColorScale(0, 0, 100),
    border=ColorScale(280, 30, 25),  # Dark purple border
    input=ColorScale(280, 25, 20),  # Dark purple input
    ring=ColorScale(330, 80, 65),  # Hot pink focus ring
    surface_1=ColorScale(280, 40, 10),  # Deepest purple
    surface_2=ColorScale(280, 38, 14),  # Mid purple
    surface_3=ColorScale(280, 35, 18),  # Elevated purple
)

PRESET = ThemePreset(
    name="candy",
    display_name="Candy",
    description="Bright pastels, pill shapes, thick borders, bouncy animations",
    light=LIGHT,
    dark=DARK,
    radius=1.0,  # 16px — very rounded
)


# =============================================================================
# Design System
# =============================================================================

TYPOGRAPHY = TypographyStyle(
    name="candy",
    heading_font='"Nunito", "Comfortaa", sans-serif',
    body_font='"Nunito", system-ui, sans-serif',
    base_size="17px",  # Large base — friendly
    heading_scale=1.3,
    line_height="1.5",
    body_line_height="1.7",
    heading_weight="800",  # Extra bold
    section_heading_weight="800",
    body_weight="400",
    letter_spacing="0em",  # No extra tracking
    prose_max_width="40rem",
    badge_radius="9999px",  # Pill badges
)

LAYOUT = LayoutStyle(
    name="candy",
    space_unit="1rem",
    space_scale=1.5,
    border_radius_sm="12px",  # Big rounded
    border_radius_md="16px",
    border_radius_lg="24px",
    button_shape="pill",  # Pill buttons
    card_shape="rounded",
    input_shape="rounded",
    container_width="1100px",
    grid_gap="1.5rem",
    section_spacing="4rem",
    hero_padding_top="6rem",
    hero_padding_bottom="4rem",
    hero_line_height="1.2",
    hero_max_width="48rem",
)

SURFACE = SurfaceStyle(
    name="candy",
    # Pink-tinted pastel shadows
    shadow_sm="0 2px 8px 0 rgba(200, 100, 150, 0.12)",
    shadow_md="0 4px 16px -2px rgba(200, 100, 150, 0.18)",
    shadow_lg="0 8px 32px -4px rgba(200, 100, 150, 0.24)",
    border_width="2px",  # Thick borders
    border_style="solid",
    surface_treatment="flat",
    backdrop_blur="0px",
    noise_opacity=0.0,
)

ICON = IconStyle(
    name="candy",
    style="rounded",  # Soft rounded icons
    weight="bold",
    size_scale=1.1,  # Slightly larger
    stroke_width="2",
    corner_rounding="4px",
)

ANIMATION = AnimationStyle(
    name="candy",
    entrance_effect="bounce",  # Bouncy entrance
    exit_effect="fade",
    hover_effect="scale",  # Scale up on hover
    hover_scale=1.05,  # Noticeable bounce
    hover_translate_y="-2px",
    click_effect="scale",
    loading_style="bounce",
    transition_style="bouncy",
    duration_fast="0.15s",
    duration_normal="0.25s",
    duration_slow="0.4s",
    easing="cubic-bezier(0.34, 1.56, 0.64, 1)",  # Overshoot bounce
)

INTERACTION = InteractionStyle(
    name="candy",
    button_hover="scale",  # Bouncy scale on hover
    link_hover="color",
    card_hover="lift",  # Cards bounce up
    focus_style="ring",
    focus_ring_width="3px",  # Thick playful ring
)

DESIGN_SYSTEM = DesignSystem(
    name="candy",
    display_name="Candy",
    description="Bright pastels, pill shapes, thick borders, bouncy animations",
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
    name="candy",
    display_name="Candy",
    description="Bright pastels, pill shapes, thick borders, bouncy animations",
    category="playful",
    design_theme="candy",
    color_preset="candy",
    icon_style=ICON,
    animation_style=ANIMATION,
    pattern_style=PATTERN_DOTS,
    interaction_style=INTERACTION,
    illustration_style=ILLUST_FLAT,
)
