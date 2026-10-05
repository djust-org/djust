"""Derived ``--primary-text`` / ``--brand-text`` / ``--info-text`` / ``--success-text`` /
``--warning-text`` (#2885), the way ``--destructive-text`` (#3320) works.

``primary``, ``brand``, ``info``, ``success`` and ``warning`` are FILLS (a
``*_foreground`` label sits on them), so many presets leave them unreadable as TEXT:
a bright orange link on white, a pale yellow status line, a deep blue on a dark page.
Everything that paints them as text reads the derived colour: the fill's hue and
saturation at the lightness that reaches 4.5:1 on the page, a card and the fill's own
10% / 15% washes, and the fill itself when it already reads. The fills never move.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
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
FILLS = ("primary", "brand", "info", "success", "warning")
ROOT = Path(_types.__file__).resolve().parents[1]
REPO = ROOT.parents[1]
STATIC_CSS = [
    ROOT / "components/static/djust_components/components.css",
    ROOT / "components/static/djust_components/components-classes.css",
    ROOT / "theming/static/djust_theming/css/components.css",
    ROOT / "theming/static/djust_theming/css/scaffold.css",
    ROOT / "theming/static/djust_theming/css/catalogue.css",
    ROOT / "theming/static/djust_theming/css/pages.css",
    ROOT / "auth/static/djust_auth/auth.css",
]
CASES = [(n, m, f) for n in sorted(THEME_PRESETS) for m in MODES for f in FILLS]
IDS = [f"{n}-{m}-{f}" for n, m, f in CASES]


# --- an independent WCAG implementation (no import of _types' helpers) --------


def _lin(v: float) -> float:
    v = v / 255
    return v / 12.92 if v <= 0.03928 else ((v + 0.055) / 1.055) ** 2.4


def _lum(rgb) -> float:
    return 0.2126 * _lin(rgb[0]) + 0.7152 * _lin(rgb[1]) + 0.0722 * _lin(rgb[2])


def _ratio(a, b) -> float:
    hi, lo = sorted((_lum(a), _lum(b)), reverse=True)
    return (hi + 0.05) / (lo + 0.05)


def _over(fg, base, alpha):
    return tuple(alpha * f + (1 - alpha) * b for f, b in zip(fg, base, strict=True))


def _worst(text: ColorScale, fill: ColorScale, tokens: ThemeTokens) -> float:
    bg, card = tokens.background.to_rgb(), tokens.card.to_rgb()
    surfaces = [bg, card]
    for alpha in (0.1, 0.15):
        for base in (bg, card):
            surfaces.append(_over(fill.to_rgb(), base, alpha))
    return min(_ratio(text.to_rgb(), s) for s in surfaces)


# --- the tokens ---------------------------------------------------------------


@pytest.mark.parametrize("name,mode,fill", CASES, ids=IDS)
def test_text_colour_reads_on_the_page_a_card_and_the_fills_own_washes(name, mode, fill):
    tokens = getattr(THEME_PRESETS[name], mode)
    text = getattr(tokens, f"{fill}_text")
    assert _worst(text, getattr(tokens, fill), tokens) >= 4.5


@pytest.mark.parametrize("name,mode,fill", CASES, ids=IDS)
def test_only_lightness_moves_and_a_fill_that_reads_is_kept_exactly(name, mode, fill):
    tokens = getattr(THEME_PRESETS[name], mode)
    fill_colour, text = getattr(tokens, fill), getattr(tokens, f"{fill}_text")
    assert (text.h, text.s) == (fill_colour.h, fill_colour.s)
    # The solver also measures the washes after an HSL round trip (what ``_tint``
    # does), which can cost a step, so require a small margin here.
    if _worst(fill_colour, fill_colour, tokens) >= 4.6:
        assert text == fill_colour, "a fill that already reads must not move"


def test_some_presets_really_are_left_alone_and_some_really_move():
    kept = moved = 0
    for preset in THEME_PRESETS.values():
        for mode in MODES:
            tokens = getattr(preset, mode)
            for fill in FILLS:
                same = getattr(tokens, f"{fill}_text") == getattr(tokens, fill)
                kept, moved = kept + same, moved + (not same)
    assert kept > 100 and moved > 100


def test_the_fills_and_their_labels_are_untouched_by_the_derived_tokens():
    """Reading a derived token must not change the token it derives from."""
    for preset in THEME_PRESETS.values():
        for mode in MODES:
            tokens = getattr(preset, mode)
            before = {f: getattr(tokens, f).to_hsl() for f in FILLS}
            labels = {f: getattr(tokens, f"{f}_foreground").to_hsl() for f in FILLS}
            for fill in FILLS:
                getattr(tokens, f"{fill}_text")
            assert before == {f: getattr(tokens, f).to_hsl() for f in FILLS}
            assert labels == {f: getattr(tokens, f"{f}_foreground").to_hsl() for f in FILLS}


def test_they_are_derived_values_not_fields():
    from dataclasses import fields

    names = {f.name for f in fields(ThemeTokens)}
    for fill in FILLS:
        assert f"{fill}_text" not in names


def test_every_fill_shares_the_one_solver():
    assert _types._solve_text_colour is _types._solve_destructive_text


# --- the matrix ---------------------------------------------------------------


def test_the_matrix_measures_the_text_colours_not_the_fills():
    pairs = {(fg, bg) for fg, bg, _m, _l in CONTRAST_PAIRS}
    for fill in FILLS:
        assert (fill, "background") not in pairs
        assert (fill, "card") not in pairs
        assert (f"{fill}_text", "background") in pairs
        assert (f"{fill}_text", "card") in pairs
    for fill in ("info", "success", "warning"):
        assert (fill, f"{fill}_tint") not in pairs
        assert (f"{fill}_text", f"{fill}_tint") in pairs


def test_every_derived_text_pair_is_solved_and_carries_no_exemption():
    derived = {k for k in NEW_PAIR_KEYS if k[0].endswith("_text")}
    assert derived and derived <= SOLVED_PAIR_KEYS
    assert not [k for k in A11Y_EXEMPTIONS if (k[2], k[3]) in SOLVED_PAIR_KEYS]
    # the 347 as-text rows the fills carried before are gone, not renamed
    assert not [k for k in A11Y_EXEMPTIONS if k[2] in FILLS]


@pytest.mark.parametrize("name", sorted(THEME_PRESETS))
def test_every_solved_pair_passes_in_the_real_matrix(name):
    validator = _types_validator()
    for mode in MODES:
        tokens = getattr(THEME_PRESETS[name], mode)
        for fg, bg, minimum, _label in CONTRAST_PAIRS:
            if (fg, bg) in SOLVED_PAIR_KEYS:
                ratio = validator.calculate_contrast_ratio(getattr(tokens, fg), getattr(tokens, bg))
                assert ratio >= minimum, (name, mode, fg, bg, ratio)


def _types_validator():
    from djust.theming.accessibility import AccessibilityValidator

    return AccessibilityValidator()


# --- generators emit them -----------------------------------------------------


def _vars_block(css: str, selector: str) -> str:
    match = re.search(re.escape(selector) + r"\s*\{(.*?)\}", css, re.S)
    assert match, selector
    return match.group(1)


@pytest.mark.parametrize(
    "name", ["default", "green", "blue", "slate", "magazine", "legal", "djust"]
)
def test_the_theme_css_declares_the_text_properties_and_keeps_the_fills(name):
    css = CSSGenerator(name).generate_css()
    preset = THEME_PRESETS[name]
    for mode in MODES:
        block = _vars_block(css, f'html[data-theme="{mode}"]')
        tokens = getattr(preset, mode)
        for fill in FILLS:
            prop = fill.replace("_", "-")
            assert f"--{prop}-text: {getattr(tokens, f'{fill}_text').to_hsl()};" in block
            # byte-identical fill
            assert f"--{prop}: {getattr(tokens, fill).to_hsl()};" in block
    root = _vars_block(css, ":root")
    for fill in FILLS:
        assert f"--{fill}-text: {getattr(preset.light, f'{fill}_text').to_hsl()};" in root


def test_every_preset_emits_its_fills_unchanged():
    for name, preset in THEME_PRESETS.items():
        css = CSSGenerator(name).generate_css()
        for mode in MODES:
            block = _vars_block(css, f'html[data-theme="{mode}"]')
            for fill in (*FILLS, "destructive", "link", "link_hover"):
                prop = fill.replace("_", "-")
                assert f"--{prop}: {getattr(getattr(preset, mode), fill).to_hsl()};" in block


def test_the_system_preference_block_and_the_light_outputs_declare_them_too():
    css = CSSGenerator("default").generate_css()
    media = css[css.index("prefers-color-scheme: dark") :]
    for fill in FILLS:
        assert (
            f"--{fill}-text: {getattr(THEME_PRESETS['default'].dark, f'{fill}_text').to_hsl()};"
            in media
        )
    generator = CSSGenerator("green")
    for output in (generator.generate_variables_only(), generator.generate_critical_css()):
        for fill in FILLS:
            assert f"--{fill}-text:" in output


def test_the_utility_classes_are_text_and_use_the_text_tokens():
    css = CSSGenerator("default").generate_css()
    for fill in ("primary", "success", "warning", "info"):
        assert f".text-{fill} {{ color: hsl(var(--{fill}-text)); }}" in css
        assert f".bg-{fill} {{ background-color: hsl(var(--{fill})); }}" in css
        assert f".border-{fill} {{ border-color: hsl(var(--{fill})); }}" in css


def test_pack_css_declares_and_uses_them():
    from djust.theming.pack_css_generator import generate_pack_css
    from djust.theming.theme_packs import THEME_PACKS

    for pack in THEME_PACKS:
        css = generate_pack_css(pack_name=pack)
        for fill in ("primary", "success", "warning", "info"):
            assert f"--{fill}-text:" in css, (pack, fill)
        for line in css.splitlines():
            if line.startswith((".text-primary {", ".text-success {", ".text-warning {")):
                assert "-text)" in line, line
            if line.startswith((".alert-info", ".alert-success", ".alert-warning")):
                assert "color: hsl(var(--" in line and "-text))" in line, line


def test_tailwind_outputs_carry_them():
    from djust.theming.tailwind import (
        export_preset_as_tailwind_colors,
        generate_tailwind_apply_examples,
        generate_tailwind_config,
        generate_tailwindv4_theme_block,
    )

    config = generate_tailwind_config()
    for fill in ("primary", "success", "warning"):
        assert f"text: 'hsl(var(--{fill}-text))'" in config
    examples = generate_tailwind_apply_examples()
    for fill in ("info", "success", "warning"):
        assert f"bg-{fill}/10 text-{fill}-text" in examples
    assert "light-primary-text" in export_preset_as_tailwind_colors("default")
    block = generate_tailwindv4_theme_block("default")
    for fill in ("primary", "success", "warning", "info"):
        assert f"--color-{fill}-text:" in block


def test_inspector_reports_them_for_both_modes():
    from djust.theming.inspector import ThemeInspector

    info = ThemeInspector().get_theme_info("minimalist", "default")
    for mode in MODES:
        for fill in ("primary", "success", "warning"):
            assert info["color_preset"][mode][f"{fill}_text"]["hsl"] == (
                getattr(getattr(THEME_PRESETS["default"], mode), f"{fill}_text").to_hsl()
            )


# --- components paint TEXT with them and FILLS with the fill ------------------

_FILL_VAR = re.compile(r"var\(--(primary|brand|info|success|warning)(?=[,)])")


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


def _bare_fill_outside_a_text_wrapper(value: str) -> bool:
    """True if ``value`` reads a fill token that is not the fallback of its ``-text`` token."""
    for match in _FILL_VAR.finditer(value):
        before = value[: match.start()]
        if not re.search(rf"var\(--{match.group(1)}-text,\s*$", before):
            return True
    return False


def test_no_text_colour_is_a_fill():
    """FILL (keep) vs TEXT (switch): a ``color:`` declaration that reads a bare fill
    is painting the fill as text. ``--primary-foreground`` is the label ON the fill and
    ``--primary-text`` is the token; neither matches. A fill that is only the fallback
    of its own ``-text`` token (a stylesheet used without the theme generator) is fine."""
    offenders = [
        (source, selectors, decls["color"])
        for source, selectors, decls in _text_rules()
        if "color" in decls and _bare_fill_outside_a_text_wrapper(decls["color"])
    ]
    assert not offenders, offenders[:8]
    converted = [1 for _, _, d in _text_rules() if re.search(r"--\w+-text", d.get("color", ""))]
    assert len(converted) > 150  # not vacuous: the scan saw the converted rules


def test_no_derived_text_token_is_used_as_a_fill():
    """The converse: the derived token is lighter or darker than the fill by design,
    so a background, border or dot painted with it would drift from the fill. Custom
    properties named ``--dj-*`` carry it to an icon's ``color``."""
    offenders = [
        (source, selectors, prop)
        for source, selectors, decls in _text_rules()
        for prop, value in decls.items()
        if re.search(r"--(primary|brand|info|success|warning)-text", value)
        and prop != "color"
        and not prop.startswith("--dj-")
    ]
    assert not offenders, offenders[:8]


TEXT_CLASSES = {
    "components/static/djust_components/components.css": [
        ".dj-callout--info .dj-callout__icon",
        ".dj-callout--warning .dj-callout__icon",
        ".dj-callout--success .dj-callout__icon",
    ],
    "theming/static/djust_theming/css/components.css": [
        ".alert-info",
        ".alert-success",
        ".alert-warning",
        ".toast-success",
        ".toast-warning",
    ],
    "theming/static/djust_theming/css/scaffold.css": [
        ".text-success",
        ".text-warning",
        ".badge-success",
        ".badge-warning",
    ],
    "auth/static/djust_auth/auth.css": [".dj-auth-links a"],
}


@pytest.mark.parametrize(
    "path,selector", [(p, s) for p, sels in TEXT_CLASSES.items() for s in sels]
)
def test_text_classes_read_a_text_token_with_the_fill_as_fallback(path, selector):
    for selectors, decls in _rules((ROOT / path).read_text()):
        if selector in selectors and "color" in decls:
            value = decls["color"]
            assert re.search(r"--(primary|info|success|warning)-text", value), (path, selector)
            assert "var(--" in value.split("-text", 1)[1], (
                "a stylesheet without the generator works"
            )
            return
    pytest.fail(f"{selector} has no colour rule in {path}")


def test_alert_and_toast_icons_take_the_text_tone_but_keep_the_fill_for_the_border():
    css = (ROOT / "components/static/djust_components/components.css").read_text()
    rules = list(_rules(css))

    def decls_for(selector):
        return next(d for s, d in rules if selector in s)

    for fill in ("info", "success", "warning"):
        alert = decls_for(f".dj-alert-{fill}")
        assert alert["--dj-alert-icon-tone"] == f"var(--{fill}-text, var(--{fill}))"
        assert alert["--dj-alert-tone"] == f"var(--{fill})"
        for cls in (f".dj-toast-{fill}", f".dj-toast--{fill}"):
            toast = decls_for(cls)
            assert toast["--dj-toast-icon-tone"] == f"var(--{fill}-text, var(--{fill}))"
            assert toast["--dj-toast-tone"] == f"var(--{fill})"
    assert "--dj-alert-icon-tone" in decls_for(".dj-alert-icon")["color"]
    assert "--dj-toast-icon-tone" in decls_for(".dj-toast__icon")["color"]


def test_fills_keep_the_fill_token():
    css = (ROOT / "theming/static/djust_theming/css/components.css").read_text()
    seen = 0
    for selectors, decls in _rules(css):
        for sel, fill in (
            (".btn-primary", "primary"),
            (".badge-primary", "primary"),
            (".status-badge-success", "success"),
            (".status-badge-warning", "warning"),
            (".status-badge-info", "info"),
        ):
            if sel in selectors and "background-color" in decls:
                assert decls["background-color"] == f"hsl(var(--{fill}))"
                assert decls["color"] == f"hsl(var(--{fill}-foreground))"
                seen += 1
    assert seen >= 3


# --- the gallery editor's live preview ----------------------------------------

NODE = shutil.which("node")
EDITOR = ROOT / "theming/templates/djust_theming/gallery/editor.html"
_HARNESS = """
const fs = require('fs');
const src = fs.readFileSync(process.argv[2], 'utf8');
const solve = new Function(src + '\\nreturn solveDestructiveText;')();
const cases = JSON.parse(fs.readFileSync(0, 'utf8'));
process.stdout.write(JSON.stringify(cases.map(c => {
  const r = solve({h: c.d[0], s: c.d[1], l: c.d[2]}, c.bg, c.card);
  return [r.h, r.s, r.l];
})));
"""


def test_editor_solver_matches_python_for_every_fill_preset_and_mode(tmp_path):
    if NODE is None:
        pytest.skip("node is not installed")
    html = EDITOR.read_text()
    match = re.search(
        r"// BEGIN destructive-text solver\n(.*?)\n\s*// END destructive-text solver", html, re.S
    )
    assert match
    (tmp_path / "solver.js").write_text(match.group(1))
    (tmp_path / "harness.js").write_text(_HARNESS)
    cases, expected = [], []
    for preset in THEME_PRESETS.values():
        for mode in MODES:
            tokens = getattr(preset, mode)
            for fill in ("primary", "info", "success", "warning"):
                colour = getattr(tokens, fill)
                cases.append(
                    {
                        "d": [colour.h, colour.s, colour.lightness],
                        "bg": list(tokens.background.to_rgb()),
                        "card": list(tokens.card.to_rgb()),
                    }
                )
                text = getattr(tokens, f"{fill}_text")
                expected.append([text.h, text.s, text.lightness])
    out = subprocess.run(
        [NODE, str(tmp_path / "harness.js"), str(tmp_path / "solver.js")],
        input=json.dumps(cases),
        capture_output=True,
        text=True,
        check=True,
        timeout=120,
    )
    assert json.loads(out.stdout) == expected


def test_editor_recomputes_every_derived_text_colour_and_exports_none():
    html = EDITOR.read_text()
    fills = re.search(r"var DERIVED_TEXT_FILLS = \[(.*?)\];", html, re.S)
    assert fills
    for fill in ("destructive", "primary", "info", "success", "warning"):
        assert f'"{fill}"' in fills.group(1)
    token_fields = re.search(r"var TOKEN_FIELDS = \[(.*?)\];", html, re.S)
    assert token_fields and "_text" not in token_fields.group(1)
