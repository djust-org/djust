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

/// Holds the serialization lock AND guarantees the process-global flag is
/// cleared, even if the test panics.
///
/// Without this, a FAILING test unwinds before its `set_virtual_keyed_ops(false)`
/// and leaves the flag ON for every test that runs after it — turning one
/// genuine failure into a cascade of unrelated ones. Observed while verifying
/// a gate-off: one real failure produced three, and the two extra pointed at
/// tests that were fine.
struct FlagGuard(#[allow(dead_code)] std::sync::MutexGuard<'static, ()>);

impl FlagGuard {
    fn on() -> Self {
        let g = FLAG_LOCK.lock().unwrap_or_else(|e| e.into_inner());
        set_virtual_keyed_ops(true);
        FlagGuard(g)
    }
}

impl Drop for FlagGuard {
    fn drop(&mut self) {
        set_virtual_keyed_ops(false);
    }
}

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
    let _g = FLAG_LOCK.lock().unwrap_or_else(|e| e.into_inner());
    assert!(
        !virtual_keyed_ops_enabled(),
        "the Rust static must stay OFF: Python's _defaults carries the ON default \
         and DjustConfig.ready() applies it, so an embedder that never runs Django \
         fails safe rather than fail-open"
    );
}

#[test]
fn with_flag_off_a_virtual_parent_diffs_exactly_like_a_plain_one() {
    let _g = FLAG_LOCK.lock().unwrap_or_else(|e| e.into_inner());

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
    let _g = FlagGuard::on();

    let old = virtual_list(&["b", "c"]);
    let new = virtual_list(&["a", "b", "c"]);
    let patches = diff_nodes(&old, &new, &[]);

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
    let _g = FlagGuard::on();
    let patches = diff_nodes(&virtual_list(&["a"]), &virtual_list(&["a", "b"]), &[]);

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
    let _g = FlagGuard::on();
    let patches = diff_nodes(
        &virtual_list(&["a", "b", "c"]),
        &virtual_list(&["a", "c"]),
        &[],
    );

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
    let _g = FlagGuard::on();
    let patches = diff_nodes(
        &virtual_list(&["a", "b", "c"]),
        &virtual_list(&["c", "b"]),
        &[],
    );

    for k in kinds(&patches) {
        assert!(
            !matches!(k, "InsertChild" | "MoveChild" | "RemoveChild"),
            "index-addressed {k} leaked for a [dj-virtual] parent"
        );
    }
}

#[test]
fn a_plain_parent_is_unaffected_even_with_the_flag_on() {
    let _g = FlagGuard::on();
    let patches = diff_nodes(&plain_list(&["a", "b"]), &plain_list(&["b", "a"]), &[]);

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
    let _g = FlagGuard::on();
    let old_keys: Vec<String> = (0..50).map(|i| format!("k{i}")).collect();
    let mut new_keys = old_keys.clone();
    new_keys.push("new".to_string());
    let ov: Vec<&str> = old_keys.iter().map(|s| s.as_str()).collect();
    let nv: Vec<&str> = new_keys.iter().map(|s| s.as_str()).collect();
    let patches = diff_nodes(&virtual_list(&ov), &virtual_list(&nv), &[]);

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

/// Applies the emitted ops the way the client will: `before_key` names an
/// anchor that must already be present, else the item lands at the tail.
fn apply_ops(start: &[&str], patches: &[Patch]) -> Vec<String> {
    let mut list: Vec<String> = start.iter().map(|s| s.to_string()).collect();
    for p in patches {
        match p {
            Patch::VirtualRemove { key, .. } => list.retain(|k| k != key),
            Patch::VirtualInsert {
                key, before_key, ..
            }
            | Patch::VirtualMove {
                key, before_key, ..
            } => {
                list.retain(|k| k != key);
                match before_key
                    .as_deref()
                    .and_then(|b| list.iter().position(|k| k == b))
                {
                    Some(at) => list.insert(at, key.clone()),
                    None => list.push(key.clone()),
                }
            }
            _ => {}
        }
    }
    list
}

#[test]
fn two_prepends_land_in_order_not_reversed() {
    // The first version walked NEW order forward, so it emitted "insert x
    // before y" while y itself was still an un-applied insert. The client
    // cannot resolve a missing anchor, falls back to the tail, and the list
    // came out y,a,b,x. Only a MULTI-insert can see this — every single-op
    // test passed (#1543).
    let _g = FlagGuard::on();
    let patches = diff_nodes(
        &virtual_list(&["a", "b"]),
        &virtual_list(&["x", "y", "a", "b"]),
        &[],
    );

    assert_eq!(apply_ops(&["a", "b"], &patches), vec!["x", "y", "a", "b"]);
}

#[test]
fn a_scramble_replays_to_the_new_order() {
    // Moves and inserts interleaved, with the LIS leaving some keys untouched.
    let _g = FlagGuard::on();
    let old = ["a", "b", "c", "d"];
    let new = ["d", "b", "z", "a"];
    let patches = diff_nodes(&virtual_list(&old), &virtual_list(&new), &[]);

    assert_eq!(
        apply_ops(&old, &patches),
        new.to_vec(),
        "replay must reach the new order"
    );
}

fn text(t: &str) -> VNode {
    VNode {
        tag: "#text".to_string(),
        attrs: Default::default(),
        children: vec![],
        text: Some(t.to_string()),
        key: None,
        djust_id: None,
        cached_html: None,
    }
}

#[test]
fn a_surviving_row_whose_content_changed_still_emits_a_patch() {
    // The structural ops only reposition. Without recursing into matched
    // pairs the way reconcile_keyed does, editing a row that does not move
    // emitted NOTHING and the client would show stale text forever.
    let _g = FlagGuard::on();
    let mut old = virtual_list(&["a", "b"]);
    let mut new = virtual_list(&["a", "b"]);
    old.children[1].children.push(text("before"));
    new.children[1].children.push(text("EDITED"));
    let patches = diff_nodes(&old, &new, &[]);

    assert!(
        !patches.is_empty(),
        "an in-place content edit inside a [dj-virtual] parent must emit a patch"
    );
}

// ---------------------------------------------------------------------------
// Stage 11 findings. Every one of these is a variant the first 14 cases did
// not exercise — the routing gate was written against a NON-EMPTY, ALL-KEYED,
// UNIQUE-KEYED new list, which is the only shape they ever built.
// ---------------------------------------------------------------------------

#[test]
fn clearing_a_virtual_list_stays_key_addressed() {
    // THE most common feed operation — clear, filter-to-nothing, search miss.
    // An empty new list has no keyed children, so a gate on `any_new_keyed`
    // sent it to the INDEX-addressed path and emitted RemoveChild against a
    // windowed container. The gate is now the parent.
    let _g = FlagGuard::on();
    let patches = diff_nodes(&virtual_list(&["a", "b", "c"]), &virtual_list(&[]), &[]);

    assert!(
        !patches
            .iter()
            .any(|p| matches!(p, Patch::RemoveChild { .. })),
        "clearing a [dj-virtual] list must not emit index-addressed removes; got {:?}",
        kinds(&patches)
    );
    assert_eq!(
        patches
            .iter()
            .filter(|p| matches!(p, Patch::VirtualRemove { .. }))
            .count(),
        3,
        "every row must be removed by key"
    );
    assert_eq!(apply_ops(&["a", "b", "c"], &patches), Vec::<String>::new());
}

#[test]
fn an_unkeyed_child_falls_back_instead_of_vanishing() {
    // reconcile_virtual_keyed only sees KEYED children, so an unkeyed sibling
    // (a header row, a totals row) had every change to it silently dropped —
    // no patch, no warning. It now falls back to the plain reconcilers, which
    // handle unkeyed children explicitly.
    let _g = FlagGuard::on();
    let mut old = virtual_list(&["a"]);
    let mut new = virtual_list(&["a"]);
    old.children
        .insert(0, el("div", None, &[], vec![text("HEADER")]));
    new.children
        .insert(0, el("div", None, &[], vec![text("CHANGED")]));
    let patches = diff_nodes(&old, &new, &[]);

    assert!(
        !patches.is_empty(),
        "a change to an unkeyed child of a [dj-virtual] parent must emit something"
    );
}

#[test]
fn a_duplicate_key_falls_back_instead_of_losing_a_row() {
    // `before_key` addresses a row by key, so a duplicate is unaddressable:
    // the new row collapsed into a hash set and never appeared. The plain
    // path demotes ambiguous keys to positional diffing and warns DJE-051.
    let _g = FlagGuard::on();
    let added = diff_nodes(
        &virtual_list(&["a", "b"]),
        &virtual_list(&["a", "a", "b"]),
        &[],
    );
    let removed = diff_nodes(
        &virtual_list(&["a", "a", "b"]),
        &virtual_list(&["a", "b"]),
        &[],
    );

    assert!(
        !added.is_empty(),
        "adding a duplicate-keyed row must emit something"
    );
    assert!(
        !removed.is_empty(),
        "dropping a duplicate-keyed row must emit something"
    );
}

fn comment(text: &str) -> VNode {
    VNode {
        tag: "#comment".to_string(),
        attrs: Default::default(),
        children: vec![],
        text: Some(text.to_string()),
        key: None,
        djust_id: None,
        cached_html: None,
    }
}

#[test]
fn a_content_change_is_key_addressed_with_row_relative_paths() {
    // #2136. This test used to pin the ABSOLUTE-index arithmetic for
    // path-addressed content patches — and that arithmetic is exactly what
    // was wrong: for a windowed container the item index counts ITEMS while
    // the DOM holds only the visible window, and the patch carries no dj-id
    // (text nodes have none), so it resolved purely positionally. Measured
    // against a real mounted list, editing row k0 after a scroll silently
    // rewrote k7.
    //
    // Content is now wrapped in a key-addressed VirtualUpdate whose inner
    // paths are relative to the ROW, so no index into the parent appears at
    // all. The dj-if boundary that used to make abs != filtered-position is
    // kept in the fixture: under the old scheme it was the shape that
    // diverged, and under this one it must simply not matter.
    let _g = FlagGuard::on();
    let build = |second: &str| {
        let mut n = el(
            "div",
            None,
            &[("dj-virtual", "rows"), ("dj-virtual-item-height", "20")],
            vec![
                comment(r#"dj-if id="b1""#),
                comment("/dj-if"),
                row("a"),
                row("b"),
            ],
        );
        n.children[3].children.push(text(second));
        n
    };
    let patches = diff_nodes(&build("before"), &build("EDITED"), &[]);

    // No path-addressed content op for a virtual parent, at all.
    assert!(
        !patches
            .iter()
            .any(|p| matches!(p, Patch::SetText { .. } | Patch::SetAttr { .. })),
        "content must be key-addressed, not path-addressed; got {:?}",
        kinds(&patches)
    );

    let updates: Vec<&Patch> = patches
        .iter()
        .filter(|p| matches!(p, Patch::VirtualUpdate { .. }))
        .collect();
    assert_eq!(updates.len(), 1, "one edited row -> one VirtualUpdate");

    match updates[0] {
        Patch::VirtualUpdate { key, patches, .. } => {
            assert_eq!(key, "b", "addressed by the row's KEY");
            assert!(!patches.is_empty(), "the inner patches carry the edit");
            // Relative to the row: the row itself is [], its first child [0].
            for inner in patches {
                if let Patch::SetText { path, .. } = inner {
                    assert_eq!(
                        path,
                        &vec![0usize],
                        "inner paths are relative to the ROW, so the text child \
                         is [0] — not an index into the virtual parent"
                    );
                }
            }
        }
        _ => unreachable!(),
    }
}

#[test]
fn an_off_window_row_still_gets_its_content_update() {
    // The point of key-addressing: a row the client is holding DETACHED (out
    // of the visible window) is unreachable by path but findable by key, so
    // its update lands in the pool and appears when it scrolls back.
    let _g = FlagGuard::on();
    let keys: Vec<String> = (0..50).map(|i| format!("k{i}")).collect();
    let kv: Vec<&str> = keys.iter().map(|s| s.as_str()).collect();
    let mut old = virtual_list(&kv);
    let mut new = virtual_list(&kv);
    old.children[49].children.push(text("before"));
    new.children[49].children.push(text("EDITED"));

    let patches = diff_nodes(&old, &new, &[]);

    let keyed: Vec<&str> = patches
        .iter()
        .filter_map(|p| match p {
            Patch::VirtualUpdate { key, .. } => Some(key.as_str()),
            _ => None,
        })
        .collect();
    assert_eq!(
        keyed,
        vec!["k49"],
        "the 50th row — far outside any window — must be addressed by key"
    );
}

#[test]
fn ops_are_only_correct_in_emitted_order() {
    // `before_key` names an anchor placed by an EARLIER op, so the sequence is
    // load-bearing. The client's _sortPatches currently keeps these in order
    // only because Array#sort is stable and they share a phase; if iteration 2
    // gives them the natural Remove/Move/Insert phases the anchors break.
    // This pins the dependency so that change fails here first.
    let _g = FlagGuard::on();
    let old = ["a", "b", "c"];
    let new = ["c", "x", "a", "y", "b"];
    let patches = diff_nodes(&virtual_list(&old), &virtual_list(&new), &[]);

    assert_eq!(
        apply_ops(&old, &patches),
        new.to_vec(),
        "emitted order must replay"
    );

    // Phase-sorted the way the other patch kinds are — must diverge, proving
    // the order is a real requirement and not an accident of this input.
    let mut phased: Vec<Patch> = patches.clone();
    phased.sort_by_key(|p| match p {
        Patch::VirtualRemove { .. } => 0,
        Patch::VirtualMove { .. } => 1,
        _ => 2,
    });
    assert_ne!(
        apply_ops(&old, &phased),
        new.to_vec(),
        "if phase-sorting also replays correctly this test proves nothing — pick a \
         harder case or the ordering requirement has changed"
    );
}

#[test]
fn exhaustive_replay_over_a_four_key_universe() {
    // The Stage 11 reviewer fuzzed this and found no counterexample; keeping
    // the sweep means a future change to the reconciler has to survive it too,
    // rather than relying on someone re-running an ad-hoc script.
    let _g = FlagGuard::on();

    let universe = ["a", "b", "c", "d"];
    let subsets: Vec<Vec<&str>> = (0..(1u32 << universe.len()))
        .map(|mask| {
            universe
                .iter()
                .enumerate()
                .filter(|(i, _)| mask & (1 << i) != 0)
                .map(|(_, k)| *k)
                .collect()
        })
        .collect();

    // Every subset against every PERMUTATION of every subset.
    let mut checked = 0usize;
    for old in &subsets {
        for new_set in &subsets {
            for new in permutations(new_set) {
                let patches = diff_nodes(&virtual_list(old), &virtual_list(&new), &[]);
                let replayed = apply_ops(old, &patches);
                assert_eq!(
                    replayed,
                    new.iter().map(|s| s.to_string()).collect::<Vec<_>>(),
                    "replay diverged for old={old:?} new={new:?}"
                );
                checked += 1;
            }
        }
    }
    set_virtual_keyed_ops(false);
    assert!(checked > 1000, "expected a meaningful sweep, ran {checked}");
}

fn permutations<'a>(items: &[&'a str]) -> Vec<Vec<&'a str>> {
    if items.len() <= 1 {
        return vec![items.to_vec()];
    }
    let mut out = Vec::new();
    for i in 0..items.len() {
        let mut rest = items.to_vec();
        let head = rest.remove(i);
        for mut tail in permutations(&rest) {
            tail.insert(0, head);
            out.push(tail);
        }
    }
    out
}
