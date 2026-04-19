"""Kanagawa — Hokusai's Great Wave, muted Japanese palette."""

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
# Colors from Kanagawa (rebelot/kanagawa.nvim):
# - sumiInk0 #16161d, sumiInk1 #1f1f28 (bg)
# - fujiWhite #dcd7ba (fg), oldWhite #c8c093
# - waveBlue1 #223249, crystalBlue #7e9cd8
# - sakuraPink #d27e99, surimiOrange #ffa066
# - springGreen #98bb6c, carpYellow #e6c384
# - peachRed #ff5d62, waveAqua #7aa89f

LIGHT = ThemeTokens(
    background=ColorScale(45, 30, 96),  # Warm cream bg
    foreground=ColorScale(240, 13, 14),  # Near-black fg
    card=ColorScale(45, 25, 98),
    card_foreground=ColorScale(240, 13, 14),
    popover=ColorScale(45, 25, 98),
    popover_foreground=ColorScale(240, 13, 14),
    primary=ColorScale(220, 54, 67),  # crystalBlue
    primary_foreground=ColorScale(0, 0, 100),
    secondary=ColorScale(45, 20, 93),
    secondary_foreground=ColorScale(240, 13, 14),
    muted=ColorScale(45, 15, 88),
    muted_foreground=ColorScale(240, 10, 45),
    accent=ColorScale(45, 20, 93),  # Subtle warm hover
    accent_foreground=ColorScale(240, 13, 14),
    destructive=ColorScale(359, 52, 51),  # peachRed muted
    destructive_foreground=ColorScale(0, 0, 100),
    success=ColorScale(103, 17, 50),  # springGreen muted
    success_foreground=ColorScale(0, 0, 100),
    warning=ColorScale(39, 39, 59),  # carpYellow muted
    warning_foreground=ColorScale(240, 13, 14),
    info=ColorScale(163, 17, 50),  # waveAqua
    info_foreground=ColorScale(0, 0, 100),
    link=ColorScale(220, 54, 67),  # crystalBlue
    link_hover=ColorScale(220, 54, 55),
    code=ColorScale(240, 13, 14),  # Dark code bg
    code_foreground=ColorScale(51, 33, 80),  # fujiWhite
    selection=ColorScale(220, 40, 88),  # Pale blue selection
    selection_foreground=ColorScale(240, 13, 14),
    brand=ColorScale(263, 29, 61),  # oniViolet
    brand_foreground=ColorScale(0, 0, 100),
    border=ColorScale(45, 15, 85),
    input=ColorScale(45, 15, 91),
    ring=ColorScale(220, 54, 67),
    surface_1=ColorScale(45, 30, 96),
    surface_2=ColorScale(45, 22, 93),
    surface_3=ColorScale(45, 15, 90),
)

DARK = ThemeTokens(
    background=ColorScale(240, 13, 14),  # #1f1f28 sumiInk1
    foreground=ColorScale(51, 33, 80),  # #dcd7ba fujiWhite
    card=ColorScale(240, 12, 18),
    card_foreground=ColorScale(51, 33, 80),
    popover=ColorScale(240, 12, 18),
    popover_foreground=ColorScale(51, 33, 80),
    primary=ColorScale(220, 54, 67),  # crystalBlue #7e9cd8
    primary_foreground=ColorScale(240, 13, 14),
    secondary=ColorScale(240, 12, 21),
    secondary_foreground=ColorScale(51, 33, 80),
    muted=ColorScale(240, 10, 24),
    muted_foreground=ColorScale(240, 10, 45),
    accent=ColorScale(240, 12, 21),  # Subtle dark hover
    accent_foreground=ColorScale(51, 33, 80),
    destructive=ColorScale(359, 52, 51),  # peachRed
    destructive_foreground=ColorScale(0, 0, 100),
    success=ColorScale(103, 17, 50),  # springGreen
    success_foreground=ColorScale(240, 13, 14),
    warning=ColorScale(39, 39, 59),  # carpYellow
    warning_foreground=ColorScale(240, 13, 14),
    info=ColorScale(163, 17, 50),  # waveAqua
    info_foreground=ColorScale(240, 13, 14),
    link=ColorScale(220, 54, 67),  # crystalBlue
    link_hover=ColorScale(220, 54, 76),
    code=ColorScale(215, 36, 21),  # waveBlue1 #223249
    code_foreground=ColorScale(51, 33, 80),  # fujiWhite
    selection=ColorScale(220, 40, 24),  # Deep blue selection
    selection_foreground=ColorScale(51, 33, 85),
    brand=ColorScale(263, 29, 61),  # oniViolet
    brand_foreground=ColorScale(0, 0, 100),
    border=ColorScale(240, 10, 26),
    input=ColorScale(240, 10, 26),
    ring=ColorScale(220, 54, 67),
    surface_1=ColorScale(240, 13, 10),  # sumiInk0
    surface_2=ColorScale(240, 13, 14),  # sumiInk1
    surface_3=ColorScale(240, 12, 18),  # sumiInk2
)

PRESET = ThemePreset(
    name="kanagawa",
    display_name="Kanagawa",
    description="Hokusai's Great Wave — muted Japanese palette with wave blues",
    light=LIGHT,
    dark=DARK,
    radius=0.25,
    default_mode="dark",
)


# =============================================================================
# Design System
# =============================================================================

TYPOGRAPHY = TypographyStyle(
    name="kanagawa",
    heading_font='"Noto Serif", "Georgia", serif',
    body_font='"Noto Sans", system-ui, sans-serif',
    base_size="16px",
    heading_scale=1.25,
    line_height="1.5",
    body_line_height="1.65",
    heading_weight="400",
    section_heading_weight="400",
    body_weight="400",
    letter_spacing="normal",
    prose_max_width="40rem",
    badge_radius="4px",
)

LAYOUT = LayoutStyle(
    name="kanagawa",
    space_unit="1rem",
    space_scale=1.5,
    border_radius_sm="2px",
    border_radius_md="4px",
    border_radius_lg="6px",
    button_shape="rounded",
    card_shape="rounded",
    input_shape="rounded",
    container_width="1100px",
    grid_gap="1.5rem",
    section_spacing="4rem",
    hero_padding_top="7rem",
    hero_padding_bottom="5rem",
    hero_line_height="1.2",
    hero_max_width="50rem",
)

SURFACE = SurfaceStyle(
    name="kanagawa",
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
    name="kanagawa",
    style="outlined",
    weight="thin",
    size_scale=1.0,
    stroke_width="1.5",
    corner_rounding="1px",
)

ANIMATION = AnimationStyle(
    name="kanagawa",
    entrance_effect="fade",
    exit_effect="fade",
    hover_effect="none",
    hover_scale=1.0,
    hover_translate_y="0px",
    click_effect="none",
    loading_style="spinner",
    transition_style="snappy",
    duration_fast="0.08s",
    duration_normal="0.1s",
    duration_slow="0.2s",
    easing="cubic-bezier(0.4, 0, 0.2, 1)",
)

INTERACTION = InteractionStyle(
    name="kanagawa",
    button_hover="darken",
    link_hover="color",
    card_hover="border",
    focus_style="ring",
    focus_ring_width="2px",
)

DESIGN_SYSTEM = DesignSystem(
    name="kanagawa",
    display_name="Kanagawa",
    description="Japanese minimalism — serif/sans mix, no shadows, snappy transitions",
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
    name="kanagawa",
    display_name="Kanagawa",
    description="Hokusai's Great Wave — muted Japanese palette with wave blues",
    category="developer",
    design_theme="kanagawa",
    color_preset="kanagawa",
    icon_style=ICON,
    animation_style=ANIMATION,
    pattern_style=PATTERN_MINIMAL,
    interaction_style=INTERACTION,
    illustration_style=ILLUST_LINE,
)
