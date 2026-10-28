//! `dj-update="append"` / `"prepend"` containers accumulate across renders (#3639).
//!
//! A view with `temporary_assigns` renders only the rows it just assigned. Diffed
//! like any other list that rewrites the first stored row into the new one and
//! removes the rest; `splice_accumulating_subtrees` instead puts the stored rows
//! back in front of (or behind) the new ones so the diff is an `InsertChild`.
//! The client already follows this rule for a full `html` frame
//! (`applyDjUpdateElements`), so these tests pin the same rule for patches.

use djust_vdom::patch::apply_patches;
use djust_vdom::{diff, parse_html, splice_accumulating_subtrees, sync_ids, Patch, VNode};
use std::collections::HashSet;

fn page(mode: &str, rows: &[(&str, &str)], tail: &str) -> String {
    let body: String = rows
        .iter()
        .map(|(id, text)| format!(r#"<div id="{id}">{text}</div>"#))
        .collect();
    format!(r#"<div><div id="list" dj-update="{mode}">{body}</div><p>{tail}</p></div>"#)
}

fn list() -> HashSet<String> {
    HashSet::from(["list".to_string()])
}

/// The splice as the render path calls it.
fn splice(old: &VNode, new: &mut VNode) -> usize {
    splice_accumulating_subtrees(Some(old), new, &list())
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

    splice(&old, &mut new);

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

    splice(&old, &mut new);

    assert_eq!(ids(container(&new)), ["r2", "r0", "r1"]);
}

#[test]
fn a_row_already_on_the_page_is_neither_added_twice_nor_updated() {
    // The client's rule: a child whose id is on the page is left as it is.
    let old = parse_html(&page("append", &[("r0", "a"), ("r1", "b")], "0")).unwrap();
    let mut new = parse_html(&page("append", &[("r1", "CHANGED"), ("r2", "c")], "0")).unwrap();

    splice(&old, &mut new);

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

    splice(&old, &mut new);

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

    splice(&old, &mut new);

    assert_eq!(ids(container(&new)), ["r0"]);
}

#[test]
fn a_container_without_an_id_is_not_accumulated() {
    // The client skips it too ("Element with dj-update must have an id").
    let old =
        parse_html(r#"<div><div dj-update="append"><div id="r0">a</div></div></div>"#).unwrap();
    let mut new =
        parse_html(r#"<div><div dj-update="append"><div id="r1">b</div></div></div>"#).unwrap();

    splice(&old, &mut new);

    assert_eq!(ids(container(&new)), ["r1"]);
}

#[test]
fn a_container_whose_mode_changed_is_not_accumulated() {
    let old = parse_html(&page("append", &[("r0", "a")], "0")).unwrap();
    let mut new = parse_html(&page("prepend", &[("r1", "b")], "0")).unwrap();

    splice(&old, &mut new);

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

        splice(&old, &mut new);

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
        splice(&stored, &mut next);
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

// ---------------------------------------------------------------------------
// Review fixes: the render path's steps, replayed with the patch applier
// ---------------------------------------------------------------------------

/// A page whose rows carry an `id` and a `data-pk`, so a row that ends up with
/// another row's attributes is visible.
fn pk_page(mode: &str, rows: &[&str], before: &str) -> String {
    let body: String = rows
        .iter()
        .map(|id| format!(r#"<li id="{id}" data-pk="pk-{id}">{id}</li>"#))
        .collect();
    format!(r#"<div>{before}<ul id="list" dj-update="{mode}">{body}</ul><p>x</p></div>"#)
}

/// One render turn, as `render_with_diff` runs it: parse continuing the id
/// counter past the stored tree, splice, diff, carry ids forward. Returns the
/// patches and the new stored tree.
fn render_turn(
    stored: Option<&VNode>,
    html: &str,
    ids: &HashSet<String>,
) -> (Vec<Patch>, VNode, usize) {
    if let Some(stored) = stored {
        if let Some(max) = djust_vdom::max_djust_id_in(stored) {
            djust_vdom::ensure_id_counter_at_least(max + 1);
        }
    }
    let mut next = match stored {
        Some(_) => djust_vdom::parse_html_continue(html).unwrap(),
        None => parse_html(html).unwrap(),
    };
    let dropped = splice_accumulating_subtrees(stored, &mut next, ids);
    let patches = stored.map(|old| diff(old, &next)).unwrap_or_default();
    if let Some(old) = stored {
        sync_ids(old, &mut next);
    }
    (patches, next, dropped)
}

fn row_attrs(root: &VNode, list_id: &str) -> Vec<(String, String, Option<String>)> {
    fn find<'a>(node: &'a VNode, list_id: &str) -> Option<&'a VNode> {
        if node.attrs.get("id").map(String::as_str) == Some(list_id) {
            return Some(node);
        }
        node.children.iter().find_map(|c| find(c, list_id))
    }
    find(root, list_id)
        .unwrap()
        .children
        .iter()
        .map(|c| {
            (
                c.attrs.get("id").cloned().unwrap_or_default(),
                c.attrs.get("data-pk").cloned().unwrap_or_default(),
                c.djust_id.clone(),
            )
        })
        .collect()
}

#[test]
fn prepend_over_several_events_is_one_insert_at_the_front_each_with_the_right_attributes() {
    let ids = list();
    let (_, mut stored, _) = render_turn(None, &pk_page("prepend", &["r0", "r1"], ""), &ids);
    // The client's copy of the page, patched event by event.
    let mut client = stored.clone();

    for (n, row) in ["r2", "r3", "r4", "r5"].iter().enumerate() {
        let (patches, next, dropped) =
            render_turn(Some(&stored), &pk_page("prepend", &[row], ""), &ids);
        assert_eq!(dropped, 0);
        assert_eq!(kinds(&patches), ["InsertChild"], "event {n}: {patches:?}");
        match &patches[0] {
            Patch::InsertChild { index, node, .. } => {
                assert_eq!(*index, 0, "event {n}");
                assert_eq!(node.attrs.get("id").map(String::as_str), Some(*row));
            }
            other => panic!("{other:?}"),
        }
        apply_patches(&mut client, &patches);
        stored = next;

        // Every row keeps its own id and data-pk, and no dj-id repeats.
        let rows = row_attrs(&stored, "list");
        for (id, pk, _) in &rows {
            assert_eq!(pk, &format!("pk-{id}"), "event {n}: {rows:?}");
        }
        let djust_ids: HashSet<_> = rows.iter().map(|(_, _, d)| d.clone()).collect();
        assert_eq!(
            djust_ids.len(),
            rows.len(),
            "event {n}: duplicate dj-id {rows:?}"
        );
        // What the patches did to the client is what the server now stores.
        assert_eq!(row_attrs(&client, "list"), rows, "event {n}");
    }
    let order: Vec<_> = row_attrs(&stored, "list")
        .into_iter()
        .map(|r| r.0)
        .collect();
    assert_eq!(order, ["r5", "r4", "r3", "r2", "r0", "r1"]);
}

#[test]
fn append_over_several_events_keeps_attributes_and_ids_too() {
    let ids = list();
    let (_, mut stored, _) = render_turn(None, &pk_page("append", &["r0", "r1"], ""), &ids);
    let mut client = stored.clone();
    for row in ["r2", "r3", "r4"] {
        let (patches, next, _) = render_turn(Some(&stored), &pk_page("append", &[row], ""), &ids);
        assert_eq!(kinds(&patches), ["InsertChild"], "{patches:?}");
        apply_patches(&mut client, &patches);
        stored = next;
        assert_eq!(row_attrs(&client, "list"), row_attrs(&stored, "list"));
    }
}

#[test]
fn a_container_that_moved_is_found_by_its_id_not_by_its_position() {
    // A block before the list appears in the same render as an appended row, so
    // the container is one position later than in the stored tree. Paired by
    // index the stored rows were lost.
    let ids = list();
    let (_, stored, _) = render_turn(None, &pk_page("append", &["r0", "r1", "r2"], ""), &ids);
    let (patches, next, _) = render_turn(
        Some(&stored),
        &pk_page("append", &["r3"], "<p class=\"banner\">hi</p>"),
        &ids,
    );
    let order: Vec<_> = row_attrs(&next, "list").into_iter().map(|r| r.0).collect();
    assert_eq!(order, ["r0", "r1", "r2", "r3"], "{patches:?}");
    // Applied to the client's copy, the result is the same page.
    let mut client = stored.clone();
    apply_patches(&mut client, &patches);
    assert_eq!(row_attrs(&client, "list"), row_attrs(&next, "list"));
}

#[test]
fn a_container_that_is_not_named_is_diffed_as_an_ordinary_list() {
    // Same markup, but the caller does not list this container: the template
    // is authoritative and a row it no longer renders is removed.
    let none = HashSet::new();
    let (_, stored, _) = render_turn(None, &pk_page("append", &["r0", "r1"], ""), &none);
    let (patches, next, _) = render_turn(Some(&stored), &pk_page("append", &["r1"], ""), &none);
    assert!(kinds(&patches).contains(&"RemoveChild"), "{patches:?}");
    assert_eq!(row_attrs(&next, "list").len(), 1);

    let other = HashSet::from(["elsewhere".to_string()]);
    let (_, stored, _) = render_turn(None, &pk_page("append", &["r0", "r1"], ""), &other);
    let (patches, _, _) = render_turn(Some(&stored), &pk_page("append", &["r1"], ""), &other);
    assert!(kinds(&patches).contains(&"RemoveChild"), "{patches:?}");
}

#[test]
fn two_containers_with_the_same_id_are_left_alone() {
    let ids = list();
    let twice = |a: &str, b: &str| {
        format!(
            r#"<div><ul id="list" dj-update="append"><li id="{a}">a</li></ul><ul id="list" dj-update="append"><li id="{b}">b</li></ul></div>"#
        )
    };
    let (_, stored, _) = render_turn(None, &twice("a0", "b0"), &ids);
    let (patches, next, _) = render_turn(Some(&stored), &twice("a1", "b1"), &ids);
    // Not accumulated: each list shows what was rendered.
    assert_eq!(next.children[0].children.len(), 1, "{patches:?}");
}

#[test]
fn rows_without_an_id_are_counted() {
    let ids = list();
    let (_, stored, _) = render_turn(None, &pk_page("append", &["r0"], ""), &ids);
    let html = r#"<div><ul id="list" dj-update="append"><li>no id</li><li id="r1">x</li><li>none</li></ul></div>"#;
    let (_, next, dropped) = render_turn(Some(&stored), html, &ids);
    assert_eq!(dropped, 2);
    assert_eq!(row_attrs(&next, "list").len(), 2);
}

#[test]
fn a_prepend_container_that_is_not_named_is_not_keyed_either() {
    // Naming some other container must leave this one exactly as the plain
    // differ treats it: positional, so removing the first row rewrites the rest.
    let other = HashSet::from(["elsewhere".to_string()]);
    let none = HashSet::new();
    let run = |ids: &HashSet<String>| {
        let (_, stored, _) = render_turn(None, &pk_page("prepend", &["r0", "r1", "r2"], ""), ids);
        let (patches, _, _) =
            render_turn(Some(&stored), &pk_page("prepend", &["r1", "r2"], ""), ids);
        kinds(&patches)
    };
    assert_eq!(run(&other), run(&none));
    assert!(
        run(&none).contains(&"SetAttr"),
        "the plain differ shifts rows by position"
    );
}

// ---------------------------------------------------------------------------
// #3647: duplicate ids in a prepend container, and a stored tree with no keys
// ---------------------------------------------------------------------------

/// A page whose rows are `(id, pk)` pairs, so two rows can share an `id` and
/// still be told apart by their `data-pk`.
fn dup_page(mode: &str, rows: &[(&str, &str)]) -> String {
    let body: String = rows
        .iter()
        .map(|(id, pk)| format!(r#"<li id="{id}" data-pk="{pk}">{pk}</li>"#))
        .collect();
    format!(r#"<div><ul id="list" dj-update="{mode}">{body}</ul><p>x</p></div>"#)
}

fn pairs(root: &VNode) -> Vec<(String, String, Option<String>)> {
    row_attrs(root, "list")
}

fn pk_order(root: &VNode) -> Vec<String> {
    pairs(root).into_iter().map(|r| r.1).collect()
}

/// A patch the client could not apply: a MoveChild (the prepend of one row never
/// needs to move another) or a RemoveChild of a row that was not asked to leave.
fn assert_only_inserts(patches: &[Patch], context: &str) {
    assert!(
        patches
            .iter()
            .all(|p| matches!(p, Patch::InsertChild { .. })),
        "{context}: {patches:?}"
    );
}

#[test]
fn a_prepend_container_whose_rows_repeat_an_id_still_takes_one_insert_per_new_row() {
    // Mount: two rows share an id. Then three events each prepend one row.
    let ids = list();
    let (_, mut stored, _) = render_turn(
        None,
        &dup_page("prepend", &[("r0", "a"), ("r0", "b"), ("r1", "c")]),
        &ids,
    );
    let mut client = stored.clone();
    for (n, pk) in ["d", "e", "f"].iter().enumerate() {
        let id = format!("n{n}");
        let (patches, next, _) = render_turn(
            Some(&stored),
            &dup_page("prepend", &[(id.as_str(), pk)]),
            &ids,
        );
        assert_only_inserts(&patches, &format!("event {n}"));
        apply_patches(&mut client, &patches);
        assert_eq!(pairs(&client), pairs(&next), "event {n}: client != server");
        stored = next;
    }
    assert_eq!(pk_order(&stored), ["f", "e", "d", "a", "b", "c"]);
}

#[test]
fn a_batch_of_new_prepend_rows_that_repeat_an_id_is_inserted_whole() {
    let ids = list();
    let (_, stored, _) = render_turn(None, &dup_page("prepend", &[("r0", "a")]), &ids);
    let (patches, next, _) = render_turn(
        Some(&stored),
        &dup_page("prepend", &[("x", "b"), ("x", "c"), ("y", "d")]),
        &ids,
    );
    assert_only_inserts(&patches, "batch");
    let mut client = stored.clone();
    apply_patches(&mut client, &patches);
    assert_eq!(pairs(&client), pairs(&next));
    assert_eq!(pk_order(&next), ["b", "c", "d", "a"]);

    // And a following event on top of it.
    let (patches, after, _) = render_turn(Some(&next), &dup_page("prepend", &[("z", "e")]), &ids);
    assert_only_inserts(&patches, "the event after the batch");
    apply_patches(&mut client, &patches);
    assert_eq!(pairs(&client), pairs(&after));
    assert_eq!(pk_order(&after), ["e", "b", "c", "d", "a"]);
}

#[test]
fn rows_with_unique_ids_are_still_keyed_by_id() {
    // The keying itself stays: a prepend is one insert at index 0 and every
    // stored row keeps its own dj-id.
    let ids = list();
    let (_, stored, _) = render_turn(
        None,
        &dup_page("prepend", &[("r0", "a"), ("r1", "b")]),
        &ids,
    );
    let (patches, next, _) = render_turn(Some(&stored), &dup_page("prepend", &[("r2", "c")]), &ids);
    assert_only_inserts(&patches, "unique ids");
    let before: Vec<_> = pairs(&stored).into_iter().map(|r| r.2).collect();
    let after: Vec<_> = pairs(&next).into_iter().skip(1).map(|r| r.2).collect();
    assert_eq!(before, after, "stored rows keep their dj-ids");
}

#[test]
fn a_prepend_container_stored_without_keys_is_rebuilt_once_then_keyed() {
    // The stored tree was built while this container was NOT named (a template
    // edit brought it into the set later), so its rows carry no keys. The first
    // prepend after that pairs unkeyed stored rows against keyed new ones: the
    // stored rows are re-created once (`merged` keys the stored children, which is
    // not redundant for such a tree), and from then on each event is a single
    // insert. Without that keying the stored rows stay unkeyed and are MOVED on
    // every event, one `MoveChild` per stored row, for the life of the page.
    let none = HashSet::new();
    let ids = list();
    let (_, stored, _) = render_turn(
        None,
        &dup_page("prepend", &[("r0", "a"), ("r1", "b"), ("r2", "c")]),
        &none,
    );
    assert!(
        stored.children[0].children.iter().all(|c| c.key.is_none()),
        "the premise: no keys on the stored rows"
    );

    let (patches, next, _) = render_turn(Some(&stored), &dup_page("prepend", &[("r3", "d")]), &ids);
    let mut client = stored.clone();
    apply_patches(&mut client, &patches);
    assert_eq!(pairs(&client), pairs(&next), "{patches:?}");
    assert_eq!(pk_order(&next), ["d", "a", "b", "c"]);
    assert!(
        !patches.iter().any(|p| matches!(p, Patch::MoveChild { .. })),
        "the stored rows are re-created once, not moved: {patches:?}"
    );

    // From here the rows are keyed: one insert per event.
    let (patches, after, _) = render_turn(Some(&next), &dup_page("prepend", &[("r4", "e")]), &ids);
    assert_only_inserts(&patches, "the event after the rebuild");
    apply_patches(&mut client, &patches);
    assert_eq!(pairs(&client), pairs(&after));
    assert_eq!(pk_order(&after), ["e", "d", "a", "b", "c"]);
}
