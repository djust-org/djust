//! Virtual DOM diffing.
//!
//! Computes a minimal `Vec<Patch>` transforming an OLD `VNode` tree into a NEW
//! one, and a companion `sync_ids` pass that carries stable `djust_id`s forward
//! from the old tree onto the new tree after a render.
//!
//! Key behaviors (reconstructed from the test suite):
//! - Element nodes are matched positionally (or by `key` when keyed).
//! - `{% if %}` conditional subtrees are wrapped by `<!--dj-if id="X"-->` /
//!   `<!--/dj-if-->` comment markers and matched by boundary id (keyed
//!   subtree diff): id-only-in-old -> `RemoveSubtree`, id-only-in-new ->
//!   `InsertSubtree`, id-in-both -> recurse into the body. Non-boundary
//!   siblings around a boundary are paired by RELATIVE position so a
//!   boundary span-length change never cascades into mis-targeted paths.
//! - Every targeting handle (`d`/`child_d`/`ref_d`) an emitted patch carries
//!   refers to a node present in the OLD tree (#1408 invariant); the only
//!   exceptions are `InsertChild.node` and `InsertSubtree.html` content.
//!
//! In scope: [`crate::Patch`], [`crate::VNode`],
//! [`crate::lis::longest_increasing_subsequence`], the `vdom_trace!` macro,
//! and `ahash` maps.

use crate::lis::longest_increasing_subsequence;
use crate::vdom_trace;
use crate::{Patch, VNode};
use ahash::{AHashMap, AHashSet};

// ============================================================================
// dj-if boundary marker helpers
// ============================================================================

/// If `node` is a dj-if OPEN marker (`<!--dj-if id="X"-->`), return its
/// boundary id `X`. A bare `<!--dj-if-->` legacy placeholder (no id) is NOT a
/// keyed boundary and returns `None`.
fn dj_if_open_id(node: &VNode) -> Option<String> {
    if !node.is_comment() {
        return None;
    }
    let text = node.text.as_deref()?;
    let trimmed = text.trim();
    // Must be `dj-if` followed by whitespace (space/tab), then an id="..." attr.
    let after = trimmed.strip_prefix("dj-if")?;
    if !after.starts_with(' ') && !after.starts_with('\t') {
        return None;
    }
    // Extract id="..." value.
    let key = "id=\"";
    let start = after.find(key)? + key.len();
    let rest = &after[start..];
    let end = rest.find('"')?;
    Some(rest[..end].to_string())
}

/// True if `node` is a dj-if CLOSE marker (`<!--/dj-if-->`).
fn is_dj_if_close(node: &VNode) -> bool {
    node.is_comment() && node.text.as_deref().map(|t| t.trim()) == Some("/dj-if")
}

/// A top-level dj-if boundary within a children slice: the open-marker local
/// index, the matching close-marker local index, and the boundary id.
#[derive(Debug, Clone)]
struct Boundary {
    id: String,
    open: usize,
    close: usize,
}

/// Scan `children` for TOP-LEVEL dj-if boundary pairs (depth-counted so nested
/// boundaries are skipped — only the outermost pair at this level is returned).
/// Also returns a mask marking every index that falls inside any boundary range
/// (open through close, inclusive), so callers can pair the remaining
/// non-boundary siblings by relative position.
fn find_top_level_boundaries(children: &[VNode]) -> (Vec<Boundary>, Vec<bool>) {
    let mut boundaries = Vec::new();
    let mut excluded = vec![false; children.len()];
    let mut i = 0;
    while i < children.len() {
        if let Some(id) = dj_if_open_id(&children[i]) {
            // Find matching close via depth counting.
            let mut depth = 1usize;
            let mut j = i + 1;
            let mut close = None;
            while j < children.len() {
                if dj_if_open_id(&children[j]).is_some() {
                    depth += 1;
                } else if is_dj_if_close(&children[j]) {
                    depth -= 1;
                    if depth == 0 {
                        close = Some(j);
                        break;
                    }
                }
                j += 1;
            }
            if let Some(close) = close {
                for e in excluded.iter_mut().take(close + 1).skip(i) {
                    *e = true;
                }
                boundaries.push(Boundary { id, open: i, close });
                i = close + 1;
                continue;
            }
        }
        i += 1;
    }
    (boundaries, excluded)
}

/// Serialize a boundary's full marker-pair span (open..=close, inclusive) to
/// HTML for `InsertSubtree.html`.
fn serialize_boundary_html(children: &[VNode], open: usize, close: usize) -> String {
    let mut html = String::new();
    for child in &children[open..=close] {
        html.push_str(&child.to_html());
    }
    html
}

// ============================================================================
// Node kind compatibility
// ============================================================================

/// Two nodes are "compatible" for positional pairing when they are either both
/// comments or both non-comments. A comment vs non-comment pairing is handled
/// as remove + insert (so a legacy `<!--dj-if-->` placeholder being replaced by
/// a real element emits InsertChild/RemoveChild, not Replace — #295).
fn positionally_compatible(a: &VNode, b: &VNode) -> bool {
    a.is_comment() == b.is_comment()
}

// ============================================================================
// Public: diff_nodes
// ============================================================================

/// Compute the patches transforming `old` into `new`. `path` is the index path
/// from the diff root to the node pair being compared; emitted patches for this
/// node use `path`, and child patches use `path + [child_index]`.
pub fn diff_nodes(old: &VNode, new: &VNode, path: &[usize]) -> Vec<Patch> {
    let mut patches = Vec::new();
    diff_node_into(old, new, path, &mut patches);
    patches
}

fn diff_node_into(old: &VNode, new: &VNode, path: &[usize], out: &mut Vec<Patch>) {
    // Tag mismatch (including #text vs element, element vs #text) -> Replace.
    if old.tag != new.tag {
        vdom_trace!("Replace at {:?}: <{}> -> <{}>", path, old.tag, new.tag);
        out.push(Patch::Replace {
            path: path.to_vec(),
            d: old.djust_id.clone(),
            node: new.clone(),
        });
        return;
    }

    // Text / comment nodes: compare text content.
    if new.is_text() || new.is_comment() {
        if old.text != new.text {
            out.push(Patch::SetText {
                path: path.to_vec(),
                d: old.djust_id.clone(),
                text: new.text.clone().unwrap_or_default(),
            });
        }
        return;
    }

    // Same-tag element: diff attributes on this node first (so survivor
    // mutations precede child removals in the emitted Vec — #1420 ordering).
    diff_attrs(old, new, path, out);

    // dj-update="ignore": the new node's interior is preserved verbatim from
    // the old render (server splices old children in before diffing). Emit no
    // child patches — the subtree is treated as unchanged. (#1252 / #1417)
    if new.attrs.get("dj-update").map(String::as_str) == Some("ignore") {
        return;
    }

    // data-djust-replace: clear-and-fill the children wholesale.
    if old.attrs.contains_key("data-djust-replace") && new.attrs.contains_key("data-djust-replace")
    {
        diff_children_replace_mode(old, new, path, out);
        return;
    }

    // General child reconciliation (handles dj-if boundaries + keyed/unkeyed).
    diff_children(
        &old.children,
        &new.children,
        0,
        0,
        path,
        old.djust_id.as_deref(),
        out,
    );
}

// ============================================================================
// Attribute diff
// ============================================================================

fn diff_attrs(old: &VNode, new: &VNode, path: &[usize], out: &mut Vec<Patch>) {
    // Deterministic order: sort keys.
    let mut new_keys: Vec<&String> = new.attrs.keys().collect();
    new_keys.sort();
    for key in new_keys {
        // `dj-id` is an id artifact, never diffed as a normal attribute.
        if key == "dj-id" {
            continue;
        }
        let new_val = &new.attrs[key];
        match old.attrs.get(key) {
            Some(old_val) if old_val == new_val => {}
            _ => {
                out.push(Patch::SetAttr {
                    path: path.to_vec(),
                    d: old.djust_id.clone(),
                    key: key.clone(),
                    value: new_val.clone(),
                });
            }
        }
    }

    let mut old_keys: Vec<&String> = old.attrs.keys().collect();
    old_keys.sort();
    for key in old_keys {
        if key == "dj-id" {
            continue;
        }
        // dj-* event bindings must never be removed (client-side handlers).
        if key.starts_with("dj-") {
            continue;
        }
        if !new.attrs.contains_key(key) {
            out.push(Patch::RemoveAttr {
                path: path.to_vec(),
                d: old.djust_id.clone(),
                key: key.clone(),
            });
        }
    }
}

// ============================================================================
// data-djust-replace mode
// ============================================================================

fn diff_children_replace_mode(old: &VNode, new: &VNode, path: &[usize], out: &mut Vec<Patch>) {
    let d = old.djust_id.clone();
    // Remove all old children, descending index (so earlier indices stay valid).
    for i in (0..old.children.len()).rev() {
        out.push(Patch::RemoveChild {
            path: path.to_vec(),
            d: d.clone(),
            index: i,
            child_d: old.children[i].djust_id.clone(),
        });
    }
    // Insert all new children, ascending index.
    for (i, child) in new.children.iter().enumerate() {
        out.push(Patch::InsertChild {
            path: path.to_vec(),
            d: d.clone(),
            index: i,
            node: child.clone(),
            ref_d: None,
        });
    }
}

// ============================================================================
// Child reconciliation (dj-if boundaries + keyed/unkeyed)
// ============================================================================

/// Reconcile two children slices. `old`/`new` are slices; `old_off`/`new_off`
/// are the ABSOLUTE indices of `old[0]`/`new[0]` within the diff parent's full
/// children vector (so emitted indices/paths are parent-absolute even when this
/// is a recursive call over a dj-if boundary body). `ppath` is the parent's
/// path; `pid` the parent's djust_id.
#[allow(clippy::too_many_arguments)]
fn diff_children(
    old: &[VNode],
    new: &[VNode],
    old_off: usize,
    new_off: usize,
    ppath: &[usize],
    pid: Option<&str>,
    out: &mut Vec<Patch>,
) {
    let (old_boundaries, old_excluded) = find_top_level_boundaries(old);
    let (new_boundaries, new_excluded) = find_top_level_boundaries(new);

    // --- Boundary matching by id ---
    if !old_boundaries.is_empty() || !new_boundaries.is_empty() {
        let new_ids: AHashMap<&str, &Boundary> =
            new_boundaries.iter().map(|b| (b.id.as_str(), b)).collect();
        let old_ids: AHashMap<&str, &Boundary> =
            old_boundaries.iter().map(|b| (b.id.as_str(), b)).collect();

        for ob in &old_boundaries {
            if let Some(nb) = new_ids.get(ob.id.as_str()) {
                // Matched in both -> recurse into the body slice.
                let old_body = &old[ob.open + 1..ob.close];
                let new_body = &new[nb.open + 1..nb.close];
                diff_children(
                    old_body,
                    new_body,
                    old_off + ob.open + 1,
                    new_off + nb.open + 1,
                    ppath,
                    pid,
                    out,
                );
            } else {
                // Old-only -> RemoveSubtree.
                out.push(Patch::RemoveSubtree { id: ob.id.clone() });
            }
        }
        for nb in &new_boundaries {
            if !old_ids.contains_key(nb.id.as_str()) {
                // New-only -> InsertSubtree.
                out.push(Patch::InsertSubtree {
                    id: nb.id.clone(),
                    path: ppath.to_vec(),
                    d: pid.map(|s| s.to_string()),
                    index: new_off + nb.open,
                    html: serialize_boundary_html(new, nb.open, nb.close),
                });
            }
        }
    }

    // --- Non-boundary sibling reconciliation ---
    let old_nb: Vec<(usize, &VNode)> = old
        .iter()
        .enumerate()
        .filter(|(i, _)| !old_excluded[*i])
        .map(|(i, n)| (old_off + i, n))
        .collect();
    let new_nb: Vec<(usize, &VNode)> = new
        .iter()
        .enumerate()
        .filter(|(i, _)| !new_excluded[*i])
        .map(|(i, n)| (new_off + i, n))
        .collect();

    reconcile_siblings(&old_nb, &new_nb, ppath, pid, out);
}

/// Reconcile two lists of non-boundary siblings (each carrying its parent-
/// absolute index). Chooses keyed reconciliation if any NEW sibling is keyed,
/// otherwise positional/indexed.
fn reconcile_siblings(
    old_nb: &[(usize, &VNode)],
    new_nb: &[(usize, &VNode)],
    ppath: &[usize],
    pid: Option<&str>,
    out: &mut Vec<Patch>,
) {
    let any_new_keyed = new_nb.iter().any(|(_, n)| n.key.is_some());
    if any_new_keyed {
        reconcile_keyed(old_nb, new_nb, ppath, pid, out);
    } else {
        reconcile_indexed(old_nb, new_nb, ppath, pid, out);
    }
}

/// Positional reconciliation: pair the i-th old non-boundary sibling with the
/// i-th new one. Compatible pairs recurse (Replace on tag mismatch); a
/// comment-vs-noncomment pair is remove+insert; surplus old -> RemoveChild,
/// surplus new -> InsertChild. Used for fully-unkeyed lists; intentionally
/// positional (no moves) — matches the original's indexed-diff behavior.
fn reconcile_indexed(
    old_nb: &[(usize, &VNode)],
    new_nb: &[(usize, &VNode)],
    ppath: &[usize],
    pid: Option<&str>,
    out: &mut Vec<Patch>,
) {
    let common = old_nb.len().min(new_nb.len());
    for i in 0..common {
        let (old_abs, old_node) = old_nb[i];
        let (new_abs, new_node) = new_nb[i];
        if positionally_compatible(old_node, new_node) {
            let mut child_path = ppath.to_vec();
            child_path.push(new_abs);
            diff_node_into(old_node, new_node, &child_path, out);
        } else {
            // Incompatible kind: remove old, insert new.
            push_remove_child(old_abs, old_node, ppath, pid, out);
            push_insert_child(new_abs, new_node, ppath, pid, out);
        }
    }
    // Surplus old -> remove (descending order so apply-index fallback is safe).
    for i in (common..old_nb.len()).rev() {
        let (old_abs, old_node) = old_nb[i];
        push_remove_child(old_abs, old_node, ppath, pid, out);
    }
    // Surplus new -> insert (ascending order).
    for &(new_abs, new_node) in new_nb.iter().skip(common) {
        push_insert_child(new_abs, new_node, ppath, pid, out);
    }
}

/// Keyed reconciliation with LIS-based move minimization.
///
/// - DUPLICATE KEYS (a key appearing >1 time on either side) are *ambiguous*:
///   keyed matching can't disambiguate them, so their siblings are reconciled
///   positionally instead (preventing a last-wins map from matching two new
///   nodes onto one old node and emitting a corrupting Replace).
/// - In the MIXED case (any effectively-unkeyed sibling interleaved with keyed
///   ones) the LIS-skip is disabled — every displaced keyed child gets a
///   MoveChild (#1260) — AND every effectively-unkeyed sibling that carries a
///   djust_id and changed absolute position also gets a MoveChild, so it is not
///   stranded by the keyed reorder.
fn reconcile_keyed(
    old_nb: &[(usize, &VNode)],
    new_nb: &[(usize, &VNode)],
    ppath: &[usize],
    pid: Option<&str>,
    out: &mut Vec<Patch>,
) {
    // Keys appearing more than once on either side are ambiguous (warns DJE-051).
    let ambiguous = ambiguous_keys(old_nb, new_nb);
    let eff_key = |n: &VNode| -> Option<String> {
        match n.key.as_deref() {
            Some(k) if !ambiguous.contains(k) => Some(k.to_string()),
            _ => None,
        }
    };

    // DJE-050: a raw unkeyed sibling alongside keyed ones.
    if new_nb.iter().any(|(_, n)| n.key.is_none()) && new_nb.iter().any(|(_, n)| n.key.is_some()) {
        vdom_trace!("DJE-050: Mixed keyed/unkeyed siblings during keyed diff");
        tracing::warn!(
            "DJE-050: Mixed keyed/unkeyed siblings detected during keyed diff; \
             reconciliation may be suboptimal"
        );
    }

    // The LIS-skip is only sound for fully-(effectively-)keyed lists.
    let has_unkeyed = old_nb.iter().any(|(_, n)| eff_key(n).is_none())
        || new_nb.iter().any(|(_, n)| eff_key(n).is_none());

    // Effective-key -> list position maps (each effective key is unique).
    let mut old_eff: AHashMap<String, usize> = AHashMap::new();
    for (pos, (_, n)) in old_nb.iter().enumerate() {
        if let Some(k) = eff_key(n) {
            old_eff.insert(k, pos);
        }
    }
    let mut new_eff: AHashMap<String, usize> = AHashMap::new();
    for (pos, (_, n)) in new_nb.iter().enumerate() {
        if let Some(k) = eff_key(n) {
            new_eff.insert(k, pos);
        }
    }

    // Matched effective-keyed pairs, in NEW order: (old_list_pos, new_list_pos).
    let mut matched: Vec<(usize, usize)> = Vec::new();
    for (np, (_, n)) in new_nb.iter().enumerate() {
        if let Some(k) = eff_key(n) {
            if let Some(&op) = old_eff.get(&k) {
                matched.push((op, np));
            }
        }
    }

    // 1) Remove old effective-keyed children absent from new.
    for &(old_abs, old_node) in old_nb.iter() {
        if let Some(k) = eff_key(old_node) {
            if !new_eff.contains_key(&k) {
                push_remove_child(old_abs, old_node, ppath, pid, out);
            }
        }
    }

    // 2) Positional group = effectively-unkeyed siblings (key None OR ambiguous).
    //    Move a repositioned member that carries a djust_id (#1260 generalization).
    let old_pos: Vec<(usize, &VNode)> = old_nb
        .iter()
        .filter(|(_, n)| eff_key(n).is_none())
        .copied()
        .collect();
    let new_pos: Vec<(usize, &VNode)> = new_nb
        .iter()
        .filter(|(_, n)| eff_key(n).is_none())
        .copied()
        .collect();
    let common = old_pos.len().min(new_pos.len());
    for i in 0..common {
        let (old_abs, old_node) = old_pos[i];
        let (new_abs, new_node) = new_pos[i];
        if positionally_compatible(old_node, new_node) {
            if old_abs != new_abs && old_node.djust_id.is_some() {
                out.push(Patch::MoveChild {
                    path: ppath.to_vec(),
                    d: pid.map(|s| s.to_string()),
                    from: old_abs,
                    to: new_abs,
                    child_d: old_node.djust_id.clone(),
                });
            }
            let mut child_path = ppath.to_vec();
            child_path.push(new_abs);
            diff_node_into(old_node, new_node, &child_path, out);
        } else {
            push_remove_child(old_abs, old_node, ppath, pid, out);
            push_insert_child(new_abs, new_node, ppath, pid, out);
        }
    }
    for i in (common..old_pos.len()).rev() {
        let (old_abs, old_node) = old_pos[i];
        push_remove_child(old_abs, old_node, ppath, pid, out);
    }
    for &(new_abs, new_node) in new_pos.iter().skip(common) {
        push_insert_child(new_abs, new_node, ppath, pid, out);
    }

    // 3) Recurse into matched effective-keyed pairs.
    for &(op, np) in &matched {
        let (_, old_node) = old_nb[op];
        let (new_abs, new_node) = new_nb[np];
        let mut child_path = ppath.to_vec();
        child_path.push(new_abs);
        diff_node_into(old_node, new_node, &child_path, out);
    }

    // 4) Insert new effective-keyed children absent from old.
    for (new_abs, new_node) in new_nb.iter() {
        if let Some(k) = eff_key(new_node) {
            if !old_eff.contains_key(&k) {
                push_insert_child(*new_abs, new_node, ppath, pid, out);
            }
        }
    }

    // 5) Moves for matched effective-keyed pairs.
    //    `matched` is in NEW order; the sequence of old_list_pos is what we LIS.
    let old_seq: Vec<usize> = matched.iter().map(|&(op, _)| op).collect();
    let keep: AHashSet<usize> = if has_unkeyed {
        // Mixed case: do NOT trust LIS; keep a pair "in place" only when its
        // absolute index is unchanged.
        let mut s = AHashSet::new();
        for &(op, np) in &matched {
            if old_nb[op].0 == new_nb[np].0 {
                s.insert(np);
            }
        }
        s
    } else {
        // Fully-keyed: keep the longest increasing subsequence of old positions.
        let lis = longest_increasing_subsequence(&old_seq);
        lis.iter().map(|&seq_i| matched[seq_i].1).collect()
    };

    for &(op, np) in &matched {
        if keep.contains(&np) {
            continue;
        }
        let (old_abs, old_node) = old_nb[op];
        let (new_abs, _) = new_nb[np];
        out.push(Patch::MoveChild {
            path: ppath.to_vec(),
            d: pid.map(|s| s.to_string()),
            from: old_abs,
            to: new_abs,
            child_d: old_node.djust_id.clone(),
        });
    }
}

/// Keys appearing more than once on either side (ambiguous for keyed matching).
/// Warns DJE-051 once per duplicated key (the key value is interpolated so the
/// message names the offender).
fn ambiguous_keys(old_nb: &[(usize, &VNode)], new_nb: &[(usize, &VNode)]) -> AHashSet<String> {
    let mut ambiguous: AHashSet<String> = AHashSet::new();
    for (which, list) in [("old", old_nb), ("new", new_nb)] {
        let mut seen: AHashSet<&str> = AHashSet::new();
        for (_, n) in list.iter() {
            if let Some(k) = n.key.as_deref() {
                if !seen.insert(k) && ambiguous.insert(k.to_string()) {
                    vdom_trace!("DJE-051: Duplicate dj-key in {} children: {}", which, k);
                    tracing::warn!(
                        "DJE-051: Duplicate dj-key '{}' in {} children (each keyed sibling \
                         must have a unique key; ambiguous keys fall back to positional diffing)",
                        k,
                        which
                    );
                }
            }
        }
    }
    ambiguous
}

fn push_remove_child(
    old_abs: usize,
    old_node: &VNode,
    ppath: &[usize],
    pid: Option<&str>,
    out: &mut Vec<Patch>,
) {
    out.push(Patch::RemoveChild {
        path: ppath.to_vec(),
        d: pid.map(|s| s.to_string()),
        index: old_abs,
        child_d: old_node.djust_id.clone(),
    });
}

fn push_insert_child(
    new_abs: usize,
    new_node: &VNode,
    ppath: &[usize],
    pid: Option<&str>,
    out: &mut Vec<Patch>,
) {
    out.push(Patch::InsertChild {
        path: ppath.to_vec(),
        d: pid.map(|s| s.to_string()),
        index: new_abs,
        node: new_node.clone(),
        ref_d: None,
    });
}

// ============================================================================
// Public: sync_ids
// ============================================================================

/// Copy stable `djust_id`s (and the matching `dj-id` attribute) from `old` onto
/// the logically-corresponding nodes of `new`, in place. Mirrors the diff's
/// matching so that after a render `new` (the next render's `old`) carries the
/// same ids the client DOM holds (#1408). dj-if-boundary aware: unmatched
/// boundaries are skipped (fresh ids preserved), matched boundaries recurse.
pub fn sync_ids(old: &VNode, new: &mut VNode) {
    if old.tag != new.tag {
        return;
    }
    // Carry this node's id forward.
    if old.djust_id.is_some() {
        new.djust_id = old.djust_id.clone();
        if new.attrs.contains_key("dj-id") || old.attrs.contains_key("dj-id") {
            if let Some(id) = &old.djust_id {
                new.attrs.insert("dj-id".to_string(), id.clone());
            }
        }
    }
    sync_children(&old.children, &mut new.children);
}

fn sync_children(old: &[VNode], new: &mut [VNode]) {
    let (old_boundaries, old_excluded) = find_top_level_boundaries(old);
    let (new_boundaries, new_excluded) = find_top_level_boundaries(new);

    // Matched boundaries: recurse into bodies. Unmatched: skip.
    if !old_boundaries.is_empty() || !new_boundaries.is_empty() {
        let old_ids: AHashMap<&str, &Boundary> =
            old_boundaries.iter().map(|b| (b.id.as_str(), b)).collect();
        // Capture (old_boundary, new_boundary) pairs before mutably borrowing.
        let matched: Vec<(Boundary, Boundary)> = new_boundaries
            .iter()
            .filter_map(|nb| {
                old_ids
                    .get(nb.id.as_str())
                    .map(|ob| ((*ob).clone(), nb.clone()))
            })
            .collect();
        for (ob, nb) in matched {
            let new_body = &mut new[nb.open + 1..nb.close];
            sync_children(&old[ob.open + 1..ob.close], new_body);
        }
    }

    // Non-boundary siblings: pair by relative order (or key when keyed).
    let old_nb: Vec<usize> = (0..old.len()).filter(|i| !old_excluded[*i]).collect();
    let new_nb: Vec<usize> = (0..new.len()).filter(|i| !new_excluded[*i]).collect();

    let any_new_keyed = new_nb.iter().any(|&i| new[i].key.is_some());
    if any_new_keyed {
        // Keyed: match by key.
        let mut old_by_key: AHashMap<String, usize> = AHashMap::new();
        for &oi in &old_nb {
            if let Some(k) = old[oi].key.as_deref() {
                old_by_key.insert(k.to_string(), oi);
            }
        }
        // Also positionally sync the unkeyed ones in relative order.
        let new_unkeyed: Vec<usize> = new_nb
            .iter()
            .copied()
            .filter(|&i| new[i].key.is_none())
            .collect();
        let old_unkeyed: Vec<usize> = old_nb
            .iter()
            .copied()
            .filter(|&i| old[i].key.is_none())
            .collect();
        let common = old_unkeyed.len().min(new_unkeyed.len());

        for &ni in &new_nb {
            if let Some(k) = new[ni].key.as_deref() {
                if let Some(&oi) = old_by_key.get(k) {
                    sync_one(old, new, oi, ni);
                }
            }
        }
        for k in 0..common {
            sync_one(old, new, old_unkeyed[k], new_unkeyed[k]);
        }
    } else {
        // Positional pairing by relative order among non-boundary siblings.
        let common = old_nb.len().min(new_nb.len());
        for k in 0..common {
            sync_one(old, new, old_nb[k], new_nb[k]);
        }
    }
}

/// Sync ids from `old[oi]` onto `new[ni]` (helper to localize the split borrow).
fn sync_one(old: &[VNode], new: &mut [VNode], oi: usize, ni: usize) {
    let old_node = &old[oi];
    let new_node = &mut new[ni];
    sync_ids(old_node, new_node);
}
