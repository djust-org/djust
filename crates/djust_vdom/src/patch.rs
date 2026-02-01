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
    // The keyed diff engine emits all paths referencing the FINAL tree layout.
    // To apply them correctly we process patches level-by-level (shallowest
    // parent path first). At each level we apply: removes (descending index),
    // moves (by djust_id), then inserts (ascending index). This ensures that
    // structural changes at a parent level establish the correct tree shape
    // before deeper subtree patches navigate into children.
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

    // Categorize patches by their parent path depth for level-by-level application.
    // Child mutation patches (Insert/Remove/Move) operate on the node at `path`,
    // so their "level" is the length of `path`. Other patches (SetText, SetAttr)
    // operate on the node AT `path`, so their level is path.len() - 1 (parent),
    // but we apply them after all child mutations at the same depth.
    struct LevelPatches<'a> {
        removes: Vec<(&'a Vec<usize>, usize)>,
        inserts: Vec<(&'a Vec<usize>, usize, &'a VNode)>,
        moves: Vec<&'a Patch>,
        others: Vec<&'a Patch>,
    }

    let mut levels: std::collections::BTreeMap<usize, LevelPatches<'_>> =
        std::collections::BTreeMap::new();

    for patch in patches {
        match patch {
            Patch::RemoveChild { path, index, .. } => {
                let level = levels.entry(path.len()).or_insert_with(|| LevelPatches {
                    removes: Vec::new(),
                    inserts: Vec::new(),
                    moves: Vec::new(),
                    others: Vec::new(),
                });
                level.removes.push((path, *index));
            }
            Patch::InsertChild {
                path, index, node, ..
            } => {
                let level = levels.entry(path.len()).or_insert_with(|| LevelPatches {
                    removes: Vec::new(),
                    inserts: Vec::new(),
                    moves: Vec::new(),
                    others: Vec::new(),
                });
                level.inserts.push((path, *index, node));
            }
            Patch::MoveChild { path, .. } => {
                let level = levels.entry(path.len()).or_insert_with(|| LevelPatches {
                    removes: Vec::new(),
                    inserts: Vec::new(),
                    moves: Vec::new(),
                    others: Vec::new(),
                });
                level.moves.push(patch);
            }
            _ => {
                let depth = match patch {
                    Patch::SetText { path, .. }
                    | Patch::SetAttr { path, .. }
                    | Patch::RemoveAttr { path, .. }
                    | Patch::Replace { path, .. } => path.len(),
                    _ => 0,
                };
                let level = levels.entry(depth).or_insert_with(|| LevelPatches {
                    removes: Vec::new(),
                    inserts: Vec::new(),
                    moves: Vec::new(),
                    others: Vec::new(),
                });
                level.others.push(patch);
            }
        }
    }

    // Process levels from shallowest to deepest.
    for level in levels.values_mut() {
        // Apply removes in descending index order so earlier indices stay valid.
        level
            .removes
            .sort_by(|a, b| a.0.cmp(b.0).then_with(|| b.1.cmp(&a.1)));
        for (path, index) in &level.removes {
            if let Some(target) = get_node_mut(root, path) {
                if *index < target.children.len() {
                    target.children.remove(*index);
                }
            }
        }

        // Apply moves with djust_id resolution.
        for patch in &level.moves {
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
        level
            .inserts
            .sort_by(|a, b| a.0.cmp(b.0).then_with(|| a.1.cmp(&b.1)));
        for (path, index, node) in &level.inserts {
            if let Some(target) = get_node_mut(root, path) {
                let insert_at = (*index).min(target.children.len());
                target.children.insert(insert_at, (*node).clone());
            }
        }

        // Apply non-child-mutation patches at this level.
        for patch in &level.others {
            apply_patch(root, patch);
        }
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
