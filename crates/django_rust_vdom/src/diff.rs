//! Fast virtual DOM diffing algorithm
//!
//! Uses a keyed diffing algorithm for efficient list updates.

use crate::{Patch, VNode};
use std::collections::HashMap;

pub fn diff_nodes(old: &VNode, new: &VNode, path: &[usize]) -> Vec<Patch> {
    let mut patches = Vec::new();

    // If tags differ, replace the whole node
    if old.tag != new.tag {
        patches.push(Patch::Replace {
            path: path.to_vec(),
            node: new.clone(),
        });
        return patches;
    }

    // Diff text content
    if old.is_text() {
        if old.text != new.text {
            if let Some(text) = &new.text {
                patches.push(Patch::SetText {
                    path: path.to_vec(),
                    text: text.clone(),
                });
            }
        }
        return patches;
    }

    // Diff attributes
    patches.extend(diff_attrs(old, new, path));

    // Diff children
    patches.extend(diff_children(&old.children, &new.children, path));

    patches
}

fn diff_attrs(old: &VNode, new: &VNode, path: &[usize]) -> Vec<Patch> {
    let mut patches = Vec::new();

    // Find removed and changed attributes
    for (key, old_value) in &old.attrs {
        match new.attrs.get(key) {
            None => {
                patches.push(Patch::RemoveAttr {
                    path: path.to_vec(),
                    key: key.clone(),
                });
            }
            Some(new_value) if new_value != old_value => {
                patches.push(Patch::SetAttr {
                    path: path.to_vec(),
                    key: key.clone(),
                    value: new_value.clone(),
                });
            }
            _ => {}
        }
    }

    // Find added attributes
    for (key, new_value) in &new.attrs {
        if !old.attrs.contains_key(key) {
            patches.push(Patch::SetAttr {
                path: path.to_vec(),
                key: key.clone(),
                value: new_value.clone(),
            });
        }
    }

    patches
}

fn diff_children(old: &[VNode], new: &[VNode], path: &[usize]) -> Vec<Patch> {
    let mut patches = Vec::new();

    // Check if we can use keyed diffing
    let has_keys = new.iter().any(|n| n.key.is_some());

    if has_keys {
        patches.extend(diff_keyed_children(old, new, path));
    } else {
        patches.extend(diff_indexed_children(old, new, path));
    }

    patches
}

fn diff_keyed_children(old: &[VNode], new: &[VNode], path: &[usize]) -> Vec<Patch> {
    let mut patches = Vec::new();

    // Build key-to-index maps
    let old_keys: HashMap<String, usize> = old
        .iter()
        .enumerate()
        .filter_map(|(i, node)| node.key.as_ref().map(|k| (k.clone(), i)))
        .collect();

    let new_keys: HashMap<String, usize> = new
        .iter()
        .enumerate()
        .filter_map(|(i, node)| node.key.as_ref().map(|k| (k.clone(), i)))
        .collect();

    // Find nodes to remove
    for (key, &old_idx) in &old_keys {
        if !new_keys.contains_key(key) {
            patches.push(Patch::RemoveChild {
                path: path.to_vec(),
                index: old_idx,
            });
        }
    }

    // Find nodes to add or move
    for (new_idx, new_node) in new.iter().enumerate() {
        if let Some(key) = &new_node.key {
            match old_keys.get(key) {
                None => {
                    // New node
                    patches.push(Patch::InsertChild {
                        path: path.to_vec(),
                        index: new_idx,
                        node: new_node.clone(),
                    });
                }
                Some(&old_idx) => {
                    // Existing node - check if it moved
                    if old_idx != new_idx {
                        patches.push(Patch::MoveChild {
                            path: path.to_vec(),
                            from: old_idx,
                            to: new_idx,
                        });
                    }

                    // Diff the node itself
                    let mut child_path = path.to_vec();
                    child_path.push(new_idx);
                    patches.extend(diff_nodes(&old[old_idx], new_node, &child_path));
                }
            }
        }
    }

    patches
}

fn diff_indexed_children(old: &[VNode], new: &[VNode], path: &[usize]) -> Vec<Patch> {
    let mut patches = Vec::new();
    let old_len = old.len();
    let new_len = new.len();

    // Diff common children
    for i in 0..old_len.min(new_len) {
        let mut child_path = path.to_vec();
        child_path.push(i);
        patches.extend(diff_nodes(&old[i], &new[i], &child_path));
    }

    // Remove extra old children
    if old_len > new_len {
        for i in (new_len..old_len).rev() {
            patches.push(Patch::RemoveChild {
                path: path.to_vec(),
                index: i,
            });
        }
    }

    // Add new children
    if new_len > old_len {
        for i in old_len..new_len {
            patches.push(Patch::InsertChild {
                path: path.to_vec(),
                index: i,
                node: new[i].clone(),
            });
        }
    }

    patches
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_diff_text_change() {
        let old = VNode::text("Hello");
        let new = VNode::text("World");
        let patches = diff_nodes(&old, &new, &[]);

        assert_eq!(patches.len(), 1);
        match &patches[0] {
            Patch::SetText { text, .. } => assert_eq!(text, "World"),
            _ => panic!("Expected SetText patch"),
        }
    }

    #[test]
    fn test_diff_attr_change() {
        let old = VNode::element("div").with_attr("class", "old");
        let new = VNode::element("div").with_attr("class", "new");
        let patches = diff_nodes(&old, &new, &[]);

        assert!(patches.iter().any(|p| matches!(p, Patch::SetAttr { key, value, .. } if key == "class" && value == "new")));
    }

    #[test]
    fn test_diff_children_insert() {
        let old = VNode::element("div");
        let new = VNode::element("div").with_child(VNode::text("child"));
        let patches = diff_nodes(&old, &new, &[]);

        assert!(patches.iter().any(|p| matches!(p, Patch::InsertChild { .. })));
    }

    #[test]
    fn test_diff_replace_tag() {
        let old = VNode::element("div");
        let new = VNode::element("span");
        let patches = diff_nodes(&old, &new, &[]);

        assert_eq!(patches.len(), 1);
        assert!(matches!(patches[0], Patch::Replace { .. }));
    }
}
