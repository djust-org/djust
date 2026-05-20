//! Regression / disconfirmation tests for #1552: production reporter
//! observed VDOM doubling when an `{% if %}/{% elif %}` block swaps the
//! `{% include %}` it emits between two renders.
//!
//! ## Status: VDOM-diff layer is NOT the cause
//!
//! These tests pin the diff layer's behavior for the exact shape the
//! reporter described. On current HEAD both tests PASS: the differ
//! correctly emits `InsertSubtree(if-X-1)` for the new nested boundary
//! AND `RemoveChild` (with `child_d`) for every OLD body child. When
//! applied with id-based RemoveChild resolution (production JS semantics
//! at `python/djust/static/djust/src/12-vdom-patch.js:1778-1788`), the
//! live VDOM matches NEW exactly.
//!
//! Hypothesis-disconfirmation status from Stage 5 of the bugfix pipeline
//! for #1552: the plan's primary suspect (Candidate A — #1538's
//! `#[serde(default)]` on `VNode.djust_id`) is empirically ruled out at
//! the diff layer. These tests fail BOTH at v0.9.6rc2 and v1.0.0rc4 when
//! a naive index-only applier is used, and pass BOTH when an id-aware
//! applier is used — proving the regression-window analysis (only one
//! VDOM commit in the window, `0a119962`) does not point at a differ
//! regression.
//!
//! Per the bugfix-pipeline canon (Action #1466 / "gate the change off"
//! self-test), shipping a fix in the diff layer would be premature
//! without a failing test. The remaining suspect surfaces are:
//!
//!   - **WS-event save block (PR #1466, commit `a5e2c50c`)**: writes
//!     `last_vdom` to the session on every WS event. Did not exist
//!     pre-v0.9.6rc2's window in the same shape — may interact with the
//!     msgpack roundtrip such that a *partial* sync_ids state lands in
//!     the next event's `last_vdom`, producing patches the client can't
//!     resolve.
//!   - **Sticky-child persistence (PRs #1526/#1527/#1528, ADR-018)**:
//!     three new persistence layers were added in the window. If the
//!     wizard's parent view holds a sticky child whose `dj-id` namespace
//!     conflicts with the include's content, restore could emit OLD
//!     content under the parent boundary.
//!   - **Pre-#1538 fallback semantics**: pre-fix, `state_backends/
//!     memory.py:118-148` would *log and discard* a view on msgpack
//!     deserialize failure; the next event would remount from scratch
//!     (full HTML, no patches). Reporter's "v0.9.6rc2 works" report is
//!     consistent with this — the bug never manifested because the
//!     state was always re-mounted. #1538 fixed the deserialize, which
//!     means the diff path now runs — exposing a pre-existing bug in
//!     some adjacent surface.
//!
//! Stage 5 reports `STAGE_5_BLOCKED: hypothesis disconfirmed — bug is
//! not at the diff layer; further investigation required at the WS-
//! event-save / sticky-child / msgpack-fallback surfaces.`
//!
//! ## Failure mode (reported)
//!
//! Template:
//! ```django
//! <div class="wizard-content">
//!   {% if current_step_name == "claimant" %}
//!     {% include "intake/steps/mv_step_claimant.html" %}
//!   {% elif current_step_name == "vehicle" %}
//!     {% include "intake/steps/vpd_step_vehicle.html" %}
//!   {% endif %}
//! </div>
//! ```
//!
//! Django parses `{% if A %}{% elif B %}{% endif %}` as
//! `If(A, true: [...claim...], false: [If(B, true: [...vehicle...], false: [])])`.
//! Rust renderer emits keyed `<!--dj-if-->` markers per `Node::If`.
//!
//! Step 1 (name=claim): the OUTER If's TRUE branch fires →
//!   `<!--dj-if id="if-X-0"--><claim include-rendered children...><!--/dj-if-->`
//!
//! Step 2 (name=vehicle): the OUTER If's FALSE branch (nested If) fires →
//!   `<!--dj-if id="if-X-0"--><!--dj-if id="if-X-1"--><vehicle children...><!--/dj-if--><!--/dj-if-->`
//!
//! The outer `if-X-0` boundary is matched in BOTH. `dj_if_pre_pass_inner`
//! recurses into the matched body. Inside:
//!   - OLD body: claim-include's rendered children — multi-rooted (a series of
//!     `<h*>` + `<input>` nodes, NO single wrapper element).
//!   - NEW body: `[<!--dj-if id="if-X-1"-->, vehicle children..., <!--/dj-if-->]`.
//!
//! The recursion identifies a top-level pair in NEW (`if-X-1`) and emits
//! `InsertSubtree(if-X-1)`. Then it pairs NEW's non-boundary children (none,
//! because vehicle is wrapped inside `if-X-1` so all NEW body content is
//! INSIDE the boundary range and gets excluded by `build_excluded_mask`)
//! against OLD's non-boundary children (everything in OLD body, since OLD
//! has no boundary pair). That's `new_kept = []` vs `old_kept = [...]`.
//!
//! Expected: `len(old_kept) > 0` and `len(new_kept) == 0` → emit
//!   `RemoveChild(...)` for every claim-include child (descending).
//!
//! BUG (#1552): if the recursion fails to emit those RemoveChild patches,
//! step-1's content stays in the DOM and step-2's content is inserted ON TOP.
//!
//! ## Test discipline
//!
//! Tests assert post-`apply_all_id_aware` state (production-faithful):
//! claim-include's roots MUST be gone and only vehicle content
//! (inserted via InsertSubtree) should be present inside the outer
//! boundary's parent. Tests PASS on current HEAD — see status note at
//! the top of this file. They function as regression backstops: if a
//! future change breaks the diff for this shape, these tests fail
//! fast and the bug is caught at Stage 6 of the pipeline.

use djust_vdom::diff::diff_nodes;
use djust_vdom::{Patch, VNode};

mod common;
use common::{
    count, dj_if_close, dj_if_open, find_by_djust_id, find_by_djust_id_mut, find_dj_if_subtree,
    remove_dj_if_subtree,
};

// =============================================================================
// Production-faithful patch applier.
//
// The crate-level `apply_patches` (and the `common::apply_all` thin wrapper)
// resolve `RemoveChild` purely by `index`. After an `InsertSubtree` patch
// in the same batch shifts the parent's children list, the OLD indices in
// `RemoveChild` no longer point at the OLD nodes — the index-only applier
// would remove the freshly-inserted subtree instead.
//
// The PRODUCTION JS client at `python/djust/static/djust/src/12-vdom-patch.js
// :1778-1788` resolves `RemoveChild` by `child_d` FIRST (id-based,
// resilient to index shifts), falling back to index only when `child_d`
// is `None`. This applier mirrors that semantics so tests can assert on
// post-apply state instead of patch shape alone.
// =============================================================================

fn apply_all_id_aware(client: &mut VNode, patches: &[Patch], new_vdom: &VNode) {
    // Phase 1: RemoveSubtree (by marker id).
    for p in patches {
        if let Patch::RemoveSubtree { id } = p {
            remove_dj_if_subtree(client, id);
        }
    }

    // Phase 2: InsertSubtree (by marker id; subtree content sourced from
    // new_vdom).
    for p in patches {
        if let Patch::InsertSubtree { id, d, index, .. } = p {
            if let (Some(parent_id), Some(subtree)) = (d.as_ref(), find_dj_if_subtree(new_vdom, id))
            {
                if let Some(target) = find_by_djust_id_mut(client, parent_id) {
                    let insert_at = (*index).min(target.children.len());
                    for (offset, node) in subtree.into_iter().enumerate() {
                        target.children.insert(insert_at + offset, node);
                    }
                }
            }
        }
    }

    // Phase 3: RemoveChild — id-based first, index fallback (production
    // semantics).
    for p in patches {
        if let Patch::RemoveChild {
            d, index, child_d, ..
        } = p
        {
            if let Some(parent_id) = d.as_ref() {
                if let Some(parent) = find_by_djust_id_mut(client, parent_id) {
                    // Try child_d first (mirrors JS `:scope >
                    // [dj-id="..."]`).
                    if let Some(cid) = child_d.as_ref() {
                        if let Some(pos) = parent
                            .children
                            .iter()
                            .position(|c| c.djust_id.as_deref() == Some(cid.as_str()))
                        {
                            parent.children.remove(pos);
                            continue;
                        }
                    }
                    // Fallback: index (in the CURRENT live children list,
                    // which may have shifted post-InsertSubtree — same
                    // failure mode as the production JS fallback).
                    if *index < parent.children.len() {
                        parent.children.remove(*index);
                    }
                }
            }
        }
    }

    // Phase 4: everything else — InsertChild, SetAttr, SetText, Replace.
    for p in patches {
        match p {
            Patch::InsertChild { d, index, node, .. } => {
                if let Some(parent_id) = d.as_ref() {
                    if let Some(parent) = find_by_djust_id_mut(client, parent_id) {
                        let insert_at = (*index).min(parent.children.len());
                        parent.children.insert(insert_at, node.clone());
                    }
                }
            }
            Patch::SetAttr { d, key, value, .. } => {
                if let Some(target_id) = d.as_ref() {
                    if let Some(target) = find_by_djust_id_mut(client, target_id) {
                        target.attrs.insert(key.clone(), value.clone());
                    }
                }
            }
            Patch::SetText { d, text, .. } => {
                if let Some(target_id) = d.as_ref() {
                    if let Some(target) = find_by_djust_id_mut(client, target_id) {
                        target.text = Some(text.clone());
                    }
                }
            }
            Patch::Replace { d, node, .. } => {
                if let Some(target_id) = d.as_ref() {
                    if let Some(target) = find_by_djust_id_mut(client, target_id) {
                        *target = node.clone();
                    }
                }
            }
            _ => {}
        }
    }
}

// =============================================================================
// Builders mirroring the production multi-rooted include (#1552 reporter
// observed 3 headings + 25 inputs for claim, 1 heading + 6 inputs for
// vehicle — both as multi-rooted top-level children of the boundary).
// =============================================================================

fn h(tag: &str, djust_id: &str, text: &str) -> VNode {
    VNode::element(tag)
        .with_djust_id(djust_id)
        .with_attr("dj-id", djust_id)
        .with_child(VNode::text(text))
}

fn input(name: &str, djust_id: &str) -> VNode {
    VNode::element("input")
        .with_djust_id(djust_id)
        .with_attr("dj-id", djust_id)
        .with_attr("type", "text")
        .with_attr("name", name)
}

/// Claim include's rendered children (multi-rooted): 3 headings + 5 inputs.
/// In production this would be 25 inputs; we use 5 for test legibility.
fn claim_include_children() -> Vec<VNode> {
    vec![
        h("h2", "claim-h-1", "About You"),
        h("h3", "claim-h-2", "Your Information"),
        h("h4", "claim-h-3", "Attorney Information"),
        input("first_name", "claim-in-0"),
        input("last_name", "claim-in-1"),
        input("address", "claim-in-2"),
        input("city", "claim-in-3"),
        input("state", "claim-in-4"),
    ]
}

/// Vehicle include's rendered children (multi-rooted): 1 heading + 4 inputs.
fn vehicle_include_children() -> Vec<VNode> {
    vec![
        h("h2", "veh-h-1", "What Happened"),
        input("incident_date", "veh-in-0"),
        input("incident_time", "veh-in-1"),
        input("incident_location", "veh-in-2"),
        input("borough", "veh-in-3"),
    ]
}

// =============================================================================
// Case 1 — `{% if A %}{% include claim %}{% elif B %}{% include vehicle %}
// {% endif %}` flip when the includes' rendered contents are MULTI-ROOTED
// (no wrapping element). This is the production scenario #1552 reports.
//
// OLD (A=true, claim):
//   [open(if-X-0), <h2 claim-h-1>, <h3>, <h4>, <input>×5, close]
//
// NEW (A=false, B=true, vehicle):
//   [open(if-X-0), open(if-X-1), <h2 veh-h-1>, <input>×4, close, close]
//
// `dj_if_pre_pass_inner` finds `if-X-0` matched in both. Recurses into:
//   - OLD body slice: [<h2>, <h3>, <h4>, <input>×5] (8 elements, no boundaries).
//   - NEW body slice: [open(if-X-1), <h2>, <input>×4, close] (7 elements, 1 boundary).
//
// Recursion identifies NEW's top-level pair (if-X-1) → emits
// `InsertSubtree(if-X-1)`. Then the non-boundary pairing:
//   - old_kept = [0..7] (all 8 children of OLD body are non-boundary).
//   - new_kept = [] (everything in NEW body is inside the if-X-1 boundary
//     range and gets excluded by `build_excluded_mask`).
//   - common = 0 → no diff_nodes calls.
//   - old_kept.len() > new_kept.len() → emit 8 RemoveChild patches (descending).
//
// Post-apply: claim's 8 children GONE; vehicle's content (in if-X-1's
// InsertSubtree.html) present.
//
// BUG: if any of the 8 RemoveChild patches fail to resolve (`child_d`
// not in client), the corresponding claim child SURVIVES → doubled
// subtree.
// =============================================================================

#[test]
fn test_include_swap_elif_cascade_multi_rooted_1552() {
    let mut old_children = vec![dj_if_open("if-X-0")];
    old_children.extend(claim_include_children());
    old_children.push(dj_if_close());

    let old = VNode::element("div")
        .with_djust_id("wizard-content")
        .with_attr("class", "wizard-content")
        .with_children(old_children);

    let mut new_children = vec![dj_if_open("if-X-0"), dj_if_open("if-X-1")];
    new_children.extend(vehicle_include_children());
    new_children.push(dj_if_close());
    new_children.push(dj_if_close());

    let new = VNode::element("div")
        .with_djust_id("wizard-content")
        .with_attr("class", "wizard-content")
        .with_children(new_children);

    let patches = diff_nodes(&old, &new, &[]);
    eprintln!("[#1552 elif-cascade patches]: {:#?}", patches);

    // Anti-overlap assertion: no Replace patches.
    assert_eq!(
        count(&patches, |p| matches!(p, Patch::Replace { .. })),
        0,
        "include-swap must NOT emit Replace patches. \
         Got: {:#?}",
        patches
    );

    // Exactly 1 InsertSubtree (for the nested if-X-1).
    assert_eq!(
        count(&patches, |p| matches!(p, Patch::InsertSubtree { .. })),
        1,
        "Expected 1 InsertSubtree(if-X-1). Got: {:#?}",
        patches
    );

    // Every claim-include child must have a matching RemoveChild.
    // claim has 8 children at slice-relative indices [1..=8] in the OLD
    // (1-indexed because of the dj-if-open marker at 0). The absolute
    // index = body_old_offset + slice_index, where body_old_offset is
    // 1 (the position of OLD body slice relative to parent), so
    // abs_old_idx = 1 + i for i in [0..=7]. RemoveChild patches inside
    // the matched body use those absolute indices.
    let claim_child_ids = [
        "claim-h-1",
        "claim-h-2",
        "claim-h-3",
        "claim-in-0",
        "claim-in-1",
        "claim-in-2",
        "claim-in-3",
        "claim-in-4",
    ];
    let removed_ids: Vec<&str> = patches
        .iter()
        .filter_map(|p| match p {
            Patch::RemoveChild { child_d, .. } => child_d.as_deref(),
            _ => None,
        })
        .collect();
    for cid in &claim_child_ids {
        assert!(
            removed_ids.contains(cid),
            "Expected RemoveChild(child_d={}) for claim child. Got \
             removed_ids: {:?}, full patches: {:#?}",
            cid,
            removed_ids,
            patches
        );
    }

    // Apply patches via subtree-aware applier (mirrors production client's
    // marker-id dispatch).
    let mut client = old.clone();
    apply_all_id_aware(&mut client, &patches, &new);

    // CRITICAL: every claim-include child MUST be gone post-apply.
    for cid in &claim_child_ids {
        assert!(
            find_by_djust_id(&client, cid).is_none(),
            "Claim-include child dj-id={} MUST be removed from live VDOM \
             after the include swap — survival is the #1552 double-subtree \
             symptom. Live VDOM: {:#?}\nPatches: {:#?}",
            cid,
            client,
            patches
        );
    }

    // Vehicle-include content MUST be present post-apply (inserted via
    // InsertSubtree of if-X-1).
    for vid in &["veh-h-1", "veh-in-0", "veh-in-1", "veh-in-2", "veh-in-3"] {
        assert!(
            find_by_djust_id(&client, vid).is_some(),
            "Vehicle-include child dj-id={} MUST be present in live VDOM \
             after the include swap. Live VDOM: {:#?}\nPatches: {:#?}",
            vid,
            client,
            patches
        );
    }

    // Strict equality: live VDOM must equal NEW (no leftover, no double).
    assert_eq!(
        client, new,
        "Live VDOM after apply_all must equal NEW. Differ output may be \
         emitting the wrong shape of patches — #1552 root cause. \
         Patches: {:#?}",
        patches
    );
}

// =============================================================================
// Case 2 — narrower form: outer matched-id boundary's body has 3 OLD
// children but NEW body has 0 non-boundary children (everything is inside
// the nested if-X-1). Pins the "all old body children removed" subcase
// in isolation.
// =============================================================================

#[test]
fn test_include_swap_matched_body_all_removed_via_nested_boundary_1552() {
    let old = VNode::element("div")
        .with_djust_id("root")
        .with_children(vec![
            dj_if_open("if-X-0"),
            h("h2", "h-a", "A"),
            h("h3", "h-b", "B"),
            h("h4", "h-c", "C"),
            dj_if_close(),
        ]);

    let new = VNode::element("div")
        .with_djust_id("root")
        .with_children(vec![
            dj_if_open("if-X-0"),
            dj_if_open("if-X-1"),
            h("h2", "v-a", "V-A"),
            dj_if_close(),
            dj_if_close(),
        ]);

    let patches = diff_nodes(&old, &new, &[]);
    eprintln!("[#1552 matched-body-all-removed patches]: {:#?}", patches);

    // Exactly 1 InsertSubtree(if-X-1).
    assert_eq!(
        count(&patches, |p| matches!(p, Patch::InsertSubtree { .. })),
        1,
        "Expected InsertSubtree(if-X-1). Got: {:#?}",
        patches
    );

    // Exactly 3 RemoveChild patches — one per OLD body child.
    let remove_child_count = count(&patches, |p| matches!(p, Patch::RemoveChild { .. }));
    assert_eq!(
        remove_child_count, 3,
        "Expected 3 RemoveChild patches (one per claim child). Got: {:#?}",
        patches
    );

    // Apply + assert.
    let mut client = old.clone();
    apply_all_id_aware(&mut client, &patches, &new);
    for hid in &["h-a", "h-b", "h-c"] {
        assert!(
            find_by_djust_id(&client, hid).is_none(),
            "Claim heading dj-id={} MUST be removed post-apply. Live: {:#?}",
            hid,
            client
        );
    }
    assert!(
        find_by_djust_id(&client, "v-a").is_some(),
        "Vehicle heading v-a MUST be present post-apply. Live: {:#?}",
        client
    );
}
