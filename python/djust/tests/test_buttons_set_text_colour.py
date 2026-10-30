"""A button that paints a background also sets its text colour.

A ``<button>`` does not inherit ``color``: without a declaration its label is the
browser's ``buttontext``, black. ``.dj-dropdown-menu__trigger`` painted
``--background`` and set no colour, so in a dark theme its label was black on a
near-black fill. The same held for every clickable rule that sets a background
(including ``none`` / ``transparent``) without a ``color``. Each now sets the
foreground token that pairs with its background, or ``inherit`` when the
background is transparent. The sweep fails on a new one.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from djust.tests.test_buttons_inherit_font import COMPONENTS_CSS, SWEPT, THEME_CSS, _pointer_rules

pytestmark = pytest.mark.theming

# Clickable controls with no text label.
NO_TEXT = frozenset(
    {".form-checkbox", ".form-radio", ".form-check-input", ".carousel-dot", ".dj-slider__input"}
)

_BACKGROUND = re.compile(r"(?<![\w-])background(?:-color)?\s*:\s*([^;]+)")
_COLOUR = re.compile(r"(?<![\w-])color\s*:\s*([^;]+)")


def _painted_without_colour(body: str) -> bool:
    return bool(_BACKGROUND.search(body)) and not _COLOUR.search(body)


def test_the_check_is_not_vacuous():
    assert _painted_without_colour(" background: hsl(var(--background)); cursor: pointer;")
    assert _painted_without_colour(" background: none; cursor: pointer;")
    assert not _painted_without_colour(" background: none; color: inherit;")
    assert not _painted_without_colour(" cursor: pointer;")  # no background: not this check
    painted = [
        sel for path in SWEPT for sel, body in _pointer_rules(path) if _BACKGROUND.search(body)
    ]
    assert len(painted) > 100, len(painted)


@pytest.mark.parametrize("css_path", SWEPT, ids=lambda p: p.name)
def test_every_painted_button_sets_a_text_colour(css_path: Path):
    missing = [
        sel
        for sel, body in _pointer_rules(css_path)
        if "::" not in sel and sel not in NO_TEXT and _painted_without_colour(body)
    ]
    assert not missing, f"{css_path.name}: set a foreground colour on {missing}"


def _rule(css_path: Path, selector: str) -> str:
    css = css_path.read_text(encoding="utf-8")
    for m in re.finditer(r"(?m)^" + re.escape(selector) + r"\s*\{([^}]*)\}", css):
        if "cursor: pointer" in m.group(1):
            return m.group(1)
    raise AssertionError(selector)


# (selector, the background it paints, the colour that pairs with it)
PAIRED = (
    (".dj-dropdown-menu__trigger", "--background", "--foreground"),
    (".rich-select-trigger", "--input-background", "--foreground"),
    (".data-table-copy-btn", "--muted", "--foreground"),
    (".dj-data-card-grid__filter", "--card", "--card-foreground"),
    (".dj-reactions__btn", "--accent", "--accent-foreground"),
)


@pytest.mark.parametrize(("selector", "background", "colour"), PAIRED)
def test_painted_button_uses_the_paired_foreground(selector: str, background: str, colour: str):
    body = _rule(COMPONENTS_CSS, selector)
    assert background in _BACKGROUND.findall(body)[-1], selector
    assert _COLOUR.search(body).group(1).startswith(f"hsl(var({colour}"), selector


def test_transparent_buttons_inherit_the_surrounding_text():
    for selector in (".notif-trigger", ".collapsible-trigger", ".dj-tour__prev"):
        assert _COLOUR.search(_rule(COMPONENTS_CSS, selector)).group(1).strip() == "inherit"


def test_upload_area_sets_foreground():
    assert (
        _COLOUR.search(_rule(THEME_CSS, ".upload-area")).group(1).startswith("hsl(var(--foreground")
    )


def test_the_active_filter_colour_still_wins():
    """The base rule's new colour must precede the modifier that recolours it."""
    css = COMPONENTS_CSS.read_text(encoding="utf-8")
    assert css.index(".dj-data-card-grid__filter {") < css.index(
        ".dj-data-card-grid__filter--active {"
    )
