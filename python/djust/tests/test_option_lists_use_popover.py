"""Floating option lists sit on ``--popover``, like djust's dropdown menu.

shadcn puts every floating list (Select, Command, Popover, DropdownMenu) on
``bg-popover text-popover-foreground``, and djust's own ``.dj-dropdown-menu`` and
autocomplete already read ``--popover``. The combobox and rich-select lists read
``--card`` instead, the mentions list too, and the model selector's list had no
background at all, so the page showed through it. Every floating ``role="listbox"``
surface now fills with ``hsl(var(--popover, var(--card)) / var(--overlay-surface-opacity, 1))``
and sets ``--popover-foreground`` text, falling back to ``--card`` /
``--card-foreground`` for a theme without the popover tokens. #3591's opacity
variable stays.

The action menus (dropdown and context menus, the split-button menu, the
popover and the notification dropdown, and djust.theming's ``.dropdown-menu``)
moved to the same surface, and their items take the menu's text colour instead
of ``--muted-foreground``.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

pytestmark = pytest.mark.theming

ROOT = Path(__file__).resolve().parents[1]
COMPONENTS_CSS = ROOT / "components/static/djust_components/components.css"
COMPONENT_CLASSES_CSS = ROOT / "components/static/djust_components/components-classes.css"
THEME_CSS = ROOT / "theming/static/djust_theming/css/components.css"

OPTION_LISTS = (
    (COMPONENTS_CSS, ".combobox-dropdown"),
    (COMPONENTS_CSS, ".rich-select-dropdown"),
    (COMPONENTS_CSS, ".dj-autocomplete__dropdown"),
    (COMPONENTS_CSS, ".dj-mentions__dropdown"),
    (COMPONENT_CLASSES_CSS, ".dj-model-sel__dropdown"),
)

ACTION_MENUS = (
    (COMPONENTS_CSS, ".dropdown-menu"),
    (COMPONENTS_CSS, ".ctx-menu"),
    (COMPONENTS_CSS, ".split-btn-menu"),
    (COMPONENTS_CSS, ".popover"),
    (COMPONENTS_CSS, ".notif-dropdown"),
    (THEME_CSS, ".dropdown-menu"),
)

SURFACES = OPTION_LISTS + ACTION_MENUS

# Menu items that used to set ``--muted-foreground`` / ``--foreground`` themselves.
MENU_ITEMS = (
    (COMPONENTS_CSS, ".dropdown-item"),
    (COMPONENTS_CSS, ".ctx-item"),
    (COMPONENTS_CSS, ".split-btn-option"),
    (THEME_CSS, ".dropdown-item"),
)

# ``hsl(var(--popover, var(--card[, fallback])) / var(--overlay-surface-opacity, 1))``
_POPOVER_FILL = re.compile(
    r"hsl\(var\(--popover, var\(--card(?:, [^)]*)?\)\) / var\(--overlay-surface-opacity, 1\)\)"
)
_POPOVER_TEXT = re.compile(
    r"(?<![\w-])color\s*:\s*hsl\(var\(--popover-foreground, var\(--card-foreground(?:, [^)]*)?\)\)\)"
)


def _rule(css_path: Path, selector: str) -> str:
    css = css_path.read_text(encoding="utf-8")
    matches = list(re.finditer(r"(?m)^" + re.escape(selector) + r"\s*\{([^}]*)\}", css))
    assert len(matches) == 1, (selector, len(matches))
    return matches[0].group(1)


def _background(body: str) -> str | None:
    found = re.findall(r"(?<![\w-])background(?:-color)?\s*:\s*([^;]+)", body)
    return found[-1].strip() if found else None


def test_the_matchers_tell_popover_from_card():
    """Non-vacuous: a ``--card`` fill or a bare ``--foreground`` does not match."""
    good = "hsl(var(--popover, var(--card)) / var(--overlay-surface-opacity, 1))"
    assert _POPOVER_FILL.search(good)
    assert _POPOVER_FILL.search(good.replace("--card)", "--card, 0 0% 100%)"))
    assert not _POPOVER_FILL.search("hsl(var(--card) / var(--overlay-surface-opacity, 1))")
    assert not _POPOVER_FILL.search("hsl(var(--popover, var(--card)))")  # lost the opacity
    assert _POPOVER_TEXT.search(" color: hsl(var(--popover-foreground, var(--card-foreground)));")
    assert not _POPOVER_TEXT.search(" color: hsl(var(--foreground));")


@pytest.mark.parametrize(("css_path", "selector"), SURFACES, ids=lambda v: getattr(v, "name", v))
def test_option_list_fills_with_popover(css_path: Path, selector: str):
    body = _rule(css_path, selector)
    fill = _background(body)
    assert fill and _POPOVER_FILL.search(fill), (selector, fill)


@pytest.mark.parametrize(("css_path", "selector"), SURFACES, ids=lambda v: getattr(v, "name", v))
def test_option_list_text_is_popover_foreground(css_path: Path, selector: str):
    assert _POPOVER_TEXT.search(_rule(css_path, selector)), selector


def test_mentions_list_keeps_its_own_override():
    """``--dj-mentions-dropdown-bg`` still wins over the popover default."""
    fill = _background(_rule(COMPONENTS_CSS, ".dj-mentions__dropdown"))
    assert fill.startswith("var(--dj-mentions-dropdown-bg, "), fill


def test_combobox_options_take_the_list_text_colour():
    """An option with its own muted colour would not show the popover foreground."""
    body = _rule(COMPONENTS_CSS, ".combobox-option")
    assert re.search(r"(?<![\w-])color\s*:\s*inherit\s*;", body), body


@pytest.mark.parametrize(("css_path", "selector"), MENU_ITEMS, ids=lambda v: getattr(v, "name", v))
def test_menu_items_take_the_menu_text_colour(css_path: Path, selector: str):
    body = _rule(css_path, selector)
    assert re.search(r"(?<![\w-])color\s*:\s*inherit\s*;", body), (selector, body)


def test_dropdown_menu_is_the_convention_this_follows():
    body = _rule(COMPONENTS_CSS, ".dj-dropdown-menu__content")
    assert "var(--popover" in _background(body)
    assert "var(--popover-foreground" in body


# Every component that renders a floating ``role="listbox"``, by the class it
# renders. ``{dropdown_cls}`` in the autocomplete is ``dj-autocomplete__dropdown``.
_LISTBOX = re.compile(r'class="([\w-]+)"[^>]*role="listbox"|role="listbox"[^>]*class="([\w-]+)"')


def test_every_rendered_listbox_is_an_option_list_on_popover():
    sources = [*(ROOT / "components").rglob("*.py")]
    rendered = set()
    for path in sources:
        if "tests" in path.parts:
            continue
        for a, b in _LISTBOX.findall(path.read_text(encoding="utf-8")):
            rendered.add("." + (a or b))
    named = {selector for _, selector in OPTION_LISTS}
    assert len(rendered) >= 4, rendered  # the scan is finding the components
    assert rendered <= named, f"a new listbox needs the popover fill: {sorted(rendered - named)}"


def test_the_combobox_renders_its_list_as_a_listbox():
    from djust.components.components.combobox import Combobox

    html = str(Combobox(name="team", options=[{"value": "a", "label": "A"}]).render())
    assert re.search(r'<div class="combobox-dropdown" id="[^"]+" role="listbox">', html)
