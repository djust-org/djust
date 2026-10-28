//! `dj-update="append"` / `"prepend"` containers accumulate across renders (#3639).
//!
//! A view with `temporary_assigns` renders only the rows it just assigned. Diffed
//! like any other list that rewrites the first stored row into the new one and
//! removes the rest; `splice_accumulating_subtrees` instead puts the stored rows
//! back in front of (or behind) the new ones so the diff is an `InsertChild`.
//! The client already follows this rule for a full `html` frame
//! (`applyDjUpdateElements`), so these tests pin the same rule for patches.

use djust_vdom::{diff, parse_html, splice_accumulating_subtrees, sync_ids, Patch, VNode};

fn page(mode: &str, rows: &[(&str, &str)], tail: &str) -> String {
    let body: String = rows
        .iter()
        .map(|(id, text)| format!(r#"<div id="{id}">{text}</div>"#))
        .collect();
    format!(r#"<div><div id="list" dj-update="{mode}">{body}</div><p>{tail}</p></div>"#)
}

fn kinds(patches: &[Patch]) -> Vec<&'static str> {
    patches
        .iter()
        .map(|p| match p {
            Patch::InsertChild { .. } => "InsertChild",
            Patch::RemoveChild { .. } => "RemoveChild",
            Patch::SetText { .. } => "SetText",
            Patch::SetAttr { .. } => "SetAttr",
            _ => "other",
        })
        .collect()
}

fn container(root: &VNode) -> &VNode {
    &root.children[0]
}

fn ids(node: &VNode) -> Vec<String> {
    node.children
        .iter()
        .map(|c| c.attrs.get("id").cloned().unwrap_or_default())
        .collect()
}

#[test]
fn append_keeps_the_stored_rows_and_adds_the_new_one_after_them() {
    let old = parse_html(&page("append", &[("r0", "a"), ("r1", "b")], "0")).unwrap();
    let mut new = parse_html(&page("append", &[("r2", "c")], "0")).unwrap();

    splice_accumulating_subtrees(&old, &mut new);

    assert_eq!(ids(container(&new)), ["r0", "r1", "r2"]);
    let patches = diff(&old, &new);
    assert_eq!(kinds(&patches), ["InsertChild"], "{patches:?}");
}

#[test]
fn without_the_splice_the_same_render_rewrites_and_removes_rows() {
    // The gate-off of the two tests above: this is what the page got before.
    let old = parse_html(&page("append", &[("r0", "a"), ("r1", "b")], "0")).unwrap();
    let new = parse_html(&page("append", &[("r2", "c")], "0")).unwrap();

    let patches = diff(&old, &new);
    assert!(kinds(&patches).contains(&"RemoveChild"), "{patches:?}");
}

#[test]
fn prepend_puts_the_new_rows_before_the_stored_ones() {
    let old = parse_html(&page("prepend", &[("r0", "a"), ("r1", "b")], "0")).unwrap();
    let mut new = parse_html(&page("prepend", &[("r2", "c")], "0")).unwrap();

    splice_accumulating_subtrees(&old, &mut new);

    assert_eq!(ids(container(&new)), ["r2", "r0", "r1"]);
}

#[test]
fn a_row_already_on_the_page_is_neither_added_twice_nor_updated() {
    // The client's rule: a child whose id is on the page is left as it is.
    let old = parse_html(&page("append", &[("r0", "a"), ("r1", "b")], "0")).unwrap();
    let mut new = parse_html(&page("append", &[("r1", "CHANGED"), ("r2", "c")], "0")).unwrap();

    splice_accumulating_subtrees(&old, &mut new);

    assert_eq!(ids(container(&new)), ["r0", "r1", "r2"]);
    assert_eq!(
        container(&new).children[1].children[0].text.as_deref(),
        Some("b")
    );
}

#[test]
fn a_render_with_no_rows_leaves_the_stored_rows_alone() {
    // What the reset default renders: an empty container.
    let old = parse_html(&page("append", &[("r0", "a"), ("r1", "b")], "0")).unwrap();
    let mut new = parse_html(&page("append", &[], "1")).unwrap();

    splice_accumulating_subtrees(&old, &mut new);

    assert_eq!(ids(container(&new)), ["r0", "r1"]);
    let patches = diff(&old, &new);
    assert_eq!(kinds(&patches), ["SetText"], "{patches:?}");
}

#[test]
fn a_new_child_with_no_id_is_dropped_as_the_client_drops_it() {
    let old = parse_html(&page("append", &[("r0", "a")], "0")).unwrap();
    let mut new = parse_html(
        r#"<div><div id="list" dj-update="append"><div>no id</div></div><p>0</p></div>"#,
    )
    .unwrap();

    splice_accumulating_subtrees(&old, &mut new);

    assert_eq!(ids(container(&new)), ["r0"]);
}

#[test]
fn a_container_without_an_id_is_not_accumulated() {
    // The client skips it too ("Element with dj-update must have an id").
    let old =
        parse_html(r#"<div><div dj-update="append"><div id="r0">a</div></div></div>"#).unwrap();
    let mut new =
        parse_html(r#"<div><div dj-update="append"><div id="r1">b</div></div></div>"#).unwrap();

    splice_accumulating_subtrees(&old, &mut new);

    assert_eq!(ids(container(&new)), ["r1"]);
}

#[test]
fn a_container_whose_mode_changed_is_not_accumulated() {
    let old = parse_html(&page("append", &[("r0", "a")], "0")).unwrap();
    let mut new = parse_html(&page("prepend", &[("r1", "b")], "0")).unwrap();

    splice_accumulating_subtrees(&old, &mut new);

    assert_eq!(ids(container(&new)), ["r1"]);
}

#[test]
fn replace_and_unmarked_containers_are_untouched() {
    for attr in [r#" dj-update="replace""#, ""] {
        let old = parse_html(&format!(
            r#"<div><div id="list"{attr}><div id="r0">a</div></div></div>"#
        ))
        .unwrap();
        let mut new = parse_html(&format!(
            r#"<div><div id="list"{attr}><div id="r1">b</div></div></div>"#
        ))
        .unwrap();

        splice_accumulating_subtrees(&old, &mut new);

        assert_eq!(ids(container(&new)), ["r1"], "attr {attr:?}");
    }
}

#[test]
fn consecutive_renders_keep_accumulating_and_keep_ids_consistent() {
    // Render 1 -> 2 -> 3 through the same steps the render path takes
    // (splice, diff, sync_ids), the stored tree being the previous result.
    let mut stored = parse_html(&page("append", &[("r0", "a")], "0")).unwrap();

    for (n, row) in ["r1", "r2", "r3"].iter().enumerate() {
        let mut next = parse_html(&page("append", &[(row, "x")], &n.to_string())).unwrap();
        splice_accumulating_subtrees(&stored, &mut next);
        let patches = diff(&stored, &next);
        let inserts: Vec<_> = patches
            .iter()
            .filter_map(|p| match p {
                Patch::InsertChild { index, .. } => Some(*index),
                _ => None,
            })
            .collect();
        assert_eq!(inserts, [n + 1], "{patches:?}");
        assert!(!kinds(&patches).contains(&"RemoveChild"), "{patches:?}");
        sync_ids(&stored, &mut next);
        stored = next;
    }

    assert_eq!(ids(container(&stored)), ["r0", "r1", "r2", "r3"]);
}
