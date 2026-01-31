//! Patch application utilities
//!
//! Utilities for applying patches to virtual DOM trees.
//! In the LiveView system, patches are serialized and sent to the client.

use crate::{Patch, VNode};

/// Apply a list of patches to a virtual DOM tree (for testing purposes).
///
/// This function handles `MoveChild` patches correctly by snapshotting
/// the original children order before applying moves, resolving children
/// by `djust_id` instead of index. This mirrors the client-side JS
/// resolution strategy using `data-d` attributes.
pub fn apply_patches(root: &mut VNode, patches: &[Patch]) {
    // The keyed diff engine emits patches where RemoveChild indices reference
    // the original tree and InsertChild indices reference the final tree.
    // To apply them correctly with sequential index-based mutation, we
    // reorder child mutations per parent: removes descending, then inserts
    // ascending. Non-child patches (SetText, SetAttr, etc.) use paths that
    // reference the final tree layout, so they are applied after child
    // mutations.
    //
    // For MoveChild, we snapshot children before mutations and resolve by
    // djust_id, mirroring the client-side data-d attribute strategy.

    // Snapshot djust_ids of children for MoveChild resolution.
    let mut move_sources: std::collections::HashMap<Vec<usize>, Vec<(usize, Option<String>)>> =
        std::collections::HashMap::new();
    for patch in patches {
        if let Patch::MoveChild { path, .. } = patch {
            move_sources.entry(path.clone()).or_insert_with(|| {
                if let Some(target) = get_node(root, path) {
                    target
                        .children
                        .iter()
                        .enumerate()
                        .map(|(i, c)| (i, c.djust_id.clone()))
                        .collect()
                } else {
                    Vec::new()
                }
            });
        }
    }

    // Collect child mutation patches.
    let mut removes: Vec<(&Vec<usize>, usize)> = Vec::new();
    let mut inserts: Vec<(&Vec<usize>, usize, &VNode)> = Vec::new();
    let mut moves: Vec<&Patch> = Vec::new();
    let mut other_patches: Vec<&Patch> = Vec::new();

    for patch in patches {
        match patch {
            Patch::RemoveChild { path, index, .. } => removes.push((path, *index)),
            Patch::InsertChild {
                path, index, node, ..
            } => inserts.push((path, *index, node)),
            Patch::MoveChild { .. } => moves.push(patch),
            _ => other_patches.push(patch),
        }
    }

    // Apply removes in descending index order so earlier indices stay valid.
    removes.sort_by(|a, b| a.0.cmp(b.0).then_with(|| b.1.cmp(&a.1)));
    for (path, index) in &removes {
        if let Some(target) = get_node_mut(root, path) {
            if *index < target.children.len() {
                target.children.remove(*index);
            }
        }
    }

    // Apply moves with djust_id resolution.
    for patch in &moves {
        if let Patch::MoveChild { path, from, to, .. } = patch {
            let child_id = move_sources.get(path).and_then(|sources| {
                sources
                    .iter()
                    .find(|(i, _)| *i == *from)
                    .and_then(|(_, id)| id.clone())
            });

            if let Some(target) = get_node_mut(root, path) {
                if let Some(ref child_id) = child_id {
                    if let Some(current_pos) = target
                        .children
                        .iter()
                        .position(|c| c.djust_id.as_deref() == Some(child_id.as_str()))
                    {
                        let node = target.children.remove(current_pos);
                        let insert_at = (*to).min(target.children.len());
                        target.children.insert(insert_at, node);
                    }
                } else if *from < target.children.len() && *to <= target.children.len() {
                    let node = target.children.remove(*from);
                    target.children.insert(*to, node);
                }
            }
        }
    }

    // Apply inserts in ascending index order.
    inserts.sort_by(|a, b| a.0.cmp(b.0).then_with(|| a.1.cmp(&b.1)));
    for (path, index, node) in &inserts {
        if let Some(target) = get_node_mut(root, path) {
            let insert_at = (*index).min(target.children.len());
            target.children.insert(insert_at, (*node).clone());
        }
    }

    // Apply non-child-mutation patches (SetText, SetAttr, etc.) after
    // child mutations, since their paths reference the final tree layout.
    for patch in &other_patches {
        apply_patch(root, patch);
    }
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
                if *from < target.children.len() && *to <= target.children.len() {
                    let node = target.children.remove(*from);
                    target.children.insert(*to, node);
                }
            }
        }
    }
}

fn get_node<'a>(root: &'a VNode, path: &[usize]) -> Option<&'a VNode> {
    let mut current = root;

    for &index in path {
        if index < current.children.len() {
            current = &current.children[index];
        } else {
            return None;
        }
    }

    Some(current)
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
        };

        apply_patch(&mut root, &patch);
        assert_eq!(root.children.len(), 1);
    }
}
