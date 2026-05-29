//! Regression tests closing coverage gaps surfaced by differential testing of
//! the clean-room `diff.rs` rebuild against the original implementation.
//!
//! Each gap is a corner the existing suite did NOT pin, where the rebuild
//! diverged from (and was less robust than) the original:
//!
//! 1. Duplicate `key` siblings — `torture_duplicate_keys` only asserts
//!    no-panic + non-empty, never a round-trip. A last-wins key map can match
//!    two new nodes onto one old node, emitting a corrupting `Replace`.
//! 2. Mixed keyed/unkeyed 3-way reorder — `#1260` covers single/two-keyed
//!    cases but not keyed+keyed+unkeyed where an UNKEYED node's absolute
//!    position changes (it must get a `MoveChild`, resolved by djust_id).
//! 3. `InsertChild.ref_d` — no test pins that the diff populates the
//!    reference-sibling id, so the client can `insertBefore` by id.

use djust_vdom::diff::diff_nodes;
use djust_vdom::patch::apply_patches;
use djust_vdom::{Patch, VNode};
use std::collections::HashMap;

// ---- helpers (mirror test_mixed_keyed_unkeyed_reorder_1260.rs) ----

fn structurally_equal(a: &VNode, b: &VNode) -> bool {
    if a.tag != b.tag || a.text != b.text {
        return false;
    }
    let fa: HashMap<&str, &str> = a
        .attrs
        .iter()
        .filter(|(k, _)| k.as_str() != "dj-id")
        .map(|(k, v)| (k.as_str(), v.as_str()))
        .collect();
    let fb: HashMap<&str, &str> = b
        .attrs
        .iter()
        .filter(|(k, _)| k.as_str() != "dj-id")
        .map(|(k, v)| (k.as_str(), v.as_str()))
        .collect();
    if fa != fb || a.children.len() != b.children.len() {
        return false;
    }
    a.children
        .iter()
        .zip(&b.children)
        .all(|(x, y)| structurally_equal(x, y))
}

fn find_by_id<'a>(root: &'a VNode, id: &str) -> Option<&'a VNode> {
    if root.djust_id.as_deref() == Some(id) {
        return Some(root);
    }
    root.children.iter().find_map(|c| find_by_id(c, id))
}

fn el(tag: &str, id: &str) -> VNode {
    VNode::element(tag).with_djust_id(id).with_attr("dj-id", id)
}
fn el_text(tag: &str, id: &str, text: &str) -> VNode {
    el(tag, id).with_child(VNode::text(text))
}
fn dj_if_open(id: &str) -> VNode {
    let mut n = VNode::element("#comment");
    n.tag = "#comment".to_string();
    n.text = Some(format!("dj-if id=\"{}\"", id));
    n
}
fn dj_if_close() -> VNode {
    let mut n = VNode::element("#comment");
    n.tag = "#comment".to_string();
    n.text = Some("/dj-if".to_string());
    n
}

fn round_trip(old: &VNode, new: &VNode) -> VNode {
    let patches = diff_nodes(old, new, &[]);
    let mut applied = old.clone();
    apply_patches(&mut applied, &patches);
    applied
}

// =============================================================================
// Gap 1: duplicate keys must still round-trip (no corrupting Replace).
// =============================================================================

#[test]
fn dup_key_siblings_different_tags_round_trip() {
    // Two siblings share key "dup" but have DIFFERENT tags. A last-wins key
    // map matches both new nodes onto the last old node -> cross-tag Replace,
    // duplicating one node and dropping the other. Must round-trip instead.
    let old = el("div", "root").with_children(vec![
        el_text("h1", "A", "x").with_key("dup"),
        el_text("p", "B", "y").with_key("dup"),
    ]);
    let new = el("div", "root").with_children(vec![
        el_text("h1", "A2", "x").with_key("dup"),
        el_text("p", "B2", "y").with_key("dup"),
    ]);

    let patches = diff_nodes(&old, &new, &[]);
    let applied = round_trip(&old, &new);
    assert!(
        structurally_equal(&applied, &new),
        "duplicate-key round-trip failed.\nNEW: {:#?}\nAPPLIED: {:#?}\nPATCHES: {:#?}",
        new,
        applied,
        patches
    );
}

#[test]
fn dup_key_noop_rerender_with_boundary_emits_no_patches() {
    // Structurally-identical re-render (fresh ids), two siblings share key
    // "dup", and a dj-if boundary is present. The diff must recognize the
    // no-op and emit ZERO patches — not a spurious Replace/Move. (seed-3948
    // class from the differential study.)
    let build = |suffix: &str, bid: &str| {
        el("ul", &format!("u{}", suffix)).with_children(vec![
            dj_if_open(bid),
            el("ul", &format!("b{}", suffix)),
            dj_if_close(),
            el_text("h1", &format!("h{}", suffix), "head").with_key("dup"),
            el_text("li", &format!("l{}", suffix), "item").with_key("dup"),
        ])
    };
    // Same boundary id in both => matched boundary; structurally identical.
    let old = build("0", "if-keep");
    let new = build("1", "if-keep");

    let patches = diff_nodes(&old, &new, &[]);
    let applied = round_trip(&old, &new);
    assert!(
        structurally_equal(&applied, &new),
        "dup-key+boundary round-trip failed.\nPATCHES: {:#?}\nAPPLIED: {:#?}",
        patches,
        applied
    );
    assert!(
        patches.is_empty(),
        "structurally-identical re-render with duplicate keys must emit 0 patches, got: {:#?}",
        patches
    );
}

#[test]
fn dup_key_same_tag_swap_round_trip() {
    // Two same-tag, same-key siblings whose content swaps. Must round-trip to
    // the swapped content regardless of how the ambiguous key is resolved.
    let old = el("ul", "root").with_children(vec![
        el_text("li", "A", "alpha").with_key("k"),
        el_text("li", "B", "beta").with_key("k"),
    ]);
    let new = el("ul", "root").with_children(vec![
        el_text("li", "A2", "beta").with_key("k"),
        el_text("li", "B2", "alpha").with_key("k"),
    ]);
    let applied = round_trip(&old, &new);
    assert!(
        structurally_equal(&applied, &new),
        "dup-key same-tag swap round-trip failed.\nAPPLIED: {:#?}",
        applied
    );
}

// =============================================================================
// Gap 2: mixed keyed/unkeyed 3-way reorder — the unkeyed node must move.
// =============================================================================

#[test]
fn mixed_three_way_reorder_round_trip() {
    // old: [a(key ka), b(key kb), #text]   new: [#text, b(key kb), a(key ka)]
    // The unkeyed #text moves from index 2 to 0; a moves 0->2; b stays at 1.
    // The rebuild previously left the unkeyed node stranded at its old
    // relative position because it never emitted a MoveChild for unkeyed
    // siblings in the keyed path.
    let old = el("div", "root").with_children(vec![
        el_text("a", "A", "anchor").with_key("ka"),
        el_text("span", "B", "bee").with_key("kb"),
        VNode::text("hello").with_djust_id("T"),
    ]);
    let new = el("div", "root").with_children(vec![
        VNode::text("hello").with_djust_id("T2"),
        el_text("span", "B2", "bee").with_key("kb"),
        el_text("a", "A2", "anchor").with_key("ka"),
    ]);
    let patches = diff_nodes(&old, &new, &[]);
    let applied = round_trip(&old, &new);
    assert!(
        structurally_equal(&applied, &new),
        "mixed 3-way reorder round-trip failed.\nNEW: {:#?}\nAPPLIED: {:#?}\nPATCHES: {:#?}",
        new,
        applied,
        patches
    );
}

// =============================================================================
// Gap 3: InsertChild.ref_d must be populated (and resolve in OLD).
// =============================================================================

#[test]
fn keyed_insert_ref_d_is_safe_not_a_wrong_guess() {
    // Insert a fresh keyed child BEFORE an existing keyed sibling.
    //
    // The client (12-vdom-patch.js) HONORS InsertChild.ref_d — it does
    // `insertBefore(node, querySelector(':scope > [dj-id=ref_d]'))` — and only
    // falls back to the index when ref_d is absent/unresolved. InsertChild is
    // applied LAST (after removes/moves/subtrees settle the structure), so the
    // index fallback is in the new-frame and is reliable.
    //
    // Therefore the diff must NOT emit a *positional guess* for ref_d: a wrong
    // ref_d (e.g. `old.get(new_idx)`) is the wrong insert-before reference under
    // reorder and mis-inserts on the real client. The safe contract is: ref_d is
    // either None (rely on the reliable index) OR a djust_id present in the OLD
    // tree that is genuinely the following sibling — never a fresh/new-only id.
    // See scratch/vdom-rebuild/ANALYSIS_REPORT.md §10.
    let old = el("ul", "root").with_children(vec![el_text("li", "B", "b").with_key("kb")]);
    let new = el("ul", "root").with_children(vec![
        el_text("li", "A", "a").with_key("ka"),
        el_text("li", "B2", "b").with_key("kb"),
    ]);

    let patches = diff_nodes(&old, &new, &[]);
    let ref_d = patches
        .iter()
        .find_map(|p| match p {
            Patch::InsertChild { node, ref_d, .. } if node.key.as_deref() == Some("ka") => {
                Some(ref_d.clone())
            }
            _ => None,
        })
        .expect("expected an InsertChild for the new keyed <li key=ka>");

    // INVARIANT (#1408): if ref_d is set it must resolve in the OLD tree —
    // never a fresh new-only id. (Currently the diff emits None and relies on
    // the index, which the client applies last; this guards either choice.)
    if let Some(rid) = &ref_d {
        assert!(
            find_by_id(&old, rid).is_some(),
            "ref_d {:?} must resolve to a node present in the OLD tree (#1408), patches: {:#?}",
            rid,
            patches
        );
    }

    // And the whole thing must round-trip.
    let applied = round_trip(&old, &new);
    assert!(
        structurally_equal(&applied, &new),
        "keyed insert round-trip failed.\nAPPLIED: {:#?}",
        applied
    );
}

// =============================================================================
// KNOWN LIMITATION (documented, NOT fixed): a matched dj-if boundary cannot be
// repositioned. dj-if markers are `#comment` nodes with NO `djust_id`, and the
// patch protocol has no `MoveSubtree`. So when an UNKEYED sibling is inserted
// before a matched boundary, the id-resolved content relocates correctly but
// the boundary's comment markers stay anchored at their old position — the
// applied tree diverges from `new`.
//
// This is a PRE-EXISTING limitation shared with the original implementation
// (the differential study found both fail identically here — see
// scratch/vdom-rebuild/ANALYSIS_REPORT.md §8). It accounts for ~all of the
// residual round-trip failures (~0.65% of an adversarial random-mutation
// corpus; near-zero in real templates, where conditionals sit at stable
// positions).
//
// This test PINS the current (limited) behavior so the limitation is explicit
// and any drift is caught. A proper fix needs a wire-protocol change
// (`MoveSubtree`, or position-aware Remove/InsertSubtree for matched
// boundaries) coordinated with the JS client — out of scope for the diff
// rebuild. WHEN THAT FIX LANDS, the two `KNOWN-LIMITATION` assertions below
// will fail; flip them to `structurally_equal(&applied, &new)`.
// =============================================================================

#[test]
fn known_limitation_matched_djif_boundary_not_repositioned() {
    // old: <div>[ <!--if-a--><!--/if-a-->, <p keep> ]
    // new: <div>[ <section new>, <!--if-a--><!--/if-a-->, <p keep> ]
    // i.e. a <section> is inserted BEFORE the (matched, empty) boundary.
    let old = el("div", "root").with_children(vec![
        dj_if_open("if-a"),
        dj_if_close(),
        el_text("p", "keep", "x"),
    ]);
    let new = el("div", "root").with_children(vec![
        el_text("section", "new", "y"),
        dj_if_open("if-a"),
        dj_if_close(),
        el_text("p", "keep", "x"),
    ]);

    // The boundary is matched in both → no subtree-flip is emitted (correct).
    let patches = diff_nodes(&old, &new, &[]);
    assert_eq!(
        patches
            .iter()
            .filter(|p| matches!(p, Patch::RemoveSubtree { .. } | Patch::InsertSubtree { .. }))
            .count(),
        0,
        "matched boundary must not emit a subtree flip, got: {:#?}",
        patches
    );

    let applied = round_trip(&old, &new);

    // Sanity: in NEW a real element precedes the boundary marker.
    assert_eq!(new.children[0].tag, "section");
    assert!(new.children[1].is_comment());

    // KNOWN-LIMITATION #1: after apply, the open marker is STILL anchored at
    // the front — the inserted <section> could not be placed before it.
    assert!(
        applied.children[0].is_comment(),
        "KNOWN LIMITATION changed: the dj-if marker is no longer anchored at \
         the front — if a MoveSubtree-style fix landed, flip this test to assert \
         structurally_equal(&applied, &new). APPLIED: {:#?}",
        applied
    );

    // KNOWN-LIMITATION #2: consequently the round-trip does NOT reproduce `new`.
    assert!(
        !structurally_equal(&applied, &new),
        "KNOWN LIMITATION appears FIXED (matched dj-if boundary repositioned \
         correctly). Flip both assertions in this test to assert the round-trip \
         now succeeds. APPLIED: {:#?}",
        applied
    );
}
