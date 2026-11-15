"""Free-form style values reach the page without an inline ``style`` (#3979).

A ``style-src`` without ``'unsafe-inline'`` refuses every ``style="..."`` attribute and every
inline ``<style>`` block. Percent fills and the gauge box already draw with classes (#3969,
#3979, ``test_progress_widths_csp_3979.py``). What is left is a value no class can stand for: a
column width, a user's colour, an item height. Each such site now writes
``data-dj-style-<property>="<value>"`` through ONE helper
(:func:`djust.components.utils.style_attrs`, its check :func:`~djust.components.utils.style_value`),
and the client module ``54-dj-style.js`` applies it with ``element.style.setProperty`` (the CSSOM,
which a strict CSP allows; ``tests/js/dj_style.test.js``).

* ``TestTheHelper``: the property table, each kind's check, hostile values dropped (never
  written) with one warning per property, a marked-safe value judged as plain text, an unknown
  property refused.
* ``TestTheClientAndServerAgree``: the client applies exactly the server's properties.
* ``TestEverySite``: one row per converted site, on every render path it has (component class,
  template tag on Django's engine, the same tag on the Rust engine, the native handler, the Rust
  component). Each asserts that no free-form value is an inline ``style`` and no ``<style>``
  block is written, and that the value is the ``data-dj-style-*`` attribute instead. The
  colours, lengths and ratios with a hostile caller value are in ``test_component_css_values.py``.
* ``TestDjustSkeleton``: ``{% djust_skeleton %}`` writes no ``<style>`` block; it asks for the
  shimmer rules with ``data-dj-css="skeleton"``, once per render, on both engines.
* ``TestBothEnginesWriteTheSameAttributes``: ``{% data_table %}`` (the Django tag and its native
  handler) write the same width attribute.
"""

from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Any, Callable

import pytest
from django.template import Context, Engine
from django.utils.safestring import mark_safe

from djust.components import rust_handlers as rh
from djust.components.utils import STYLE_PROPERTIES, style_attrs, style_value

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


def _tag(source: str, **context: Any) -> str:
    return _ENGINE.from_string("{% load djust_components %}" + source).render(Context(context))


_RUST_READY: list[bool] = []


def _engine(source: str, **context: Any) -> str:
    from djust._rust import render_template

    if not _RUST_READY:
        rh.register_with_rust_engine()
        _RUST_READY.append(True)
    return render_template(source, context)


#: Constant inline styles a converted component still writes elsewhere in its markup (a hidden
#: trigger, a pointer cursor, the Bootstrap navbar's forced flex). No caller's value reaches
#: them; they are not free-form values and are left for a follow-up (#3979).
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
    def test_writes_data_attributes_in_order(self):
        assert style_attrs(width="240px", min_width="10rem") == (
            ' data-dj-style-width="240px" data-dj-style-min-width="10rem"'
        )

    def test_a_mapping_names_hyphenated_and_custom_properties(self):
        out = style_attrs({"--dj-masonry-columns": 3, "-webkit-line-clamp": 2})
        assert out == ' data-dj-style---dj-masonry-columns="3" data-dj-style--webkit-line-clamp="2"'

    @pytest.mark.parametrize(
        "prop,value",
        [
            ("width", "calc(100% - 2rem)"),
            ("height", "50%"),
            ("background-color", "hsl(var(--primary))"),
            ("color", "#abc"),
            ("z-index", "-1"),
            ("aspect-ratio", "16 / 9"),
            ("animation-delay", "150ms"),
            ("grid-column", "2/span 3"),
            ("grid-template-columns", "repeat(4,1fr)"),
            ("grid-auto-rows", "minmax(calc(10vh + 1rem),auto)"),
            ("position", "sticky"),
            ("display", "-webkit-box"),
            ("overflow-y", "auto"),
            ("font-weight", "bold"),
        ],
    )
    def test_each_kind_accepts_its_values(self, prop, value):
        assert style_value(prop, value) == value

    @pytest.mark.parametrize("prop", sorted(STYLE_PROPERTIES))
    @pytest.mark.parametrize("value", HOSTILE)
    def test_a_hostile_value_is_never_written(self, prop, value):
        assert style_value(prop, value) == ""
        assert style_attrs({prop: value}) == ""
        assert style_attrs({prop: mark_safe(value)}) == ""

    @pytest.mark.parametrize(
        "prop,value",
        [
            ("display", "none"),  # only the two layouts the sites use
            ("position", "absolute;top:0"),
            ("z-index", "1.5"),
            ("color", "notacolour"),
            ("grid-template-columns", "repeat(a,1fr)"),
            ("grid-column", "1/span x"),
            ("animation-delay", "150"),
        ],
    )
    def test_a_value_of_the_wrong_kind_is_dropped(self, prop, value):
        assert style_attrs({prop: value}) == ""

    def test_nothing_for_an_absent_value(self):
        assert style_attrs(width=None, height="", color="  ", z_index=False) == ""

    def test_an_unknown_property_is_a_programming_error(self):
        with pytest.raises(ValueError):
            style_attrs(cursor="pointer")
        with pytest.raises(ValueError):
            style_value("behavior", "url(x.htc)")

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

    def test_the_output_is_escaped_for_a_quoted_attribute(self):
        out = style_attrs(width="1px")
        assert '"' not in out.split("=", 1)[1][1:-1]


class TestTheClientAndServerAgree:
    def test_the_client_applies_exactly_the_server_properties(self):
        js = (_COMPONENTS.parent / "static" / "djust" / "src" / "54-dj-style.js").read_text()
        listed = re.search(r"const _DJ_STYLE_PROPS = \((.*?)\)\.split", js, re.S).group(1)
        props = set("".join(re.findall(r"'([^']*)'", listed)).split())
        assert props == set(STYLE_PROPERTIES)


# ---------------------------------------------------------------------------
# Every converted site
# ---------------------------------------------------------------------------


def _ui_progress(framework: str, **kw: Any) -> Callable[[], str]:
    def render() -> str:
        from djust.components.ui.progress import ProgressComponent
        from djust.config import config

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
        from djust.config import config

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
        Context({"ttyd_url": "ws://localhost/x", "rows": 30, "cols": 80, "theme_json": "{}"})
    )


_TREE = [{"id": "a", "label": "A", "expanded": True, "children": [{"id": "b", "label": "B"}]}]
_FILES = [{"name": "src", "type": "folder", "expanded": True, "children": [{"name": "a.py"}]}]
_ANSI = ["\x1b[31mred\x1b[0m \x1b[1mbold\x1b[0m"]
_USERS = [{"name": "Ann Lee"}, {"name": "Bo Diaz"}]
_MASONRY = [{"content": "a"}, {"content": "b"}]
_CARDS = [{"title": "A", "category": "x"}]


def _cls(module: str, name: str, **kw: Any) -> Callable[[], str]:
    def render() -> str:
        import importlib

        return str(getattr(importlib.import_module(module), name)(**kw).render())

    return render


_C = "djust.components.components."

#: id -> (render, the attributes it must carry). One row per site and render path.
SITES: dict[str, tuple[Callable[[], str], list[str]]] = {
    "ui.Progress bootstrap": (
        _ui_progress("bootstrap5"),
        [_dj("height", "12px"), _dj("width", "40%")],
    ),
    "ui.Progress tailwind": (
        _ui_progress("tailwind", animated=True),
        [_dj("height", "12px"), _dj("width", "40%"), 'data-dj-css="shimmer"'],
    ),
    "ui.Progress plain": (_ui_progress("plain"), [_dj("height", "12px"), _dj("width", "40%")]),
    "progress_simple python": (
        _progress_simple("python"),
        [_dj("height", "12px"), _dj("width", "40.0%")],
    ),
    "progress_simple template": (
        _progress_simple("template"),
        [_dj("height", "12px"), _dj("width", "40.0%")],
    ),
    "RustProgress": (_progress_simple("rust"), [_dj("height", "12px"), _dj("width", "40.0%")]),
    "avatar_simple python": (_avatar_simple(False), [_dj("width", "4rem"), _dj("height", "4rem")]),
    "RustAvatar": (_avatar_simple(True), [_dj("width", "4rem"), _dj("height", "4rem")]),
    "navbar logo": (_navbar, [_dj("width", "auto"), _dj("margin-right", "0.5rem")]),
    "spinner lg": (_spinner("lg"), [_dj("width", "3rem"), _dj("height", "3rem")]),
    "spinner xl": (_spinner("xl"), [_dj("width", "5rem"), _dj("height", "5rem")]),
    "spinner dots": (
        _spinner("md", "dots", "tailwind"),
        [_dj("animation-delay", "150ms"), _dj("animation-delay", "300ms")],
    ),
    "foreign_key checkboxes": (
        _foreign_key,
        [_dj("max-height", "200px"), _dj("overflow-y", "auto")],
    ),
    "dropdown popover": (
        _dropdown,
        [_dj("position", "fixed"), _dj("inset", "0"), _dj("margin", "auto")],
    ),
    "ttyd terminal": (
        _ttyd,
        [_dj("min-height", "calc(30 * 1.2em)"), _dj("background-color", "#1e1e2e")],
    ),
    "virtual_list tag": (
        lambda: _tag("{% virtual_list items=i item_height=60 %}", i=[{"label": "a"}, "b"]),
        [_dj("height", "60px")],
    ),
    "table_of_contents tag": (
        lambda: _tag("{% table_of_contents items=i %}", i=[{"id": "s", "label": "S", "level": 2}]),
        [_dj("padding-left", "1.75rem")],
    ),
    "tree_view class": (
        _cls(_C + "tree_view", "TreeView", nodes=_TREE),
        [_dj("padding-left", "0.0rem")],
    ),
    "tree_view tag": (
        lambda: _tag("{% tree_view nodes=n %}", n=_TREE),
        [_dj("padding-left", "0.0rem")],
    ),
    "file_tree class": (
        _cls(_C + "file_tree", "FileTree", nodes=_FILES),
        [_dj("padding-left", "0.0rem"), _dj("padding-left", "1.25rem")],
    ),
    "file_tree tag": (
        lambda: _tag("{% file_tree nodes=n %}", n=_FILES),
        [_dj("padding-left", "0.0rem"), _dj("padding-left", "1.25rem")],
    ),
    "terminal class": (
        _cls(_C + "terminal", "Terminal", output=_ANSI),
        [_dj("color", "#e74c3c"), _dj("font-weight", "bold")],
    ),
    "terminal tag": (
        lambda: _tag("{% terminal output=o %}", o=_ANSI),
        [_dj("color", "#e74c3c"), _dj("font-weight", "bold")],
    ),
    "avatar_group class": (
        _cls(_C + "avatar_group", "AvatarGroup", users=_USERS),
        [_dj("z-index", "2")],
    ),
    "avatar_group tag": (
        lambda: _tag("{% avatar_group users=u %}", u=_USERS),
        [_dj("z-index", "2")],
    ),
    "presence_avatars class": (
        _cls(_C + "presence_avatars", "PresenceAvatars", users=_USERS),
        [_dj("z-index", "2")],
    ),
    "presence_avatars tag": (
        lambda: _tag("{% presence_avatars users=u %}", u=_USERS),
        [_dj("z-index", "2")],
    ),
    "expandable_text class": (
        _cls(_C + "expandable_text", "ExpandableText", text="x", max_lines=4),
        [_dj("-webkit-line-clamp", "4"), _dj("display", "-webkit-box"), _dj("overflow", "hidden")],
    ),
    "expandable_text tag": (
        lambda: _tag("{% expandable_text max_lines=4 %}x{% endexpandable_text %}"),
        [_dj("-webkit-line-clamp", "4"), _dj("display", "-webkit-box"), _dj("overflow", "hidden")],
    ),
    "progress_circle class": (
        _cls(_C + "progress_circle", "ProgressCircle", value=40, size="lg"),
        [_dj("font-size", "1.5rem")],
    ),
    "progress_circle tag": (
        lambda: _tag('{% progress_circle value=40 size="lg" %}'),
        [_dj("font-size", "1.5rem")],
    ),
    "masonry_grid class": (
        _cls(_C + "masonry_grid", "MasonryGrid", items=_MASONRY, columns=2, gap=8),
        [_dj("--dj-masonry-columns", "2"), _dj("--dj-masonry-gap", "8px")],
    ),
    "masonry_grid tag": (
        lambda: _tag("{% masonry_grid items=i columns=2 gap=8 %}", i=_MASONRY),
        [_dj("--dj-masonry-columns", "2"), _dj("--dj-masonry-gap", "8px")],
    ),
    "data_card_grid class": (
        _cls(_C + "data_card_grid", "DataCardGrid", items=_CARDS, columns=4),
        [_dj("--dj-data-card-grid-cols", "4")],
    ),
    "data_card_grid tag": (
        lambda: _tag("{% data_card_grid items=i columns=4 %}", i=_CARDS),
        [_dj("--dj-data-card-grid-cols", "4")],
    ),
    "split_pane class": (
        _cls(_C + "split_pane", "SplitPane", left="a", right="b", initial=30),
        [_dj("width", "30%"), _dj("flex", "1")],
    ),
    "split_pane tag": (
        lambda: _tag("{% split_pane initial=30 %}a{% pane %}b{% endsplit_pane %}"),
        [_dj("width", "30%"), _dj("flex", "1")],
    ),
    "dashboard_grid class": (
        _cls(_C + "dashboard_grid", "DashboardGrid", panels=[{"id": "p", "col": 2, "width": 2}]),
        [
            _dj("display", "grid"),
            _dj("grid-template-columns", "repeat(4,1fr)"),
            _dj("grid-column", "2/span 2"),
            _dj("grid-row", "1/span 1"),
        ],
    ),
    "dashboard_grid tag": (
        lambda: _tag(
            "{% dashboard_grid panels=p %}{% enddashboard_grid %}",
            p=[{"id": "p", "col": 2, "width": 2}],
        ),
        [
            _dj("display", "grid"),
            _dj("grid-template-columns", "repeat(4,1fr)"),
            _dj("grid-column", "2/span 2"),
            _dj("grid-row", "1/span 1"),
        ],
    ),
    "sortable_grid class": (
        _cls(_C + "sortable_grid", "SortableGrid", items=[{"id": "a", "label": "A"}], columns=2),
        [_dj("grid-template-columns", "repeat(2,1fr)")],
    ),
    "sortable_grid tag": (
        lambda: _tag("{% sortable_grid items=i columns=2 %}", i=[{"id": "a", "label": "A"}]),
        [_dj("grid-template-columns", "repeat(2,1fr)")],
    ),
    "resizable_panel class": (
        _cls(_C + "resizable_panel", "ResizablePanel", content="x", max_size="80%"),
        [_dj("width", "50%"), _dj("min-width", "100px"), _dj("max-width", "80%")],
    ),
    "resizable_panel tag": (
        lambda: _tag('{% resizable_panel max_size="80%" %}x{% endresizable_panel %}'),
        [_dj("width", "50%"), _dj("min-width", "100px"), _dj("max-width", "80%")],
    ),
    "scroll_area class": (
        _cls(_C + "scroll_area", "ScrollArea", content="x"),
        [_dj("max-height", "400px"), _dj("overflow-y", "auto")],
    ),
    "scroll_area tag": (
        lambda: _tag("{% scroll_area %}x{% endscroll_area %}"),
        [_dj("max-height", "400px"), _dj("overflow-y", "auto")],
    ),
    "sticky_header class": (
        _cls(_C + "sticky_header", "StickyHeader", content="x"),
        [_dj("position", "sticky"), _dj("top", "0"), _dj("z-index", "10")],
    ),
    "sticky_header tag": (
        lambda: _tag("{% sticky_header %}x{% endsticky_header %}"),
        [_dj("position", "sticky"), _dj("top", "0"), _dj("z-index", "10")],
    ),
    "data_grid tag": (
        lambda: _tag(
            "{% data_grid columns=c rows=r %}", c=[{"key": "a", "width": "9rem"}], r=[{"id": 1}]
        ),
        [_dj("width", "9rem"), _dj("min-width", "9rem")],
    ),
    "data_grid class": (
        _cls(
            _C + "data_grid", "DataGrid", columns=[{"key": "a", "width": "9rem"}], rows=[{"id": 1}]
        ),
        [_dj("width", "9rem")],
    ),
    "kanban tag": (
        lambda: _tag(
            "{% kanban_board columns=c %}", c=[{"id": "t", "title": "T", "color": "#123456"}]
        ),
        [_dj("border-top-color", "#123456")],
    ),
    "meter class": (
        _cls(_C + "meter", "Meter", segments=[{"value": 30, "color": "#ff0000", "label": "a"}]),
        [_dj("background-color", "#ff0000")],
    ),
    "meter tag": (
        lambda: _tag("{% meter segments=s %}", s=[{"value": 30, "color": "#ff0000", "label": "a"}]),
        [_dj("background-color", "#ff0000")],
    ),
    "theme_skeleton": (
        lambda: _ENGINE.from_string(
            '{% load theme_components %}{% theme_skeleton width="10rem" height="2rem" %}'
        ).render(Context({})),
        [_dj("width", "10rem"), _dj("height", "2rem")],
    ),
}


def _engine_rows() -> dict[str, tuple[Callable[[], str], list[str]]]:
    """The same tags through the Rust engine (``render_template``)."""
    rows: dict[str, tuple[Callable[[], str], list[str]]] = {}
    for name, src, ctx, frags in (
        (
            "cursors",
            "{% cursors users=u %}",
            {"u": [{"name": "A", "color": "#123", "x": 1, "y": 2}]},
            [_dj("left", "1px"), _dj("top", "2px"), _dj("background-color", "#123")],
        ),
        (
            "virtual_list",
            "{% virtual_list items=i item_height=60 %}",
            {"i": ["a"]},
            [_dj("height", "60px")],
        ),
        (
            "expandable_text",
            "{% expandable_text max_lines=4 %}x{% endexpandable_text %}",
            {},
            [_dj("-webkit-line-clamp", "4")],
        ),
        (
            "masonry_grid",
            "{% masonry_grid items=i columns=2 gap=8 %}",
            {"i": _MASONRY},
            [_dj("--dj-masonry-gap", "8px")],
        ),
        ("terminal", "{% terminal output=o %}", {"o": _ANSI}, [_dj("color", "#e74c3c")]),
        (
            "djust_skeleton",
            '{% load live_tags %}{% djust_skeleton width="5rem" %}',
            {},
            [_dj("width", "5rem"), 'data-dj-css="skeleton"'],
        ),
    ):
        rows[f"{name} engine"] = (lambda s=src, c=ctx: _engine(s, **c), frags)
    return rows


SITES.update(_engine_rows())


class TestEverySite:
    @pytest.mark.parametrize("site", sorted(SITES))
    def test_the_value_is_a_data_attribute_not_an_inline_style(self, site):
        render, frags = SITES[site]
        markup = render()
        _assert_no_free_form_inline_style(markup)
        for frag in frags:
            assert frag in markup, (frag, markup)


# ---------------------------------------------------------------------------
# {% djust_skeleton %}
# ---------------------------------------------------------------------------


def _skeleton(source: str) -> str:
    return _ENGINE.from_string("{% load live_tags %}" + source).render(Context({}))


class TestDjustSkeleton:
    def test_no_style_block_and_the_rules_are_asked_for_once_per_render(self):
        out = _skeleton('{% djust_skeleton %}{% djust_skeleton shape="circle" %}')
        _assert_no_free_form_inline_style(out)
        assert out.count('data-dj-css="skeleton"') == 1
        assert _dj("width", "40px") in out and _dj("height", "40px") in out

    def test_each_render_asks_again(self):
        assert _skeleton("{% djust_skeleton %}").count('data-dj-css="skeleton"') == 1
        assert _skeleton("{% djust_skeleton %}").count('data-dj-css="skeleton"') == 1

    def test_the_rules_the_client_adds_are_the_ones_the_block_had(self):
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
    def test_data_table_column_width(self):
        cols = [{"key": "a", "label": "A", "width": "12rem"}]
        rows = [{"id": 1, "a": "x"}]
        django = _tag("{% data_table rows=r columns=c %}", c=cols, r=rows)
        native = str(rh.DataTableHandler().render(["rows=r", "columns=c"], {"c": cols, "r": rows}))
        for markup in (django, native):
            _assert_no_free_form_inline_style(markup)
        width = re.compile(r'<th[^>]*data-dj-style-width="([^"]*)"')
        assert width.findall(django) == width.findall(native) == ["12rem"]
