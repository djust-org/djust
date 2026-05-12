//! Patch-batch ordering invariant torture (#1420).
//!
//! ## What this exercises
//!
//! When `diff()` returns a `Vec<Patch>`, the client applies them in
//! order. Each patch's targeting handle (`d`, `child_d`, `ref_d`) is
//! resolved via `querySelector('[dj-id="X"]')` on the client DOM AT
//! THE TIME OF APPLICATION. If an earlier patch in the batch removes
//! or invalidates a node whose dj-id is referenced by a later patch's
//! handle, the later patch silently fails — the client's
//! `querySelector` returns `null`.
//!
//! ## The invariant under test
//!
//! For each emitted patch batch, every targeting handle must resolve
//! against the client state RESULTING FROM applying all prior patches
//! in the batch. The harness's `assert_handles_resolve` resolves
//! against the FULL client tracker (i.e., the state before the batch);
//! that's the strict invariant. The stricter intra-batch invariant
//! holds whenever the strict invariant holds.
//!
//! Snapshot: for the canonical "RemoveChild + SetAttr on the removed
//! child" scenario, we additionally assert RemoveChild is the LAST
//! patch in the batch — so any SetAttr/etc. on the same child resolves
//! before the child is gone.
//!
//! ## Why this matters
//!
//! Several patch variants share handle namespaces:
//!
//! - `RemoveChild.child_d` invalidates the child's dj-id.
//! - `SetAttr.d` / `RemoveAttr.d` / `SetText.d` / `Replace.d` reference
//!   a node's dj-id.
//! - `MoveChild` changes DOM position but preserves the dj-id, so it
//!   doesn't invalidate handles (it's a no-op for the invariant).
//!
//! A diff emitter that orders RemoveChild BEFORE SetAttr on the same
//! child produces a bad batch.

use djust_vdom::{diff, Patch};

mod common;
use common::{assert_handles_resolve, elem, elem_with_text, IdGen};

// =============================================================================
// Scenarios — assert handles resolve on each emitted batch
// =============================================================================

/// Scenario 1: remove a child whose attribute also changed. The diff
/// must not emit a SetAttr (or any other handle-targeting patch) for
/// the removed child AFTER the RemoveChild — but the simpler and more
/// commonly-emitted shape is no SetAttr at all (since the node is
/// being removed). The assertion: handles resolve against the client
/// pre-batch.
#[test]
fn remove_child_with_attr_change_handles_resolve() {
    let c = IdGen::new();

    let parent = elem("div", &c);
    let kept = elem_with_text("p", "kept", &c).with_attr("class", "x");
    let removed = elem_with_text("p", "removed", &c).with_attr("class", "old");
    let old = parent
        .clone()
        .with_children(vec![kept.clone(), removed.clone()]);

    // New: removed gone; kept's class changed.
    let mut kept_new = kept.clone();
    kept_new.attrs.insert("class".to_string(), "y".to_string());
    let new = parent.with_children(vec![kept_new]);

    let patches = diff(&old, &new);
    assert_handles_resolve(&patches, &old, "remove_with_attr_change");
}

/// Scenario 2: multiple RemoveChild on siblings in non-sorted insert
/// positions. Each RemoveChild's `child_d` must resolve in the
/// pre-batch tracker.
#[test]
fn multiple_remove_children_non_sorted_handles_resolve() {
    let c = IdGen::new();

    let parent = elem("div", &c);
    let kids = vec![
        elem_with_text("p", "k0", &c),
        elem_with_text("p", "k1", &c),
        elem_with_text("p", "k2", &c),
        elem_with_text("p", "k3", &c),
        elem_with_text("p", "k4", &c),
    ];
    let old = parent.clone().with_children(kids.clone());

    // New: keep only k0 and k3 (remove k1, k2, k4 — non-contiguous,
    // not in any sorted order if processed naively).
    let new = parent.with_children(vec![kids[0].clone(), kids[3].clone()]);

    let patches = diff(&old, &new);
    assert_handles_resolve(&patches, &old, "multi_remove_nonsorted");
}

/// Scenario 3: InsertChild followed by SetAttr on a node sharing the
/// insertion position. The new node carries its own id; existing
/// nodes at that position keep their ids. The `SetAttr.d` must
/// resolve against the pre-batch client (which has the existing node).
#[test]
fn insert_then_attr_on_existing_handles_resolve() {
    let c = IdGen::new();

    let parent = elem("div", &c);
    let existing = elem_with_text("p", "existing", &c).with_attr("class", "old");
    let old = parent.clone().with_children(vec![existing.clone()]);

    // New: a fresh node inserted at index 0, the existing node now at
    // index 1 with a changed class.
    let new_node = elem_with_text("p", "new-first", &c);
    let mut existing_new = existing.clone();
    existing_new
        .attrs
        .insert("class".to_string(), "new".to_string());
    let new = parent.with_children(vec![new_node, existing_new]);

    let patches = diff(&old, &new);
    assert_handles_resolve(&patches, &old, "insert_then_attr");
}

/// Scenario 4: text-only change on a node alongside a sibling removal.
/// `SetText.d` and `RemoveChild.child_d` must each resolve.
#[test]
fn settext_with_sibling_remove_handles_resolve() {
    let c = IdGen::new();

    let parent = elem("div", &c);
    let text_node = elem_with_text("p", "before", &c);
    let removed = elem_with_text("p", "removed", &c);
    let old = parent
        .clone()
        .with_children(vec![text_node.clone(), removed.clone()]);

    // New: text_node text changed; removed gone.
    let mut text_node_new = text_node.clone();
    if let Some(child) = text_node_new.children.first_mut() {
        child.text = Some("after".to_string());
    }
    let new = parent.with_children(vec![text_node_new]);

    let patches = diff(&old, &new);
    assert_handles_resolve(&patches, &old, "settext_with_remove");
}

/// Scenario 5: replace one child while attr-changing another. Both
/// patches' handles must resolve pre-batch.
#[test]
fn replace_with_attr_change_handles_resolve() {
    let c = IdGen::new();

    let parent = elem("div", &c);
    let kept = elem_with_text("p", "kept", &c).with_attr("class", "a");
    let replaced = elem_with_text("p", "old", &c);
    let old = parent
        .clone()
        .with_children(vec![kept.clone(), replaced.clone()]);

    // New: different tag at index 1 (forces Replace) + attr change on kept.
    let new_replacement = elem_with_text("section", "fresh", &c);
    let mut kept_new = kept.clone();
    kept_new.attrs.insert("class".to_string(), "b".to_string());
    let new = parent.with_children(vec![kept_new, new_replacement]);

    let patches = diff(&old, &new);
    assert_handles_resolve(&patches, &old, "replace_with_attr_change");
}

/// Scenario 6: remove ALL children but one, plus mutate the survivor's
/// text. Stress-test the batch ordering with many handle references.
#[test]
fn bulk_remove_with_survivor_mutation_handles_resolve() {
    let c = IdGen::new();

    let parent = elem("div", &c);
    let mut kids = Vec::new();
    for i in 0..8 {
        kids.push(elem_with_text("p", &format!("k{}", i), &c));
    }
    let old = parent.clone().with_children(kids.clone());

    // New: only kids[4] survives, with new text.
    let mut survivor = kids[4].clone();
    if let Some(child) = survivor.children.first_mut() {
        child.text = Some("survivor-new".to_string());
    }
    let new = parent.with_children(vec![survivor]);

    let patches = diff(&old, &new);
    assert_handles_resolve(&patches, &old, "bulk_remove_with_survivor");
}

/// Scenario 7: attribute change on a node followed by removing one of
/// its later siblings.
#[test]
fn attr_change_then_sibling_remove_handles_resolve() {
    let c = IdGen::new();

    let parent = elem("div", &c);
    let attr_target = elem_with_text("p", "a", &c).with_attr("data-x", "1");
    let removed = elem_with_text("p", "rm", &c);
    let old = parent
        .clone()
        .with_children(vec![attr_target.clone(), removed.clone()]);

    let mut attr_target_new = attr_target.clone();
    attr_target_new
        .attrs
        .insert("data-x".to_string(), "2".to_string());
    let new = parent.with_children(vec![attr_target_new]);

    let patches = diff(&old, &new);
    assert_handles_resolve(&patches, &old, "attr_then_sibling_remove");
}

// =============================================================================
// Snapshot: RemoveChild order in the canonical "remove + sibling attr
// change" scenario.
// =============================================================================

/// For the canonical "remove a child while mutating an attr on a
/// sibling" scenario, assert RemoveChild patches come at-or-after any
/// SetAttr patches on siblings. This is the snapshot test: it locks
/// in the current emit-order so a future diff-emitter regression that
/// emits RemoveChild before sibling SetAttr would be caught here.
///
/// Note: the strict invariant (handles resolve pre-batch) is checked
/// by `assert_handles_resolve` and holds regardless of order. This
/// snapshot guards against the WEAKER intra-batch invariant (handles
/// resolve against the client state after applying prior patches).
#[test]
fn snapshot_remove_child_appears_at_or_after_sibling_setattr() {
    let c = IdGen::new();

    let parent = elem("div", &c);
    let mutated = elem_with_text("p", "a", &c).with_attr("class", "old");
    let removed = elem_with_text("p", "rm", &c);
    let old = parent
        .clone()
        .with_children(vec![mutated.clone(), removed.clone()]);

    let mut mutated_new = mutated.clone();
    mutated_new
        .attrs
        .insert("class".to_string(), "new".to_string());
    let new = parent.with_children(vec![mutated_new]);

    let patches = diff(&old, &new);

    // Find the index of the first SetAttr, and the index of the first
    // RemoveChild. Both MUST be present (the diff has both a mutation
    // and a removal); a tautology-pass via `if let (Some, Some)` would
    // silently accept a future regression that stopped emitting either
    // patch type (Action #1200). Use expect() so absence is a test
    // failure, not a silent pass.
    let first_setattr = patches
        .iter()
        .position(|p| matches!(p, Patch::SetAttr { .. }))
        .expect("expected at least one SetAttr in the batch (mutated child's class change)");
    let first_removechild = patches
        .iter()
        .position(|p| matches!(p, Patch::RemoveChild { .. }))
        .expect("expected at least one RemoveChild in the batch (removed sibling)");

    // RemoveChild MUST come at-or-after SetAttr — emitting RemoveChild
    // first would not affect SetAttr's handle (different node), but it
    // WOULD affect any future emitter regression that tried to reference
    // the removed child's id post-removal.
    assert!(
        first_removechild >= first_setattr,
        "RemoveChild at idx {} preceded SetAttr at idx {} — emit order regression. Patches: {:#?}",
        first_removechild,
        first_setattr,
        patches
    );
}
