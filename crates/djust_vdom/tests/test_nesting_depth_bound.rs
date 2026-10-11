//! The nesting depth of a parsed VDOM is bounded by `MAX_VDOM_DEPTH`.
//!
//! Every walker over a `VNode` tree (diff, patch serialization, `to_html`,
//! clone, drop) recurses once per level, so the parser bounds the depth of the
//! trees it returns. These pin the boundary (limit - 1, limit, limit + 1),
//! what is kept below the limit, that the result survives a round trip, and
//! that the other walkers cope with a tree of exactly the limit.

use djust_vdom::diff::diff_nodes;
use djust_vdom::{
    parse_html, parse_html_continue, parse_html_fragment, to_base62, VNode, MAX_VDOM_DEPTH,
};

/// Run `f` on a thread with a `kib` KiB stack. 2 MiB is the smallest stack a
/// walker normally runs on (tokio's worker default, and a Rust test thread);
/// the root search is run on less, because it walks the raw tree, which is as
/// deep as the input and which 10,000 levels would not fit if it recursed.
/// (A debug build uses several times the stack of a release build; the
/// release-build stack bound is pinned in `python/tests/test_vdom_nesting_bound.py`.)
fn on_small_stack(kib: usize, f: impl FnOnce() + Send + 'static) {
    std::thread::Builder::new()
        .stack_size(kib * 1024)
        .spawn(f)
        .unwrap()
        .join()
        .expect("must fit a small stack");
}

fn nested(tag: &str, depth: usize, inner: &str) -> String {
    format!(
        "<{tag}>{}{inner}{}",
        format!("<{tag}>").repeat(depth.saturating_sub(1)),
        format!("</{tag}>").repeat(depth),
    )
}

/// `<div>t0<div>t1 ... <div>t(n-1)</div>u(n-1) ... </div>u1</div>u0`, wrapped
/// in one element so the text around the outermost div has a parent too.
fn nested_with_text(depth: usize) -> String {
    let mut html = String::from("<section>");
    for i in 0..depth {
        html.push_str(&format!("<div>t{i}"));
    }
    for i in (0..depth).rev() {
        html.push_str(&format!("</div>u{i}"));
    }
    html.push_str("</section>");
    html
}

/// Walk down the first element child `levels` times.
fn descend(node: &VNode, levels: usize) -> &VNode {
    let mut n = node;
    for _ in 0..levels {
        n = n
            .children
            .iter()
            .find(|c| !c.is_text() && !c.is_comment())
            .expect("element child");
    }
    n
}

fn text_of(node: &VNode) -> String {
    node.children
        .iter()
        .filter(|c| c.is_text())
        .filter_map(|c| c.text.clone())
        .collect()
}

#[test]
fn parsed_depth_is_capped_at_the_limit() {
    on_small_stack(2048, || {
        for depth in [
            1,
            MAX_VDOM_DEPTH - 1,
            MAX_VDOM_DEPTH,
            MAX_VDOM_DEPTH + 1,
            MAX_VDOM_DEPTH + 2,
            5 * MAX_VDOM_DEPTH,
            10_000,
        ] {
            let root = parse_html(&nested("div", depth, "x")).unwrap();
            assert_eq!(
                root.element_depth(),
                depth.min(MAX_VDOM_DEPTH),
                "nesting {depth} must keep min({depth}, {MAX_VDOM_DEPTH}) levels"
            );
        }
    });
}

#[test]
fn trees_within_the_limit_are_untouched() {
    for depth in [1, 2, 10, MAX_VDOM_DEPTH - 1, MAX_VDOM_DEPTH] {
        let root = parse_html(&nested("div", depth, "x")).unwrap();
        let mut expected = String::new();
        for i in 0..depth {
            expected.push_str(&format!("<div dj-id=\"{}\">", to_base62(i as u64)));
        }
        expected.push('x');
        expected.push_str(&"</div>".repeat(depth));
        assert_eq!(root.to_html(), expected, "depth {depth}");
    }
}

#[test]
fn the_text_below_the_limit_is_kept_in_document_order() {
    let depth = MAX_VDOM_DEPTH + 40;
    let root = parse_html(&nested_with_text(depth)).unwrap();
    // <section> is level 1, so the limit falls on the (MAX - 1)th div.
    let boundary = descend(&root, MAX_VDOM_DEPTH - 1);
    assert_eq!(boundary.tag, "div");
    assert_eq!(boundary.element_depth(), 1, "nothing nests under the limit");
    assert_eq!(boundary.children.len(), 1, "the text is one node");

    let first = MAX_VDOM_DEPTH - 2; // index of the boundary div
    let mut expected = String::new();
    for i in first..depth {
        expected.push_str(&format!("t{i}"));
    }
    // `u{i}` follows the close of div i, so it belongs to div i - 1.
    for i in ((first + 1)..depth).rev() {
        expected.push_str(&format!("u{i}"));
    }
    assert_eq!(text_of(boundary), expected);
    // Above the limit the structure is the page's own.
    let above = descend(&root, MAX_VDOM_DEPTH - 2);
    assert_eq!(above.tag, "div");
    assert!(above.children.iter().any(|c| c.tag == "div"));
}

#[test]
fn the_element_at_the_limit_keeps_its_attributes() {
    let mut html = String::new();
    for i in 0..MAX_VDOM_DEPTH + 5 {
        if i == MAX_VDOM_DEPTH - 1 {
            html.push_str("<div class=\"edge\" dj-click=\"go\" data-key=\"k\">");
        } else {
            html.push_str("<div>");
        }
    }
    html.push_str("<b class=\"hidden\">deep</b>");
    html.push_str(&"</div>".repeat(MAX_VDOM_DEPTH + 5));
    let root = parse_html(&html).unwrap();
    let edge = descend(&root, MAX_VDOM_DEPTH - 1);
    assert_eq!(edge.attrs.get("class").map(String::as_str), Some("edge"));
    assert_eq!(edge.attrs.get("dj-click").map(String::as_str), Some("go"));
    assert_eq!(edge.key.as_deref(), Some("k"));
    assert_eq!(text_of(edge), "deep");
}

#[test]
fn an_empty_or_blank_subtree_below_the_limit_leaves_no_text() {
    for filler in ["", "  \n "] {
        let root = parse_html(&nested("div", MAX_VDOM_DEPTH + 10, filler)).unwrap();
        let edge = descend(&root, MAX_VDOM_DEPTH - 1);
        assert!(edge.children.is_empty(), "filler {filler:?}");
    }
}

#[test]
fn preformatted_text_below_the_limit_keeps_its_whitespace() {
    let mut html = String::new();
    for _ in 0..MAX_VDOM_DEPTH - 1 {
        html.push_str("<div>");
    }
    html.push_str("<pre><b>  a  </b>\n  <i>b</i></pre>");
    html.push_str(&"</div>".repeat(MAX_VDOM_DEPTH - 1));
    let root = parse_html(&html).unwrap();
    let pre = descend(&root, MAX_VDOM_DEPTH - 1);
    assert_eq!(pre.tag, "pre");
    assert_eq!(text_of(pre), "  a  \n  b");
}

#[test]
fn a_raw_text_named_element_at_the_limit_keeps_no_text_from_its_children() {
    // In svg/math these are foreign elements with element children. Their
    // descendants' decoded text must not become the element's own text.
    let payload = "<a>&lt;/style&gt;&lt;/script&gt;&lt;img src=x onerror=alert(1)&gt;</a>";
    for (open, tag, close) in [
        ("<svg>", "style", "</svg>"),
        ("<svg>", "script", "</svg>"),
        ("<math>", "style", "</math>"),
        ("<svg>", "noscript", "</svg>"),
        ("<svg>", "xmp", "</svg>"),
        ("<svg>", "iframe", "</svg>"),
    ] {
        // `MAX - 2` divs, then the svg/math, puts `tag` at depth MAX.
        let html = format!(
            "{}{open}<{tag}>{payload}</{tag}>{close}{}",
            "<div>".repeat(MAX_VDOM_DEPTH - 2),
            "</div>".repeat(MAX_VDOM_DEPTH - 2)
        );
        let root = parse_html(&html).unwrap();
        let edge = descend(&root, MAX_VDOM_DEPTH - 1);
        assert_eq!(edge.tag, tag);
        assert!(edge.children.is_empty(), "{tag}: {:?}", edge.children);
        let out = root.to_html();
        assert!(!out.contains("<img"), "{tag}: {out}");
        let again = parse_html(&out).unwrap();
        assert_eq!(again.to_html(), out, "{tag}");
    }
    // An element with text only (no child elements) keeps it, as before.
    let html = format!(
        "{}<svg><style>.a {{ fill: red }}</style></svg>{}",
        "<div>".repeat(MAX_VDOM_DEPTH - 2),
        "</div>".repeat(MAX_VDOM_DEPTH - 2)
    );
    let root = parse_html(&html).unwrap();
    assert_eq!(
        text_of(descend(&root, MAX_VDOM_DEPTH - 1)),
        ".a { fill: red }"
    );
}

#[test]
fn a_flattened_tree_survives_the_round_trip_through_html() {
    for depth in [MAX_VDOM_DEPTH + 1, MAX_VDOM_DEPTH + 50] {
        for html in [nested("span", depth, "x"), nested_with_text(depth)] {
            let first = parse_html(&html).unwrap();
            let second = parse_html(&first.to_html()).unwrap();
            assert_eq!(second.to_html(), first.to_html());
            assert_eq!(second.element_depth(), first.element_depth());
        }
    }
}

#[test]
fn fragments_are_bounded_too() {
    let html = nested("div", 20_000, "x");
    let roots = parse_html_fragment(&html, "body").unwrap();
    assert_eq!(roots.len(), 1);
    assert_eq!(roots[0].element_depth(), MAX_VDOM_DEPTH);
}

#[test]
fn the_root_search_does_not_recurse_through_the_raw_tree() {
    // A `dj-root` under 10,000 wrappers is still the root, found without
    // walking the raw tree recursively.
    on_small_stack(512, || {
        let html = format!(
            "{}<section dj-root><p>hi</p></section>{}",
            "<div>".repeat(10_000),
            "</div>".repeat(10_000)
        );
        let root = parse_html(&html).unwrap();
        assert_eq!(root.tag, "section");
        assert_eq!(root.element_depth(), 2);
    });
}

#[test]
fn a_root_below_deep_wrappers_keeps_the_full_depth_budget() {
    // The limit counts from the root the VDOM starts at, not from <body>.
    let html = format!(
        "{}<section dj-root>{}x{}</section>{}",
        "<div>".repeat(1000),
        "<i>".repeat(MAX_VDOM_DEPTH - 1),
        "</i>".repeat(MAX_VDOM_DEPTH - 1),
        "</div>".repeat(1000)
    );
    let root = parse_html(&html).unwrap();
    assert_eq!(root.tag, "section");
    assert_eq!(root.element_depth(), MAX_VDOM_DEPTH);
}

#[test]
fn many_deep_branches_are_each_bounded() {
    let branch = nested("div", 3000, "x");
    let html = format!("<div>{}</div>", branch.repeat(30));
    let root = parse_html(&html).unwrap();
    assert_eq!(root.children.len(), 30);
    assert_eq!(root.element_depth(), MAX_VDOM_DEPTH);
}

#[test]
fn the_other_walkers_cope_with_a_tree_of_exactly_the_limit() {
    let old_html = nested("span", 10_000, "before");
    let new_html = nested("span", 10_000, "after");
    let old = parse_html(&old_html).unwrap();
    let new = parse_html_continue(&new_html).unwrap();
    assert_eq!(old.element_depth(), MAX_VDOM_DEPTH);

    // diff, patch serialization (JSON and MessagePack), to_html, clone, drop.
    let patches = diff_nodes(&old, &new, &[]);
    assert!(!patches.is_empty());
    assert!(serde_json::to_string(&patches).unwrap().contains("after"));
    assert!(!rmp_serde::to_vec_named(&patches).unwrap().is_empty());
    assert!(new.to_html().contains("after"));
    let copy = old.clone();
    assert_eq!(copy.to_html(), old.to_html());
    drop(copy);

    // A tree that appears for the first time is serialized whole.
    let before = parse_html("<div><p>x</p></div>").unwrap();
    let after = parse_html_continue(&format!("<div>{new_html}</div>")).unwrap();
    let inserted = diff_nodes(&before, &after, &[]);
    assert!(serde_json::to_string(&inserted).unwrap().contains("after"));
}

#[test]
fn element_depth_counts_elements_only() {
    assert_eq!(VNode::text("x").element_depth(), 0);
    assert_eq!(VNode::element("div").element_depth(), 1);
    let tree = VNode::element("div")
        .with_child(VNode::text("a"))
        .with_child(VNode::element("p").with_child(VNode::element("b")))
        .with_child(VNode::element("i"));
    assert_eq!(tree.element_depth(), 3);
}
