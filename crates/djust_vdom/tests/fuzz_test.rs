//! Property-based tests for the VDOM diff algorithm using proptest.
//!
//! Tests five key properties:
//! 1. Identity: diff(A, A) produces 0 patches
//! 2. Round-trip: apply(A, diff(A, B)) structurally equals B
//! 3. No panics: arbitrary tree pairs (including keyed) never panic
//! 4. Patch count bounds: patches ≤ total nodes in both trees
//! 5. Keyed mutation round-trip: mutating a tree (reorder, add, remove keyed
//!    children) produces a correct round-trip — exercises the keyed diff path
//!    far more effectively than independent random generation (#216, #217)
//! 6. Mixed keyed/unkeyed interleave round-trip: shuffling keyed children in a
//!    mixed list causes unkeyed children (elements and text nodes) to shift —
//!    exercises diff_keyed_children with mixed lists (#219, #222)
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

// ============================================================================
// Fully-keyed tree generator and keyed-mutation generator (#216)
//
// Produces tree B by *mutating* tree A, guaranteeing key overlap and exercising
// keyed reorder/move/add/remove paths that independent generation rarely hits.
//
// Uses fully-keyed trees (every element child has a key) to avoid a known
// limitation where the diff engine doesn't emit move patches for unkeyed
// children interleaved with keyed moves (tracked separately).
// ============================================================================

/// Generate a fully-keyed VNode tree where every element child has a key.
fn arb_fully_keyed_inner(
    max_depth: u32,
    current_depth: u32,
    key_prefix: &str,
) -> BoxedStrategy<VNode> {
    if current_depth >= max_depth {
        // Leaf: only element nodes (no text, to keep everything keyed)
        (
            prop::sample::select(TAGS),
            prop::collection::hash_map(prop::sample::select(ATTR_KEYS), "[a-z]{1,10}", 0..=2),
            "[a-z]{1,4}",
        )
            .prop_map(|(tag, attrs, key)| {
                let mut node = VNode::element(tag);
                node.attrs = attrs.into_iter().map(|(k, v)| (k.to_string(), v)).collect();
                node.key = Some(key);
                node
            })
            .boxed()
    } else {
        let prefix = key_prefix.to_string();
        (
            prop::sample::select(TAGS),
            prop::collection::hash_map(prop::sample::select(ATTR_KEYS), "[a-z]{1,10}", 0..=2),
            "[a-z]{1,4}",
            prop::collection::vec(0..=5u32, 0..=4),
        )
            .prop_flat_map(move |(tag, attrs, key, child_seeds)| {
                let n = child_seeds.len();
                let prefix = prefix.clone();
                let tag = tag.to_string();
                let attrs: Vec<(String, String)> =
                    attrs.into_iter().map(|(k, v)| (k.to_string(), v)).collect();
                let children_strats: Vec<BoxedStrategy<VNode>> = (0..n)
                    .map(|i| {
                        let child_prefix = format!("{}{}.", prefix, i);
                        arb_fully_keyed_inner(max_depth, current_depth + 1, &child_prefix)
                    })
                    .collect();
                if children_strats.is_empty() {
                    Just(VNode {
                        tag: tag.clone(),
                        attrs: attrs.into_iter().collect(),
                        children: vec![],
                        text: None,
                        key: Some(key.clone()),
                        djust_id: None,
                    })
                    .boxed()
                } else {
                    children_strats
                        .into_iter()
                        .collect::<Vec<_>>()
                        .prop_map(move |mut children| {
                            // Ensure unique keys among siblings
                            let mut seen = std::collections::HashSet::new();
                            for (i, child) in children.iter_mut().enumerate() {
                                if let Some(ref k) = child.key {
                                    if !seen.insert(k.clone()) {
                                        child.key = Some(format!("{}_{}", k, i));
                                    }
                                }
                            }
                            VNode {
                                tag: tag.clone(),
                                attrs: attrs.clone().into_iter().collect(),
                                children,
                                text: None,
                                key: Some(key.clone()),
                                djust_id: None,
                            }
                        })
                        .boxed()
                }
            })
            .boxed()
    }
}

fn arb_fully_keyed_tree() -> BoxedStrategy<VNode> {
    (1u32..=3)
        .prop_flat_map(|depth| arb_fully_keyed_inner(depth, 0, "r"))
        .boxed()
}

/// Apply random mutations to a cloned tree to produce tree B.
/// Mutations: shuffle keyed siblings, modify attrs, add/remove keyed children.
/// `key_counter` ensures newly inserted keys are globally unique.
fn mutate_tree(node: &VNode, rng: &mut impl Iterator<Item = u8>, key_counter: &mut u32) -> VNode {
    let mut result = node.clone();

    let action = rng.next().unwrap_or(0);

    if result.text.is_some() {
        return result;
    }

    // Recurse into children first
    result.children = result
        .children
        .iter()
        .map(|c| mutate_tree(c, rng, key_counter))
        .collect();

    let n_children = result.children.len();

    match action % 6 {
        // Shuffle keyed siblings (the main target)
        0 if n_children >= 2 => {
            let swap_a = rng.next().unwrap_or(0) as usize % n_children;
            let swap_b = rng.next().unwrap_or(1) as usize % n_children;
            if swap_a != swap_b {
                result.children.swap(swap_a, swap_b);
            }
        }
        // Remove a child
        1 if n_children >= 1 => {
            let idx = rng.next().unwrap_or(0) as usize % n_children;
            result.children.remove(idx);
        }
        // Add a new keyed child
        2 => {
            *key_counter += 1;
            let new_key = format!("new_{}", key_counter);
            let mut child = VNode::element("div");
            child.key = Some(new_key);
            let pos = if n_children > 0 {
                rng.next().unwrap_or(0) as usize % (n_children + 1)
            } else {
                0
            };
            result.children.insert(pos, child);
        }
        // Modify an attribute
        3 => {
            result
                .attrs
                .insert("class".to_string(), format!("m{}", action));
        }
        // Reverse children (exercises reorder)
        4 if n_children >= 2 => {
            result.children.reverse();
        }
        // No mutation (identity)
        _ => {}
    }

    result
}

/// Strategy that generates fully-keyed tree A then mutates it into tree B.
/// Guarantees key overlap between the two trees.
fn arb_keyed_mutation_pair() -> BoxedStrategy<(VNode, VNode)> {
    arb_fully_keyed_tree()
        .prop_flat_map(|tree_a| {
            let a = tree_a.clone();
            // Use a large seed to avoid iterator exhaustion on deep trees,
            // which would bias later mutations toward unwrap_or defaults.
            prop::collection::vec(any::<u8>(), 50..200).prop_map(move |seed_bytes| {
                let mut rng = seed_bytes.into_iter();
                let mut key_counter = 0u32;
                let tree_b = mutate_tree(&a, &mut rng, &mut key_counter);
                (a.clone(), tree_b)
            })
        })
        .boxed()
}

// ============================================================================
// Mixed keyed/unkeyed interleave generator (#222)
//
// Produces a parent with a deliberate mix of keyed elements, unkeyed elements,
// and text nodes as children. Tree B is derived by shuffling the keyed children
// (which shifts the unkeyed children's positions), exercising the
// diff_keyed_children path for mixed lists.
// ============================================================================

/// Generate a parent node with mixed keyed/unkeyed children.
///
/// Children are a mix of:
/// - Keyed elements (unique keys "k0", "k1", ...)
/// - Unkeyed elements (no key)
/// - Text nodes
fn arb_mixed_interleave_parent() -> BoxedStrategy<VNode> {
    // Generate 2-6 keyed children and 1-3 unkeyed children (elements + text)
    (
        prop::collection::vec(
            (
                prop::sample::select(TAGS),
                prop::collection::hash_map(prop::sample::select(ATTR_KEYS), "[a-z]{1,8}", 0..=2),
            ),
            2..=6,
        ),
        prop::collection::vec(
            prop_oneof![
                "[a-zA-Z0-9 ]{1,15}".prop_map(VNode::text),
                (
                    prop::sample::select(TAGS),
                    prop::collection::hash_map(
                        prop::sample::select(ATTR_KEYS),
                        "[a-z]{1,8}",
                        0..=2,
                    ),
                )
                    .prop_map(|(tag, attrs)| {
                        let mut node = VNode::element(tag);
                        node.attrs = attrs.into_iter().map(|(k, v)| (k.to_string(), v)).collect();
                        node
                    }),
            ],
            1..=3,
        ),
        // Shuffle seed: determines where unkeyed children are interleaved
        prop::collection::vec(any::<usize>(), 3..=8),
    )
        .prop_map(|(keyed_specs, unkeyed_children, positions)| {
            // Build keyed children with unique keys
            let mut children: Vec<VNode> = keyed_specs
                .into_iter()
                .enumerate()
                .map(|(i, (tag, attrs))| {
                    let mut node = VNode::element(tag);
                    node.attrs = attrs.into_iter().map(|(k, v)| (k.to_string(), v)).collect();
                    node.key = Some(format!("k{}", i));
                    node
                })
                .collect();

            // Interleave unkeyed children at pseudo-random positions
            for (i, child) in unkeyed_children.into_iter().enumerate() {
                let pos = positions.get(i).copied().unwrap_or(0) % (children.len() + 1);
                children.insert(pos, child);
            }

            let mut parent = VNode::element("div");
            parent.children = children;
            parent
        })
        .boxed()
}

/// Mutate a mixed-interleave tree: shuffle keyed children, modify some
/// unkeyed children's content/attrs, optionally add/remove a keyed child.
fn mutate_mixed_tree(node: &VNode, rng: &mut impl Iterator<Item = u8>) -> VNode {
    let mut result = node.clone();
    let action = rng.next().unwrap_or(0);

    // Separate keyed and unkeyed children, preserving relative order
    let mut keyed: Vec<(usize, VNode)> = Vec::new();
    let mut unkeyed: Vec<(usize, VNode)> = Vec::new();
    for (i, child) in result.children.iter().enumerate() {
        if child.key.is_some() {
            keyed.push((i, child.clone()));
        } else {
            unkeyed.push((i, child.clone()));
        }
    }

    match action % 5 {
        // Shuffle keyed children
        0 if keyed.len() >= 2 => {
            let a = rng.next().unwrap_or(0) as usize % keyed.len();
            let b = rng.next().unwrap_or(1) as usize % keyed.len();
            if a != b {
                keyed.swap(a, b);
            }
        }
        // Reverse keyed children
        1 if keyed.len() >= 2 => {
            keyed.reverse();
        }
        // Remove a keyed child
        2 if keyed.len() >= 3 => {
            let idx = rng.next().unwrap_or(0) as usize % keyed.len();
            keyed.remove(idx);
        }
        // Add a new keyed child
        3 => {
            let new_key = format!("k{}", keyed.len() + 10);
            let mut child = VNode::element("div");
            child.key = Some(new_key);
            keyed.push((0, child));
        }
        _ => {}
    }

    // Modify some unkeyed children
    for (_, child) in unkeyed.iter_mut() {
        let modify = rng.next().unwrap_or(0);
        if modify.is_multiple_of(3) {
            if child.is_text() {
                child.text = Some(format!("mod{}", modify));
            } else {
                child
                    .attrs
                    .insert("class".to_string(), format!("m{}", modify));
            }
        }
    }

    // Reassemble: interleave unkeyed among keyed at new positions
    let mut children: Vec<VNode> = keyed.into_iter().map(|(_, n)| n).collect();
    for (i, (_, child)) in unkeyed.into_iter().enumerate() {
        let pos_seed = rng.next().unwrap_or(i as u8) as usize;
        let pos = pos_seed % (children.len() + 1);
        children.insert(pos, child);
    }

    result.children = children;
    result
}

/// Strategy that generates a mixed keyed/unkeyed tree A then mutates into B.
fn arb_mixed_interleave_pair() -> BoxedStrategy<(VNode, VNode)> {
    arb_mixed_interleave_parent()
        .prop_flat_map(|tree_a| {
            let a = tree_a.clone();
            prop::collection::vec(any::<u8>(), 30..100).prop_map(move |seed_bytes| {
                let mut rng = seed_bytes.into_iter();
                let tree_b = mutate_mixed_tree(&a, &mut rng);
                (a.clone(), tree_b)
            })
        })
        .boxed()
}

/// Assign unique djust_ids to all nodes in a tree (elements and text nodes).
///
/// In production, only element nodes receive IDs (the parser skips text nodes).
/// For testing, we assign IDs to text nodes too so that `apply_patches` can
/// resolve them by ID after structural changes shift path indices (#221).
fn assign_ids(node: &mut VNode, counter: &mut u64) {
    node.djust_id = Some(format!("t{}", counter));
    *counter += 1;
    for child in &mut node.children {
        assign_ids(child, counter);
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

/// Regression test for issue #212: keyed child reorder round-trip failure.
///
/// Tree A:
///   div
///   ├── #text "a"
///   └── a
///       └── div (key="a")
///
/// Tree B:
///   div
///   ├── div (key="a")
///   ├── #text "A"
///   └── a (empty)
#[test]
fn issue_212_keyed_reorder_round_trip() {
    let mut a = VNode::element("div").with_children(vec![
        VNode::text("a"),
        VNode::element("a").with_child(VNode::element("div").with_key("a")),
    ]);

    let mut b = VNode::element("div").with_children(vec![
        VNode::element("div").with_key("a"),
        VNode::text("A"),
        VNode::element("a"),
    ]);

    let mut counter = 0u64;
    assign_ids(&mut a, &mut counter);
    assign_ids(&mut b, &mut counter);

    let patches = diff_nodes(&a, &b, &[]);
    let mut patched = a.clone();
    apply_patches(&mut patched, &patches);

    assert!(
        structurally_equal(&patched, &b),
        "Round-trip failed for issue #212.\nA: {:#?}\nB: {:#?}\nPatches: {:#?}\nPatched: {:#?}",
        a,
        b,
        patches,
        patched,
    );
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
    #![proptest_config(ProptestConfig::with_cases(1000))]

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
        // Continue counter from tree A so IDs don't overlap (mirrors real parse_html_continue)
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
        // Continue counter from tree A so IDs don't overlap (mirrors real parse_html_continue)
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
        // Continue counter from tree A so IDs don't overlap (mirrors real parse_html_continue)
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

    /// Property 5: keyed mutation round-trip (#216, #217).
    /// Tree B is derived from tree A by mutation, guaranteeing key overlap.
    /// This exercises keyed reorder/move paths far more than independent generation.
    #[test]
    fn round_trip_keyed_mutation(
        pair in arb_keyed_mutation_pair(),
    ) {
        let (mut a, mut b) = pair;

        let mut counter = 0u64;
        assign_ids(&mut a, &mut counter);
        assign_ids(&mut b, &mut counter);

        let patches = diff_nodes(&a, &b, &[]);
        let mut patched = a.clone();
        apply_patches(&mut patched, &patches);

        prop_assert!(
            structurally_equal(&patched, &b),
            "Keyed mutation round-trip failed.\nA: {:?}\nB: {:?}\nPatches: {:?}\nPatched: {:?}",
            a, b, patches, patched,
        );
    }

    /// Property 6: mixed keyed/unkeyed interleave round-trip (#219, #222).
    /// Tree B is derived from tree A by shuffling keyed children and modifying
    /// unkeyed children. This exercises diff_keyed_children with mixed lists
    /// where keyed moves cause unkeyed children to shift positions.
    #[test]
    fn round_trip_mixed_interleave(
        pair in arb_mixed_interleave_pair(),
    ) {
        let (mut a, mut b) = pair;

        let mut counter = 0u64;
        assign_ids(&mut a, &mut counter);
        assign_ids(&mut b, &mut counter);

        let patches = diff_nodes(&a, &b, &[]);
        let mut patched = a.clone();
        apply_patches(&mut patched, &patches);

        prop_assert!(
            structurally_equal(&patched, &b),
            "Mixed interleave round-trip failed.\nA: {:?}\nB: {:?}\nPatches: {:?}\nPatched: {:?}",
            a, b, patches, patched,
        );
    }
}
