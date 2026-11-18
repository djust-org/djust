"""#1678 — `{% kanban_board %}` must emit `dj-key` anchors on cards and columns.

Without keyed anchors, a card move shifts per-column child counts and the VDOM
differ patches against stale positional paths → a storm of failed patches +
full `html_recovery` on every drag. `dj-key` lets the differ reconcile cards
(and columns) by IDENTITY across the move.

Doc-claim-verbatim discipline (Action #1046): assert on the actual rendered
markup, not "no error". Gate-off: revert the `dj-key` additions → these fail.
"""

import re

import pytest
from django.template import Context, Engine

pytestmark = pytest.mark.components

_TAG_ENGINE = Engine(
    libraries={"djust_components": "djust.components.templatetags.djust_components"}
)


def _render_board():
    board = [
        {
            "id": "todo",
            "title": "To Do",
            "cards": [{"id": "c1", "title": "Task A"}, {"id": "c2", "title": "Task B"}],
        },
        {"id": "done", "title": "Done", "cards": [{"id": "c3", "title": "Task C"}]},
    ]
    src = "{% load djust_components %}{% kanban_board columns=board move_event='move_card' %}"
    return _TAG_ENGINE.from_string(src).render(Context({"board": board}))


def test_cards_emit_dj_key_by_card_id():
    html = _render_board()
    for card_id in ("c1", "c2", "c3"):
        assert f'dj-key="{card_id}"' in html, (
            f"card {card_id} must carry a dj-key so the differ tracks it across a move (#1678)"
        )


def test_columns_emit_dj_key_by_col_id():
    html = _render_board()
    for col_id in ("todo", "done"):
        assert f'dj-key="{col_id}"' in html, (
            f"column {col_id} must carry a dj-key for keyed reconciliation (#1678)"
        )


def test_dj_key_is_on_the_card_and_col_elements():
    """The dj-key must be ON the kanban-card / kanban-col elements (not loose)."""
    html = _render_board()
    # Every kanban-card div carries a dj-key.
    card_divs = re.findall(r'<div class="kanban-card"[^>]*>', html)
    assert len(card_divs) == 3
    for div in card_divs:
        assert "dj-key=" in div, f"kanban-card element missing dj-key: {div}"
    # Every kanban-col div carries a dj-key.
    col_divs = re.findall(r'<div class="kanban-col"[^>]*>', html)
    assert len(col_divs) == 2
    for div in col_divs:
        assert "dj-key=" in div, f"kanban-col element missing dj-key: {div}"
