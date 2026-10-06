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
