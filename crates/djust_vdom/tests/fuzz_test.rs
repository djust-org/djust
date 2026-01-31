//! Property-based tests for the VDOM diff algorithm using proptest.
//!
//! Tests four key properties:
//! 1. Identity: diff(A, A) produces 0 patches
//! 2. Round-trip: apply(A, diff(A, B)) structurally equals B (unkeyed trees)
//! 3. No panics: arbitrary tree pairs (including keyed) never panic
//! 4. Patch count bounds: patches ≤ total nodes in both trees (unkeyed trees)
//!
//! Note: Round-trip and patch-count tests now work with keyed trees too,
//! since `apply_patches` resolves InsertChild/RemoveChild/MoveChild via
//! `djust_id` (mirroring the client's `data-d` attribute strategy).
//! See: https://github.com/djust-org/djust/issues/152

use djust_vdom::diff::diff_nodes;
use djust_vdom::patch::apply_patches;
use djust_vdom::VNode;
use proptest::prelude::*;
use std::collections::HashMap;

// ============================================================================
// Random VNode tree generators
// ============================================================================

const TAGS: &[&str] = &["div", "span", "p", "li", "ul", "a", "h1", "section"];
const ATTR_KEYS: &[&str] = &["class", "style", "href", "title", "role"];

/// Generate a random VNode tree without keys.
#[allow(dead_code)]
fn arb_unkeyed_inner(max_depth: u32, current_depth: u32) -> BoxedStrategy<VNode> {
    if current_depth >= max_depth {
        prop_oneof![
            "[a-zA-Z0-9 ]{1,20}".prop_map(VNode::text),
            prop::sample::select(TAGS).prop_map(VNode::element),
        ]
        .boxed()
    } else {
        prop_oneof![
            "[a-zA-Z0-9 ]{1,20}".prop_map(VNode::text),
            (
                prop::sample::select(TAGS),
                prop::collection::hash_map(prop::sample::select(ATTR_KEYS), "[a-z]{1,10}", 0..=3,),
                prop::collection::vec(arb_unkeyed_inner(max_depth, current_depth + 1), 0..=6,),
            )
                .prop_map(|(tag, attrs, children)| {
                    let mut node = VNode::element(tag);
                    node.attrs = attrs.into_iter().map(|(k, v)| (k.to_string(), v)).collect();
                    node.children = children;
                    node
                }),
        ]
        .boxed()
    }
}

/// Generate a random VNode tree with optional keys (for panic/stress testing).
/// Keys are made unique per sibling group by appending the child index.
fn arb_keyed_inner(max_depth: u32, current_depth: u32) -> BoxedStrategy<VNode> {
    if current_depth >= max_depth {
        prop_oneof![
            "[a-zA-Z0-9 ]{1,20}".prop_map(VNode::text),
            prop::sample::select(TAGS).prop_map(VNode::element),
        ]
        .boxed()
    } else {
        prop_oneof![
            "[a-zA-Z0-9 ]{1,20}".prop_map(VNode::text),
            (
                prop::sample::select(TAGS),
                prop::collection::hash_map(prop::sample::select(ATTR_KEYS), "[a-z]{1,10}", 0..=3,),
                prop::collection::vec(arb_keyed_inner(max_depth, current_depth + 1), 0..=6,),
                prop::option::weighted(0.3, "[a-z]{1,5}"),
            )
                .prop_map(|(tag, attrs, mut children, key)| {
                    // Deduplicate keys among siblings by appending index
                    let mut seen_keys = std::collections::HashSet::new();
                    for (i, child) in children.iter_mut().enumerate() {
                        if let Some(ref k) = child.key {
                            if !seen_keys.insert(k.clone()) {
                                child.key = Some(format!("{}_{}", k, i));
                            }
                        }
                    }
                    let mut node = VNode::element(tag);
                    node.attrs = attrs.into_iter().map(|(k, v)| (k.to_string(), v)).collect();
                    node.children = children;
                    node.key = key;
                    node
                }),
        ]
        .boxed()
    }
}

#[allow(dead_code)]
fn arb_unkeyed_tree() -> BoxedStrategy<VNode> {
    (0u32..=5)
        .prop_flat_map(|depth| arb_unkeyed_inner(depth, 0))
        .boxed()
}

fn arb_keyed_tree() -> BoxedStrategy<VNode> {
    (0u32..=5)
        .prop_flat_map(|depth| arb_keyed_inner(depth, 0))
        .boxed()
}

/// Assign unique djust_ids to all element nodes in a tree.
fn assign_ids(node: &mut VNode, counter: &mut u64) {
    if !node.is_text() {
        node.djust_id = Some(format!("t{}", counter));
        *counter += 1;
        for child in &mut node.children {
            assign_ids(child, counter);
        }
    }
}

/// Count total nodes in a tree.
fn count_nodes(node: &VNode) -> usize {
    1 + node.children.iter().map(count_nodes).sum::<usize>()
}

/// Count total attributes across all nodes in a tree.
fn count_attrs(node: &VNode) -> usize {
    node.attrs.len() + node.children.iter().map(count_attrs).sum::<usize>()
}

/// Structural equality check ignoring djust_id.
fn structurally_equal(a: &VNode, b: &VNode) -> bool {
    if a.tag != b.tag || a.text != b.text {
        return false;
    }
    let a_attrs: HashMap<String, String> = a
        .attrs
        .iter()
        .filter(|(k, _)| k.as_str() != "data-dj-id")
        .map(|(k, v)| (k.clone(), v.clone()))
        .collect();
    let b_attrs: HashMap<String, String> = b
        .attrs
        .iter()
        .filter(|(k, _)| k.as_str() != "data-dj-id")
        .map(|(k, v)| (k.clone(), v.clone()))
        .collect();
    if a_attrs != b_attrs {
        return false;
    }
    if a.children.len() != b.children.len() {
        return false;
    }
    a.children
        .iter()
        .zip(b.children.iter())
        .all(|(ca, cb)| structurally_equal(ca, cb))
}

// ============================================================================
// Property tests
// ============================================================================

proptest! {
    #![proptest_config(ProptestConfig::with_cases(500))]

    /// Property 1: diff(A, A) always produces 0 patches.
    #[test]
    fn identity_diff_produces_no_patches(tree in arb_keyed_tree()) {
        let mut a = tree;
        let mut counter = 0u64;
        assign_ids(&mut a, &mut counter);

        let patches = diff_nodes(&a, &a, &[]);
        prop_assert!(
            patches.is_empty(),
            "diff(A, A) produced {} patches: {:?}",
            patches.len(),
            patches
        );
    }

    /// Property 2: apply(A, diff(A, B)) structurally equals B.
    /// Works with keyed trees — apply_patches resolves children by djust_id.
    #[test]
    fn round_trip_correctness(
        tree_a in arb_keyed_tree(),
        tree_b in arb_keyed_tree(),
    ) {
        let mut a = tree_a;
        let mut b = tree_b;

        let mut counter = 0u64;
        assign_ids(&mut a, &mut counter);
        counter = 0;
        assign_ids(&mut b, &mut counter);

        let patches = diff_nodes(&a, &b, &[]);
        let mut patched = a.clone();
        apply_patches(&mut patched, &patches);

        prop_assert!(
            structurally_equal(&patched, &b),
            "Round-trip failed.\nA: {:?}\nB: {:?}\nPatches: {:?}\nPatched: {:?}",
            a, b, patches, patched,
        );
    }

    /// Property 3: arbitrary tree pairs (including keyed) never cause panics.
    #[test]
    fn no_panics_on_arbitrary_trees(
        tree_a in arb_keyed_tree(),
        tree_b in arb_keyed_tree(),
    ) {
        let mut a = tree_a;
        let mut b = tree_b;
        let mut counter = 0u64;
        assign_ids(&mut a, &mut counter);
        counter = 0;
        assign_ids(&mut b, &mut counter);

        let _patches = diff_nodes(&a, &b, &[]);
    }

    /// Property 4: patch count is bounded by total nodes + total attributes.
    /// Each node can produce at most: 1 structural patch + N attribute patches.
    #[test]
    fn patch_count_bounded(
        tree_a in arb_keyed_tree(),
        tree_b in arb_keyed_tree(),
    ) {
        let mut a = tree_a;
        let mut b = tree_b;
        let mut counter = 0u64;
        assign_ids(&mut a, &mut counter);
        counter = 0;
        assign_ids(&mut b, &mut counter);

        let total_nodes = count_nodes(&a) + count_nodes(&b);
        let total_attrs = count_attrs(&a) + count_attrs(&b);
        let bound = total_nodes + total_attrs;
        let patches = diff_nodes(&a, &b, &[]);

        prop_assert!(
            patches.len() <= bound,
            "Patch count {} exceeds bound {} (nodes={}, attrs={})",
            patches.len(), bound, total_nodes, total_attrs,
        );
    }
}
