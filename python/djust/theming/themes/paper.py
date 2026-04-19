"""Paper — Sunlit afternoon reading. Warm cream, serif headings, book-like measure, paper texture."""

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
    ILLUST_LINE,
)


# =============================================================================
# Color Preset
# =============================================================================
# Paper palette philosophy: the warmth of a physical book.
# - Background: cream/ivory like aged paper in afternoon sun
# - Text: warm near-black (sepia-tinged, not blue-black)
# - Primary: a rich warm brown like bookbinding leather
# - Links: traditional blue-ink — the one cool accent
# - Brand: the brown leather — the identity
# - Success/warning: muted, natural tones — nothing digital-bright
# - Borders: like pencil lines on paper — light and warm

LIGHT = ThemeTokens(
    background=ColorScale(38, 45, 97),  # Cream paper — warm, sunlit
    foreground=ColorScale(25, 25, 16),  # Warm near-black — like printed text
    card=ColorScale(38, 35, 99),  # Slightly brighter cream
    card_foreground=ColorScale(25, 25, 16),
    popover=ColorScale(38, 35, 99),
    popover_foreground=ColorScale(25, 25, 16),
    primary=ColorScale(25, 50, 38),  # Rich leather brown — bookbinding
    primary_foreground=ColorScale(38, 45, 97),  # Cream on brown
    secondary=ColorScale(38, 30, 93),  # Lighter cream panel
    secondary_foreground=ColorScale(25, 25, 16),
    muted=ColorScale(38, 25, 90),  # Muted cream
    muted_foreground=ColorScale(25, 12, 45),  # Faded print
    accent=ColorScale(38, 25, 95),  # Warm hover
    accent_foreground=ColorScale(25, 25, 16),
    destructive=ColorScale(5, 55, 48),  # Muted warm red — like a margin note
    destructive_foreground=ColorScale(0, 0, 100),
    success=ColorScale(140, 30, 38),  # Forest green — like a bookplate
    success_foreground=ColorScale(0, 0, 100),
    warning=ColorScale(38, 65, 50),  # Warm amber — like aged paper edge
    warning_foreground=ColorScale(25, 25, 16),
    info=ColorScale(215, 45, 45),  # Blue-ink — traditional
    info_foreground=ColorScale(0, 0, 100),
    link=ColorScale(215, 50, 42),  # Blue ink — the one cool color
    link_hover=ColorScale(215, 50, 32),  # Darker blue on hover
    code=ColorScale(25, 25, 16),  # Dark as printed code
    code_foreground=ColorScale(38, 45, 90),  # Light cream on dark code
    selection=ColorScale(215, 50, 85),  # Pale blue-ink selection
    selection_foreground=ColorScale(25, 25, 16),
    brand=ColorScale(25, 50, 38),  # Leather brown IS the brand
    brand_foreground=ColorScale(38, 45, 97),
    border=ColorScale(38, 18, 82),  # Pencil line — light and warm
    input=ColorScale(38, 20, 94),  # Near-cream input
    ring=ColorScale(215, 50, 42),  # Blue-ink focus ring
    surface_1=ColorScale(38, 45, 97),  # Paper base
    surface_2=ColorScale(38, 35, 94),  # Slightly aged
    surface_3=ColorScale(38, 25, 91),  # Deeper cream
)

DARK = ThemeTokens(
    # Dark mode: reading by lamplight — warm dark, golden-tinted text
    background=ColorScale(25, 20, 11),  # Dark leather — warm, not cold
    foreground=ColorScale(38, 30, 85),  # Warm parchment text
    card=ColorScale(25, 16, 15),  # Slightly lighter
    card_foreground=ColorScale(38, 30, 85),
    popover=ColorScale(25, 16, 15),
    popover_foreground=ColorScale(38, 30, 85),
    primary=ColorScale(25, 50, 55),  # Leather — brightened
    primary_foreground=ColorScale(25, 20, 11),
    secondary=ColorScale(25, 14, 18),
    secondary_foreground=ColorScale(38, 30, 85),
    muted=ColorScale(25, 12, 20),
    muted_foreground=ColorScale(38, 15, 55),  # Faded parchment
    accent=ColorScale(25, 12, 17),  # Warm dark hover
    accent_foreground=ColorScale(38, 30, 85),
    destructive=ColorScale(5, 55, 55),
    destructive_foreground=ColorScale(0, 0, 100),
    success=ColorScale(140, 30, 48),
    success_foreground=ColorScale(25, 20, 11),
    warning=ColorScale(38, 65, 55),
    warning_foreground=ColorScale(25, 20, 11),
    info=ColorScale(215, 45, 55),
    info_foreground=ColorScale(0, 0, 100),
    link=ColorScale(215, 50, 58),  # Blue ink — brightened for dark
    link_hover=ColorScale(215, 50, 68),
    code=ColorScale(25, 25, 8),  # Darkest leather
    code_foreground=ColorScale(38, 30, 75),  # Warm parchment code text
    selection=ColorScale(215, 40, 22),  # Dark blue-ink selection
    selection_foreground=ColorScale(38, 30, 90),
    brand=ColorScale(25, 50, 55),
    brand_foreground=ColorScale(25, 20, 11),
    border=ColorScale(25, 12, 22),
    input=ColorScale(25, 12, 18),
    ring=ColorScale(215, 50, 55),
    surface_1=ColorScale(25, 25, 7),  # Deepest
    surface_2=ColorScale(25, 20, 11),  # Dark leather
    surface_3=ColorScale(25, 16, 15),  # Raised
)

PRESET = ThemePreset(
    name="paper",
    display_name="Paper",
    description="Sunlit afternoon reading — cream paper, leather-brown, serif headings, blue-ink links",
    light=LIGHT,
    dark=DARK,
    radius=0.25,  # 4px — softly rounded like a book's worn edges
)


# =============================================================================
# Design System
# =============================================================================
# Book design principles:
# - Typography IS the design — serif headings, generous measure
# - Whitespace is generous but not dramatic — comfortable reading
# - Paper grain texture — subtle reminder of physicality
# - Warm shadows — like a book casting a shadow on a table
# - Gentle transitions — pages don't snap, they turn

TYPOGRAPHY = TypographyStyle(
    name="paper",
    heading_font='"Libre Baskerville", "Georgia", "Noto Serif", serif',  # Classic book serif
    body_font='"Charter", "Georgia", serif',  # Body in serif too — this is a reading theme
    base_size="17px",  # Slightly larger — books are comfortable
    heading_scale=1.25,  # Moderate — book headings are clear, not dramatic
    line_height="1.6",
    body_line_height="1.85",  # Very generous — the key to comfortable reading
    heading_weight="700",  # Bold serif headings — traditional book style
    section_heading_weight="600",
    body_weight="400",
    letter_spacing="0.005em",  # Barely open — natural serif rhythm
    prose_max_width="36rem",  # Narrow measure — 60-70 characters, book-like
    badge_radius="3px",  # Slightly rounded — not sharp, not pill
)

LAYOUT = LayoutStyle(
    name="paper",
    space_unit="1rem",
    space_scale=1.5,
    border_radius_sm="3px",  # Subtle softness
    border_radius_md="4px",
    border_radius_lg="6px",
    button_shape="rounded",
    card_shape="rounded",
    input_shape="rounded",
    container_width="880px",  # Very narrow — book page width
    grid_gap="1.5rem",
    section_spacing="4rem",  # Comfortable chapter breaks
    hero_padding_top="6rem",
    hero_padding_bottom="4rem",
    hero_line_height="1.2",
    hero_max_width="38rem",  # Narrow hero — book title page
)

SURFACE = SurfaceStyle(
    name="paper",
    # Warm, soft shadows — a book on a table
    shadow_sm="0 1px 3px rgba(80, 60, 40, 0.06)",
    shadow_md="0 4px 12px rgba(80, 60, 40, 0.08)",
    shadow_lg="0 8px 24px rgba(80, 60, 40, 0.1)",
    border_width="1px",
    border_style="solid",
    surface_treatment="noise",  # Paper grain — the defining texture
    backdrop_blur="0px",
    noise_opacity=0.04,  # Subtle paper grain — visible but not distracting
)

ICON = IconStyle(
    name="paper",
    style="outlined",
    weight="regular",
    size_scale=1.0,
    stroke_width="1.5",
    corner_rounding="2px",
)

ANIMATION = AnimationStyle(
    name="paper",
    entrance_effect="fade",  # Gentle fade — like a page appearing
    exit_effect="fade",
    hover_effect="lift",  # Tiny lift — picking up the page
    hover_scale=1.0,
    hover_translate_y="-1px",  # Barely perceptible
    click_effect="none",
    loading_style="pulse",  # Gentle pulse
    transition_style="smooth",
    duration_fast="0.2s",  # Unhurried
    duration_normal="0.3s",
    duration_slow="0.45s",
    easing="cubic-bezier(0.25, 0.1, 0.25, 1.0)",  # Smooth page-turn feel
)

INTERACTION = InteractionStyle(
    name="paper",
    button_hover="lift",
    link_hover="underline",  # Traditional underline — like a book hyperlink
    card_hover="shadow",  # Shadow deepens — picking up the card
    focus_style="ring",
    focus_ring_width="2px",
)

DESIGN_SYSTEM = DesignSystem(
    name="paper",
    display_name="Paper",
    description="Sunlit reading — book serif, paper texture, narrow measure, blue-ink accent",
    category="elegant",
    typography=TYPOGRAPHY,
    layout=LAYOUT,
    surface=SURFACE,
    icons=ICON,
    animation=ANIMATION,
    interaction=INTERACTION,
)

PACK = ThemePack(
    name="paper",
    display_name="Paper",
    description="Sunlit afternoon reading — cream paper, serif headings, blue-ink links",
    category="elegant",
    design_theme="paper",
    color_preset="paper",
    icon_style=ICON,
    animation_style=ANIMATION,
    pattern_style=PATTERN_NOISE,
    interaction_style=INTERACTION,
    illustration_style=ILLUST_LINE,
)
