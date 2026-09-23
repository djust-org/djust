//! #2999 — whitespace between inline siblings is a kept `" "` VDOM node.
//!
//! These pin the DIFF side: patches must address nodes with that space
//! counted (so the client, which counts a `" "` text node, lands on the same
//! node), and applying them with the server's own model
//! (`patch::apply_patches`, mirrored by the client's `_applyChildPlacements`)
//! must reproduce the new tree — including keyed reorders across `" "`
//! separators, which need the placement model and the id-less-relocation rule
//! in `reconcile_keyed`.

use djust_vdom::diff::{diff_nodes, sync_ids};
use djust_vdom::patch::apply_patches;
use djust_vdom::{parse_html, parse_html_continue, Patch, VNode};

/// Old tree, new tree (ids continued past the old tree's, as render_with_diff
/// does), and the patches between them.
fn diff_pair(old_html: &str, new_html: &str) -> (VNode, VNode, Vec<Patch>) {
    let old = parse_html(old_html).unwrap();
    let mut new = parse_html_continue(new_html).unwrap();
    let patches = diff_nodes(&old, &new, &[]);
    sync_ids(&old, &mut new);
    (old, new, patches)
}

/// Serialization without dj-id attributes, for comparing trees whose ids were
/// assigned independently.
fn shape_html(node: &VNode) -> String {
    let mut n = node.clone();
    fn strip(n: &mut VNode) {
        n.attrs.remove("dj-id");
        n.cached_html = None;
        for c in &mut n.children {
            strip(c);
        }
    }
    strip(&mut n);
    n.to_html()
}

fn assert_round_trip(old_html: &str, new_html: &str) -> Vec<Patch> {
    let (old, new, patches) = diff_pair(old_html, new_html);
    let mut applied = old.clone();
    apply_patches(&mut applied, &patches);
    assert_eq!(
        shape_html(&applied),
        shape_html(&new),
        "round trip failed\n old: {old_html}\n new: {new_html}\n patches: {patches:#?}"
    );
    patches
}

#[test]
fn settext_after_a_kept_space_targets_the_right_node() {
    let (_, _, patches) = diff_pair(
        "<div dj-root><p><b>A</b> <i>B</i></p></div>",
        "<div dj-root><p><b>A</b> <i>C</i></p></div>",
    );
    // p=[0]; its children: b=0, " "=1, i=2; the text is i's child 0.
    assert_eq!(patches.len(), 1, "{patches:#?}");
    match &patches[0] {
        Patch::SetText { path, text, .. } => {
            assert_eq!(path, &vec![0, 2, 0]);
            assert_eq!(text, "C");
        }
        other => panic!("expected SetText, got {other:?}"),
    }
}

#[test]
fn inserting_an_inline_sibling_next_to_a_kept_space() {
    let patches = assert_round_trip(
        "<div dj-root><p><b>A</b> <i>B</i></p></div>",
        "<div dj-root><p><b>A</b> <u>N</u> <i>B</i></p></div>",
    );
    // New children: b=0, " "=1, u=2, " "=3, i=4. Every insert index counts
    // the spaces.
    let inserts: Vec<usize> = patches
        .iter()
        .filter_map(|p| match p {
            Patch::InsertChild { index, .. } => Some(*index),
            _ => None,
        })
        .collect();
    assert!(!inserts.is_empty(), "{patches:#?}");
    assert!(inserts.iter().all(|&i| i <= 4), "{patches:#?}");
}

#[test]
fn removing_an_inline_sibling_next_to_a_kept_space() {
    assert_round_trip(
        "<div dj-root><p><b>A</b> <u>N</u> <i>B</i></p></div>",
        "<div dj-root><p><b>A</b> <i>B</i></p></div>",
    );
    assert_round_trip(
        "<div dj-root><p><b>A</b> <i>B</i></p></div>",
        "<div dj-root><p><i>B</i></p></div>",
    );
    assert_round_trip(
        "<div dj-root><p><b>A</b> <i>B</i></p></div>",
        "<div dj-root><p><b>A</b></p></div>",
    );
}

#[test]
fn space_appearing_and_disappearing_with_a_neighbour_change() {
    // `</b> <div>` has no space; `</b> <i>` does.
    assert_round_trip(
        "<div dj-root><section><b>A</b> <div>x</div></section></div>",
        "<div dj-root><section><b>A</b> <i>x</i></section></div>",
    );
    assert_round_trip(
        "<div dj-root><section><b>A</b> <i>x</i></section></div>",
        "<div dj-root><section><b>A</b> <div>x</div></section></div>",
    );
}

fn keyed_inline(keys: &[&str], sep: &str) -> String {
    let items: Vec<String> = keys
        .iter()
        .map(|k| format!("<b dj-key=\"{k}\">{k}</b>"))
        .collect();
    format!("<div dj-root><p>{}</p></div>", items.join(sep))
}

fn keyed_block(keys: &[&str]) -> String {
    let items: String = keys
        .iter()
        .map(|k| format!("\n  <li dj-key=\"{k}\">{k}</li>"))
        .collect();
    format!("<div dj-root><ul>{items}\n</ul></div>")
}

const REORDERS: &[(&[&str], &[&str])] = &[
    (&["a", "b", "c"], &["b", "c", "a"]),
    (&["a", "b", "c"], &["c", "a", "b"]),
    (&["a", "b"], &["b", "a"]),
    (&["A", "B", "C", "D", "E"], &["C", "D", "E", "A", "B"]),
    (&["A", "B", "C", "D"], &["D", "C", "B", "A"]),
    (&["A", "B", "C", "D"], &["B", "A", "D", "C"]),
    (&["a", "b", "c", "d"], &["x", "d", "a"]),
    (&["a", "b", "c"], &["c", "x", "b", "y", "a"]),
    (&["a", "b", "c", "d", "e"], &["e", "b"]),
];

#[test]
fn keyed_reorders_round_trip_across_space_separators() {
    for (old, new) in REORDERS {
        for sep in [" ", ", ", ""] {
            assert_round_trip(&keyed_inline(old, sep), &keyed_inline(new, sep));
        }
    }
}

#[test]
fn keyed_block_reorders_round_trip() {
    // No separators survive between block items; the rotation case used to
    // come out [C,D,A,E,B] under one-at-a-time moves.
    for (old, new) in REORDERS {
        assert_round_trip(&keyed_block(old), &keyed_block(new));
    }
}

#[test]
fn relocated_space_separator_is_reinserted_not_left_behind() {
    // Removing the first item shifts every " " one slot left; an id-less text
    // can't be moved by dj-id, so the differ re-creates it at its new index.
    let patches = assert_round_trip(
        &keyed_inline(&["a", "b", "c"], " "),
        &keyed_inline(&["b", "c"], " "),
    );
    assert!(
        patches
            .iter()
            .any(|p| matches!(p, Patch::RemoveChild { child_d: None, .. })),
        "{patches:#?}"
    );
}
