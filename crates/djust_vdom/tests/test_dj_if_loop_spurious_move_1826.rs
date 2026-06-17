//! Regression: a `{% if %}`-wrapped element inside a `{% for %}` loop must NOT
//! emit spurious `MoveSubtree` ops against LATER dj-if boundaries when an
//! EARLIER boundary body fills (#1826, P0 regression in 1.0.6rc1).
//!
//! Production shape (browser symptom): N "main" rows, each immediately
//! followed by its own `<!--dj-if id="if-<n>-0"-->…<!--/dj-if-->` block whose
//! body goes empty → non-empty when a flag toggles. On 1.0.6rc1 `diff_html`
//! emitted `MoveSubtree { id: "if-b-0" }` / `{ id: "if-c-0" }` because the
//! move-decision compared ABSOLUTE child offsets: filling `if-a-0`'s body
//! inserts a node that shifts the absolute index of `if-b-0`/`if-c-0` even
//! though they did NOT move relative to their siblings. The client can't pair
//! those marker moves ("close marker not found"), ~15/22 patches fail, an
//! `html_recovery` morph runs, and a row is visibly dropped per toggle.
//!
//! The fix (`crates/djust_vdom/src/diff.rs`, matched-boundary block) keys the
//! move-decision on the boundary's position RELATIVE to its non-boundary
//! siblings (+ its ordinal among same-level boundaries), so a sibling
//! boundary's span-length change never triggers a move.
//!
//! Container note: the production DOM wraps these rows in `<table><tbody>`.
//! Exercising Defect 2 here we use a NON-table container (`<div>`/`<span>`)
//! so the html5ever foster-parenting that flattens a bare `<tbody>`'s `<tr>`
//! to a `#text` node (Defect 1 in #1826 — an ENVIRONMENT artifact that does
//! not reproduce with the real `<table>` ancestor) cannot confound the
//! move-decision assertions. We hand-build the VNode trees directly (not via
//! `diff_html`) so the boundaries carry stable inner `djust_id`s, mirroring a
//! real keyed render. A separate Python test in
//! `python/djust/tests/test_diff_html_if_marker_rows_1826.py` exercises the
//! `diff_html` entry point and documents the table-fragment foster-parenting.

mod common;

use common::{
    apply_all, assert_handles_resolve, count, dj_if_close, dj_if_open, elem, elem_with_text,
    find_by_djust_id, IdGen,
};
use djust_vdom::diff::diff_nodes;
use djust_vdom::{Patch, VNode};

const ITEMS: [&str; 3] = ["a", "b", "c"];

/// Build the `<div>` container for the loop. When `show` is false every
/// boundary body is EMPTY; when true each body holds one `<span class="detail">`.
///
/// dj-ids are allocated from a single `IdGen` in render order so the OLD and
/// NEW trees share ids for nodes present in both (the three `span.main` rows
/// and — when shown — the detail spans only exist in NEW). This mirrors how
/// `sync_ids` carries stable ids forward across a real re-render.
fn build(show: bool) -> VNode {
    let gen = IdGen::new();
    let mut children: Vec<VNode> = Vec::new();
    for n in ITEMS {
        // The persistent "main" row (present in both renders).
        children.push(elem_with_text("span", n, &gen).with_attr("class", "main"));
        // Its own dj-if boundary.
        children.push(dj_if_open(&format!("if-{}-0", n)));
        if show {
            children.push(
                elem_with_text("span", &format!("detail {}", n), &gen).with_attr("class", "detail"),
            );
        }
        children.push(dj_if_close());
    }
    elem("div", &gen).with_children(children)
}

#[test]
fn djif_loop_fill_emits_no_spurious_move_for_later_boundaries() {
    let old = build(false);
    let new = build(true);

    let patches = diff_nodes(&old, &new, &[]);

    // (b) — LOAD-BEARING. Filling earlier boundary bodies must NOT emit a
    // MoveSubtree for the later boundaries. This is the exact #1826 symptom
    // (`if-b-0`, `if-c-0`). FAILS on 1.0.6rc1 (2 spurious moves).
    let later_moves = count(
        &patches,
        |p| matches!(p, Patch::MoveSubtree { id, .. } if id == "if-b-0" || id == "if-c-0"),
    );
    assert_eq!(
        later_moves, 0,
        "spurious MoveSubtree for later dj-if boundaries (#1826); patches: {:#?}",
        patches
    );

    // No boundary should move at all here — none repositioned relative to its
    // siblings. (Pins the broader invariant, not just the two cited ids.)
    let any_move = count(&patches, |p| matches!(p, Patch::MoveSubtree { .. }));
    assert_eq!(
        any_move, 0,
        "no boundary repositioned relative to siblings, so no MoveSubtree expected; patches: {:#?}",
        patches
    );

    // (a) — the in-body inserted detail node is the `<span>` ELEMENT, never a
    // flattened `#text` node. (Defect-1 guard for the non-table container; the
    // bare-`<tbody>` foster-parenting that produces `#text` is documented in
    // the Python test as expected html5ever behavior.)
    for p in &patches {
        if let Patch::InsertChild { node, .. } = p {
            assert_eq!(
                node.tag, "span",
                "inserted body node must be the <span> element, not a flattened #text; patch: {:#?}",
                p
            );
        }
    }
    // Sanity: the fill actually produced the three detail inserts (the test
    // would be vacuous otherwise — the boundary bodies must really have filled).
    let span_inserts = count(
        &patches,
        |p| matches!(p, Patch::InsertChild { node, .. } if node.tag == "span"),
    );
    assert_eq!(
        span_inserts, 3,
        "expected three detail-span inserts (one per filled boundary); patches: {:#?}",
        patches
    );

    // (c) — CLIENT-FAITHFUL. Apply the patch stream to a clone of OLD and
    // assert it reproduces NEW structurally (all 3 rows + 3 details present),
    // and that every targeting handle resolves in the client tracker.
    assert_handles_resolve(&patches, &old, "1826");
    let mut client = old.clone();
    apply_all(&mut client, &patches, &new);
    assert!(
        structurally_equal(&client, &new),
        "client tracker after apply_all must match NEW; got {} top-level children, want {}",
        client.children.len(),
        new.children.len()
    );
    // Both detail spans for b and c must be present after apply (the dropped-row
    // symptom is exactly their absence).
    for n in ITEMS {
        let want = format!("detail {}", n);
        assert!(
            contains_text(&client, &want),
            "row detail '{}' missing after apply_all — this is the dropped-row symptom",
            want
        );
    }
}

#[test]
fn djif_genuine_reposition_still_emits_move_subtree() {
    // GUARD (do-not-over-correct): a REAL element inserted BEFORE a matched,
    // empty boundary IS a genuine reposition (its non-boundary-sibling count
    // goes 0 → 1) and MUST still emit MoveSubtree. Mirrors
    // `test_diff_robustness_gaps::matched_djif_boundary_repositioned_via_move_subtree`.
    let gen = IdGen::new();
    let keep = elem_with_text("p", "keep", &gen);
    let keep_id = keep.djust_id.clone().unwrap();

    let old =
        VNode::element("div").with_children(vec![dj_if_open("if-a"), dj_if_close(), keep.clone()]);
    let new = VNode::element("div").with_children(vec![
        elem_with_text("section", "new", &gen),
        dj_if_open("if-a"),
        dj_if_close(),
        keep,
    ]);

    let patches = diff_nodes(&old, &new, &[]);

    // The matched boundary is NOT torn down/re-inserted — it MOVES.
    assert_eq!(
        count(&patches, |p| matches!(
            p,
            Patch::RemoveSubtree { .. } | Patch::InsertSubtree { .. }
        )),
        0,
        "matched boundary must move, not tear-down/re-insert; patches: {:#?}",
        patches
    );
    assert_eq!(
        count(&patches, |p| matches!(p, Patch::MoveSubtree { id, .. } if id == "if-a")),
        1,
        "genuine reposition (real element inserted before boundary) MUST emit MoveSubtree; patches: {:#?}",
        patches
    );
    // The "keep" node still resolves (untouched).
    assert!(find_by_djust_id(&new, &keep_id).is_some());
}

// ---- local structural helpers (id-agnostic; dj-id attrs differ by design) ----

fn structurally_equal(a: &VNode, b: &VNode) -> bool {
    if a.tag != b.tag || a.text != b.text {
        return false;
    }
    let fa: Vec<(&str, &str)> = a
        .attrs
        .iter()
        .filter(|(k, _)| k.as_str() != "dj-id")
        .map(|(k, v)| (k.as_str(), v.as_str()))
        .collect();
    let fb: Vec<(&str, &str)> = b
        .attrs
        .iter()
        .filter(|(k, _)| k.as_str() != "dj-id")
        .map(|(k, v)| (k.as_str(), v.as_str()))
        .collect();
    // Attribute order is insertion order; both built the same way.
    if fa != fb || a.children.len() != b.children.len() {
        return false;
    }
    a.children
        .iter()
        .zip(&b.children)
        .all(|(x, y)| structurally_equal(x, y))
}

/// True if `text` appears as a `#text` node anywhere in the tree.
fn contains_text(node: &VNode, text: &str) -> bool {
    if node.text.as_deref() == Some(text) {
        return true;
    }
    node.children.iter().any(|c| contains_text(c, text))
}
