//! Regression tests for #1408: `sync_ids` was not dj-if-boundary-aware.
//!
//! ## Failure mode (pre-fix)
//!
//! `diff::diff_children` already aligned children across dj-if boundary
//! swaps via `dj_if_pre_pass` (#1358 / PR #1365). After the diff produced
//! correct patches, the runtime called `sync_ids(old, new)` to copy old
//! djust_ids onto matched new nodes so that `last_vdom` (the next render's
//! "old" tree) had ids matching the client DOM.
//!
//! `sync_ids` had **no** equivalent dj-if pre-pass. It fell through to
//! `sync_ids_indexed`, which paired children by absolute position. When
//! a dj-if branch swap meant old/new had different content at the same
//! index, fresh new-tree djust_ids were overwritten with stale old-tree
//! ids. The next render's diff then emitted RemoveChild/SetAttr patches
//! whose `child_d`/`d` referenced ids the client DOM never had — patches
//! silently failed, leaving orphan content from the previous branch.
//!
//! Reproduced in a downstream consumer's bottom-tab swap (two
//! sibling `{% if active_tab == "X" %}` blocks rendering different
//! content per tab): clicking through tab A → tab B → tab A left
//! content from tab B sitting inside the tab A render.
//!
//! ## Fix
//!
//! Add a parallel `sync_ids_dj_if_pre_pass` to `diff::sync_ids` so the
//! ID-sync path matches the diff path's boundary semantics:
//!   - id in OLD only → skip (subtree is being removed)
//!   - id in NEW only → skip (subtree is brand-new; keep fresh ids)
//!   - id in BOTH → recurse `sync_ids` into the matched body
//!   - non-boundary siblings → pair by RELATIVE order among non-boundary
//!     siblings (mirrors `dj_if_pre_pass`'s `build_excluded_mask` logic).

use djust_vdom::diff::sync_ids;
use djust_vdom::VNode;

fn dj_if_open(id: &str) -> VNode {
    VNode {
        tag: "#comment".to_string(),
        attrs: Default::default(),
        children: Vec::new(),
        text: Some(format!("dj-if id=\"{}\"", id)),
        key: None,
        djust_id: None,
        cached_html: None,
    }
}

fn dj_if_close() -> VNode {
    VNode {
        tag: "#comment".to_string(),
        attrs: Default::default(),
        children: Vec::new(),
        text: Some("/dj-if".to_string()),
        key: None,
        djust_id: None,
        cached_html: None,
    }
}

/// Case 1: branch swap — id in OLD only + id in NEW only.
///
/// OLD had dj-if A active wrapping `<div A-content/>`. NEW has dj-if B
/// active wrapping `<div B-content/>`. The new content's fresh djust_id
/// must NOT be overwritten with the old branch's id.
#[test]
fn test_branch_swap_keeps_fresh_id_for_unmatched_new_boundary() {
    let old = VNode::element("div")
        .with_djust_id("parent")
        .with_children(vec![
            dj_if_open("if-X-A"),
            VNode::element("div")
                .with_djust_id("OLD_A_CONTENT_ID")
                .with_attr("dj-id", "OLD_A_CONTENT_ID"),
            dj_if_close(),
        ]);

    let mut new = VNode::element("div")
        .with_djust_id("parent")
        .with_children(vec![
            dj_if_open("if-X-B"),
            VNode::element("div")
                .with_djust_id("FRESH_B_CONTENT_ID")
                .with_attr("dj-id", "FRESH_B_CONTENT_ID"),
            dj_if_close(),
        ]);

    sync_ids(&old, &mut new);

    // The B-content's fresh id must survive — it's inside a NEW-only
    // boundary, so there's no semantic match in the old tree.
    let b_content = &new.children[1];
    assert_eq!(
        b_content.djust_id.as_deref(),
        Some("FRESH_B_CONTENT_ID"),
        "Content inside an unmatched-new dj-if boundary must keep its \
         fresh djust_id (not be overwritten by positional alignment with \
         the old branch). Got: {:?}",
        b_content.djust_id
    );
    assert_eq!(
        b_content.attrs.get("dj-id").map(|s| s.as_str()),
        Some("FRESH_B_CONTENT_ID"),
        "dj-id attr must also stay fresh"
    );
}

/// Case 2: branch-only-content inside an unmatched boundary stays
/// untouched even when a positional `sync_ids_indexed` would have
/// happily synced ids onto it.
///
/// All elements are `<div>` so positional pairing IS tag-compatible
/// pre-fix — that's what makes this a real regression catch (test 1
/// happened to use different tags, which would early-return on tag
/// mismatch even without the fix).
///
/// Pre-fix `sync_ids_indexed` walks abs index 0..=2 of each children
/// list and `sync_ids`-pairs:
///   old[0]=div(OLD_OUTSIDE) ↔ new[0]=div(FRESH_OUTSIDE)
///   old[1]=#comment(open A) ↔ new[1]=#comment(open B)   (both djust_id=None)
///   old[2]=div(OLD_INSIDE_A) ↔ new[2]=div(FRESH_INSIDE_B)  ← BUG: tag matches → ID gets stolen
///
/// Post-fix the boundary pre-pass:
/// - non-boundary siblings (old[0], new[0]) pair by relative order →
///   OLD_OUTSIDE syncs onto new[0] (correct: same logical position)
/// - id-only-in-OLD `if-X-A` → skip, no syncing
/// - id-only-in-NEW `if-X-B` → skip, fresh ids preserved
/// - new[2] keeps FRESH_INSIDE_B
#[test]
fn test_branch_swap_with_non_boundary_siblings_keeps_fresh_ids() {
    let old = VNode::element("div")
        .with_djust_id("parent")
        .with_children(vec![
            VNode::element("div")
                .with_djust_id("OLD_OUTSIDE")
                .with_attr("dj-id", "OLD_OUTSIDE"),
            dj_if_open("if-X-A"),
            VNode::element("div")
                .with_djust_id("OLD_INSIDE_A")
                .with_attr("dj-id", "OLD_INSIDE_A"),
            dj_if_close(),
        ]);

    let mut new = VNode::element("div")
        .with_djust_id("parent")
        .with_children(vec![
            VNode::element("div")
                .with_djust_id("FRESH_OUTSIDE")
                .with_attr("dj-id", "FRESH_OUTSIDE"),
            dj_if_open("if-X-B"),
            VNode::element("div")
                .with_djust_id("FRESH_INSIDE_B")
                .with_attr("dj-id", "FRESH_INSIDE_B"),
            dj_if_close(),
        ]);

    sync_ids(&old, &mut new);

    // Outside-boundary sibling: pairs by relative order, syncs the old id.
    // (This is the desired behavior — old[0] and new[0] are logically the
    // same position in their respective renders.)
    assert_eq!(
        new.children[0].djust_id.as_deref(),
        Some("OLD_OUTSIDE"),
        "Non-boundary sibling outside any boundary should sync from old"
    );
    assert_eq!(
        new.children[0].attrs.get("dj-id").map(|s| s.as_str()),
        Some("OLD_OUTSIDE"),
    );

    // Inside an unmatched-NEW boundary: fresh id must survive even though
    // positional alignment WOULD have a tag-compatible match available.
    // This is the regression-catching assertion.
    assert_eq!(
        new.children[2].djust_id.as_deref(),
        Some("FRESH_INSIDE_B"),
        "Content inside an unmatched-NEW dj-if boundary must keep its \
         fresh id even when a tag-compatible old sibling sits at the same \
         absolute index. Pre-fix this returned 'OLD_INSIDE_A'."
    );
    assert_eq!(
        new.children[2].attrs.get("dj-id").map(|s| s.as_str()),
        Some("FRESH_INSIDE_B"),
    );
}

/// Case 3: matched boundary in BOTH — sync_ids should recurse into the
/// matched body (so stable ids inside a stable branch carry over). Only
/// the unmatched-branch case should bypass syncing.
#[test]
fn test_matched_boundary_recurses_and_syncs_inner_ids() {
    let old = VNode::element("div")
        .with_djust_id("parent")
        .with_children(vec![
            dj_if_open("if-stable-A"),
            VNode::element("p")
                .with_djust_id("OLD_INNER_ID")
                .with_attr("dj-id", "OLD_INNER_ID")
                .with_child(VNode::text("body content")),
            dj_if_close(),
        ]);

    let mut new = VNode::element("div")
        .with_djust_id("parent")
        .with_children(vec![
            dj_if_open("if-stable-A"),
            VNode::element("p")
                .with_djust_id("NEW_INNER_ID_TO_BE_OVERWRITTEN")
                .with_attr("dj-id", "NEW_INNER_ID_TO_BE_OVERWRITTEN")
                .with_child(VNode::text("body content")),
            dj_if_close(),
        ]);

    sync_ids(&old, &mut new);

    // Boundary id is the SAME in both → matched-body recursion → inner
    // node's id syncs from old.
    let inner = &new.children[1];
    assert_eq!(
        inner.djust_id.as_deref(),
        Some("OLD_INNER_ID"),
        "Matched boundary should recurse and sync inner ids"
    );
    assert_eq!(
        inner.attrs.get("dj-id").map(|s| s.as_str()),
        Some("OLD_INNER_ID"),
    );
}

/// Case 4: end-to-end shape — diff produces patches against last_vdom,
/// then sync_ids updates last_vdom's IDs to match the client DOM. After
/// the swap, the recorded last_vdom's ids must match the ids the client
/// actually has (which were emitted in the prior diff's patches).
///
/// Mirrors the production failure: confirm that after the dj-if
/// branch swap, the new_vdom (which becomes the next round's
/// last_vdom) carries the ids the client just got via InsertChild —
/// not stale ids from positional alignment with the prior branch.
#[test]
fn test_post_swap_last_vdom_ids_match_client_dom() {
    // Simulates: client DOM has tasks-panel with dj-id "FRESH_5m" because
    // the prior diff emitted InsertChild with that id. last_vdom (post-
    // sync_ids) MUST also report the tasks-panel's djust_id as "FRESH_5m"
    // so the next diff's RemoveChild emits child_d="FRESH_5m" (matches
    // client DOM) instead of a stale id.

    let prev_old = VNode::element("div")
        .with_djust_id("tab-content")
        .with_children(vec![
            dj_if_open("if-DOCS"),
            VNode::element("div")
                .with_djust_id("STALE_OLD_ID")
                .with_attr("dj-id", "STALE_OLD_ID"),
            dj_if_close(),
        ]);

    let mut new_after_swap = VNode::element("div")
        .with_djust_id("tab-content")
        .with_children(vec![
            dj_if_open("if-NOTES"),
            VNode::element("div")
                .with_djust_id("FRESH_5m")
                .with_attr("class", "tasks-panel")
                .with_attr("dj-id", "FRESH_5m"),
            dj_if_close(),
        ]);

    sync_ids(&prev_old, &mut new_after_swap);

    // What gets stored as `last_vdom` for the NEXT render. The id at
    // children[1] must remain FRESH_5m — that's what the client DOM has.
    let stored = &new_after_swap.children[1];
    assert_eq!(
        stored.djust_id.as_deref(),
        Some("FRESH_5m"),
        "After a dj-if branch swap, sync_ids must NOT overwrite the new \
         branch's content ids with ids from the unmatched-old branch. \
         Pre-fix this returned 'STALE_OLD_ID' — see #1408."
    );
}
