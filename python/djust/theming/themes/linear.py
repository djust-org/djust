"""Linear — Dark-first productivity tool with purple accent and precise density."""

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
# Colors traced to Linear's UI:
# - Linear violet #5E6AD2 — the signature brand color
# - Dark indigo backgrounds with precise, tight UI density
# - Purple/violet accents throughout

LIGHT = ThemeTokens(
    background=ColorScale(240, 3, 98),  # Near-white with cool tint
    foreground=ColorScale(240, 16, 13),  # Dark indigo text
    card=ColorScale(0, 0, 100),  # Pure white cards
    card_foreground=ColorScale(240, 16, 13),
    popover=ColorScale(0, 0, 100),
    popover_foreground=ColorScale(240, 16, 13),
    primary=ColorScale(234, 56, 60),  # Purple primary
    primary_foreground=ColorScale(0, 0, 100),
    secondary=ColorScale(240, 3, 95),  # Subtle light bg
    secondary_foreground=ColorScale(240, 16, 13),
    muted=ColorScale(219, 6, 92),  # Cool muted surface
    muted_foreground=ColorScale(219, 6, 47),  # Muted text
    accent=ColorScale(240, 5, 95),  # Hover surface
    accent_foreground=ColorScale(240, 16, 13),
    destructive=ColorScale(0, 72, 55),  # Red
    destructive_foreground=ColorScale(0, 0, 100),
    success=ColorScale(123, 36, 46),  # Green
    success_foreground=ColorScale(0, 0, 100),
    warning=ColorScale(40, 90, 50),  # Amber
    warning_foreground=ColorScale(240, 16, 13),
    info=ColorScale(234, 56, 60),  # Primary as info
    info_foreground=ColorScale(0, 0, 100),
    link=ColorScale(243, 100, 69),  # Linear violet
    link_hover=ColorScale(234, 56, 50),  # Deeper purple on hover
    code=ColorScale(240, 16, 10),  # Dark code bg
    code_foreground=ColorScale(243, 80, 78),  # Purple-tinted code text
    selection=ColorScale(243, 60, 90),  # Pale purple selection
    selection_foreground=ColorScale(240, 16, 13),
    brand=ColorScale(243, 100, 69),  # #5E6AD2 — Linear violet
    brand_foreground=ColorScale(0, 0, 100),
    border=ColorScale(240, 5, 88),  # Subtle border
    input=ColorScale(240, 5, 88),
    ring=ColorScale(234, 56, 60),  # Purple focus ring
    surface_1=ColorScale(240, 3, 98),  # Lightest
    surface_2=ColorScale(240, 3, 96),  # Mid
    surface_3=ColorScale(240, 4, 94),  # Slightly deeper
)

DARK = ThemeTokens(
    background=ColorScale(240, 16, 13),  # Dark indigo
    foreground=ColorScale(240, 3, 94),  # Light text
    card=ColorScale(240, 13, 15),  # Slightly lighter card
    card_foreground=ColorScale(240, 3, 94),
    popover=ColorScale(240, 13, 15),
    popover_foreground=ColorScale(240, 3, 94),
    primary=ColorScale(234, 56, 65),  # Purple — brightened for dark
    primary_foreground=ColorScale(0, 0, 100),
    secondary=ColorScale(240, 10, 18),  # Dark panel
    secondary_foreground=ColorScale(240, 3, 94),
    muted=ColorScale(240, 10, 20),  # Dark muted
    muted_foreground=ColorScale(219, 6, 57),  # Muted text on dark
    accent=ColorScale(240, 10, 18),  # Dark hover surface
    accent_foreground=ColorScale(240, 3, 94),
    destructive=ColorScale(0, 72, 60),  # Red — brightened
    destructive_foreground=ColorScale(0, 0, 100),
    success=ColorScale(123, 36, 52),  # Green — brightened
    success_foreground=ColorScale(0, 0, 100),
    warning=ColorScale(40, 90, 55),  # Amber — brightened
    warning_foreground=ColorScale(240, 16, 13),
    info=ColorScale(234, 56, 65),  # Primary
    info_foreground=ColorScale(0, 0, 100),
    link=ColorScale(243, 100, 75),  # Violet — brightened
    link_hover=ColorScale(234, 56, 70),  # Lighter purple
    code=ColorScale(240, 16, 10),  # Very dark code bg
    code_foreground=ColorScale(243, 80, 78),  # Purple-tinted code text
    selection=ColorScale(243, 80, 25),  # Deep purple selection
    selection_foreground=ColorScale(240, 3, 94),
    brand=ColorScale(243, 100, 75),  # Violet — brightened
    brand_foreground=ColorScale(0, 0, 100),
    border=ColorScale(240, 10, 22),  # Dark border
    input=ColorScale(240, 10, 22),
    ring=ColorScale(234, 56, 65),  # Purple focus ring
    surface_1=ColorScale(240, 16, 10),  # Deepest
    surface_2=ColorScale(240, 14, 12),  # Mid
    surface_3=ColorScale(240, 13, 15),  # Elevated
)

PRESET = ThemePreset(
    name="linear",
    display_name="Linear",
    description="Dark-first productivity — purple accent, precise density",
    light=LIGHT,
    dark=DARK,
    radius=0.25,  # 4px — tight, precise
    default_mode="dark",
)


# =============================================================================
# Design System
# =============================================================================

TYPOGRAPHY = TypographyStyle(
    name="linear",
    heading_font='"Inter", system-ui, sans-serif',
    body_font='"Inter", system-ui, sans-serif',
    base_size="14px",
    heading_scale=1.2,  # Compact scale
    line_height="1.35",
    body_line_height="1.5",
    heading_weight="500",  # Semibold, not heavy
    section_heading_weight="500",
    body_weight="400",
    letter_spacing="-0.02em",  # Tight — Linear's signature
    prose_max_width="40rem",
    badge_radius="4px",
)

LAYOUT = LayoutStyle(
    name="linear",
    space_unit="1rem",
    space_scale=1.25,
    border_radius_sm="4px",
    border_radius_md="6px",
    border_radius_lg="8px",
    button_shape="rounded",
    card_shape="rounded",
    input_shape="rounded",
    container_width="1100px",
    grid_gap="1rem",  # Tight grid
    section_spacing="3rem",  # Compact sections
    hero_padding_top="5rem",
    hero_padding_bottom="3rem",
    hero_line_height="1.2",
    hero_max_width="48rem",
)

SURFACE = SurfaceStyle(
    name="linear",
    shadow_sm="0 1px 3px rgba(0, 0, 0, 0.08)",
    shadow_md="0 3px 8px rgba(0, 0, 0, 0.1)",
    shadow_lg="0 8px 20px rgba(0, 0, 0, 0.12)",
    border_width="1px",
    border_style="solid",
    surface_treatment="flat",
    backdrop_blur="0px",
    noise_opacity=0.0,
)

ICON = IconStyle(
    name="linear",
    style="outlined",
    weight="regular",
    size_scale=1.0,
    stroke_width="1.5",  # Thin, precise
    corner_rounding="1px",
)

ANIMATION = AnimationStyle(
    name="linear",
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
    duration_slow="0.2s",
    easing="ease-out",
)

INTERACTION = InteractionStyle(
    name="linear",
    button_hover="darken",
    link_hover="color",
    card_hover="shadow",
    focus_style="ring",
    focus_ring_width="2px",
)

DESIGN_SYSTEM = DesignSystem(
    name="linear",
    display_name="Linear",
    description="Dark-first productivity — precise typography, tight spacing, purple accent",
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
    name="linear",
    display_name="Linear",
    description="Dark-first productivity — purple accent, precise density",
    category="professional",
    design_theme="linear",
    color_preset="linear",
    icon_style=ICON,
    animation_style=ANIMATION,
    pattern_style=PATTERN_MINIMAL,
    interaction_style=INTERACTION,
    illustration_style=ILLUST_LINE,
)
