"""Free-form style values: an inline style by default, ``data-dj-style-*`` under a strict CSP (#3979).

A ``style-src`` without ``'unsafe-inline'`` refuses every ``style="..."`` attribute and every
inline ``<style>`` block. Every value a component writes that no class can stand for (a column
width, a user's colour, an item height) goes through ONE helper,
:func:`djust.components.utils.style_attrs` (its check for one value is
:func:`~djust.components.utils.style_value`; the Rust components take ``strict_styles``):

* by default (``LIVEVIEW_CONFIG["csp_strict_styles"]`` off) it is the inline ``style`` the site
  has always written, byte for byte, now from a checked value: there at first paint and with
  JavaScript off;
* with the switch on it is ``data-dj-style`` plus ``data-dj-style-<property>="<value>"``, the page
  head carries ``<meta name="djust-styles" content="attrs">`` and the client module
  ``54-dj-style.js`` applies the values through the CSSOM (``tests/js/dj_style.test.js``).

Percent fills are a ``dj-progress-w-N`` class in both modes (and, by default, also their old
inline width).

* ``TestTheHelper``: the property table, each kind's check, hostile values dropped in both modes
  with one warning per property, the escaping of what is written, the two modes.
* ``TestTheClientAndServerAgree``: the client applies exactly the server's properties.
* ``TestEverySite``: one row per site and render path (component class, Django tag, the same tag
  on the Rust engine, the native handler, the Rust component), in both modes: by default the
  site's old inline style, byte for byte, and no ``data-dj-style``; in strict mode no inline
  style for a free-form value, no ``<style>`` block, the ``data-dj-style-*`` attributes.
* ``TestThePageMarker``: the meta is in a strict page's head and nowhere else.
* ``TestDjustSkeleton``: ``{% djust_skeleton %}`` in both modes.
* ``TestBothEnginesWriteTheSameAttributes``: ``{% data_table %}`` (the Django tag and its native
  handler) write the same width attribute, in both modes.
"""

from __future__ import annotations

import html as _html
import logging
import re
from pathlib import Path
from typing import Any, Callable

import pytest
from django.template import Context, Engine
from django.utils.safestring import mark_safe

from djust.components import rust_handlers as rh
from djust.components.utils import (
    STYLE_PROPERTIES,
    STYLE_PROPERTY_NAMES,
    STYLES_META,
    progress_fill,
    strict_styles,
    style_attrs,
    style_value,
)
from djust.config import config

_COMPONENTS = Path(rh.__file__).resolve().parent
_ENGINE = Engine(
    dirs=[str(_COMPONENTS / "templates")],
    libraries={
        "djust_components": "djust.components.templatetags.djust_components",
        "live_tags": "djust.templatetags.live_tags",
        "theme_components": "djust.theming.templatetags.theme_components",
        "static": "django.templatetags.static",
        "djust_assets": "djust.templatetags.djust_assets",
    },
)


@pytest.fixture(params=["default", "strict"])
def mode(request):
    before = config.get("csp_strict_styles", False)
    config.set("csp_strict_styles", request.param == "strict")
    try:
        yield request.param
    finally:
        config.set("csp_strict_styles", before)


@pytest.fixture
def strict():
    before = config.get("csp_strict_styles", False)
    config.set("csp_strict_styles", True)
    try:
        yield
    finally:
        config.set("csp_strict_styles", before)


def _tag(source: str, **context: Any) -> str:
    return _ENGINE.from_string("{% load djust_components %}" + source).render(Context(context))


_RUST_READY: list[bool] = []


def _engine(source: str, **context: Any) -> str:
    from djust._rust import render_template

    if not _RUST_READY:
        rh.register_with_rust_engine()
        _RUST_READY.append(True)
    return render_template(source, context)


#: Constant inline styles a component still writes elsewhere in its markup (a hidden trigger, a
#: pointer cursor, the Bootstrap navbar's forced flex). No caller's value reaches them.
CONSTANT_STYLES = {"display:none", "cursor:pointer", "display:flex!important"}


def _assert_no_free_form_inline_style(markup: str) -> None:
    assert "<style" not in markup, markup
    for inline in re.findall(r'\sstyle="([^"]*)"', markup):
        assert inline.replace(" ", "").rstrip(";") in CONSTANT_STYLES, markup


def _dj(prop: str, value: str) -> str:
    return f'data-dj-style-{prop}="{value}"'


# ---------------------------------------------------------------------------
# The helper
# ---------------------------------------------------------------------------

HOSTILE = [
    "1px;background:red",
    "1px}body{display:none",
    "url(https://example.com/x.png)",
    "expression(alert(1))",
    '1px" onmouseover="alert(1)',
    "1px</div><script>alert(1)</script>",
    "1px\\;",
    "1px /* x */",
    "attr(data-x)",
]


class TestTheHelper:
    def test_default_mode_writes_an_inline_style(self):
        assert style_attrs(width="240px", min_width="10rem") == (
            ' style="width:240px;min-width:10rem"'
        )
        assert style_attrs(height="1rem", css="height: {};") == ' style="height: 1rem;"'

    def test_a_dropped_value_writes_the_pairs_not_the_template(self):
        out = style_attrs(width="1px", height="url(x)", css="width: {}; height: {};")
        assert out == ' style="width:1px"'

    def test_strict_mode_writes_data_attributes_and_the_marker(self, strict):
        assert style_attrs(width="240px", min_width="10rem", css="ignored {} {}") == (
            ' data-dj-style data-dj-style-width="240px" data-dj-style-min-width="10rem"'
        )

    def test_a_mapping_names_hyphenated_and_custom_properties(self, strict):
        out = style_attrs({"--dj-masonry-columns": 3, "-webkit-line-clamp": 2})
        assert out == (
            ' data-dj-style data-dj-style---dj-masonry-columns="3"'
            ' data-dj-style--webkit-line-clamp="2"'
        )

    @pytest.mark.parametrize(
        "prop,value",
        [
            ("width", "calc(100% - 2rem)"),
            ("height", "50%"),
            ("background-color", "hsl(var(--primary))"),
            ("color", "#abc"),
            ("z-index", "-1"),
            ("z-index", "9999"),
            ("aspect-ratio", "16 / 9"),
            ("animation-delay", "150ms"),
            ("grid-column", "2/span 3"),
            ("grid-template-columns", "repeat(4,1fr)"),
            ("grid-auto-rows", "minmax(calc(10vh + 1rem),auto)"),
            ("position", "sticky"),
            ("position", "relative"),
            ("display", "-webkit-box"),
            ("overflow-y", "auto"),
            ("font-weight", "bold"),
        ],
    )
    def test_each_kind_accepts_its_values(self, prop, value):
        assert style_value(prop, value) == value

    @pytest.mark.parametrize("prop", sorted(STYLE_PROPERTIES))
    @pytest.mark.parametrize("value", HOSTILE)
    def test_a_hostile_value_is_never_written(self, prop, value, mode):
        assert style_value(prop, value) == ""
        assert style_attrs({prop: value}) == ""
        assert style_attrs({prop: mark_safe(value)}) == ""

    @pytest.mark.parametrize(
        "prop,value",
        [
            ("display", "none"),  # only the two layouts the sites use
            ("position", "fixed"),  # nothing that can cover the page
            ("position", "absolute"),
            ("position", "absolute;top:0"),
            ("z-index", "10000"),  # bounded stacking
            ("z-index", "-2147483647"),
            ("z-index", "auto"),
            ("z-index", "1.5"),
            ("color", "notacolour"),
            ("grid-template-columns", "repeat(a,1fr)"),
            ("grid-column", "1/span x"),
            ("animation-delay", "150"),
        ],
    )
    def test_a_value_of_the_wrong_kind_is_dropped(self, prop, value):
        assert style_attrs({prop: value}) == ""

    def test_nothing_for_an_absent_value(self, mode):
        assert style_attrs(width=None, height="", color="  ", z_index=False) == ""

    @pytest.mark.parametrize("name", ["cursor", "inset", "margin", "behavior"])
    def test_an_unknown_property_is_a_programming_error(self, name):
        with pytest.raises(ValueError):
            style_attrs({name: "1px"})
        with pytest.raises(ValueError):
            style_value(name, "1px")

    def test_one_warning_per_property(self, caplog, monkeypatch):
        from djust.components import utils

        monkeypatch.setattr(utils, "_STYLE_WARNED", set())
        with caplog.at_level(logging.WARNING, logger="djust.components.utils"):
            style_attrs(width="1px;x:y")
            style_attrs(width="url(x)")
            style_attrs(height="url(x)")
        messages = [r.getMessage() for r in caplog.records]
        assert len(messages) == 2, messages
        assert "width" in messages[0] and "height" in messages[1]
        # The value itself is never logged.
        assert not any("url(" in m or "x:y" in m for m in messages)

    @pytest.mark.parametrize("css", [None, "width: {}"])
    def test_what_is_written_is_escaped_for_a_quoted_attribute(self, monkeypatch, mode, css):
        """Every check refuses a quote; this proves the escaping behind them on its own: a check
        that let one through still leaves one well-formed attribute."""
        from djust.components import utils

        evil = '1px" onclick="x()" data-x="<b>&'
        monkeypatch.setitem(utils.STYLE_PROPERTIES, "width", lambda v: evil)
        out = style_attrs(width="1px", css=css)
        assert '"' not in out.split('="', 1)[-1].rsplit('"', 1)[0].replace("&quot;", "")
        assert "<" not in out and 'onclick="' not in out
        assert _html.escape(evil) in out
        assert re.fullmatch(r'( data-dj-style)?( [-a-z]+="[^"]*")+', out), out

    def test_the_switch_is_the_one_config_key(self):
        assert strict_styles() is False  # the default
        config.set("csp_strict_styles", True)
        try:
            assert strict_styles() is True
        finally:
            config.set("csp_strict_styles", False)

    def test_progress_fill(self, mode):
        cls, style = progress_fill(12.5, css="width: {}%")
        assert cls == "dj-progress-w-13"  # half up, as the Rust component
        assert style == ("" if mode == "strict" else ' style="width: 12.5%"')
        assert progress_fill(250)[0] == "dj-progress-w-100"
        assert progress_fill(-3)[0] == "dj-progress-w-0"
        assert progress_fill("nope")[0] == "dj-progress-w-0"
        assert progress_fill(float("inf"))[0] == "dj-progress-w-0"
        if mode == "default":
            # Checked and clamped: nothing but a number in 0..100 reaches the style.
            assert progress_fill(40)[1] == ' style="width: 40%"'
            assert progress_fill(250)[1] == ' style="width: 100%"'
            assert progress_fill(-3)[1] == ' style="width: 0%"'
            assert progress_fill('40"><script>')[1] == ' style="width: 0%"'
            assert progress_fill(float("nan"))[1] == ' style="width: 0%"'
            assert progress_fill("33.5")[1] == ' style="width: 33.5%"'

    @pytest.mark.parametrize("size", ["-3", "-3px", "+3px", -3])
    def test_a_negative_skeleton_size_falls_back_as_it_always_has(self, mode, size):
        out = _ENGINE.from_string(
            "{% load live_tags %}{% djust_skeleton width=w height=w %}"
        ).render(Context({"w": size}))
        assert "-3" not in out and "+3" not in out
        assert ("width:100%;height:1em" in out) or (
            _dj("width", "100%") in out and _dj("height", "1em") in out
        ), out


class TestTheClientAndServerAgree:
    def test_the_client_applies_exactly_the_server_properties(self):
        js = (_COMPONENTS.parent / "static" / "djust" / "src" / "54-dj-style.js").read_text()
        listed = re.search(r"const _DJ_STYLE_PROPS = \((.*?)\)\.split", js, re.S).group(1)
        props = set("".join(re.findall(r"'([^']*)'", listed)).split())
        assert props == set(STYLE_PROPERTY_NAMES) == set(STYLE_PROPERTIES)
        assert len(STYLE_PROPERTY_NAMES) == len(set(STYLE_PROPERTY_NAMES))
        assert not {"inset", "margin"} & props


# ---------------------------------------------------------------------------
# Every site, both modes
# ---------------------------------------------------------------------------


def _ui_progress(framework: str, **kw: Any) -> Callable[[], str]:
    def render() -> str:
        from djust.components.ui.progress import ProgressComponent

        before = config.get("css_framework", "bootstrap5")
        config.set("css_framework", framework)
        try:
            return str(ProgressComponent(value=40, height="12px", **kw).render())
        finally:
            config.set("css_framework", before)

    return render


def _progress_simple(path: str) -> Callable[[], str]:
    def render() -> str:
        from djust.components.ui.progress_simple import Progress

        p = Progress(value=40, height="12px")
        if path == "python":
            return p._render_custom()
        if path == "template":
            p._rust_instance = None
            return str(p.render())
        assert p._rust_instance is not None
        return str(p.render())

    return render


def _avatar_simple(rust: bool) -> Callable[[], str]:
    def render() -> str:
        from djust.components.ui.avatar_simple import Avatar

        a = Avatar(initials="AB", size="lg")
        if rust:
            assert a._rust_instance is not None
            return str(a.render())
        return a._render_bootstrap()

    return render


def _navbar() -> str:
    from djust.components.layout.navbar import NavbarComponent

    return str(NavbarComponent(brand_name="Acme", brand_logo="/logo.png", items=[]).render())


def _spinner(size: str, type_: str = "border", framework: str = "bootstrap5") -> Callable[[], str]:
    def render() -> str:
        from djust.components.ui.spinner import SpinnerComponent

        before = config.get("css_framework", "bootstrap5")
        config.set("css_framework", framework)
        try:
            return str(SpinnerComponent(size=size, type=type_).render())
        finally:
            config.set("css_framework", before)

    return render


def _foreign_key() -> str:
    from djust.components.forms.foreign_key import ManyToManySelect

    class _Row:
        pk = 1

        def __str__(self) -> str:
            return "One"

    return str(ManyToManySelect(name="m", queryset=[_Row()], render_as="checkboxes").render())


def _dropdown() -> str:
    from djust import LiveView
    from djust.components._interactive import DropdownMenu

    class _Page(LiveView):
        exposure_policy = "legacy"
        template = "<div dj-root>{{ menu }}</div>"
        menu = DropdownMenu(label="Menu", items=[{"label": "A", "value": "a"}], visibility="client")

    view = _Page()
    view.mount(None)
    return str(view.menu.render())


def _ttyd() -> str:
    return _ENGINE.get_template("djust_components/ttyd_terminal.html").render(
        Context(
            {
                "ttyd_url": "ws://localhost/x",
                "rows": 30,
                "cols": 80,
                "theme_json": "{}",
                "strict_styles": strict_styles(),
            }
        )
    )


def _cls(module: str, cls_name: str, /, **kw: Any) -> Callable[[], str]:
    def render() -> str:
        import importlib

        return str(getattr(importlib.import_module(module), cls_name)(**kw).render())

    return render


def _theme_skeleton() -> str:
    return _ENGINE.from_string(
        '{% load theme_components %}{% theme_skeleton width="10rem" height="2rem" %}'
    ).render(Context({}))


def _colour_scale() -> str:
    return str(
        rh.DataTableHandler().render(
            ["rows=rows", "columns=cols", "conditional_formatting=cf"],
            {
                "rows": [{"id": 1, "n": 50}],
                "cols": [{"key": "n", "label": "N"}],
                "cf": [{"column": "n", "type": "color_scale", "colors": ["#abcdef"]}],
            },
        )
    )


_C = "djust.components.components."
_TREE = [{"id": "a", "label": "A", "expanded": True, "children": [{"id": "b", "label": "B"}]}]
_FILES = [{"name": "src", "type": "folder", "expanded": True, "children": [{"name": "a.py"}]}]
_ANSI = ["\x1b[31mred\x1b[0m \x1b[1mbold\x1b[0m"]
_USERS = [{"name": "Ann Lee"}, {"name": "Bo Diaz"}]
_MASONRY = [{"content": "a"}, {"content": "b"}]
_CARDS = [{"title": "A", "category": "x"}]
_CURSOR = [{"name": "A", "color": "#123", "x": 1, "y": 2}]
_SELECTION = [{"name": "A", "color": "#123", "start": 0, "end": 5, "text": "Hello"}]
_EVENTS = [{"date": "2026-03-25", "title": "Standup", "color": "#123"}]
_SEGMENTS = [{"value": 30, "color": "#ff0000", "label": "a"}]
_PANELS = [{"id": "p", "col": 2, "width": 2}]

_DASH_DEFAULT = [
    'style="grid-column:2/span 2;grid-row:1/span 1"',
    'style="display:grid;grid-template-columns:repeat(4,1fr);grid-auto-rows:minmax(200px,auto);gap:1rem"',
]
_DASH_STRICT = [
    ("display", "grid"),
    ("grid-template-columns", "repeat(4,1fr)"),
    ("grid-column", "2/span 2"),
    ("grid-row", "1/span 1"),
]


#: id -> (render, main's inline styles, byte for byte (default mode), the data attributes (strict
#: mode), anything else each mode must hold). One row per site and render path.
SITES: dict[
    str, tuple[Callable[[], str], list[str], list[tuple[str, str]], dict[str, list[str]]]
] = {
    "ui.Progress bootstrap": (
        _ui_progress("bootstrap5"),
        ['style="height: 12px;"', 'style="width: 40%"'],
        [("height", "12px")],
        {"both": ["dj-progress-w-40"]},
    ),
    "ui.Progress tailwind animated": (
        _ui_progress("tailwind", animated=True),
        ['style="height: 12px"', 'style="width: 40%"'],
        [("height", "12px")],
        {
            "both": ["dj-progress-w-40"],
            "default": ["<style>@keyframes shimmer {0% {background-position: -100% 0;}"],
            "strict": ['data-dj-css="shimmer"'],
        },
    ),
    "ui.Progress plain": (
        _ui_progress("plain"),
        ['style="height: 12px;"', 'style="width: 40%"'],
        [("height", "12px")],
        {"both": ["dj-progress-w-40"]},
    ),
    "progress_simple python": (
        _progress_simple("python"),
        ['style="height: 12px"', 'style="width: 40.0%"'],
        [("height", "12px")],
        {"both": ["dj-progress-w-40"]},
    ),
    "progress_simple template": (
        _progress_simple("template"),
        ['style="height: 12px"', 'style="width: 40.0%"'],
        [("height", "12px")],
        {"both": ["dj-progress-w-40"]},
    ),
    "RustProgress": (
        _progress_simple("rust"),
        ['style="height: 12px"', 'style="width: 40.0%"'],
        [("height", "12px")],
        {"both": ["dj-progress-w-40"]},
    ),
    "avatar_simple python": (
        _avatar_simple(False),
        ['style="width: 4rem; height: 4rem;"'],
        [("width", "4rem"), ("height", "4rem")],
        {},
    ),
    "RustAvatar": (
        _avatar_simple(True),
        ['style="width: 4rem; height: 4rem;"'],
        [("width", "4rem"), ("height", "4rem")],
        {},
    ),
    "navbar logo": (
        _navbar,
        ['style="width: auto; margin-right: 0.5rem;"'],
        [("width", "auto"), ("margin-right", "0.5rem")],
        {},
    ),
    "spinner lg": (
        _spinner("lg"),
        ['style="width: 3rem; height: 3rem;"'],
        [("width", "3rem"), ("height", "3rem")],
        {},
    ),
    "spinner xl": (
        _spinner("xl"),
        ['style="width: 5rem; height: 5rem;"'],
        [("width", "5rem"), ("height", "5rem")],
        {},
    ),
    "spinner dots": (
        _spinner("md", "dots", "tailwind"),
        [
            'animate-bounce" style=""',
            'style="animation-delay: 150ms;"',
            'style="animation-delay: 300ms;"',
        ],
        [("animation-delay", "150ms"), ("animation-delay", "300ms")],
        {},
    ),
    "foreign_key checkboxes": (
        _foreign_key,
        ['style="max-height: 200px; overflow-y: auto;"'],
        [("max-height", "200px"), ("overflow-y", "auto")],
        {},
    ),
    "dropdown popover": (
        _dropdown,
        ['style="position:fixed;inset:0;margin:auto;width:max-content;height:max-content"'],
        [],
        {"strict": ["dj-dropdown-menu__content--popover"]},
    ),
    "ttyd terminal": (
        _ttyd,
        ['style="width: 100%; height: 100%; min-height: calc(30 * 1.2em); background: #1e1e2e;"'],
        [("width", "100%"), ("min-height", "calc(30 * 1.2em)"), ("background-color", "#1e1e2e")],
        {},
    ),
    "virtual_list tag": (
        lambda: _tag("{% virtual_list items=i item_height=60 %}", i=[{"label": "a"}, "b"]),
        ['style="height:60px"'],
        [("height", "60px")],
        {},
    ),
    "table_of_contents tag": (
        lambda: _tag("{% table_of_contents items=i %}", i=[{"id": "s", "label": "S", "level": 2}]),
        ['style="padding-left:1.75rem"'],
        [("padding-left", "1.75rem")],
        {},
    ),
    "tree_view class": (
        _cls(_C + "tree_view", "TreeView", nodes=_TREE),
        ['style="padding-left:0.0rem"'],
        [("padding-left", "0.0rem")],
        {},
    ),
    "tree_view tag": (
        lambda: _tag("{% tree_view nodes=n %}", n=_TREE),
        ['style="padding-left:0.0rem"'],
        [("padding-left", "0.0rem")],
        {},
    ),
    "file_tree class": (
        _cls(_C + "file_tree", "FileTree", nodes=_FILES),
        ['style="padding-left:0.0rem"', 'style="padding-left:1.25rem"'],
        [("padding-left", "0.0rem"), ("padding-left", "1.25rem")],
        {},
    ),
    "file_tree tag": (
        lambda: _tag("{% file_tree nodes=n %}", n=_FILES),
        ['style="padding-left:0.0rem"', 'style="padding-left:1.25rem"'],
        [("padding-left", "0.0rem"), ("padding-left", "1.25rem")],
        {},
    ),
    "terminal class": (
        _cls(_C + "terminal", "Terminal", output=_ANSI),
        ['<span style="color:#e74c3c">', '<span style="font-weight:bold">'],
        [("color", "#e74c3c"), ("font-weight", "bold")],
        {},
    ),
    "terminal tag": (
        lambda: _tag("{% terminal output=o %}", o=_ANSI),
        ['<span style="color:#e74c3c">', '<span style="font-weight:bold">'],
        [("color", "#e74c3c"), ("font-weight", "bold")],
        {},
    ),
    "avatar_group class": (
        _cls(_C + "avatar_group", "AvatarGroup", users=_USERS),
        ['style="z-index:2"'],
        [("z-index", "2")],
        {},
    ),
    "avatar_group tag": (
        lambda: _tag("{% avatar_group users=u %}", u=_USERS),
        ['style="z-index:2"'],
        [("z-index", "2")],
        {},
    ),
    "presence_avatars class": (
        _cls(_C + "presence_avatars", "PresenceAvatars", users=_USERS),
        ['style="z-index:2"'],
        [("z-index", "2")],
        {},
    ),
    "presence_avatars tag": (
        lambda: _tag("{% presence_avatars users=u %}", u=_USERS),
        ['style="z-index:2"'],
        [("z-index", "2")],
        {},
    ),
    "expandable_text class": (
        _cls(_C + "expandable_text", "ExpandableText", text="x", max_lines=4),
        [
            'style="-webkit-line-clamp:4;display:-webkit-box;-webkit-box-orient:vertical;overflow:hidden"'
        ],
        [("-webkit-line-clamp", "4"), ("display", "-webkit-box"), ("overflow", "hidden")],
        {},
    ),
    "expandable_text tag": (
        lambda: _tag("{% expandable_text max_lines=4 %}x{% endexpandable_text %}"),
        [
            'style="-webkit-line-clamp:4;display:-webkit-box;-webkit-box-orient:vertical;overflow:hidden"'
        ],
        [("-webkit-line-clamp", "4"), ("display", "-webkit-box"), ("overflow", "hidden")],
        {},
    ),
    "progress_circle class": (
        _cls(_C + "progress_circle", "ProgressCircle", value=40, size="lg"),
        ['style="font-size:1.5rem"'],
        [("font-size", "1.5rem")],
        {},
    ),
    "progress_circle tag": (
        lambda: _tag('{% progress_circle value=40 size="lg" %}'),
        ['style="font-size:1.5rem"'],
        [("font-size", "1.5rem")],
        {},
    ),
    "masonry_grid class": (
        _cls(_C + "masonry_grid", "MasonryGrid", items=_MASONRY, columns=2, gap=8),
        ['style="--dj-masonry-columns: 2; --dj-masonry-gap: 8px"'],
        [("--dj-masonry-columns", "2"), ("--dj-masonry-gap", "8px")],
        {},
    ),
    "masonry_grid tag": (
        lambda: _tag("{% masonry_grid items=i columns=2 gap=8 %}", i=_MASONRY),
        ['style="--dj-masonry-columns: 2; --dj-masonry-gap: 8px"'],
        [("--dj-masonry-columns", "2"), ("--dj-masonry-gap", "8px")],
        {},
    ),
    "data_card_grid class": (
        _cls(_C + "data_card_grid", "DataCardGrid", items=_CARDS, columns=4),
        ['style="--dj-data-card-grid-cols:4"'],
        [("--dj-data-card-grid-cols", "4")],
        {},
    ),
    "data_card_grid tag": (
        lambda: _tag("{% data_card_grid items=i columns=4 %}", i=_CARDS),
        ['style="--dj-data-card-grid-cols:4"'],
        [("--dj-data-card-grid-cols", "4")],
        {},
    ),
    "split_pane class": (
        _cls(_C + "split_pane", "SplitPane", left="a", right="b", initial=30),
        ['style="width:30%"', 'style="flex:1"'],
        [("width", "30%"), ("flex", "1")],
        {},
    ),
    "split_pane tag": (
        lambda: _tag("{% split_pane initial=30 %}a{% pane %}b{% endsplit_pane %}"),
        ['style="width:30%"', 'style="flex:1"'],
        [("width", "30%"), ("flex", "1")],
        {},
    ),
    "dashboard_grid class": (
        _cls(_C + "dashboard_grid", "DashboardGrid", panels=_PANELS),
        _DASH_DEFAULT,
        _DASH_STRICT,
        {},
    ),
    "dashboard_grid tag": (
        lambda: _tag("{% dashboard_grid panels=p %}{% enddashboard_grid %}", p=_PANELS),
        _DASH_DEFAULT,
        _DASH_STRICT,
        {},
    ),
    "sortable_grid class": (
        _cls(_C + "sortable_grid", "SortableGrid", items=[{"id": "a", "label": "A"}], columns=2),
        ['style="grid-template-columns:repeat(2,1fr);gap:0.75rem"'],
        [("grid-template-columns", "repeat(2,1fr)"), ("gap", "0.75rem")],
        {},
    ),
    "sortable_grid tag": (
        lambda: _tag("{% sortable_grid items=i columns=2 %}", i=[{"id": "a", "label": "A"}]),
        ['style="grid-template-columns:repeat(2,1fr);gap:0.75rem"'],
        [("grid-template-columns", "repeat(2,1fr)"), ("gap", "0.75rem")],
        {},
    ),
    "resizable_panel class": (
        _cls(_C + "resizable_panel", "ResizablePanel", content="x", max_size="80%"),
        ['style="width:50%;min-width:100px;max-width:80%"'],
        [("width", "50%"), ("min-width", "100px"), ("max-width", "80%")],
        {},
    ),
    "resizable_panel tag": (
        lambda: _tag('{% resizable_panel max_size="80%" %}x{% endresizable_panel %}'),
        ['style="width:50%;min-width:100px;max-width:80%"'],
        [("width", "50%"), ("min-width", "100px"), ("max-width", "80%")],
        {},
    ),
    "scroll_area class": (
        _cls(_C + "scroll_area", "ScrollArea", content="x"),
        ['style="max-height: 400px; overflow-y: auto;"'],
        [("max-height", "400px"), ("overflow-y", "auto")],
        {},
    ),
    "scroll_area tag": (
        lambda: _tag("{% scroll_area %}x{% endscroll_area %}"),
        [
            'style="--dj-scroll-area-max-height: 400px; '
            'max-height: var(--dj-scroll-area-max-height); overflow-y: auto;"'
        ],
        [("max-height", "400px"), ("overflow-y", "auto")],
        {},
    ),
    "sticky_header class": (
        _cls(_C + "sticky_header", "StickyHeader", content="x"),
        ['style="position: sticky; top: 0; z-index: 10;"'],
        [("position", "sticky"), ("top", "0"), ("z-index", "10")],
        {},
    ),
    "sticky_header tag": (
        lambda: _tag("{% sticky_header %}x{% endsticky_header %}"),
        ['style="position: sticky; top: 0; z-index: 10;"'],
        [("position", "sticky"), ("top", "0"), ("z-index", "10")],
        {},
    ),
    "data_grid tag": (
        lambda: _tag(
            "{% data_grid columns=c rows=r %}", c=[{"key": "a", "width": "9rem"}], r=[{"id": 1}]
        ),
        ['style="width:9rem;min-width:9rem"'],
        [("width", "9rem"), ("min-width", "9rem")],
        {},
    ),
    "data_grid class": (
        _cls(
            _C + "data_grid", "DataGrid", columns=[{"key": "a", "width": "9rem"}], rows=[{"id": 1}]
        ),
        ['style="width:9rem"'],
        [("width", "9rem")],
        {},
    ),
    "data_table colour scale (native)": (
        _colour_scale,
        ['style="background-color:#abcdef"'],
        [("background-color", "#abcdef")],
        {},
    ),
    "kanban tag": (
        lambda: _tag(
            "{% kanban_board columns=c %}", c=[{"id": "t", "title": "T", "color": "#123456"}]
        ),
        ['style="border-top-color:#123456"'],
        [("border-top-color", "#123456")],
        {},
    ),
    "meter class": (
        _cls(_C + "meter", "Meter", segments=_SEGMENTS),
        ['style="background:#ff0000"'],
        [("background-color", "#ff0000")],
        {},
    ),
    "meter tag": (
        lambda: _tag("{% meter segments=s %}", s=_SEGMENTS),
        ['style="background:#ff0000"'],
        [("background-color", "#ff0000")],
        {},
    ),
    "calendar class": (
        _cls(_C + "calendar_view", "CalendarView", events=_EVENTS, month=3, year=2026),
        ['style="--dj-calendar-event-color: #123"'],
        [("--dj-calendar-event-color", "#123")],
        {},
    ),
    "calendar tag": (
        lambda: _tag("{% calendar events=e month=3 year=2026 %}", e=_EVENTS),
        ['style="--dj-calendar-event-color: #123"'],
        [("--dj-calendar-event-color", "#123")],
        {},
    ),
    "cursors class": (
        _cls(_C + "cursors_overlay", "CursorsOverlay", users=_CURSOR),
        ['style="left:1px;top:2px"', 'style="background:#123"'],
        [("left", "1px"), ("top", "2px"), ("background-color", "#123")],
        {},
    ),
    "cursors tag": (
        lambda: _tag("{% cursors users=u %}", u=_CURSOR),
        ['style="left:1px;top:2px"', 'style="background:#123"'],
        [("left", "1px"), ("top", "2px"), ("background-color", "#123")],
        {},
    ),
    "collab_selection class": (
        _cls(_C + "collab_selection", "CollabSelection", users=_SELECTION),
        ['style="--dj-collab-sel-color:#123"', 'style="background:#123"'],
        [("--dj-collab-sel-color", "#123"), ("background-color", "#123")],
        {},
    ),
    "collab_selection tag": (
        lambda: _tag("{% collab_selection users=u %}", u=_SELECTION),
        ['style="--dj-collab-sel-color:#123"', 'style="background:#123"'],
        [("--dj-collab-sel-color", "#123"), ("background-color", "#123")],
        {},
    ),
    "color_picker class": (
        _cls(_C + "color_picker", "ColorPicker", name="c", value="#123456", swatches=["#123456"]),
        ['style="background:#123456"'],
        [("background-color", "#123456")],
        {},
    ),
    "color_picker tag": (
        lambda: _tag('{% color_picker name="c" value=v swatches=s %}', v="#123456", s=["#123456"]),
        ['style="background:#123456"'],
        [("background-color", "#123456")],
        {},
    ),
    "aspect_ratio class": (
        _cls(_C + "aspect_ratio", "AspectRatio", content="x", ratio="4/3"),
        ['style="aspect-ratio: 4/3;"'],
        [("aspect-ratio", "4/3")],
        {},
    ),
    "aspect_ratio tag": (
        lambda: _tag('{% aspect_ratio ratio="4/3" %}x{% endaspect_ratio %}'),
        ['style="aspect-ratio: 4/3;"'],
        [("aspect-ratio", "4/3")],
        {},
    ),
    "responsive_image class": (
        _cls(_C + "responsive_image", "ResponsiveImage", src="/a.png", aspect_ratio="4/3"),
        ['style="aspect-ratio:4/3"'],
        [("aspect-ratio", "4/3")],
        {},
    ),
    "responsive_image tag": (
        lambda: _tag('{% responsive_image src="/a.png" aspect_ratio="4/3" %}'),
        ['style="aspect-ratio:4/3"'],
        [("aspect-ratio", "4/3")],
        {},
    ),
    "map_picker class": (
        _cls(_C + "map_picker", "MapPicker", height="300px"),
        ['style="height:300px"'],
        [("height", "300px")],
        {},
    ),
    "map_picker tag": (
        lambda: _tag('{% map_picker height="300px" %}'),
        ['style="height:300px"'],
        [("height", "300px")],
        {},
    ),
    "rich_text_editor class": (
        _cls(_C + "rich_text_editor", "RichTextEditor", height="150px"),
        ['style="min-height:150px"'],
        [("min-height", "150px")],
        {},
    ),
    "rich_text_editor tag": (
        lambda: _tag('{% rich_text_editor height="150px" %}'),
        ['style="min-height:150px"'],
        [("min-height", "150px")],
        {},
    ),
    "theme_skeleton": (
        _theme_skeleton,
        ['style="width: 10rem; height: 2rem"'],
        [("width", "10rem"), ("height", "2rem")],
        {},
    ),
    "djust_skeleton": (
        lambda: _ENGINE.from_string('{% load live_tags %}{% djust_skeleton width="5rem" %}').render(
            Context({})
        ),
        ['style="width:5rem;height:1em"'],
        [("width", "5rem"), ("height", "1em")],
        {"default": ["@keyframes djust-skeleton-shimmer"], "strict": ['data-dj-css="skeleton"']},
    ),
}


def _engine_rows() -> dict[str, Any]:
    """The same tags through the Rust engine (``render_template``)."""
    rows: dict[str, Any] = {}
    for name, src, ctx, default, attrs in (
        (
            "cursors",
            "{% cursors users=u %}",
            {"u": _CURSOR},
            ['style="left:1px;top:2px"', 'style="background:#123"'],
            [("left", "1px"), ("top", "2px"), ("background-color", "#123")],
        ),
        (
            "virtual_list",
            "{% virtual_list items=i item_height=60 %}",
            {"i": ["a"]},
            ['style="height:60px"'],
            [("height", "60px")],
        ),
        (
            "expandable_text",
            "{% expandable_text max_lines=4 %}x{% endexpandable_text %}",
            {},
            ['style="-webkit-line-clamp:4;'],
            [("-webkit-line-clamp", "4")],
        ),
        (
            "masonry_grid",
            "{% masonry_grid items=i columns=2 gap=8 %}",
            {"i": _MASONRY},
            ['style="--dj-masonry-columns: 2; --dj-masonry-gap: 8px"'],
            [("--dj-masonry-gap", "8px")],
        ),
        (
            "terminal",
            "{% terminal output=o %}",
            {"o": _ANSI},
            ['<span style="color:#e74c3c">'],
            [("color", "#e74c3c")],
        ),
        (
            "dashboard_grid",
            "{% dashboard_grid panels=p %}{% enddashboard_grid %}",
            {"p": _PANELS},
            _DASH_DEFAULT,
            _DASH_STRICT,
        ),
        (
            "responsive_image",
            '{% responsive_image src="/a.png" aspect_ratio="4/3" %}',
            {},
            ['style="aspect-ratio:4/3"'],
            [("aspect-ratio", "4/3")],
        ),
        (
            "djust_skeleton",
            '{% load live_tags %}{% djust_skeleton width="5rem" %}',
            {},
            ['style="width:5rem;height:1em"'],
            [("width", "5rem")],
        ),
    ):
        rows[f"{name} engine"] = (lambda s=src, c=ctx: _engine(s, **c), default, attrs, {})
    return rows


SITES.update(_engine_rows())


class TestEverySite:
    @pytest.mark.parametrize("site", sorted(SITES))
    def test_both_modes(self, site, mode):
        render, main_styles, attrs, extra = SITES[site]
        markup = render()
        for frag in extra.get("both", []) + extra.get(mode, []):
            assert frag in markup, (frag, markup)
        if mode == "default":
            # The site's own inline style, byte for byte, and nothing for the client.
            for style in main_styles:
                assert style in markup, (style, markup)
            assert "data-dj-style" not in markup, markup
        else:
            _assert_no_free_form_inline_style(markup)
            assert "data-dj-style" in markup or not attrs, markup
            for prop, value in attrs:
                assert _dj(prop, value) in markup, (prop, value, markup)


# ---------------------------------------------------------------------------
# The page marker
# ---------------------------------------------------------------------------


class TestThePageMarker:
    def test_the_client_config_tag_writes_it_only_in_strict_mode(self, mode):
        from djust.templatetags.live_tags import _client_config_html

        assert (STYLES_META in str(_client_config_html())) == (mode == "strict")

    @staticmethod
    def _page(template: str, rf) -> str:
        from django.contrib.sessions.backends.signed_cookies import SessionStore

        from djust import LiveView

        class V(LiveView):
            exposure_policy = "explicit"

        V.template = template
        request = rf.get("/")
        request.session = SessionStore()
        response = V.as_view()(request)
        return response.content.decode() if hasattr(response, "content") else str(response)

    @pytest.mark.django_db
    def test_a_liveview_page_carries_it_in_its_head_only_in_strict_mode(self, mode, rf):
        html = self._page(
            "<html><head><title>t</title></head><body><div dj-root>x</div></body></html>", rf
        )
        head = html.split("</head>")[0]
        assert (STYLES_META in head) == (mode == "strict"), head
        assert html.count(STYLES_META) == (1 if mode == "strict" else 0)

    @pytest.mark.django_db
    @pytest.mark.parametrize(
        "template",
        [
            "<div dj-root>x</div>",
            "<!DOCTYPE html><div dj-root>x</div>",
            "<!doctype html>\n<html><body><div dj-root>x</div></body></html>",
        ],
    )
    def test_a_page_without_a_head_carries_it_where_the_parser_puts_it_in_the_head(
        self, strict, rf, template
    ):
        """The client reads the meta from ``document.head`` only. Without a ``<head>`` it is the
        first thing after the doctype, which the HTML parser places in the implied head."""
        html = self._page(template, rf)
        rest = re.sub(r"^\s*<!doctype[^>]*>", "", html, flags=re.I)
        # Only metas before it (the tab id is written the same way): all in the implied head.
        leading = re.match(r"(?:\s*<meta [^<>]*>)*", rest).group(0)
        assert STYLES_META in leading, html[:200]
        assert html.count(STYLES_META) == 1

    def test_strict_mode_is_one_project_wide_setting(self, strict):
        """Live navigation keeps the document head (``_page_shell``), so the meta of the first
        page stands for every page: the setting is project-wide, read from one config key, and
        every full page render and ``{% djust_client_config %}`` carry the meta."""
        from djust.templatetags.live_tags import _client_config_html

        assert strict_styles() is True
        assert STYLES_META in str(_client_config_html())


# ---------------------------------------------------------------------------
# {% djust_skeleton %}
# ---------------------------------------------------------------------------


def _skeleton(source: str) -> str:
    return _ENGINE.from_string("{% load live_tags %}" + source).render(Context({}))


class TestDjustSkeleton:
    def test_default_mode_is_unchanged(self):
        out = _skeleton('{% djust_skeleton %}{% djust_skeleton shape="circle" %}')
        assert out.count("<style>") == 1
        assert 'style="width:40px;height:40px;border-radius:50%"' in out
        assert "data-dj-" not in out

    def test_strict_mode_asks_for_the_rules_once_per_render(self, strict):
        out = _skeleton('{% djust_skeleton %}{% djust_skeleton shape="circle" %}')
        _assert_no_free_form_inline_style(out)
        assert out.count('data-dj-css="skeleton"') == 1
        assert _dj("width", "40px") in out and _dj("height", "40px") in out
        assert _skeleton("{% djust_skeleton %}").count('data-dj-css="skeleton"') == 1

    def test_the_rules_the_client_adds_are_the_ones_the_block_has(self):
        js = (_COMPONENTS.parent / "static" / "djust" / "src" / "54-dj-style.js").read_text()
        for rule in (
            "@keyframes djust-skeleton-shimmer",
            ":where(.djust-skeleton-circle){border-radius:50%}",
            "prefers-reduced-motion",
        ):
            assert rule in js


# ---------------------------------------------------------------------------
# Both engines write the same attributes
# ---------------------------------------------------------------------------


class TestBothEnginesWriteTheSameAttributes:
    def test_data_table_column_width(self, mode):
        cols = [{"key": "a", "label": "A", "width": "12rem"}]
        rows = [{"id": 1, "a": "x"}]
        django = _tag("{% data_table rows=r columns=c %}", c=cols, r=rows)
        native = str(rh.DataTableHandler().render(["rows=r", "columns=c"], {"c": cols, "r": rows}))
        attr = (
            r'<th[^>]*( data-dj-style data-dj-style-width="[^"]*")'
            if mode == "strict"
            else r'<th[^>]*( style="width:[^"]*")'
        )
        assert re.findall(attr, django) == re.findall(attr, native) != []


# ---------------------------------------------------------------------------
# The stylesheet reads the custom properties only in longhands
# ---------------------------------------------------------------------------


def test_no_custom_property_a_component_writes_feeds_a_shorthand_that_takes_url():
    """A ``--dj-*`` value read by ``background:`` (a shorthand that accepts ``url()``) would load a
    URL a hostile value names; the rules read them as ``background-color`` (#3979)."""
    css = (_COMPONENTS / "static" / "djust_components" / "components.css").read_text()
    custom = [p for p in STYLE_PROPERTY_NAMES if p.startswith("--")]
    shorthand = re.compile(
        r"(?<![-\w])(background|border-image|list-style|mask|content|cursor)\s*:[^;}]*var\(\s*(%s)"
        % "|".join(map(re.escape, custom))
    )
    assert not shorthand.findall(css), shorthand.findall(css)
    assert "background-color: var(--dj-calendar-event-color" in css
