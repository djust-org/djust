"""Colours, lengths and ratios that components write into ``style`` attributes.

A component that takes a CSS value from its caller (a segment colour in
``Meter``, a column width, a gap) writes it into an inline ``style``
attribute. HTML escaping keeps the value inside the attribute but not inside
its declaration, so each value is checked against the shapes the component
documents (``djust.components.utils.safe_css_color`` / ``safe_css_length`` /
``safe_css_ratio`` / ``safe_css_number``) and anything else is replaced by the
component's default. A value that passes is written exactly as given.

Every render path is covered: the component class, the Django template tag,
and the Rust template engine (which renders the tag's own node, one render
path per tag).
"""

from __future__ import annotations

import html as _html
import re
from pathlib import Path
from typing import Any, Callable

import pytest
from django.template import Context, Engine

from djust.components import rust_handlers as rh
from djust.components.utils import (
    safe_css_color,
    safe_css_length,
    safe_css_number,
    safe_css_ratio,
)

VALID_COLORS = [
    "#3b82f6",
    "#FFF",
    "#fff8",
    "#11223344",
    "red",
    "RebeccaPurple",
    "transparent",
    "currentColor",
    "rgb(59, 130, 246)",
    "rgba(59,130,246,.5)",
    "rgb(59 130 246 / 50%)",
    "hsl(210, 50%, 40%)",
    "hsla(210deg 50% 40% / 0.5)",
    "hsl(var(--primary))",
    "var(--accent)",
]

REFUSED_COLORS = [
    "red;position:fixed;inset:0;z-index:2147483647",
    "url(https://example.com/a.png)",
    "red url(https://example.com/a.png)",
    "expression(alert(1))",
    "red/**/",
    "/* x */red",
    "re\\64",
    "#fff\\;",
    "'red'",
    '"red"',
    "red;",
    "rgb(1,2,3);color:red",
    "rgb(1,2,3) ;color:red",
    "var(--x, red)",
    "red !important",
    "#12345",
    "notacolour",
    "javascript:red",
]

VALID_LENGTHS = [
    "400px",
    "50%",
    "2.5rem",
    "0",
    "auto",
    "none",
    "100dvh",
    "1rem 2rem",
    "calc(100vh - 4rem)",
    "min(50%, 30rem)",
    "clamp(1rem, 2vw, 3rem)",
    "var(--panel-height)",
    "calc(var(--h) * 2)",
]

REFUSED_LENGTHS = [
    "10px;position:fixed",
    "10px; background:url(https://example.com/a.png)",
    "url(https://example.com/a.png)",
    "expression(alert(1))",
    "10px /*",
    "10px/**/",
    "10px\\;",
    "'10px'",
    '"10px"',
    "calc(1px",
    "calc(1px))",
    "attr(data-x)",
    "env(safe-area-inset-top)",
    "10pxx",
    "red",
]

VALID_RATIOS = ["16/9", "16 / 9", "1.5", "auto", "auto 4/3"]
REFUSED_RATIOS = ["16/9;position:fixed", "url(x)", "16/9/**/", "'16/9'", "16\\/9", "expression(1)"]


_ENGINE = Engine(
    dirs=[str(Path(rh.__file__).resolve().parent / "templates")],
    libraries={
        "djust_components": "djust.components.templatetags.djust_components",
        "theme_components": "djust.theming.templatetags.theme_components",
    },
)


def _tag(source: str, **context: Any) -> str:
    return _ENGINE.from_string("{% load djust_components %}" + source).render(Context(context))


def _theme_tag(source: str, **context: Any) -> str:
    return _ENGINE.from_string("{% load theme_components %}" + source).render(Context(context))


_ENGINE_READY: list[bool] = []


def _engine(source: str, **context: Any) -> str:
    from djust._rust import render_template

    if not _ENGINE_READY:
        # The components app registers these at startup; the test settings
        # may not install it.
        rh.register_with_rust_engine()
        _ENGINE_READY.append(True)
    return render_template(source, context)


def _style_attrs(markup: str) -> list[str]:
    return re.findall(r'style="([^"]*)"', markup)


def _assert_absent(markup: str, value: str) -> None:
    """``value`` reaches no style attribute (a data attribute may carry it)."""
    for style in _style_attrs(markup):
        assert value not in style and _html.escape(value) not in style
        assert ";position" not in style
        assert "url(" not in style and "expression(" not in style and "/*" not in style
        assert "\\" not in style


# ---------------------------------------------------------------------------
# The helpers
# ---------------------------------------------------------------------------


class TestHelpers:
    @pytest.mark.parametrize("value", VALID_COLORS + [" #fff ", "Transparent"])
    def test_colour_accepted_unchanged(self, value):
        assert safe_css_color(value, "DEFAULT") == value

    @pytest.mark.parametrize("value", REFUSED_COLORS + ["", "   ", "#" + "f" * 300, None])
    def test_colour_refused(self, value):
        assert safe_css_color(value, "DEFAULT") == "DEFAULT"

    def test_colour_non_string(self):
        assert safe_css_color(12, "D") == "D"
        assert safe_css_color(["red"], "D") == "D"

    @pytest.mark.parametrize("value", VALID_LENGTHS)
    def test_length_accepted_unchanged(self, value):
        assert safe_css_length(value, "DEFAULT") == value

    @pytest.mark.parametrize("value", REFUSED_LENGTHS + ["", None, "1px " * 100])
    def test_length_refused(self, value):
        assert safe_css_length(value, "DEFAULT") == "DEFAULT"

    def test_length_number(self):
        assert safe_css_length(120, "D") == "120"

    @pytest.mark.parametrize("value", VALID_RATIOS)
    def test_ratio_accepted_unchanged(self, value):
        assert safe_css_ratio(value, "D") == value

    @pytest.mark.parametrize("value", REFUSED_RATIOS + ["", None])
    def test_ratio_refused(self, value):
        assert safe_css_ratio(value, "D") == "D"

    def test_number(self):
        assert safe_css_number(50, "D") == "50"
        assert safe_css_number("33.5", "D") == "33.5"
        assert safe_css_number("50;x:y", "D") == "D"
        assert safe_css_number(True, "D") == "D"
        assert safe_css_number("10", "D", integer=True) == "10"
        assert safe_css_number("auto", "D", integer=True) == "auto"
        assert safe_css_number("1.5", "D", integer=True) == "D"
        assert safe_css_number("10;x:y", "D", integer=True) == "D"


# ---------------------------------------------------------------------------
# Other components that take a colour
# ---------------------------------------------------------------------------


def _meter_segments(c: Any) -> list[dict[str, Any]]:
    return [{"value": 40, "color": c, "label": "Used"}]


def _meter_paths() -> dict[str, Callable[[Any], str]]:
    from djust.components.components.meter import Meter

    return {
        "class": lambda c: Meter(segments=_meter_segments(c)).render(),
        "tag": lambda c: _tag("{% meter segments=segs %}", segs=_meter_segments(c)),
        "engine": lambda c: _engine("{% meter segments=segs %}", segs=_meter_segments(c)),
    }


def _color_picker_paths() -> dict[str, Callable[[Any], str]]:
    from djust.components.components.color_picker import ColorPicker

    src = '{% color_picker name="c" value=v swatches=sw %}'
    return {
        "class": lambda c: ColorPicker(name="c", value=c, swatches=[c]).render(),
        "tag": lambda c: _tag(src, v=c, sw=[c]),
    }


def _kanban_cols(c: Any) -> list[dict[str, Any]]:
    return [{"id": "todo", "title": "To do", "color": c, "cards": []}]


def _kanban_paths() -> dict[str, Callable[[Any], str]]:
    return {
        "tag": lambda c: _tag("{% kanban_board columns=cols %}", cols=_kanban_cols(c)),
    }


COLOUR_CASES = [
    # name, paths, fragments for a valid colour, fragment for a refused one
    ("meter", _meter_paths, [';background:{v}"', 'style="background:{v}"'], None),
    (
        "color_picker",
        _color_picker_paths,
        ['style="background:{v}"'],
        'style="background:transparent"',
    ),
    ("kanban", _kanban_paths, ['style="border-top-color:{v}"'], 'style="border-top-color:#6366F1"'),
]


def _expand(cases):
    for name, factory, frags, default in cases:
        for path in factory():
            yield pytest.param(factory, path, frags, default, id=f"{name}-{path}")


class TestOtherColours:
    @pytest.mark.parametrize("factory,path,frags,default", list(_expand(COLOUR_CASES)))
    @pytest.mark.parametrize("color", VALID_COLORS)
    def test_valid_colour_written_as_given(self, factory, path, frags, default, color):
        out = factory()[path](color)
        for frag in frags:
            assert frag.format(v=color) in out, out

    @pytest.mark.parametrize("factory,path,frags,default", list(_expand(COLOUR_CASES)))
    @pytest.mark.parametrize("color", REFUSED_COLORS)
    def test_other_values_become_the_default(self, factory, path, frags, default, color):
        out = factory()[path](color)
        if default is None:
            assert "background:" not in out, out
        else:
            assert default in out, out
        _assert_absent(out, color)


class TestDataTableTwin:
    """``data_table`` keeps its native twin, whose ``conditional_formatting`` is native-only (#3869)."""

    def test_data_table_colour_scale_single_stop(self):
        def render(colors):
            return str(
                rh.DataTableHandler().render(
                    ["rows=rows", "columns=cols", "conditional_formatting=cf"],
                    {
                        "rows": [{"id": 1, "n": 50}],
                        "cols": [{"key": "n", "label": "N"}],
                        "cf": [{"column": "n", "type": "color_scale", "colors": colors}],
                    },
                )
            )

        assert 'style="background-color:#abcdef"' in render(["#abcdef"])
        out = render(["red;position:fixed"])
        assert "background-color" not in out
        _assert_absent(out, "red;position:fixed")
        assert 'style="background-color:#7f7f00"' in render(["#ff0000", "#00ff00"])


# ---------------------------------------------------------------------------
# Lengths, ratios and numbers
# ---------------------------------------------------------------------------


def _rte_paths() -> dict[str, Callable[[Any], str]]:
    from djust.components.components.rich_text_editor import RichTextEditor

    return {
        "class": lambda v: RichTextEditor(height=v).render(),
        "tag": lambda v: _tag("{% rich_text_editor height=h %}", h=v),
    }


def _scroll_paths() -> dict[str, Callable[[Any], str]]:
    from djust.components.components.scroll_area import ScrollArea

    return {
        "class": lambda v: ScrollArea(content="x", max_height=v).render(),
        "tag": lambda v: _tag("{% scroll_area max_height=h %}x{% endscroll_area %}", h=v),
    }


def _sticky_paths() -> dict[str, Callable[[Any], str]]:
    from djust.components.components.sticky_header import StickyHeader

    return {
        "class": lambda v: StickyHeader(content="x", offset=v).render(),
        "tag": lambda v: _tag("{% sticky_header offset=o %}x{% endsticky_header %}", o=v),
    }


def _grid_columns(v: Any) -> list[dict[str, Any]]:
    return [{"key": "a", "label": "A", "width": v}]


def _data_grid_paths() -> dict[str, Callable[[Any], str]]:
    from djust.components.components.data_grid import DataGrid

    rows = [{"id": 1, "a": "x"}]
    return {
        "class": lambda v: DataGrid(columns=_grid_columns(v), rows=rows).render(),
        "tag": lambda v: _tag("{% data_grid columns=c rows=r %}", c=_grid_columns(v), r=rows),
    }


def _data_table_paths() -> dict[str, Callable[[Any], str]]:
    rows = [{"id": 1, "a": "x"}]
    return {
        "tag": lambda v: _tag("{% data_table rows=r columns=c %}", c=_grid_columns(v), r=rows),
        # ``data_table`` keeps its native twin (one render path per tag where it costs nothing).
        "handler": lambda v: str(
            rh.DataTableHandler().render(
                ["rows=r", "columns=c"], {"c": _grid_columns(v), "r": rows}
            )
        ),
    }


def _resizable_paths() -> dict[str, Callable[[Any], str]]:
    from djust.components.components.resizable_panel import ResizablePanel

    src = "{% resizable_panel min_size=v %}x{% endresizable_panel %}"
    return {
        "class": lambda v: ResizablePanel(content="x", min_size=v).render(),
        "tag": lambda v: _tag(src, v=v),
        "engine": lambda v: _engine(src, v=v),
    }


def _sortable_paths() -> dict[str, Callable[[Any], str]]:
    from djust.components.components.sortable_grid import SortableGrid

    items = [{"id": "a", "label": "A"}]
    return {
        "class": lambda v: SortableGrid(items=items, gap=v).render(),
        "tag": lambda v: _tag("{% sortable_grid items=i gap=g %}", i=items, g=v),
    }


def _dashboard_paths() -> dict[str, Callable[[Any], str]]:
    from djust.components.components.dashboard_grid import DashboardGrid

    src = "{% dashboard_grid panels=p row_height=v %}{% enddashboard_grid %}"
    return {
        "class": lambda v: DashboardGrid(panels=[], row_height=v).render(),
        "tag": lambda v: _tag(src, p=[], v=v),
        "engine": lambda v: _engine(src, p=[], v=v),
    }


def _progress_paths() -> dict[str, Callable[[Any], str]]:
    from djust.components.ui.progress import ProgressComponent
    from djust.components.ui.progress_simple import Progress

    return {
        "ui-progress": lambda v: ProgressComponent(value=10, height=v).render(),
        "progress-simple": lambda v: Progress(value=10, height=v).render(),
        "progress-simple-python": lambda v: Progress(value=10, height=v)._render_custom(),
    }


def _skeleton_paths() -> dict[str, Callable[[Any], str]]:
    return {"theme_skeleton": lambda v: _theme_tag("{% theme_skeleton width=w %}", w=v)}


LENGTH_CASES = [
    ("rich_text_editor", _rte_paths, 'style="min-height:{v}"', 'style="min-height:200px"'),
    ("scroll_area", _scroll_paths, "max-height: {v};", "max-height: 400px;"),
    ("sticky_header", _sticky_paths, "top: {v};", "top: 0;"),
    ("data_grid", _data_grid_paths, 'style="width:{v}', "<th"),
    ("data_table", _data_table_paths, 'style="width:{v}"', "<th"),
    ("resizable_panel", _resizable_paths, "min-width:{v}", "min-width:100px"),
    ("sortable_grid", _sortable_paths, 'gap:{v}"', 'gap:0.75rem"'),
    ("dashboard_grid", _dashboard_paths, "minmax({v},auto)", "minmax(200px,auto)"),
    ("progress", _progress_paths, 'style="height: {v}', "progress"),
    ("skeleton", _skeleton_paths, "width: {v};", "width: 100%;"),
]


class TestLengths:
    @pytest.mark.parametrize(
        "factory,path,frag,default",
        list(_expand([(n, f, [fr], d) for n, f, fr, d in LENGTH_CASES])),
    )
    @pytest.mark.parametrize("value", VALID_LENGTHS)
    def test_valid_length_written_as_given(self, factory, path, frag, default, value):
        out = factory()[path](value)
        assert frag[0].format(v=value) in out, out

    @pytest.mark.parametrize(
        "factory,path,frag,default",
        list(_expand([(n, f, [fr], d) for n, f, fr, d in LENGTH_CASES])),
    )
    @pytest.mark.parametrize("value", REFUSED_LENGTHS)
    def test_other_values_become_the_default(self, factory, path, frag, default, value):
        out = factory()[path](value)
        assert default in out, out
        _assert_absent(out, value)


def _aspect_paths() -> dict[str, Callable[[Any], str]]:
    from djust.components.components.aspect_ratio import AspectRatio

    return {
        "class": lambda v: AspectRatio(content="x", ratio=v).render(),
        "tag": lambda v: _tag("{% aspect_ratio ratio=r %}x{% endaspect_ratio %}", r=v),
    }


def _image_paths() -> dict[str, Callable[[Any], str]]:
    from djust.components.components.responsive_image import ResponsiveImage

    return {
        "class": lambda v: ResponsiveImage(src="/a.png", aspect_ratio=v).render(),
        "tag": lambda v: _tag('{% responsive_image src="/a.png" aspect_ratio=r %}', r=v),
        "engine": lambda v: _engine('{% responsive_image src="/a.png" aspect_ratio=r %}', r=v),
    }


class TestRatiosAndNumbers:
    @pytest.mark.parametrize("path", ["class", "tag"])
    @pytest.mark.parametrize("value", VALID_RATIOS)
    def test_aspect_ratio_valid(self, path, value):
        assert f"aspect-ratio: {value};" in _aspect_paths()[path](value)

    @pytest.mark.parametrize("path", ["class", "tag"])
    @pytest.mark.parametrize("value", REFUSED_RATIOS)
    def test_aspect_ratio_refused(self, path, value):
        out = _aspect_paths()[path](value)
        assert "aspect-ratio: 16/9;" in out
        _assert_absent(out, value)

    @pytest.mark.parametrize("path", ["class", "tag", "engine"])
    @pytest.mark.parametrize("value", VALID_RATIOS)
    def test_responsive_image_valid(self, path, value):
        assert f'style="aspect-ratio:{value}"' in _image_paths()[path](value)

    @pytest.mark.parametrize("path", ["class", "tag", "engine"])
    @pytest.mark.parametrize("value", REFUSED_RATIOS)
    def test_responsive_image_refused(self, path, value):
        out = _image_paths()[path](value)
        assert "aspect-ratio" not in out
        _assert_absent(out, value)

    def test_split_pane_initial(self):
        from djust.components.components.split_pane import SplitPane

        src = "{% split_pane initial=i %}a{% pane %}b{% endsplit_pane %}"
        for render in (
            lambda v: SplitPane(left="a", right="b", initial=v).render(),
            lambda v: _tag(src, i=v),
        ):
            assert "width:30%" in render(30)
            assert "width:33.5%" in render("33.5")
            out = render("30%;position:fixed")
            assert "width:50%" in out
            _assert_absent(out, "30%;position:fixed")

    def test_sticky_header_z_index(self):
        from djust.components.components.sticky_header import StickyHeader

        src = "{% sticky_header z_index=z %}x{% endsticky_header %}"
        for render in (
            lambda v: StickyHeader(content="x", z_index=v).render(),
            lambda v: _tag(src, z=v),
        ):
            assert "z-index: 20;" in render("20")
            out = render("20;position:fixed")
            assert "z-index: 10;" in out
            _assert_absent(out, "20;position:fixed")
