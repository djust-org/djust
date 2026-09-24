//! Patch application utilities
//!
//! Utilities for applying patches to virtual DOM trees.
//! In the LiveView system, patches are serialized and sent to the client.

use crate::{Patch, VNode};

/// Apply a list of patches to a virtual DOM tree (for testing purposes).
///
/// This is the reference model of how a patch batch is applied; the client
/// (`_applyPatchBatch` in `12-vdom-patch.js`) implements the same model, so
/// the round-trip tests that check `apply_patches(old, diff(old, new)) == new`
/// validate what the browser does (#2999). Per parent:
///
/// 1. **Removals** — `RemoveChild` (by `child_d`, else its OLD index) and
///    `RemoveSubtree` (the marker span, by boundary id) — are all resolved
///    against the ORIGINAL child list, then removed together. Resolving an
///    id-less `RemoveChild` by index after a `RemoveSubtree` had shifted the
///    list removed the wrong node.
/// 2. **Placements** — `InsertChild`, `MoveChild`, `InsertSubtree`,
///    `MoveSubtree` — each carry the FINAL index of what they place. Every
///    moved child / moved span is detached first (a span never carries a
///    nested moved item with it), then all placements go in by ascending
///    final index. The differ guarantees that what is left in place is already
///    in final relative order, so each placement at index `i` lands after
///    exactly its `i` final predecessors.
/// 3. Node patches (`SetText`, `SetAttr`, `RemoveAttr`, `Replace`) in emitted
///    order, by djust_id when available, else by (final-tree) path.
pub fn apply_patches(root: &mut VNode, patches: &[Patch]) {
    #[derive(Default)]
    struct ParentGroup<'a> {
        removes: Vec<&'a Patch>,
        remove_subtrees: Vec<&'a str>,
        placements: Vec<&'a Patch>,
    }

    let mut parent_order: Vec<String> = Vec::new();
    let mut parent_groups: std::collections::HashMap<String, ParentGroup<'_>> =
        std::collections::HashMap::new();
    let mut non_child_patches: Vec<&Patch> = Vec::new();

    let group_for =
        |pid: &str,
         order: &mut Vec<String>,
         groups: &mut std::collections::HashMap<String, ParentGroup<'_>>| {
            if !groups.contains_key(pid) {
                order.push(pid.to_string());
                groups.insert(pid.to_string(), ParentGroup::default());
            }
        };

    for patch in patches {
        match patch {
            Patch::RemoveChild { d: Some(pid), .. } => {
                group_for(pid, &mut parent_order, &mut parent_groups);
                parent_groups.get_mut(pid).unwrap().removes.push(patch);
            }
            Patch::InsertChild { d: Some(pid), .. }
            | Patch::MoveChild { d: Some(pid), .. }
            | Patch::InsertSubtree { d: Some(pid), .. }
            | Patch::MoveSubtree { d: Some(pid), .. } => {
                group_for(pid, &mut parent_order, &mut parent_groups);
                parent_groups.get_mut(pid).unwrap().placements.push(patch);
            }
            Patch::RemoveSubtree { id } => {
                // No parent in the patch: find the element holding the marker.
                if let Some(pid) = find_marker_parent_id(root, id) {
                    group_for(&pid, &mut parent_order, &mut parent_groups);
                    parent_groups
                        .get_mut(&pid)
                        .unwrap()
                        .remove_subtrees
                        .push(id.as_str());
                }
            }
            Patch::RemoveChild { d: None, .. }
            | Patch::InsertChild { d: None, .. }
            | Patch::MoveChild { d: None, .. }
            | Patch::InsertSubtree { d: None, .. }
            | Patch::MoveSubtree { d: None, .. } => {
                // The differ always sets `d` on id-assigned trees.
                debug_assert!(false, "child patch without a parent djust_id: {patch:?}");
            }
            _ => non_child_patches.push(patch),
        }
    }

    for pid in &parent_order {
        let group = parent_groups.get(pid).unwrap();
        let Some(target) = find_by_djust_id_mut(root, pid) else {
            continue;
        };
        let parent_tag = target.tag.clone();
        let original = std::mem::take(&mut target.children);
        let n = original.len();

        // 1. Removals, all resolved against the original list.
        let mut removed = vec![false; n];
        for patch in &group.removes {
            if let Patch::RemoveChild { index, child_d, .. } = patch {
                let pos = child_d
                    .as_deref()
                    .and_then(|cid| {
                        original
                            .iter()
                            .position(|c| c.djust_id.as_deref() == Some(cid))
                    })
                    .or((*index < n).then_some(*index));
                if let Some(pos) = pos {
                    removed[pos] = true;
                }
            }
        }
        for id in &group.remove_subtrees {
            if let Some(open) = find_dj_if_open_idx(&original, id) {
                if let Some(close) = match_close_idx(&original, open) {
                    for r in removed.iter_mut().take(close + 1).skip(open) {
                        *r = true;
                    }
                }
            }
        }

        // 2. Placements. Owner of each original node: None = stays in place.
        //    Moved children first, then moved spans innermost-first, so an
        //    outer span never carries a nested moved item with it.
        let mut owner: Vec<Option<usize>> = vec![None; n];
        let mut spans: Vec<(usize, usize, usize)> = Vec::new(); // (len, open, placement#)
        for (k, patch) in group.placements.iter().enumerate() {
            match patch {
                Patch::MoveChild { from, child_d, .. } => {
                    let pos = child_d
                        .as_deref()
                        .and_then(|cid| {
                            original
                                .iter()
                                .position(|c| c.djust_id.as_deref() == Some(cid))
                        })
                        .or((*from < n).then_some(*from));
                    if let Some(pos) = pos {
                        if !removed[pos] && owner[pos].is_none() {
                            owner[pos] = Some(k);
                        }
                    }
                }
                Patch::MoveSubtree { id, .. } => {
                    if let Some(open) = find_dj_if_open_idx(&original, id) {
                        if let Some(close) = match_close_idx(&original, open) {
                            spans.push((close - open, open, k));
                        }
                    }
                }
                _ => {}
            }
        }
        spans.sort_unstable();
        for (len, open, k) in spans {
            for slot in owner.iter_mut().skip(open).take(len + 1) {
                if slot.is_none() {
                    *slot = Some(k);
                }
            }
        }

        let mut moved: std::collections::HashMap<usize, Vec<VNode>> =
            std::collections::HashMap::new();
        let mut list: Vec<VNode> = Vec::with_capacity(n);
        for (pos, node) in original.into_iter().enumerate() {
            if removed[pos] {
                continue;
            }
            match owner[pos] {
                Some(k) => moved.entry(k).or_default().push(node),
                None => list.push(node),
            }
        }

        let mut order: Vec<(usize, usize)> = group
            .placements
            .iter()
            .enumerate()
            .map(|(k, p)| {
                let index = match p {
                    Patch::InsertChild { index, .. }
                    | Patch::InsertSubtree { index, .. }
                    | Patch::MoveSubtree { index, .. } => *index,
                    Patch::MoveChild { to, .. } => *to,
                    _ => usize::MAX,
                };
                (index, k)
            })
            .collect();
        order.sort_unstable();
        for (index, k) in order {
            let nodes: Vec<VNode> = match group.placements[k] {
                Patch::InsertChild { node, .. } => vec![node.clone()],
                Patch::MoveChild { .. } | Patch::MoveSubtree { .. } => {
                    moved.remove(&k).unwrap_or_default()
                }
                Patch::InsertSubtree { id, html, .. } => {
                    if find_dj_if_open_idx(&list, id).is_some() {
                        continue; // idempotent, as on the client
                    }
                    crate::parser::parse_html_fragment_client_view(html, &parent_tag)
                        .unwrap_or_default()
                }
                _ => Vec::new(),
            };
            let at = index.min(list.len());
            for (offset, node) in nodes.into_iter().enumerate() {
                list.insert(at + offset, node);
            }
        }

        if let Some(target) = find_by_djust_id_mut(root, pid) {
            target.children = list;
        }
    }

    // 3. Node patches, using djust_id resolution when available, falling back
    //    to path-based traversal for text nodes (d=None).
    for patch in &non_child_patches {
        match patch {
            Patch::SetText { path, d, text } => {
                let target = resolve_node_mut(root, path, d.as_deref());
                if let Some(target) = target {
                    target.text = Some(text.clone());
                }
            }
            Patch::SetAttr {
                path,
                d,
                key,
                value,
            } => {
                let target = resolve_node_mut(root, path, d.as_deref());
                if let Some(target) = target {
                    target.attrs.insert(key.clone(), value.clone());
                }
            }
            Patch::RemoveAttr { path, d, key } => {
                let target = resolve_node_mut(root, path, d.as_deref());
                if let Some(target) = target {
                    target.attrs.remove(key);
                }
            }
            Patch::Replace { path, d, node } => {
                let target = resolve_node_mut(root, path, d.as_deref());
                if let Some(target) = target {
                    *target = node.clone();
                }
            }
            _ => {}
        }
    }
}

/// djust_id of the element whose children hold the `<!--dj-if id="<id>"-->`
/// open marker.
fn find_marker_parent_id(node: &VNode, id: &str) -> Option<String> {
    if find_dj_if_open_idx(&node.children, id).is_some() {
        return node.djust_id.clone();
    }
    node.children
        .iter()
        .find_map(|c| find_marker_parent_id(c, id))
}

/// Index of the `<!--dj-if id="<id>"-->` open marker among `children`.
fn find_dj_if_open_idx(children: &[VNode], id: &str) -> Option<usize> {
    children.iter().position(|n| {
        n.tag == "#comment"
            && n.text.as_deref().is_some_and(|t| {
                let t = t.trim();
                (t.starts_with("dj-if ") || t.starts_with("dj-if\t"))
                    && t.contains(&format!("id=\"{}\"", id))
            })
    })
}

/// Index of the close marker matching the open at `open_idx` (depth-counted).
fn match_close_idx(children: &[VNode], open_idx: usize) -> Option<usize> {
    let is_open = |n: &VNode| {
        n.tag == "#comment"
            && n.text
                .as_deref()
                .is_some_and(|t| t.trim().starts_with("dj-if ") || t.trim().starts_with("dj-if\t"))
    };
    let is_close =
        |n: &VNode| n.tag == "#comment" && n.text.as_deref().map(|t| t.trim()) == Some("/dj-if");
    let mut depth = 1;
    for (i, n) in children.iter().enumerate().skip(open_idx + 1) {
        if is_open(n) {
            depth += 1;
        } else if is_close(n) {
            depth -= 1;
            if depth == 0 {
                return Some(i);
            }
        }
    }
    None
}

/// Resolve a node: try djust_id first, fall back to path traversal.
///
/// Note: This does two tree walks when using djust_id — an immutable check
/// followed by a mutable lookup. This is required because Rust's borrow checker
/// won't allow attempting a mutable borrow and falling back on failure within
/// the same scope. Acceptable for test-only code; production use should build
/// an id→node index upfront.
fn resolve_node_mut<'a>(
    root: &'a mut VNode,
    path: &[usize],
    djust_id: Option<&str>,
) -> Option<&'a mut VNode> {
    let use_id = djust_id
        .map(|id| find_by_djust_id(root, id).is_some())
        .unwrap_or(false);
    if use_id {
        find_by_djust_id_mut(root, djust_id.unwrap())
    } else {
        get_node_mut(root, path)
    }
}

/// Find a node by its djust_id (immutable).
fn find_by_djust_id<'a>(root: &'a VNode, id: &str) -> Option<&'a VNode> {
    if root.djust_id.as_deref() == Some(id) {
        return Some(root);
    }
    for child in &root.children {
        if let Some(found) = find_by_djust_id(child, id) {
            return Some(found);
        }
    }
    None
}

/// Find a node by its djust_id (mutable).
fn find_by_djust_id_mut<'a>(root: &'a mut VNode, id: &str) -> Option<&'a mut VNode> {
    if root.djust_id.as_deref() == Some(id) {
        return Some(root);
    }
    for child in &mut root.children {
        if let Some(found) = find_by_djust_id_mut(child, id) {
            return Some(found);
        }
    }
    None
}

/// Apply a single patch to a virtual DOM tree (for testing purposes)
///
/// Note: For correct `MoveChild` handling with multiple moves, prefer
/// `apply_patches()` which resolves children by `djust_id`. This function
/// uses index-based `MoveChild` which may produce incorrect results when
/// multiple moves shift indices.
pub fn apply_patch(root: &mut VNode, patch: &Patch) {
    match patch {
        Patch::Replace { path, node, .. } => {
            if let Some(target) = get_node_mut(root, path) {
                *target = node.clone();
            }
        }

        Patch::SetText { path, text, .. } => {
            if let Some(target) = get_node_mut(root, path) {
                target.text = Some(text.clone());
            }
        }

        Patch::SetAttr {
            path, key, value, ..
        } => {
            if let Some(target) = get_node_mut(root, path) {
                target.attrs.insert(key.clone(), value.clone());
            }
        }

        Patch::RemoveAttr { path, key, .. } => {
            if let Some(target) = get_node_mut(root, path) {
                target.attrs.remove(key);
            }
        }

        Patch::InsertChild {
            path, index, node, ..
        } => {
            if let Some(target) = get_node_mut(root, path) {
                if *index <= target.children.len() {
                    target.children.insert(*index, node.clone());
                }
            }
        }

        Patch::RemoveChild { path, index, .. } => {
            if let Some(target) = get_node_mut(root, path) {
                if *index < target.children.len() {
                    target.children.remove(*index);
                }
            }
        }

        Patch::MoveChild { path, from, to, .. } => {
            if let Some(target) = get_node_mut(root, path) {
                if *from < target.children.len() {
                    let node = target.children.remove(*from);
                    let insert_at = (*to).min(target.children.len());
                    target.children.insert(insert_at, node);
                }
            }
        }

        // RemoveSubtree / InsertSubtree are dispatched by marker id on the
        // client; the test-only `apply_patch` helper does not model the
        // marker-id index so these are intentionally no-ops here. Tests
        // that exercise the diff output should assert on patch shape (via
        // `matches!`) rather than round-tripping through `apply_patch`.
        Patch::RemoveSubtree { .. } | Patch::InsertSubtree { .. } | Patch::MoveSubtree { .. } => {}

        // [dj-virtual] keyed splice ops (ADR-026). Unlike the Subtree variants
        // above, these ARE modelled here: they address a child by `key` within
        // the parent at `path`, which this helper can resolve exactly. Tests
        // can therefore round-trip them rather than only asserting shape.
        Patch::VirtualInsert {
            path,
            key,
            node,
            before_key,
            ..
        } => {
            if let Some(parent) = get_node_mut(root, path) {
                let at = match before_key {
                    Some(bk) => parent
                        .children
                        .iter()
                        .position(|c| c.key.as_deref() == Some(bk.as_str()))
                        .unwrap_or(parent.children.len()),
                    None => parent.children.len(),
                };
                let mut inserted = node.clone();
                if inserted.key.is_none() {
                    inserted.key = Some(key.clone());
                }
                parent.children.insert(at, inserted);
            }
        }

        Patch::VirtualUpdate {
            path, key, patches, ..
        } => {
            // Resolve the row by KEY, then apply the inner patches with that
            // row as the root — the inner paths are relative to it (#2136).
            if let Some(parent) = get_node_mut(root, path) {
                if let Some(idx) = parent
                    .children
                    .iter()
                    .position(|c| c.key.as_deref() == Some(key.as_str()))
                {
                    let row = &mut parent.children[idx];
                    for inner in patches {
                        apply_patch(row, inner);
                    }
                }
            }
        }
        Patch::VirtualMove {
            path,
            key,
            before_key,
            ..
        } => {
            if let Some(parent) = get_node_mut(root, path) {
                if let Some(from) = parent
                    .children
                    .iter()
                    .position(|c| c.key.as_deref() == Some(key.as_str()))
                {
                    let item = parent.children.remove(from);
                    let at = match before_key {
                        Some(bk) => parent
                            .children
                            .iter()
                            .position(|c| c.key.as_deref() == Some(bk.as_str()))
                            .unwrap_or(parent.children.len()),
                        None => parent.children.len(),
                    };
                    parent.children.insert(at, item);
                }
            }
        }

        Patch::VirtualRemove { path, key, .. } => {
            if let Some(parent) = get_node_mut(root, path) {
                parent
                    .children
                    .retain(|c| c.key.as_deref() != Some(key.as_str()));
            }
        }
    }
}

fn get_node_mut<'a>(root: &'a mut VNode, path: &[usize]) -> Option<&'a mut VNode> {
    let mut current = root;

    for &index in path {
        if index < current.children.len() {
            current = &mut current.children[index];
        } else {
            return None;
        }
    }

    Some(current)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_apply_set_text() {
        let mut root = VNode::text("old");
        let patch = Patch::SetText {
            path: vec![],
            d: None,
            text: "new".to_string(),
        };

        apply_patch(&mut root, &patch);
        assert_eq!(root.text, Some("new".to_string()));
    }

    #[test]
    fn test_apply_set_attr() {
        let mut root = VNode::element("div");
        let patch = Patch::SetAttr {
            path: vec![],
            d: Some("0".to_string()),
            key: "class".to_string(),
            value: "active".to_string(),
        };

        apply_patch(&mut root, &patch);
        assert_eq!(root.attrs.get("class"), Some(&"active".to_string()));
    }

    #[test]
    fn test_apply_insert_child() {
        let mut root = VNode::element("div");
        let patch = Patch::InsertChild {
            path: vec![],
            d: Some("0".to_string()),
            index: 0,
            node: VNode::text("child"),
            ref_d: None,
        };

        apply_patch(&mut root, &patch);
        assert_eq!(root.children.len(), 1);
    }
}
