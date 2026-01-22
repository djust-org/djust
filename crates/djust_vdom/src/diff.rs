//! Fast virtual DOM diffing algorithm
//!
//! Uses a keyed diffing algorithm for efficient list updates.
//! Includes compact djust_id (data-dj) in patches for O(1) client-side resolution.

use crate::{Patch, VNode};
use std::collections::HashMap;

/// Diff two VNodes and generate patches.
///
/// Each patch includes:
/// - `path`: Index-based path (fallback)
/// - `d`: Target element's djust_id for O(1) querySelector lookup
///
/// IMPORTANT: We use the OLD node's djust_id for targeting because that's what
/// exists in the client DOM. The new node may have different IDs if the server
/// re-parsed the HTML with a reset ID counter.
pub fn diff_nodes(old: &VNode, new: &VNode, path: &[usize]) -> Vec<Patch> {
    let mut patches = Vec::new();

    // Use OLD node's djust_id for targeting - that's what's in the client DOM
    let target_id = old.djust_id.clone();

    // If tags differ, replace the whole node
    if old.tag != new.tag {
        patches.push(Patch::Replace {
            path: path.to_vec(),
            d: target_id,
            node: new.clone(),
        });
        return patches;
    }

    // Diff text content (text nodes don't have djust_ids)
    if old.is_text() {
        if old.text != new.text {
            if let Some(text) = &new.text {
                patches.push(Patch::SetText {
                    path: path.to_vec(),
                    d: None, // Text nodes don't have IDs
                    text: text.clone(),
                });
            }
        }
        return patches;
    }

    // Diff attributes
    patches.extend(diff_attrs(old, new, path, &target_id));

    // Diff children (parent's djust_id is used for child operations)
    patches.extend(diff_children(old, new, path, &target_id));

    patches
}

fn diff_attrs(old: &VNode, new: &VNode, path: &[usize], target_id: &Option<String>) -> Vec<Patch> {
    let mut patches = Vec::new();

    // Find removed and changed attributes
    for (key, old_value) in &old.attrs {
        // Skip data-dj attribute - it's managed by the parser and shouldn't generate patches
        if key == "data-dj" {
            continue;
        }

        match new.attrs.get(key) {
            None => {
                patches.push(Patch::RemoveAttr {
                    path: path.to_vec(),
                    d: target_id.clone(),
                    key: key.clone(),
                });
            }
            Some(new_value) if new_value != old_value => {
                patches.push(Patch::SetAttr {
                    path: path.to_vec(),
                    d: target_id.clone(),
                    key: key.clone(),
                    value: new_value.clone(),
                });
            }
            _ => {}
        }
    }

    // Find added attributes
    for (key, new_value) in &new.attrs {
        // Skip data-dj attribute
        if key == "data-dj" {
            continue;
        }

        if !old.attrs.contains_key(key) {
            patches.push(Patch::SetAttr {
                path: path.to_vec(),
                d: target_id.clone(),
                key: key.clone(),
                value: new_value.clone(),
            });
        }
    }

    patches
}

/// Diff children of two nodes.
/// `parent_id` is the djust_id of the parent element, used for child operations.
fn diff_children(
    old: &VNode,
    new: &VNode,
    path: &[usize],
    parent_id: &Option<String>,
) -> Vec<Patch> {
    let mut patches = Vec::new();

    // Check if we can use keyed diffing
    let has_keys = new.children.iter().any(|n| n.key.is_some());

    if has_keys {
        patches.extend(diff_keyed_children(
            &old.children,
            &new.children,
            path,
            parent_id,
        ));
    } else {
        patches.extend(diff_indexed_children(
            &old.children,
            &new.children,
            path,
            parent_id,
        ));
    }

    patches
}

fn diff_keyed_children(
    old: &[VNode],
    new: &[VNode],
    path: &[usize],
    parent_id: &Option<String>,
) -> Vec<Patch> {
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
                d: parent_id.clone(),
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
                        d: parent_id.clone(),
                        index: new_idx,
                        node: new_node.clone(),
                    });
                }
                Some(&old_idx) => {
                    // Existing node - check if it moved
                    if old_idx != new_idx {
                        patches.push(Patch::MoveChild {
                            path: path.to_vec(),
                            d: parent_id.clone(),
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

fn diff_indexed_children(
    old: &[VNode],
    new: &[VNode],
    path: &[usize],
    parent_id: &Option<String>,
) -> Vec<Patch> {
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
                d: parent_id.clone(),
                index: i,
            });
        }
    }

    // Add new children
    if new_len > old_len {
        #[allow(clippy::needless_range_loop)]
        for i in old_len..new_len {
            patches.push(Patch::InsertChild {
                path: path.to_vec(),
                d: parent_id.clone(),
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
        let old = VNode::element("div").with_attr("class", "old").with_djust_id("0");
        let new = VNode::element("div").with_attr("class", "new").with_djust_id("0");
        let patches = diff_nodes(&old, &new, &[]);

        assert!(patches.iter().any(
            |p| matches!(p, Patch::SetAttr { key, value, d, .. } if key == "class" && value == "new" && d == &Some("0".to_string()))
        ));
    }

    #[test]
    fn test_diff_children_insert() {
        let old = VNode::element("div").with_djust_id("0");
        let new = VNode::element("div").with_djust_id("0").with_child(VNode::text("child"));
        let patches = diff_nodes(&old, &new, &[]);

        assert!(patches
            .iter()
            .any(|p| matches!(p, Patch::InsertChild { d, .. } if d == &Some("0".to_string()))));
    }

    #[test]
    fn test_diff_replace_tag() {
        let old = VNode::element("div").with_djust_id("0");
        let new = VNode::element("span").with_djust_id("1");
        let patches = diff_nodes(&old, &new, &[]);

        assert_eq!(patches.len(), 1);
        // Use OLD node's ID for targeting - that's what's in the client DOM
        assert!(matches!(&patches[0], Patch::Replace { d, .. } if d == &Some("0".to_string())));
    }

    #[test]
    fn test_diff_with_whitespace_text_nodes() {
        // Simulate what html5ever creates: element children interspersed with whitespace text nodes
        // This is the structure we see in the real bug: form has 11 children in Rust VDOM
        // (elements at even indices 0,2,4,6,8,10 and whitespace at odd indices 1,3,5,7,9)
        let old = VNode::element("form").with_djust_id("0").with_children(vec![
            VNode::element("div").with_attr("class", "mb-3").with_djust_id("1"),
            VNode::text("\n            "),
            VNode::element("div").with_attr("class", "mb-3").with_djust_id("2"),
            VNode::text("\n            "),
            VNode::element("div").with_attr("class", "mb-3").with_djust_id("3"),
            VNode::text("\n            "),
            VNode::element("button").with_djust_id("4"),
            VNode::text("\n        "),
        ]);

        // After removing some validation error divs, we have fewer element children
        let new = VNode::element("form").with_djust_id("0").with_children(vec![
            VNode::element("div").with_attr("class", "mb-3").with_djust_id("1"),
            VNode::text("\n            "),
            VNode::element("div").with_attr("class", "mb-3").with_djust_id("2"),
            VNode::text("\n            "),
            VNode::element("button").with_djust_id("4"),
            VNode::text("\n        "),
        ]);

        let patches = diff_nodes(&old, &new, &[0, 0, 0, 1, 2]);

        // Should generate RemoveChild patches for indices 6 and 7 (removed in reverse order)
        // Parent ID should be "0" (the form)
        assert!(patches
            .iter()
            .any(|p| matches!(p, Patch::RemoveChild { index: 7, d, .. } if d == &Some("0".to_string()))));
        assert!(patches
            .iter()
            .any(|p| matches!(p, Patch::RemoveChild { index: 6, d, .. } if d == &Some("0".to_string()))));
    }

    #[test]
    fn test_form_validation_error_removal() {
        // Simulates the exact bug we encountered:
        // Form field with conditional validation error div
        //
        // Before: <div class="mb-3">
        //           <input>
        //           <div class="invalid-feedback">Error message</div>
        //         </div>
        //
        // After:  <div class="mb-3">
        //           <input>
        //         </div>

        let old_field = VNode::element("div")
            .with_attr("class", "mb-3")
            .with_djust_id("0")
            .with_children(vec![
                VNode::element("input").with_attr("class", "form-control is-invalid").with_djust_id("1"),
                VNode::text("\n                "),
                VNode::element("div")
                    .with_attr("class", "invalid-feedback")
                    .with_djust_id("2")
                    .with_child(VNode::text("Username is required")),
                VNode::text("\n            "),
            ]);

        let new_field = VNode::element("div")
            .with_attr("class", "mb-3")
            .with_djust_id("0")
            .with_children(vec![
                VNode::element("input").with_attr("class", "form-control").with_djust_id("1"),
                VNode::text("\n                "),
                VNode::text("\n            "),
            ]);

        let patches = diff_nodes(&old_field, &new_field, &[0, 0, 0, 1, 2, 7]);

        // Should remove the "is-invalid" class from input
        // The patch should include the target's djust_id ("1")
        assert!(patches.iter().any(|p| matches!(p,
            Patch::SetAttr { key, value, d, .. }
            if key == "class" && value == "form-control" && d == &Some("1".to_string())
        )));

        // Should remove the validation error div at index 3
        // Parent ID should be "0"
        assert!(patches
            .iter()
            .any(|p| matches!(p, Patch::RemoveChild { index: 3, d, .. } if d == &Some("0".to_string()))));
    }

    #[test]
    fn test_multiple_conditional_divs_removal() {
        // Test the scenario where multiple form fields have validation errors cleared
        // This creates patches targeting multiple child indices
        let form_old = VNode::element("form").with_djust_id("form").with_children(vec![
            // Field 1 WITH error
            VNode::element("div")
                .with_attr("class", "mb-3")
                .with_djust_id("f1")
                .with_children(vec![
                    VNode::element("input").with_djust_id("i1"),
                    VNode::element("div").with_attr("class", "invalid-feedback").with_djust_id("e1"),
                ]),
            VNode::text("\n            "),
            // Field 2 WITH error
            VNode::element("div")
                .with_attr("class", "mb-3")
                .with_djust_id("f2")
                .with_children(vec![
                    VNode::element("input").with_djust_id("i2"),
                    VNode::element("div").with_attr("class", "invalid-feedback").with_djust_id("e2"),
                ]),
            VNode::text("\n            "),
            // Submit button
            VNode::element("button").with_djust_id("btn"),
        ]);

        let form_new = VNode::element("form").with_djust_id("form").with_children(vec![
            // Field 1 WITHOUT error
            VNode::element("div")
                .with_attr("class", "mb-3")
                .with_djust_id("f1")
                .with_children(vec![VNode::element("input").with_djust_id("i1")]),
            VNode::text("\n            "),
            // Field 2 WITHOUT error
            VNode::element("div")
                .with_attr("class", "mb-3")
                .with_djust_id("f2")
                .with_children(vec![VNode::element("input").with_djust_id("i2")]),
            VNode::text("\n            "),
            // Submit button
            VNode::element("button").with_djust_id("btn"),
        ]);

        let patches = diff_nodes(&form_old, &form_new, &[0, 0, 0, 1, 2]);

        // Should generate patches to remove validation error divs from both fields
        // Each RemoveChild should have the parent's djust_id
        let remove_patches: Vec<_> = patches
            .iter()
            .filter(|p| matches!(p, Patch::RemoveChild { .. }))
            .collect();

        assert_eq!(
            remove_patches.len(),
            2,
            "Should remove 2 validation error divs"
        );

        // Verify parent IDs are included
        assert!(remove_patches.iter().any(|p| matches!(p, Patch::RemoveChild { d, .. } if d == &Some("f1".to_string()))));
        assert!(remove_patches.iter().any(|p| matches!(p, Patch::RemoveChild { d, .. } if d == &Some("f2".to_string()))));
    }

    #[test]
    fn test_path_traversal_with_whitespace() {
        // Ensure patches have correct paths when whitespace nodes are present
        // Path should account for ALL children including whitespace
        let old = VNode::element("div").with_djust_id("0").with_children(vec![
            VNode::element("span").with_djust_id("1").with_child(VNode::text("A")),
            VNode::text("\n    "), // whitespace at index 1
            VNode::element("span").with_djust_id("2").with_child(VNode::text("B")),
            VNode::text("\n    "), // whitespace at index 3
            VNode::element("span").with_djust_id("3").with_child(VNode::text("C")),
        ]);

        let new = VNode::element("div").with_djust_id("0").with_children(vec![
            VNode::element("span").with_djust_id("1").with_child(VNode::text("A")),
            VNode::text("\n    "), // whitespace at index 1
            VNode::element("span").with_djust_id("2").with_child(VNode::text("B-modified")), // Changed
            VNode::text("\n    "), // whitespace at index 3
            VNode::element("span").with_djust_id("3").with_child(VNode::text("C")),
        ]);

        let patches = diff_nodes(&old, &new, &[5]);

        // The text change in the second span should have path [5, 2, 0]
        // Text nodes don't have djust_ids (d should be None)
        assert!(patches.iter().any(|p| matches!(p,
            Patch::SetText { path, text, d, .. }
            if path == &[5, 2, 0] && text == "B-modified" && d.is_none()
        )));
    }

    #[test]
    fn test_djust_id_included_in_patches() {
        // Verify that patches include the djust_id for client-side resolution
        let old = VNode::element("div")
            .with_djust_id("abc")
            .with_attr("class", "old");
        let new = VNode::element("div")
            .with_djust_id("abc")
            .with_attr("class", "new");

        let patches = diff_nodes(&old, &new, &[]);

        assert_eq!(patches.len(), 1);
        match &patches[0] {
            Patch::SetAttr { d, key, value, .. } => {
                assert_eq!(d, &Some("abc".to_string()));
                assert_eq!(key, "class");
                assert_eq!(value, "new");
            }
            _ => panic!("Expected SetAttr patch"),
        }
    }

    #[test]
    fn test_data_d_attr_not_diffed() {
        // Ensure that data-dj attribute changes don't generate patches
        // (the parser handles data-dj, diffing should ignore it)
        let old = VNode::element("div")
            .with_djust_id("old")
            .with_attr("data-dj", "old")
            .with_attr("class", "same");
        let new = VNode::element("div")
            .with_djust_id("new")
            .with_attr("data-dj", "new")
            .with_attr("class", "same");

        let patches = diff_nodes(&old, &new, &[]);

        // Should be empty - no attribute changes (data-dj is ignored)
        assert!(patches.is_empty(), "data-dj changes should not generate patches");
    }
}
