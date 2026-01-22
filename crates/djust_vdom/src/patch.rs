//! Patch application utilities
//!
//! Utilities for applying patches to virtual DOM trees.
//! In the LiveView system, patches are serialized and sent to the client.

use crate::{Patch, VNode};

/// Apply a patch to a virtual DOM tree (for testing purposes)
///
/// Note: The `d` field (djust_id) is used for client-side resolution
/// and is ignored here since we're working with in-memory VNodes.
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
