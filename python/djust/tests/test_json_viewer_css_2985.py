"""JsonViewer values are readable on the viewer's own background (#2985).

``.dj-json__value`` (a class-coverage rule further down the stylesheet) sets
the page foreground colour, which is near-black. It has the same specificity as
the ``--string``/``--number``/``--bool`` rules, comes later, and so won: every
value rendered about 1.2:1 against the viewer's fixed dark background.

The viewer's background and its value colours are fixed fallbacks (a theme may
override the ``--dj-json-*`` tokens, but the shipped palette does not change
with the light/dark theme), so the same computation holds in both.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

CSS = (
    Path(__file__).resolve().parents[1]
    / "components"
    / "static"
    / "djust_components"
    / "components.css"
).read_text()


def _fallback(token: str) -> str:
    """The ``var(--token, #hex)`` fallback colour the stylesheet ships."""
    return re.search(rf"var\(--{token},\s*(#[0-9a-fA-F]{{6}})\)", CSS).group(1)


def _luminance(hex_colour: str) -> float:
    def channel(v: int) -> float:
        v = v / 255
        return v / 12.92 if v <= 0.03928 else ((v + 0.055) / 1.055) ** 2.4

    r, g, b = (int(hex_colour[i : i + 2], 16) for i in (1, 3, 5))
    return 0.2126 * channel(r) + 0.7152 * channel(g) + 0.0722 * channel(b)


def _contrast(a: str, b: str) -> float:
    hi, lo = sorted((_luminance(a), _luminance(b)), reverse=True)
    return (hi + 0.05) / (lo + 0.05)


BACKGROUND = _fallback("dj-json-bg")


@pytest.mark.parametrize(
    "token",
    [
        "dj-json-fg",
        "dj-json-key-color",
        "dj-json-string-color",
        "dj-json-number-color",
        "dj-json-bool-color",
    ],
)
def test_the_shipped_value_colours_read_on_the_viewer_background(token):
    assert _contrast(_fallback(token), BACKGROUND) >= 4.5


def _specificity(selector: str) -> int:
    return len(re.findall(r"\.[A-Za-z_][\w-]*", selector))


def _rules_for(suffix: str) -> list[tuple[int, str, int]]:
    """``(line, selector, specificity)`` of every rule that colours a value."""
    found = []
    for lineno, line in enumerate(CSS.splitlines(), start=1):
        if "{" not in line or "color:" not in line:
            continue
        for selector in line.split("{", 1)[0].split(","):
            selector = selector.strip()
            if selector.endswith(suffix) and "dj-json__value" in selector:
                found.append((lineno, selector, _specificity(selector)))
    return found


@pytest.mark.parametrize("kind", ["--string", "--number", "--bool", "--null"])
def test_no_later_rule_of_equal_or_higher_specificity_overrides_a_value_colour(kind):
    """Whatever colours a value class last, at its specificity, must be the
    viewer's own rule. The coverage rule ``.dj-json__value`` stays (the class
    coverage test needs it) but is outranked."""
    mine = [r for r in _rules_for(kind) if r[1].startswith(".dj-json-viewer ")]
    assert mine, kind
    line, _selector, specificity = mine[-1]
    others = [
        r
        for r in _rules_for("")  # selectors ending in a bare .dj-json__value
        if r[1].endswith(".dj-json__value") and r[0] > line and r[2] >= specificity
    ]
    assert others == []


def test_the_coverage_rule_the_class_coverage_test_needs_is_still_there():
    assert re.search(r"^\.dj-json__value \{", CSS, re.M)
