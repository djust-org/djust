"""Art Deco — Gold and midnight navy with geometric elegance and luxury shadows."""

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
    PATTERN_GRADIENT,
    ILLUST_LINE,
)


# =============================================================================
# Color Preset
# =============================================================================
# Art Deco palette: midnight navy base, gold accents, champagne/cream text,
# emerald green success, burgundy destructive. Dark-first — the style demands
# dark backgrounds with metallic highlights.

LIGHT = ThemeTokens(
    background=ColorScale(44, 58, 96),  # #FBF8F0 — cream
    foreground=ColorScale(219, 55, 10),  # #0C1629 — midnight navy text
    card=ColorScale(0, 0, 100),  # White cards
    card_foreground=ColorScale(219, 55, 10),
    popover=ColorScale(0, 0, 100),
    popover_foreground=ColorScale(219, 55, 10),
    primary=ColorScale(41, 49, 59),  # #C9A962 — gold
    primary_foreground=ColorScale(219, 55, 10),  # Dark on gold
    secondary=ColorScale(42, 60, 90),  # Champagne panel
    secondary_foreground=ColorScale(219, 55, 10),
    muted=ColorScale(42, 40, 88),  # Warm muted
    muted_foreground=ColorScale(219, 30, 35),
    accent=ColorScale(42, 40, 93),  # Warm hover surface
    accent_foreground=ColorScale(219, 55, 10),
    destructive=ColorScale(333, 61, 34),  # #8B2252 — burgundy
    destructive_foreground=ColorScale(0, 0, 100),
    success=ColorScale(160, 64, 29),  # #1B7A5A — emerald
    success_foreground=ColorScale(0, 0, 100),
    warning=ColorScale(41, 49, 59),  # Gold doubles as warning
    warning_foreground=ColorScale(219, 55, 10),
    info=ColorScale(226, 49, 40),  # Deep blue
    info_foreground=ColorScale(0, 0, 100),
    link=ColorScale(41, 55, 50),  # Deeper gold links
    link_hover=ColorScale(29, 57, 46),  # Copper on hover
    code=ColorScale(219, 55, 10),  # Midnight code bg
    code_foreground=ColorScale(42, 52, 65),  # Bright gold code text
    selection=ColorScale(41, 49, 80),  # Pale gold selection
    selection_foreground=ColorScale(219, 55, 10),
    brand=ColorScale(41, 49, 59),  # Gold IS the brand
    brand_foreground=ColorScale(219, 55, 10),
    border=ColorScale(41, 30, 78),  # Gold-tinted border
    input=ColorScale(42, 30, 90),  # Light gold input
    ring=ColorScale(41, 49, 59),  # Gold focus ring
    surface_1=ColorScale(44, 58, 96),  # Cream base
    surface_2=ColorScale(42, 60, 93),  # Champagne
    surface_3=ColorScale(42, 50, 90),  # Deeper champagne
)

DARK = ThemeTokens(
    background=ColorScale(219, 55, 10),  # #0C1629 — midnight navy
    foreground=ColorScale(40, 44, 89),  # #F0E8D8 — ivory text
    card=ColorScale(226, 49, 17),  # #162040 — deep navy card
    card_foreground=ColorScale(40, 44, 89),
    popover=ColorScale(222, 45, 20),  # #1C2A4A — navy panel
    popover_foreground=ColorScale(40, 44, 89),
    primary=ColorScale(41, 49, 59),  # #C9A962 — gold (unchanged — metallic stays constant)
    primary_foreground=ColorScale(219, 55, 10),  # Dark on gold
    secondary=ColorScale(222, 45, 20),  # Navy panel
    secondary_foreground=ColorScale(40, 44, 89),
    muted=ColorScale(222, 40, 22),  # Dark muted
    muted_foreground=ColorScale(0, 0, 55),  # Silver-gray muted text
    accent=ColorScale(222, 40, 22),  # Dark hover surface
    accent_foreground=ColorScale(40, 44, 89),
    destructive=ColorScale(333, 61, 45),  # Burgundy — brightened
    destructive_foreground=ColorScale(0, 0, 100),
    success=ColorScale(160, 64, 40),  # Emerald — brightened
    success_foreground=ColorScale(0, 0, 100),
    warning=ColorScale(42, 52, 65),  # Bright gold
    warning_foreground=ColorScale(219, 55, 10),
    info=ColorScale(226, 49, 55),  # Blue — brightened
    info_foreground=ColorScale(0, 0, 100),
    link=ColorScale(42, 52, 65),  # Bright gold links on dark
    link_hover=ColorScale(43, 60, 77),  # Pale gold hover
    code=ColorScale(219, 60, 7),  # Deepest midnight
    code_foreground=ColorScale(41, 49, 59),  # Gold code text
    selection=ColorScale(41, 40, 25),  # Dark gold selection
    selection_foreground=ColorScale(40, 44, 89),
    brand=ColorScale(42, 52, 65),  # Bright gold on dark
    brand_foreground=ColorScale(219, 55, 10),
    border=ColorScale(41, 30, 30),  # Dark gold-tinted border
    input=ColorScale(222, 40, 22),  # Dark input bg
    ring=ColorScale(41, 49, 59),  # Gold focus ring
    surface_1=ColorScale(219, 60, 7),  # Deepest midnight
    surface_2=ColorScale(219, 55, 10),  # Midnight navy
    surface_3=ColorScale(226, 49, 17),  # Deep navy
)

PRESET = ThemePreset(
    name="art_deco",
    display_name="Art Deco",
    description="Gold and midnight navy — geometric elegance, luxury shadows, 1920s glamour",
    light=LIGHT,
    dark=DARK,
    radius=0.125,  # 2px — sharp but not brutal, geometric precision
    default_mode="dark",  # Art Deco demands dark backgrounds
)


# =============================================================================
# Design System
# =============================================================================

TYPOGRAPHY = TypographyStyle(
    name="art_deco",
    # Condensed geometric display font for headings — Art Deco signature
    heading_font='"DM Sans", "Josefin Sans", "Poiret One", system-ui',
    body_font='"Inter", system-ui, sans-serif',
    base_size="16px",
    heading_scale=1.35,  # Dramatic scale — Art Deco posters are bold
    line_height="1.5",
    body_line_height="1.65",  # Elegant, not cramped
    heading_weight="300",  # LIGHT weight — Art Deco uses thin, elegant type
    section_heading_weight="400",
    body_weight="400",
    letter_spacing="0.08em",  # WIDE tracking — defining Art Deco characteristic
    prose_max_width="40rem",
    badge_radius="0px",  # Sharp geometric badges
)

LAYOUT = LayoutStyle(
    name="art_deco",
    space_unit="1rem",
    space_scale=1.5,
    border_radius_sm="2px",  # Barely rounded — geometric
    border_radius_md="2px",
    border_radius_lg="4px",
    button_shape="sharp",  # Sharp buttons
    card_shape="sharp",  # Sharp cards
    input_shape="sharp",  # Sharp inputs
    container_width="1100px",
    grid_gap="2rem",  # Generous, luxurious spacing
    section_spacing="5rem",  # Grand sections
    hero_padding_top="10rem",  # Dramatic entrance
    hero_padding_bottom="6rem",
    hero_line_height="1.1",
    hero_max_width="48rem",
)

SURFACE = SurfaceStyle(
    name="art_deco",
    # Gold-tinted colored shadows — luxury signature
    shadow_sm="0 2px 8px rgba(201, 169, 98, 0.08), 0 1px 2px rgba(0, 0, 0, 0.06)",
    shadow_md="0 8px 24px rgba(201, 169, 98, 0.12), 0 4px 8px rgba(0, 0, 0, 0.08)",
    shadow_lg="0 20px 40px rgba(201, 169, 98, 0.15), 0 8px 16px rgba(0, 0, 0, 0.1)",
    border_width="1px",
    border_style="solid",
    surface_treatment="gradient",  # Subtle gradient surfaces
    backdrop_blur="0px",
    noise_opacity=0.0,
)

ICON = IconStyle(
    name="art_deco",
    style="outlined",  # Thin elegant outlines
    weight="thin",
    size_scale=1.0,
    stroke_width="1",  # Very thin — Art Deco lines are precise
    corner_rounding="0px",
)

ANIMATION = AnimationStyle(
    name="art_deco",
    entrance_effect="fade",  # Elegant fade-in
    exit_effect="fade",
    hover_effect="lift",  # Gentle lift
    hover_scale=1.0,
    hover_translate_y="-3px",  # Subtle float
    click_effect="pulse",
    loading_style="spinner",
    transition_style="smooth",
    duration_fast="0.2s",  # Elegant, not rushed
    duration_normal="0.35s",
    duration_slow="0.5s",
    easing="cubic-bezier(0.25, 0.1, 0.25, 1.0)",  # Smooth ease — graceful
)

INTERACTION = InteractionStyle(
    name="art_deco",
    button_hover="lift",  # Elegant lift
    link_hover="color",  # Gold shift
    card_hover="shadow",  # Shadow deepens — luxury glow
    focus_style="glow",  # Gold glow focus — not harsh ring
    focus_ring_width="2px",
)

DESIGN_SYSTEM = DesignSystem(
    name="art_deco",
    display_name="Art Deco",
    description="Gold and midnight — geometric elegance, wide tracking, luxury shadows",
    category="elegant",
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
    name="art_deco",
    display_name="Art Deco",
    description="Gold and midnight navy — geometric elegance, 1920s glamour",
    category="elegant",
    design_theme="art_deco",
    color_preset="art_deco",
    icon_style=ICON,
    animation_style=ANIMATION,
    pattern_style=PATTERN_GRADIENT,
    interaction_style=INTERACTION,
    illustration_style=ILLUST_LINE,
)
