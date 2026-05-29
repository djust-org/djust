//! Shared test helpers for `djust_vdom` integration tests (#1415).
//!
//! Cargo treats every `tests/*.rs` file as its own crate, so helpers
//! aren't directly importable across files. The convention for sharing
//! is a `tests/common/mod.rs` (a *directory* with `mod.rs`, not a flat
//! `common.rs` — that would compile as its own test target).
//!
//! Test files include via `mod common;` and call into this module:
//!
//! ```ignore
//! mod common;
//! use common::{dj_if_open, dj_if_close, IdGen, find_by_djust_id};
//! ```
//!
//! This module is `#[allow(dead_code)]` because each test file uses
//! only a subset of the helpers; per-file unused-warnings would noise
//! the build.

#![allow(dead_code)]

use djust_vdom::patch::apply_patches;
use djust_vdom::{Patch, VNode};
use std::cell::Cell;

// =============================================================================
// dj-if marker helpers
// =============================================================================

/// Build a `<!--dj-if id="<boundary_id>"-->` open-marker comment node.
pub fn dj_if_open(boundary_id: &str) -> VNode {
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

/// Build a `<!--/dj-if-->` close-marker comment node.
pub fn dj_if_close() -> VNode {
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

/// True if `n` is a `<!--dj-if id="<id>"-->` open marker for the
/// specific boundary id.
pub fn is_dj_if_open(n: &VNode, id: &str) -> bool {
    n.tag == "#comment"
        && n.text
            .as_deref()
            .map(|t| {
                let trimmed = t.trim();
                trimmed.starts_with("dj-if ") && trimmed.contains(&format!("id=\"{}\"", id))
            })
            .unwrap_or(false)
}

/// True if `n` is any dj-if open marker (irrespective of id).
pub fn is_dj_if_open_any(n: &VNode) -> bool {
    n.tag == "#comment"
        && n.text
            .as_deref()
            .map(|t| t.trim().starts_with("dj-if "))
            .unwrap_or(false)
}

/// True if `n` is the `<!--/dj-if-->` close marker.
pub fn is_dj_if_close(n: &VNode) -> bool {
    n.tag == "#comment"
        && n.text
            .as_deref()
            .map(|t| t.trim() == "/dj-if")
            .unwrap_or(false)
}

/// Given the index of an open marker in `siblings`, return the index of
/// its matching close marker (handling nested boundaries via depth-counting).
pub fn match_close_idx(siblings: &[VNode], open_idx: usize) -> Option<usize> {
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

// =============================================================================
// VNode lookup helpers
// =============================================================================

/// Find a node anywhere in the tree by its `djust_id` (immutable).
pub fn find_by_djust_id<'a>(root: &'a VNode, id: &str) -> Option<&'a VNode> {
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

/// Find a node anywhere in the tree by its `djust_id` (mutable).
pub fn find_by_djust_id_mut<'a>(root: &'a mut VNode, id: &str) -> Option<&'a mut VNode> {
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
// dj-if subtree manipulation (used by `apply_all` to model the production
// client's marker-id-keyed subtree dispatch)
// =============================================================================

/// Walk `node`, find a `<!--dj-if id="boundary_id"-->` open marker, then
/// the matching `<!--/dj-if-->` close marker among its siblings (counting
/// nested boundaries). Return the slice of nodes from open through close,
/// inclusive, cloned.
pub fn find_dj_if_subtree(node: &VNode, boundary_id: &str) -> Option<Vec<VNode>> {
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
pub fn remove_dj_if_subtree(node: &mut VNode, boundary_id: &str) {
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

// =============================================================================
// Subtree-aware patch applier (mirrors what the production client does
// with marker-id dispatch, which the crate's `apply_patches` skips)
// =============================================================================

/// Apply every patch variant to a tracker `VNode`. Layered on top of
/// the crate's `apply_patches` to handle `Insert/RemoveSubtree` via
/// boundary-marker lookup.
///
/// `new_vdom` is the source-of-truth from which `InsertSubtree` content
/// is sliced (production parses `InsertSubtree.html`; we look up the
/// matching boundary in `new_vdom` directly to avoid a parser
/// round-trip — see #1416 for the round-trip torture).
pub fn apply_all(client: &mut VNode, patches: &[Patch], new_vdom: &VNode) {
    // Phase 1: RemoveSubtree.
    for p in patches {
        if let Patch::RemoveSubtree { id } = p {
            remove_dj_if_subtree(client, id);
        }
    }

    // Phase 2: InsertSubtree.
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

// =============================================================================
// Cross-render invariant checker
// =============================================================================

/// For each emitted patch, every `djust_id` referenced as a targeting
/// handle (`d`, `child_d`, `ref_d`) must be present in the client
/// tracker. If absent, the production client's
/// `querySelector('[dj-id="X"]')` returns `null` and the patch silently
/// drops, leaving stale content from a prior render.
///
/// `child_d` of `InsertChild` is exempt (it's the id of a brand-new
/// node not yet in the tracker — the patch's purpose is to add it).
///
/// `id` of `Insert/RemoveSubtree` is a boundary marker id (a different
/// namespace), not a `djust_id`, so it's exempt.
pub fn assert_handles_resolve(patches: &[Patch], client: &VNode, ctx: &str) {
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
                if d.is_some() {
                    check_handle(d, client, ctx, i, "SetText.d");
                }
            }
            Patch::Replace { d, .. } => {
                check_handle(d, client, ctx, i, "Replace.d");
            }
            Patch::InsertSubtree { d, .. } => {
                check_handle(d, client, ctx, i, "InsertSubtree.d");
            }
            Patch::RemoveSubtree { .. } => {
                // Boundary id, not a dj-id; verified by `apply_all`
                // panicking if it can't find the marker.
            }
            Patch::MoveSubtree { d, .. } => {
                // Boundary id is a marker namespace (exempt); the parent `d`
                // must resolve in the client tracker, like InsertSubtree.d.
                check_handle(d, client, ctx, i, "MoveSubtree.d");
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

// =============================================================================
// Sequential dj-id generation (mirrors the production base62 counter)
// =============================================================================

/// Sequential base62 dj-id generator. Mirrors the production
/// `next_djust_id()` counter so handcrafted test trees use the same
/// id alphabet/order as a real render.
pub struct IdGen {
    counter: Cell<u64>,
}

impl IdGen {
    pub fn new() -> Self {
        Self {
            counter: Cell::new(0),
        }
    }

    /// Allocate the next id and return it (advances the counter).
    pub fn next(&self) -> String {
        let v = self.counter.get();
        self.counter.set(v + 1);
        base62(v)
    }
}

impl Default for IdGen {
    fn default() -> Self {
        Self::new()
    }
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
// Quick element constructors (id-stamped)
// =============================================================================

/// Build a `<tag>` element with a fresh sequential `dj-id` from `gen`.
pub fn elem(tag: &str, gen: &IdGen) -> VNode {
    let id = gen.next();
    VNode::element(tag)
        .with_djust_id(&id)
        .with_attr("dj-id", &id)
}

/// Build a `<tag>` element with sequential `dj-id` and a `#text` child.
pub fn elem_with_text(tag: &str, text: &str, gen: &IdGen) -> VNode {
    elem(tag, gen).with_child(VNode::text(text))
}

// =============================================================================
// Misc
// =============================================================================

/// Count patches matching `pred`.
pub fn count<F: Fn(&Patch) -> bool>(patches: &[Patch], pred: F) -> usize {
    patches.iter().filter(|p| pred(p)).count()
}
