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


# --- the KanbanBoard class component (#4120 review) ---------------------------

CLASS_COLS = [{"id": "todo", "title": "To do", "cards": [{"id": "c1", "title": "A"}]}]


def _class_paths(board) -> dict:
    """The class component's markup: ``render()`` and in a template on both engines."""
    paths = {"render": str(board.render())}
    paths.update(
        {
            engine: _DJ_ID.sub("", html)
            for engine, html in _both("{{ board }}", {"board": board}).items()
        }
    )
    return paths


def test_class_component_shows_add_card_by_default():
    from djust.components.components.kanban_board import KanbanBoard

    for path, out in _class_paths(KanbanBoard(columns=CLASS_COLS)).items():
        assert "+ Add card" in out, (path, out)


@pytest.mark.parametrize("value", [None, ""])
def test_class_component_without_add_card_event_has_no_button(value):
    from djust.components.components.kanban_board import KanbanBoard

    for path, out in _class_paths(KanbanBoard(columns=CLASS_COLS, add_card_event=value)).items():
        assert "kanban-add-card" not in out and "+ Add card" not in out, (path, out)
        assert 'data-col-id="todo"' in out, (path, out)


# --- allowed columns and the keyboard move (#4120 parts 3 and 4) ------------


def test_card_targets_render_as_data_targets():
    cols = [
        {
            "id": "todo",
            "title": "To do",
            "cards": [
                {"id": "c1", "title": "A", "targets": ["doing", "done"]},
                {"id": "c2", "title": "B", "targets": []},
                {"id": "c3", "title": "C"},
            ],
        }
    ]
    out = _render("{% kanban_board columns=cols %}", {"cols": cols})
    assert 'data-card-id="c1" data-col-id="todo" data-targets="doing done"' in out
    # An empty list: the card may go nowhere else.
    assert 'data-card-id="c2" data-col-id="todo" data-targets=""' in out
    # No targets: any column, so no attribute.
    assert 'data-card-id="c3" data-col-id="todo" tabindex' in out


def test_card_targets_are_escaped():
    cols = [{"id": "t", "title": "T", "cards": [{"id": "c", "title": "C", "targets": ['a" x="1']}]}]
    out = _render("{% kanban_board columns=cols %}", {"cols": cols})
    assert 'data-targets="a&quot; x=&quot;1"' in out
    assert ' x="1"' not in out


def test_every_card_takes_focus_for_the_keyboard_move():
    out = _render("{% kanban_board columns=cols %}", {"cols": COLS})
    cards = re.findall(r'<div class="kanban-card"[^>]*>', out)
    assert len(cards) == 2
    for card in cards:
        assert 'tabindex="0"' in card, card
        # aria-roledescription needs a role; aria-pressed is not allowed on a group.
        assert 'role="group" aria-roledescription="movable card"' in card, card
        assert "aria-pressed" not in card
        assert 'aria-describedby="kanban-hint-todo"' in card, card
    # The instruction a screen reader announces on focus, before pickup.
    assert (
        '<span class="sr-only kanban-hint" id="kanban-hint-todo">Press Space to pick up the card'
    ) in out


@pytest.mark.parametrize(
    "targets, expected",
    [
        ("done", "done"),
        (("doing", "done"), "doing done"),
        ({"done", "doing"}, "doing done"),
        (iter(["a", "b"]), "a b"),
        # A lazy string is one id, not its characters.
        ("lazy:done", "done"),
    ],
)
def test_card_targets_accept_a_string_or_any_iterable(targets, expected):
    from django.utils.functional import lazy

    from djust.components.templatetags.djust_components import kanban_board

    if targets == "lazy:done":
        targets = lazy(lambda: "done", str)()
    cols = [{"id": "t", "title": "T", "cards": [{"id": "c", "title": "C", "targets": targets}]}]
    assert 'data-targets="%s"' % expected in str(kanban_board(columns=cols))


def test_a_column_id_with_whitespace_warns_once(caplog):
    import logging

    from djust.components.templatetags import djust_components as tags

    tags._KANBAN_SPACED_IDS.discard("in review")
    cols = [
        {
            "id": "in review",
            "title": "R",
            "cards": [{"id": "c", "title": "C", "targets": ["in review"]}],
        },
    ]
    with caplog.at_level(logging.WARNING, logger="djust.components"):
        tags.kanban_board(columns=cols)
        tags.kanban_board(columns=cols)
    warnings = [r.getMessage() for r in caplog.records if "whitespace" in r.getMessage()]
    assert len(warnings) == 1, warnings
    assert "'in review'" in warnings[0]


def test_form_control_focus_rule_keeps_its_ring_fallback():
    """An unrelated rule a kanban change once edited by accident (#4120 review)."""
    from pathlib import Path

    import djust

    css = (
        Path(djust.__file__).parent / "components/static/djust_components/components.css"
    ).read_text()
    assert (
        ".form-control:focus { border-color: hsl(var(--ring, var(--primary))); "
        "box-shadow: 0 0 0 var(--form-focus-ring-width, 3px) "
        "hsl(var(--ring, var(--primary)) / var(--form-focus-ring-opacity, 0.2)); outline: none; }"
    ) in css
