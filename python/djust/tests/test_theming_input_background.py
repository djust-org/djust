"""Form fields are filled with ``--input-background``, never the ``--input`` border.

``--input`` is the field BORDER colour (shadcn convention: ``.border-input``;
``a11y_exemptions`` gates it as a 3:1 non-text UI colour). ``components.css`` and
the generated pack CSS used it as the field FILL, so a preset whose border is a
mid slate painted every input, select and textarea slate with dark text on it
(Point Guard's light mode: ``rgb(121, 140, 175)``). Fields now read
``--input-background``, which the theme generator emits for every preset in both
modes and which defaults to the page ``--background``.

The contrast arithmetic is deliberately independent of ``djust.theming._types``.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from djust.theming import _types
from djust.theming.css_generator import ThemeCSSGenerator
from djust.theming.presets import THEME_PRESETS, ColorScale

pytestmark = pytest.mark.theming

MODES = ("light", "dark")
ROOT = Path(_types.__file__).resolve().parents[1]

# A ``background``/``background-color`` declaration, up to the end of its value.
_BACKGROUND_DECL = re.compile(r"(?<![\w-])background(?:-color)?\s*:\s*([^;{}]*)")
# ``var(--input`` as the BORDER token itself: not ``--input-background``,
# ``--input-radius`` or any other ``--input-*`` token.
_INPUT_TOKEN = re.compile(r"var\(\s*--input(?![\w-])")


def _offending_backgrounds(css: str) -> list[str]:
    return [m.group(0) for m in _BACKGROUND_DECL.finditer(css) if _INPUT_TOKEN.search(m.group(1))]


def _static_css_files() -> list[Path]:
    files = sorted(p for p in ROOT.rglob("*.css") if "node_modules" not in p.parts)
    assert len(files) >= 5, files  # guards against the glob silently finding nothing
    return files


# --- the lint --------------------------------------------------------------------


def test_the_lint_catches_the_border_token_as_a_fill():
    """Non-vacuous: the matcher flags the shapes that shipped, and only those."""
    assert _offending_backgrounds(".a { background: hsl(var(--input)); }")
    assert _offending_backgrounds(".a { background-color: hsl(var(--input, var(--card))); }")
    assert _offending_backgrounds(".a{background:hsl(var(--input, var(--card, 0 0% 100%))) url(x)}")
    assert _offending_backgrounds(".a { background: hsl(var( --input ) / 0.5); }")
    assert not _offending_backgrounds(".a { background: hsl(var(--input-background)); }")
    assert not _offending_backgrounds(".a { border: 1px solid hsl(var(--input)); }")
    assert not _offending_backgrounds(".a { border-radius: var(--input-radius); }")
    assert not _offending_backgrounds(".a { --tw-bg: x; background-clip: padding-box; }")


@pytest.mark.parametrize("path", _static_css_files(), ids=lambda p: str(p.relative_to(ROOT)))
def test_static_css_never_fills_with_the_border_token(path: Path):
    assert _offending_backgrounds(path.read_text(encoding="utf-8")) == []


def test_generated_pack_css_never_fills_with_the_border_token():
    from djust.theming.pack_css_generator import generate_pack_css
    from djust.theming.theme_packs import THEME_PACKS

    assert THEME_PACKS
    for pack in list(THEME_PACKS):
        css = generate_pack_css(pack_name=pack)
        assert _offending_backgrounds(css) == [], pack


def test_framework_override_css_never_fills_with_the_border_token():
    """The Bootstrap/... overrides are emitted by ``_generate_framework_css``, which
    ``generate_pack_css`` does not include, so they are checked on their own."""
    from djust.theming.pack_css_generator import _FRAMEWORK_CSS

    assert _FRAMEWORK_CSS
    for framework, css in _FRAMEWORK_CSS.items():
        assert _offending_backgrounds(css) == [], framework
        for selector in (".form-control", ".form-select"):
            if f"\n{selector} {{" not in css:
                continue
            rule = css.split(f"\n{selector} {{", 1)[1].split("}", 1)[0]
            assert "var(--input-background" in rule, (framework, selector)
            assert _border_colours(rule) == ["hsl(var(--input, var(--border)))"], (
                framework,
                selector,
            )


@pytest.mark.parametrize("preset", sorted(THEME_PRESETS))
def test_generated_theme_css_never_fills_with_the_border_token(preset: str):
    assert _offending_backgrounds(ThemeCSSGenerator(preset).generate_css()) == []


FIELD_FILL_RULES = (
    ".form-input",
    ".form-select",
    ".form-textarea",
    ".form-checkbox",
    ".form-radio",
    ".table-search",
    ".data-table-search input",
    ".data-table-filter",
    ".data-table-cell-editing input",
    ".data-table-row-editing td input",
    ".multi-select-search",
    ".otp-digit",
    ".number-stepper-input",
    ".tag-input-field",
    ".dj-search-input__input",
    ".rich-select-trigger",
    ".dj-dependent-select__control select",
    ".dj-currency-input__control",
    ".dj-filter-bar__select",
    ".dj-mminput",
    ".dj-mentions__input",
    ".dj-cron-input__input",
)


@pytest.mark.parametrize("selector", FIELD_FILL_RULES)
def test_the_field_rules_read_the_field_token(selector: str):
    css = (ROOT / "components/static/djust_components/components.css").read_text(encoding="utf-8")
    rule = re.search(r"(?m)^" + re.escape(selector) + r"\s*\{([^}]*)\}", css)
    assert rule, selector
    assert "var(--input-background" in rule.group(1), selector


# The rules whose border IS the field outline. ``.data-table-cell-editing input`` is
# left out on purpose: its border is the ``--ring`` edit state. The number stepper's
# outline is ``.number-stepper-controls``; ``.number-stepper-input``'s inline borders
# divide the value from the buttons, so they stay ``--border``, a divider.
FIELD_OUTLINE_RULES = (
    ".form-input",
    ".form-select",
    ".form-textarea",
    ".form-checkbox",
    ".form-radio",
    ".table-search",
    ".data-table-search input",
    ".data-table-filter",
    ".data-table-row-editing td input",
    ".multi-select-search",
    ".otp-digit",
    ".number-stepper-controls",
    ".tag-input-field",
    ".dj-search-input__input",
    ".input-group",
    ".rich-select-trigger",
    ".rich-select-search-input",
    ".dj-dependent-select__control select",
    ".dj-currency-input__control",
    ".dj-filter-bar__select",
    ".dj-mminput",
    ".dj-mentions__input",
    ".dj-md-textarea",
    ".dj-form-array__input",
    ".dj-import-wizard__field-select",
    ".dj-signature-pad__typed-input",
    ".dj-prompt-editor__textarea",
    ".dj-cron-input__input",
)

# The colour part of every ``border``/``border-color``/``border-<side>`` declaration.
_BORDER_DECL = re.compile(
    r"(?<![\w-])border(?:-(?:top|right|bottom|left|inline|block))?(?:-color)?\s*:\s*([^;]*)"
)


# ``hsl(var(--a))``, ``hsl(var(--a, fallback))`` or ``hsl(var(--a, var(--b[, fallback])))``, also inside a
# per-component override such as ``var(--dj-mminput-border, hsl(...))``.
_HSL_TOKEN = re.compile(r"hsl\(var\(--[\w-]+(?:, (?:var\(--[\w-]+(?:, [^()]*)?\)|[^()]*))?\)\)")


def _border_colours(rule: str) -> list[str]:
    colours = []
    for value in _BORDER_DECL.findall(rule):
        if value.strip() == "none":
            continue
        match = _HSL_TOKEN.search(value)
        assert match, value
        colours.append(match.group(0))
    return colours


def test_the_border_matcher_reads_the_colour():
    """Non-vacuous: what the outline test compares is the declared colour."""
    assert _border_colours(" border: 1px solid hsl(var(--border)); border-radius: 4px;") == [
        "hsl(var(--border))"
    ]
    assert _border_colours(" border: none; border-inline: 1px solid hsl(var(--x));") == [
        "hsl(var(--x))"
    ]
    assert _border_colours(" border-color: hsl(var(--input, var(--border)));") == [
        "hsl(var(--input, var(--border)))"
    ]
    assert _border_colours(
        " border: 1px solid var(--dj-x-border, hsl(var(--border, 1 2% 3%)));"
    ) == ["hsl(var(--border, 1 2% 3%))"]


@pytest.mark.parametrize("selector", FIELD_OUTLINE_RULES)
def test_field_outlines_read_the_input_border_token(selector: str):
    """A field's outline is ``--input`` (the input-border token), with ``--border``
    only as the fallback for a theme that does not set it, never ``--border`` alone."""
    css = (ROOT / "components/static/djust_components/components.css").read_text(encoding="utf-8")
    rule = re.search(r"(?m)^" + re.escape(selector) + r"\s*\{([^}]*)\}", css)
    assert rule, selector
    colours = _border_colours(rule.group(1))
    assert colours, selector
    for colour in colours:
        assert re.fullmatch(r"hsl\(var\(--input, var\(--border(, [^)]*)?\)\)\)", colour), (
            selector,
            colour,
        )


# --- the token -------------------------------------------------------------------


@pytest.mark.parametrize("preset", sorted(THEME_PRESETS))
def test_every_preset_emits_the_token_in_both_modes(preset: str):
    css = ThemeCSSGenerator(preset).generate_css()
    for mode in MODES:
        tokens = getattr(THEME_PRESETS[preset], mode)
        assert f"--input-background: {tokens.input_background.to_hsl()};" in css, mode
    assert css.count("--input-background:") >= 2


def test_every_pack_emits_the_token():
    from djust.theming.pack_css_generator import generate_pack_css
    from djust.theming.theme_packs import THEME_PACKS

    for pack in list(THEME_PACKS):
        assert generate_pack_css(pack_name=pack).count("--input-background:") >= 2, pack


def test_the_default_is_the_page_background_not_the_border():
    for preset in THEME_PRESETS.values():
        for mode in MODES:
            tokens = getattr(preset, mode)
            assert tokens.input_background == tokens.background


# --- an independent WCAG implementation (no import of _types' helpers) -------------


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


def _ratio(a: ColorScale, b: ColorScale) -> float:
    hi, lo = sorted(
        (_lum(_hsl_to_rgb(a.h, a.s, a.lightness)), _lum(_hsl_to_rgb(b.h, b.s, b.lightness))),
        reverse=True,
    )
    return (hi + 0.05) / (lo + 0.05)


@pytest.mark.parametrize("mode", MODES)
@pytest.mark.parametrize("preset", sorted(THEME_PRESETS))
def test_field_text_meets_wcag_aa(preset: str, mode: str):
    """``--foreground`` typed into a field reads at 4.5:1 on ``--input-background``."""
    tokens = getattr(THEME_PRESETS[preset], mode)
    ratio = _ratio(tokens.foreground, tokens.input_background)
    assert ratio >= 4.5, f"{preset} {mode}: field text {ratio:.2f}:1"


# --- the outline against the fill (WCAG 1.4.11) ------------------------------------


@pytest.mark.parametrize("mode", MODES)
@pytest.mark.parametrize("preset", sorted(THEME_PRESETS))
def test_field_outline_meets_non_text_contrast(preset: str, mode: str):
    """``--input`` reaches 3:1 on ``--input-background`` (WCAG 1.4.11). A shipped
    preset that the owner exempted (``INPUT_BORDER_EXEMPTION_REASON`` in
    ``a11y_exemptions``) must still fail, so a fixed palette drops its row."""
    from djust.theming.a11y_exemptions import A11Y_EXEMPTIONS

    tokens = getattr(THEME_PRESETS[preset], mode)
    ratio = _ratio(tokens.input, tokens.input_background)
    exempt = (preset, mode, "input", "background") in A11Y_EXEMPTIONS
    if exempt:
        assert (
            tokens.input_background == tokens.background
        )  # the exemption is measured on --background
        assert ratio < 3.0, f"{preset} {mode}: --input now {ratio:.2f}:1, remove its exemption row"
    else:
        assert ratio >= 3.0, f"{preset} {mode}: field outline {ratio:.2f}:1"


# --- djust.theming's own field classes ------------------------------------------

# ``.input``, ``.select`` and ``.textarea`` from djust.theming's ``components.css``
# (the classes the theme gallery and scaffolds use). They filled with the page
# ``--background`` and outlined with a bare ``--input``.
THEME_FIELD_RULES = (".input", ".select", ".textarea")


def _theme_rule(selector: str) -> str:
    css = (ROOT / "theming/static/djust_theming/css/components.css").read_text(encoding="utf-8")
    rule = re.search(r"(?m)^" + re.escape(selector) + r"\s*\{([^}]*)\}", css)
    assert rule, selector
    return rule.group(1)


@pytest.mark.parametrize("selector", THEME_FIELD_RULES)
def test_theme_field_classes_fill_from_the_field_token(selector: str):
    fills = [m.group(1).strip() for m in _BACKGROUND_DECL.finditer(_theme_rule(selector))]
    assert fills == ["hsl(var(--input-background, var(--background)))"], (selector, fills)


@pytest.mark.parametrize("selector", THEME_FIELD_RULES)
def test_theme_field_classes_outline_with_the_input_border_token(selector: str):
    assert _border_colours(_theme_rule(selector)) == ["hsl(var(--input, var(--border)))"], selector


def test_tailwind_input_class_fills_from_the_field_token():
    from djust.theming import tailwind

    source = Path(tailwind.__file__).read_text(encoding="utf-8")
    rule = source.split("\n.input {", 1)[1].split("}", 1)[0]
    assert "bg-input-background" in rule
    assert "bg-background" not in rule
    assert "border-input" in rule
