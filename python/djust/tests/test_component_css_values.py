"""Colours, lengths and ratios that components write into ``style`` attributes.

A component that takes a CSS value from its caller (a user's colour in
``CursorsOverlay``, a column width, a gap) writes it into an inline ``style``
attribute. HTML escaping keeps the value inside the attribute but not inside
its declaration, so each value is checked against the shapes the component
documents (``djust.components.utils.safe_css_color`` / ``safe_css_length`` /
``safe_css_ratio`` / ``safe_css_number``) and anything else is replaced by the
component's default. A value that passes is written exactly as given.

Every render path is covered: the component class, the Django template tag,
the Rust-engine handler called directly, and the Rust template engine.
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


def _engine_unquotes(path: str, value: str) -> bool:
    """The Rust engine's argument parser reads a context string wrapped in
    quotes as that literal, so ``'10px'`` arrives as ``10px`` (a valid value)."""
    return path == "engine" and len(value) > 1 and value[0] == value[-1] and value[0] in "'\""


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
# CursorsOverlay and CollabSelection: a participant's colour
# ---------------------------------------------------------------------------


def _cursor_users(color: Any) -> list[dict[str, Any]]:
    return [{"name": "Alice", "color": color, "x": 3, "y": 4}]


def _selection_users(color: Any) -> list[dict[str, Any]]:
    return [{"name": "Alice", "color": color, "start": 0, "end": 5, "text": "Hello"}]


def _cursor_paths() -> dict[str, Callable[[Any], str]]:
    from djust.components.components.cursors_overlay import CursorsOverlay

    return {
        "class": lambda c: CursorsOverlay(users=_cursor_users(c)).render(),
        "tag": lambda c: _tag("{% cursors users=users %}", users=_cursor_users(c)),
        "handler": lambda c: str(
            rh.CursorsOverlayHandler().render(["users=users"], {"users": _cursor_users(c)})
        ),
        "engine": lambda c: _engine("{% cursors users=users %}", users=_cursor_users(c)),
    }


def _selection_paths() -> dict[str, Callable[[Any], str]]:
    from djust.components.components.collab_selection import CollabSelection

    return {
        "class": lambda c: CollabSelection(users=_selection_users(c)).render(),
        "tag": lambda c: _tag("{% collab_selection users=users %}", users=_selection_users(c)),
        "handler": lambda c: str(
            rh.CollabSelectionHandler().render(["users=users"], {"users": _selection_users(c)})
        ),
        "engine": lambda c: _engine(
            "{% collab_selection users=users %}", users=_selection_users(c)
        ),
    }


PATHS = ["class", "tag", "handler", "engine"]


class TestCursorsOverlay:
    @pytest.mark.parametrize("path", PATHS)
    @pytest.mark.parametrize("color", VALID_COLORS)
    def test_valid_colour_written_as_given(self, path, color):
        out = _cursor_paths()[path](color)
        assert f'fill="{color}"' in out
        assert f'style="background:{color}"' in out

    @pytest.mark.parametrize("path", PATHS)
    @pytest.mark.parametrize("color", REFUSED_COLORS)
    def test_other_values_become_the_palette_colour(self, path, color):
        out = _cursor_paths()[path](color)
        assert 'fill="#3b82f6"' in out
        assert 'style="background:#3b82f6"' in out
        _assert_absent(out, color)

    def test_palette_follows_position(self):
        users = [
            {"name": "A", "color": "#111111"},
            {"name": "B", "color": "red;x:y"},
        ]
        for render in (
            lambda: _tag("{% cursors users=users %}", users=users),
            lambda: str(rh.CursorsOverlayHandler().render(["users=users"], {"users": users})),
        ):
            assert _style_attrs(render())[1::2] == ["background:#111111", "background:#ef4444"]

    @pytest.mark.parametrize("color", VALID_COLORS[:4] + REFUSED_COLORS[:4])
    def test_paths_agree(self, color):
        outs = {p: _cursor_paths()[p](color) for p in PATHS}
        assert len({tuple(_style_attrs(o)) for o in outs.values()}) == 1, outs
        assert len({tuple(re.findall(r'fill="[^"]*"', o)) for o in outs.values()}) == 1


class TestCollabSelection:
    @pytest.mark.parametrize("path", PATHS)
    @pytest.mark.parametrize("color", VALID_COLORS)
    def test_valid_colour_written_as_given(self, path, color):
        out = _selection_paths()[path](color)
        assert f'style="--dj-collab-sel-color:{color}"' in out
        assert f'style="background:{color}"' in out

    @pytest.mark.parametrize("path", PATHS)
    @pytest.mark.parametrize("color", REFUSED_COLORS)
    def test_other_values_become_the_palette_colour(self, path, color):
        out = _selection_paths()[path](color)
        assert 'style="--dj-collab-sel-color:#3b82f6"' in out
        assert 'style="background:#3b82f6"' in out
        _assert_absent(out, color)

    @pytest.mark.parametrize("color", VALID_COLORS[:4] + REFUSED_COLORS[:4])
    def test_paths_agree(self, color):
        outs = {p: _selection_paths()[p](color) for p in PATHS}
        assert len({tuple(_style_attrs(o)) for o in outs.values()}) == 1, outs


# ---------------------------------------------------------------------------
# Other components that take a colour
# ---------------------------------------------------------------------------


def _calendar_events(c: Any) -> list[dict[str, Any]]:
    return [{"date": "2026-03-25", "title": "Standup", "color": c}]


def _calendar_paths() -> dict[str, Callable[[Any], str]]:
    from djust.components.components.calendar_view import CalendarView

    src = "{% calendar events=events month=3 year=2026 %}"
    return {
        "class": lambda c: CalendarView(events=_calendar_events(c), month=3, year=2026).render(),
        "tag": lambda c: _tag(src, events=_calendar_events(c)),
        "handler": lambda c: str(
            rh.CalendarViewHandler().render(
                ["events=events", "month=3", "year=2026"], {"events": _calendar_events(c)}
            )
        ),
        "engine": lambda c: _engine(src, events=_calendar_events(c)),
    }


def _meter_segments(c: Any) -> list[dict[str, Any]]:
    return [{"value": 40, "color": c, "label": "Used"}]


def _meter_paths() -> dict[str, Callable[[Any], str]]:
    from djust.components.components.meter import Meter

    return {
        "class": lambda c: Meter(segments=_meter_segments(c)).render(),
        "tag": lambda c: _tag("{% meter segments=segs %}", segs=_meter_segments(c)),
        "handler": lambda c: str(
            rh.MeterHandler().render(["segments=segs"], {"segs": _meter_segments(c)})
        ),
        "engine": lambda c: _engine("{% meter segments=segs %}", segs=_meter_segments(c)),
    }


def _color_picker_paths() -> dict[str, Callable[[Any], str]]:
    from djust.components.components.color_picker import ColorPicker

    src = '{% color_picker name="c" value=v swatches=sw %}'
    return {
        "class": lambda c: ColorPicker(name="c", value=c, swatches=[c]).render(),
        "tag": lambda c: _tag(src, v=c, sw=[c]),
        "handler": lambda c: str(
            rh.ColorPickerHandler().render(
                ['name="c"', "value=v", "swatches=sw"], {"v": c, "sw": [c]}
            )
        ),
    }


def _kanban_cols(c: Any) -> list[dict[str, Any]]:
    return [{"id": "todo", "title": "To do", "color": c, "cards": []}]


def _kanban_paths() -> dict[str, Callable[[Any], str]]:
    return {
        "tag": lambda c: _tag("{% kanban_board columns=cols %}", cols=_kanban_cols(c)),
        "handler": lambda c: str(
            rh.KanbanBoardHandler().render(["columns=cols"], {"cols": _kanban_cols(c)})
        ),
    }


COLOUR_CASES = [
    # name, paths, fragments for a valid colour, fragment for a refused one
    (
        "calendar",
        _calendar_paths,
        ['style="--dj-calendar-event-color: {v}"'],
        'style="--dj-calendar-event-color: #3b82f6"',
    ),
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


def _map_paths() -> dict[str, Callable[[Any], str]]:
    from djust.components.components.map_picker import MapPicker

    return {
        "class": lambda v: MapPicker(height=v).render(),
        "tag": lambda v: _tag("{% map_picker height=h %}", h=v),
        "handler": lambda v: str(rh.MapPickerHandler().render(["height=h"], {"h": v})),
        "engine": lambda v: _engine("{% map_picker height=h %}", h=v),
    }


def _rte_paths() -> dict[str, Callable[[Any], str]]:
    from djust.components.components.rich_text_editor import RichTextEditor

    return {
        "class": lambda v: RichTextEditor(height=v).render(),
        "tag": lambda v: _tag("{% rich_text_editor height=h %}", h=v),
        "handler": lambda v: str(rh.RichTextEditorHandler().render(["height=h"], {"h": v})),
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
        "handler": lambda v: str(
            rh.DataGridHandler().render(["columns=c", "rows=r"], {"c": _grid_columns(v), "r": rows})
        ),
    }


def _data_table_paths() -> dict[str, Callable[[Any], str]]:
    rows = [{"id": 1, "a": "x"}]
    return {
        "tag": lambda v: _tag("{% data_table rows=r columns=c %}", c=_grid_columns(v), r=rows),
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
        "handler": lambda v: str(
            rh.SortableGridHandler().render(["items=i", "gap=g"], {"i": items, "g": v})
        ),
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
    ("map_picker", _map_paths, 'style="height:{v}"', 'style="height:400px"'),
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
        if _engine_unquotes(path, value):
            assert frag[0].format(v=value[1:-1]) in out, out
        else:
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
        "handler": lambda v: str(
            rh.ResponsiveImageHandler().render(['src="/a.png"', "aspect_ratio=r"], {"r": v})
        ),
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

    @pytest.mark.parametrize("path", ["class", "tag", "handler", "engine"])
    @pytest.mark.parametrize("value", VALID_RATIOS)
    def test_responsive_image_valid(self, path, value):
        assert f'style="aspect-ratio:{value}"' in _image_paths()[path](value)

    @pytest.mark.parametrize("path", ["class", "tag", "handler", "engine"])
    @pytest.mark.parametrize("value", REFUSED_RATIOS)
    def test_responsive_image_refused(self, path, value):
        out = _image_paths()[path](value)
        if _engine_unquotes(path, value):
            assert f'style="aspect-ratio:{value[1:-1]}"' in out
        else:
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
