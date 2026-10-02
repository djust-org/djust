"""Follow-ups to the derived ``--destructive-text`` token (#3344, from the #3343 review).

1. The gallery editor's live preview recomputes ``--destructive-text`` itself (a JS
   port of ``_solve_destructive_text``), so it no longer keeps the first preset's value.
2. A solver case with a ``card`` far from the page, so dropping the plain card from
   the solved surfaces fails a test (the card washes alone cover it for every shipped
   preset).
3. The W001 hint names the tokens a preset author can change; the audit keeps its own
   label for the label-on-fill pair.
4. Tailwind: ``text-destructive`` is the fill by design, ``text-destructive-text`` is the text.
5. The demo project paints error text with the token.
"""

from __future__ import annotations

import json
import random
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from djust.theming import _types
from djust.theming.checks import _contrast_hint
from djust.theming.presets import THEME_PRESETS, ColorScale

pytestmark = pytest.mark.theming

REPO = Path(_types.__file__).resolve().parents[3]
EDITOR = REPO / "python/djust/theming/templates/djust_theming/gallery/editor.html"
DEMO_BASE = REPO / "examples/demo_project/djust_rentals/templates/rentals/base.html"


# --- an independent contrast helper (not _types') -----------------------------


def _lin(v: float) -> float:
    v = v / 255
    return v / 12.92 if v <= 0.03928 else ((v + 0.055) / 1.055) ** 2.4


def _contrast(a: tuple[int, int, int], b: tuple[int, int, int]) -> float:
    la, lb = (0.2126 * _lin(c[0]) + 0.7152 * _lin(c[1]) + 0.0722 * _lin(c[2]) for c in (a, b))
    hi, lo = max(la, lb), min(la, lb)
    return (hi + 0.05) / (lo + 0.05)


# --- 2. the plain card surface is solved against ------------------------------

#: (destructive, page, card): a card far from the page, so that the plain card, not
#: its 10%/15% washes, is the tightest surface. With the card dropped from
#: ``surfaces`` the solver lands a few lightness steps too close to the card.
FAR_CARD_CASES = [
    ((0, 100, 95), (0, 0, 100), (0, 0, 60)),
    ((0, 100, 95), (0, 0, 100), (0, 0, 50)),
    ((0, 100, 95), (0, 0, 98), (0, 0, 70)),
    ((0, 62, 30), (0, 0, 5), (0, 0, 40)),
]


@pytest.mark.parametrize("dest,page,card", FAR_CARD_CASES)
def test_solver_reads_on_a_card_far_from_the_page(dest, page, card):
    bg = ColorScale(*page).to_rgb()
    cd = ColorScale(*card).to_rgb()
    h, s, light = _types._solve_destructive_text(dest, bg, cd)
    text = ColorScale(h, s, light).to_rgb()
    assert (h, s) == (dest[0], dest[1]), "only the lightness moves"
    assert _contrast(text, cd) >= 4.5, f"{dest} on card {card}: {_contrast(text, cd):.3f}"
    assert _contrast(text, bg) >= 4.5


@pytest.mark.parametrize("dest,page,card", FAR_CARD_CASES)
def test_dropping_the_card_surface_changes_the_answer(dest, page, card):
    """The mutation the review found surviving: ``surfaces = [background, card]``
    without ``card``. Re-run the solver's source without it and require a different
    answer on every far-card case, so these cases really pin the plain card."""
    import inspect

    src = inspect.getsource(_types._solve_destructive_text.__wrapped__)
    assert "surfaces: list = [background, card]" in src
    mutant_src = src.replace("surfaces: list = [background, card]", "surfaces: list = []").replace(
        "def _solve_destructive_text", "def mutant"
    )
    ns = dict(vars(_types))
    exec(mutant_src, ns)  # noqa: S102 - our own source, in-test mutation
    bg, cd = ColorScale(*page).to_rgb(), ColorScale(*card).to_rgb()
    assert ns["mutant"](dest, bg, cd) != _types._solve_destructive_text(dest, bg, cd)


# --- 1. the editor's JS solver agrees with the Python one ---------------------

NODE = shutil.which("node")


def _editor_solver_source() -> str:
    html = EDITOR.read_text()
    match = re.search(
        r"// BEGIN destructive-text solver\n(.*?)\n\s*// END destructive-text solver", html, re.S
    )
    assert match, "editor.html lost its destructive-text solver block"
    return match.group(1)


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


def _run_js(cases: list[dict], tmp_path: Path) -> list[list[int]]:
    if NODE is None:
        pytest.skip("node is not installed")
    (tmp_path / "solver.js").write_text(_editor_solver_source())
    (tmp_path / "harness.js").write_text(_HARNESS)
    out = subprocess.run(
        [NODE, str(tmp_path / "harness.js"), str(tmp_path / "solver.js")],
        input=json.dumps(cases),
        capture_output=True,
        text=True,
        check=True,
        timeout=120,
    )
    return json.loads(out.stdout)


def _case(dest, bg_rgb, card_rgb) -> dict:
    return {"d": list(dest), "bg": list(bg_rgb), "card": list(card_rgb)}


def test_editor_solver_matches_python_on_every_preset_and_mode(tmp_path):
    cases, expected = [], []
    for preset in THEME_PRESETS.values():
        for mode in ("light", "dark"):
            tokens = getattr(preset, mode)
            d = tokens.destructive
            bg, cd = tokens.background.to_rgb(), tokens.card.to_rgb()
            cases.append(_case((d.h, d.s, d.lightness), bg, cd))
            expected.append(list(_py(d, bg, cd)))
    assert len(cases) == 2 * len(THEME_PRESETS)
    assert _run_js(cases, tmp_path) == expected


def _py(d: ColorScale, bg, cd) -> tuple[int, int, int]:
    return _types._solve_destructive_text((d.h, d.s, d.lightness), tuple(bg), tuple(cd))


def test_editor_solver_matches_python_on_random_and_far_card_inputs(tmp_path):
    rng = random.Random(3344)
    cases, expected = [], []
    inputs = [(dest, page, card) for dest, page, card in FAR_CARD_CASES]
    for _ in range(400):
        inputs.append(
            (
                (rng.randrange(361), rng.randrange(101), rng.randrange(101)),
                (rng.randrange(361), rng.randrange(101), rng.randrange(101)),
                (rng.randrange(361), rng.randrange(101), rng.randrange(101)),
            )
        )
    for dest, page, card in inputs:
        bg, cd = ColorScale(*page).to_rgb(), ColorScale(*card).to_rgb()
        cases.append(_case(dest, bg, cd))
        expected.append(list(_types._solve_destructive_text(dest, bg, cd)))
    assert _run_js(cases, tmp_path) == expected


# --- 1. the editor wires it into every place a token changes ------------------


def test_editor_recomputes_destructive_text_on_preset_mode_and_picker_changes():
    html = EDITOR.read_text()
    # applied after a preset / mode switch (applyAllTokens) ...
    body = re.search(r"function applyAllTokens\(mode\) \{(.*?)\n      \}", html, re.S)
    assert body and "applyDestructiveText(mode)" in body.group(1)
    # ... and after a destructive / background / card picker edit
    picker = re.search(r"function onColorChange\(e\) \{(.*?)\n      \}", html, re.S)
    assert picker
    for field in ("destructive", "background", "card"):
        assert f'field === "{field}"' in picker.group(1)
    assert "applyDestructiveText(mode)" in picker.group(1)


def test_destructive_text_is_derived_not_an_editable_or_exported_token():
    html = EDITOR.read_text()
    fields = re.search(r"var TOKEN_FIELDS = \[(.*?)\];", html, re.S)
    assert fields and "destructive_text" not in fields.group(1)
    # the export payload is ``currentTokens``; nothing stores the derived value there
    assert not re.search(r"currentTokens\[[^\]]*\]\[[\"']destructive_text", html)


# --- 3. W001 hint -------------------------------------------------------------


def test_w001_hint_names_settable_tokens_not_the_derived_one():
    hint = _contrast_hint("destructive_text", "background", 4.5)
    assert "derived" in hint
    for token in ("destructive", "background", "card"):
        assert token in hint
    assert "Adjust destructive_text" not in hint
    tint = _contrast_hint("destructive_text", "destructive_tint", 4.5)
    assert "Adjust destructive_text" not in tint
    assert "destructive" in tint and "background" in tint and "card" in tint
    # other pairs keep their wording
    assert _contrast_hint("primary", "background", 4.5).startswith("Adjust primary or background")


# --- 3. Tailwind ---------------------------------------------------------------


def test_tailwind_keeps_text_destructive_as_the_fill_and_exposes_the_text_token():
    from djust.theming.tailwind import generate_tailwind_config

    config = generate_tailwind_config("default")
    assert '"hsl(var(--destructive))"' in config or "hsl(var(--destructive))" in config
    assert "hsl(var(--destructive-text))" in config


def test_tailwind_docs_explain_text_destructive():
    doc = (REPO / "docs/website/guides/accessibility.md").read_text()
    assert "text-destructive-text" in doc
    assert "bg-destructive" in doc and "text-destructive`" in doc


def test_docs_say_to_override_both_tokens_and_state_the_surface_limit():
    doc = " ".join((REPO / "docs/website/guides/accessibility.md").read_text().split())
    assert "override `--destructive-text` too" in doc
    assert "extra_css_vars" in doc
    assert "`muted`, `accent` or `secondary`" in doc


# --- 3. the demo project -------------------------------------------------------


def test_demo_project_error_text_uses_the_token():
    css = DEMO_BASE.read_text()
    rule = re.search(r"\.text-destructive\s*\{([^}]*)\}", css)
    assert rule, "the demo's .text-destructive rule moved"
    assert "--destructive-text" in rule.group(1)
