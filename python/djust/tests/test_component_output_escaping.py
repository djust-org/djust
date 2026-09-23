"""Inline-rendered UI components HTML-escape the values they interpolate.

``AlertComponent``, ``BadgeComponent``, ``CardComponent``, ``ProgressComponent``,
``ButtonComponent``, ``DropdownComponent``, ``ModalComponent``,
``SpinnerComponent``, ``NavbarComponent``, ``TabsComponent``,
``TableComponent`` and ``PaginationComponent`` build their markup in Python and return it marked safe,
so every text and attribute value is passed through ``conditional_escape``
first. A value the developer marked safe (``mark_safe``) is emitted as-is, and
URL attributes (``href``/``src``) keep only navigation-safe schemes.

Each case runs under all three CSS-framework renderers.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest
from django.utils.safestring import mark_safe

from djust import config as config_module
from djust.components.data.pagination import PaginationComponent
from djust.components.data.table import TableComponent
from djust.components.layout.navbar import NavbarComponent, NavItem
from djust.components.layout.tabs import TabsComponent
from djust.components.ui.alert import AlertComponent
from djust.components.ui.badge import BadgeComponent
from djust.components.ui.button import ButtonComponent
from djust.components.ui.card import CardComponent
from djust.components.ui.dropdown import DropdownComponent
from djust.components.ui.modal import ModalComponent
from djust.components.ui.progress import ProgressComponent
from djust.components.ui.spinner import SpinnerComponent
from djust.components.utils import url_attr

FRAMEWORKS = ("bootstrap5", "tailwind", "plain")

MARKUP = "<img src=x onerror=alert(1)>"
MARKUP_ESCAPED = "&lt;img src=x onerror=alert(1)&gt;"
QUOTED = 'x" onmouseover="y'
QUOTED_ESCAPED = "x&quot; onmouseover=&quot;y"
SAFE = "<b>ok</b>"


def _render(component, framework: str) -> str:
    with patch.object(config_module.config, "get", lambda key, default=None: framework):
        return str(component.render())


# Factories that put ``value`` into a text slot of each component.
TEXT_SLOTS = {
    "alert_message": lambda v: AlertComponent(message=v),
    "badge_text": lambda v: BadgeComponent(text=v),
    "card_title": lambda v: CardComponent(title=v),
    "card_body": lambda v: CardComponent(body=v),
    "card_footer": lambda v: CardComponent(footer=v),
    "progress_label": lambda v: ProgressComponent(value=10, custom_label=v),
    "button_label": lambda v: ButtonComponent(label=v),
    "dropdown_label": lambda v: DropdownComponent(label=v),
    "dropdown_item_text": lambda v: DropdownComponent(items=[{"text": v, "action": "go"}]),
    "modal_title": lambda v: ModalComponent(title=v, show=True),
    "modal_body": lambda v: ModalComponent(body=v, show=True),
    "spinner_label": lambda v: SpinnerComponent(label=v),
    "navbar_brand": lambda v: NavbarComponent(brand_name=v),
    "navbar_item_label": lambda v: NavbarComponent(items=[NavItem(label=v, href="/a/")]),
    "tabs_label": lambda v: TabsComponent(tabs=[{"id": "t1", "label": v, "content": "c"}]),
    "tabs_content": lambda v: TabsComponent(tabs=[{"id": "t1", "label": "L", "content": v}]),
    "table_cell": lambda v: TableComponent(columns=[{"key": "n", "label": "N"}], rows=[{"n": v}]),
    "table_header": lambda v: TableComponent(columns=[{"key": "n", "label": v}], rows=[]),
}

# Factories that put ``value`` into a quoted attribute.
ATTR_SLOTS = {
    "alert_alt_id": lambda v: AlertComponent(message="m", component_id=v),
    "card_image_alt": lambda v: CardComponent(title=v, image="/i.png"),
    "button_click": lambda v: ButtonComponent(label="b", on_click=v),
    "dropdown_item_action": lambda v: DropdownComponent(items=[{"text": "t", "action": v}]),
    "dropdown_item_data": lambda v: DropdownComponent(
        items=[{"text": "t", "action": "go", "data": {"id": v}}]
    ),
    "navbar_target": lambda v: NavbarComponent(items=[NavItem(label="a", href="/", target=v)]),
    "tabs_id": lambda v: TabsComponent(tabs=[{"id": v, "label": "L", "content": "c"}]),
    "pagination_id": lambda v: PaginationComponent(component_id=v, total_pages=3),
}


@pytest.mark.parametrize("framework", FRAMEWORKS)
@pytest.mark.parametrize("slot", sorted(TEXT_SLOTS))
def test_text_value_is_html_escaped(slot, framework):
    html = _render(TEXT_SLOTS[slot](MARKUP), framework)
    assert MARKUP not in html
    assert MARKUP_ESCAPED in html


@pytest.mark.parametrize("framework", FRAMEWORKS)
@pytest.mark.parametrize("slot", sorted(TEXT_SLOTS))
def test_marked_safe_text_value_passes_through(slot, framework):
    html = _render(TEXT_SLOTS[slot](mark_safe(SAFE)), framework)
    assert SAFE in html


@pytest.mark.parametrize("framework", FRAMEWORKS)
@pytest.mark.parametrize("slot", sorted(ATTR_SLOTS))
def test_attribute_value_is_html_escaped(slot, framework):
    html = _render(ATTR_SLOTS[slot](QUOTED), framework)
    assert QUOTED not in html
    assert QUOTED_ESCAPED in html


@pytest.mark.parametrize("framework", FRAMEWORKS)
def test_dropdown_data_key_outside_attribute_name_charset_is_skipped(framework):
    component = DropdownComponent(
        items=[{"text": "t", "action": "go", "data": {"x onclick=y": 1, "item-id": 2}}]
    )
    html = _render(component, framework)
    assert "onclick" not in html
    assert 'data-item-id="2"' in html


@pytest.mark.parametrize("framework", FRAMEWORKS)
def test_modal_footer_is_escaped_unless_marked_safe(framework):
    plain = _render(ModalComponent(footer=MARKUP, show=True), framework)
    assert MARKUP not in plain and MARKUP_ESCAPED in plain
    safe = _render(ModalComponent(footer=mark_safe(SAFE), show=True), framework)
    assert SAFE in safe


# --- URL attributes -------------------------------------------------------


@pytest.mark.parametrize("framework", FRAMEWORKS)
def test_card_image_src_is_escaped(framework):
    html = _render(CardComponent(image='x" onerror="alert(1)', title="t"), framework)
    assert 'src="x" onerror' not in html
    assert 'src="x&quot; onerror=&quot;alert(1)"' in html


@pytest.mark.parametrize("framework", FRAMEWORKS)
@pytest.mark.parametrize("src", ["/static/p.jpg", "https://cdn.example.com/p.png"])
def test_card_image_src_keeps_ordinary_urls(framework, src):
    html = _render(CardComponent(image=src, title="t"), framework)
    assert f'src="{src}"' in html


@pytest.mark.parametrize("framework", FRAMEWORKS)
def test_card_image_src_keeps_inline_image_data(framework):
    src = "data:image/png;base64,iVBORw0KGgo="
    assert f'src="{src}"' in _render(CardComponent(image=src), framework)


@pytest.mark.parametrize("framework", FRAMEWORKS)
@pytest.mark.parametrize(
    "href",
    ["javascript:alert(1)", " JaVaScRiPt:alert(1)", "java\tscript:alert(1)", "data:text/html,x"],
)
def test_navbar_non_navigation_scheme_becomes_hash(framework, href):
    html = _render(
        NavbarComponent(brand_href=href, items=[NavItem(label="a", href=href)]), framework
    )
    assert "script:" not in html.lower()
    assert "data:text" not in html
    assert 'href="#"' in html


@pytest.mark.parametrize("framework", FRAMEWORKS)
def test_navbar_ordinary_hrefs_are_unchanged(framework):
    html = _render(
        NavbarComponent(
            brand_href="/",
            items=[NavItem(label="Docs", href="/docs/"), NavItem("Out", "https://example.com/x")],
        ),
        framework,
    )
    assert 'href="/"' in html
    assert 'href="/docs/"' in html
    assert 'href="https://example.com/x"' in html


@pytest.mark.parametrize("framework", FRAMEWORKS)
def test_navbar_marked_safe_href_passes_through(framework):
    href = mark_safe("javascript:void(0)")
    html = _render(NavbarComponent(items=[NavItem(label="a", href=href)]), framework)
    assert 'href="javascript:void(0)"' in html


@pytest.mark.parametrize("framework", FRAMEWORKS)
def test_navbar_logo_src_is_escaped(framework):
    html = _render(NavbarComponent(brand_logo='x" onerror="y'), framework)
    assert 'src="x&quot; onerror=&quot;y"' in html


def test_url_attr_policy():
    assert url_attr("/a?b=1&c=2") == "/a?b=1&amp;c=2"
    assert url_attr("javascript:alert(1)") == "#"
    assert url_attr("data:image/png;base64,AAA") == "#"
    assert url_attr("data:image/png;base64,AAA", image=True) == "data:image/png;base64,AAA"
    assert url_attr("javascript:alert(1)", image=True) == "#"
    assert url_attr(None) == ""
    assert url_attr(mark_safe("javascript:void(0)")) == "javascript:void(0)"


# --- Benign values render exactly as before ---------------------------------


@pytest.mark.parametrize("framework", FRAMEWORKS)
def test_plain_text_values_render_unchanged(framework):
    html = _render(
        TableComponent(
            columns=[{"key": "n", "label": "Name"}, {"key": "age", "label": "Age"}],
            rows=[{"n": "Ada", "age": 36}],
        ),
        framework,
    )
    assert ">Name<" in html and ">Ada<" in html and ">36<" in html
    alert = _render(AlertComponent(message="Saved!"), framework)
    assert "Saved!" in alert
