"""
Dependency-free dataclass types for the theming system.

This module exists to break a cyclic import between
``themes/_base.py`` → ``presets.py`` / ``theme_packs.py`` → ``themes/*``.
Theme files import the type definitions from here instead of from
``presets.py`` / ``theme_packs.py``, which allows those higher-level
modules to continue importing individual theme files at module level
without forming a cycle.

CodeQL flagged ~872 ``py/unsafe-cyclic-import`` alerts across the theme
files because of the previous structure. Moving the pure type definitions
here resolves them without behavioural change — ``presets.py`` and
``theme_packs.py`` re-export the types for backward compatibility so
existing user imports like ``from djust.theming.presets import ColorScale``
keep working.

IMPORTANT: this module MUST NOT import from anywhere else in ``djust``.
Only stdlib imports are allowed so the dependency graph stays acyclic.
"""

from dataclasses import dataclass
from functools import lru_cache
from typing import FrozenSet, Tuple


def _check_vocab(
    class_name: str,
    instance_name: "str | None",
    field_name: str,
    value: str,
    valid: "FrozenSet[str]",
) -> None:
    """Raise ValueError if ``value`` is outside the field's documented vocabulary.

    Called from ``__post_init__`` on the design-system style dataclasses below to
    turn a silent CSS-generator no-op (e.g. ``card_hover="glow"`` — compiled,
    tested green, rendered nothing because no generator branch matched it; see
    PR #2056 / issue #2057) into an authoring-time error naming the field, the
    bad value, and the full valid set.

    ``instance_name`` is the owning instance's ``name`` field for dataclasses
    that have one (most style dataclasses do); pass ``None`` for leaf value
    types without a ``name`` field (e.g. ``SurfaceTreatment``).
    """
    if value not in valid:
        where = f"(name={instance_name!r})" if instance_name is not None else ""
        raise ValueError(
            f"{class_name}{where}.{field_name}={value!r} is not a "
            f"recognized value. Valid values: {sorted(valid)}"
        )


# =============================================================================
# Color types (previously in presets.py)
# =============================================================================


@dataclass
class ColorScale:
    """HSL color representation for CSS custom properties."""

    h: int  # Hue 0-360
    s: int  # Saturation 0-100
    lightness: int  # Lightness 0-100

    def to_hsl(self) -> str:
        """Return HSL values for CSS variable (without hsl() wrapper)."""
        return f"{self.h} {self.s}% {self.lightness}%"

    def to_hsl_func(self) -> str:
        """Return complete hsl() function."""
        return f"hsl({self.h}, {self.s}%, {self.lightness}%)"

    def to_hex(self) -> str:
        """Return hex color string, e.g. '#3b82f6'."""
        from .colors import hsl_to_hex

        return hsl_to_hex(self.h, self.s, self.lightness)

    def to_rgb(self) -> Tuple[int, int, int]:
        """Return RGB tuple (0-255 each)."""
        from .colors import hsl_to_rgb

        return hsl_to_rgb(self.h, self.s, self.lightness)

    def to_rgb_func(self) -> str:
        """Return complete rgb() CSS function string, e.g. 'rgb(59, 130, 246)'."""
        r, g, b = self.to_rgb()
        return f"rgb({r}, {g}, {b})"

    @classmethod
    def from_hex(cls, hex_str: str) -> "ColorScale":
        """Create ColorScale from hex string (#RRGGBB or #RGB)."""
        from .colors import hex_to_hsl

        h, s, l = hex_to_hsl(hex_str)
        return cls(h, s, l)

    @classmethod
    def from_rgb(cls, r: int, g: int, b: int) -> "ColorScale":
        """Create ColorScale from RGB values (0-255 each)."""
        from .colors import rgb_to_hsl

        h, s, l = rgb_to_hsl(r, g, b)
        return cls(h, s, l)

    def with_lightness(self, new_lightness: int) -> "ColorScale":
        """Return a new ColorScale with modified lightness."""
        return ColorScale(self.h, self.s, new_lightness)

    def with_saturation(self, new_saturation: int) -> "ColorScale":
        """Return a new ColorScale with modified saturation."""
        return ColorScale(self.h, new_saturation, self.lightness)


def _luminance(rgb: Tuple[float, ...]) -> float:
    """WCAG relative luminance. Same formula as ``AccessibilityValidator``, which this
    module may not import (see the note at the top); a test pins that they agree."""

    def lin(channel: float) -> float:
        c = channel / 255.0
        return c / 12.92 if c <= 0.03928 else float(((c + 0.055) / 1.055) ** 2.4)

    r, g, b = rgb
    return 0.2126 * lin(r) + 0.7152 * lin(g) + 0.0722 * lin(b)


def _contrast(a: "ColorScale", b: "ColorScale") -> float:
    hi, lo = sorted((_luminance(a.to_rgb()), _luminance(b.to_rgb())), reverse=True)
    return (hi + 0.05) / (lo + 0.05)


#: WCAG AA for normal text. ``ThemeTokens.destructive_text`` is solved to this.
DESTRUCTIVE_TEXT_MIN_CONTRAST = 4.5

#: The destructive washes ``destructive_text`` must also read on: the 10% the
#: alert/toast/badge variants paint, and the 15% of the strongest wash that sits
#: under resting text (``.dj-announcement-bar--danger``, ``.dj-server-toast--error``).
#: A hover wash behind a remove glyph goes to 20%; that is a glyph, not text.
DESTRUCTIVE_WASH_ALPHAS: Tuple[float, ...] = (0.1, 0.15)


def _washes(
    base: Tuple[int, int, int], fg: Tuple[int, int, int], alpha: float
) -> Tuple[Tuple[float, ...], Tuple[int, int, int]]:
    """``fg`` at ``alpha`` over ``base``, two ways: exactly, as the browser paints
    ``hsl(var(--x) / alpha)``, and round-tripped through ``ColorScale`` the way
    ``ThemeTokens._tint`` (the contrast matrix's tint) does. The HSL integers move
    a channel by a step or two, which is enough to turn 4.51:1 into 4.46:1, so the
    text has to clear both."""
    exact = tuple(alpha * f + (1 - alpha) * c for f, c in zip(fg, base, strict=True))
    r, g, b = (round(v) for v in exact)
    return exact, ColorScale.from_rgb(r, g, b).to_rgb()


@lru_cache(maxsize=4096)
def _solve_destructive_text(
    destructive: Tuple[int, int, int],
    background: Tuple[int, int, int],
    card: Tuple[int, int, int],
) -> Tuple[int, int, int]:
    """Return ``(h, s, lightness)`` of the destructive colour, moved ONLY in
    lightness, until it reads as text on the page, on a card, and on destructive
    washes of both.

    ``destructive`` is an ``(h, s, l)`` triple and ``background``/``card`` are
    RGB triples, so the function is cacheable. A colour that already clears
    the bar is returned unchanged, so presets that were fine render exactly as
    before. Otherwise the nearest passing integer lightness wins, searching both
    directions (the tie goes to the side with more contrast against the page).
    If nothing in 0..100 passes (a mid-tone page that neither black nor white
    clears), the lightness with the best worst-case contrast is returned.
    """
    h, s, lightness = destructive
    fill_rgb = ColorScale(h, s, lightness).to_rgb()
    surfaces: list = [background, card]
    for alpha in DESTRUCTIVE_WASH_ALPHAS:
        for base in (background, card):
            surfaces.extend(_washes(base, fill_rgb, alpha))

    def worst(light: int) -> float:
        rgb = ColorScale(h, s, light).to_rgb()
        lum = _luminance(rgb)
        return min(
            (max(lum, sl) + 0.05) / (min(lum, sl) + 0.05)
            for sl in (_luminance(surface) for surface in surfaces)
        )

    if worst(lightness) >= DESTRUCTIVE_TEXT_MIN_CONTRAST:
        return (h, s, lightness)
    # Lighter first when the page is dark, darker first when it is light: the
    # tie-break for two equally near lightnesses.
    up_first = _luminance(background) < 0.18
    directions = (1, -1) if up_first else (-1, 1)
    for step in range(1, 101):
        for sign in directions:
            candidate = lightness + sign * step
            if 0 <= candidate <= 100 and worst(candidate) >= DESTRUCTIVE_TEXT_MIN_CONTRAST:
                return (h, s, candidate)
    best = max(range(101), key=worst)
    return (h, s, best)


@dataclass
class ThemeTokens:
    """
    Complete token set for a theme mode.

    Follows shadcn/ui naming conventions with extensions for
    success, warning, info, and additional semantic states.
    """

    # Backgrounds
    background: ColorScale
    foreground: ColorScale

    # Card surfaces
    card: ColorScale
    card_foreground: ColorScale

    # Popover surfaces
    popover: ColorScale
    popover_foreground: ColorScale

    # Primary action color
    primary: ColorScale
    primary_foreground: ColorScale

    # Secondary/muted action
    secondary: ColorScale
    secondary_foreground: ColorScale

    # Muted backgrounds
    muted: ColorScale
    muted_foreground: ColorScale

    # Accent for highlights
    accent: ColorScale
    accent_foreground: ColorScale

    # Destructive/error
    destructive: ColorScale
    destructive_foreground: ColorScale

    # Success state (extension)
    success: ColorScale
    success_foreground: ColorScale

    # Warning state (extension)
    warning: ColorScale
    warning_foreground: ColorScale

    # Info state (extension)
    info: ColorScale
    info_foreground: ColorScale

    # Link color (extension)
    link: ColorScale
    link_hover: ColorScale

    # Code/mono background (extension)
    code: ColorScale
    code_foreground: ColorScale

    # Selection/highlight (extension)
    selection: ColorScale
    selection_foreground: ColorScale

    # Brand/signature color (extension)
    # The distinctive identity color beyond primary. Dracula Pink, Nord Frost,
    # Catppuccin Rosewater, Itten Blue, etc. Themes without a distinct brand
    # color should set this to match primary.
    brand: ColorScale
    brand_foreground: ColorScale

    # UI elements
    border: ColorScale
    input: ColorScale
    ring: ColorScale

    # Surface levels for complex dark layouts (e.g., landing pages)
    # surface_1: darkest (ultra-dark background)
    # surface_2: mid-level (panels, navbar)
    # surface_3: elevated (cards, elevated elements)
    surface_1: ColorScale
    surface_2: ColorScale
    surface_3: ColorScale

    # Derived surfaces. Not tokens: no preset sets them and no CSS variable is
    # emitted. They exist so the contrast matrix can name the background that
    # ``.alert-*`` and ``.toast-*`` actually paint, which is the status colour
    # at 10% alpha over whatever it sits on (``hsl(var(--success) / 0.1)``), and
    # so check the status colour used as text on it (#3281).
    def _tint(self, colour: ColorScale, alpha: float = 0.1) -> ColorScale:
        """``colour`` at ``alpha`` composited over the page or a card, whichever
        gives the status text LESS contrast. An alert is placed on either, and
        the check must hold for both."""
        fg = colour.to_rgb()
        candidates = []
        for surface in (self.background, self.card):
            base = surface.to_rgb()
            candidates.append(
                ColorScale.from_rgb(
                    *(round(alpha * f + (1 - alpha) * b) for f, b in zip(fg, base, strict=True))
                )
            )
        return min(candidates, key=lambda tint: _contrast(colour, tint))

    @property
    def info_tint(self) -> ColorScale:
        return self._tint(self.info)

    @property
    def success_tint(self) -> ColorScale:
        return self._tint(self.success)

    @property
    def warning_tint(self) -> ColorScale:
        return self._tint(self.warning)

    @property
    def destructive_tint(self) -> ColorScale:
        return self._tint(self.destructive)

    @property
    def destructive_text(self) -> ColorScale:
        """``destructive`` as TEXT (#3320). Emitted as ``--destructive-text``.

        ``destructive`` is a FILL: many presets set it to a deep red under a
        white label (``0 62% 30%`` in dark ``default``), which is unreadable as
        text on the dark page, and the bright light-mode reds (``0 84% 60%``)
        are 3.8:1 on white. This is the same hue and saturation at the
        lightness that reaches 4.5:1 on the page, a card and the destructive
        washes, so error text, ``.text-destructive`` and an alert's text use it
        and ``destructive`` stays the fill. A preset whose ``destructive``
        already passes keeps it exactly. Read-only: no preset sets it.
        """
        d = self.destructive
        h, s, lightness = _solve_destructive_text(
            (d.h, d.s, d.lightness), self.background.to_rgb(), self.card.to_rgb()
        )
        return ColorScale(h, s, lightness)


VALID_SURFACE_TREATMENT_STYLES: "FrozenSet[str]" = frozenset({"glass", "gradient", "noise"})


@dataclass
class SurfaceTreatment:
    """Surface styling treatments for glass panels, gradients, and noise effects."""

    style: str = "glass"  # "glass" | "gradient" | "noise"

    # Glass surface properties
    glass_background: str = "rgba(21, 27, 43, 0.7)"
    glass_border: str = "rgba(255, 255, 255, 0.1)"
    glass_blur: str = "12px"
    surface_radius: str | None = None  # None means use --radius

    # Gradient surface properties
    gradient_direction: str = "180deg"
    gradient_from: str = "#1e293b"
    gradient_to: str = "#0f172a"

    # Noise surface properties
    noise_opacity: float = 0.03

    def __post_init__(self) -> None:
        # SurfaceTreatment has no ``name`` field of its own (it's a leaf value
        # nested under ThemePreset.surface).
        _check_vocab("SurfaceTreatment", None, "style", self.style, VALID_SURFACE_TREATMENT_STYLES)


VALID_THEME_PRESET_DEFAULT_MODES: "FrozenSet[str]" = frozenset({"light", "dark"})


@dataclass
class ThemePreset:
    """A complete theme with light and dark mode tokens."""

    name: str
    display_name: str
    light: ThemeTokens
    dark: ThemeTokens
    description: str = ""
    radius: float = 0.5  # Border radius multiplier (output as --radius: {val}rem)

    # Which mode is the default (emitted in :root)?
    # "light" = :root gets light tokens (standard shadcn behavior)
    # "dark" = :root gets dark tokens (for dark-first themes like djust.org)
    default_mode: str = "light"

    # Extra CSS custom properties beyond the standard shadcn set.
    # Use this for brand-specific variables like --color-brand-rust,
    # --background-image-grid-pattern, --animation-pulse-slow, etc.
    # These are emitted in the base :root block.
    extra_css_vars: dict | None = None

    # Per-mode brand CSS variables for light and dark modes.
    # Use these for brand surface colors that need to differ between modes
    # (e.g., --color-brand-dark: #0B0F19 in dark, #f8fafc in light).
    # If None, extra_css_vars is used for both modes.
    extra_css_vars_light: dict | None = None
    extra_css_vars_dark: dict | None = None

    # Surface treatment for glass panels, gradients, etc.
    surface: SurfaceTreatment | None = None

    def __post_init__(self) -> None:
        _check_vocab(
            "ThemePreset",
            self.name,
            "default_mode",
            self.default_mode,
            VALID_THEME_PRESET_DEFAULT_MODES,
        )


# =============================================================================
# Design-system types (previously in theme_packs.py, first definition block)
# =============================================================================


@dataclass
class TypographyStyle:
    """Typography configuration."""

    name: str

    # Font families — free-form CSS font-family value/stack (not an enum;
    # e.g. '"Inter", system-ui, sans-serif'). NOT validated at construction.
    heading_font: str = "system-ui"
    body_font: str = "system-ui"

    # Scale and sizing
    base_size: str = "16px"
    heading_scale: float = 1.25  # Multiplier between heading levels
    line_height: str = "1.5"
    body_line_height: str = "1.6"  # Relaxed line-height for body/paragraph text

    # Weight and style
    heading_weight: str = "600"  # "300", "400", "500", "600", "700", "800", "900"
    section_heading_weight: str = "700"  # Weight for section h2s (often lighter than hero)
    body_weight: str = "400"
    letter_spacing: str = "normal"  # CSS value: "normal", "-0.025em", "0.025em"

    # Form labels — how form field labels look (consumed by djust-components .form-label)
    form_label_weight: str = "500"  # "400" for BS4/gov, "500" for modern/djust default
    form_label_size: str = "0.875rem"  # var(--text-sm) by default; "1rem" for BS4

    # Measure
    prose_max_width: str = "42rem"  # Max width for readable text blocks
    badge_radius: str = "9999px"  # Badge border-radius (pill by default)


VALID_LAYOUT_SHAPES: "FrozenSet[str]" = frozenset({"sharp", "rounded", "pill", "organic"})


@dataclass
class LayoutStyle:
    """Layout and spacing configuration."""

    name: str

    # Spacing system
    space_unit: str = "1rem"  # Base unit for spacing
    space_scale: float = 1.5  # Ratio between spacing levels

    # Border radius system
    border_radius_sm: str = "0.25rem"
    border_radius_md: str = "0.5rem"
    border_radius_lg: str = "1rem"

    # Component shapes — consumed via a shape_map in pack_css_generator.py
    # (`_generate_layout_css`) and design_system_css.py.
    button_shape: str = "rounded"  # "sharp", "rounded", "pill", "organic"
    card_shape: str = "rounded"  # "sharp", "rounded", "pill", "organic"
    input_shape: str = "rounded"  # "sharp", "rounded", "pill", "organic"

    # Grid and layout
    container_width: str = "1200px"
    grid_gap: str = "1.5rem"
    section_spacing: str = "3rem"

    # Form layout — spacing between form fields and internal gaps
    form_group_margin: str = "1rem"  # Vertical space between form fields
    form_group_gap: str = "0.25rem"  # Internal gap (label → input)
    form_focus_ring_width: str = "3px"  # Focus outline thickness
    form_focus_ring_opacity: str = "0.2"  # Focus outline opacity (0–1)

    # Hero section
    hero_padding_top: str = "8rem"
    hero_padding_bottom: str = "5rem"
    hero_line_height: str = "1.1"
    hero_max_width: str = "64rem"  # Content width within hero

    def __post_init__(self) -> None:
        _check_vocab(
            "LayoutStyle", self.name, "button_shape", self.button_shape, VALID_LAYOUT_SHAPES
        )
        _check_vocab("LayoutStyle", self.name, "card_shape", self.card_shape, VALID_LAYOUT_SHAPES)
        _check_vocab("LayoutStyle", self.name, "input_shape", self.input_shape, VALID_LAYOUT_SHAPES)


VALID_SURFACE_BORDER_STYLES: "FrozenSet[str]" = frozenset({"solid", "dashed", "dotted", "none"})
# "textured" IS legitimately in use (theme_packs.py's registered DESIGN_RETRO
# design system) even though no CSS generator currently dispatches on it —
# same status as "neumorphic"/PatternStyle.surface_style below: implemented-
# nowhere-yet but a real, shipped, non-typo value, so it stays valid rather
# than being treated as dead. "noise" is a second real value (4 built-in
# `themes/*.py` files: paper grain, CRT scanline, handmade texture) that was
# undocumented before #2057 — added here alongside "textured", not instead
# of it.
VALID_SURFACE_TREATMENTS: "FrozenSet[str]" = frozenset(
    {"flat", "glass", "gradient", "noise", "textured"}
)


@dataclass
class SurfaceStyle:
    """Surface treatments and visual depth."""

    name: str

    # Shadow system
    shadow_sm: str = "0 1px 2px rgba(0,0,0,0.1)"
    shadow_md: str = "0 4px 6px rgba(0,0,0,0.1)"
    shadow_lg: str = "0 10px 15px rgba(0,0,0,0.1)"

    # Border system
    border_width: str = "1px"
    border_style: str = "solid"  # "solid", "dashed", "dotted", "none"

    # Background treatments
    surface_treatment: str = "flat"  # "flat", "glass", "gradient", "noise", "textured"
    backdrop_blur: str = "0px"
    noise_opacity: float = 0.0

    def __post_init__(self) -> None:
        _check_vocab(
            "SurfaceStyle",
            self.name,
            "border_style",
            self.border_style,
            VALID_SURFACE_BORDER_STYLES,
        )
        _check_vocab(
            "SurfaceStyle",
            self.name,
            "surface_treatment",
            self.surface_treatment,
            VALID_SURFACE_TREATMENTS,
        )


# "duotone" was documented but never consumed by pack_css_generator.py's
# icon.style dispatch (`_generate_icon_css`) and never used by a built-in
# theme — dropped at #2057 (same silent-no-op class as card_hover="glow").
VALID_ICON_STYLES: "FrozenSet[str]" = frozenset({"outlined", "filled", "rounded", "sharp"})
VALID_ICON_WEIGHTS: "FrozenSet[str]" = frozenset({"thin", "regular", "bold"})


@dataclass
class IconStyle:
    """Icon styling configuration."""

    name: str
    style: str  # "outlined", "filled", "rounded", "sharp"
    weight: str  # "thin", "regular", "bold"
    size_scale: float = 1.0  # Multiplier for icon sizes

    # CSS properties
    stroke_width: str = "2"
    corner_rounding: str = "0"  # For rounded style

    def __post_init__(self) -> None:
        _check_vocab("IconStyle", self.name, "style", self.style, VALID_ICON_STYLES)
        _check_vocab("IconStyle", self.name, "weight", self.weight, VALID_ICON_WEIGHTS)


VALID_ANIMATION_ENTRANCE_EFFECTS: "FrozenSet[str]" = frozenset(
    {"fade", "slide", "scale", "bounce", "none"}
)
# exit_effect mirrors entrance_effect's vocabulary (symmetric animation pair);
# not yet dispatched by any CSS generator, but real built-in themes set both
# fields from the same conceptual palette.
VALID_ANIMATION_EXIT_EFFECTS: "FrozenSet[str]" = VALID_ANIMATION_ENTRANCE_EFFECTS
VALID_ANIMATION_HOVER_EFFECTS: "FrozenSet[str]" = frozenset({"lift", "scale", "glow", "none"})
VALID_ANIMATION_CLICK_EFFECTS: "FrozenSet[str]" = frozenset({"ripple", "pulse", "bounce", "none"})
# "bounce" (candy.py) was in real use but undocumented — added at #2057.
VALID_ANIMATION_LOADING_STYLES: "FrozenSet[str]" = frozenset(
    {"spinner", "skeleton", "progress", "pulse", "bounce"}
)
# "gentle" (notion.py) was in real use but undocumented — added at #2057.
VALID_ANIMATION_TRANSITION_STYLES: "FrozenSet[str]" = frozenset(
    {"smooth", "snappy", "bouncy", "instant", "gentle"}
)


@dataclass
class AnimationStyle:
    """Animation and motion configuration."""

    name: str

    # Entrance/Exit
    entrance_effect: str = "fade"  # "fade", "slide", "scale", "bounce", "none"
    exit_effect: str = "fade"  # "fade", "slide", "scale", "bounce", "none"

    # Hover behaviors
    hover_effect: str = "lift"  # "lift", "scale", "glow", "none"
    hover_scale: float = 1.02
    hover_translate_y: str = "-2px"

    # Click feedback
    click_effect: str = "ripple"  # "ripple", "pulse", "bounce", "none"

    # Loading states
    loading_style: str = "spinner"  # "spinner", "skeleton", "progress", "pulse", "bounce"

    # Transition characteristics
    transition_style: str = "smooth"  # "smooth", "snappy", "bouncy", "instant", "gentle"
    duration_fast: str = "0.15s"
    duration_normal: str = "0.3s"
    duration_slow: str = "0.5s"
    easing: str = "cubic-bezier(0.4, 0, 0.2, 1)"

    def __post_init__(self) -> None:
        _check_vocab(
            "AnimationStyle",
            self.name,
            "entrance_effect",
            self.entrance_effect,
            VALID_ANIMATION_ENTRANCE_EFFECTS,
        )
        _check_vocab(
            "AnimationStyle",
            self.name,
            "exit_effect",
            self.exit_effect,
            VALID_ANIMATION_EXIT_EFFECTS,
        )
        _check_vocab(
            "AnimationStyle",
            self.name,
            "hover_effect",
            self.hover_effect,
            VALID_ANIMATION_HOVER_EFFECTS,
        )
        _check_vocab(
            "AnimationStyle",
            self.name,
            "click_effect",
            self.click_effect,
            VALID_ANIMATION_CLICK_EFFECTS,
        )
        _check_vocab(
            "AnimationStyle",
            self.name,
            "loading_style",
            self.loading_style,
            VALID_ANIMATION_LOADING_STYLES,
        )
        _check_vocab(
            "AnimationStyle",
            self.name,
            "transition_style",
            self.transition_style,
            VALID_ANIMATION_TRANSITION_STYLES,
        )


# "color" was documented for link_hover only, but 9 built-in themes set
# button_hover="color" — undocumented AND unconsumed by pack_css_generator.py's
# button_hover dispatch (lift/scale/glow/darken only), a silent no-op on all 9.
# Fixed at #2057 by remapping those themes to "darken" (nearest consumed
# effect); see PR body for the theme list. NOT adding "color" here — it would
# perpetuate the same silent-no-op class this issue exists to catch.
VALID_INTERACTION_BUTTON_HOVERS: "FrozenSet[str]" = frozenset(
    {"lift", "scale", "glow", "darken", "none"}
)
VALID_INTERACTION_LINK_HOVERS: "FrozenSet[str]" = frozenset(
    {"underline", "color", "background", "none"}
)
VALID_INTERACTION_CARD_HOVERS: "FrozenSet[str]" = frozenset(
    {"lift", "scale", "border", "shadow", "none"}
)
VALID_INTERACTION_BUTTON_CLICKS: "FrozenSet[str]" = frozenset({"scale", "ripple", "pulse", "none"})
VALID_INTERACTION_FOCUS_STYLES: "FrozenSet[str]" = frozenset(
    {"ring", "outline", "glow", "underline"}
)
VALID_INTERACTION_CURSOR_STYLES: "FrozenSet[str]" = frozenset({"pointer", "default", "custom"})


@dataclass
class InteractionStyle:
    """User interaction feedback.

    This is the canonical InteractionStyle. theme_packs.py historically had
    two definitions — a smaller one at the top and a fuller one further down
    whose extra fields (``button_click``, ``focus_ring_offset``,
    ``cursor_style``) were relied on by the module-level INTERACT_* instances.
    The second definition shadowed the first at runtime, so we unify on the
    extended version here — it is a superset of both and all call-sites that
    used the narrower form still work because the extra fields have defaults.
    """

    name: str

    # Hover effects
    button_hover: str = "lift"  # "lift", "scale", "glow", "darken", "none"
    link_hover: str = "underline"  # "underline", "color", "background", "none"
    card_hover: str = "lift"  # "lift", "scale", "border", "shadow", "none"

    # Click effects
    button_click: str = "scale"  # "scale", "ripple", "pulse", "none"

    # Focus effects
    focus_style: str = "ring"  # "ring", "outline", "glow", "underline"
    focus_ring_width: str = "2px"
    focus_ring_offset: str = "2px"

    # Cursor
    cursor_style: str = "pointer"  # "pointer", "default", "custom"

    def __post_init__(self) -> None:
        _check_vocab(
            "InteractionStyle",
            self.name,
            "button_hover",
            self.button_hover,
            VALID_INTERACTION_BUTTON_HOVERS,
        )
        _check_vocab(
            "InteractionStyle",
            self.name,
            "link_hover",
            self.link_hover,
            VALID_INTERACTION_LINK_HOVERS,
        )
        _check_vocab(
            "InteractionStyle",
            self.name,
            "card_hover",
            self.card_hover,
            VALID_INTERACTION_CARD_HOVERS,
        )
        _check_vocab(
            "InteractionStyle",
            self.name,
            "button_click",
            self.button_click,
            VALID_INTERACTION_BUTTON_CLICKS,
        )
        _check_vocab(
            "InteractionStyle",
            self.name,
            "focus_style",
            self.focus_style,
            VALID_INTERACTION_FOCUS_STYLES,
        )
        _check_vocab(
            "InteractionStyle",
            self.name,
            "cursor_style",
            self.cursor_style,
            VALID_INTERACTION_CURSOR_STYLES,
        )


@dataclass
class DesignSystem:
    """
    Complete design system - all visual aspects EXCEPT colors.

    This allows any design system to be combined with any color preset.
    """

    name: str
    display_name: str
    description: str
    category: str  # "minimal", "bold", "elegant", "playful", "industrial"

    # Core styling components
    typography: TypographyStyle
    layout: LayoutStyle
    surface: SurfaceStyle
    icons: IconStyle
    animation: AnimationStyle
    interaction: InteractionStyle


# =============================================================================
# Legacy ThemePack types (previously in theme_packs.py, second definition block)
# =============================================================================


# "geometric" was documented but never consumed by pack_css_generator.py's
# background_pattern dispatch (`_generate_pattern_css`) and never used by a
# built-in theme — dropped at #2057 (same silent-no-op class as
# card_hover="glow").
VALID_PATTERN_BACKGROUND_PATTERNS: "FrozenSet[str]" = frozenset(
    {"dots", "grid", "noise", "gradient", "none"}
)
# "elevated" was documented but never consumed and never used — dropped at
# #2057. "neumorphic" IS dispatched (`_generate_pattern_css`) though no
# built-in theme currently uses it; kept as a real, implemented option.
VALID_PATTERN_SURFACE_STYLES: "FrozenSet[str]" = frozenset({"flat", "glass", "neumorphic"})


@dataclass
class PatternStyle:
    """Background patterns and textures."""

    name: str

    # Pattern types
    background_pattern: str = "none"  # "dots", "grid", "noise", "gradient", "none"
    pattern_opacity: float = 0.05
    pattern_scale: str = "1rem"

    # Surface treatment
    surface_style: str = "flat"  # "flat", "glass", "neumorphic"

    # Blur/frosting for glassmorphism
    backdrop_blur: str = "0px"

    # Noise for texture
    noise_intensity: float = 0.0

    def __post_init__(self) -> None:
        _check_vocab(
            "PatternStyle",
            self.name,
            "background_pattern",
            self.background_pattern,
            VALID_PATTERN_BACKGROUND_PATTERNS,
        )
        _check_vocab(
            "PatternStyle",
            self.name,
            "surface_style",
            self.surface_style,
            VALID_PATTERN_SURFACE_STYLES,
        )


VALID_ILLUSTRATION_IMAGE_FILTERS: "FrozenSet[str]" = frozenset(
    {"none", "grayscale", "sepia", "vibrant", "duotone"}
)


@dataclass
class IllustrationStyle:
    """Illustration and imagery treatment."""

    name: str

    # Illustration style — free-form CSS class-name suffix
    # (`.illustration-{illustration_type}`), not dispatched/validated; any
    # string is a syntactically valid (if unstyled) class name. NOT an
    # enumerated-choice field in the #2057 sense.
    illustration_type: str = (
        "flat"  # "flat", "isometric", "3d", "line-art", "hand-drawn", "abstract"
    )

    # Image treatment
    image_border_radius: str = "0.5rem"
    image_filter: str = "none"  # "none", "grayscale", "sepia", "vibrant", "duotone"

    # Aspect ratios preference — free-form CSS aspect-ratio value (e.g.
    # "16:9"), not an enum. NOT validated at construction.
    preferred_aspect: str = "16:9"

    def __post_init__(self) -> None:
        _check_vocab(
            "IllustrationStyle",
            self.name,
            "image_filter",
            self.image_filter,
            VALID_ILLUSTRATION_IMAGE_FILTERS,
        )


@dataclass
class ThemePack:
    """
    Complete design system combining all styling dimensions.

    A ThemePack provides a cohesive design experience by bundling:
    - Core design (typography, spacing, shadows)
    - Color palette
    - Icon styling
    - Animation behavior
    - Background patterns
    - Interaction feedback
    - Illustration style
    """

    name: str
    display_name: str
    description: str
    category: str  # "professional", "playful", "minimal", "bold", "elegant", "retro"

    # Core components
    design_theme: str  # Reference to Theme name (e.g., "material", "elegant")
    color_preset: str  # Reference to ColorPreset name (e.g., "blue", "purple")

    # Style dimensions
    icon_style: IconStyle
    animation_style: AnimationStyle
    pattern_style: PatternStyle
    interaction_style: InteractionStyle
    illustration_style: IllustrationStyle


__all__ = [
    "ColorScale",
    "ThemeTokens",
    "SurfaceTreatment",
    "ThemePreset",
    "TypographyStyle",
    "LayoutStyle",
    "SurfaceStyle",
    "IconStyle",
    "AnimationStyle",
    "InteractionStyle",
    "DesignSystem",
    "PatternStyle",
    "IllustrationStyle",
    "ThemePack",
    # Style-vocabulary frozensets (#2057) — single source of truth per field,
    # enforced in each dataclass's __post_init__.
    "VALID_SURFACE_TREATMENT_STYLES",
    "VALID_THEME_PRESET_DEFAULT_MODES",
    "VALID_LAYOUT_SHAPES",
    "VALID_SURFACE_BORDER_STYLES",
    "VALID_SURFACE_TREATMENTS",
    "VALID_ICON_STYLES",
    "VALID_ICON_WEIGHTS",
    "VALID_ANIMATION_ENTRANCE_EFFECTS",
    "VALID_ANIMATION_EXIT_EFFECTS",
    "VALID_ANIMATION_HOVER_EFFECTS",
    "VALID_ANIMATION_CLICK_EFFECTS",
    "VALID_ANIMATION_LOADING_STYLES",
    "VALID_ANIMATION_TRANSITION_STYLES",
    "VALID_INTERACTION_BUTTON_HOVERS",
    "VALID_INTERACTION_LINK_HOVERS",
    "VALID_INTERACTION_CARD_HOVERS",
    "VALID_INTERACTION_BUTTON_CLICKS",
    "VALID_INTERACTION_FOCUS_STYLES",
    "VALID_INTERACTION_CURSOR_STYLES",
    "VALID_PATTERN_BACKGROUND_PATTERNS",
    "VALID_PATTERN_SURFACE_STYLES",
    "VALID_ILLUSTRATION_IMAGE_FILTERS",
]
