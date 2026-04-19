"""Adaptive — Bold royal blue with vermillion energy, enterprise clarity, forward momentum."""

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
# Adaptive palette: deep royal blue primary, warm orange brand accent,
# dark navy text, clean light backgrounds. Enterprise trust meets
# forward-thinking energy.

LIGHT = ThemeTokens(
    background=ColorScale(210, 20, 98),  # Cool clean white
    foreground=ColorScale(215, 28, 17),  # Dark navy text — not pure black
    card=ColorScale(0, 0, 100),  # White cards
    card_foreground=ColorScale(215, 28, 17),
    popover=ColorScale(0, 0, 100),
    popover_foreground=ColorScale(215, 28, 17),
    primary=ColorScale(228, 61, 41),  # Royal blue — trust, authority
    primary_foreground=ColorScale(0, 0, 100),
    secondary=ColorScale(210, 15, 95),  # Cool light panel
    secondary_foreground=ColorScale(215, 28, 17),
    muted=ColorScale(210, 12, 92),
    muted_foreground=ColorScale(215, 15, 45),
    accent=ColorScale(210, 12, 96),  # Subtle cool hover
    accent_foreground=ColorScale(215, 28, 17),
    destructive=ColorScale(0, 70, 50),
    destructive_foreground=ColorScale(0, 0, 100),
    success=ColorScale(155, 60, 40),  # Teal green — adaptive growth
    success_foreground=ColorScale(0, 0, 100),
    warning=ColorScale(38, 85, 50),  # Warm amber
    warning_foreground=ColorScale(215, 28, 17),
    info=ColorScale(228, 61, 50),  # Lighter royal blue
    info_foreground=ColorScale(0, 0, 100),
    link=ColorScale(228, 61, 41),  # Blue links — trustworthy
    link_hover=ColorScale(12, 66, 48),  # Vermillion on hover — energy shift
    code=ColorScale(215, 28, 17),  # Dark navy code bg
    code_foreground=ColorScale(12, 66, 65),  # Vermillion code text
    selection=ColorScale(228, 61, 85),  # Pale blue selection
    selection_foreground=ColorScale(215, 28, 17),
    brand=ColorScale(12, 66, 50),  # Warm vermillion — the energy accent
    brand_foreground=ColorScale(0, 0, 100),
    border=ColorScale(210, 15, 88),  # Cool subtle border
    input=ColorScale(210, 12, 95),
    ring=ColorScale(228, 61, 41),  # Blue focus ring
    surface_1=ColorScale(210, 20, 98),  # Base
    surface_2=ColorScale(210, 15, 96),  # Navbar
    surface_3=ColorScale(210, 12, 93),  # Elevated
)

DARK = ThemeTokens(
    background=ColorScale(215, 28, 17),  # Dark navy
    foreground=ColorScale(210, 15, 92),  # Cool light text
    card=ColorScale(215, 25, 20),
    card_foreground=ColorScale(210, 15, 92),
    popover=ColorScale(215, 25, 20),
    popover_foreground=ColorScale(210, 15, 92),
    primary=ColorScale(228, 61, 55),  # Royal blue — brightened
    primary_foreground=ColorScale(0, 0, 100),
    secondary=ColorScale(215, 20, 22),
    secondary_foreground=ColorScale(210, 15, 92),
    muted=ColorScale(215, 18, 24),
    muted_foreground=ColorScale(210, 10, 55),
    accent=ColorScale(215, 18, 22),
    accent_foreground=ColorScale(210, 15, 92),
    destructive=ColorScale(0, 70, 55),
    destructive_foreground=ColorScale(0, 0, 100),
    success=ColorScale(155, 60, 48),
    success_foreground=ColorScale(215, 28, 17),
    warning=ColorScale(38, 85, 55),
    warning_foreground=ColorScale(215, 28, 17),
    info=ColorScale(228, 61, 60),
    info_foreground=ColorScale(0, 0, 100),
    link=ColorScale(228, 61, 60),
    link_hover=ColorScale(12, 66, 58),  # Vermillion hover
    code=ColorScale(215, 30, 12),
    code_foreground=ColorScale(12, 66, 65),
    selection=ColorScale(228, 50, 28),
    selection_foreground=ColorScale(210, 15, 92),
    brand=ColorScale(12, 66, 58),  # Warm vermillion — brightened
    brand_foreground=ColorScale(0, 0, 100),
    border=ColorScale(215, 18, 26),
    input=ColorScale(215, 18, 22),
    ring=ColorScale(228, 61, 55),
    surface_1=ColorScale(215, 30, 12),
    surface_2=ColorScale(215, 28, 15),
    surface_3=ColorScale(215, 25, 20),
)

PRESET = ThemePreset(
    name="adaptive",
    display_name="Adaptive",
    description="Royal blue trust with vermillion energy — bold enterprise clarity, forward momentum",
    light=LIGHT,
    dark=DARK,
    radius=0,  # Sharp corners — decisive, no softness
)


# =============================================================================
# Design System
# =============================================================================
# Design principles: clarity, trust, forward momentum.
# - Figtree/Inter geometric sans — modern, clean, approachable
# - Bold headings (700) — decisive leadership
# - Sharp 0px corners — no ambiguity, direct
# - Clean shadows — professional depth without drama
# - Smooth deliberate transitions — confident, not flashy
# - Wide container — enterprise content needs space

TYPOGRAPHY = TypographyStyle(
    name="adaptive",
    heading_font='"Figtree", "Inter", system-ui, sans-serif',
    body_font='"Inter", system-ui, sans-serif',
    base_size="16px",
    heading_scale=1.3,  # Bold scale — enterprise headings are prominent
    line_height="1.5",
    body_line_height="1.65",  # Generous for long-form enterprise content
    heading_weight="700",  # Bold — decisive
    section_heading_weight="700",
    body_weight="400",
    letter_spacing="-0.01em",  # Slightly tight — modern, efficient
    prose_max_width="44rem",
    badge_radius="0px",  # Sharp badges — matches corners
)

LAYOUT = LayoutStyle(
    name="adaptive",
    space_unit="1rem",
    space_scale=1.5,
    border_radius_sm="0px",  # Sharp everywhere
    border_radius_md="0px",
    border_radius_lg="0px",
    button_shape="sharp",
    card_shape="sharp",
    input_shape="sharp",
    container_width="1280px",  # Wide — enterprise content
    grid_gap="1.5rem",
    section_spacing="4rem",
    hero_padding_top="8rem",  # Grand enterprise hero
    hero_padding_bottom="5rem",
    hero_line_height="1.1",
    hero_max_width="56rem",
)

SURFACE = SurfaceStyle(
    name="adaptive",
    shadow_sm="0 1px 3px rgba(31, 41, 55, 0.06)",
    shadow_md="0 4px 12px rgba(31, 41, 55, 0.08)",
    shadow_lg="0 12px 24px rgba(31, 41, 55, 0.1)",
    border_width="1px",
    border_style="solid",
    surface_treatment="flat",
    backdrop_blur="0px",
    noise_opacity=0.0,
)

ICON = IconStyle(
    name="adaptive",
    style="outlined",  # Clean line icons — enterprise clarity
    weight="regular",
    size_scale=1.0,
    stroke_width="2",
    corner_rounding="0px",
)

ANIMATION = AnimationStyle(
    name="adaptive",
    entrance_effect="fade",
    exit_effect="fade",
    hover_effect="lift",
    hover_scale=1.0,
    hover_translate_y="-2px",
    click_effect="pulse",
    loading_style="spinner",
    transition_style="smooth",
    duration_fast="0.15s",
    duration_normal="0.25s",
    duration_slow="0.35s",
    easing="cubic-bezier(0.4, 0, 0.2, 1)",
)

INTERACTION = InteractionStyle(
    name="adaptive",
    button_hover="lift",
    link_hover="color",  # Blue → orange color shift on hover
    card_hover="shadow",
    focus_style="ring",
    focus_ring_width="2px",
)

DESIGN_SYSTEM = DesignSystem(
    name="adaptive",
    display_name="Adaptive",
    description="Royal blue trust, vermillion energy — enterprise clarity with forward momentum",
    category="professional",
    typography=TYPOGRAPHY,
    layout=LAYOUT,
    surface=SURFACE,
    icons=ICON,
    animation=ANIMATION,
    interaction=INTERACTION,
)

PACK = ThemePack(
    name="adaptive",
    display_name="Adaptive",
    description="Royal blue trust with vermillion energy — bold enterprise clarity",
    category="professional",
    design_theme="adaptive",
    color_preset="adaptive",
    icon_style=ICON,
    animation_style=ANIMATION,
    pattern_style=PATTERN_MINIMAL,
    interaction_style=INTERACTION,
    illustration_style=ILLUST_LINE,
)
