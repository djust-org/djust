//! #1970 — parsed-subtree loop cache: djust_vdom splice + dj-id re-walk tests.
//!
//! The load-bearing correctness primitive is `splice_loop_placeholders`: it
//! replaces `<dj-pc h=...>` placeholders with cached subtrees and re-assigns
//! ALL dj-ids by a pre-order re-walk so the assembled tree is byte-identical to
//! a fresh full parse. dj-ids are purely positional (the parser assigns them
//! pre-order), so a cached subtree carrying stale ids is position-WRONG when
//! reused; the re-walk is what makes the cache correct.
//!
//! Companion: the END-TO-END byte-identity battery (cache ON == OFF through the
//! real `RustLiveView.render_with_diff` path) lives in the Python suite
//! `python/djust/tests/test_loop_render_cache_1967.py`.

use djust_vdom::{
    ensure_id_counter_at_least, max_djust_id_in, parse_html, parse_html_continue, reset_id_counter,
    set_id_counter, splice_loop_placeholders, VNode,
};
use std::collections::HashMap;

/// Collect every element dj-id in pre-order (document) order.
fn collect_ids(node: &VNode, out: &mut Vec<String>) {
    if let Some(id) = &node.djust_id {
        out.push(id.clone());
    }
    for c in &node.children {
        collect_ids(c, out);
    }
}

/// Cache an item's parsed subtree roots (ids irrelevant — the splice re-walks).
fn parse_item(html: &str) -> Vec<VNode> {
    reset_id_counter();
    let root = parse_html(&format!("<x>{html}</x>")).unwrap();
    root.children
}

// ---------------------------------------------------------------------------
// Core: splice + re-walk reproduces a fresh full parse, byte-for-byte.
// ---------------------------------------------------------------------------

#[test]
fn splice_initial_reproduces_fresh_parse_ids() {
    let full = "<ul><li dj-key=\"1\"><span>a</span></li><li dj-key=\"2\"><span>b</span></li><li dj-key=\"3\"><span>c</span></li></ul>";
    reset_id_counter();
    let control = parse_html(full).unwrap();
    let mut control_ids = Vec::new();
    collect_ids(&control, &mut control_ids);

    // Three cached item subtrees (all parse-cache HITs → all placeholders).
    let mut subtrees: HashMap<u64, Vec<VNode>> = HashMap::new();
    subtrees.insert(1, parse_item("<li dj-key=\"1\"><span>a</span></li>"));
    subtrees.insert(2, parse_item("<li dj-key=\"2\"><span>b</span></li>"));
    subtrees.insert(3, parse_item("<li dj-key=\"3\"><span>c</span></li>"));

    // Reduced HTML: every item a placeholder.
    let reduced = "<ul><dj-pc h=\"1\"></dj-pc><dj-pc h=\"2\"></dj-pc><dj-pc h=\"3\"></dj-pc></ul>";
    let mut tree = parse_html_continue(reduced).unwrap();
    let found = splice_loop_placeholders(&mut tree, &subtrees, 0).unwrap();
    assert_eq!(found, 3, "all three placeholders replaced");

    let mut spliced_ids = Vec::new();
    collect_ids(&tree, &mut spliced_ids);
    assert_eq!(
        control_ids, spliced_ids,
        "splice+re-walk dj-ids must equal a fresh full parse"
    );
    assert_eq!(
        control.to_html(),
        tree.to_html(),
        "splice+re-walk HTML must be byte-identical to a fresh full parse"
    );
}

#[test]
fn splice_continue_with_mixed_hit_miss_matches_fresh_continue() {
    // A continuing render (last_vdom present): one item changed (parse MISS →
    // real HTML in reduced), two unchanged (HITs → placeholders).
    let full_v2 = "<ul><li dj-key=\"1\"><span>a</span></li><li dj-key=\"2\"><span>CHANGED</span></li><li dj-key=\"3\"><span>c</span></li></ul>";

    // Establish an "old tree" so the counter base mirrors render_with_diff.
    reset_id_counter();
    let old = parse_html("<ul><li dj-key=\"1\"><span>a</span></li><li dj-key=\"2\"><span>b</span></li><li dj-key=\"3\"><span>c</span></li></ul>").unwrap();
    let max_old = max_djust_id_in(&old).unwrap();

    // Control: fresh continuing parse of v2 (ensure>=max+1 then continue).
    ensure_id_counter_at_least(max_old + 1);
    let counter_base = djust_vdom::get_id_counter();
    let control = parse_html_continue(full_v2).unwrap();
    let mut control_ids = Vec::new();
    collect_ids(&control, &mut control_ids);

    // Splice: items 1 + 3 from cache (placeholders), item 2 real HTML in reduced.
    let mut subtrees: HashMap<u64, Vec<VNode>> = HashMap::new();
    subtrees.insert(1, parse_item("<li dj-key=\"1\"><span>a</span></li>"));
    subtrees.insert(3, parse_item("<li dj-key=\"3\"><span>c</span></li>"));
    let reduced = "<ul><dj-pc h=\"1\"></dj-pc><li dj-key=\"2\"><span>CHANGED</span></li><dj-pc h=\"3\"></dj-pc></ul>";
    let mut tree = parse_html_continue(reduced).unwrap();
    let found = splice_loop_placeholders(&mut tree, &subtrees, counter_base).unwrap();
    assert_eq!(found, 2, "two placeholders replaced");

    let mut spliced_ids = Vec::new();
    collect_ids(&tree, &mut spliced_ids);
    assert_eq!(
        control_ids, spliced_ids,
        "continuing splice ids must equal a fresh continuing parse"
    );
    assert_eq!(
        control.to_html(),
        tree.to_html(),
        "continuing HTML identical"
    );
}

// ---------------------------------------------------------------------------
// Gate-off (#1468): prove the re-walk is LOAD-BEARING.
// ---------------------------------------------------------------------------

#[test]
fn gate_off_naive_reuse_without_rewalk_collides() {
    // If we splice cached subtrees WITHOUT re-walking ids (i.e. reuse the cached
    // subtree's baked ids verbatim), two items sharing a cached subtree collide.
    // This is the bug the re-walk fixes; the splice primitive ALWAYS re-walks,
    // so we demonstrate the collision by reusing the SAME parsed subtree twice
    // and checking ids are NOT unique BEFORE a re-walk would fix them.
    reset_id_counter();
    let cached = parse_html("<li><span>A</span></li>").unwrap(); // ids 0,1
                                                                 // Two items share the same cached subtree (e.g. two identical-content rows).
    let mut tree = VNode::element("ul");
    tree.djust_id = Some("z".to_string());
    tree.attrs.insert("dj-id".to_string(), "z".to_string());
    tree.children.push(cached.clone());
    tree.children.push(cached.clone());
    let mut ids = Vec::new();
    collect_ids(&tree, &mut ids);
    let mut uniq = ids.clone();
    uniq.sort();
    uniq.dedup();
    assert!(
        uniq.len() < ids.len(),
        "naive verbatim reuse MUST produce duplicate dj-ids (the hazard the re-walk fixes)"
    );

    // Now re-walk via the splice primitive (no placeholders → found=0, but the
    // re-walk still runs) and confirm ids become unique + sequential.
    set_id_counter(0);
    let subtrees: HashMap<u64, Vec<VNode>> = HashMap::new();
    let found = splice_loop_placeholders(&mut tree, &subtrees, 0).unwrap();
    assert_eq!(found, 0, "no placeholders in this tree");
    let mut ids2 = Vec::new();
    collect_ids(&tree, &mut ids2);
    let mut uniq2 = ids2.clone();
    uniq2.sort();
    uniq2.dedup();
    assert_eq!(
        uniq2.len(),
        ids2.len(),
        "after re-walk every dj-id is unique"
    );
}

// ---------------------------------------------------------------------------
// Validation: a missing cached subtree → Err (caller falls back to full parse).
// ---------------------------------------------------------------------------

#[test]
fn splice_errs_on_missing_cached_subtree() {
    let subtrees: HashMap<u64, Vec<VNode>> = HashMap::new(); // empty → every hash misses
    let reduced = "<ul><dj-pc h=\"5\"></dj-pc></ul>";
    let mut tree = parse_html_continue(reduced).unwrap();
    let res = splice_loop_placeholders(&mut tree, &subtrees, 0);
    assert!(
        res.is_err(),
        "a placeholder with no cached subtree must Err"
    );
}

// ---------------------------------------------------------------------------
// Wire determinism (#1970): attrs serialize in SORTED key order so the parse-
// cache patch JSON matches the cache-OFF full-parse patch byte-for-byte
// regardless of HashMap bucket layout.
// ---------------------------------------------------------------------------

#[test]
fn attrs_serialize_in_sorted_order() {
    let mut node = VNode::element("li");
    node.djust_id = Some("a".to_string());
    node.attrs.insert("dj-id".to_string(), "a".to_string());
    node.attrs.insert("dj-key".to_string(), "k".to_string());
    node.attrs.insert("class".to_string(), "row".to_string());
    let json = serde_json::to_string(&node).unwrap();
    // Sorted keys: class < dj-id < dj-key.
    let pos_class = json.find("\"class\"").unwrap();
    let pos_djid = json.find("\"dj-id\"").unwrap();
    let pos_djkey = json.find("\"dj-key\"").unwrap();
    assert!(
        pos_class < pos_djid && pos_djid < pos_djkey,
        "attrs must serialize in sorted key order, got: {json}"
    );
}
