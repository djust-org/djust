//! Three-way interaction: `dj-update="ignore"` subtree inside a
//! `dj-if` boundary that swaps (#1417).
//!
//! ## What this exercises
//!
//! Three orthogonal mechanisms interacting in one tree:
//!
//! 1. `dj-if` boundary swaps — boundary-id-keyed Insert/RemoveSubtree
//!    patches (#1358).
//! 2. `dj-update="ignore"` subtrees — diff is supposed to emit ZERO
//!    patches targeting nodes inside the ignored subtree, since the
//!    splice step replaces new-tree children with old-tree children
//!    (preserving ids and cached HTML) before the diff runs (#1252).
//! 3. `sync_ids` after the diff — the `last_vdom` mirror must end up
//!    matching the client tracker after every cycle.
//!
//! ## The invariant under test
//!
//! After a boundary swap that removes the body containing the ignore
//! subtree, then a swap back that re-inserts it, the dj-ids of nodes
//! inside the ignore subtree must be either:
//!
//! - re-stamped fresh on the re-insert (the body came in via
//!   `InsertSubtree.html`, so the client adopts the fresh ids
//!   transmitted with the HTML), OR
//! - preserved unchanged when the ignore subtree is the only mutation
//!   between renders (the splice step kicks in).
//!
//! Either way, the `assert_handles_resolve` invariant must hold on
//! every emitted patch.
//!
//! ## Why this combo can break
//!
//! `dj-update="ignore"` semantics are "this subtree never changes
//! across renders, splice the old children in and emit no patches".
//! When the surrounding context flips it out of the tree entirely
//! (via `RemoveSubtree`), the cached HTML and dj-ids on the ignore
//! subtree refer to a node the client no longer has. If a subsequent
//! diff round resurfaces those ids as targeting handles, the patches
//! drop silently. This file pins that the patches don't drop and the
//! tree stays in sync.

use djust_vdom::diff::sync_ids;
use djust_vdom::{diff, Patch, VNode};

mod common;
use common::{
    apply_all, assert_handles_resolve, dj_if_close, dj_if_open, elem, elem_with_text,
    find_by_djust_id, IdGen,
};

// =============================================================================
// Cycle helper — matches the production loop
// =============================================================================

fn run_cycle(
    label: &str,
    last_vdom: &mut VNode,
    client: &mut VNode,
    new_vdom: VNode,
) -> Vec<Patch> {
    let mut new_vdom = new_vdom;

    let patches = diff(last_vdom, &new_vdom);
    assert_handles_resolve(&patches, client, label);
    apply_all(client, &patches, &new_vdom);

    sync_ids(last_vdom, &mut new_vdom);
    *last_vdom = new_vdom;
    patches
}

// =============================================================================
// Helpers
// =============================================================================

/// Build a child `dj-update="ignore"` subtree with several inner
/// elements stamped with sequential dj-ids.
fn ignore_subtree(c: &IdGen) -> VNode {
    elem("aside", c)
        .with_attr("dj-update", "ignore")
        .with_children(vec![
            elem_with_text("p", "static one", c),
            elem_with_text("p", "static two", c),
            elem_with_text("p", "static three", c),
        ])
}

/// Recursively collect every `djust_id` present in the tree.
fn collect_ids(node: &VNode, out: &mut Vec<String>) {
    if let Some(id) = node.djust_id.as_ref() {
        out.push(id.clone());
    }
    for child in &node.children {
        collect_ids(child, out);
    }
}

// =============================================================================
// Scenarios
// =============================================================================

/// Scenario 1: ignore subtree is INSIDE a dj-if body. Swap the boundary
/// OUT (RemoveSubtree), then back IN (InsertSubtree). After the swap-in,
/// the client tracker must be consistent with last_vdom — handles for
/// the next diff round must resolve.
#[test]
fn ignore_inside_dj_if_swap_out_and_in() {
    let c = IdGen::new();

    fn build(active: bool, c: &IdGen) -> VNode {
        let parent = elem("div", c);
        let mut children = vec![elem_with_text("h2", "Header", c)];
        if active {
            children.push(dj_if_open("if-with-ignore"));
            children.push(elem_with_text("div", "active body", c));
            children.push(ignore_subtree(c));
            children.push(dj_if_close());
        } else {
            children.push(dj_if_open("if-with-ignore"));
            children.push(dj_if_close());
        }
        children.push(elem_with_text("footer", "Stable Footer", c));
        parent.with_children(children)
    }

    let initial = build(true, &c);
    let mut last_vdom = initial.clone();
    let mut client = initial;

    // Swap OUT (boundary body removed)
    run_cycle("swap_out", &mut last_vdom, &mut client, build(false, &c));
    // Swap BACK IN (boundary body re-inserted with fresh ids)
    run_cycle("swap_in", &mut last_vdom, &mut client, build(true, &c));
    // One more no-op-shape cycle to verify subsequent diff handles resolve
    run_cycle(
        "swap_in_again_idempotent",
        &mut last_vdom,
        &mut client,
        build(true, &c),
    );
}

/// Scenario 2: ignore subtree as a SIBLING of a swapping boundary
/// (so the ignore subtree stays in the tree across the swap; its
/// children must keep their ids and the splice step must keep the diff
/// emitting zero patches into the ignore subtree).
#[test]
fn ignore_sibling_of_swapping_dj_if() {
    let c = IdGen::new();

    fn build(branch: char, c: &IdGen) -> VNode {
        let parent = elem("div", c);
        let mut children = vec![
            ignore_subtree(c),
            dj_if_open(&format!("if-branch-{}", branch)),
        ];
        match branch {
            'A' => children.push(elem_with_text("div", "branch A body", c)),
            'B' => children.push(elem_with_text("section", "branch B body", c)),
            _ => unreachable!(),
        }
        children.push(dj_if_close());
        parent.with_children(children)
    }

    let initial = build('A', &c);

    // Snapshot the ignore-subtree dj-ids BEFORE any swap.
    let mut original_ignore_ids = Vec::new();
    collect_ids(&initial.children[0], &mut original_ignore_ids);
    assert!(
        !original_ignore_ids.is_empty(),
        "ignore subtree must have stamped ids in the initial tree"
    );

    let mut last_vdom = initial.clone();
    let mut client = initial;

    for branch in ['B', 'A', 'B', 'A'] {
        let patches = run_cycle(
            &format!("sibling_swap→{}", branch),
            &mut last_vdom,
            &mut client,
            build(branch, &c),
        );

        // Assert no patch targets a node inside the ignore subtree.
        // The ignore-subtree's ids must NOT appear as `d` or `child_d`
        // on any emitted patch.
        for (i, patch) in patches.iter().enumerate() {
            let touched = match patch {
                Patch::SetAttr { d, .. }
                | Patch::RemoveAttr { d, .. }
                | Patch::Replace { d, .. } => d.clone(),
                Patch::SetText { d, .. } => d.clone(),
                Patch::RemoveChild { d, child_d, .. } | Patch::MoveChild { d, child_d, .. } => {
                    // Either parent or child being inside the ignore tree is a hit.
                    if let Some(id) = d {
                        if original_ignore_ids.contains(id) {
                            panic!(
                                "[{}] patch[{}] {:?} targets a node inside the ignore subtree (d={})",
                                branch, i, patch, id
                            );
                        }
                    }
                    child_d.clone()
                }
                Patch::InsertChild { d, .. } => d.clone(),
                _ => None,
            };
            if let Some(id) = touched {
                assert!(
                    !original_ignore_ids.contains(&id),
                    "[{}] patch[{}] {:?} targets ignore-subtree id {}",
                    branch,
                    i,
                    patch,
                    id
                );
            }
        }
    }

    // After the swap dance, every original ignore-subtree dj-id must
    // still be present in the client tracker (the ignore semantics
    // preserved them through splice).
    for id in &original_ignore_ids {
        assert!(
            find_by_djust_id(&client, id).is_some(),
            "ignore-subtree id {} disappeared from client tracker after sibling swaps",
            id
        );
    }
}

/// Scenario 3: two ignore subtrees, one INSIDE the swapping boundary
/// body and one OUTSIDE (sibling). After OUT/IN swaps, ids of the
/// OUTSIDE-ignore subtree must persist; ids of the INSIDE-ignore
/// subtree may legitimately change (it was removed + re-inserted with
/// fresh ids from `InsertSubtree.html`).
#[test]
fn two_ignore_subtrees_inside_and_outside_dj_if() {
    let c = IdGen::new();

    fn build(active: bool, c: &IdGen) -> VNode {
        let parent = elem("div", c);
        let outside_ignore = elem("aside", c)
            .with_attr("dj-update", "ignore")
            .with_children(vec![elem_with_text("p", "outside-static", c)]);
        let mut children = vec![outside_ignore];
        children.push(dj_if_open("if-with-inside-ignore"));
        if active {
            children.push(elem_with_text("div", "inner-body", c));
            children.push(
                elem("aside", c)
                    .with_attr("dj-update", "ignore")
                    .with_children(vec![elem_with_text("p", "inside-static", c)]),
            );
        }
        children.push(dj_if_close());
        parent.with_children(children)
    }

    let initial = build(true, &c);
    let mut outside_ids = Vec::new();
    collect_ids(&initial.children[0], &mut outside_ids);

    let mut last_vdom = initial.clone();
    let mut client = initial;

    run_cycle(
        "two_ignore_swap_out",
        &mut last_vdom,
        &mut client,
        build(false, &c),
    );
    run_cycle(
        "two_ignore_swap_in",
        &mut last_vdom,
        &mut client,
        build(true, &c),
    );
    run_cycle(
        "two_ignore_extra_cycle",
        &mut last_vdom,
        &mut client,
        build(true, &c),
    );

    // Outside-ignore ids must still resolve in the client tracker.
    for id in &outside_ids {
        assert!(
            find_by_djust_id(&client, id).is_some(),
            "outside ignore-subtree id {} lost across boundary swaps",
            id
        );
    }
}
