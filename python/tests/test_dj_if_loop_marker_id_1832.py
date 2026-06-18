"""Regression test for issue #1832 — P0 VDOM data-loss.

A ``{% if %}`` inside a ``{% for %}`` emits a ``<!--dj-if id="if-<hash>-N"-->``
open marker whose ``N`` is the parser's COMPILE-TIME ordinal. The ``For`` node
renders the same ``If`` node once per iteration, so before #1832 the id was
DUPLICATED across every iteration. On re-render the differ emits a
``MoveSubtree`` whose id matches N identical markers; the client cannot pair
them ("close marker not found"), most patches fail, and the recovery morph
drops a row per toggle.

These tests assert (mirroring ``scratch/vdom-1832/repro.py``):

1. There are NO duplicate dj-if marker ids at mount.
2. Toggling ONLY the table class (a non-loop state change) yields ZERO
   unpairable ``MoveSubtree`` patches — every ``MoveSubtree`` id appears
   exactly once among the rendered open markers.

Both assertions fail on the OLD behavior (duplicate ids + 15 spurious moves).
"""

import json
import re
from collections import Counter

from djust import LiveView

# Matches both the bare ``if-<8hex>-N`` form (if outside any loop) and the
# loop-path-suffixed ``if-<8hex>-N-<path>`` form (#1832).
_OPEN_MARKER_RE = re.compile(r'<!--dj-if id="(if-[0-9a-f]+-[0-9-]+)"-->')


class _ToggleView(LiveView):
    """16 rows, each with an inner ``{% if %}`` and a nested ``{% for %}``.

    Mirrors the structure in ``scratch/vdom-1832/repro.py`` — the shape that
    triggered the P0 in production (a table whose class toggles).
    """

    template = (
        '<div dj-root dj-view="tests.ToggleView" dj-id="0">'
        '<table class="{{ tcls }}"><tbody>'
        "{% for r in rows %}"
        "<tr><td>{% if r.hi %}<span>{{ r.hi }}</span>{% else %}<span>-</span>{% endif %}</td></tr>"
        '{% if r.alerts %}<tr class="detail"><td>'
        "{% for a in r.alerts %}<span>{{ a }}</span>{% endfor %}</td></tr>{% endif %}"
        "{% endfor %}"
        "</tbody></table></div>"
    )

    def mount(self, request, **kwargs):
        self.rows = [{"hi": i, "alerts": ["x", "y"]} for i in range(16)]
        self.tcls = "collapsed"

    def get_context_data(self, **kwargs):
        return {"rows": self.rows, "tcls": self.tcls, "view": self}


class _Req:
    method = "GET"
    GET = {}  # noqa: RUF012


def _mount_view():
    view = _ToggleView()
    view.mount(_Req())
    return view


def test_no_duplicate_dj_if_marker_ids_at_mount():
    """Every dj-if open-marker id at mount must be unique (#1832)."""
    view = _mount_view()
    html, _, _ = view.render_with_diff()

    ids = Counter(_OPEN_MARKER_RE.findall(html))
    dupes = {marker_id: count for marker_id, count in ids.items() if count > 1}

    assert not dupes, (
        f"dj-if marker ids must be unique across loop iterations; found duplicates: {dupes}"
    )
    # Sanity: two if-markers per row (the inner if + the alerts if) over 16
    # rows = 32 markers, all distinct.
    assert sum(ids.values()) == 32, f"expected 32 markers, got {sum(ids.values())}: {dict(ids)}"
    assert len(ids) == 32, "expected 32 DISTINCT marker ids"


def test_toggle_table_class_yields_no_unpairable_move_subtree():
    """Toggling only the table class must not emit unpairable MoveSubtree.

    A ``MoveSubtree`` whose id appears more than once among the rendered open
    markers is unpairable by the client. After #1832 every marker id is unique,
    so every ``MoveSubtree`` (if any) targets exactly one marker.
    """
    view = _mount_view()
    first_html, _, _ = view.render_with_diff()

    # Change ONLY the table class — a non-loop state toggle. The loop
    # structure is unchanged, so a correct differ should not move any
    # conditional subtree.
    view.tcls = ""
    second_html, patches, _ = view.render_with_diff()

    patch_list = json.loads(patches)
    moves = [p for p in patch_list if p.get("type") == "MoveSubtree"]

    # All open-marker ids currently in the DOM (after the second render).
    marker_id_counts = Counter(_OPEN_MARKER_RE.findall(second_html))

    # Every MoveSubtree id must correspond to exactly one marker (pairable).
    unpairable = [m["id"] for m in moves if marker_id_counts.get(m["id"], 0) != 1]
    assert not unpairable, (
        "every MoveSubtree must target a unique, pairable marker id; "
        f"unpairable move ids: {Counter(unpairable)}"
    )

    # And the marker ids themselves must remain unique post-toggle.
    dupes = {k: c for k, c in marker_id_counts.items() if c > 1}
    assert not dupes, f"duplicate marker ids after toggle: {dupes}"
