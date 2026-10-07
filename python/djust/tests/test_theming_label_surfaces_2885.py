"""A ``*_foreground`` label is only read on the fill it belongs to (#2885 review).

The contrast matrix measures ``primary_foreground`` on ``primary``, ``success_foreground``
on ``success`` and so on. Moving a label to make that pair pass (white to dark ink on a
bright fill) is only safe if every place the CSS paints the label sits on THAT fill. A
rule that paints a label on anything else (a tooltip on ``--foreground``, a 12% wash of
the fill over the page) is measured by nothing, and a flip can turn it from readable to
invisible; two such rules shipped in the first version of #3390.

This scan walks every rule of the static stylesheets and the generated theme and pack CSS
that sets ``color`` from a ``--<fill>-foreground`` token, resolves the ``background`` of
the same rule and requires it to be the same fill, solid. Anything else needs an entry in
``REVIEWED`` with the reason, and an entry that is no longer needed fails.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from djust.theming import _types
from djust.theming.css_generator import ThemeCSSGenerator as CSSGenerator
from djust.theming.presets import THEME_PRESETS

pytestmark = pytest.mark.theming

ROOT = Path(_types.__file__).resolve().parents[1]
FILLS = (
    "primary",
    "brand",
    "destructive",
    "info",
    "success",
    "warning",
    "secondary",
    "accent",
    "code",
)
#: Labels whose token was flipped by #2885: they must always resolve to a background.
FLIPPED = ("primary", "brand", "destructive", "info", "success", "warning")

LABEL_RE = re.compile(r"var\(--(%s)-foreground(?![-\w])" % "|".join(FILLS))
BG_RE = re.compile(r"(?<![-\w])background(?:-color)?\s*:\s*([^;]*)")
COLOR_RE = re.compile(r"(?<![-\w])color\s*:\s*([^;]*)")

#: (source, first selector) -> why a label on something other than its own solid fill is fine.
REVIEWED = {
    (
        "components.css:djust_components",
        ".rich-select-trigger--variant-secondary",
    ): "secondary_foreground on a 14% wash of secondary; secondary has no derived text "
    "colour and #2885 moved secondary_foreground by at most 1 lightness point (solarized light)",
}


def _static_sources() -> dict[str, str]:
    return {
        f"{p.name}:{p.parent.name}": p.read_text()
        for p in sorted(ROOT.glob("**/*.css"))
        if "/tests/" not in str(p)
    }


def _generated_sources() -> dict[str, str]:
    from djust.theming.pack_css_generator import generate_pack_css
    from djust.theming.theme_packs import THEME_PACKS

    sources = {"css_generator": CSSGenerator("default").generate_css()}
    for pack in list(THEME_PACKS):
        sources[f"pack:{pack}"] = generate_pack_css(pack_name=pack)
    return sources


def _rules(css: str):
    css = re.sub(r"/\*.*?\*/", "", css, flags=re.S)
    for match in re.finditer(r"([^{}@]+)\{([^{}]*)\}", css):
        selectors = [s.strip() for s in match.group(1).split(",") if s.strip()]
        if selectors:
            yield re.sub(r"\s+", " ", selectors[0]), match.group(2)


def _label_rules():
    """(source, selector, label fill, background value or None) for every rule that sets
    ``color`` from a ``--<fill>-foreground`` token."""
    for source, css in {**_static_sources(), **_generated_sources()}.items():
        for selector, body in _rules(css):
            if selector.startswith(".text-"):
                continue  # a utility: it names a colour, it paints no surface
            color = COLOR_RE.search(body)
            label = LABEL_RE.search(color.group(1)) if color else None
            if not label:
                continue
            if re.search(r"var\(--%s-text\s*," % label.group(1), color.group(1)):
                continue  # reads the derived text colour; the label is only its fallback
            bg = BG_RE.search(body)
            yield source, selector, label.group(1), (bg.group(1).strip() if bg else None)


def _is_its_own_solid_fill(label: str, background: str) -> bool:
    """The first theme token the background reads (custom ``--dj-*`` hooks skipped) is the
    label's own fill, with no alpha, i.e. not a wash over the page."""
    tokens = [t for t in re.findall(r"var\(--([a-z0-9-]+)", background) if not t.startswith("dj-")]
    if not tokens or tokens[0] != label:
        return False
    return not re.search(r"/\s*[0-9.]+\s*\)", background)


def _divergences():
    for source, selector, label, background in _label_rules():
        if background is None:
            if label in FLIPPED:
                yield source, selector, label, "no background in the rule"
            continue
        if not _is_its_own_solid_fill(label, background):
            yield source, selector, label, background


def test_the_scan_sees_the_label_rules():
    rules = list(_label_rules())
    assert len(rules) > 150, len(rules)
    assert {label for _s, _sel, label, _bg in rules} >= set(FLIPPED) - {"brand"}


def test_a_label_is_only_painted_on_its_own_solid_fill():
    bad = [d for d in _divergences() if (d[0], d[1]) not in REVIEWED]
    assert not bad, bad[:8]


def test_every_reviewed_exception_is_still_a_divergence():
    seen = {(source, selector) for source, selector, _label, _bg in _divergences()}
    stale = [key for key in REVIEWED if key not in seen]
    assert not stale, f"no longer needed, remove: {stale}"


def test_the_tooltip_bubble_pairs_its_text_with_its_own_background():
    """``[data-tooltip]::after`` paints on ``--foreground``; its text is ``--background``.
    It read ``--primary-foreground``, which #2885 flipped to dark ink in 34 light presets."""
    css = (ROOT / "theming/static/djust_theming/css/components.css").read_text()
    for selector, body in _rules(css):
        if selector == "[data-tooltip]::after":
            assert "color: hsl(var(--background))" in body
            assert "background-color: hsl(var(--foreground))" in body
            return
    pytest.fail("the tooltip rule moved")


@pytest.mark.parametrize("name", sorted(THEME_PRESETS))
def test_the_tooltip_pair_reads_in_every_preset(name):
    from djust.theming.accessibility import AccessibilityValidator

    v = AccessibilityValidator()
    for mode in ("light", "dark"):
        tokens = getattr(THEME_PRESETS[name], mode)
        assert v.calculate_contrast_ratio(tokens.background, tokens.foreground) >= 4.5, (name, mode)


@pytest.mark.parametrize("variant", ["primary", "info", "success", "warning"])
def test_rich_select_variants_read_the_text_colour_of_their_wash(variant):
    css = (ROOT / "components/static/djust_components/components.css").read_text()
    for selector, body in _rules(css):
        if selector == f".rich-select-trigger--variant-{variant}":
            assert f"var(--{variant}-text," in COLOR_RE.search(body).group(1)
            return
    pytest.fail("variant rule moved")


# --- state rules, ancestors and the other surfaces (round 2) ---------------------------
#
# A rule that sets the label and the background together (above) is not the whole story:
# ``.chip:hover { background: accent }`` replaces the background under an ``--active`` chip's
# flipped label (23 dark presets went from 8-18:1 to ~1:1), and ``.code-block .hl-c`` paints
# ``--muted-foreground`` on an ANCESTOR's ``--code``. The scans below read those from the
# stylesheets, the way the matrix is a table: the surfaces and the hover alphas they find
# must be the ones the solver in ``scripts/fix_theme_text_contrast.py`` already guards.

import importlib.util  # noqa: E402
import sys  # noqa: E402

from djust.tests._label_moves_2885 import ORIGINAL_LIGHTNESS  # noqa: E402
from djust.theming._types import ColorScale  # noqa: E402
from djust.theming.a11y_exemptions import A11Y_EXEMPTIONS  # noqa: E402

_SCRIPT = ROOT.parents[1] / "scripts" / "fix_theme_text_contrast.py"
_spec = importlib.util.spec_from_file_location("fix_theme_text_contrast_surfaces", _SCRIPT)
assert _spec is not None and _spec.loader is not None
fix = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = fix
_spec.loader.exec_module(fix)

STATE_RE = re.compile(r":(hover|focus|focus-visible|focus-within|active)\b")
NOT_RE = re.compile(r":not\(([^)]*)\)")
ALPHA_RE = re.compile(r"/\s*([0-9.]+)\s*\)")

#: (source, selector) -> why a state rule may sit on something other than the label's fill.
REVIEWED_STATES: dict[tuple[str, str], str] = {}


def _classes(selector: str) -> set[str]:
    compound = re.split(r"[ >+~]+", selector.strip())[-1]
    compound = NOT_RE.sub("", compound)
    compound = re.sub(r"::?[\w-]+(\([^)]*\))?", "", compound)
    return set(re.findall(r"\.([\w-]+)", compound))


def _normal(selector: str) -> str:
    """The selector without states, ``:not()`` groups and pseudo-elements."""
    selector = NOT_RE.sub("", selector)
    selector = re.sub(r"::?[\w-]+(\([^)]*\))?", "", selector)
    return re.sub(r"\s+", " ", selector).strip()


def _same_family(a: str, b: str) -> bool:
    return a == b or a.startswith(b + "-") or b.startswith(a + "-")


def _all_rules():
    for source, css in {**_static_sources(), **_generated_sources()}.items():
        css = re.sub(r"/\*.*?\*/", "", css, flags=re.S)
        for match in re.finditer(r"([^{}@]+)\{([^{}]*)\}", css):
            selectors = [
                re.sub(r"\s+", " ", s.strip()) for s in match.group(1).split(",") if s.strip()
            ]
            yield source, selectors, match.group(2)


def _alpha_ok(fill: str, background: str) -> bool:
    """A solid own fill, or the fill at an alpha the solver guards (a button's hover)."""
    if _is_its_own_solid_fill(fill, background):
        return True
    alpha = ALPHA_RE.search(background)
    tokens = [t for t in re.findall(r"var\(--([a-z0-9-]+)", background) if not t.startswith("dj-")]
    return bool(
        alpha and tokens and tokens[0] == fill and float(alpha.group(1)) in fix.HOVER_FILL_ALPHAS
    )


def _state_divergences():
    """(source, state selector, label fill, background) for every state rule that replaces
    the background under a flipped label of the same element family."""
    rules = list(_all_rules())
    labels = []  # (fill, normalised selector)
    own_colour: dict[str, str] = {}  # element class -> colour its plain rule sets
    state_bg: dict[tuple[str, str], str] = {}  # (normalised selector, state) -> background
    for _source, selectors, body in rules:
        color = COLOR_RE.search(body)
        for selector in selectors:
            if not STATE_RE.search(selector) and color:
                for cls in _classes(selector):
                    own_colour.setdefault(cls, color.group(1))
            if STATE_RE.search(selector):
                bg = BG_RE.search(body)
                if bg:
                    state_bg[(_normal(selector), STATE_RE.search(selector).group(1))] = bg.group(1)
        label = LABEL_RE.search(color.group(1)) if color else None
        if label and label.group(1) in FLIPPED and not selectors[0].startswith(".text-"):
            if not re.search(r"var\(--%s-text\s*," % label.group(1), color.group(1)):
                labels.extend(
                    (label.group(1), _normal(sel)) for sel in selectors if not STATE_RE.search(sel)
                )
    for source, selectors, body in rules:
        bg = BG_RE.search(body)
        if not bg:
            continue
        for selector in selectors:
            state = STATE_RE.search(selector)
            if not state:
                continue
            excluded = set(re.findall(r"\.([\w-]+)", " ".join(NOT_RE.findall(selector))))
            normal = _normal(selector)
            for fill, label_selector in labels:
                label_cls = re.findall(r"\.([\w-]+)", label_selector.split(" ")[-1])
                if not label_cls or set(label_cls) & excluded:
                    continue
                last = normal.split(" ")[-1]
                if not (_same_family(last.lstrip("."), label_cls[-1])):
                    continue
                # an element of the state's class that sets its own colour does not wear the label
                state_cls = _classes(selector)
                if any(
                    c in own_colour and not re.search(r"var\(--%s-foreground" % fill, own_colour[c])
                    for c in state_cls
                ):
                    continue
                # a state rule scoped to the label's own context wins by specificity
                exact = state_bg.get((label_selector, state.group(1)))
                if exact is not None and normal != label_selector:
                    if _alpha_ok(fill, exact):
                        continue
                if not _alpha_ok(fill, bg.group(1)):
                    yield source, selector, fill, bg.group(1).strip()


def test_a_state_rule_never_puts_another_surface_under_a_flipped_label():
    bad = [d for d in _state_divergences() if (d[0], d[1]) not in REVIEWED_STATES]
    assert not bad, bad[:8]


def test_the_state_scan_finds_the_active_chip_when_the_exclusion_is_removed():
    """Mutation: the scan must flag the chip's hover rule without its ``:not(--active)``."""
    css = (ROOT / "components/static/djust_components/components.css").read_text()
    mutated = css.replace(
        ".dj-data-card-grid__filter:hover:not(.dj-data-card-grid__filter--active)",
        ".dj-data-card-grid__filter:hover",
    )
    assert mutated != css
    sources = {"mutant": mutated}
    original = globals()["_static_sources"]
    globals()["_static_sources"] = lambda: sources
    try:
        flagged = [d for d in _state_divergences() if d[1].startswith(".dj-data-card-grid__filter")]
    finally:
        globals()["_static_sources"] = original
    assert flagged and flagged[0][2] == "primary"


def test_the_active_filter_chip_keeps_its_fill_on_hover():
    """23 dark presets: hovering the active chip put ``--accent`` under the flipped
    ``--primary-foreground`` (8-18:1 to ~1:1). The hover affordance stays on the other chips."""
    hover = [
        sel
        for _src, sels, _body in _all_rules()
        for sel in sels
        if sel.startswith(".dj-data-card-grid__filter") and ":hover" in sel
    ]
    assert hover and all(":not(.dj-data-card-grid__filter--active)" in sel for sel in hover), hover


def _hover_alphas() -> set[float]:
    """Alphas of the fill under a solid-fill hover rule (the ones that keep a label on top)."""
    found = set()
    for _source, selectors, body in _all_rules():
        if not any(STATE_RE.search(s) for s in selectors):
            continue
        bg = BG_RE.search(body)
        if not bg:
            continue
        match = re.search(
            r"var\(--(%s)\b[^)]*\)\s*/\s*([0-9.]+)\)" % "|".join(fix.HOVER_LABEL_FILLS), bg.group(1)
        )
        if match and float(match.group(2)) >= 0.7:
            found.add(float(match.group(2)))
    return found


def test_the_hover_alphas_in_the_stylesheets_are_the_ones_the_solver_guards():
    found = _hover_alphas()
    assert found and found <= set(fix.HOVER_FILL_ALPHAS), found - set(fix.HOVER_FILL_ALPHAS)


def _muted_surfaces() -> set[str]:
    """Theme surfaces a rule paints ``--muted-foreground`` on in the SAME rule."""
    surfaces = set()
    for _source, selectors, body in _all_rules():
        color = COLOR_RE.search(body)
        bg = BG_RE.search(body)
        if not (color and bg and re.search(r"var\(--muted-foreground(?![-\w])", color.group(1))):
            continue
        if ALPHA_RE.search(bg.group(1)):
            continue
        tokens = [
            t for t in re.findall(r"var\(--([a-z0-9-]+)", bg.group(1)) if not t.startswith("dj-")
        ]
        if tokens:
            surfaces.add(tokens[0])
    return surfaces


def test_every_surface_muted_foreground_is_painted_on_is_one_the_solver_guards():
    surfaces = _muted_surfaces()
    assert {"muted", "card", "background"} <= surfaces, surfaces
    guarded = set(fix.PAINTED_SURFACES["muted_foreground"]) | {"border"}
    assert surfaces <= guarded, surfaces - guarded


def test_the_code_block_paints_its_syntax_comments_on_the_code_surface():
    """``.code-block .hl-c`` paints ``--muted-foreground`` on its ancestor's ``--code``: the
    reason ``code`` is in ``PAINTED_SURFACES`` (a same-rule scan cannot see an ancestor)."""
    comment = punct = surface = False
    for _source, selectors, body in _all_rules():
        color = COLOR_RE.search(body)
        if color and "--muted-foreground" in color.group(1):
            comment = comment or any(s.startswith(".code-block .hl-c") for s in selectors)
            punct = punct or any(
                s.startswith(".dj-code-snippet .hl-o") or ".code-block .hl-o" in s
                for s in selectors
            )
        if ".code-block" in selectors and "--code" in (
            BG_RE.search(body).group(1) if BG_RE.search(body) else ""
        ):
            surface = True
    assert comment and punct and surface
    assert "code" in fix.PAINTED_SURFACES["muted_foreground"]


def _current(preset: str, mode: str, token: str) -> ColorScale:
    return getattr(getattr(THEME_PRESETS[preset], mode), token)


def _original(key: tuple[str, str, str]) -> ColorScale:
    current = _current(*key)
    return ColorScale(current.h, current.s, ORIGINAL_LIGHTNESS[key])


def test_the_original_table_is_the_moved_tokens():
    assert len(ORIGINAL_LIGHTNESS) == 374
    for key, light in ORIGINAL_LIGHTNESS.items():
        assert _current(*key).lightness != light, f"{key} did not move"


@pytest.mark.parametrize("key", sorted(ORIGINAL_LIGHTNESS), ids=lambda k: "-".join(k))
def test_no_moved_token_reads_worse_on_a_surface_it_is_also_painted_on(key):
    preset, mode, token = key
    tokens = getattr(THEME_PRESETS[preset], mode)
    aux = fix.aux_surfaces(token, tokens)
    if not aux:
        return
    assert not fix._worsens(_original(key), _current(*key), aux), key


def test_a_label_flip_keeps_its_hover_state_readable():
    """The reviewer's hover case: ``natural20`` dark destructive went from 4.36 to 3.54 on
    hover, and 39 cases fell under 3:1. No flipped label is under 3:1 on a hover fill unless
    it already was before."""
    under = []
    for (preset, mode, token), light in ORIGINAL_LIGHTNESS.items():
        fill = token.removesuffix("_foreground")
        if not token.endswith("_foreground") or fill not in fix.HOVER_LABEL_FILLS:
            continue
        tokens = getattr(THEME_PRESETS[preset], mode)
        new, old = _current(preset, mode, token), _original((preset, mode, token))
        for name, surface, floor in fix.aux_surfaces(token, tokens):
            if fix._ratio(new, surface) < min(fix._ratio(old, surface), floor) - 1e-9:
                under.append((preset, mode, token, name))
    assert not under, under[:5]
    natural20 = getattr(THEME_PRESETS["natural20"], "dark")
    surfaces = fix.aux_surfaces("destructive_foreground", natural20)
    assert min(fix._ratio(natural20.destructive_foreground, s) for _n, s, _f in surfaces) >= 3.0


def test_the_code_surface_keeps_its_syntax_comments_readable():
    """The reviewer's code-block case: darkening ``muted_foreground`` of tailwind and
    everforest (light) put ``.hl-c`` comments on the dark ``--code`` at 3.1 and 2.1:1. Those two
    moves are held: the tokens are unchanged and the page pair is an exemption with the reason."""
    held_lightness = {("tailwind", "light"): 65, ("everforest", "light"): 55}
    for preset, mode in fix_exemptions():
        assert (preset, mode, "muted_foreground") not in ORIGINAL_LIGHTNESS, "the move is held"
        assert (
            _current(preset, mode, "muted_foreground").lightness == held_lightness[(preset, mode)]
        )
        for surface in ("muted", "background"):
            reason = A11Y_EXEMPTIONS[(preset, mode, "muted_foreground", surface)]
            assert "--code" in reason and "not debt" in reason
    for key in ORIGINAL_LIGHTNESS:
        if key[2] == "muted_foreground":
            tokens = getattr(THEME_PRESETS[key[0]], key[1])
            code = tokens.code
            assert (
                fix._ratio(_current(*key), code)
                >= min(fix._ratio(_original(key), code), 4.5) - 1e-9
            ), key


def fix_exemptions():
    from djust.theming.a11y_exemptions import CODE_SURFACE_EXEMPTIONS

    return CODE_SURFACE_EXEMPTIONS
