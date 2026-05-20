//! Unit tests for the id-counter monotonicity helpers introduced for
//! #1550 / #1552. The integration-level effect of these helpers is
//! verified end-to-end in
//! `python/tests/test_if_elif_swap_e2e_1550_1552.py` (cross-thread +
//! msgpack-roundtrip cases). This file pins the unit-level shape so
//! a future refactor of `from_base62` / `max_djust_id_in` /
//! `ensure_id_counter_at_least` doesn't silently break the
//! integration path.

use djust_vdom::{
    ensure_id_counter_at_least, from_base62, get_id_counter, max_djust_id_in, next_djust_id,
    reset_id_counter, set_id_counter, to_base62, VNode,
};

#[test]
fn from_base62_inverts_to_base62_for_all_single_digits() {
    for v in 0..62 {
        let s = to_base62(v as u64);
        let decoded = from_base62(&s).expect("single-digit base62 must round-trip");
        assert_eq!(decoded, v as u64, "round-trip failed for value {v}");
    }
}

#[test]
fn from_base62_round_trips_multi_digit_values() {
    for &v in &[
        62_u64,
        100,
        1000,
        3843,
        3844,
        100_000,
        1_000_000,
        u32::MAX as u64,
    ] {
        let s = to_base62(v);
        let decoded = from_base62(&s).expect("multi-digit base62 must round-trip");
        assert_eq!(decoded, v, "round-trip failed for {v}");
    }
}

#[test]
fn from_base62_rejects_invalid_characters_and_empty() {
    assert_eq!(from_base62(""), None);
    assert_eq!(from_base62("-"), None);
    assert_eq!(from_base62("a-b"), None);
    assert_eq!(from_base62("abc!"), None);
}

#[test]
fn max_djust_id_in_returns_none_for_id_less_tree() {
    let node = VNode::element("div").with_child(VNode::text("hello"));
    assert_eq!(max_djust_id_in(&node), None);
}

#[test]
fn max_djust_id_in_finds_root_id() {
    let node = VNode::element("div").with_djust_id("5");
    assert_eq!(max_djust_id_in(&node), Some(5));
}

#[test]
fn max_djust_id_in_finds_deepest_id_across_siblings() {
    // Root id "0", child ids "3" and "7", grandchild "f" (= 15).
    let root = VNode::element("div")
        .with_djust_id("0")
        .with_child(VNode::element("span").with_djust_id("3"))
        .with_child(
            VNode::element("p")
                .with_djust_id("7")
                .with_child(VNode::element("em").with_djust_id("f")),
        );
    assert_eq!(max_djust_id_in(&root), Some(15));
}

#[test]
fn max_djust_id_in_ignores_unparseable_ids() {
    // Mixed parseable + unparseable; unparseable shouldn't break or
    // mask the real max.
    let root = VNode::element("div")
        .with_djust_id("not-base62!") // unparseable → skipped
        .with_child(VNode::element("p").with_djust_id("a")); // 10
    assert_eq!(max_djust_id_in(&root), Some(10));
}

#[test]
fn ensure_id_counter_at_least_advances_when_below() {
    set_id_counter(5);
    ensure_id_counter_at_least(100);
    assert_eq!(get_id_counter(), 100);
}

#[test]
fn ensure_id_counter_at_least_is_noop_when_above() {
    set_id_counter(500);
    ensure_id_counter_at_least(100);
    assert_eq!(get_id_counter(), 500);
}

#[test]
fn ensure_id_counter_at_least_is_noop_when_equal() {
    set_id_counter(100);
    ensure_id_counter_at_least(100);
    assert_eq!(get_id_counter(), 100);
}

#[test]
fn ensure_id_counter_at_least_then_next_djust_id_never_collides_with_max() {
    // The end-to-end invariant: after `ensure_id_counter_at_least(max + 1)`,
    // the very next `next_djust_id()` call produces a string whose
    // decoded value is > max.
    reset_id_counter();
    let max_id_value = from_base62("8").unwrap(); // 8
    ensure_id_counter_at_least(max_id_value + 1);
    let next = next_djust_id();
    let next_value = from_base62(&next).expect("next id must be base62");
    assert!(
        next_value > max_id_value,
        "next id {next} (decoded={next_value}) is not strictly greater than max {max_id_value}"
    );
}

#[test]
fn max_id_in_then_ensure_counter_then_parse_yields_non_colliding_ids() {
    use djust_vdom::parse_html_continue;

    // Simulate the cross-thread / cross-roundtrip case at the unit
    // level: an old VDOM exists with ids 0..N, the counter is at
    // some value <= N (mimicking a fresh thread). Without the fix
    // the next parse would generate colliding ids; with the fix the
    // next parse generates ids > N.
    reset_id_counter();

    // Build a synthetic "OLD" tree carrying ids 0..8 (matches the
    // production reporter's claim-include footprint at scale).
    let old = VNode::element("div").with_djust_id("0").with_children(vec![
        VNode::element("p").with_djust_id("1"),
        VNode::element("p").with_djust_id("2"),
        VNode::element("p").with_djust_id("3"),
        VNode::element("p").with_djust_id("4"),
        VNode::element("p").with_djust_id("5"),
        VNode::element("p").with_djust_id("6"),
        VNode::element("p").with_djust_id("7"),
        VNode::element("p").with_djust_id("8"),
    ]);

    // The fresh-thread counter is at 0 — this is the empirical bug
    // shape (see python/tests/test_if_elif_swap_e2e_1550_1552.py).
    assert_eq!(get_id_counter(), 0);

    // The fix: advance the counter past the max id in the surviving
    // tree before generating new ids.
    let max_id = max_djust_id_in(&old).expect("old tree has ids");
    ensure_id_counter_at_least(max_id + 1);

    // Now a parse generates ids strictly greater than the old max.
    let new_tree = parse_html_continue("<h2>What Happened</h2><input name=\"d\" />").unwrap();
    let new_max = max_djust_id_in(&new_tree).expect("new tree has at least one id");
    let new_min_id_value = {
        // Find the MIN id in the new tree (depth-first).
        fn walk(n: &VNode, min: &mut Option<u64>) {
            if let Some(ref id) = n.djust_id {
                if let Some(v) = from_base62(id) {
                    *min = Some(min.map_or(v, |m| m.min(v)));
                }
            }
            for c in &n.children {
                walk(c, min);
            }
        }
        let mut min = None;
        walk(&new_tree, &mut min);
        min.expect("at least one id in the new tree")
    };
    assert!(
        new_min_id_value > max_id,
        "new-tree min id {new_min_id_value} <= old-tree max {max_id}; \
         the counter was not advanced and ids would collide. new_max={new_max}"
    );
}
