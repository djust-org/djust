"""Ink — Japanese calligraphy minimalism. Sumi ink on washi paper, single vermillion accent."""

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
# Ink palette philosophy: restraint above all.
# - Sumi ink (墨): blue-black, not warm brown
# - Washi paper (和紙): cool off-white with subtle blue-gray undertone
# - Vermillion (朱): the single deliberate accent — red-orange seal ink
# - Everything else: quiet, receding, monochromatic
# - No decoration, no gradients, no glow — just ink on paper.

LIGHT = ThemeTokens(
    background=ColorScale(220, 15, 97),  # Washi paper — cool off-white, NOT warm
    foreground=ColorScale(220, 20, 12),  # Sumi ink — blue-black, deep
    card=ColorScale(220, 10, 99),  # Slightly brighter washi
    card_foreground=ColorScale(220, 20, 12),
    popover=ColorScale(220, 10, 99),
    popover_foreground=ColorScale(220, 20, 12),
    primary=ColorScale(8, 85, 48),  # 朱 Vermillion — traditional seal red
    primary_foreground=ColorScale(0, 0, 100),
    secondary=ColorScale(220, 8, 93),  # Pale washi
    secondary_foreground=ColorScale(220, 20, 12),
    muted=ColorScale(220, 8, 91),
    muted_foreground=ColorScale(220, 10, 45),  # Diluted ink
    accent=ColorScale(220, 8, 95),  # Subtle hover — barely there
    accent_foreground=ColorScale(220, 20, 12),
    destructive=ColorScale(8, 85, 48),  # Vermillion doubles as destructive
    destructive_foreground=ColorScale(0, 0, 100),
    success=ColorScale(160, 25, 40),  # Muted pine green — subdued like nature
    success_foreground=ColorScale(0, 0, 100),
    warning=ColorScale(45, 50, 50),  # Muted ochre — earthy, not bright
    warning_foreground=ColorScale(220, 20, 12),
    info=ColorScale(220, 20, 35),  # Deep ink-blue — informational
    info_foreground=ColorScale(0, 0, 100),
    link=ColorScale(8, 85, 43),  # Darker vermillion — links are deliberate marks
    link_hover=ColorScale(8, 85, 33),  # Deeper on hover — ink pressed harder
    code=ColorScale(220, 20, 12),  # Sumi ink code bg — full darkness
    code_foreground=ColorScale(8, 85, 60),  # Vermillion code text
    selection=ColorScale(8, 60, 88),  # Pale vermillion wash — like diluted ink
    selection_foreground=ColorScale(220, 20, 12),
    brand=ColorScale(8, 85, 48),  # Vermillion IS the brand — the hanko seal
    brand_foreground=ColorScale(0, 0, 100),
    border=ColorScale(220, 10, 82),  # Light ink wash border
    input=ColorScale(220, 8, 95),  # Near-paper input bg
    ring=ColorScale(8, 85, 48),  # Vermillion focus
    surface_1=ColorScale(220, 15, 97),  # Washi base
    surface_2=ColorScale(220, 10, 94),  # Slightly deeper
    surface_3=ColorScale(220, 8, 91),  # Deepest washi
)

DARK = ThemeTokens(
    # Dark mode: ink-soaked paper — deep blue-black with pale ink text
    background=ColorScale(220, 25, 7),  # Deep sumi — nearly black with blue
    foreground=ColorScale(220, 10, 80),  # Worn ink on dark paper
    card=ColorScale(220, 20, 10),
    card_foreground=ColorScale(220, 10, 80),
    popover=ColorScale(220, 20, 10),
    popover_foreground=ColorScale(220, 10, 80),
    primary=ColorScale(8, 85, 55),  # Vermillion — brightened for dark
    primary_foreground=ColorScale(220, 25, 7),
    secondary=ColorScale(220, 18, 12),
    secondary_foreground=ColorScale(220, 10, 80),
    muted=ColorScale(220, 15, 14),
    muted_foreground=ColorScale(220, 8, 50),  # Faded ink
    accent=ColorScale(220, 15, 12),  # Barely visible hover
    accent_foreground=ColorScale(220, 10, 80),
    destructive=ColorScale(8, 85, 55),
    destructive_foreground=ColorScale(0, 0, 100),
    success=ColorScale(160, 25, 50),
    success_foreground=ColorScale(220, 25, 7),
    warning=ColorScale(45, 50, 55),
    warning_foreground=ColorScale(220, 25, 7),
    info=ColorScale(220, 20, 55),
    info_foreground=ColorScale(0, 0, 100),
    link=ColorScale(8, 85, 58),
    link_hover=ColorScale(8, 85, 68),
    code=ColorScale(220, 30, 5),  # Deepest ink
    code_foreground=ColorScale(8, 85, 60),  # Vermillion
    selection=ColorScale(8, 60, 20),  # Dark vermillion wash
    selection_foreground=ColorScale(220, 10, 85),
    brand=ColorScale(8, 85, 55),
    brand_foreground=ColorScale(220, 25, 7),
    border=ColorScale(220, 15, 18),
    input=ColorScale(220, 15, 12),
    ring=ColorScale(8, 85, 55),
    surface_1=ColorScale(220, 30, 4),  # Deepest
    surface_2=ColorScale(220, 25, 7),  # Sumi black
    surface_3=ColorScale(220, 20, 10),  # Raised
)

PRESET = ThemePreset(
    name="ink",
    display_name="Ink",
    description="Sumi ink on washi paper — vermillion seal accent, calligraphic restraint",
    light=LIGHT,
    dark=DARK,
    radius=0,  # Sharp — calligraphy is precise, no softness
)


# =============================================================================
# Design System
# =============================================================================
# Japanese calligraphy principles:
# - Ma (間): negative space is content, not emptiness
# - Wabi-sabi (侘寂): beauty in imperfection and simplicity
# - One stroke, one meaning — no redundant decoration

TYPOGRAPHY = TypographyStyle(
    name="ink",
    heading_font='"Noto Serif JP", "Noto Serif", Georgia, serif',  # Serif for brush-stroke character
    body_font='"Noto Sans JP", system-ui, sans-serif',  # Clean sans for body readability
    base_size="16px",
    heading_scale=1.3,  # Moderate — calligraphy isn't shouty
    line_height="1.7",  # Generous — ma (negative space) between lines
    body_line_height="1.85",  # Very generous body — reading is contemplative
    heading_weight="400",  # Light weight — calligraphy strokes vary, not uniformly bold
    section_heading_weight="500",
    body_weight="400",
    letter_spacing="0.03em",  # Slightly open — each character breathes
    prose_max_width="34rem",  # Narrow — like a scroll, not a billboard
    badge_radius="0px",  # Sharp — no pills
)

LAYOUT = LayoutStyle(
    name="ink",
    space_unit="1rem",
    space_scale=1.618,  # Golden ratio — natural proportion
    border_radius_sm="0px",
    border_radius_md="0px",
    border_radius_lg="0px",
    button_shape="sharp",
    card_shape="sharp",
    input_shape="sharp",
    container_width="860px",  # Very narrow — scroll-like
    grid_gap="2rem",  # Generous gaps — ma
    section_spacing="5rem",  # Dramatic section breathing
    hero_padding_top="10rem",  # Grand emptiness before content
    hero_padding_bottom="5rem",
    hero_line_height="1.0",  # Tight hero — calligraphic impact
    hero_max_width="36rem",  # Narrow hero
)

SURFACE = SurfaceStyle(
    name="ink",
    shadow_sm="none",  # No shadows — ink is flat
    shadow_md="none",
    shadow_lg="none",
    border_width="1px",  # Single precise stroke
    border_style="solid",
    surface_treatment="flat",  # Absolutely flat — washi is flat
    backdrop_blur="0px",
    noise_opacity=0.0,  # No texture — washi's texture is too subtle for screen
)

ICON = IconStyle(
    name="ink",
    style="outlined",  # Thin outlines — brush strokes
    weight="thin",
    size_scale=0.95,  # Slightly smaller — humble
    stroke_width="1",  # Hairline — like a fine brush
    corner_rounding="0px",
)

ANIMATION = AnimationStyle(
    name="ink",
    entrance_effect="none",  # No animation — content appears like ink on paper
    exit_effect="none",
    hover_effect="none",  # No hover transform — stillness
    hover_scale=1.0,
    hover_translate_y="0px",
    click_effect="none",
    loading_style="spinner",
    transition_style="instant",  # Instant — ink doesn't animate
    duration_fast="0s",
    duration_normal="0.05s",  # Near-zero for color transitions
    duration_slow="0.1s",
    easing="linear",
)

INTERACTION = InteractionStyle(
    name="ink",
    button_hover="darken",  # Simple darken — ink pressed harder
    link_hover="underline",  # Underline only — the minimum gesture
    card_hover="none",  # No card hover — stillness
    focus_style="outline",  # Hard outline — precise
    focus_ring_width="1px",  # Hairline focus ring
)

DESIGN_SYSTEM = DesignSystem(
    name="ink",
    display_name="Ink",
    description="Sumi ink on washi — calligraphic restraint, vermillion seal, 間 negative space",
    category="minimal",
    typography=TYPOGRAPHY,
    layout=LAYOUT,
    surface=SURFACE,
    icons=ICON,
    animation=ANIMATION,
    interaction=INTERACTION,
)

PACK = ThemePack(
    name="ink",
    display_name="Ink",
    description="Sumi ink on washi paper — calligraphic restraint, vermillion seal accent",
    category="minimal",
    design_theme="ink",
    color_preset="ink",
    icon_style=ICON,
    animation_style=ANIMATION,
    pattern_style=PATTERN_MINIMAL,
    interaction_style=INTERACTION,
    illustration_style=ILLUST_LINE,
)
