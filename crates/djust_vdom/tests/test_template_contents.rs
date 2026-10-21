//! A `<template>`'s parsed children live in its contents fragment, not its
//! child list, so a VDOM built from the child list held an empty template:
//! a template a patch inserted arrived empty, and a change inside an existing
//! template produced no patch at all. The parser reads the contents.

use djust_vdom::diff::{diff_nodes, sync_ids};
use djust_vdom::patch::apply_patches;
use djust_vdom::{parse_html, parse_html_continue, Patch, VNode};

fn diff_pair(old_html: &str, new_html: &str) -> (VNode, VNode, Vec<Patch>) {
    let old = parse_html(old_html).unwrap();
    let mut new = parse_html_continue(new_html).unwrap();
    let patches = diff_nodes(&old, &new, &[]);
    sync_ids(&old, &mut new);
    (old, new, patches)
}

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

fn template_of(root: &VNode) -> &VNode {
    root.children
        .iter()
        .find(|c| c.tag == "template")
        .expect("a template child")
}

#[test]
fn a_template_keeps_its_contents() {
    let root = parse_html(
        r#"<div dj-root><template data-t><li class="o">one</li><li>two</li></template></div>"#,
    )
    .unwrap();
    let tpl = template_of(&root);
    let tags: Vec<&str> = tpl.children.iter().map(|c| c.tag.as_str()).collect();
    assert_eq!(tags, ["li", "li"]);
    assert_eq!(tpl.children[0].children[0].text.as_deref(), Some("one"));
    assert!(root.to_html().contains("<template data-t=\"\""));
    assert!(shape_html(&root)
        .contains(r#"<template data-t=""><li class="o">one</li><li>two</li></template>"#));
}

#[test]
fn an_inserted_template_carries_its_contents() {
    let (_, _, patches) = diff_pair(
        "<div dj-root><p>x</p></div>",
        r#"<div dj-root><p>x</p><template><li>one</li></template></div>"#,
    );
    let inserted = patches
        .iter()
        .find_map(|p| match p {
            Patch::InsertChild { node, .. } if node.tag == "template" => Some(node),
            _ => None,
        })
        .expect("the template is inserted");
    assert_eq!(inserted.children.len(), 1);
    assert_eq!(inserted.children[0].tag, "li");
}

#[test]
fn a_change_inside_a_template_is_patched() {
    let old_html = r#"<div dj-root><template data-state="unloaded"></template></div>"#;
    let new_html =
        r#"<div dj-root><template data-state="ready"><li>a</li><li>b</li></template></div>"#;
    let (mut old, new, patches) = diff_pair(old_html, new_html);
    assert!(
        patches
            .iter()
            .any(|p| matches!(p, Patch::InsertChild { .. })),
        "the options are inserted: {patches:?}"
    );
    apply_patches(&mut old, &patches);
    assert_eq!(shape_html(&old), shape_html(&new));

    let (mut old, new, patches) = diff_pair(
        r#"<div dj-root><template><li>a</li><li>b</li></template></div>"#,
        r#"<div dj-root><template><li>a!</li></template></div>"#,
    );
    assert!(!patches.is_empty());
    apply_patches(&mut old, &patches);
    assert_eq!(shape_html(&old), shape_html(&new));
}

#[test]
fn a_nested_template_keeps_its_contents() {
    let root = parse_html(
        r#"<div dj-root><template><section><template><b>deep</b></template></section></template></div>"#,
    )
    .unwrap();
    let outer = template_of(&root);
    let inner = &outer.children[0].children[0];
    assert_eq!(inner.tag, "template");
    assert_eq!(inner.children[0].tag, "b");
}
