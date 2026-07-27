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

/// Count how many NON-boundary siblings precede the local index `open`.
///
/// `excluded` is the mask from [`find_top_level_boundaries`] (index `k` is
/// `true` when it falls inside ANY boundary marker span). This collapses the
/// boundary spans so that a boundary's "significant position" is measured
/// against its real (non-marker) siblings ONLY — not the absolute child
/// index, which shifts whenever an EARLIER boundary body fills/empties
/// (#1826). Two renders that keep the same non-boundary neighborhood around a
/// boundary yield the same count even if an earlier sibling boundary changed
/// span length.
fn non_boundary_count_before(excluded: &[bool], open: usize) -> usize {
    (0..open).filter(|&k| !excluded[k]).count()
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
use std::sync::atomic::{AtomicBool, Ordering};

/// Emit KEY-addressed splice ops for `[dj-virtual]` parents (ADR-026).
///
/// Default OFF. Iteration 1 of the ADR ships the differ side dark: the ops are
/// emitted only when this is enabled, so `main` behaviour is byte-identical
/// until the client half (iteration 2) can apply them and the flag is flipped
/// after a soak (iteration 3).
///
/// A process-global atomic rather than a threaded parameter because `diff_nodes`
/// is a pure free function reached from several entry points; threading config
/// through every signature would touch far more surface than the flag is worth
/// while it is still dark. Set once at startup from
/// a `LIVEVIEW_CONFIG` key — that wiring SHIPPED in the #2017 iteration-3 PR
/// (`virtual_keyed_ops`, applied once by `DjustConfig.ready()`). The DEFAULT
/// is still OFF: the browser gate found the client applier does not achieve
/// keyed positioning even though this differ emits the right op — see #2164.
static VIRTUAL_KEYED_OPS: AtomicBool = AtomicBool::new(false);

/// Enable/disable `[dj-virtual]` keyed splice ops. Wired from Python config.
pub fn set_virtual_keyed_ops(enabled: bool) {
    VIRTUAL_KEYED_OPS.store(enabled, Ordering::Relaxed);
}

/// Whether `[dj-virtual]` keyed splice ops are enabled.
pub fn virtual_keyed_ops_enabled() -> bool {
    VIRTUAL_KEYED_OPS.load(Ordering::Relaxed)
}

/// True when this parent is a client-windowed `[dj-virtual]` container AND the
/// feature is enabled. Both conditions matter: the ops are meaningless for an
/// ordinary parent, and must stay dark until the client can apply them.
fn emits_virtual_ops(parent: &VNode) -> bool {
    virtual_keyed_ops_enabled() && parent.attrs.contains_key("dj-virtual")
}

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
        // The NEW tree is authoritative for what this container is now.
        emits_virtual_ops(new),
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
    // virtual_parent: true when the PARENT is a `[dj-virtual]` container and
    // the ADR-026 flag is on — its children are a client-side window, so
    // index-addressed ops are meaningless and keyed splice ops are emitted.
    virtual_parent: bool,
) {
    let (old_boundaries, old_excluded) = find_top_level_boundaries(old);
    let (new_boundaries, new_excluded) = find_top_level_boundaries(new);

    // --- Boundary matching by id ---
    if !old_boundaries.is_empty() || !new_boundaries.is_empty() {
        let new_ids: AHashMap<&str, &Boundary> =
            new_boundaries.iter().map(|b| (b.id.as_str(), b)).collect();
        let old_ids: AHashMap<&str, &Boundary> =
            old_boundaries.iter().map(|b| (b.id.as_str(), b)).collect();
        // Ordinal of each NEW boundary among same-level boundaries (its index
        // in `new_boundaries`). Paired with the non-boundary-sibling count to
        // decide MoveSubtree by RELATIVE position (#1826).
        let new_ordinals: AHashMap<&str, usize> = new_boundaries
            .iter()
            .enumerate()
            .map(|(i, b)| (b.id.as_str(), i))
            .collect();

        for (old_ordinal, ob) in old_boundaries.iter().enumerate() {
            if let Some(nb) = new_ids.get(ob.id.as_str()) {
                // Matched in both. Emit MoveSubtree to relocate the id-less
                // marker span ONLY when the boundary actually repositioned
                // relative to its real (non-marker) siblings — a plain
                // MoveChild can't target the markers (#1666). Then recurse
                // into the body.
                //
                // #1826: the decision must NOT use the absolute child offset
                // (`old_off + ob.open` vs `new_off + nb.open`). Filling an
                // EARLIER empty dj-if body inserts nodes that shift the
                // absolute index of every LATER boundary even though those
                // boundaries did NOT move relative to their siblings — which
                // emitted spurious MoveSubtree ops the client couldn't pair
                // (`close marker not found`). Instead key on (count of
                // non-boundary siblings before the boundary, ordinal among
                // same-level boundaries): both are invariant to a sibling
                // boundary's span-length change, so only a GENUINE reposition
                // (a real element inserted/removed before the boundary, or a
                // boundary reorder) flips the key and emits the move.
                let old_key = (
                    non_boundary_count_before(&old_excluded, ob.open),
                    old_ordinal,
                );
                let new_key = (
                    non_boundary_count_before(&new_excluded, nb.open),
                    new_ordinals[ob.id.as_str()],
                );
                if old_key != new_key {
                    out.push(Patch::MoveSubtree {
                        id: ob.id.clone(),
                        path: ppath.to_vec(),
                        d: pid.map(|s| s.to_string()),
                        // The move TARGET stays the absolute new index — only
                        // the move DECISION was wrong, not this value.
                        index: new_off + nb.open,
                    });
                }
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
                    // A dj-if body lives INSIDE this parent, so it inherits
                    // the parent's virtual-ness.
                    virtual_parent,
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

    reconcile_siblings(&old_nb, &new_nb, ppath, pid, out, virtual_parent);
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
    virtual_parent: bool,
) {
    let any_new_keyed = new_nb.iter().any(|(_, n)| n.key.is_some());

    // A [dj-virtual] parent gets KEY-addressed ops (ADR-026): its children on
    // the client are only the visible window, so an index means different
    // things on the two sides.
    //
    // The gate is the PARENT, not `any_new_keyed`. Gating on the children let
    // two shapes escape to the index-addressed path: an EMPTY new list (clear
    // the feed, a filter matching nothing) and an all-unkeyed new list — both
    // of which then emit RemoveChild/InsertChild against a windowed container,
    // which is the exact failure this exists to remove. "Clear the list" is
    // about the most common operation a feed has.
    if virtual_parent {
        if let Some(reason) = virtual_keyed_unsupported(old_nb, new_nb) {
            // Key-addressed ops cannot express this change, so fall back to
            // the plain reconcilers — which handle both cases explicitly and
            // warn — rather than silently dropping it. The fallback still
            // emits index-addressed ops, which is wrong for a windowed
            // container; that is why this warns rather than passing quietly.
            vdom_trace!(
                "DJE-052: [dj-virtual] children fell back to index diffing: {}",
                reason
            );
            tracing::warn!(
                "DJE-052: a [dj-virtual] container's children {} — falling back to \
                 index-addressed diffing, which cannot address items outside the \
                 client's visible window. Give every child a unique dj-key.",
                reason
            );
            if any_new_keyed {
                reconcile_keyed(old_nb, new_nb, ppath, pid, out);
            } else {
                reconcile_indexed(old_nb, new_nb, ppath, pid, out);
            }
        } else {
            reconcile_virtual_keyed(old_nb, new_nb, ppath, pid, out);
        }
    } else if any_new_keyed {
        reconcile_keyed(old_nb, new_nb, ppath, pid, out);
    } else {
        reconcile_indexed(old_nb, new_nb, ppath, pid, out);
    }
}

/// Reconcile a `[dj-virtual]` parent's keyed children into KEY-addressed
/// splice ops (ADR-026, #2017 items 2-4).
///
/// Uses the same LIS minimisation as `reconcile_keyed`, and for a reason that
/// is easy to get wrong: the FIRST version of this skipped LIS, arguing that
/// the client applies these to an item pool where a move is an array splice
/// rather than a DOM operation, so extra moves are cheap. That reasoning was
/// about the wrong cost. Without LIS every surviving key gets a move, so a
/// single append to a 50-item list emitted 50 moves — and on the 10k-row feeds
/// `dj-virtual` exists for, one append would emit 10k ops. The binding cost is
/// WIRE SIZE, not DOM mutations, and O(n) ops per patch defeats the purpose of
/// virtualising at all.
/// Why `reconcile_virtual_keyed` cannot handle these children, if it cannot.
///
/// Key-addressed ops address a row by its key and anchor it to a neighbour's
/// key. Two shapes are unrepresentable in that scheme, and both are silent
/// data loss if the reconciler is handed them anyway:
///
/// - **An unkeyed child** has no address at all, so every change to it —
///   content, insertion, removal — is invisible. `reconcile_keyed` handles
///   this case at length (a positional group, LIS disabled, a DJE-050
///   warning); this reconciler had none of it.
/// - **A duplicate key** makes `before_key` ambiguous: two rows answer to the
///   same anchor. `reconcile_keyed` demotes ambiguous keys to positional
///   diffing and warns DJE-051; here they would collapse in a hash set and the
///   extra row would simply never appear (or never leave).
fn virtual_keyed_unsupported(
    old_nb: &[(usize, &VNode)],
    new_nb: &[(usize, &VNode)],
) -> Option<&'static str> {
    if new_nb.iter().any(|(_, n)| n.key.is_none()) || old_nb.iter().any(|(_, n)| n.key.is_none()) {
        return Some("include a child with no dj-key");
    }
    for list in [old_nb, new_nb] {
        let mut seen: AHashSet<&str> = AHashSet::new();
        for (_, n) in list.iter() {
            if let Some(k) = n.key.as_deref() {
                if !seen.insert(k) {
                    return Some("include a duplicate dj-key");
                }
            }
        }
    }
    None
}

fn reconcile_virtual_keyed(
    old_nb: &[(usize, &VNode)],
    new_nb: &[(usize, &VNode)],
    ppath: &[usize],
    pid: Option<&str>,
    out: &mut Vec<Patch>,
) {
    // Old positions by key, so a surviving key can be located in old-order.
    let mut old_pos: AHashMap<&str, usize> = AHashMap::new();
    for (i, (_, n)) in old_nb.iter().enumerate() {
        if let Some(k) = n.key.as_deref() {
            // First occurrence wins; duplicate keys are ambiguous and are
            // reported separately by the shared `ambiguous_keys` warning.
            old_pos.entry(k).or_insert(i);
        }
    }

    // (position within this vec, ABSOLUTE index in new_nb, key, node).
    // The absolute index is what a child path must use: the filtered position
    // diverges from it the moment an unkeyed sibling precedes a keyed one, and
    // a content patch built from the wrong one rewrites the WRONG child. The
    // gate above rejects unkeyed children, so today they cannot diverge — this
    // carries both anyway rather than depending on a caller's invariant.
    let new_keyed: Vec<(usize, usize, &str, &VNode)> = new_nb
        .iter()
        .filter_map(|(abs, n)| n.key.as_deref().map(|k| (*abs, k, *n)))
        .enumerate()
        .map(|(i, (abs, k, n))| (i, abs, k, n))
        .collect();

    // Removals: an old key with no counterpart in the new list.
    let new_key_set: AHashSet<&str> = new_keyed.iter().map(|(_, _, k, _)| *k).collect();
    for (_, n) in old_nb.iter() {
        if let Some(k) = n.key.as_deref() {
            if !new_key_set.contains(k) {
                out.push(Patch::VirtualRemove {
                    path: ppath.to_vec(),
                    d: pid.map(|s| s.to_string()),
                    key: k.to_string(),
                });
            }
        }
    }

    // Survivors in NEW order, carrying their OLD index. Their old-index
    // sequence is what the LIS runs over: the increasing subsequence is
    // already in relative order and needs no move.
    let survivors: Vec<(&str, usize)> = new_keyed
        .iter()
        .filter_map(|(_, _, k, _)| old_pos.get(k).map(|oi| (*k, *oi)))
        .collect();
    let old_seq: Vec<usize> = survivors.iter().map(|(_, oi)| *oi).collect();
    let stable_positions = longest_increasing_subsequence(&old_seq);
    let stable: AHashSet<&str> = stable_positions
        .iter()
        .filter_map(|si| survivors.get(*si).map(|(k, _)| *k))
        .collect();

    // Content updates for survivors. Without this a surviving row's text
    // change emits NOTHING for a [dj-virtual] parent — the structural ops
    // below only reposition. `reconcile_keyed` recurses for every matched
    // pair (step 3) and this must too; emitted BEFORE the structural ops to
    // match that function's phase ordering.
    for (_, _abs, key, new_node) in new_keyed.iter() {
        let Some(oi) = old_pos.get(*key) else {
            continue;
        };
        let (_, old_node) = old_nb[*oi];
        // Diff against an EMPTY base path, so the inner patches are relative
        // to the row's own root, and wrap them in a key-addressed op (#2136).
        //
        // These used to be emitted as ordinary patches whose path was the
        // item's ABSOLUTE index. For a windowed container that index is
        // meaningless — it counts ITEMS while the DOM holds only the visible
        // window — and the patches carry no dj-id (text nodes have none), so
        // they resolved purely positionally. Measured against a real mounted
        // list: editing row `k0` after a scroll silently rewrote `k7`, with
        // applyPatches returning true and no warning.
        //
        // Key-addressed, the client finds the row in the POOL, which is also
        // what makes an OFF-WINDOW update land — the row is detached, and
        // mutating a detached node is fine.
        let mut inner: Vec<Patch> = Vec::new();
        diff_node_into(old_node, new_node, &[], &mut inner);
        if !inner.is_empty() {
            out.push(Patch::VirtualUpdate {
                path: ppath.to_vec(),
                d: pid.map(|s| s.to_string()),
                key: (*key).to_string(),
                patches: inner,
            });
        }
    }

    // Structural ops, walked in REVERSE new order. `before_key` names the NEXT
    // new sibling, so that anchor must already be in place when the op is
    // applied — which reverse order guarantees and forward order does not.
    // Forward, prepending [x, y] onto [a, b] emits "insert x before y" while y
    // is still absent: the client cannot find the anchor, falls back to the
    // tail, and the list ends up y,a,b,x instead of x,y,a,b.
    for (i, _, key, node) in new_keyed.iter().rev() {
        let before_key = new_keyed.get(i + 1).map(|(_, _, k, _)| (*k).to_string());

        if old_pos.contains_key(*key) {
            // Survivor: emit a move ONLY if it is outside the stable run.
            if !stable.contains(*key) {
                out.push(Patch::VirtualMove {
                    path: ppath.to_vec(),
                    d: pid.map(|s| s.to_string()),
                    key: (*key).to_string(),
                    before_key,
                });
            }
        } else {
            out.push(Patch::VirtualInsert {
                path: ppath.to_vec(),
                d: pid.map(|s| s.to_string()),
                key: (*key).to_string(),
                node: (*node).clone(),
                before_key,
            });
        }
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
