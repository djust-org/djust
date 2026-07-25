//! ADR-026 iteration 1 — `[dj-virtual]` keyed splice ops (#2017 items 2–4).
//!
//! The differ emits KEY-addressed ops for a `[dj-virtual]` parent instead of
//! the index-addressed `InsertChild`/`MoveChild`/`RemoveChild`, because such a
//! parent's children on the client are only the visible WINDOW — index 7 means
//! "the 8th item" to the differ and "the 8th VISIBLE item" to the DOM.
//!
//! This iteration ships the differ side DARK: the flag defaults OFF, so `main`
//! behaviour must be byte-identical until the client half lands (iteration 2)
//! and the flag is flipped after a soak (iteration 3). The first test below is
//! the one that matters most — it pins that darkness.

use djust_vdom::diff::{diff_nodes, set_virtual_keyed_ops, virtual_keyed_ops_enabled};
use djust_vdom::{Patch, VNode};
use std::collections::HashMap;

/// Serial guard: the flag is process-global, so tests that toggle it must not
/// interleave. Each test takes this lock for its whole body.
static FLAG_LOCK: std::sync::Mutex<()> = std::sync::Mutex::new(());

fn el(tag: &str, key: Option<&str>, attrs: &[(&str, &str)], children: Vec<VNode>) -> VNode {
    let mut map = HashMap::new();
    for (k, v) in attrs {
        map.insert((*k).to_string(), (*v).to_string());
    }
    VNode {
        tag: tag.to_string(),
        attrs: map,
        children,
        text: None,
        key: key.map(|s| s.to_string()),
        djust_id: Some(format!("id-{}", key.unwrap_or(tag))),
        cached_html: None,
    }
}

fn row(key: &str) -> VNode {
    el("div", Some(key), &[], vec![])
}

/// A `[dj-virtual]` container with the given keyed rows.
fn virtual_list(keys: &[&str]) -> VNode {
    el(
        "div",
        None,
        &[("dj-virtual", "rows"), ("dj-virtual-item-height", "20")],
        keys.iter().map(|k| row(k)).collect(),
    )
}

/// An ordinary keyed container — the control.
fn plain_list(keys: &[&str]) -> VNode {
    el("div", None, &[], keys.iter().map(|k| row(k)).collect())
}

fn kinds(patches: &[Patch]) -> Vec<&'static str> {
    patches
        .iter()
        .map(|p| match p {
            Patch::VirtualInsert { .. } => "VirtualInsert",
            Patch::VirtualMove { .. } => "VirtualMove",
            Patch::VirtualRemove { .. } => "VirtualRemove",
            Patch::InsertChild { .. } => "InsertChild",
            Patch::MoveChild { .. } => "MoveChild",
            Patch::RemoveChild { .. } => "RemoveChild",
            _ => "other",
        })
        .collect()
}

// ---------------------------------------------------------------------------
// The flag must be DARK by default — the most important test in this file
// ---------------------------------------------------------------------------

#[test]
fn flag_is_off_by_default() {
    let _g = FLAG_LOCK.lock().unwrap();
    assert!(
        !virtual_keyed_ops_enabled(),
        "ADR-026 iteration 1 ships dark; the client cannot apply these ops yet"
    );
}

#[test]
fn with_flag_off_a_virtual_parent_diffs_exactly_like_a_plain_one() {
    let _g = FLAG_LOCK.lock().unwrap();
    set_virtual_keyed_ops(false);

    let v_old = virtual_list(&["a", "b", "c"]);
    let v_new = virtual_list(&["c", "a", "b"]);
    let p_old = plain_list(&["a", "b", "c"]);
    let p_new = plain_list(&["c", "a", "b"]);

    let v = diff_nodes(&v_old, &v_new, &[]);
    let p = diff_nodes(&p_old, &p_new, &[]);

    assert_eq!(
        kinds(&v),
        kinds(&p),
        "with the flag OFF a [dj-virtual] parent must produce the SAME patch \
         kinds as an ordinary parent — otherwise the change is not dark"
    );
    assert!(
        !kinds(&v).iter().any(|k| k.starts_with("Virtual")),
        "no Virtual* op may be emitted while the flag is off"
    );
}

// ---------------------------------------------------------------------------
// With the flag ON
// ---------------------------------------------------------------------------

#[test]
fn insert_at_front_is_key_addressed_with_a_before_key() {
    let _g = FLAG_LOCK.lock().unwrap();
    set_virtual_keyed_ops(true);

    let old = virtual_list(&["b", "c"]);
    let new = virtual_list(&["a", "b", "c"]);
    let patches = diff_nodes(&old, &new, &[]);
    set_virtual_keyed_ops(false);

    let insert = patches
        .iter()
        .find_map(|p| match p {
            Patch::VirtualInsert {
                key, before_key, ..
            } => Some((key.clone(), before_key.clone())),
            _ => None,
        })
        .expect("a new key must produce a VirtualInsert");

    assert_eq!(insert.0, "a");
    assert_eq!(
        insert.1,
        Some("b".to_string()),
        "insert position must be anchored to the NEXT key, not an index — \
         that anchor is what #2017 item 4 lacked"
    );
}

#[test]
fn append_at_tail_has_no_before_key() {
    let _g = FLAG_LOCK.lock().unwrap();
    set_virtual_keyed_ops(true);
    let patches = diff_nodes(&virtual_list(&["a"]), &virtual_list(&["a", "b"]), &[]);
    set_virtual_keyed_ops(false);

    let insert = patches
        .iter()
        .find_map(|p| match p {
            Patch::VirtualInsert {
                key, before_key, ..
            } if key == "b" => Some(before_key.clone()),
            _ => None,
        })
        .expect("appended key must produce a VirtualInsert");
    assert_eq!(insert, None, "a tail append has no anchor");
}

#[test]
fn a_removed_key_produces_virtual_remove() {
    let _g = FLAG_LOCK.lock().unwrap();
    set_virtual_keyed_ops(true);
    let patches = diff_nodes(
        &virtual_list(&["a", "b", "c"]),
        &virtual_list(&["a", "c"]),
        &[],
    );
    set_virtual_keyed_ops(false);

    let removed: Vec<String> = patches
        .iter()
        .filter_map(|p| match p {
            Patch::VirtualRemove { key, .. } => Some(key.clone()),
            _ => None,
        })
        .collect();
    assert_eq!(removed, vec!["b".to_string()]);
}

#[test]
fn no_index_addressed_child_ops_for_a_virtual_parent() {
    // The whole point: index ops are meaningless for a windowed parent.
    let _g = FLAG_LOCK.lock().unwrap();
    set_virtual_keyed_ops(true);
    let patches = diff_nodes(
        &virtual_list(&["a", "b", "c"]),
        &virtual_list(&["c", "b"]),
        &[],
    );
    set_virtual_keyed_ops(false);

    for k in kinds(&patches) {
        assert!(
            !matches!(k, "InsertChild" | "MoveChild" | "RemoveChild"),
            "index-addressed {k} leaked for a [dj-virtual] parent"
        );
    }
}

#[test]
fn a_plain_parent_is_unaffected_even_with_the_flag_on() {
    let _g = FLAG_LOCK.lock().unwrap();
    set_virtual_keyed_ops(true);
    let patches = diff_nodes(&plain_list(&["a", "b"]), &plain_list(&["b", "a"]), &[]);
    set_virtual_keyed_ops(false);

    assert!(
        !kinds(&patches).iter().any(|k| k.starts_with("Virtual")),
        "only a [dj-virtual] parent may get keyed splice ops"
    );
}

// ---------------------------------------------------------------------------
// Wire format (#1448) — Patch is serde_json (a NAMED map), not msgpack
// ---------------------------------------------------------------------------

#[test]
fn wire_shape_virtual_insert() {
    let p = Patch::VirtualInsert {
        path: vec![0, 1],
        d: Some("42".to_string()),
        key: "k1".to_string(),
        node: row("k1"),
        before_key: Some("k2".to_string()),
    };
    let j = serde_json::to_string(&p).expect("serialize");
    assert!(j.contains(r#""type":"VirtualInsert""#), "got {j}");
    assert!(j.contains(r#""key":"k1""#), "got {j}");
    assert!(j.contains(r#""before_key":"k2""#), "got {j}");
    assert!(!j.contains(r#""index""#), "must not carry an index: {j}");
}

#[test]
fn wire_shape_omits_none_optionals() {
    let p = Patch::VirtualInsert {
        path: vec![0],
        d: None,
        key: "k".to_string(),
        node: row("k"),
        before_key: None,
    };
    let j = serde_json::to_string(&p).expect("serialize");
    assert!(!j.contains(r#""d":"#), "None d must be skipped: {j}");
    assert!(
        !j.contains(r#""before_key""#),
        "None before_key must be skipped: {j}"
    );
}

#[test]
fn wire_round_trips_through_json_in_every_optional_permutation() {
    // Patch is JSON-encoded (named map), so field ORDER cannot shift the way
    // it does for msgpack positional arrays (#1541). This pins that all four
    // Some/None permutations survive a round trip regardless.
    for d in [None, Some("7".to_string())] {
        for bk in [None, Some("nxt".to_string())] {
            let p = Patch::VirtualInsert {
                path: vec![1, 2],
                d: d.clone(),
                key: "k".to_string(),
                node: row("k"),
                before_key: bk.clone(),
            };
            let j = serde_json::to_string(&p).expect("serialize");
            let back: Patch = serde_json::from_str(&j).expect("deserialize");
            match back {
                Patch::VirtualInsert {
                    d: d2,
                    before_key: bk2,
                    key,
                    ..
                } => {
                    assert_eq!(d2, d);
                    assert_eq!(bk2, bk);
                    assert_eq!(key, "k");
                }
                other => panic!("wrong variant back: {other:?}"),
            }
        }
    }
}

#[test]
fn append_to_a_large_list_emits_one_op_not_n() {
    // The first version emitted a VirtualMove for EVERY surviving key, so a
    // single append to a 50-item list produced 50 moves. On the 10k-row feeds
    // dj-virtual exists for that is 10k ops for one new row — which defeats
    // virtualising at all. LIS keeps the untouched run stable.
    let _g = FLAG_LOCK.lock().unwrap();
    set_virtual_keyed_ops(true);
    let old_keys: Vec<String> = (0..50).map(|i| format!("k{i}")).collect();
    let mut new_keys = old_keys.clone();
    new_keys.push("new".to_string());
    let ov: Vec<&str> = old_keys.iter().map(|s| s.as_str()).collect();
    let nv: Vec<&str> = new_keys.iter().map(|s| s.as_str()).collect();
    let patches = diff_nodes(&virtual_list(&ov), &virtual_list(&nv), &[]);
    set_virtual_keyed_ops(false);

    let moves = patches
        .iter()
        .filter(|p| matches!(p, Patch::VirtualMove { .. }))
        .count();
    let inserts = patches
        .iter()
        .filter(|p| matches!(p, Patch::VirtualInsert { .. }))
        .count();
    assert_eq!(inserts, 1, "one new key -> one insert");
    assert_eq!(
        moves, 0,
        "an append moves nothing; got {moves} moves for 50 unchanged rows"
    );
}
