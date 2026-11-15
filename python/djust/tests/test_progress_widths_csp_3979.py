"""Progress bars outside ``{% progress %}`` draw their width without an inline style (#3979).

A ``style-src`` without ``'unsafe-inline'`` refuses a ``style="width:N%"`` attribute,
so the bar showed empty under a strict CSP. ``{% theme_progress %}`` now uses the
width classes ``{% progress %}`` uses (#3969), and the admin's bulk-action
progress page a native ``<progress>`` element.
"""

import re
from pathlib import Path

import pytest

from djust.tests.theming_component_test_base import ComponentTestCase

THEMING_CSS = (
    Path(__file__).resolve().parents[1]
    / "theming"
    / "static"
    / "djust_theming"
    / "css"
    / "components.css"
)
BULK_TEMPLATE = (
    Path(__file__).resolve().parents[1]
    / "admin_ext"
    / "templates"
    / "djust_admin"
    / "bulk_progress.html"
)


class TestThemeProgressWidthClass(ComponentTestCase):
    def test_the_fill_width_is_a_class_not_a_style(self):
        html = self.render_component("progress", value=25, max=100)
        assert "style=" not in html
        assert "progress-w-25" in html

    def test_the_width_rounds_to_a_whole_percent_and_caps_at_100(self):
        assert "progress-w-33" in self.render_component("progress", value=1, max=3)
        assert "progress-w-100" in self.render_component("progress", value=7, max=5)
        assert "progress-w-0" in self.render_component("progress", value=0, max=5)

    def test_an_indeterminate_bar_has_no_fill(self):
        html = self.render_component("progress", label="Loading")
        assert "progress-w-" not in html and "style=" not in html


def test_the_width_classes_take_the_theme_prefix_like_every_component_class():
    from djust.theming.component_css_generator import generate_component_css

    css = generate_component_css("dj-")
    assert ".dj-progress-w-25 { width: 25%; }" in css
    assert ".dj-progress-wrapper" in css and ".dj-dj-progress" not in css


@pytest.mark.parametrize("percent", [0, 1, 50, 99, 100])
def test_every_width_class_the_tag_emits_is_defined(percent):
    css = THEMING_CSS.read_text()
    assert re.search(r"\.progress-w-%d \{ width: %d%%; \}" % (percent, percent), css)


def test_the_bulk_progress_page_has_no_inline_style():
    from django.template import engines

    source = BULK_TEMPLATE.read_text()
    assert "style=" not in source
    # The bar itself, rendered: a native <progress> carrying the value.
    bar = re.search(r"<progress.*?</progress>", source, re.S).group(0)
    html = engines["django"].from_string(bar).render({"percent": 40, "action_label": "Export"})
    assert 'value="40"' in html and 'max="100"' in html and 'aria-label="Export"' in html


# --------------------------------------------------------------------------- #
# The other percent fills and the gauge box (#3979): one test per site.
#
# Every site below drew with `style="width:…"`. Each now carries a width class
# that a stylesheet the component already depends on defines, so a page under a
# `style-src` without 'unsafe-inline' draws them. "No inline style" is asserted
# on the rendered markup; "the class is defined" against the stylesheet.

from pathlib import Path as _Path  # noqa: E402

from django.template import Context as _Context  # noqa: E402
from django.template import Engine as _Engine  # noqa: E402

from djust.components import rust_handlers as _rh  # noqa: E402

_STATIC = _Path(_rh.__file__).resolve().parent / "static" / "djust_components"
_CATALOG_CSS = (_STATIC / "components.css").read_text()
_CLASSES_CSS = (_STATIC / "components-classes.css").read_text()
_TAGS = _Engine(
    dirs=[str(_Path(_rh.__file__).resolve().parent / "templates")],
    libraries={"djust_components": "djust.components.templatetags.djust_components"},
)


def _tag(source, **context):
    return _TAGS.from_string("{% load djust_components %}" + source).render(_Context(context))


def _width_classes(markup):
    return [int(n) for n in re.findall(r"\bdj-progress-w-(\d+)\b", markup)]


def _assert_defined(css, percents):
    for n in percents:
        assert re.search(r"\.dj-progress-w-%d \{ width: %d%%; \}" % (n, n), css), n


def _token_class(current, maximum):
    from djust.components.components.token_counter import TokenCounter

    return TokenCounter(current=current, max=maximum).render()


def _token_tag(current, maximum):
    return _tag("{% token_counter current=c max=m %}", c=current, m=maximum)


@pytest.mark.parametrize("render", [_token_class, _token_tag], ids=["class", "tag"])
def test_the_token_bar_width_is_a_class(render):
    html = render(250, 1000)
    assert "style=" not in html, html
    assert _width_classes(html) == [25]
    # `TokenCounter` is styled from components-classes.css alone, the tag from
    # either sheet: the classes are defined in both.
    _assert_defined(_CLASSES_CSS, range(101))
    _assert_defined(_CATALOG_CSS, range(101))


@pytest.mark.parametrize("render", [_token_class, _token_tag], ids=["class", "tag"])
def test_the_token_bar_rounds_and_clamps(render):
    assert _width_classes(render(1, 3)) == [33]
    assert _width_classes(render(9000, 1000)) == [100]


def _meter_class(segments, total):
    from djust.components.components.meter import Meter

    return Meter(segments=segments, total=total).render()


def _meter_tag(segments, total):
    return _tag("{% meter segments=s total=t %}", s=segments, t=total)


@pytest.mark.parametrize("render", [_meter_class, _meter_tag], ids=["class", "tag"])
def test_the_meter_segment_widths_are_classes(render):
    html = render([{"value": 30, "label": "a"}, {"value": 45, "label": "b"}], 100)
    assert "style=" not in html, html
    assert _width_classes(html) == [30, 45]
    _assert_defined(_CATALOG_CSS, [30, 45])


@pytest.mark.parametrize("render", [_meter_class, _meter_tag], ids=["class", "tag"])
def test_a_meter_segment_colour_is_a_data_attribute(render):
    """A custom colour has no class to stand for it: the client applies it (#3979)."""
    html = render([{"value": 50, "color": "#ff0000"}], 100)
    # The segment and its legend swatch: colour only, no width, nothing inline.
    assert "style=" not in html, html
    assert (
        re.findall(r'data-dj-style-([a-z-]+)="([^"]*)"', html)
        == [("background-color", "#ff0000")] * 2
    ), html
    assert _width_classes(html) == [50]


def _skeleton_class(component):
    from djust.components.components.skeleton_factory import SkeletonFactory

    return SkeletonFactory(component=component, rows=3, columns=2).render()


def _skeleton_tag(component):
    return _tag("{% skeleton_for component=c rows=3 columns=2 %}", c=component)


@pytest.mark.parametrize("component", ["table", "card", "list", "text"])
@pytest.mark.parametrize("render", [_skeleton_class, _skeleton_tag], ids=["class", "tag"])
def test_the_skeleton_line_widths_are_classes(render, component):
    html = render(component)
    assert "style=" not in html, html
    widths = _width_classes(html)
    assert widths, html
    _assert_defined(_CATALOG_CSS, widths)


def test_the_skeleton_text_lines_keep_their_width_palette():
    assert _width_classes(_skeleton_class("text")) == [95, 85, 90]
    assert _width_classes(_skeleton_tag("text")) == [95, 85, 90]


def _data_table(cf):
    return str(
        _rh.DataTableHandler().render(
            ["rows=rows", "columns=cols", "conditional_formatting=cf"],
            {
                "rows": [{"id": 1, "n": 25}],
                "cols": [{"key": "n", "label": "N"}],
                "cf": cf,
            },
        )
    )


def test_the_data_table_data_bar_width_is_a_class():
    html = _data_table([{"column": "n", "type": "data_bar", "min": 0, "max": 100}])
    bar = re.search(r'<div class="data-table-data-bar[^"]*"[^>]*>', html).group(0)
    assert "style=" not in bar, bar
    assert _width_classes(bar) == [25]


def test_the_data_table_loading_skeleton_has_no_inline_style():
    html = str(
        _rh.DataTableHandler().render(
            ["rows=rows", "columns=cols", "loading=True"],
            {"rows": [], "cols": [{"key": "n", "label": "N"}]},
        )
    )
    assert "skeleton-line" in html
    assert "style=" not in html, html
    _assert_defined(_CATALOG_CSS, [100])


def test_the_table_template_loading_skeleton_has_no_inline_style():
    source = _Path(_rh.__file__).resolve().parent / "templates" / "djust_components" / "table.html"
    lines = [ln for ln in source.read_text().splitlines() if "skeleton-line" in ln]
    assert len(lines) == 5
    assert all("style=" not in ln and "dj-progress-w-100" in ln for ln in lines), lines


def _gauge_class(size):
    from djust.components.components.gauge import Gauge

    return Gauge(value=40, size=size).render()


def _gauge_tag(size):
    return _tag("{% gauge value=40 size=s %}", s=size)


@pytest.mark.parametrize(
    "size,expected,px", [("sm", "sm", 64), ("md", "md", 96), ("lg", "lg", 128), ("xl", "md", 96)]
)
@pytest.mark.parametrize("render", [_gauge_class, _gauge_tag], ids=["class", "tag"])
def test_the_gauge_box_is_a_size_class(render, size, expected, px):
    html = render(size)
    outer = re.search(r'<div class="gauge [^"]*"[^>]*>', html).group(0)
    assert "style=" not in outer, outer
    assert f"gauge--{expected}" in outer
    assert re.search(
        r"\.gauge--%s \{ width: %dpx; height: %dpx; \}" % (expected, px, px), _CATALOG_CSS
    )


_REPO = _Path(_rh.__file__).resolve().parents[3]


@pytest.mark.parametrize(
    "path",
    [
        "examples/demo_project/demo_app/views/component_demo.py",
        "examples/demo_project/demo_app/views/no_template_demo.py",
        "examples/demo_project/djust_demos/views/component_demo.py",
        "examples/demo_project/djust_demos/views/no_template_demo.py",
        "docs/components/COMPONENT_BEST_PRACTICES.md",
    ],
)
def test_the_hand_written_bars_that_teach_the_pattern_use_a_native_progress(path):
    """The demo views and the best-practices example are what people copy."""
    source = (_REPO / path).read_text()
    assert not re.search(r"style=\"width: ?(\{\{|\{\}|\{)", source), path
    assert "<progress" in source, path
