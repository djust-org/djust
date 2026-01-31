//! Fast virtual DOM diffing algorithm
//!
//! Uses a keyed diffing algorithm for efficient list updates.
//! Includes compact djust_id (data-dj-id) in patches for O(1) client-side resolution.
//!
//! ## Debugging
//!
//! Set `DJUST_VDOM_TRACE=1` environment variable to enable detailed tracing
//! of the diffing algorithm. This logs:
//! - Node comparisons with IDs
//! - Attribute changes
//! - Child diffing decisions
//! - Generated patches

use crate::{vdom_trace, Patch, VNode};
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

    // Trace: log node comparison
    vdom_trace!(
        "diff_nodes: path={:?} old_tag={} new_tag={} old_id={:?} new_id={:?}",
        path,
        old.tag,
        new.tag,
        old.djust_id,
        new.djust_id
    );

    // If tags differ, replace the whole node
    if old.tag != new.tag {
        vdom_trace!(
            "TAG MISMATCH: replacing <{}> (id={:?}) with <{}>",
            old.tag,
            target_id,
            new.tag
        );
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
            vdom_trace!(
                "TEXT CHANGE: path={:?} old={:?} new={:?}",
                path,
                old.text
                    .as_ref()
                    .map(|t| t.chars().take(50).collect::<String>()),
                new.text
                    .as_ref()
                    .map(|t| t.chars().take(50).collect::<String>())
            );
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
        // Skip data-dj-id attribute - it's managed by the parser and shouldn't generate patches
        if key == "data-dj-id" {
            continue;
        }

        match new.attrs.get(key) {
            None => {
                // Never remove dj-* event handler attributes — these must be preserved
                // to protect against VDOM path mismatches when conditional rendering
                // changes the DOM structure (e.g., {% if conversation %} adds elements,
                // shifting indices so diff incorrectly matches unrelated elements).
                if key.starts_with("dj-") {
                    continue;
                }
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
        // Skip data-dj-id attribute
        if key == "data-dj-id" {
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

    // Check for data-djust-replace attribute - if present, replace all children
    // instead of diffing them. This is useful for containers where content
    // changes completely (like switching conversations in a chat app).
    let should_replace = old.attrs.contains_key("data-djust-replace")
        || new.attrs.contains_key("data-djust-replace");

    if should_replace {
        vdom_trace!(
            "diff_children: parent_id={:?} - REPLACE MODE (data-djust-replace)",
            parent_id
        );
        return replace_all_children(old, new, path, parent_id);
    }

    // Check if we can use keyed diffing
    let has_keys = new.children.iter().any(|n| n.key.is_some());

    vdom_trace!(
        "diff_children: path={:?} parent_id={:?} old_children={} new_children={} has_keys={}",
        path,
        parent_id,
        old.children.len(),
        new.children.len(),
        has_keys
    );

    if has_keys {
        // Warn about mixed keyed/unkeyed children — a common source of suboptimal diffs.
        // Keyed siblings should ideally ALL have keys for best diffing performance.
        if !new.children.iter().all(|n| n.key.is_some()) {
            let keyed_count = new.children.iter().filter(|n| n.key.is_some()).count();
            let total = new.children.len();
            vdom_trace!(
                "  WARNING: Mixed keyed/unkeyed children ({}/{} keyed). \
                 For optimal diffing, add keys to all siblings or none. \
                 Parent tag=<{}> id={:?}",
                keyed_count,
                total,
                new.tag,
                parent_id
            );
        }

        vdom_trace!("  Using KEYED diffing");
        patches.extend(diff_keyed_children(
            &old.children,
            &new.children,
            path,
            parent_id,
        ));
    } else {
        vdom_trace!("  Using INDEXED diffing");
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

    vdom_trace!(
        "diff_keyed_children: old_keys={:?} new_keys={:?}",
        old_keys.keys().collect::<Vec<_>>(),
        new_keys.keys().collect::<Vec<_>>()
    );

    // Find keyed nodes to remove
    for (key, &old_idx) in &old_keys {
        if !new_keys.contains_key(key) {
            vdom_trace!("  REMOVE key={} from old_idx={}", key, old_idx);
            patches.push(Patch::RemoveChild {
                path: path.to_vec(),
                d: parent_id.clone(),
                index: old_idx,
            });
        }
    }

    // Track which indices have been processed (keyed children)
    let mut processed_old_indices: std::collections::HashSet<usize> =
        std::collections::HashSet::new();
    let mut processed_new_indices: std::collections::HashSet<usize> =
        std::collections::HashSet::new();

    // Find keyed nodes to add, move, or diff
    for (new_idx, new_node) in new.iter().enumerate() {
        if let Some(key) = &new_node.key {
            processed_new_indices.insert(new_idx);
            match old_keys.get(key) {
                None => {
                    // New keyed node
                    vdom_trace!("  INSERT key={} at new_idx={}", key, new_idx);
                    patches.push(Patch::InsertChild {
                        path: path.to_vec(),
                        d: parent_id.clone(),
                        index: new_idx,
                        node: new_node.clone(),
                    });
                }
                Some(&old_idx) => {
                    processed_old_indices.insert(old_idx);

                    // Existing keyed node - check if it moved
                    if old_idx != new_idx {
                        vdom_trace!("  MOVE key={} from {} to {}", key, old_idx, new_idx);
                        patches.push(Patch::MoveChild {
                            path: path.to_vec(),
                            d: parent_id.clone(),
                            from: old_idx,
                            to: new_idx,
                        });
                    }

                    // Diff the keyed node itself
                    vdom_trace!("  DIFF key={} old_idx={} new_idx={}", key, old_idx, new_idx);
                    let mut child_path = path.to_vec();
                    child_path.push(new_idx);
                    patches.extend(diff_nodes(&old[old_idx], new_node, &child_path));
                }
            }
        }
    }

    // Collect unkeyed children by their relative order (not absolute index)
    // This ensures correct matching when keyed children shift positions
    let old_unkeyed: Vec<usize> = old
        .iter()
        .enumerate()
        .filter(|(i, n)| n.key.is_none() && !processed_old_indices.contains(i))
        .map(|(i, _)| i)
        .collect();

    let new_unkeyed: Vec<usize> = new
        .iter()
        .enumerate()
        .filter(|(i, n)| n.key.is_none() && !processed_new_indices.contains(i))
        .map(|(i, _)| i)
        .collect();

    vdom_trace!(
        "  Unkeyed children: old={:?} new={:?}",
        old_unkeyed,
        new_unkeyed
    );

    // Diff common unkeyed children by relative position
    let common_len = old_unkeyed.len().min(new_unkeyed.len());
    for i in 0..common_len {
        let old_idx = old_unkeyed[i];
        let new_idx = new_unkeyed[i];
        processed_old_indices.insert(old_idx);
        processed_new_indices.insert(new_idx);
        vdom_trace!(
            "  DIFF unkeyed: old[{}] <-> new[{}] (relative pos {})",
            old_idx,
            new_idx,
            i
        );
        let mut child_path = path.to_vec();
        child_path.push(new_idx);
        patches.extend(diff_nodes(&old[old_idx], &new[new_idx], &child_path));
    }

    // Insert extra new unkeyed children
    for &new_idx in &new_unkeyed[common_len..] {
        vdom_trace!("  INSERT unkeyed at index {}", new_idx);
        patches.push(Patch::InsertChild {
            path: path.to_vec(),
            d: parent_id.clone(),
            index: new_idx,
            node: new[new_idx].clone(),
        });
    }

    // Remove extra old unkeyed children
    for &old_idx in &old_unkeyed[common_len..] {
        vdom_trace!("  REMOVE unkeyed at index {}", old_idx);
        patches.push(Patch::RemoveChild {
            path: path.to_vec(),
            d: parent_id.clone(),
            index: old_idx,
        });
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

    vdom_trace!(
        "diff_indexed_children: old_len={} new_len={} common={}",
        old_len,
        new_len,
        old_len.min(new_len)
    );

    // Diff common children
    for i in 0..old_len.min(new_len) {
        let mut child_path = path.to_vec();
        child_path.push(i);
        vdom_trace!(
            "  Comparing child[{}]: old=<{}> (id={:?}) vs new=<{}> (id={:?})",
            i,
            old[i].tag,
            old[i].djust_id,
            new[i].tag,
            new[i].djust_id
        );
        patches.extend(diff_nodes(&old[i], &new[i], &child_path));
    }

    // Remove extra old children
    if old_len > new_len {
        vdom_trace!(
            "  Removing {} extra children (indices {}-{})",
            old_len - new_len,
            new_len,
            old_len - 1
        );
        for i in (new_len..old_len).rev() {
            vdom_trace!("    RemoveChild index={} parent_id={:?}", i, parent_id);
            patches.push(Patch::RemoveChild {
                path: path.to_vec(),
                d: parent_id.clone(),
                index: i,
            });
        }
    }

    // Add new children
    if new_len > old_len {
        vdom_trace!(
            "  Adding {} new children (indices {}-{})",
            new_len - old_len,
            old_len,
            new_len - 1
        );
        #[allow(clippy::needless_range_loop)]
        for i in old_len..new_len {
            vdom_trace!(
                "    InsertChild index={} tag=<{}> parent_id={:?}",
                i,
                new[i].tag,
                parent_id
            );
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

/// Replace all children without diffing.
///
/// Used when a container has `data-djust-replace` attribute, indicating that
/// its content should be fully replaced rather than diffed. This is more
/// efficient for scenarios like conversation switching where the entire
/// content changes.
fn replace_all_children(
    old: &VNode,
    new: &VNode,
    path: &[usize],
    parent_id: &Option<String>,
) -> Vec<Patch> {
    let mut patches = Vec::new();
    let old_len = old.children.len();
    let new_len = new.children.len();

    vdom_trace!(
        "replace_all_children: removing {} old, inserting {} new",
        old_len,
        new_len
    );

    // Remove all old children (in reverse order to maintain indices)
    for i in (0..old_len).rev() {
        vdom_trace!("  RemoveChild index={}", i);
        patches.push(Patch::RemoveChild {
            path: path.to_vec(),
            d: parent_id.clone(),
            index: i,
        });
    }

    // Insert all new children
    for i in 0..new_len {
        vdom_trace!("  InsertChild index={} tag=<{}>", i, new.children[i].tag);
        patches.push(Patch::InsertChild {
            path: path.to_vec(),
            d: parent_id.clone(),
            index: i,
            node: new.children[i].clone(),
        });
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
        let old = VNode::element("div")
            .with_attr("class", "old")
            .with_djust_id("0");
        let new = VNode::element("div")
            .with_attr("class", "new")
            .with_djust_id("0");
        let patches = diff_nodes(&old, &new, &[]);

        assert!(patches.iter().any(
            |p| matches!(p, Patch::SetAttr { key, value, d, .. } if key == "class" && value == "new" && d == &Some("0".to_string()))
        ));
    }

    #[test]
    fn test_diff_children_insert() {
        let old = VNode::element("div").with_djust_id("0");
        let new = VNode::element("div")
            .with_djust_id("0")
            .with_child(VNode::text("child"));
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
        let old = VNode::element("form")
            .with_djust_id("0")
            .with_children(vec![
                VNode::element("div")
                    .with_attr("class", "mb-3")
                    .with_djust_id("1"),
                VNode::text("\n            "),
                VNode::element("div")
                    .with_attr("class", "mb-3")
                    .with_djust_id("2"),
                VNode::text("\n            "),
                VNode::element("div")
                    .with_attr("class", "mb-3")
                    .with_djust_id("3"),
                VNode::text("\n            "),
                VNode::element("button").with_djust_id("4"),
                VNode::text("\n        "),
            ]);

        // After removing some validation error divs, we have fewer element children
        let new = VNode::element("form")
            .with_djust_id("0")
            .with_children(vec![
                VNode::element("div")
                    .with_attr("class", "mb-3")
                    .with_djust_id("1"),
                VNode::text("\n            "),
                VNode::element("div")
                    .with_attr("class", "mb-3")
                    .with_djust_id("2"),
                VNode::text("\n            "),
                VNode::element("button").with_djust_id("4"),
                VNode::text("\n        "),
            ]);

        let patches = diff_nodes(&old, &new, &[0, 0, 0, 1, 2]);

        // Should generate RemoveChild patches for indices 6 and 7 (removed in reverse order)
        // Parent ID should be "0" (the form)
        assert!(patches.iter().any(
            |p| matches!(p, Patch::RemoveChild { index: 7, d, .. } if d == &Some("0".to_string()))
        ));
        assert!(patches.iter().any(
            |p| matches!(p, Patch::RemoveChild { index: 6, d, .. } if d == &Some("0".to_string()))
        ));
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
                VNode::element("input")
                    .with_attr("class", "form-control is-invalid")
                    .with_djust_id("1"),
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
                VNode::element("input")
                    .with_attr("class", "form-control")
                    .with_djust_id("1"),
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
        assert!(patches.iter().any(
            |p| matches!(p, Patch::RemoveChild { index: 3, d, .. } if d == &Some("0".to_string()))
        ));
    }

    #[test]
    fn test_multiple_conditional_divs_removal() {
        // Test the scenario where multiple form fields have validation errors cleared
        // This creates patches targeting multiple child indices
        let form_old = VNode::element("form")
            .with_djust_id("form")
            .with_children(vec![
                // Field 1 WITH error
                VNode::element("div")
                    .with_attr("class", "mb-3")
                    .with_djust_id("f1")
                    .with_children(vec![
                        VNode::element("input").with_djust_id("i1"),
                        VNode::element("div")
                            .with_attr("class", "invalid-feedback")
                            .with_djust_id("e1"),
                    ]),
                VNode::text("\n            "),
                // Field 2 WITH error
                VNode::element("div")
                    .with_attr("class", "mb-3")
                    .with_djust_id("f2")
                    .with_children(vec![
                        VNode::element("input").with_djust_id("i2"),
                        VNode::element("div")
                            .with_attr("class", "invalid-feedback")
                            .with_djust_id("e2"),
                    ]),
                VNode::text("\n            "),
                // Submit button
                VNode::element("button").with_djust_id("btn"),
            ]);

        let form_new = VNode::element("form")
            .with_djust_id("form")
            .with_children(vec![
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
        assert!(remove_patches
            .iter()
            .any(|p| matches!(p, Patch::RemoveChild { d, .. } if d == &Some("f1".to_string()))));
        assert!(remove_patches
            .iter()
            .any(|p| matches!(p, Patch::RemoveChild { d, .. } if d == &Some("f2".to_string()))));
    }

    #[test]
    fn test_path_traversal_with_whitespace() {
        // Ensure patches have correct paths when whitespace nodes are present
        // Path should account for ALL children including whitespace
        let old = VNode::element("div").with_djust_id("0").with_children(vec![
            VNode::element("span")
                .with_djust_id("1")
                .with_child(VNode::text("A")),
            VNode::text("\n    "), // whitespace at index 1
            VNode::element("span")
                .with_djust_id("2")
                .with_child(VNode::text("B")),
            VNode::text("\n    "), // whitespace at index 3
            VNode::element("span")
                .with_djust_id("3")
                .with_child(VNode::text("C")),
        ]);

        let new = VNode::element("div").with_djust_id("0").with_children(vec![
            VNode::element("span")
                .with_djust_id("1")
                .with_child(VNode::text("A")),
            VNode::text("\n    "), // whitespace at index 1
            VNode::element("span")
                .with_djust_id("2")
                .with_child(VNode::text("B-modified")), // Changed
            VNode::text("\n    "), // whitespace at index 3
            VNode::element("span")
                .with_djust_id("3")
                .with_child(VNode::text("C")),
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
        // Ensure that data-dj-id attribute changes don't generate patches
        // (the parser handles data-dj-id, diffing should ignore it)
        let old = VNode::element("div")
            .with_djust_id("old")
            .with_attr("data-dj-id", "old")
            .with_attr("class", "same");
        let new = VNode::element("div")
            .with_djust_id("new")
            .with_attr("data-dj-id", "new")
            .with_attr("class", "same");

        let patches = diff_nodes(&old, &new, &[]);

        // Should be empty - no attribute changes (data-dj-id is ignored)
        assert!(
            patches.is_empty(),
            "data-dj-id changes should not generate patches"
        );
    }

    #[test]
    fn test_conditional_content_change_empty_to_messages() {
        // Test the conversation switching scenario:
        // When switching from empty state to having messages, the diff generates
        // patches that morph the old structure by:
        // 1. Changing the class attribute
        // 2. Replacing/removing children
        //
        // This is valid behavior - the patches correctly target OLD element IDs
        // because that's what exists in the client DOM.

        let old_messages = VNode::element("div")
            .with_attr("class", "messages")
            .with_djust_id("messages")
            .with_children(vec![VNode::element("div")
                .with_attr("class", "messages__empty")
                .with_djust_id("empty")
                .with_children(vec![
                    VNode::element("h2")
                        .with_djust_id("h2")
                        .with_child(VNode::text("Start a new conversation")),
                    VNode::element("p")
                        .with_djust_id("p")
                        .with_child(VNode::text("Choose a model and type your message below.")),
                ])]);

        let new_messages = VNode::element("div")
            .with_attr("class", "messages")
            .with_djust_id("messages")
            .with_children(vec![VNode::element("div")
                .with_attr("class", "message message--user")
                .with_djust_id("msg1")
                .with_children(vec![VNode::element("div")
                    .with_attr("class", "message__content")
                    .with_djust_id("content1")
                    .with_child(VNode::text("Hello world"))])]);

        let patches = diff_nodes(&old_messages, &new_messages, &[]);

        // Verify patches are generated and target OLD element IDs (correct behavior)
        assert!(
            !patches.is_empty(),
            "Should generate patches for structural change"
        );

        // The patches should:
        // 1. SetAttr on "empty" to change class to "message message--user"
        // 2. Replace the h2 with the content div
        // 3. Remove the p element

        let has_class_change = patches.iter().any(|p| match p {
            Patch::SetAttr { d, key, value, .. } => {
                d == &Some("empty".to_string())
                    && key == "class"
                    && value == "message message--user"
            }
            _ => false,
        });
        assert!(
            has_class_change,
            "Should change class attribute on old 'empty' element"
        );

        // The h2 should be replaced (targeting OLD ID "h2")
        let has_h2_replace = patches.iter().any(|p| match p {
            Patch::Replace { d, .. } => d == &Some("h2".to_string()),
            _ => false,
        });
        assert!(
            has_h2_replace,
            "Should replace h2 with new content (targeting old h2 ID)"
        );

        // The p should be removed
        let has_p_remove = patches.iter().any(|p| match p {
            Patch::RemoveChild { d, index, .. } => d == &Some("empty".to_string()) && *index == 1,
            _ => false,
        });
        assert!(
            has_p_remove,
            "Should remove p element (index 1 of parent 'empty')"
        );
    }

    #[test]
    fn test_conditional_content_change_messages_to_empty() {
        // Reverse test: going from messages back to empty state
        // Patches should morph the message div into empty state div

        let old_messages = VNode::element("div")
            .with_attr("class", "messages")
            .with_djust_id("messages")
            .with_children(vec![VNode::element("div")
                .with_attr("class", "message message--user")
                .with_djust_id("msg1")
                .with_children(vec![VNode::element("div")
                    .with_attr("class", "message__content")
                    .with_djust_id("content1")
                    .with_child(VNode::text("Hello world"))])]);

        let new_messages = VNode::element("div")
            .with_attr("class", "messages")
            .with_djust_id("messages")
            .with_children(vec![VNode::element("div")
                .with_attr("class", "messages__empty")
                .with_djust_id("empty")
                .with_children(vec![
                    VNode::element("h2")
                        .with_djust_id("h2")
                        .with_child(VNode::text("Start a new conversation")),
                    VNode::element("p")
                        .with_djust_id("p")
                        .with_child(VNode::text("Choose a model and type your message below.")),
                ])]);

        let patches = diff_nodes(&old_messages, &new_messages, &[]);

        // Verify patches target OLD element IDs
        assert!(!patches.is_empty(), "Should generate patches");

        // Class should change on "msg1"
        let has_class_change = patches.iter().any(|p| match p {
            Patch::SetAttr { d, key, value, .. } => {
                d == &Some("msg1".to_string()) && key == "class" && value == "messages__empty"
            }
            _ => false,
        });
        assert!(
            has_class_change,
            "Should change class on old 'msg1' element"
        );

        // content1 should be replaced with h2
        let has_content_replace = patches.iter().any(|p| match p {
            Patch::Replace { d, node, .. } => {
                d == &Some("content1".to_string()) && node.tag == "h2"
            }
            _ => false,
        });
        assert!(has_content_replace, "Should replace content div with h2");

        // p should be inserted
        let has_p_insert = patches.iter().any(|p| match p {
            Patch::InsertChild { d, node, .. } => d == &Some("msg1".to_string()) && node.tag == "p",
            _ => false,
        });
        assert!(has_p_insert, "Should insert p element");
    }

    #[test]
    fn test_data_djust_replace_removes_and_inserts_all() {
        // Test that data-djust-replace causes all children to be replaced
        // instead of diffed position-by-position
        let old = VNode::element("div")
            .with_attr("class", "messages")
            .with_attr("data-djust-replace", "")
            .with_djust_id("container")
            .with_children(vec![
                VNode::element("div")
                    .with_djust_id("a1")
                    .with_child(VNode::text("Message A1")),
                VNode::element("div")
                    .with_djust_id("a2")
                    .with_child(VNode::text("Message A2")),
                VNode::element("div")
                    .with_djust_id("a3")
                    .with_child(VNode::text("Message A3")),
            ]);

        let new = VNode::element("div")
            .with_attr("class", "messages")
            .with_attr("data-djust-replace", "")
            .with_djust_id("container")
            .with_children(vec![
                VNode::element("div")
                    .with_djust_id("b1")
                    .with_child(VNode::text("Message B1")),
                VNode::element("div")
                    .with_djust_id("b2")
                    .with_child(VNode::text("Message B2")),
            ]);

        let patches = diff_nodes(&old, &new, &[]);

        // Should have 3 RemoveChild + 2 InsertChild = 5 patches
        // (not SetText patches which would indicate indexed diffing)
        let remove_count = patches
            .iter()
            .filter(|p| matches!(p, Patch::RemoveChild { .. }))
            .count();
        let insert_count = patches
            .iter()
            .filter(|p| matches!(p, Patch::InsertChild { .. }))
            .count();

        assert_eq!(remove_count, 3, "Should remove all 3 old children");
        assert_eq!(insert_count, 2, "Should insert all 2 new children");

        // Verify no SetText patches (which would indicate indexed diffing happened)
        let set_text_count = patches
            .iter()
            .filter(|p| matches!(p, Patch::SetText { .. }))
            .count();
        assert_eq!(
            set_text_count, 0,
            "Should NOT have SetText patches (replace mode bypasses diffing)"
        );
    }

    #[test]
    fn test_data_djust_replace_on_new_element() {
        // Test that data-djust-replace on the NEW element also triggers replace mode
        let old = VNode::element("div")
            .with_attr("class", "messages")
            .with_djust_id("container")
            .with_children(vec![VNode::element("div")
                .with_djust_id("a1")
                .with_child(VNode::text("Old"))]);

        let new = VNode::element("div")
            .with_attr("class", "messages")
            .with_attr("data-djust-replace", "")
            .with_djust_id("container")
            .with_children(vec![VNode::element("div")
                .with_djust_id("b1")
                .with_child(VNode::text("New"))]);

        let patches = diff_nodes(&old, &new, &[]);

        // Should use replace mode
        let remove_count = patches
            .iter()
            .filter(|p| matches!(p, Patch::RemoveChild { .. }))
            .count();
        let insert_count = patches
            .iter()
            .filter(|p| matches!(p, Patch::InsertChild { .. }))
            .count();

        assert_eq!(remove_count, 1, "Should remove old child");
        assert_eq!(insert_count, 1, "Should insert new child");
    }

    #[test]
    fn test_data_djust_replace_empty_to_content() {
        // Test replace mode when going from empty to having content
        let old = VNode::element("div")
            .with_attr("data-djust-replace", "")
            .with_djust_id("container");
        // No children

        let new = VNode::element("div")
            .with_attr("data-djust-replace", "")
            .with_djust_id("container")
            .with_children(vec![VNode::element("p")
                .with_djust_id("p1")
                .with_child(VNode::text("Content"))]);

        let patches = diff_nodes(&old, &new, &[]);

        let insert_count = patches
            .iter()
            .filter(|p| matches!(p, Patch::InsertChild { .. }))
            .count();
        assert_eq!(insert_count, 1, "Should insert new child");
    }

    #[test]
    fn test_data_djust_replace_content_to_empty() {
        // Test replace mode when going from content to empty
        let old = VNode::element("div")
            .with_attr("data-djust-replace", "")
            .with_djust_id("container")
            .with_children(vec![
                VNode::element("p")
                    .with_djust_id("p1")
                    .with_child(VNode::text("Content")),
                VNode::element("p")
                    .with_djust_id("p2")
                    .with_child(VNode::text("More content")),
            ]);

        let new = VNode::element("div")
            .with_attr("data-djust-replace", "")
            .with_djust_id("container");
        // No children

        let patches = diff_nodes(&old, &new, &[]);

        let remove_count = patches
            .iter()
            .filter(|p| matches!(p, Patch::RemoveChild { .. }))
            .count();
        assert_eq!(remove_count, 2, "Should remove both old children");
    }

    #[test]
    fn test_interleaved_keyed_and_unkeyed_children() {
        // Keyed children reorder while unkeyed children change content
        // old: [keyed-A, unkeyed-X, keyed-B]
        // new: [keyed-B, unkeyed-Y, keyed-A]
        let old = VNode::element("div")
            .with_djust_id("parent")
            .with_children(vec![
                VNode::element("div")
                    .with_key("a")
                    .with_djust_id("a")
                    .with_child(VNode::text("A content")),
                VNode::element("div")
                    .with_djust_id("x")
                    .with_child(VNode::text("X content")),
                VNode::element("div")
                    .with_key("b")
                    .with_djust_id("b")
                    .with_child(VNode::text("B content")),
            ]);

        let new = VNode::element("div")
            .with_djust_id("parent")
            .with_children(vec![
                VNode::element("div")
                    .with_key("b")
                    .with_djust_id("b2")
                    .with_child(VNode::text("B content")),
                VNode::element("div")
                    .with_djust_id("y")
                    .with_child(VNode::text("Y content")),
                VNode::element("div")
                    .with_key("a")
                    .with_djust_id("a2")
                    .with_child(VNode::text("A content")),
            ]);

        let patches = diff_nodes(&old, &new, &[]);

        // Should have patches for:
        // - Moving keyed children (a and b swapped)
        // - Diffing the unkeyed child (X -> Y text change)
        assert!(!patches.is_empty(), "Should generate patches");

        // The unkeyed child text should change from X to Y
        let has_text_change = patches
            .iter()
            .any(|p| matches!(p, Patch::SetText { text, .. } if text == "Y content"));
        assert!(
            has_text_change,
            "Should update unkeyed child text from X to Y. Patches: {:?}",
            patches
        );

        // Should NOT have duplicate patches for the same node
        // (i.e., no RemoveChild for the unkeyed node that was already diffed)
        let remove_count = patches
            .iter()
            .filter(|p| matches!(p, Patch::RemoveChild { .. }))
            .count();
        assert_eq!(
            remove_count, 0,
            "Should not remove any children (all matched). Patches: {:?}",
            patches
        );
    }

    #[test]
    fn test_adversarial_interleaved_keyed_unkeyed() {
        // Adversarial pattern: keyed and unkeyed children alternate and reorder
        // old: [unkeyed-X, keyed-A, unkeyed-Y, keyed-B]
        // new: [keyed-B, unkeyed-Y2, keyed-A, unkeyed-X2]
        let old = VNode::element("div")
            .with_djust_id("parent")
            .with_children(vec![
                VNode::element("span")
                    .with_djust_id("x")
                    .with_child(VNode::text("X")),
                VNode::element("div")
                    .with_key("a")
                    .with_djust_id("a")
                    .with_child(VNode::text("A")),
                VNode::element("span")
                    .with_djust_id("y")
                    .with_child(VNode::text("Y")),
                VNode::element("div")
                    .with_key("b")
                    .with_djust_id("b")
                    .with_child(VNode::text("B")),
            ]);

        let new = VNode::element("div")
            .with_djust_id("parent")
            .with_children(vec![
                VNode::element("div")
                    .with_key("b")
                    .with_djust_id("b2")
                    .with_child(VNode::text("B")),
                VNode::element("span")
                    .with_djust_id("y2")
                    .with_child(VNode::text("Y2")),
                VNode::element("div")
                    .with_key("a")
                    .with_djust_id("a2")
                    .with_child(VNode::text("A")),
                VNode::element("span")
                    .with_djust_id("x2")
                    .with_child(VNode::text("X2")),
            ]);

        let patches = diff_nodes(&old, &new, &[]);

        // Both unkeyed children should be diffed in-place (text changes X->X2, Y->Y2)
        let text_patches: Vec<_> = patches
            .iter()
            .filter(|p| matches!(p, Patch::SetText { .. }))
            .collect();

        // Should have exactly 2 text changes (X->something, Y->something)
        assert_eq!(
            text_patches.len(),
            2,
            "Should diff both unkeyed children. Patches: {:?}",
            patches
        );

        // Should NOT generate insert+remove pairs for unkeyed children
        let insert_count = patches
            .iter()
            .filter(|p| matches!(p, Patch::InsertChild { .. }))
            .count();
        let remove_count = patches
            .iter()
            .filter(|p| matches!(p, Patch::RemoveChild { .. }))
            .count();
        assert_eq!(
            insert_count, 0,
            "Should not insert unkeyed children (should diff in place). Patches: {:?}",
            patches
        );
        assert_eq!(
            remove_count, 0,
            "Should not remove unkeyed children (should diff in place). Patches: {:?}",
            patches
        );
    }

    #[test]
    fn test_unkeyed_count_changes_in_keyed_context() {
        // old: [keyed-A, unkeyed-X, keyed-B]
        // new: [keyed-A, unkeyed-X, unkeyed-Y, keyed-B]
        // One unkeyed child added between keyed children
        let old = VNode::element("div")
            .with_djust_id("parent")
            .with_children(vec![
                VNode::element("div")
                    .with_key("a")
                    .with_djust_id("a")
                    .with_child(VNode::text("A")),
                VNode::element("span")
                    .with_djust_id("x")
                    .with_child(VNode::text("X")),
                VNode::element("div")
                    .with_key("b")
                    .with_djust_id("b")
                    .with_child(VNode::text("B")),
            ]);

        let new = VNode::element("div")
            .with_djust_id("parent")
            .with_children(vec![
                VNode::element("div")
                    .with_key("a")
                    .with_djust_id("a2")
                    .with_child(VNode::text("A")),
                VNode::element("span")
                    .with_djust_id("x2")
                    .with_child(VNode::text("X")),
                VNode::element("span")
                    .with_djust_id("y2")
                    .with_child(VNode::text("Y")),
                VNode::element("div")
                    .with_key("b")
                    .with_djust_id("b2")
                    .with_child(VNode::text("B")),
            ]);

        let patches = diff_nodes(&old, &new, &[]);

        // The existing unkeyed child X should be diffed (no text change)
        // A new unkeyed child Y should be inserted
        let insert_count = patches
            .iter()
            .filter(|p| matches!(p, Patch::InsertChild { .. }))
            .count();
        assert_eq!(
            insert_count, 1,
            "Should insert exactly one new unkeyed child. Patches: {:?}",
            patches
        );

        // No removals
        let remove_count = patches
            .iter()
            .filter(|p| matches!(p, Patch::RemoveChild { .. }))
            .count();
        assert_eq!(
            remove_count, 0,
            "Should not remove any children. Patches: {:?}",
            patches
        );
    }

    #[test]
    fn test_unkeyed_count_decreases_in_keyed_context() {
        // old: [keyed-A, unkeyed-X, unkeyed-Y, keyed-B]
        // new: [keyed-A, unkeyed-X, keyed-B]
        // One unkeyed child removed
        let old = VNode::element("div")
            .with_djust_id("parent")
            .with_children(vec![
                VNode::element("div")
                    .with_key("a")
                    .with_djust_id("a")
                    .with_child(VNode::text("A")),
                VNode::element("span")
                    .with_djust_id("x")
                    .with_child(VNode::text("X")),
                VNode::element("span")
                    .with_djust_id("y")
                    .with_child(VNode::text("Y")),
                VNode::element("div")
                    .with_key("b")
                    .with_djust_id("b")
                    .with_child(VNode::text("B")),
            ]);

        let new = VNode::element("div")
            .with_djust_id("parent")
            .with_children(vec![
                VNode::element("div")
                    .with_key("a")
                    .with_djust_id("a2")
                    .with_child(VNode::text("A")),
                VNode::element("span")
                    .with_djust_id("x2")
                    .with_child(VNode::text("X")),
                VNode::element("div")
                    .with_key("b")
                    .with_djust_id("b2")
                    .with_child(VNode::text("B")),
            ]);

        let patches = diff_nodes(&old, &new, &[]);

        // Should remove exactly one unkeyed child (Y)
        let remove_count = patches
            .iter()
            .filter(|p| matches!(p, Patch::RemoveChild { .. }))
            .count();
        assert_eq!(
            remove_count, 1,
            "Should remove exactly one unkeyed child. Patches: {:?}",
            patches
        );

        // No inserts
        let insert_count = patches
            .iter()
            .filter(|p| matches!(p, Patch::InsertChild { .. }))
            .count();
        assert_eq!(
            insert_count, 0,
            "Should not insert any children. Patches: {:?}",
            patches
        );
    }

    #[test]
    fn test_dj_event_attrs_never_removed() {
        // When conditional rendering shifts DOM indices, the diff may incorrectly match
        // old elements with new elements that lack dj-* attrs. RemoveAttr must be suppressed.
        let old = VNode::element("button")
            .with_djust_id("0")
            .with_attr("class", "theme-btn")
            .with_attr("dj-click", "set_theme('light')");
        let new = VNode::element("button")
            .with_djust_id("0")
            .with_attr("class", "export-btn");

        let patches = diff_nodes(&old, &new, &[]);

        assert!(
            patches.iter().any(
                |p| matches!(p, Patch::SetAttr { key, value, .. } if key == "class" && value == "export-btn")
            ),
            "Should update class attribute"
        );
        assert!(
            !patches
                .iter()
                .any(|p| matches!(p, Patch::RemoveAttr { key, .. } if key == "dj-click")),
            "dj-click attribute should never be removed"
        );
    }

    #[test]
    fn test_dj_attrs_preserved_across_all_event_types() {
        let old = VNode::element("input")
            .with_djust_id("0")
            .with_attr("dj-input", "search(value)")
            .with_attr("dj-change", "on_change")
            .with_attr("dj-blur", "validate")
            .with_attr("dj-keydown.enter", "submit");
        let new = VNode::element("input").with_djust_id("0");

        let patches = diff_nodes(&old, &new, &[]);

        for patch in &patches {
            if let Patch::RemoveAttr { key, .. } = patch {
                assert!(
                    !key.starts_with("dj-"),
                    "Should not remove dj-* attribute: {}",
                    key
                );
            }
        }
    }

    #[test]
    fn test_regular_attrs_still_removed() {
        let old = VNode::element("div")
            .with_djust_id("0")
            .with_attr("class", "old-class")
            .with_attr("title", "old-title")
            .with_attr("dj-click", "handler");
        let new = VNode::element("div")
            .with_djust_id("0")
            .with_attr("class", "new-class");

        let patches = diff_nodes(&old, &new, &[]);

        assert!(
            patches
                .iter()
                .any(|p| matches!(p, Patch::RemoveAttr { key, .. } if key == "title")),
            "Regular attributes should still be removed"
        );
        assert!(
            !patches
                .iter()
                .any(|p| matches!(p, Patch::RemoveAttr { key, .. } if key == "dj-click")),
            "dj-click should be preserved"
        );
    }

    #[test]
    fn test_data_djust_replace_patch_ordering() {
        // Verify that replace_all_children emits all RemoveChild patches
        // before any InsertChild patches. The JS client batching path
        // depends on this ordering to avoid stale index bugs (Issue #142).
        let old = VNode::element("div")
            .with_djust_id("container")
            .with_attr("data-djust-replace", "")
            .with_children(vec![
                VNode::element("p").with_child(VNode::text("old-1")),
                VNode::element("p").with_child(VNode::text("old-2")),
                VNode::element("p").with_child(VNode::text("old-3")),
            ]);
        let new = VNode::element("div")
            .with_djust_id("container")
            .with_attr("data-djust-replace", "")
            .with_children(vec![
                VNode::element("span").with_child(VNode::text("new-1")),
                VNode::element("span").with_child(VNode::text("new-2")),
            ]);

        let patches = diff_nodes(&old, &new, &[]);

        // Find positions of last RemoveChild and first InsertChild
        let last_remove_idx = patches
            .iter()
            .rposition(|p| matches!(p, Patch::RemoveChild { .. }));
        let first_insert_idx = patches
            .iter()
            .position(|p| matches!(p, Patch::InsertChild { .. }));

        assert!(
            last_remove_idx.is_some() && first_insert_idx.is_some(),
            "Should have both RemoveChild and InsertChild patches"
        );
        assert!(
            last_remove_idx.unwrap() < first_insert_idx.unwrap(),
            "All RemoveChild patches must come before any InsertChild patch. \
             Got last RemoveChild at index {}, first InsertChild at index {}",
            last_remove_idx.unwrap(),
            first_insert_idx.unwrap()
        );

        // Verify RemoveChild indices are in descending order (for safe removal)
        let remove_indices: Vec<usize> = patches
            .iter()
            .filter_map(|p| match p {
                Patch::RemoveChild { index, .. } => Some(*index),
                _ => None,
            })
            .collect();
        assert_eq!(
            remove_indices,
            vec![2, 1, 0],
            "RemoveChild should be in descending index order"
        );
    }

    #[test]
    fn test_data_djust_replace_with_siblings() {
        // Regression test: when a data-djust-replace container has a sibling,
        // all InsertChild/RemoveChild patches must have correct path and d values
        // targeting only the replace container. This ensures the JS client's
        // groupPatchesByParent groups them correctly (Issue #142 continued).
        let old = VNode::element("div")
            .with_djust_id("root")
            .with_children(vec![
                VNode::element("div")
                    .with_djust_id("messages")
                    .with_attr("data-djust-replace", "")
                    .with_children(vec![
                        VNode::element("p").with_child(VNode::text("No messages"))
                    ]),
                VNode::element("div")
                    .with_djust_id("chat-input")
                    .with_children(vec![VNode::element("input")]),
            ]);
        let new = VNode::element("div")
            .with_djust_id("root")
            .with_children(vec![
                VNode::element("div")
                    .with_djust_id("messages")
                    .with_attr("data-djust-replace", "")
                    .with_children(vec![
                        VNode::element("p").with_child(VNode::text("Message 1")),
                        VNode::element("p").with_child(VNode::text("Message 2")),
                    ]),
                VNode::element("div")
                    .with_djust_id("chat-input")
                    .with_children(vec![VNode::element("input")]),
            ]);

        let patches = diff_nodes(&old, &new, &[]);

        // All child-op patches should target the messages container
        for patch in &patches {
            match patch {
                Patch::InsertChild { path, d, .. } | Patch::RemoveChild { path, d, .. } => {
                    assert_eq!(
                        d.as_deref(),
                        Some("messages"),
                        "Child op patch should target 'messages' container, got d={:?} path={:?}",
                        d,
                        path
                    );
                    assert_eq!(
                        path,
                        &vec![0],
                        "Child op patch path should be [0] (first child of root), got {:?}",
                        path
                    );
                }
                _ => {}
            }
        }

        // Should have at least 1 remove and 2 inserts
        let remove_count = patches
            .iter()
            .filter(|p| matches!(p, Patch::RemoveChild { .. }))
            .count();
        let insert_count = patches
            .iter()
            .filter(|p| matches!(p, Patch::InsertChild { .. }))
            .count();
        assert!(
            remove_count >= 1,
            "Should have at least 1 RemoveChild, got {}",
            remove_count
        );
        assert!(
            insert_count >= 2,
            "Should have at least 2 InsertChild, got {}",
            insert_count
        );

        // No patches should target the chat-input sibling
        for patch in &patches {
            match patch {
                Patch::InsertChild { d, .. } | Patch::RemoveChild { d, .. } => {
                    assert_ne!(
                        d.as_deref(),
                        Some("chat-input"),
                        "No child op patches should target the sibling 'chat-input'"
                    );
                }
                _ => {}
            }
        }
    }
}
