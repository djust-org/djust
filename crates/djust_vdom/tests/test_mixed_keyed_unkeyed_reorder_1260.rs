//! Regression test for #1260: VDOM diff/patch round-trip fails on
//! mixed keyed/unkeyed siblings under reorder + count-change.
//!
//! Audit context: docs/vdom/AUDIT-2026-04-30.md weaknesses #5 + #6
//! upgraded from yellow (warnings only) to red (real correctness bug)
//! after the proptest fuzz_test::round_trip_correctness shrunk to the
//! minimal failing case captured below.
//!
//! The bug: when the surviving keyed child is the SOLE entry in its
//! group, the LIS-skip optimisation in `diff_keyed_children` decided
//! it was "in place" because a single-element LIS is trivially of
//! length one. With unkeyed siblings interleaved, the keyed child's
//! absolute position changed but no `MoveChild` was emitted. Other
//! patches (remove/move of unkeyed siblings) couldn't re-position
//! the keyed child, so the round-trip diverged.
//!
//! The fix: when any unkeyed siblings are interleaved with keyed
//! siblings (the mixed case), `diff_keyed_children` always emits
//! `MoveChild` for surviving keyed children whose `old_idx != new_idx`
//! — the LIS-skip optimisation only stays safe in fully-keyed lists.

use djust_vdom::diff::diff_nodes;
use djust_vdom::patch::apply_patches;
use djust_vdom::{Patch, VNode};
use std::collections::HashMap;

/// Structural equality check ignoring djust_id (mirrors fuzz_test.rs helper).
fn structurally_equal(a: &VNode, b: &VNode) -> bool {
    if a.tag != b.tag || a.text != b.text {
        return false;
    }
    let a_attrs: HashMap<String, String> = a
        .attrs
        .iter()
        .filter(|(k, _)| k.as_str() != "dj-id")
        .map(|(k, v)| (k.clone(), v.clone()))
        .collect();
    let b_attrs: HashMap<String, String> = b
        .attrs
        .iter()
        .filter(|(k, _)| k.as_str() != "dj-id")
        .map(|(k, v)| (k.clone(), v.clone()))
        .collect();
    if a_attrs != b_attrs {
        return false;
    }
    if a.children.len() != b.children.len() {
        return false;
    }
    a.children
        .iter()
        .zip(b.children.iter())
        .all(|(ca, cb)| structurally_equal(ca, cb))
}

/// Recursively assign synthetic djust_ids (mirrors fuzz_test.rs helper).
fn assign_ids(node: &mut VNode, counter: &mut u64) {
    node.djust_id = Some(format!("t{}", counter));
    *counter += 1;
    for child in &mut node.children {
        assign_ids(child, counter);
    }
}

/// Build the proptest-shrunk minimal reproducer for #1260.
///
///   tree_a:
///     <section>
///       #text "A"   (unkeyed)
///       #text "A"   (unkeyed)
///       #text "A"   (unkeyed)
///       #text "a"   (unkeyed)
///       <div key="f"/>
///
///   tree_b:
///     <section>
///       <div key="f"/>   (moved to front)
///       #text "A"        (unkeyed)
///       #text "a"        (unkeyed)
///
/// The keyed `<div key="f">` moves from position 4 to position 0 while
/// the count of unkeyed siblings drops from 4 to 2. The diff must
/// emit a `MoveChild` for the keyed `<div>` (or otherwise produce
/// patches that yield a structurally-equal `tree_b`).
fn build_trees() -> (VNode, VNode) {
    let tree_a = VNode::element("section").with_children(vec![
        VNode::text("A"),
        VNode::text("A"),
        VNode::text("A"),
        VNode::text("a"),
        VNode::element("div").with_key("f"),
    ]);

    let tree_b = VNode::element("section").with_children(vec![
        VNode::element("div").with_key("f"),
        VNode::text("A"),
        VNode::text("a"),
    ]);

    (tree_a, tree_b)
}

#[test]
fn issue_1260_mixed_keyed_unkeyed_reorder_round_trip() {
    let (mut tree_a, mut tree_b) = build_trees();

    // Mirror fuzz_test.rs: assign synthetic djust_ids to every node so
    // apply_patches can resolve nodes by id after structural shifts.
    let mut counter = 0u64;
    assign_ids(&mut tree_a, &mut counter);
    assign_ids(&mut tree_b, &mut counter);

    let patches = diff_nodes(&tree_a, &tree_b, &[]);
    let mut applied = tree_a.clone();
    apply_patches(&mut applied, &patches);

    assert!(
        structurally_equal(&applied, &tree_b),
        "round-trip failed for #1260\nA: {:#?}\nB: {:#?}\nPatches: {:#?}\nApplied: {:#?}",
        tree_a,
        tree_b,
        patches,
        applied,
    );
}

#[test]
fn issue_1260_keyed_div_must_emit_move_child() {
    // Asserts the *cause*, not just the symptom: under mixed siblings
    // the surviving keyed `<div key="f">` MUST receive a MoveChild
    // patch. Before the fix, the LIS-skip optimisation suppressed it.
    let (mut tree_a, mut tree_b) = build_trees();

    let mut counter = 0u64;
    assign_ids(&mut tree_a, &mut counter);
    let keyed_old_id = tree_a.children[4].djust_id.clone();
    assign_ids(&mut tree_b, &mut counter);

    let patches = diff_nodes(&tree_a, &tree_b, &[]);

    let move_for_keyed = patches.iter().any(|p| {
        matches!(
            p,
            Patch::MoveChild { child_d, to, .. }
                if child_d == &keyed_old_id && *to == 0
        )
    });
    assert!(
        move_for_keyed,
        "expected MoveChild(child_d={:?}, to=0) for the keyed <div key=\"f\">. \
         Without it the LIS-skip optimisation strands the keyed sibling at the \
         wrong position when unkeyed siblings are interleaved. Patches: {:#?}",
        keyed_old_id, patches,
    );
}

/// Symmetric variant: the keyed child moves from front to BACK while
/// unkeyed siblings shrink. Same bug class, opposite direction.
#[test]
fn issue_1260_keyed_to_back_with_unkeyed_shrink() {
    let mut tree_a = VNode::element("section").with_children(vec![
        VNode::element("div").with_key("k"),
        VNode::text("X"),
        VNode::text("Y"),
        VNode::text("Z"),
    ]);
    let mut tree_b = VNode::element("section")
        .with_children(vec![VNode::text("X"), VNode::element("div").with_key("k")]);

    let mut counter = 0u64;
    assign_ids(&mut tree_a, &mut counter);
    assign_ids(&mut tree_b, &mut counter);

    let patches = diff_nodes(&tree_a, &tree_b, &[]);
    let mut applied = tree_a.clone();
    apply_patches(&mut applied, &patches);

    assert!(
        structurally_equal(&applied, &tree_b),
        "round-trip failed (keyed-to-back).\nA: {:#?}\nB: {:#?}\nPatches: {:#?}\nApplied: {:#?}",
        tree_a,
        tree_b,
        patches,
        applied,
    );
}

/// Two surviving keyed children with unkeyed siblings — both keyed
/// nodes change absolute position even though their RELATIVE order
/// among keyed siblings is preserved (so LIS would skip both of them).
#[test]
fn issue_1260_two_keyed_in_lis_with_unkeyed_shrink() {
    // old: [u-A, u-B, k-1, u-C, k-2]
    // new: [k-1, k-2, u-A]
    // Both keyed nodes remain in the same relative order (k-1 before k-2)
    // — LIS=[0,1] would mark both as "in place" — yet both moved
    // absolute positions.
    let mut tree_a = VNode::element("section").with_children(vec![
        VNode::text("A"),
        VNode::text("B"),
        VNode::element("div").with_key("k1"),
        VNode::text("C"),
        VNode::element("div").with_key("k2"),
    ]);
    let mut tree_b = VNode::element("section").with_children(vec![
        VNode::element("div").with_key("k1"),
        VNode::element("div").with_key("k2"),
        VNode::text("A"),
    ]);

    let mut counter = 0u64;
    assign_ids(&mut tree_a, &mut counter);
    assign_ids(&mut tree_b, &mut counter);

    let patches = diff_nodes(&tree_a, &tree_b, &[]);
    let mut applied = tree_a.clone();
    apply_patches(&mut applied, &patches);

    assert!(
        structurally_equal(&applied, &tree_b),
        "round-trip failed (two keyed in LIS, unkeyed shrink).\n\
         A: {:#?}\nB: {:#?}\nPatches: {:#?}\nApplied: {:#?}",
        tree_a,
        tree_b,
        patches,
        applied,
    );
}
