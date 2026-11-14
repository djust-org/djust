"""#4120: ``{% kanban_board %}`` under a strict CSP, without "Add card", with links.

Every case runs on Django's engine and on djust's Rust engine, which must agree.
"""

from __future__ import annotations

import re

import pytest

from djust.tests.test_components_csp_aria import _both

COLS = [
    {
        "id": "todo",
        "title": "To do",
        "cards": [{"id": "c1", "title": "A <b>", "href": "/items/1/"}],
    },
    {"id": "done", "title": "Done", "color": "success", "cards": [{"id": "c2", "title": "B"}]},
]

_DJ_ID = re.compile(r' dj-id="[^"]*"')


def _render(src: str, ctx: dict) -> str:
    out = {engine: _DJ_ID.sub("", html) for engine, html in _both(src, ctx).items()}
    assert out["django"] == out["rust"], out
    return out["django"]


def test_no_style_attribute_by_default_or_for_a_theme_colour():
    out = _render("{% kanban_board columns=cols %}", {"cols": COLS})
    assert "style=" not in out, out
    assert '<div class="kanban-col-header">' in out
    assert '<div class="kanban-col-header kanban-col--success">' in out


@pytest.mark.parametrize("name", ["primary", "success", "warning", "destructive", "info", "muted"])
def test_each_theme_colour_has_a_rule_in_the_stylesheet(name):
    from pathlib import Path

    import djust

    css = (
        Path(djust.__file__).parent / "components/static/djust_components/components.css"
    ).read_text()
    assert re.search(r"\.kanban-col--%s \{ border-top-color:" % name, css)


def test_a_custom_css_colour_still_renders_as_given():
    cols = [{"id": "x", "title": "X", "color": "#123456", "cards": []}]
    out = _render("{% kanban_board columns=cols %}", {"cols": cols})
    assert 'style="border-top-color:#123456"' in out


def test_add_card_button_by_default():
    out = _render("{% kanban_board columns=cols %}", {"cols": COLS})
    assert out.count('class="kanban-add-card" dj-click="kanban_add_card"') == 2


@pytest.mark.parametrize("value", ["None", '""'])
def test_add_card_event_none_leaves_the_button_out(value):
    out = _render("{% kanban_board columns=cols add_card_event=" + value + " %}", {"cols": COLS})
    assert "kanban-add-card" not in out
    assert "+ Add card" not in out
    # The drag markup is unchanged.
    assert 'data-move-event="kanban_move"' in out
    assert 'draggable="true" data-card-id="c1" data-col-id="todo"' in out


def test_a_card_href_makes_its_title_a_safe_link():
    out = _render("{% kanban_board columns=cols %}", {"cols": COLS})
    assert (
        '<div class="kanban-card-title"><a class="kanban-card-link" href="/items/1/" '
        'draggable="false">A &lt;b&gt;</a></div>'
    ) in out
    assert '<div class="kanban-card-title">B</div>' in out
    bad = [
        {
            "id": "x",
            "title": "X",
            "cards": [{"id": "c", "title": "C", "href": "javascript:alert(1)"}],
        }
    ]
    out = _render("{% kanban_board columns=cols %}", {"cols": bad})
    assert "javascript:" not in out
