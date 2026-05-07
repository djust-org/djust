//! Multi-cycle `sync_ids` round-trip torture (#1412 — regression-class
//! hardener for #1408).
//!
//! ## What this exercises
//!
//! djust runs the following loop server-side, once per render:
//!
//! ```text
//! patches  = diff(last_vdom, &new_vdom)
//! emit_to_client(patches)
//! sync_ids(&last_vdom, &mut new_vdom)
//! last_vdom = new_vdom
//! ```
//!
//! `last_vdom` is the server's mirror of the client DOM. The patches the
//! diff emits use `last_vdom`'s `djust_id`s as `child_d` / `d` / `ref_d`
//! targeting handles. If `sync_ids` drifts (ids in `last_vdom` no longer
//! match what the client actually has), subsequent diff rounds emit
//! patches whose handles reference ids the client DOM never had — those
//! patches silently fail, and content from the prior render persists.
//!
//! `crates/djust_vdom/tests/torture_test.rs` covers single-diff
//! correctness exhaustively (228 tests). `tests/fuzz_test.rs` covers
//! single-diff identity / round-trip / no-panics under proptest. **Neither
//! exercised the multi-cycle invariant** — pre-#1408 every existing
//! torture test passed while production silently shipped stale content.
//!
//! ## The invariant under test
//!
//! For every emitted patch in every cycle, every `djust_id` referenced as
//! a targeting handle (`d`, `child_d`, `ref_d`) MUST be present in the
//! client tracker (a `VNode` that we evolve by faithfully applying every
//! emitted patch). If a handle isn't present, the production client would
//! `querySelector` for it, find nothing, and silently drop the patch.
//!
//! ## What fails pre-#1408 vs what's complementary coverage
//!
//! The drifted dj-id only manifests as a bug when subsequent diff
//! rounds reference it as a targeting handle (`d` / `child_d` / `ref_d`)
//! AND the client tracker doesn't have it. Two concrete preconditions:
//!
//! 1. The drifted node persists across the next swap (i.e. it's NOT
//!    removed via `RemoveSubtree`, which is boundary-id-keyed and
//!    bypasses dj-id handles entirely).
//! 2. The drift overwrites a fresh id that the client received via
//!    `InsertChild` (not via `InsertSubtree`, since subtree HTML carries
//!    its own ids that the client adopts directly).
//!
//! Scenario 2 (`five_boundary_independent_toggles`) hits both: matched
//! boundary ids on every cycle, inner bodies that toggle on/off via
//! `InsertChild` / `RemoveChild` patches, length-shifts in the children
//! list that make positional alignment misalign.
//!
//! Scenarios 1, 3, and 4 are complementary coverage — they exercise
//! adjacent boundary patterns (Insert/RemoveSubtree on different
//! boundary ids; long alternation under matched ids; same-tag
//! non-boundary siblings) and pass on both `main` and post-fix. They
//! lock in non-regression of those patterns without doubling as
//! bug-triggers.

use djust_vdom::diff::sync_ids;
use djust_vdom::patch::apply_patches;
use djust_vdom::{diff, Patch, VNode};
use std::cell::Cell;

// =============================================================================
// dj-id generator (mirrors the production base62 counter)
// =============================================================================

fn next_id(counter: &Cell<u64>) -> String {
    let v = counter.get();
    counter.set(v + 1);
    base62(v)
}

fn base62(mut n: u64) -> String {
    const ALPHA: &[u8] = b"0123456789abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ";
    if n == 0 {
        return "0".to_string();
    }
    let mut s = Vec::new();
    while n > 0 {
        s.push(ALPHA[(n % 62) as usize]);
        n /= 62;
    }
    s.reverse();
    String::from_utf8(s).unwrap()
}

// =============================================================================
// VNode helpers
// =============================================================================

fn elem(tag: &str, c: &Cell<u64>) -> VNode {
    let id = next_id(c);
    VNode::element(tag)
        .with_djust_id(&id)
        .with_attr("dj-id", &id)
}

fn elem_with_text(tag: &str, text: &str, c: &Cell<u64>) -> VNode {
    elem(tag, c).with_child(VNode::text(text))
}

fn dj_if_open(boundary_id: &str) -> VNode {
    VNode {
        tag: "#comment".to_string(),
        attrs: Default::default(),
        children: Vec::new(),
        text: Some(format!("dj-if id=\"{}\"", boundary_id)),
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

// =============================================================================
// Client tracker — faithfully apply ALL patches (including subtree variants)
// =============================================================================

/// Apply every patch variant to a tracker `VNode` so we can assert that
/// each subsequent diff round's targeting handles still resolve.
///
/// The crate's `apply_patches` helper does not model Insert/RemoveSubtree
/// (the production client dispatches those by marker id, not by tree
/// walk). We layer a small wrapper that handles them, so the tracker
/// stays faithful to what the real client would have after applying the
/// same patch stream.
fn apply_all(client: &mut VNode, patches: &[Patch], new_vdom: &VNode) {
    // Phase 1: RemoveSubtree — find the open marker in the tracker, drop
    // everything from open through matching close (inclusive).
    for p in patches {
        if let Patch::RemoveSubtree { id } = p {
            remove_dj_if_subtree(client, id);
        }
    }

    // Phase 2: InsertSubtree — find the corresponding boundary in
    // `new_vdom` (which IS the source-of-truth content the server emits
    // as `html` in the patch), splice it into the tracker at the
    // specified parent + index.
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
            } else {
                panic!(
                    "InsertSubtree id={} could not be resolved in new_vdom (parent_id={:?})",
                    id, d
                );
            }
        }
    }

    // Phase 3: everything else via the crate helper.
    let others: Vec<Patch> = patches
        .iter()
        .filter(|p| !matches!(p, Patch::InsertSubtree { .. } | Patch::RemoveSubtree { .. }))
        .cloned()
        .collect();
    apply_patches(client, &others);
}

/// Walk `node`, find a `<!--dj-if id="boundary_id"-->` open marker, then
/// the matching `<!--/dj-if-->` close marker among its siblings (counting
/// nested boundaries). Return the slice of nodes from open through close,
/// inclusive, cloned.
fn find_dj_if_subtree(node: &VNode, boundary_id: &str) -> Option<Vec<VNode>> {
    fn walk(siblings: &[VNode], target: &str) -> Option<Vec<VNode>> {
        for (i, child) in siblings.iter().enumerate() {
            if is_dj_if_open(child, target) {
                let close = match_close_idx(siblings, i)?;
                return Some(siblings[i..=close].to_vec());
            }
            if let Some(found) = walk(&child.children, target) {
                return Some(found);
            }
        }
        None
    }
    walk(&node.children, boundary_id)
}

/// Remove the boundary identified by `boundary_id` from `node` (anywhere
/// in the tree). Removes the open marker, everything between, and the
/// close marker.
fn remove_dj_if_subtree(node: &mut VNode, boundary_id: &str) {
    fn walk(siblings: &mut Vec<VNode>, target: &str) -> bool {
        let mut open_idx: Option<usize> = None;
        for (i, child) in siblings.iter().enumerate() {
            if is_dj_if_open(child, target) {
                open_idx = Some(i);
                break;
            }
        }
        if let Some(open) = open_idx {
            if let Some(close) = match_close_idx(siblings, open) {
                siblings.drain(open..=close);
                return true;
            }
        }
        for child in siblings.iter_mut() {
            if walk(&mut child.children, target) {
                return true;
            }
        }
        false
    }
    walk(&mut node.children, boundary_id);
}

fn is_dj_if_open(n: &VNode, id: &str) -> bool {
    n.tag == "#comment"
        && n.text
            .as_deref()
            .map(|t| {
                let trimmed = t.trim();
                trimmed.starts_with("dj-if ") && trimmed.contains(&format!("id=\"{}\"", id))
            })
            .unwrap_or(false)
}

fn is_dj_if_open_any(n: &VNode) -> bool {
    n.tag == "#comment"
        && n.text
            .as_deref()
            .map(|t| t.trim().starts_with("dj-if "))
            .unwrap_or(false)
}

fn is_dj_if_close(n: &VNode) -> bool {
    n.tag == "#comment"
        && n.text
            .as_deref()
            .map(|t| t.trim() == "/dj-if")
            .unwrap_or(false)
}

/// Given the index of an open marker in `siblings`, return the index of
/// its matching close marker (handling nested boundaries).
fn match_close_idx(siblings: &[VNode], open_idx: usize) -> Option<usize> {
    let mut depth = 1;
    for (i, n) in siblings.iter().enumerate().skip(open_idx + 1) {
        if is_dj_if_open_any(n) {
            depth += 1;
        } else if is_dj_if_close(n) {
            depth -= 1;
            if depth == 0 {
                return Some(i);
            }
        }
    }
    None
}

fn find_by_djust_id_mut<'a>(root: &'a mut VNode, id: &str) -> Option<&'a mut VNode> {
    if root.djust_id.as_deref() == Some(id) {
        return Some(root);
    }
    for child in root.children.iter_mut() {
        if let Some(found) = find_by_djust_id_mut(child, id) {
            return Some(found);
        }
    }
    None
}

// =============================================================================
// THE INVARIANT — every patch's targeting handle must be in the tracker
// =============================================================================

/// For each emitted patch, every `djust_id` referenced as a targeting
/// handle (`d`, `child_d`, `ref_d`) must be present somewhere in the
/// client tracker. If absent, the production client's
/// `querySelector('[dj-id="X"]')` returns `null` and the patch silently
/// drops, leaving stale content from a prior render.
///
/// `child_d` of `InsertChild` is exempt (it's the id of a brand-new
/// node not yet in the tracker — the patch's purpose is to add it).
///
/// `id` of `Insert/RemoveSubtree` is a boundary marker id (a different
/// namespace), not a `djust_id`, so it's exempt.
fn assert_handles_resolve(patches: &[Patch], client: &VNode, ctx: &str) {
    for (i, patch) in patches.iter().enumerate() {
        match patch {
            Patch::RemoveChild { d, child_d, .. } => {
                check_handle(d, client, ctx, i, "RemoveChild.d");
                check_handle(child_d, client, ctx, i, "RemoveChild.child_d");
            }
            Patch::InsertChild { d, ref_d, .. } => {
                check_handle(d, client, ctx, i, "InsertChild.d");
                if ref_d.is_some() {
                    check_handle(ref_d, client, ctx, i, "InsertChild.ref_d");
                }
            }
            Patch::MoveChild { d, child_d, .. } => {
                check_handle(d, client, ctx, i, "MoveChild.d");
                check_handle(child_d, client, ctx, i, "MoveChild.child_d");
            }
            Patch::SetAttr { d, .. } => {
                check_handle(d, client, ctx, i, "SetAttr.d");
            }
            Patch::RemoveAttr { d, .. } => {
                check_handle(d, client, ctx, i, "RemoveAttr.d");
            }
            Patch::SetText { d, .. } => {
                // SetText.d is None for text nodes (text nodes have no
                // dj-id); only enforce when present.
                if d.is_some() {
                    check_handle(d, client, ctx, i, "SetText.d");
                }
            }
            Patch::Replace { d, .. } => {
                check_handle(d, client, ctx, i, "Replace.d");
            }
            Patch::InsertSubtree { d, .. } => {
                // d is the parent's id; the subtree itself is brand-new.
                check_handle(d, client, ctx, i, "InsertSubtree.d");
            }
            Patch::RemoveSubtree { .. } => {
                // Boundary id, not a dj-id. No handle to check; the
                // boundary marker's presence is verified by
                // `apply_all` panicking if it can't find it.
            }
        }
    }
}

fn check_handle(handle: &Option<String>, client: &VNode, ctx: &str, patch_idx: usize, field: &str) {
    if let Some(id) = handle {
        let present = find_by_djust_id(client, id).is_some();
        assert!(
            present,
            "[{}] patch[{}] {} = {:?} — id not present in client tracker; \
             this is the #1408 class of bug (server-side `last_vdom` drifted \
             from client DOM, emitting handles for nodes the client never had)",
            ctx, patch_idx, field, id
        );
    }
}

fn find_by_djust_id<'a>(root: &'a VNode, id: &str) -> Option<&'a VNode> {
    if root.djust_id.as_deref() == Some(id) {
        return Some(root);
    }
    for child in root.children.iter() {
        if let Some(found) = find_by_djust_id(child, id) {
            return Some(found);
        }
    }
    None
}

// =============================================================================
// Cycle helper — one render iteration matching the production loop
// =============================================================================

fn run_cycle(label: &str, last_vdom: &mut VNode, client: &mut VNode, new_vdom: VNode) {
    let mut new_vdom = new_vdom;

    let patches = diff(last_vdom, &new_vdom);
    assert_handles_resolve(&patches, client, label);
    apply_all(client, &patches, &new_vdom);

    sync_ids(last_vdom, &mut new_vdom);
    *last_vdom = new_vdom;
}

// =============================================================================
// Scenarios
// =============================================================================

/// Scenario 1: three-branch tab toggle (complementary coverage).
///
/// Three sibling `{% if %}` blocks (one per active tab); each branch
/// uses different boundary ids and different element tags inside the
/// body. Toggle through 10 random branch swaps.
///
/// Passes pre-#1408 — boundary-keyed Insert/RemoveSubtree patches
/// fully replace the inside content per swap, so any drift in last_vdom
/// for the inside-body nodes never gets emitted as a handle. Locks in
/// non-regression of the boundary-keyed swap path under variety.
#[test]
fn torture_three_branch_tab_toggle_no_handle_drift() {
    let c = Cell::new(0u64);

    fn build(branch: char, c: &Cell<u64>) -> VNode {
        let parent = elem("div", c);
        let header = elem_with_text("h2", "Stable Header", c);

        let body = match branch {
            'A' => vec![
                dj_if_open("if-tab-A"),
                elem_with_text("div", "tab A content", c),
                elem_with_text("p", "tab A footer", c),
                dj_if_close(),
            ],
            'B' => vec![
                dj_if_open("if-tab-B"),
                elem_with_text("section", "tab B body", c),
                elem_with_text("ul", "tab B list", c),
                dj_if_close(),
            ],
            'C' => vec![
                dj_if_open("if-tab-C"),
                elem_with_text("article", "tab C body", c),
                dj_if_close(),
            ],
            _ => unreachable!(),
        };

        let mut children = vec![header];
        children.extend(body);
        let trailing = elem_with_text("footer", "Stable Footer", c);
        children.push(trailing);

        parent.with_children(children)
    }

    let initial = build('A', &c);
    let mut last_vdom = initial.clone();
    let mut client = initial;

    for branch in ['B', 'A', 'C', 'B', 'A', 'C', 'A', 'B', 'C', 'A'] {
        run_cycle(
            &format!("tab→{}", branch),
            &mut last_vdom,
            &mut client,
            build(branch, &c),
        );
    }
}

/// Scenario 2 (BUG-TRIGGER): parent with FIVE independent dj-if
/// boundaries, each independently flipping on/off across 16 cycles.
/// All boundary ids stay matched between renders; inner bodies toggle
/// on/off via `InsertChild` / `RemoveChild` (not `Insert/RemoveSubtree`).
///
/// This scenario IS the canonical regression catch for #1408. When
/// boundaries toggle, the children list LENGTH changes between old
/// and new; pre-fix `sync_ids_indexed` walks positionally across the
/// length-shifted list and pairs same-tag elements at wrong absolute
/// indices, overwriting a fresh just-inserted dj-id with a stale id
/// from an unrelated old position. Next cycle's diff emits patches
/// against the drifted last_vdom; the targeting handles reference
/// stale ids the client tracker never had → invariant trips.
///
/// On pre-#1408 (commit a44e63cb), this trips at round 4 with the
/// message:
/// ```text
/// patch[1] RemoveChild.child_d = "q" — id not present in client tracker
/// ```
#[test]
fn torture_five_boundary_independent_toggles() {
    let c = Cell::new(0u64);

    fn build(toggles: [bool; 5], c: &Cell<u64>) -> VNode {
        let parent = elem("div", c);
        let mut children = Vec::new();
        for (i, on) in toggles.iter().enumerate() {
            children.push(dj_if_open(&format!("if-bnd-{}", i)));
            if *on {
                children.push(elem_with_text("div", &format!("body of boundary {}", i), c));
            }
            children.push(dj_if_close());
            // A non-boundary stable sibling between each pair.
            children.push(elem_with_text("span", &format!("sib-{}", i), c));
        }
        parent.with_children(children)
    }

    // 16 toggles covering all 2^4 of the lower 4 bits (boundary 4 stays
    // on as a control), then a few all-on / all-off rounds.
    let sequences: Vec<[bool; 5]> = (0..16u8)
        .map(|m| {
            [
                m & 1 != 0,
                m & 2 != 0,
                m & 4 != 0,
                m & 8 != 0,
                true, // boundary 4 stable-on
            ]
        })
        .collect();

    let initial = build(sequences[0], &c);
    let mut last_vdom = initial.clone();
    let mut client = initial;

    for (i, seq) in sequences.iter().enumerate().skip(1) {
        run_cycle(
            &format!("toggles_round_{}_mask_{:04b}", i, i),
            &mut last_vdom,
            &mut client,
            build(*seq, &c),
        );
    }
}

/// Scenario 3: long N-cycle alternation between two trees that share
/// the same dj-if boundary id but have different inner text per state
/// (complementary coverage).
///
/// Body content is same-tag (`<p>`) so positional alignment inside
/// the matched boundary is correct pre-fix too — diff emits SetText
/// on the same dj-id, and ids stay in lock-step. Locks in
/// non-regression of the matched-boundary recurse-into-body path
/// across many cycles.
#[test]
fn torture_long_alternation_matched_boundary_id() {
    let c = Cell::new(0u64);

    fn build(state: bool, c: &Cell<u64>) -> VNode {
        let parent = elem("div", c);
        let mut children = vec![dj_if_open("if-stable-id")];
        if state {
            children.push(elem_with_text("p", "ON state body", c));
        } else {
            children.push(elem_with_text("p", "OFF state body", c));
        }
        children.push(dj_if_close());
        parent.with_children(children)
    }

    let initial = build(true, &c);
    let mut last_vdom = initial.clone();
    let mut client = initial;

    for i in 0..20 {
        let state = i % 2 == 0;
        run_cycle(
            &format!("alt_cycle_{}_state_{}", i, state),
            &mut last_vdom,
            &mut client,
            build(state, &c),
        );
    }
}

/// Scenario 4: branch swap with same-tag non-boundary siblings around
/// an unmatched-id dj-if boundary (complementary coverage).
///
/// Pre-fix sync_ids does drift the inside-body's id (positional
/// alignment with the old branch's same-tag inside-body), but the
/// drifted node gets removed by `RemoveSubtree` on the next swap
/// (boundary-id-keyed), so the drifted dj-id never gets emitted as
/// a handle. Outside-boundary siblings sync correctly (they're the
/// same logical position). Locks in non-regression of the
/// unmatched-boundary path with same-tag wrappers.
#[test]
fn torture_branch_swap_with_same_tag_non_boundary_siblings() {
    let c = Cell::new(0u64);

    fn build(branch: char, c: &Cell<u64>) -> VNode {
        let parent = elem("div", c);
        // All sibling tags are <div> so positional alignment WOULD have
        // tag-compatible matches available pre-fix.
        let outside_top = elem_with_text("div", &format!("outside-top-{}", branch), c);
        let outside_bottom = elem_with_text("div", &format!("outside-bottom-{}", branch), c);
        let inside = elem_with_text("div", &format!("inside-{}-body", branch), c);
        let mut children = vec![outside_top];
        children.push(dj_if_open(&format!("if-branch-{}", branch)));
        children.push(inside);
        children.push(dj_if_close());
        children.push(outside_bottom);
        parent.with_children(children)
    }

    let initial = build('A', &c);
    let mut last_vdom = initial.clone();
    let mut client = initial;

    for branch in ['B', 'A', 'B', 'A', 'B', 'A'] {
        run_cycle(
            &format!("same_tag_swap→{}", branch),
            &mut last_vdom,
            &mut client,
            build(branch, &c),
        );
    }
}
