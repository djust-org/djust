//! Opt-in `id` diff keys for HTML without `dj-if` markers (#4228).
//!
//! HTML rendered without `dj-if` boundary markers gives the differ no anchor
//! for a conditional block: when an unkeyed `{% if %}` block appears, it is
//! paired by position with the sibling that used to be there and every
//! following sibling is rewritten. `key_by_id` is the opt-in that keys such
//! siblings by their `id`. It is never called by djust's own render path, and
//! `parse_html` still leaves `id` out of the key (see the parser's
//! `test_id_attribute_not_used_as_key` / `test_id_in_list_not_used_as_key`).

use djust_vdom::patch::apply_patches;
use djust_vdom::{diff, key_by_id, parse_html, Patch, VNode, ID_KEY_PREFIX};

fn kinds(patches: &[Patch]) -> Vec<String> {
    patches
        .iter()
        .map(|p| {
            let v = serde_json::to_value(p).expect("patch serializes");
            v["type"].as_str().unwrap_or("?").to_string()
        })
        .collect()
}

fn parse(html: &str, id_keys: bool) -> VNode {
    let mut node = parse_html(html).expect("parses");
    if id_keys {
        key_by_id(&mut node);
    }
    node
}

/// `diff_html` as the binding runs it: both sides parsed with a reset counter.
fn diff_html(old: &str, new: &str, id_keys: bool) -> (VNode, VNode, Vec<Patch>) {
    let old = parse(old, id_keys);
    let new = parse(new, id_keys);
    let patches = diff(&old, &new);
    (old, new, patches)
}

fn without_dj_ids(node: &VNode) -> VNode {
    let mut n = node.clone();
    n.attrs.remove("dj-id");
    n.djust_id = None;
    n.cached_html = None;
    n.children = n.children.iter().map(without_dj_ids).collect();
    n
}

/// Applying the patches to the old tree gives the new tree.
fn assert_round_trip(old: &VNode, new: &VNode, patches: &[Patch]) {
    let mut client = old.clone();
    apply_patches(&mut client, patches);
    assert_eq!(
        without_dj_ids(&client).to_html(),
        without_dj_ids(new).to_html(),
        "{patches:?}"
    );
}

const FIELDS: &str = r#"<section id="a"><input name="x"></section><section id="b"><textarea name="y"></textarea></section><section id="c"><p>tail</p></section>"#;

/// The page with or without a conditional banner at the top. `banner_id`:
/// whether the banner itself carries an `id` (so every sibling is keyed).
fn page_with(banner: bool, banner_id: bool) -> String {
    let banner = match (banner, banner_id) {
        (false, _) => "",
        (true, true) => r#"<div id="banner">Saved</div>"#,
        (true, false) => r#"<div class="banner">Saved</div>"#,
    };
    format!("<main>{banner}{FIELDS}</main>")
}

fn page(banner: bool) -> String {
    page_with(banner, true)
}

#[test]
fn default_parse_has_no_id_keys() {
    let node = parse_html(&page(true)).unwrap();
    assert!(node.children.iter().all(|c| c.key.is_none()));
}

#[test]
fn opt_in_keys_elements_by_prefixed_id() {
    let node = parse(&page(false), true);
    let keys: Vec<_> = node.children.iter().map(|c| c.key.clone()).collect();
    assert_eq!(
        keys,
        [Some("#a".into()), Some("#b".into()), Some("#c".into())]
    );
    assert_eq!(ID_KEY_PREFIX, "#");
    // The id attribute itself is unchanged.
    assert_eq!(
        node.children[0].attrs.get("id").map(String::as_str),
        Some("a")
    );
}

#[test]
fn conditional_at_top_without_opt_in_rewrites_the_siblings() {
    // Pins the defect the opt-in exists for; the default must stay this way.
    let (old, new, patches) = diff_html(&page_with(false, false), &page_with(true, false), false);
    assert!(patches.len() > 1, "{patches:?}");
    assert!(
        kinds(&patches).iter().any(|k| k == "Replace"),
        "{patches:?}"
    );
    assert_round_trip(&old, &new, &patches);
}

#[test]
fn conditional_at_top_with_opt_in_is_one_insert() {
    // Every sibling, the conditional included, has an id: one patch each way.
    let (old, new, patches) = diff_html(&page(false), &page(true), true);
    assert_eq!(kinds(&patches), ["InsertChild"], "{patches:?}");
    assert_round_trip(&old, &new, &patches);

    let (old, new, patches) = diff_html(&page(true), &page(false), true);
    assert_eq!(kinds(&patches), ["RemoveChild"], "{patches:?}");
    assert_round_trip(&old, &new, &patches);
}

#[test]
fn conditional_without_id_keeps_its_siblings_but_moves_them() {
    // An unkeyed conditional among id-keyed siblings: the siblings are matched
    // by key and never rewritten, but a mixed keyed/unkeyed list takes no LIS
    // shortcut (#1260), so each shifted sibling gets a MoveChild. The documented
    // rule is to give the conditional an id (or a dj-key) too.
    let (old, new, patches) = diff_html(&page_with(false, false), &page_with(true, false), true);
    let kinds = kinds(&patches);
    assert_eq!(kinds[0], "InsertChild", "{patches:?}");
    assert!(
        kinds[1..].iter().all(|k| k == "MoveChild"),
        "no Replace / SetText of the siblings: {patches:?}"
    );
    assert_round_trip(&old, &new, &patches);
}

#[test]
fn explicit_dj_key_wins_over_id_and_does_not_collide() {
    let node = parse(
        r#"<ul><li dj-key="a" id="a">1</li><li data-key="k" id="z">2</li><li id="a2">3</li></ul>"#,
        true,
    );
    let keys: Vec<_> = node.children.iter().map(|c| c.key.clone()).collect();
    assert_eq!(
        keys,
        [Some("a".into()), Some("k".into()), Some("#a2".into())]
    );
}

#[test]
fn empty_id_and_text_and_comment_nodes_get_no_key() {
    let node = parse(
        r#"<div><p id="">x</p>text<!--dj-if id="if-1"--><b>y</b></div>"#,
        true,
    );
    assert!(node.children.iter().all(|c| c.key.is_none()), "{node:?}");
}

#[test]
fn dj_update_and_dj_virtual_children_are_exempt_but_their_descendants_are_not() {
    for container in [
        r#"dj-update="append""#,
        r#"dj-update="prepend""#,
        r#"dj-update="ignore""#,
        r#"dj-virtual="40""#,
    ] {
        let node = parse(
            &format!(
                r#"<div><div id="list" {container}><div id="r1"><span id="inner">x</span></div></div></div>"#
            ),
            true,
        );
        let list = &node.children[0];
        assert_eq!(list.key.as_deref(), Some("#list"), "{container}");
        let row = &list.children[0];
        assert_eq!(
            row.key, None,
            "{container}: row must keep no id-derived key"
        );
        assert_eq!(
            row.children[0].key.as_deref(),
            Some("#inner"),
            "{container}"
        );
    }
}

#[test]
fn opt_in_is_idempotent() {
    let mut node = parse(&page(true), true);
    let once = node.clone();
    key_by_id(&mut node);
    assert_eq!(node, once);
}

#[test]
fn duplicate_sibling_ids_fall_back_to_positional_diffing() {
    let old = r#"<ul><li id="d">1</li><li id="d">2</li></ul>"#;
    let new = r#"<ul><li id="d">2</li><li id="d">1</li></ul>"#;
    let (o, n, keyed) = diff_html(old, new, true);
    let (_, _, plain) = diff_html(old, new, false);
    let json = |p: &[Patch]| serde_json::to_string(p).expect("serializes");
    assert_eq!(json(&keyed), json(&plain));
    assert_round_trip(&o, &n, &keyed);
}

#[test]
fn position_encoded_ids_behave_like_positional_diffing() {
    // id="row-{{ forloop.counter }}": inserting at the front shifts every id.
    let old = r#"<ul><li id="row-1">a</li><li id="row-2">b</li></ul>"#;
    let new = r#"<ul><li id="row-1">z</li><li id="row-2">a</li><li id="row-3">b</li></ul>"#;
    let (o, n, keyed) = diff_html(old, new, true);
    let (_, _, plain) = diff_html(old, new, false);
    assert_eq!(kinds(&keyed), kinds(&plain), "{keyed:?} vs {plain:?}");
    assert_round_trip(&o, &n, &keyed);
}

#[test]
fn opt_in_keyed_reorder_round_trips() {
    let old = r#"<div><p id="x">x</p><p id="y">y</p><p id="z">z</p></div>"#;
    let new = r#"<div><p id="z">z</p><p id="x">x</p><p id="y">y</p></div>"#;
    let (o, n, patches) = diff_html(old, new, true);
    assert!(
        kinds(&patches).iter().all(|k| k == "MoveChild"),
        "{patches:?}"
    );
    assert_round_trip(&o, &n, &patches);
}
