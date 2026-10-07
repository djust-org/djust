"""Error text uses a derived ``--destructive-text``, not the ``--destructive`` fill (#3320).

``destructive`` is a FILL colour: in 51 of 68 presets' dark mode it is a deep red
(``0 62% 30%``) under a white label, which as text on the dark page is 1.7:1 to
1.9:1, and in 42 light modes (41 on exact float math: magazine is 4.4976 rounded,
4.5009 exact) the bright red is under 4.5:1 on the page or a card. Recolouring the
brand palettes is #2885 and stays parked; instead ``ThemeTokens.destructive_text``
is the same hue and saturation moved in lightness until it reads, and every place
that paints destructive as TEXT uses it.

The contrast arithmetic below is deliberately NOT ``djust.theming._types``'s: the
token is solved there, so asserting it with the same helper would only prove the
solver agrees with itself.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from djust.theming import _types
from djust.theming.a11y_exemptions import (
    A11Y_EXEMPTIONS,
    CONTRAST_PAIRS,
    NEW_PAIR_KEYS,
    SOLVED_PAIR_KEYS,
)
from djust.theming.css_generator import ThemeCSSGenerator as CSSGenerator
from djust.theming.presets import THEME_PRESETS, ColorScale, ThemeTokens

pytestmark = pytest.mark.theming

MODES = ("light", "dark")
ROOT = Path(_types.__file__).resolve().parents[1]
STATIC_CSS = [
    ROOT / "components/static/djust_components/components.css",
    ROOT / "components/static/djust_components/components-classes.css",
    ROOT / "theming/static/djust_theming/css/components.css",
    ROOT / "theming/static/djust_theming/css/scaffold.css",
    ROOT / "auth/static/djust_auth/auth.css",
]


# --- an independent WCAG implementation (no import of _types' helpers) --------


def _hsl_to_rgb(h: float, s: float, light: float) -> tuple[int, int, int]:
    s, light = s / 100, light / 100
    c = (1 - abs(2 * light - 1)) * s
    x = c * (1 - abs((h / 60) % 2 - 1))
    m = light - c / 2
    sector = int(h // 60) % 6
    r, g, b = [(c, x, 0), (x, c, 0), (0, c, x), (0, x, c), (x, 0, c), (c, 0, x)][sector]
    return tuple(round((v + m) * 255) for v in (r, g, b))  # type: ignore[return-value]


def _lum(rgb: tuple[int, int, int]) -> float:
    def lin(v: int) -> float:
        v = v / 255
        return v / 12.92 if v <= 0.04045 else ((v + 0.055) / 1.055) ** 2.4

    r, g, b = (lin(v) for v in rgb)
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def _ratio(a: tuple[int, int, int], b: tuple[int, int, int]) -> float:
    hi, lo = sorted((_lum(a), _lum(b)), reverse=True)
    return (hi + 0.05) / (lo + 0.05)


def _rgb(c: ColorScale) -> tuple[int, int, int]:
    return _hsl_to_rgb(c.h, c.s, c.lightness)


def _over(fg: tuple[int, int, int], base: tuple[int, int, int], a: float):
    """``fg`` at alpha ``a`` over ``base``: what the browser paints for ``hsl(var(--x) / a)``."""
    return tuple(a * f + (1 - a) * c for f, c in zip(fg, base, strict=True))


def _worst_ratio(text: ColorScale, tokens: ThemeTokens, alphas=(0.1, 0.15)) -> float:
    t = _rgb(text)
    fill = _rgb(tokens.destructive)
    surfaces = [_rgb(tokens.background), _rgb(tokens.card)]
    for alpha in alphas:
        for base in (_rgb(tokens.background), _rgb(tokens.card)):
            surfaces.append(_over(fill, base, alpha))  # type: ignore[arg-type]
    return min(_ratio(t, s) for s in surfaces)  # type: ignore[arg-type]


CASES = [(n, m) for n in sorted(THEME_PRESETS) for m in MODES]


# --- the token ----------------------------------------------------------------


@pytest.mark.parametrize("name,mode", CASES, ids=[f"{n}-{m}" for n, m in CASES])
def test_error_text_reads_on_the_page_a_card_and_the_destructive_washes(name, mode):
    tokens = getattr(THEME_PRESETS[name], mode)
    text = tokens.destructive_text
    assert _ratio(_rgb(text), _rgb(tokens.background)) >= 4.5, "background"
    assert _ratio(_rgb(text), _rgb(tokens.card)) >= 4.5, "card"
    # 10% is the alert/toast/badge wash, 15% the strongest under resting text
    assert _worst_ratio(text, tokens) >= 4.5, "destructive wash"


@pytest.mark.parametrize("name,mode", CASES, ids=[f"{n}-{m}" for n, m in CASES])
def test_only_lightness_moves(name, mode):
    tokens = getattr(THEME_PRESETS[name], mode)
    assert (tokens.destructive_text.h, tokens.destructive_text.s) == (
        tokens.destructive.h,
        tokens.destructive.s,
    )


@pytest.mark.parametrize("name,mode", CASES, ids=[f"{n}-{m}" for n, m in CASES])
def test_a_destructive_that_already_reads_is_kept_exactly(name, mode):
    tokens = getattr(THEME_PRESETS[name], mode)
    if _worst_ratio(tokens.destructive, tokens) >= 4.5:
        assert tokens.destructive_text == tokens.destructive


def test_some_presets_really_are_left_alone():
    """The 'unchanged' branch is exercised by shipped presets, not only by a
    synthetic one: otherwise the test above could pass vacuously."""
    kept = [
        (n, m)
        for n, m in CASES
        if getattr(THEME_PRESETS[n], m).destructive_text == getattr(THEME_PRESETS[n], m).destructive
    ]
    assert kept, "no preset keeps its destructive; the 'already passes' rule never runs"


def test_the_fill_and_its_label_are_untouched():
    """The whole point of a separate token: the dark ``default`` fill stays the deep
    red its white label is built for."""
    dark = THEME_PRESETS["default"].dark
    assert dark.destructive == ColorScale(0, 62, 30)
    assert dark.destructive_foreground.lightness > 90
    assert dark.destructive_text == ColorScale(0, 62, dark.destructive_text.lightness)
    assert dark.destructive_text.lightness > 50  # lightened, not the fill


@pytest.mark.parametrize(
    "name,mode,before",
    [
        ("green", "dark", 1.71),
        ("blue", "dark", 1.75),
        ("default", "dark", 1.94),
        ("shadcn", "dark", 1.94),
        ("slate", "light", 3.62),
        ("magazine", "light", 4.50),
    ],
)
def test_the_cases_named_in_the_issue(name, mode, before):
    tokens = getattr(THEME_PRESETS[name], mode)

    def worst_bg_card(c):
        return min(_ratio(_rgb(c), _rgb(tokens.background)), _ratio(_rgb(c), _rgb(tokens.card)))

    assert worst_bg_card(tokens.destructive) == pytest.approx(before, abs=0.02)
    assert worst_bg_card(tokens.destructive_text) >= 4.5


def test_it_is_a_derived_value_not_a_field():
    from dataclasses import fields

    assert "destructive_text" not in {f.name for f in fields(ThemeTokens)}


def _tokens(background: ColorScale, card: ColorScale, destructive: ColorScale) -> ThemeTokens:
    base = THEME_PRESETS["default"].light
    from dataclasses import replace

    return replace(base, background=background, card=card, destructive=destructive)


def test_a_clearing_colour_is_kept_and_a_failing_one_moves_toward_contrast():
    dark_page = ColorScale(240, 10, 4)
    keep = _tokens(dark_page, dark_page, ColorScale(0, 80, 75))
    assert keep.destructive_text == keep.destructive
    lighten = _tokens(dark_page, dark_page, ColorScale(0, 62, 30))
    assert lighten.destructive_text.lightness > 30
    light_page = ColorScale(0, 0, 100)
    darken = _tokens(light_page, light_page, ColorScale(0, 84, 60))
    assert darken.destructive_text.lightness < 60


def test_the_nearest_passing_lightness_wins_not_the_extreme():
    """Clamped to what is needed: a pastel-pink or near-black error text would not
    read as an error."""
    tokens = _tokens(ColorScale(0, 0, 100), ColorScale(0, 0, 100), ColorScale(0, 84, 60))
    got = tokens.destructive_text
    assert 30 <= got.lightness <= 50
    one_step_closer = ColorScale(got.h, got.s, got.lightness + 1)  # lighter = less contrast
    assert _worst_ratio(one_step_closer, tokens) < 4.5


def test_an_unreachable_surface_pair_returns_the_best_effort_and_is_flagged_by_w001(settings):
    """Black page and white card: no lightness reads on both with the washes. The
    token must not crash or leave its lightness range, and W001 must not stay silent."""
    from dataclasses import replace
    from unittest.mock import patch

    from djust.theming.checks import check_preset_contrast

    impossible = _tokens(ColorScale(0, 0, 0), ColorScale(0, 0, 100), ColorScale(0, 84, 60))
    got = impossible.destructive_text
    assert 0 <= got.lightness <= 100
    assert _worst_ratio(got, impossible) < 4.5
    preset = replace(
        THEME_PRESETS["default"], name="user_midtone", light=impossible, dark=impossible
    )
    settings.DJUST_THEMING = {"contrast_check_scope": "all"}
    with patch("djust.theming.checks.get_registry") as registry:
        registry.return_value.list_presets.return_value = {"user_midtone": preset}
        warnings = check_preset_contrast(app_configs=None)
    assert any("error text" in w.msg for w in warnings)


def test_an_ordinary_user_preset_cannot_trip_the_solved_pairs(settings):
    """The pairs the token is solved against never warn for a normal palette, even a
    badly chosen destructive: the derivation absorbs it."""
    from dataclasses import replace
    from unittest.mock import patch

    from djust.theming.checks import check_preset_contrast

    base = THEME_PRESETS["default"]
    light = replace(base.light, destructive=ColorScale(0, 100, 50))  # 4.0:1 on white
    dark = replace(base.dark, destructive=ColorScale(0, 100, 20))  # 1.7:1 on near-black
    preset = replace(base, name="user_loud_red", light=light, dark=dark)
    settings.DJUST_THEMING = {"contrast_check_scope": "all"}
    with patch("djust.theming.checks.get_registry") as registry:
        registry.return_value.list_presets.return_value = {"user_loud_red": preset}
        warnings = check_preset_contrast(app_configs=None)
    assert not [w for w in warnings if "error text" in w.msg or "destructive text" in w.msg]


# --- the matrix and the exemptions --------------------------------------------


def test_the_matrix_measures_error_text_not_the_fill():
    pairs = {(fg, bg): minimum for fg, bg, minimum, _ in CONTRAST_PAIRS}
    for bg in ("background", "card", "destructive_tint"):
        assert pairs[("destructive_text", bg)] == 4.5
    assert ("destructive", "destructive_tint") not in pairs
    assert set(SOLVED_PAIR_KEYS) <= NEW_PAIR_KEYS


def test_no_preset_needs_an_exemption_for_a_solved_pair():
    assert not [k for k in A11Y_EXEMPTIONS if (k[2], k[3]) in SOLVED_PAIR_KEYS]
    assert not [k for k in A11Y_EXEMPTIONS if k[2] == "destructive" and k[3] == "destructive_tint"]


@pytest.mark.parametrize("name,mode", CASES, ids=[f"{n}-{m}" for n, m in CASES])
def test_every_solved_pair_passes_in_the_real_matrix(name, mode):
    from djust.theming.accessibility import AccessibilityValidator

    validator = AccessibilityValidator()
    tokens = getattr(THEME_PRESETS[name], mode)
    for fg, bg, minimum, label in CONTRAST_PAIRS:
        if (fg, bg) in SOLVED_PAIR_KEYS:
            ratio = validator.calculate_contrast_ratio(getattr(tokens, fg), getattr(tokens, bg))
            assert ratio >= minimum, f"{name}/{mode}: {label} = {ratio:.2f}"


# --- generators emit it -------------------------------------------------------


def _vars_block(css: str, selector: str) -> str:
    match = re.search(re.escape(selector) + r"\s*\{(.*?)\}", css, re.S)
    assert match, selector
    return match.group(1)


@pytest.mark.parametrize("name", ["default", "green", "blue", "slate", "magazine", "legal"])
def test_the_theme_css_declares_the_property_per_mode(name):
    css = CSSGenerator(name).generate_css()
    preset = THEME_PRESETS[name]
    for mode in MODES:
        block = _vars_block(css, f'html[data-theme="{mode}"]')
        want = getattr(preset, mode).destructive_text.to_hsl()
        assert f"--destructive-text: {want};" in block, (name, mode)
    root = _vars_block(css, ":root")
    assert f"--destructive-text: {preset.light.destructive_text.to_hsl()};" in root


def test_the_system_preference_block_declares_it_too():
    css = CSSGenerator("default").generate_css()
    media = css[css.index("prefers-color-scheme: dark") :]
    want = THEME_PRESETS["default"].dark.destructive_text.to_hsl()
    assert f"--destructive-text: {want};" in media


def test_critical_and_variables_only_outputs_carry_it():
    generator = CSSGenerator("green")
    assert "--destructive-text:" in generator.generate_variables_only()
    assert "--destructive-text:" in generator.generate_critical_css()


def test_the_utility_class_is_text_and_uses_the_text_token():
    css = CSSGenerator("default").generate_css()
    assert ".text-destructive { color: hsl(var(--destructive-text)); }" in css
    assert ".bg-destructive { background-color: hsl(var(--destructive)); }" in css
    assert ".btn-destructive {\n  background-color: hsl(var(--destructive));" in css


def test_pack_css_declares_and_uses_it():
    from djust.theming.pack_css_generator import generate_pack_css
    from djust.theming.theme_packs import THEME_PACKS

    for pack in THEME_PACKS:
        css = generate_pack_css(pack_name=pack)
        assert "--destructive-text:" in css, pack
        for line in css.splitlines():
            if line.startswith((".alert-danger", ".text-danger")):
                assert "var(--destructive-text)" in line, line


def test_tailwind_outputs_carry_it():
    from djust.theming.tailwind import (
        generate_tailwind_apply_examples,
        generate_tailwind_config,
        generate_tailwindv4_theme_block,
    )

    assert "text: 'hsl(var(--destructive-text))'" in generate_tailwind_config()
    alert = generate_tailwind_apply_examples().split(".alert-destructive")[1]
    assert "text-destructive-text" in alert
    assert "--color-destructive-text: hsl(" in generate_tailwindv4_theme_block("default")


def test_inspector_reports_it_for_both_modes():
    from djust.theming.inspector import ThemeInspector

    info = ThemeInspector().get_theme_info("minimalist", "default")
    for mode in MODES:
        assert info["color_preset"][mode]["destructive_text"]["hsl"] == (
            THEME_PRESETS["default"].__getattribute__(mode).destructive_text.to_hsl()
        )


# --- components paint TEXT with it and FILLS with the fill --------------------

_DESTRUCTIVE_VAR = re.compile(r"var\(--destructive(?=[,)])")


def _rules(css: str):
    css = re.sub(r"/\*.*?\*/", "", css, flags=re.S)
    for match in re.finditer(r"([^{}]+)\{([^{}]*)\}", css):
        selectors = [s.strip() for s in match.group(1).split(",") if s.strip()]
        decls = {}
        for part in match.group(2).split(";"):
            if ":" in part:
                prop, value = part.split(":", 1)
                decls[prop.strip()] = value.strip()
        yield selectors, decls


def _text_rules():
    sources = {p.name + ":" + p.parent.name: p.read_text() for p in STATIC_CSS}
    sources["css_generator"] = CSSGenerator("default").generate_css()
    from djust.theming import pack_css_generator

    sources["packs"] = "\n".join(
        css
        for value in vars(pack_css_generator).values()
        if isinstance(value, dict)
        for css in value.values()
        if isinstance(css, str)
    )
    for source, css in sources.items():
        for selectors, decls in _rules(css):
            yield source, selectors, decls


def test_no_text_colour_is_the_destructive_fill():
    """FILL (keep) vs TEXT (switch): a ``color:`` declaration that reads the bare
    ``--destructive`` is painting the fill as text. ``--destructive-foreground`` is
    the label ON the fill and ``--destructive-text`` is the token; neither matches."""
    offenders = [
        (source, selectors, decls["color"])
        for source, selectors, decls in _text_rules()
        if "color" in decls
        and "--destructive-text" not in decls["color"]
        and _DESTRUCTIVE_VAR.search(decls["color"])
        # ``.dj-status-dot`` paints ``background: currentColor``: its ``color`` is the
        # FILL (a dot, not text), so the variant keeps the fill token (#2885).
        and selectors != [".dj-status-dot-danger"]
    ]
    assert not offenders, offenders[:5]
    # not vacuous: the scan really did see the converted rules
    converted = [1 for _, _, d in _text_rules() if "--destructive-text" in d.get("color", "")]
    assert len(converted) > 40


def test_no_destructive_text_token_is_used_as_a_fill():
    """The converse: the derived token is lighter or darker than the fill by design,
    so a background or border painted with it would drift from the fill."""
    offenders = [
        (source, selectors, prop)
        for source, selectors, decls in _text_rules()
        for prop, value in decls.items()
        if "--destructive-text" in value and prop not in ("color",) and not prop.startswith("--dj-")
    ]
    assert not offenders, offenders[:5]


# Class -> the property that carries its text colour. Pinned so a refactor that
# puts a class back on the fill fails here, naming the class.
TEXT_CLASSES = {
    "components/static/djust_components/components.css": [
        ".form-error-message",
        ".dj-field-error__message",
        ".dj-form-errors__item",
        ".badge-error",
        ".tag-danger",
        ".dj-tag-destructive",
        ".stat-trend-down",
        ".dj-stat-card-trend-down",
        ".ctx-item-danger",
        ".dj-dropdown-menu__item--danger",
        ".dj-error-boundary__message",
        ".dj-content-loader__error",
        ".dj-server-toast--error",
        ".dj-announcement-bar--danger",
        ".rich-select-trigger--variant-danger",
        ".alert-error .alert-icon",
        ".dj-callout--danger .dj-callout__icon",
        ".form-label-required::after",
        ".data-table-import-errors",
    ],
    "theming/static/djust_theming/css/components.css": [
        ".theme-field-error",
        ".field-error",
        ".textarea-error",
        ".alert-destructive",
        ".toast-error",
        ".required",
    ],
    "theming/static/djust_theming/css/scaffold.css": [
        ".text-danger",
        ".badge-danger",
        ".stat-card.stat-danger .stat-value",
        ".message-error",
    ],
    "auth/static/djust_auth/auth.css": [".dj-auth-error", ".dj-auth-alert"],
}


@pytest.mark.parametrize(
    "path,selector",
    [(p, s) for p, sels in TEXT_CLASSES.items() for s in sels],
)
def test_text_classes_read_the_text_token(path, selector):
    for selectors, decls in _rules((ROOT / path).read_text()):
        if selector in selectors and "color" in decls:
            assert "--destructive-text" in decls["color"], (path, selector, decls["color"])
            # a stylesheet used without the theme generator still works
            assert "var(--destructive" in decls["color"].split("--destructive-text", 1)[1]
            return
    pytest.fail(f"{selector} has no colour rule in {path}")


def test_alert_and_toast_icons_take_the_text_tone_but_keep_the_fill_for_the_border():
    css = (ROOT / "components/static/djust_components/components.css").read_text()
    rules = list(_rules(css))

    def decls_for(selector):
        return next(d for s, d in rules if selector in s)

    assert "--destructive-text" in decls_for(".dj-alert-danger")["--dj-alert-icon-tone"]
    assert decls_for(".dj-alert-danger")["--dj-alert-tone"] == "var(--destructive)"
    assert "--dj-alert-icon-tone" in decls_for(".dj-alert-icon")["color"]
    assert "--destructive-text" in decls_for(".dj-toast-error")["--dj-toast-icon-tone"]
    assert decls_for(".dj-toast-error")["--dj-toast-tone"] == "var(--destructive)"
    assert "--dj-toast-icon-tone" in decls_for(".dj-toast__icon")["color"]


def test_fills_keep_the_fill_token():
    css = (ROOT / "theming/static/djust_theming/css/components.css").read_text()
    for selectors, decls in _rules(css):
        if any(
            s in (".btn-destructive", ".badge-destructive", ".status-badge-danger")
            for s in selectors
        ):
            assert decls["background-color"] == "hsl(var(--destructive))"
            assert decls["color"] == "hsl(var(--destructive-foreground))"


def test_scaffold_templates_use_it_for_inline_error_text():
    from djust.scaffolding import templates

    source = Path(templates.__file__).read_text()
    assert "color: hsl(var(--destructive))" not in source
    assert source.count("color: hsl(var(--destructive-text, var(--destructive)))") == 2


# --- end to end: an error message under a dark preset -------------------------


def _resolve(expr: str, props: dict[str, str]) -> str:
    """Resolve ``var(--x, fallback)`` the way the cascade does."""
    inner = re.compile(r"var\((--[\w-]+)(?:,\s*([^()]*))?\)")
    for _ in range(10):
        new = inner.sub(lambda m: props.get(m.group(1), m.group(2) or ""), expr)
        if new == expr:
            break
        expr = new
    return expr.strip()


def _props(css: str, mode: str) -> dict[str, str]:
    block = _vars_block(css, f'html[data-theme="{mode}"]')
    return {m.group(1): m.group(2).strip() for m in re.finditer(r"(--[\w-]+):\s*([^;]+);", block)}


def _hsl_value(resolved: str) -> tuple[float, float, float]:
    match = re.fullmatch(r"hsl\(\s*([\d.]+)\s+([\d.]+)%\s+([\d.]+)%\s*\)", resolved)
    assert match, resolved
    h, s, light = (float(v) for v in match.groups())
    return h, s, light


@pytest.mark.parametrize("preset", ["green", "blue", "default", "shadcn"])
def test_a_rendered_field_error_is_legible_on_a_dark_page(preset):
    """Render a real bound-form error, find the class it carries, and resolve that
    class's colour through the generated dark variables. These four presets are the
    worst measured in the issue (1.7:1 to 1.9:1 before)."""
    from django import forms
    from django.template import Context, Template

    class F(forms.Form):
        email = forms.EmailField()

    form = F({"email": "nope"})
    html = Template("{% load theme_form_tags %}{% theme_form form %}").render(
        Context({"form": form})
    )
    assert "theme-field-error" in html and "Enter a valid email address" in html

    css = CSSGenerator(preset).generate_css()
    props = _props(css, "dark")
    components = (ROOT / "theming/static/djust_theming/css/components.css").read_text()
    colour = next(
        decls["color"]
        for selectors, decls in _rules(components)
        if ".theme-field-error" in selectors and "color" in decls
    )
    text = _hsl_value(_resolve(colour, props))
    page = _hsl_value(f"hsl({props['--background']})")
    card = _hsl_value(f"hsl({props['--card']})")
    for surface in (page, card):
        assert _ratio(_hsl_to_rgb(*text), _hsl_to_rgb(*surface)) >= 4.5
    # and it IS the derived token, not the 30% fill
    assert text == pytest.approx(
        (
            THEME_PRESETS[preset].dark.destructive_text.h,
            THEME_PRESETS[preset].dark.destructive_text.s,
            THEME_PRESETS[preset].dark.destructive_text.lightness,
        )
    )
    assert text[2] > THEME_PRESETS[preset].dark.destructive.lightness
