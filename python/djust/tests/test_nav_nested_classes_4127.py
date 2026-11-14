"""#4127: ``{% nav_menu %}`` / ``{% nav_item %}`` draw the same classes on both engines.

The native handler uses ``dj-nav__item`` / ``dj-nav__link`` at every depth; the Django node used
``dj-nav__dropdown-item`` / ``dj-nav__dropdown-link`` below the top level, so the same template
was styled differently depending on the engine. Native is the target: the Django node now draws
the native classes, and ``components.css`` styles a nested item by where it sits
(``.dj-nav__dropdown-list .dj-nav__item``) while keeping the old selectors for markup that still
names them. A falsy ``class=`` (``None``) adds no class on either engine.

The parity baseline (``scripts/template-library-parity-baseline.csv``) never nests items, so the
nested shapes are compared here, element by element.
"""

from __future__ import annotations

import re
from collections import Counter
from html.parser import HTMLParser
from pathlib import Path

import pytest

import djust.components
from djust.tests.test_component_dj_attr_parity_3669 import CTX, _django, _rust


class _Classes(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.found: Counter[tuple[str, str]] = Counter()

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        for name, value in attrs:
            if name == "class":
                self.found[(tag, " ".join(sorted((value or "").split())))] += 1


def classes(markup: str) -> Counter[tuple[str, str]]:
    parser = _Classes()
    parser.feed(markup)
    parser.close()
    return parser.found


NESTED = {
    "two-levels": (
        '{% nav_menu %}{% nav_item label="Menu" %}'
        '{% nav_item label="A" href="/a" %}{% endnav_item %}'
        '{% nav_item label="B" event="b" description="d" %}{% endnav_item %}'
        "{% endnav_item %}{% endnav_menu %}"
    ),
    "active-child": (
        '{% nav_menu active="/a" %}{% nav_item label="Menu" %}'
        '{% nav_item label="A" href="/a" %}{% endnav_item %}'
        '{% nav_item label="B" href="/b" %}{% endnav_item %}'
        "{% endnav_item %}{% endnav_menu %}"
    ),
    "three-levels": (
        '{% nav_menu %}{% nav_item label="Top" %}{% nav_item label="Mid" %}'
        '{% nav_item label="Deep" href="/d" %}{% endnav_item %}{% endnav_item %}'
        "{% endnav_item %}{% endnav_menu %}"
    ),
    "mega": (
        '{% nav_menu %}{% nav_item label="Menu" mega=True %}'
        '{% nav_item label="A" href="/a" %}{% endnav_item %}{% endnav_item %}{% endnav_menu %}'
    ),
    "class-none": "{% nav_menu class=none %}{% endnav_menu %}",
}


@pytest.mark.parametrize("src", NESTED.values(), ids=list(NESTED))
def test_both_engines_draw_the_same_classes(src: str) -> None:
    ctx = dict(CTX, none=None)
    py, rs = classes(_django(src, ctx)), classes(_rust(src, ctx))
    assert py == rs, (
        f"\n  python only: {sorted((py - rs).elements())}"
        f"\n  rust   only: {sorted((rs - py).elements())}"
    )


@pytest.mark.parametrize("src", NESTED.values(), ids=list(NESTED))
def test_nested_items_use_the_native_classes(src: str) -> None:
    for html in (_django(src, dict(CTX, none=None)), _rust(src, dict(CTX, none=None))):
        assert "dj-nav__dropdown-item" not in html and "dj-nav__dropdown-link" not in html, html
        assert 'class="dj-nav None"' not in html, html


def test_an_active_child_is_marked_on_both_engines() -> None:
    src = NESTED["active-child"]
    for html in (_django(src, CTX), _rust(src, CTX)):
        assert (
            '<li class="dj-nav__item dj-nav__item--active"><a class="dj-nav__link" href="/a">'
            in html
        ), html


CSS = Path(djust.components.__file__).parent / "static/djust_components/components.css"


def _rule(selector: str) -> str:
    css = CSS.read_text(encoding="utf-8")
    match = re.search(r"(?m)^" + re.escape(selector) + r" \{([^}]*)\}", css)
    assert match, selector
    return match.group(1)


def test_the_css_styles_a_nested_item_as_a_dropdown_entry() -> None:
    """A nested ``dj-nav__link`` is drawn like a dropdown entry, not like a top-level tab: no
    tab underline on the active one, and the dropdown entry's padding and colour."""
    assert "var(--space-2" in _rule(".dj-nav__dropdown-list .dj-nav__link")
    assert "--foreground" in _rule(".dj-nav__dropdown-list .dj-nav__link")
    assert "box-shadow: none" in _rule(
        ".dj-nav__dropdown-list .dj-nav__item--active > .dj-nav__link"
    )


def test_the_old_dropdown_selectors_stay_styled() -> None:
    """Markup written against the old names (a theme pack, an app template) keeps its style."""
    for selector in (".dj-nav__dropdown-item", ".dj-nav__dropdown-link"):
        assert _rule(selector).strip()
