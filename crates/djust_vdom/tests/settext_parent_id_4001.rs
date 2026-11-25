//! #4001 — a `SetText` names its parent element by dj-id plus the node's index.
//!
//! A text node has no dj-id, so a `SetText` addressed by `path` alone made the
//! client walk child lists from the root on every turn. Both producers (the
//! tree diff and the text-only fast path) now add `parent_d` + `index`, and a
//! patch without them still deserializes (wire compatibility).

use djust_vdom::diff::diff_nodes;
use djust_vdom::{parse_html, parse_html_continue, try_text_patches, Patch, VNode};

fn node_at<'a>(root: &'a VNode, path: &[usize]) -> &'a VNode {
    path.iter().fold(root, |n, &i| &n.children[i])
}

/// Every `SetText` in `patches` names the parent of the node its path reaches.
fn assert_parent_addressed(old: &VNode, patches: &[Patch]) -> usize {
    let mut seen = 0;
    for p in patches {
        if let Patch::SetText {
            path,
            parent_d,
            index,
            ..
        } = p
        {
            seen += 1;
            let (last, parent_path) = path.split_last().expect("text below the root");
            let parent = node_at(old, parent_path);
            assert_eq!(parent_d.as_deref(), parent.djust_id.as_deref(), "{p:?}");
            assert!(parent_d.is_some(), "{p:?}");
            assert_eq!(*index, Some(*last), "{p:?}");
            assert!(parent.children[*last].is_text(), "{p:?}");
        }
    }
    seen
}

#[test]
fn diff_set_text_carries_parent_id_and_index() {
    let old_html =
        "<div dj-root><header><b>0</b></header><ul><li>a</li><li>x <i>y</i> b</li></ul></div>";
    let new_html =
        "<div dj-root><header><b>1</b></header><ul><li>a</li><li>x <i>y</i> c</li></ul></div>";
    let old = parse_html(old_html).unwrap();
    let new = parse_html_continue(new_html).unwrap();
    let patches = diff_nodes(&old, &new, &[]);
    assert_eq!(assert_parent_addressed(&old, &patches), 2, "{patches:?}");
}

#[test]
fn text_fast_path_set_text_carries_parent_id_and_index() {
    let old_html = "<div dj-root><p>x <b>y</b> one</p></div>";
    let new_html = "<div dj-root><p>x <b>y</b> two</p></div>";
    let old = parse_html(old_html).unwrap();
    let patches = try_text_patches(&old, old_html, new_html).expect("text-only change");
    assert_eq!(assert_parent_addressed(&old, &patches), 1, "{patches:?}");
}

#[test]
fn set_text_wire_shape_and_backward_compatible_parse() {
    let p = Patch::SetText {
        path: vec![0, 2],
        d: None,
        text: "t".into(),
        parent_d: Some("1a".into()),
        index: Some(2),
    };
    assert_eq!(
        serde_json::to_string(&p).unwrap(),
        r#"{"type":"SetText","path":[0,2],"text":"t","parent_d":"1a","index":2}"#
    );
    // A SetText from before #4001 (no parent_d / index) still parses.
    let old: Patch = serde_json::from_str(r#"{"type":"SetText","path":[0],"text":"x"}"#).unwrap();
    match old {
        Patch::SetText {
            parent_d, index, ..
        } => assert!(parent_d.is_none() && index.is_none()),
        other => panic!("{other:?}"),
    }
}
