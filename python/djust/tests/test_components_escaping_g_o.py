"""Component classes (``components/[g-o]*.py``) HTML-escape the values they
interpolate into their markup, while values marked safe pass through unchanged.
"""

from __future__ import annotations

from typing import Any, Callable

import django
import pytest
from django.conf import settings

if not settings.configured:
    settings.configure(SECRET_KEY="test-secret-key-escaping-g-o", INSTALLED_APPS=[], TEMPLATES=[])
    django.setup()

from django.utils.safestring import mark_safe  # noqa: E402

from djust.components.components.hover_card import HoverCard  # noqa: E402
from djust.components.components.input_group import InputGroup  # noqa: E402
from djust.components.components.loading_overlay import LoadingOverlay  # noqa: E402
from djust.components.components.markdown_editor import MarkdownEditor  # noqa: E402
from djust.components.components.masonry_grid import MasonryGrid  # noqa: E402
from djust.components.components.meter import Meter  # noqa: E402
from djust.components.components.modal import Modal  # noqa: E402
from djust.components.components.nav_menu import NavMenu  # noqa: E402
from djust.components.components.notification_badge import NotificationBadge  # noqa: E402
from djust.components.components.number_stepper import NumberStepper  # noqa: E402

TAG = "<img src=x onerror=alert(1)>"
QUOTE = 'x" onmouseover="y'

#: name -> factory taking the value to place in the escaped slot.
FACTORIES: dict[str, Callable[[Any], Any]] = {
    "hover_card_content": lambda v: HoverCard(trigger="t", content=v),
    "input_group_content": lambda v: InputGroup(content=v),
    "loading_overlay_content": lambda v: LoadingOverlay(content=v),
    "masonry_item_content": lambda v: MasonryGrid(items=[{"content": v}]),
    "meter_segment_value": lambda v: Meter(
        segments=[{"value": v, "label": "a"}], total=0, show_legend=True
    ),
    "modal_content": lambda v: Modal(is_open=True, title="t", content=v),
    "nav_menu_content": lambda v: NavMenu(content=v),
    "notification_badge_size": lambda v: NotificationBadge(count=1, size=v),
    "number_stepper_value": lambda v: NumberStepper(name="n", value=v),
    "number_stepper_step": lambda v: NumberStepper(name="n", step=v),
    "number_stepper_min": lambda v: NumberStepper(name="n", min_val=v),
    "number_stepper_max": lambda v: NumberStepper(name="n", max_val=v),
    "markdown_editor_rows": lambda v: MarkdownEditor(name="n", rows=v),
}

#: Slots whose value is rendered as element content (a marked-safe value keeps its markup).
CONTENT_SLOTS = [
    "hover_card_content",
    "input_group_content",
    "loading_overlay_content",
    "masonry_item_content",
    "modal_content",
    "nav_menu_content",
]


def _render(key: str, value: Any) -> str:
    return str(FACTORIES[key](value).render())


@pytest.mark.parametrize("key", sorted(FACTORIES))
def test_tag_like_value_is_html_escaped(key: str) -> None:
    out = _render(key, TAG)
    assert TAG not in out
    assert "&lt;img" in out


@pytest.mark.parametrize("key", sorted(FACTORIES))
def test_quote_in_value_is_html_escaped(key: str) -> None:
    out = _render(key, QUOTE)
    assert QUOTE not in out
    assert "&quot;" in out


@pytest.mark.parametrize("key", CONTENT_SLOTS)
def test_marked_safe_value_passes_through(key: str) -> None:
    out = _render(key, mark_safe("<b>ok</b>"))
    assert "<b>ok</b>" in out


@pytest.mark.parametrize("key", CONTENT_SLOTS)
def test_rendered_component_passes_through(key: str) -> None:
    inner = NotificationBadge(count=3).render()
    out = _render(key, inner)
    assert str(inner) in out


def test_plain_number_stepper_output_unchanged() -> None:
    out = str(NumberStepper(name="qty", value=2, min_val=0, max_val=9, step=1).render())
    assert 'value="2" step="1" min="0" max="9"' in out


def test_nav_menu_item_href_non_navigation_scheme_becomes_hash() -> None:
    out = str(NavMenu(items=[{"label": "a", "href": "javascript:alert(1)"}]).render())
    assert "javascript:" not in out
    assert 'href="#"' in out


def test_nav_menu_brand_href_non_navigation_scheme_becomes_hash() -> None:
    out = str(NavMenu(brand="B", brand_href="javascript:alert(1)").render())
    assert "javascript:" not in out
    assert 'class="dj-nav__brand" href="#"' in out


def test_nav_menu_relative_href_still_works() -> None:
    out = str(
        NavMenu(brand="B", brand_href="/home", items=[{"label": "a", "href": "/path?a=1"}]).render()
    )
    assert 'href="/home"' in out
    assert 'href="/path?a=1"' in out


def test_nav_menu_href_quote_is_escaped() -> None:
    out = str(NavMenu(items=[{"label": "a", "href": QUOTE}]).render())
    assert QUOTE not in out
    assert "&quot;" in out


def test_nav_menu_marked_safe_href_passes_through() -> None:
    out = str(NavMenu(items=[{"label": "a", "href": mark_safe("/x?a=1&b=2")}]).render())
    assert 'href="/x?a=1&b=2"' in out
