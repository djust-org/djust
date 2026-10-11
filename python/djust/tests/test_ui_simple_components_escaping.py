"""The ``djust.components.ui`` simple components HTML-escape their values.

Every ``*_simple.py`` component renders through ``Component.render()``: a Rust
implementation, else its ``template`` string, else ``_render_custom()`` (which
dispatches to ``_render_bootstrap`` / ``_render_tailwind`` / ``_render_plain``
where the component has per-framework output). These tests pin the Python
f-string path: the Rust instance and the template are switched off on the
instance so ``render()`` reaches ``_render_custom()`` under each CSS framework.

Values are escaped with ``conditional_escape``, so a value the developer marked
safe (``mark_safe`` / ``SafeString``) passes through unchanged. URL-bearing
attributes go through ``url_attr``: non-navigation schemes become ``#``.
"""

from __future__ import annotations

from typing import Any, Callable, Dict, List, Tuple
from unittest.mock import patch

import pytest
from django.utils.safestring import mark_safe

from djust import config as config_module
from djust.components.ui import (
    Accordion,
    Alert,
    Avatar,
    Badge,
    Breadcrumb,
    Button,
    ButtonGroup,
    Card,
    Checkbox,
    Divider,
    Dropdown,
    Icon,
    Input,
    ListGroup,
    Modal,
    NavBar,
    Offcanvas,
    Pagination,
    Progress,
    Radio,
    Range,
    Select,
    Spinner,
    Switch,
    Table,
    Tabs,
    TextArea,
    Toast,
    Tooltip,
)

MARKUP = "<img src=x onerror=alert(1)>"
QUOTE = 'x" onmouseover="y'
SAFE = "<b>ok</b>"

ALL_FRAMEWORKS = ("bootstrap5", "tailwind", "plain")
ONE_FRAMEWORK = ("bootstrap5",)


def _framework(framework: str):
    """Patch ONLY ``css_framework``; every other key keeps its real value."""
    real_get = config_module.config.get

    def _get(key, default=None):
        return framework if key == "css_framework" else real_get(key, default)

    return patch.object(config_module.config, "get", _get)


def _python_render(component: Any, framework: str) -> str:
    """Render through ``_render_custom()`` (the f-string path)."""
    component._rust_instance = None
    component.template = None
    with _framework(framework):
        return str(component.render())


# name -> (factory(value) -> component, frameworks the component distinguishes)
Factory = Callable[[Any], Any]
CASES: Dict[str, Tuple[Factory, Tuple[str, ...]]] = {
    "accordion": (
        lambda v: Accordion(items=[{"title": v, "content": v}], id=v),
        ONE_FRAMEWORK,
    ),
    "alert": (lambda v: Alert(text=v, variant=v, dismissable=True), ALL_FRAMEWORKS),
    "avatar": (lambda v: Avatar(src="/a.png", alt=v, size=v, status=v), ALL_FRAMEWORKS),
    "badge": (lambda v: Badge(text=v, variant=v, size=v), ALL_FRAMEWORKS),
    "breadcrumb": (
        lambda v: Breadcrumb(
            items=[{"label": v, "url": "/a"}, {"label": v, "url": None}],
            separator=v,
            show_home=True,
        ),
        ALL_FRAMEWORKS,
    ),
    "button": (lambda v: Button(text=v, variant=v, size=v), ALL_FRAMEWORKS),
    "button_group": (
        lambda v: ButtonGroup(buttons=[{"label": v, "variant": v}], size=v, role=v),
        ALL_FRAMEWORKS,
    ),
    "card": (lambda v: Card(body=v, header=v, footer=v), ALL_FRAMEWORKS),
    "checkbox": (
        lambda v: Checkbox(name=v, label=v, id=v, value=v, help_text=v),
        ONE_FRAMEWORK,
    ),
    "divider": (lambda v: Divider(text=v, style=v, margin=v), ALL_FRAMEWORKS),
    "dropdown": (
        lambda v: Dropdown(
            label=v, items=[{"label": v, "url": "/a"}], variant=v, size=v, id=v, split=True
        ),
        ALL_FRAMEWORKS,
    ),
    "icon": (lambda v: Icon(name=v, color=v, label=v, size=v), ALL_FRAMEWORKS),
    "input": (
        lambda v: Input(
            name=v,
            id=v,
            label=v,
            type=v,
            value=v,
            placeholder=v,
            help_text=v,
            size=v,
            validation_state="invalid",
            validation_message=v,
        ),
        ONE_FRAMEWORK,
    ),
    "list_group": (
        lambda v: ListGroup(
            items=[
                {"label": v, "url": "/a", "variant": v, "badge": {"text": v, "variant": v}},
                {"label": v, "variant": v},
            ]
        ),
        ALL_FRAMEWORKS,
    ),
    "modal": (lambda v: Modal(body=v, id=v, title=v, footer=v, size=v), ONE_FRAMEWORK),
    "navbar": (
        lambda v: NavBar(
            items=[
                {"label": v, "url": "/a"},
                {"label": v, "dropdown": [{"label": v, "url": "/b"}]},
            ],
            brand={"text": v, "url": "/", "logo": "/logo.png"},
            variant=v,
            sticky=v,
            container=v,
            expand=v,
            id=v,
        ),
        ALL_FRAMEWORKS,
    ),
    "offcanvas": (
        lambda v: Offcanvas(body=v, id=v, title=v, placement="start"),
        ONE_FRAMEWORK,
    ),
    "pagination": (lambda v: Pagination(current_page=2, total_pages=3, size=v), ONE_FRAMEWORK),
    "progress": (
        lambda v: Progress(value=40, variant=v, label=v, height=v),
        ONE_FRAMEWORK,
    ),
    "radio": (
        lambda v: Radio(name=v, options=[{"value": v, "label": v}], label=v, value=v, help_text=v),
        ONE_FRAMEWORK,
    ),
    "range": (
        lambda v: Range(name=v, id=v, label=v, show_value=True, help_text=v),
        ONE_FRAMEWORK,
    ),
    "select": (
        lambda v: Select(
            name=v,
            options=[{"value": v, "label": v}],
            id=v,
            label=v,
            help_text=v,
            size=v,
            validation_state="invalid",
            validation_message=v,
        ),
        ONE_FRAMEWORK,
    ),
    "spinner": (lambda v: Spinner(variant=v, size=v, animation=v, sr_text=v), ALL_FRAMEWORKS),
    "switch": (
        lambda v: Switch(name=v, label=v, id=v, help_text=v, value=v),
        ONE_FRAMEWORK,
    ),
    "table": (
        lambda v: Table(columns=[{"key": "a", "label": v}], data=[{"a": v}]),
        ALL_FRAMEWORKS,
    ),
    "tabs": (lambda v: Tabs(tabs=[{"title": v, "content": v}], id=v, style=v), ONE_FRAMEWORK),
    "textarea": (
        lambda v: TextArea(
            name=v,
            id=v,
            label=v,
            value=v,
            placeholder=v,
            help_text=v,
            validation_state="invalid",
            validation_message=v,
        ),
        ONE_FRAMEWORK,
    ),
    "toast": (lambda v: Toast(title=v, message=v, variant=v), ALL_FRAMEWORKS),
    "tooltip": (
        lambda v: Tooltip(content=v, text=v, placement=v, trigger=v),
        ALL_FRAMEWORKS,
    ),
}

PARAMS: List[Tuple[str, str]] = [
    (name, framework) for name, (_, frameworks) in CASES.items() for framework in frameworks
]


@pytest.mark.parametrize("name,framework", PARAMS)
def test_html_like_value_is_html_escaped(name: str, framework: str) -> None:
    factory, _ = CASES[name]
    html = _python_render(factory(MARKUP), framework)
    assert MARKUP not in html
    assert "<img src=x" not in html
    assert "&lt;img src=x onerror=alert(1)&gt;" in html


@pytest.mark.parametrize("name,framework", PARAMS)
def test_quoted_value_cannot_leave_its_attribute(name: str, framework: str) -> None:
    factory, _ = CASES[name]
    html = _python_render(factory(QUOTE), framework)
    assert '" onmouseover="' not in html
    assert "x&quot; onmouseover=&quot;y" in html


# Divider text and the Tooltip title were already escaped unconditionally
# (``html.escape`` / ``_escape_html_attr``) before these tests existed, so a
# marked-safe value is still escaped there.
ALWAYS_ESCAPED = {"divider", "tooltip"}


@pytest.mark.parametrize("name,framework", [(n, fw) for n, fw in PARAMS if n not in ALWAYS_ESCAPED])
def test_marked_safe_value_passes_through(name: str, framework: str) -> None:
    factory, _ = CASES[name]
    html = _python_render(factory(mark_safe(SAFE)), framework)
    assert SAFE in html
    assert "&lt;b&gt;" not in html


@pytest.mark.parametrize("framework", ALL_FRAMEWORKS)
def test_marked_safe_tooltip_content_passes_through(framework: str) -> None:
    html = _python_render(Tooltip(content=mark_safe(SAFE), text="tip"), framework)
    assert f">{SAFE}<" in html


@pytest.mark.parametrize("name,framework", PARAMS)
def test_plain_words_render_unchanged(name: str, framework: str) -> None:
    factory, _ = CASES[name]
    html = _python_render(factory("hello"), framework)
    assert "hello" in html
    assert "&amp;" not in html and "&quot;" not in html


# --- URL attributes -----------------------------------------------------------

URL_CASES: Dict[str, Tuple[Callable[[str], Any], Tuple[str, ...], str]] = {
    "breadcrumb": (
        lambda u: Breadcrumb(items=[{"label": "a", "url": u}, {"label": "b"}]),
        ALL_FRAMEWORKS,
        "href",
    ),
    "dropdown": (
        lambda u: Dropdown(label="m", items=[{"label": "a", "url": u}]),
        ALL_FRAMEWORKS,
        "href",
    ),
    "list_group": (
        lambda u: ListGroup(items=[{"label": "a", "url": u}]),
        ALL_FRAMEWORKS,
        "href",
    ),
    "navbar_item": (
        lambda u: NavBar(items=[{"label": "a", "url": u}]),
        ALL_FRAMEWORKS,
        "href",
    ),
    "navbar_dropdown_item": (
        lambda u: NavBar(items=[{"label": "a", "dropdown": [{"label": "b", "url": u}]}]),
        ALL_FRAMEWORKS,
        "href",
    ),
    "navbar_brand": (
        lambda u: NavBar(items=[], brand={"text": "t", "url": u}),
        ALL_FRAMEWORKS,
        "href",
    ),
    "navbar_logo": (
        lambda u: NavBar(items=[], brand={"text": "t", "url": "/", "logo": u}),
        ALL_FRAMEWORKS,
        "src",
    ),
    "avatar": (lambda u: Avatar(src=u, alt="a"), ALL_FRAMEWORKS, "src"),
}

URL_PARAMS: List[Tuple[str, str]] = [
    (name, framework) for name, (_, frameworks, _a) in URL_CASES.items() for framework in frameworks
]


@pytest.mark.parametrize("name,framework", URL_PARAMS)
def test_non_navigation_url_scheme_becomes_hash(name: str, framework: str) -> None:
    factory, _, attr = URL_CASES[name]
    html = _python_render(factory("javascript:alert(1)"), framework)
    assert "javascript:" not in html
    assert f'{attr}="#"' in html


@pytest.mark.parametrize("name,framework", URL_PARAMS)
def test_relative_url_is_kept(name: str, framework: str) -> None:
    factory, _, attr = URL_CASES[name]
    html = _python_render(factory("/path?a=1"), framework)
    assert f'{attr}="/path?a=1"' in html


@pytest.mark.parametrize("name,framework", URL_PARAMS)
def test_url_with_quote_is_escaped(name: str, framework: str) -> None:
    factory, _, attr = URL_CASES[name]
    html = _python_render(factory('/p" onmouseover="y'), framework)
    assert '" onmouseover="' not in html
    assert "&quot;" in html


def test_marked_safe_url_passes_through() -> None:
    html = _python_render(
        Breadcrumb(items=[{"label": "a", "url": mark_safe("/a?x=1&amp;y=2")}, {"label": "b"}]),
        "bootstrap5",
    )
    assert 'href="/a?x=1&amp;y=2"' in html


# --- NavBar id inside the inline onclick handlers --------------------------------


@pytest.mark.parametrize("framework", ("tailwind", "plain"))
def test_navbar_id_in_onclick_is_js_string_escaped(framework: str) -> None:
    html = _python_render(NavBar(items=[], id="x');alert(1);//"), framework)
    assert "x');alert(1)" not in html
    assert "x\\u0027)" in html


@pytest.mark.parametrize("framework", ("tailwind", "plain"))
def test_navbar_plain_id_in_onclick_is_unchanged(framework: str) -> None:
    html = _python_render(NavBar(items=[], id="main-nav"), framework)
    assert "getElementById('main-nav-" in html


# --- Default render() for components with no Rust implementation or template ----


@pytest.mark.parametrize(
    "name",
    ["accordion", "breadcrumb", "dropdown", "list_group", "navbar", "pagination", "radio"]
    + ["select", "table", "tabs"],
)
def test_default_render_escapes_components_without_template(name: str) -> None:
    factory, _ = CASES[name]
    component = factory(MARKUP)
    with _framework("bootstrap5"):
        html = str(component.render())
    if name == "pagination":
        # Only the size class carries the value.
        assert MARKUP not in html
        return
    assert MARKUP not in html
    assert "&lt;img" in html


# --- Default render() for markup slots handed to the Rust renderer ---------


def test_tooltip_content_is_escaped_on_the_default_render_path():
    from djust.components.ui.tooltip_simple import Tooltip

    html = str(Tooltip("<img src=x onerror=alert(1)>", "tip").render())
    assert "<img src=x" not in html
    assert "&lt;img src=x onerror=alert(1)&gt;" in html


def test_tooltip_marked_safe_content_passes_through_on_the_default_render_path():
    from djust.components.ui.tooltip_simple import Tooltip

    assert "<b>ok</b>" in str(Tooltip(mark_safe("<b>ok</b>"), "tip").render())


def test_modal_body_and_footer_are_escaped_on_the_default_render_path():
    from djust.components.ui.modal_simple import Modal

    html = str(Modal("<img src=x>", id="m", footer="<i>f</i>").render())
    assert "<img src=x>" not in html and "&lt;img src=x&gt;" in html
    assert "<i>f</i>" not in html and "&lt;i&gt;f&lt;/i&gt;" in html


def test_modal_body_and_footer_are_escaped_once_on_the_default_render_path():
    from djust.components.ui.modal_simple import Modal

    html = str(Modal("a & b", id="m", footer="c & d").render())
    assert "a &amp; b" in html and "c &amp; d" in html
    assert "&amp;amp;" not in html


def test_modal_body_and_footer_are_escaped_on_the_rust_render_path():
    from djust.components.ui.modal_simple import Modal

    modal = Modal("<img src=x>", id="m", footer="<i>f</i>")
    modal.update(title="t")  # update() renders through the Rust instance
    assert modal._rust_instance is not None
    html = str(modal.render())
    assert "<img src=x>" not in html and "&lt;img src=x&gt;" in html
    assert "<i>f</i>" not in html and "&lt;i&gt;f&lt;/i&gt;" in html


def test_modal_marked_safe_body_and_footer_pass_through_on_the_rust_render_path():
    from djust.components.ui.modal_simple import Modal

    modal = Modal(mark_safe("<p>b</p>"), id="m", footer=mark_safe("<b>f</b>"))
    modal.update(title="t")
    assert modal._rust_instance is not None
    html = str(modal.render())
    assert "<p>b</p>" in html and "<b>f</b>" in html
