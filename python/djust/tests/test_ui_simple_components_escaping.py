"""The ``djust.components.ui`` simple components HTML-escape their values.

Every ``*_simple.py`` component has ONE renderer (#4002): its ``template`` string where it has
one (``Checkbox``, ``Input``, ``ButtonGroup``, ``Offcanvas``), else ``_render_custom()``. The
eight with a ui ``*Component`` twin (``Alert``, ``Badge``, ``Button``, ``Card``, ``Dropdown``,
``Modal``, ``Progress``, ``Spinner``) return the twin's markup, which follows ``css_framework``;
the other crate-backed ones draw its markup; the rest choose their markup by ``css_framework``.
These tests render through that one renderer under each CSS framework.

A value in an ATTRIBUTE, and every URL (``data_url_attr``: non-navigation schemes become ``#``),
is checked and escaped whatever it is marked as (#3898). A TEXT slot of a template, and every
text slot of the fifteen and of the twin-drawn ``Dropdown``, is escaped whatever it is marked
as too (the twin classes hand their twin plain strings), except a declared
``markup_slots`` parameter the constructor escapes itself (#3932). The remaining
``_render_custom`` components escape text with ``conditional_escape``, so a marked-safe value
keeps its markup there.
"""

from __future__ import annotations

import contextlib
from typing import Any, Callable, Dict, Iterator, List, Tuple
from unittest.mock import patch

from html.parser import HTMLParser

import pytest
from django.utils.safestring import SafeString, mark_safe

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
    """Render through the component's one renderer (#4002): ``template`` where the class has
    one, else ``_render_custom()``."""
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
            label=v, items=[{"label": v, "action": "pick"}], variant=v, size=v, id=v, split=True
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
    "modal": (
        lambda v: Modal(body=v, id=v, title=v, footer=v, size=v, show=True),
        ALL_FRAMEWORKS,
    ),
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
        ALL_FRAMEWORKS,
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

#: Renders that draw none of the value: ``SpinnerComponent``'s Tailwind spinner maps the
#: variant onto a fixed class and draws no text (``sr_text`` has no twin equivalent, #4002).
VALUE_NOT_DRAWN = {("spinner", "tailwind")}
DRAWN_PARAMS = [p for p in PARAMS if p not in VALUE_NOT_DRAWN]


@pytest.mark.parametrize("name,framework", sorted(VALUE_NOT_DRAWN))
def test_a_render_that_draws_no_value_draws_none_of_it(name: str, framework: str) -> None:
    factory, _ = CASES[name]
    html = _python_render(factory(MARKUP), framework)
    assert "img" not in html and "onerror" not in html


@pytest.mark.parametrize("name,framework", DRAWN_PARAMS)
def test_html_like_value_is_html_escaped(name: str, framework: str) -> None:
    factory, _ = CASES[name]
    html = _python_render(factory(MARKUP), framework)
    assert MARKUP not in html
    assert "<img src=x" not in html
    assert "&lt;img src=x onerror=alert(1)&gt;" in html


@pytest.mark.parametrize("name,framework", DRAWN_PARAMS)
def test_quoted_value_cannot_leave_its_attribute(name: str, framework: str) -> None:
    factory, _ = CASES[name]
    html = _python_render(factory(QUOTE), framework)
    assert '" onmouseover="' not in html
    assert "x&quot; onmouseover=&quot;y" in html


# These print every text slot escaped, marked safe or not: what the retired Rust crate drew
# for the fifteen it backed (pinned by ``python/tests/test_ui_simple_golden_4002.py``), and what
# a ``template`` draws for a slot outside ``markup_slots`` (#3932). Since #4002 that is the one
# renderer; the ``_render_custom`` f-strings that kept the markup were never reached. The twin
# classes (#4002 slice 3) hand their twin plain strings, so the twin escapes them too.
ALWAYS_ESCAPED = {
    "alert",
    "badge",
    "button",
    "button_group",
    "card",
    "checkbox",
    "divider",
    "dropdown",
    "input",
    "progress",
    "range",
    "spinner",
    "switch",
    "textarea",
    "toast",
    "tooltip",
}


@pytest.mark.parametrize("name", sorted(ALWAYS_ESCAPED - {"tooltip"}))
def test_marked_safe_text_is_escaped_where_the_renderer_escapes_every_text_slot(name: str) -> None:
    factory, _ = CASES[name]
    html = _python_render(factory(mark_safe(SAFE)), "bootstrap5")
    assert SAFE not in html, html
    assert "&lt;b&gt;ok&lt;/b&gt;" in html, html


#: A marked-safe value keeps its markup in a TEXT slot and is escaped in every ATTRIBUTE: an item,
#: a URL (#3898) and, since #4009, a scalar constructor argument too (``role=``, ``aria-label=``, a
#: ``for=`` id, a class suffix). ``mark_safe`` means "this is markup", never "write this into a
#: quoted attribute". These cases hand the same value to every slot.
#: Components whose value lands ONLY in attributes (no text slot draws it), per framework.
ATTRIBUTE_ONLY: set[tuple[str, str]] = {
    *(("avatar", fw) for fw in ALL_FRAMEWORKS),
    *(("icon", fw) for fw in ALL_FRAMEWORKS),
    ("pagination", "bootstrap5"),  # the size suffix of the class
}


class _Attrs(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.values: list[str] = []

    def handle_starttag(self, tag, attrs):
        self.values += [v for _n, v in attrs if v]

    handle_startendtag = handle_starttag


def _attribute_values(html: str) -> list[str]:
    parser = _Attrs()
    parser.feed(html)
    parser.close()
    return parser.values


@pytest.mark.parametrize("name,framework", [(n, fw) for n, fw in PARAMS if n not in ALWAYS_ESCAPED])
def test_marked_safe_value_keeps_its_markup_in_text_and_is_escaped_in_attributes(
    name: str, framework: str
) -> None:
    factory, _ = CASES[name]
    html = _python_render(factory(mark_safe(SAFE)), framework)
    # an attribute that holds the value holds it escaped: decoded, it is the markup as text
    assert not any(SAFE in v for v in _attribute_values(html.replace("&lt;b&gt;", "\x00"))), html
    if (name, framework) not in ATTRIBUTE_ONLY:
        assert SAFE in html  # a text slot kept the markup


@pytest.mark.parametrize("framework", ALL_FRAMEWORKS)
def test_marked_safe_tooltip_content_passes_through(framework: str) -> None:
    html = _python_render(Tooltip(content=mark_safe(SAFE), text="tip"), framework)
    assert f">{SAFE}<" in html


@pytest.mark.parametrize("name,framework", DRAWN_PARAMS)
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


def test_marked_safe_url_is_checked_and_escaped_like_any_other() -> None:
    """A URL read out of an item is data: ``mark_safe`` does not exempt it from the scheme
    allowlist or the escaping (#3898). Pass the plain URL (``/a?x=1&y=2``), not a pre-escaped one."""
    html = _python_render(
        Breadcrumb(items=[{"label": "a", "url": mark_safe("/a?x=1&amp;y=2")}, {"label": "b"}]),
        "bootstrap5",
    )
    assert 'href="/a?x=1&amp;amp;y=2"' in html
    html = _python_render(
        Breadcrumb(items=[{"label": "a", "url": mark_safe("javascript:alert(1)")}, {"label": "b"}]),
        "bootstrap5",
    )
    assert "javascript:" not in html


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


@contextlib.contextmanager
def _csp_strict() -> Iterator[None]:
    from djust.config import config

    before = config.get("csp_strict", False)
    config.set("csp_strict", True)
    try:
        yield
    finally:
        config.set("csp_strict", before)


# --- NavBar id in the strict-mode toggle marker (#4146) --------------------------


@pytest.mark.parametrize("framework", ("tailwind", "plain"))
def test_navbar_id_in_toggle_marker_is_attribute_escaped(framework: str) -> None:
    from scripts.lib.template_library_parity import url_sweep

    with _csp_strict():
        html = _python_render(NavBar(items=[], id='x" onclick="alert(1)'), framework)
    names = {a.name for a in url_sweep.attributes(html)}
    assert "onclick" not in names, html
    assert 'data-navbar-toggle="x&quot; onclick=&quot;alert(1)-' in html


@pytest.mark.parametrize("framework", ("tailwind", "plain"))
def test_navbar_plain_id_in_toggle_marker_is_unchanged(framework: str) -> None:
    with _csp_strict():
        html = _python_render(NavBar(items=[], id="main-nav"), framework)
    suffix = "mobile" if framework == "tailwind" else "menu"
    assert 'data-navbar-toggle="main-nav-%s"' % suffix in html
    assert 'id="main-nav-%s"' % suffix in html


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


def test_dropdown_item_url_is_not_drawn():
    """``DropdownItem`` has no URL (#4002 slice 3): a simple item's ``url`` warns and the item
    links to ``#``, whatever the URL."""
    html = _python_render(
        Dropdown(label="m", items=[{"label": "a", "url": "javascript:alert(1)"}]), "bootstrap5"
    )
    assert "javascript:" not in html
    assert 'href="#"' in html


def test_modal_body_and_footer_are_escaped_on_the_default_render_path():
    from djust.components.ui.modal_simple import Modal

    html = str(Modal("<img src=x>", id="m", footer="<i>f</i>", show=True).render())
    assert "<img src=x>" not in html and "&lt;img src=x&gt;" in html
    assert "<i>f</i>" not in html and "&lt;i&gt;f&lt;/i&gt;" in html


def test_modal_marked_safe_body_and_footer_pass_through_on_the_default_render_path():
    from djust.components.ui.modal_simple import Modal

    html = str(
        Modal(mark_safe("<p>b</p>"), id="m", footer=mark_safe("<b>f</b>"), show=True).render()
    )
    assert "<p>b</p>" in html and "<b>f</b>" in html


# --- The template path agrees with the Rust instance (#3932) ----------------


def _template_classes() -> List[Tuple[str, type, str]]:
    """``(class name, class, parameter)`` of every ``*_simple`` class that has a ``template``,
    for each of its string parameters. Derived from the code, not listed."""
    import importlib
    import inspect
    import pkgutil

    import djust.components.ui as ui_package
    from djust.components.base import Component

    found: List[Tuple[str, type, str]] = []
    for info in pkgutil.iter_modules(ui_package.__path__):
        if not info.name.endswith("_simple"):
            continue
        module = importlib.import_module(f"djust.components.ui.{info.name}")
        for name, cls in vars(module).items():
            if not (
                inspect.isclass(cls)
                and issubclass(cls, Component)
                and cls.__module__ == module.__name__
                and cls.template is not None
            ):
                continue
            for param in inspect.signature(cls.__init__).parameters.values():
                if param.name == "self" or param.name == "id":
                    continue
                if param.default is inspect.Parameter.empty or isinstance(
                    param.default, (str, type(None))
                ):
                    found.append((name, cls, param.name))
    return found


def _build(cls: type, param: str, value: Any) -> Any:
    import inspect

    kwargs = {
        p.name: "req"
        for p in inspect.signature(cls.__init__).parameters.values()  # type: ignore[misc]
        if p.name != "self" and p.default is inspect.Parameter.empty
    }
    kwargs[param] = value
    return cls(**kwargs)


TEMPLATE_CASES = _template_classes()


def test_the_template_classes_were_found() -> None:
    names = {name for name, _cls, _param in TEMPLATE_CASES}
    # #4002: the crate-backed fifteen draw through ``_render_custom`` and have no template
    assert names >= {"Offcanvas", "Checkbox", "Input", "ButtonGroup"}
    assert not names & {"Alert", "Badge", "Card", "Modal", "Tooltip"}


@pytest.mark.parametrize(
    "name,cls,param", TEMPLATE_CASES, ids=[f"{n}.{p}" for n, _c, p in TEMPLATE_CASES]
)
def test_the_template_path_prints_a_marked_safe_value_as_the_rust_instance_does(
    name: str, cls: type, param: str
) -> None:
    """For each string parameter given ``mark_safe("<b>x</b>")``: the template render and the Rust
    instance's render agree on whether the markup appears. Where the constructor refuses the value
    (no Rust instance) the expectation is the class's own ``markup_slots``: a parameter it escapes
    itself and documents as markup."""
    marked = mark_safe("<b>x</b>")
    try:
        component = _build(cls, param, marked)
    except (TypeError, ValueError):
        pytest.skip("the constructor refuses the value")
    with _framework("bootstrap5"):
        via_template = _build(cls, param, marked)
        via_template._rust_instance = None
        template_prints_markup = "<b>x</b>" in str(via_template.render())
        if component._rust_instance is not None:
            rust_prints_markup = "<b>x</b>" in str(component.render())
            assert template_prints_markup == rust_prints_markup, (name, param)
        else:
            assert template_prints_markup == (param in cls.markup_slots), (name, param)  # type: ignore[attr-defined]


@pytest.mark.parametrize(
    "name,cls,param", TEMPLATE_CASES, ids=[f"{n}.{p}" for n, _c, p in TEMPLATE_CASES]
)
def test_a_plain_value_is_not_escaped_twice_on_the_template_path(
    name: str, cls: type, param: str
) -> None:
    try:
        component = _build(cls, param, "a & b")
    except (TypeError, ValueError):
        pytest.skip("the constructor refuses the value")
    component._rust_instance = None
    with _framework("bootstrap5"):
        html = str(component.render())
    assert "a &amp;amp; b" not in html, (name, param)


def test_the_declared_markup_slots_are_the_documented_ones() -> None:
    from djust.components.ui.modal_simple import Modal
    from djust.components.ui.offcanvas_simple import Offcanvas
    from djust.components.ui.tooltip_simple import Tooltip

    assert Modal.markup_slots == {"body", "footer"}
    assert Tooltip.markup_slots == {"content"}
    assert Offcanvas.markup_slots == {"body"}
    from djust.components.ui import Card

    assert Card.markup_slots == frozenset()


def _markup_slot_cases() -> List[Tuple[str, type, str]]:
    """``(class name, class, parameter)`` for every ``markup_slots`` entry of a ``*_simple``
    class, template or not (#4002: Modal and Tooltip draw through ``_render_custom``)."""
    import importlib
    import inspect
    import pkgutil

    import djust.components.ui as ui_package
    from djust.components.base import Component

    found: List[Tuple[str, type, str]] = []
    for info in pkgutil.iter_modules(ui_package.__path__):
        if not info.name.endswith("_simple"):
            continue
        module = importlib.import_module(f"djust.components.ui.{info.name}")
        for name, cls in vars(module).items():
            if (
                inspect.isclass(cls)
                and issubclass(cls, Component)
                and cls.__module__ == module.__name__
            ):
                found += [(name, cls, param) for param in sorted(cls.markup_slots)]
    return found


MARKUP_SLOT_CASES = _markup_slot_cases()


def test_the_markup_slot_classes_were_found() -> None:
    assert {(n, p) for n, _c, p in MARKUP_SLOT_CASES} == {
        ("Modal", "body"),
        ("Modal", "footer"),
        ("Tooltip", "content"),
        ("Offcanvas", "body"),
    }


@pytest.mark.parametrize(
    "name,cls,param", MARKUP_SLOT_CASES, ids=[f"{n}.{p}" for n, _c, p in MARKUP_SLOT_CASES]
)
def test_a_markup_slot_is_one_the_constructor_escapes_itself(
    name: str, cls: type, param: str
) -> None:
    """The criterion ``Component.markup_slots`` documents (review S2): the template prints the
    name as given because the constructor already ``conditional_escape``d it, so a plain string
    cannot reach the page raw and a marked-safe one is markup the developer wrote."""
    plain = getattr(_build(cls, param, "a <i> & b"), param)
    assert isinstance(plain, SafeString) and str(plain) == "a &lt;i&gt; &amp; b", (name, param)
    marked = getattr(_build(cls, param, mark_safe("<b>x</b>")), param)
    assert str(marked) == "<b>x</b>", (name, param)


def test_an_undeclared_markup_slot_template_escapes_a_marked_safe_value() -> None:
    """The class with no crate counterpart used to print the markup always (``Checkbox.label``)."""
    from djust.components.ui import Checkbox

    with _framework("bootstrap5"):
        html = str(Checkbox("n", label=mark_safe("<b>x</b>")).render())
    assert "<b>x</b>" not in html and "&lt;b&gt;x&lt;/b&gt;" in html
