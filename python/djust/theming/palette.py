"""
Brand Color Auto-Palette Generator (I21).

Derives a complete ThemePreset (light + dark modes, 31 color fields each)
from 1-3 brand colors using HSL color math and WCAG contrast validation.
"""

from __future__ import annotations

from .colors import hex_to_hsl
from .presets import ColorScale, ThemePreset, ThemeTokens


# ---------------------------------------------------------------------------
# Mode configuration
# ---------------------------------------------------------------------------

_MODE_PARAMS = {
    "professional": {
        "sat_scale": 0.85,
        "secondary_hue_offset": 180,
        "accent_hue_offset": 30,
        "bg_sat": 1,
        "muted_sat": 6,
        "radius": 0.5,
    },
    "playful": {
        "sat_scale": 1.15,
        "secondary_hue_offset": 150,
        "accent_hue_offset": 60,
        "bg_sat": 3,
        "muted_sat": 8,
        "radius": 0.75,
    },
    "muted": {
        "sat_scale": 0.55,
        "secondary_hue_offset": 180,
        "accent_hue_offset": 20,
        "bg_sat": 3,
        "muted_sat": 5,
        "radius": 0.375,
    },
    "vibrant": {
        "sat_scale": 1.30,
        "secondary_hue_offset": 120,
        "accent_hue_offset": 45,
        "bg_sat": 2,
        "muted_sat": 8,
        "radius": 0.5,
    },
}


# ---------------------------------------------------------------------------
# Contrast helpers
# ---------------------------------------------------------------------------


def _luminance(color: ColorScale) -> float:
    """Relative luminance of a ColorScale (WCAG formula)."""
    from .colors import hsl_to_rgb

    r, g, b = hsl_to_rgb(color.h, color.s, color.lightness)
    channels = []
    for c in (r, g, b):
        c_norm = c / 255.0
        if c_norm <= 0.03928:
            channels.append(c_norm / 12.92)
        else:
            channels.append(((c_norm + 0.055) / 1.055) ** 2.4)
    return 0.2126 * channels[0] + 0.7152 * channels[1] + 0.0722 * channels[2]


def _contrast_ratio(c1: ColorScale, c2: ColorScale) -> float:
    l1 = _luminance(c1)
    l2 = _luminance(c2)
    lighter = max(l1, l2)
    darker = min(l1, l2)
    return (lighter + 0.05) / (darker + 0.05)


def _binary_search_lightness(
    fg: ColorScale, bg: ColorScale, low: float, high: float, min_ratio: float
) -> ColorScale | None:
    """Binary search for fg lightness in [low, high] that meets min_ratio against bg."""
    best = None
    for _ in range(25):
        mid = (low + high) / 2.0
        candidate = ColorScale(fg.h, fg.s, round(mid))
        ratio = _contrast_ratio(candidate, bg)
        if ratio >= min_ratio:
            best = candidate
            # Try to stay closer to original (narrower range)
            if mid < fg.lightness:
                low = mid  # found a dark-enough value, try lighter
            else:
                high = mid  # found a light-enough value, try darker
        else:
            if mid < fg.lightness:
                high = mid  # too light, go darker
            else:
                low = mid  # too dark, go lighter
    return best


def _ensure_contrast(fg: ColorScale, bg: ColorScale, min_ratio: float = 4.5) -> ColorScale:
    """Adjust *fg* lightness until contrast against *bg* meets *min_ratio*.

    Tries the preferred direction first (darken on light bg, lighten on dark bg).
    Falls back to the opposite direction if the preferred one can't reach the target.
    """
    if _contrast_ratio(fg, bg) >= min_ratio:
        return fg

    bg_l = bg.lightness

    # Preferred direction: darken fg on light bg, lighten fg on dark bg
    if bg_l > 50:
        result = _binary_search_lightness(fg, bg, 0, fg.lightness, min_ratio)
        if result is None:
            # Fallback: try lightening instead
            result = _binary_search_lightness(fg, bg, fg.lightness, 100, min_ratio)
    else:
        result = _binary_search_lightness(fg, bg, fg.lightness, 100, min_ratio)
        if result is None:
            # Fallback: try darkening instead
            result = _binary_search_lightness(fg, bg, 0, fg.lightness, min_ratio)

    return result if result is not None else fg


def _clamp(val: float, lo: float, hi: float) -> int:
    return round(max(lo, min(hi, val)))


def _pick_fg_on(bg: ColorScale) -> ColorScale:
    """Return near-white or near-black, whichever has more contrast on *bg*."""
    white = ColorScale(0, 0, 98)
    black = ColorScale(0, 0, 4)
    if _contrast_ratio(white, bg) >= _contrast_ratio(black, bg):
        return white
    return black


# ---------------------------------------------------------------------------
# Token builders
# ---------------------------------------------------------------------------


def _build_light_tokens(
    primary_h: int,
    primary_s: int,
    primary_l: int,
    sec_h: int,
    sec_s: int,
    acc_h: int,
    acc_s: int,
    params: dict,
) -> ThemeTokens:
    sat_scale = params["sat_scale"]
    bg_sat = params["bg_sat"]
    muted_sat = params["muted_sat"]

    # Primary with clamped lightness for usable contrast
    primary = ColorScale(
        primary_h, _clamp(primary_s * sat_scale, 0, 100), _clamp(primary_l, 35, 55)
    )
    primary_fg = _pick_fg_on(primary)

    # Surfaces
    background = ColorScale(primary_h, bg_sat, 100)
    foreground = ColorScale(primary_h, 10, 4)
    card = ColorScale(primary_h, bg_sat, 100)
    popover = ColorScale(primary_h, bg_sat, 100)

    # Secondary — subtle tinted background
    secondary = ColorScale(sec_h, _clamp(sec_s * 0.15, 15, 25), 96)
    secondary_fg = ColorScale(sec_h, _clamp(sec_s * sat_scale, 0, 100), 10)

    # Accent — subtle tinted background
    accent = ColorScale(acc_h, _clamp(acc_s * 0.15, 15, 25), 96)
    accent_fg = ColorScale(acc_h, _clamp(acc_s * sat_scale, 0, 100), 10)

    # Muted
    muted = ColorScale(primary_h, muted_sat, 96)
    muted_fg = ColorScale(primary_h, muted_sat, 40)

    # Semantic states
    destructive = ColorScale(0, _clamp(84 * sat_scale, 40, 100), 60)
    destructive_fg = _pick_fg_on(destructive)
    success = ColorScale(142, _clamp(76 * sat_scale, 40, 100), 36)
    success_fg = _pick_fg_on(success)
    warning = ColorScale(38, _clamp(92 * sat_scale, 40, 100), 50)
    warning_fg = _pick_fg_on(warning)
    info = ColorScale(199, _clamp(89 * sat_scale, 40, 100), 48)
    info_fg = _pick_fg_on(info)

    # Extensions
    link = ColorScale(primary_h, primary_s, 43)
    link_hover = ColorScale(primary_h, primary_s, 35)
    code = ColorScale(primary_h, 5, 94)
    code_fg = ColorScale(primary_h, 10, 20)
    selection = ColorScale(primary_h, _clamp(primary_s, 30, 100), 80)
    selection_fg = foreground

    # UI chrome
    border = ColorScale(primary_h, 6, 90)
    input_border = ColorScale(primary_h, 6, 90)
    ring = primary

    tokens = ThemeTokens(
        background=background,
        foreground=foreground,
        card=card,
        card_foreground=foreground,
        popover=popover,
        popover_foreground=foreground,
        primary=primary,
        primary_foreground=primary_fg,
        secondary=secondary,
        secondary_foreground=secondary_fg,
        muted=muted,
        muted_foreground=muted_fg,
        accent=accent,
        accent_foreground=accent_fg,
        destructive=destructive,
        destructive_foreground=destructive_fg,
        success=success,
        success_foreground=success_fg,
        warning=warning,
        warning_foreground=warning_fg,
        info=info,
        info_foreground=info_fg,
        link=link,
        link_hover=link_hover,
        code=code,
        code_foreground=code_fg,
        selection=selection,
        selection_foreground=selection_fg,
        brand=primary,
        brand_foreground=primary_fg,
        border=border,
        input=input_border,
        ring=ring,
        surface_1=ColorScale(0, 0, 99),
        surface_2=ColorScale(0, 0, 97),
        surface_3=ColorScale(0, 0, 95),
    )

    return _fix_light_contrast(tokens)


def _build_dark_tokens(
    primary_h: int,
    primary_s: int,
    primary_l_light: int,
    sec_h: int,
    sec_s: int,
    acc_h: int,
    acc_s: int,
    params: dict,
) -> ThemeTokens:
    sat_scale = params["sat_scale"]

    # Surfaces
    background = ColorScale(primary_h, 10, 4)
    foreground = ColorScale(0, 0, 98)
    card = ColorScale(primary_h, 10, 6)
    popover = ColorScale(primary_h, 10, 6)

    # Primary — lighter in dark mode
    dark_primary_l = _clamp(85 - primary_l_light, 50, 75)
    primary = ColorScale(primary_h, _clamp(primary_s * sat_scale * 0.9, 0, 100), dark_primary_l)
    primary_fg = _pick_fg_on(primary)

    # Secondary
    secondary = ColorScale(sec_h, _clamp(sec_s * 0.12, 10, 15), 15)
    secondary_fg = ColorScale(sec_h, 5, 90)

    # Accent
    accent = ColorScale(acc_h, _clamp(acc_s * 0.12, 10, 15), 15)
    accent_fg = ColorScale(acc_h, 5, 90)

    # Muted
    muted = ColorScale(primary_h, 8, 15)
    muted_fg = ColorScale(primary_h, 5, 65)

    # Semantic states — slightly desaturated, lighter for dark bg
    destructive = ColorScale(0, _clamp(80 * sat_scale, 40, 100), 55)
    destructive_fg = _pick_fg_on(destructive)
    success = ColorScale(142, _clamp(70 * sat_scale, 40, 100), 45)
    success_fg = _pick_fg_on(success)
    warning = ColorScale(38, _clamp(85 * sat_scale, 40, 100), 55)
    warning_fg = _pick_fg_on(warning)
    info = ColorScale(199, _clamp(80 * sat_scale, 40, 100), 55)
    info_fg = _pick_fg_on(info)

    # Extensions
    link = ColorScale(primary_h, primary_s, 65)
    link_hover = ColorScale(primary_h, primary_s, 75)
    code = ColorScale(primary_h, 8, 12)
    code_fg = ColorScale(primary_h, 10, 80)
    selection = ColorScale(primary_h, _clamp(primary_s * 0.6, 20, 80), 25)
    selection_fg = foreground

    # UI chrome
    border = ColorScale(primary_h, 6, 18)
    input_border = ColorScale(primary_h, 6, 18)
    ring = primary

    tokens = ThemeTokens(
        background=background,
        foreground=foreground,
        card=card,
        card_foreground=foreground,
        popover=popover,
        popover_foreground=foreground,
        primary=primary,
        primary_foreground=primary_fg,
        secondary=secondary,
        secondary_foreground=secondary_fg,
        muted=muted,
        muted_foreground=muted_fg,
        accent=accent,
        accent_foreground=accent_fg,
        destructive=destructive,
        destructive_foreground=destructive_fg,
        success=success,
        success_foreground=success_fg,
        warning=warning,
        warning_foreground=warning_fg,
        info=info,
        info_foreground=info_fg,
        link=link,
        link_hover=link_hover,
        code=code,
        code_foreground=code_fg,
        selection=selection,
        selection_foreground=selection_fg,
        brand=primary,
        brand_foreground=primary_fg,
        border=border,
        input=input_border,
        ring=ring,
        surface_1=ColorScale(primary_h, _clamp(primary_s, 5, 15), 8),
        surface_2=ColorScale(primary_h, _clamp(primary_s, 5, 15), 12),
        surface_3=ColorScale(primary_h, _clamp(primary_s, 5, 15), 16),
    )

    return _fix_dark_contrast(tokens)


# ---------------------------------------------------------------------------
# WCAG auto-fix passes
# ---------------------------------------------------------------------------


def _fix_light_contrast(t: ThemeTokens) -> ThemeTokens:
    """Ensure all light-mode fg/bg pairs meet WCAG AA."""
    return ThemeTokens(
        background=t.background,
        foreground=_ensure_contrast(t.foreground, t.background),
        card=t.card,
        card_foreground=_ensure_contrast(t.card_foreground, t.card),
        popover=t.popover,
        popover_foreground=_ensure_contrast(t.popover_foreground, t.popover),
        primary=t.primary,
        primary_foreground=_ensure_contrast(t.primary_foreground, t.primary),
        secondary=t.secondary,
        secondary_foreground=_ensure_contrast(t.secondary_foreground, t.secondary),
        muted=t.muted,
        muted_foreground=_ensure_contrast(t.muted_foreground, t.muted),
        accent=t.accent,
        accent_foreground=_ensure_contrast(t.accent_foreground, t.accent),
        destructive=t.destructive,
        destructive_foreground=_ensure_contrast(t.destructive_foreground, t.destructive),
        success=t.success,
        success_foreground=_ensure_contrast(t.success_foreground, t.success),
        warning=t.warning,
        warning_foreground=_ensure_contrast(t.warning_foreground, t.warning),
        info=t.info,
        info_foreground=_ensure_contrast(t.info_foreground, t.info),
        link=_ensure_contrast(t.link, t.background),
        link_hover=_ensure_contrast(t.link_hover, t.background),
        code=t.code,
        code_foreground=_ensure_contrast(t.code_foreground, t.code),
        selection=t.selection,
        selection_foreground=_ensure_contrast(t.selection_foreground, t.selection),
        brand=t.brand,
        brand_foreground=_ensure_contrast(t.brand_foreground, t.brand),
        border=_ensure_contrast(t.border, t.background, min_ratio=3.0),
        input=_ensure_contrast(t.input, t.background, min_ratio=3.0),
        ring=t.ring,
        surface_1=t.surface_1,
        surface_2=t.surface_2,
        surface_3=t.surface_3,
    )


def _fix_dark_contrast(t: ThemeTokens) -> ThemeTokens:
    """Ensure all dark-mode fg/bg pairs meet WCAG AA."""
    return ThemeTokens(
        background=t.background,
        foreground=_ensure_contrast(t.foreground, t.background),
        card=t.card,
        card_foreground=_ensure_contrast(t.card_foreground, t.card),
        popover=t.popover,
        popover_foreground=_ensure_contrast(t.popover_foreground, t.popover),
        primary=t.primary,
        primary_foreground=_ensure_contrast(t.primary_foreground, t.primary),
        secondary=t.secondary,
        secondary_foreground=_ensure_contrast(t.secondary_foreground, t.secondary),
        muted=t.muted,
        muted_foreground=_ensure_contrast(t.muted_foreground, t.muted),
        accent=t.accent,
        accent_foreground=_ensure_contrast(t.accent_foreground, t.accent),
        destructive=t.destructive,
        destructive_foreground=_ensure_contrast(t.destructive_foreground, t.destructive),
        success=t.success,
        success_foreground=_ensure_contrast(t.success_foreground, t.success),
        warning=t.warning,
        warning_foreground=_ensure_contrast(t.warning_foreground, t.warning),
        info=t.info,
        info_foreground=_ensure_contrast(t.info_foreground, t.info),
        link=_ensure_contrast(t.link, t.background),
        link_hover=_ensure_contrast(t.link_hover, t.background),
        code=t.code,
        code_foreground=_ensure_contrast(t.code_foreground, t.code),
        selection=t.selection,
        selection_foreground=_ensure_contrast(t.selection_foreground, t.selection),
        brand=t.brand,
        brand_foreground=_ensure_contrast(t.brand_foreground, t.brand),
        border=_ensure_contrast(t.border, t.background, min_ratio=3.0),
        input=_ensure_contrast(t.input, t.background, min_ratio=3.0),
        ring=t.ring,
        surface_1=t.surface_1,
        surface_2=t.surface_2,
        surface_3=t.surface_3,
    )


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


class PaletteGenerator:
    """Derives a complete ThemePreset from 1-3 brand colors."""

    MODES = ("professional", "playful", "muted", "vibrant")

    @classmethod
    def from_brand_colors(
        cls,
        primary: str,
        secondary: str | None = None,
        accent: str | None = None,
        mode: str = "professional",
    ) -> ThemePreset:
        """Generate a complete ThemePreset from brand colors.

        Args:
            primary: Primary brand color as hex (#RRGGBB or #RGB).
            secondary: Secondary color. If None, derived as complementary.
            accent: Accent color. If None, derived as analogous.
            mode: Generation style -- professional, playful, muted, vibrant.

        Returns:
            Complete ThemePreset with light and dark modes.

        Raises:
            ValueError: If mode is invalid or colors are not valid hex.
        """
        if mode not in cls.MODES:
            raise ValueError(f"Invalid mode {mode!r}. Choose from: {', '.join(cls.MODES)}")

        params = _MODE_PARAMS[mode]

        # Parse primary (will raise ValueError on bad hex)
        p_h, p_s, p_l = hex_to_hsl(primary)

        # Derive or parse secondary
        if secondary is not None:
            s_h, s_s, _s_l = hex_to_hsl(secondary)
        else:
            s_h = (p_h + params["secondary_hue_offset"]) % 360
            s_s = _clamp(p_s * params["sat_scale"], 0, 100)

        # Derive or parse accent
        if accent is not None:
            a_h, a_s, _a_l = hex_to_hsl(accent)
        else:
            a_h = (p_h + params["accent_hue_offset"]) % 360
            a_s = _clamp(p_s * params["sat_scale"], 0, 100)

        light = _build_light_tokens(p_h, p_s, p_l, s_h, s_s, a_h, a_s, params)
        dark = _build_dark_tokens(p_h, p_s, p_l, s_h, s_s, a_h, a_s, params)

        # Build a readable name from the primary hex
        name = f"brand-{primary.lstrip('#').lower()}"
        display_name = f"Brand {primary.upper()}"

        return ThemePreset(
            name=name,
            display_name=display_name,
            light=light,
            dark=dark,
            description=f"Auto-generated {mode} palette from {primary}",
            radius=params["radius"],
        )
