"""Regression for #1826 (P0, 1.0.6rc1): a ``{% if %}``-wrapped element inside a
``{% for %}`` loop must not emit spurious ``MoveSubtree`` ops against LATER
dj-if boundaries when an EARLIER boundary body fills.

Exercises the ``diff_html`` entry point directly (the same surface the browser
symptom flowed through). The matched-boundary move-decision in
``crates/djust_vdom/src/diff.rs`` used to compare ABSOLUTE child offsets, so
filling ``if-a-0``'s empty body shifted the absolute index of ``if-b-0`` /
``if-c-0`` and emitted ``MoveSubtree`` ops the client could not pair
(``close marker not found``) → ~15/22 patches failed → ``html_recovery`` morph
dropped a row per toggle. The fix keys the decision on the boundary's position
RELATIVE to its non-boundary siblings (+ its ordinal among same-level
boundaries).

Defect-1 note (documented, NOT fixed here, follow-up #1827): with a BARE
``<tbody>`` fragment (no ``<table>`` ancestor) html5ever foster-parents the
``<tr>`` out and ``diff_html`` flattens the inserted body node to a ``#text``
node. This is an ENVIRONMENT artifact — the real production DOM has the
``<table>`` ancestor, where the ``<tr>`` is preserved. The ``<div>``/``<span>``
shape below exercises Defect 2 cleanly (no foster-parenting); the two table
cases pin that the flattening is purely the bare-fragment context.
"""

import json

import djust._rust as rust

ITEMS = ("a", "b", "c")


def _div_dom(show):
    """Non-table container: ``<div>`` of ``<span class=main>`` rows, each
    followed by its own dj-if boundary whose body is empty (``show=False``)
    or holds a ``<span class=detail>`` (``show=True``)."""
    out = ["<div>"]
    for n in ITEMS:
        out.append(f'<span class="main">{n}</span>')
        inner = f'<span class="detail">detail {n}</span>' if show else ""
        out.append(f'<!--dj-if id="if-{n}-0"-->{inner}<!--/dj-if-->')
    out.append("</div>")
    return "".join(out)


def _table_dom(show, wrap_table):
    """Table rows. ``wrap_table=False`` is the bare ``<tbody>`` fragment that
    triggers html5ever foster-parenting; ``True`` wraps in ``<table>`` (the
    real production shape)."""
    out = ["<table><tbody>" if wrap_table else "<tbody>"]
    for n in ITEMS:
        out.append(f'<tr class="main"><td>{n}</td></tr>')
        inner = f'<tr class="detail"><td>detail {n}</td></tr>' if show else ""
        out.append(f'<!--dj-if id="if-{n}-0"-->{inner}<!--/dj-if-->')
    out.append("</tbody>")
    if wrap_table:
        out.append("</table>")
    return "".join(out)


def test_div_loop_fill_emits_no_spurious_move_subtree():
    """LOAD-BEARING #1826 assertion: filling earlier dj-if bodies must not
    move later boundaries, and the inserted body node is the real element."""
    patches = json.loads(diff := rust.diff_html(_div_dom(False), _div_dom(True)))
    moves = [p for p in patches if p["type"] == "MoveSubtree"]
    assert not moves, (
        f"spurious MoveSubtree for later dj-if boundaries (#1826): {[m.get('id') for m in moves]}\n{diff}"
    )

    inserts = [p for p in patches if p["type"] == "InsertChild"]
    # Three boundary bodies filled -> three element inserts, none flattened.
    assert len(inserts) == 3, f"expected 3 detail inserts, got {len(inserts)}: {diff}"
    flattened = [p for p in inserts if (p.get("node") or {}).get("tag") == "#text"]
    assert not flattened, (
        f"non-table container must insert the <span> element, not #text: {flattened}"
    )
    assert all((p.get("node") or {}).get("tag") == "span" for p in inserts), diff


def test_div_loop_round_trip_keeps_all_rows():
    """Toggling show=False -> True then True -> False must be symmetric: the
    forward fill inserts exactly the bodies the reverse empty removes, with no
    marker moves in either direction (the dropped-row symptom is a marker move
    the client can't pair)."""
    fwd = json.loads(rust.diff_html(_div_dom(False), _div_dom(True)))
    rev = json.loads(rust.diff_html(_div_dom(True), _div_dom(False)))
    assert not [p for p in fwd if p["type"] == "MoveSubtree"], fwd
    assert not [p for p in rev if p["type"] == "MoveSubtree"], rev
    # Forward fills 3 bodies; reverse empties them (RemoveSubtree per boundary
    # is the empty-body path; either way, no marker MOVE).
    assert len([p for p in fwd if p["type"] == "InsertChild"]) == 3, fwd


def test_table_wrapped_preserves_tr_and_no_move():
    """Production shape (``<table><tbody>``): no spurious MoveSubtree, and the
    inserted body node is the real ``<tr>`` element (no foster-parenting)."""
    patches = json.loads(rust.diff_html(_table_dom(False, True), _table_dom(True, True)))
    assert not [p for p in patches if p["type"] == "MoveSubtree"], patches
    inserts = [p for p in patches if p["type"] == "InsertChild"]
    assert inserts, patches
    assert all((p.get("node") or {}).get("tag") == "tr" for p in inserts), (
        "with the <table> ancestor the inserted body node is the <tr> element",
        patches,
    )


def test_bare_tbody_text_flatten_is_environment_artifact():
    """DOCUMENTS Defect 1: a BARE ``<tbody>`` fragment (no ``<table>``) makes
    html5ever foster-parent the ``<tr>`` out, so ``diff_html`` flattens the
    inserted body node to a ``#text`` node. This is expected html5ever
    behavior for an out-of-context table fragment — it does NOT reproduce in
    production (real DOM has the ``<table>`` ancestor; see the table-wrapped
    test above). Tracked as follow-up #1827 for optional ``diff_html``
    table-fragment context-sensitivity. The #1826 P0 fix (no spurious
    MoveSubtree) holds regardless of the container.
    """
    patches = json.loads(rust.diff_html(_table_dom(False, False), _table_dom(True, False)))
    # The P0 fix holds even in the bare-fragment case.
    assert not [p for p in patches if p["type"] == "MoveSubtree"], patches
    inserts = [p for p in patches if p["type"] == "InsertChild"]
    flattened = [p for p in inserts if (p.get("node") or {}).get("tag") == "#text"]
    # Pin the CURRENT (environment-artifact) behavior so a future
    # context-sensitivity fix updates this test deliberately.
    assert flattened, (
        "bare <tbody> fragment is expected to foster-parent <tr> to #text "
        "(html5ever); if this changed, the table-fragment context fix landed",
        patches,
    )
