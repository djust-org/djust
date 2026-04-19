"""djust.org -- djust.org brand — dark with rust orange and Django green accents."""

from ._base import (
    AnimationStyle,
    ColorScale,
    DesignSystem,
    IconStyle,
    InteractionStyle,
    LayoutStyle,
    SurfaceStyle,
    SurfaceTreatment,
    ThemePack,
    ThemePreset,
    ThemeTokens,
    TypographyStyle,
    ANIM_SMOOTH,
    ICON_OUTLINED,
    ILLUST_LINE,
    INTERACT_SUBTLE,
    PATTERN_DOTS,
)

# --- Color Preset ---

LIGHT = ThemeTokens(
    background=ColorScale(220, 10, 98),
    foreground=ColorScale(220, 10, 4),
    card=ColorScale(220, 10, 100),
    card_foreground=ColorScale(220, 10, 4),
    popover=ColorScale(220, 10, 100),
    popover_foreground=ColorScale(220, 10, 4),
    primary=ColorScale(28, 80, 53),
    primary_foreground=ColorScale(0, 0, 100),
    secondary=ColorScale(154, 48, 39),
    secondary_foreground=ColorScale(0, 0, 100),
    muted=ColorScale(215, 14, 93),
    muted_foreground=ColorScale(215, 10, 45),
    accent=ColorScale(157, 46, 39),
    accent_foreground=ColorScale(0, 0, 100),
    destructive=ColorScale(350, 89, 60),
    destructive_foreground=ColorScale(0, 0, 100),
    success=ColorScale(160, 84, 39),
    success_foreground=ColorScale(0, 0, 100),
    warning=ColorScale(38, 92, 50),
    warning_foreground=ColorScale(0, 0, 100),
    info=ColorScale(199, 89, 48),
    info_foreground=ColorScale(0, 0, 100),
    link=ColorScale(28, 80, 53),
    link_hover=ColorScale(28, 80, 45),
    code=ColorScale(215, 14, 93),
    code_foreground=ColorScale(215, 10, 20),
    selection=ColorScale(28, 80, 53),
    selection_foreground=ColorScale(0, 0, 100),
    brand=ColorScale(28, 80, 53),
    brand_foreground=ColorScale(0, 0, 100),
    border=ColorScale(215, 14, 88),
    input=ColorScale(215, 14, 88),
    ring=ColorScale(28, 80, 53),
    surface_1=ColorScale(220, 10, 98),
    surface_2=ColorScale(220, 10, 95),
    surface_3=ColorScale(220, 10, 92),
)

DARK = ThemeTokens(
    background=ColorScale(223, 39, 7),
    foreground=ColorScale(214, 32, 91),
    card=ColorScale(224, 34, 13),
    card_foreground=ColorScale(214, 32, 91),
    popover=ColorScale(224, 34, 13),
    popover_foreground=ColorScale(214, 32, 91),
    primary=ColorScale(28, 80, 55),
    primary_foreground=ColorScale(0, 0, 100),
    secondary=ColorScale(154, 48, 53),
    secondary_foreground=ColorScale(0, 0, 100),
    muted=ColorScale(224, 30, 16),
    muted_foreground=ColorScale(215, 20, 65),
    accent=ColorScale(157, 46, 49),
    accent_foreground=ColorScale(0, 0, 100),
    destructive=ColorScale(350, 89, 60),
    destructive_foreground=ColorScale(0, 0, 100),
    success=ColorScale(160, 84, 39),
    success_foreground=ColorScale(0, 0, 100),
    warning=ColorScale(38, 92, 50),
    warning_foreground=ColorScale(0, 0, 100),
    info=ColorScale(199, 89, 48),
    info_foreground=ColorScale(0, 0, 100),
    link=ColorScale(28, 80, 55),
    link_hover=ColorScale(28, 80, 65),
    code=ColorScale(224, 34, 13),
    code_foreground=ColorScale(214, 32, 91),
    selection=ColorScale(28, 80, 55),
    selection_foreground=ColorScale(0, 0, 100),
    brand=ColorScale(28, 80, 55),
    brand_foreground=ColorScale(0, 0, 100),
    border=ColorScale(215, 25, 18),
    input=ColorScale(215, 25, 18),
    ring=ColorScale(28, 80, 55),
    surface_1=ColorScale(223, 39, 7),
    surface_2=ColorScale(224, 34, 13),
    surface_3=ColorScale(215, 25, 18),
)

PRESET = ThemePreset(
    name="djust",
    display_name="djust.org",
    description="djust.org brand — dark with rust orange and Django green accents",
    light=LIGHT,
    dark=DARK,
    default_mode="dark",
    extra_css_vars={
        "color-brand-dark": "#0B0F19",
        "color-brand-panel": "#151B2B",
        "color-brand-rust": "#E57324",
        "color-brand-django": "#44B78B",
        "color-brand-text": "#E2E8F0",
        "color-brand-muted": "#94A3B8",
        "color-brand-border": "#1E293B",
        "color-brand-danger": "#F43F5E",
        "color-brand-success": "#10B981",
        "background-image-grid-pattern": "linear-gradient(to right, #1e293b 1px, transparent 1px), linear-gradient(to bottom, #1e293b 1px, transparent 1px)",
        "background-image-gradient-radial": "radial-gradient(var(--tw-gradient-stops))",
        "animation-pulse-slow": "pulse 4s cubic-bezier(0.4, 0, 0.6, 1) infinite",
    },
    extra_css_vars_light={
        "color-brand-dark": "#f8fafc",
        "color-brand-panel": "#f1f5f9",
        "color-brand-text": "#0f172a",
        "color-brand-muted": "#64748b",
        "color-brand-border": "#e2e8f0",
    },
    surface=SurfaceTreatment(
        style="glass",
        glass_background="rgba(21, 27, 43, 0.7)",
        glass_border="rgba(255, 255, 255, 0.1)",
        glass_blur="12px",
    ),
)

# --- Design System ---

TYPOGRAPHY = TypographyStyle(
    name="djust",
    heading_font="Inter, sans-serif",
    body_font="Inter, sans-serif",
    heading_scale=1.35,
    heading_weight="800",
    letter_spacing="-0.025em",
)

LAYOUT = LayoutStyle(
    name="djust",
    border_radius_sm="0.375rem",
    border_radius_lg="0.75rem",
    card_shape="organic",
    container_width="1280px",
    section_spacing="6rem",
)

SURFACE = SurfaceStyle(
    name="djust",
    shadow_sm="0 1px 3px rgba(0,0,0,0.3)",
    shadow_md="0 10px 15px -3px rgba(0,0,0,0.3), 0 4px 6px -2px rgba(0,0,0,0.2)",
    shadow_lg="0 20px 25px -5px rgba(0,0,0,0.3), 0 10px 10px -5px rgba(0,0,0,0.15)",
    surface_treatment="glass",
    backdrop_blur="12px",
)

ICON = IconStyle(
    name="djust",
    style="outlined",
    weight="regular",
    corner_rounding="0px",
)

ANIMATION = AnimationStyle(
    name="djust",
    duration_normal="0.2s",
    duration_slow="0.3s",
)

INTERACTION = InteractionStyle(
    name="djust",
    card_hover="shadow",
)

DESIGN_SYSTEM = DesignSystem(
    name="djust",
    display_name="djust.org",
    description="djust.org brand — dark, professional, with rust orange accents",
    category="professional",
    typography=TYPOGRAPHY,
    layout=LAYOUT,
    surface=SURFACE,
    icons=ICON,
    animation=ANIMATION,
    interaction=INTERACTION,
)

# --- Theme Pack ---

PACK = ThemePack(
    name="djust",
    display_name="djust.org",
    description="djust.org brand — dark, professional, with rust orange accents",
    category="professional",
    design_theme="djust",
    color_preset="djust",
    icon_style=ICON_OUTLINED,
    animation_style=ANIM_SMOOTH,
    pattern_style=PATTERN_DOTS,
    interaction_style=INTERACT_SUBTLE,
    illustration_style=ILLUST_LINE,
)
