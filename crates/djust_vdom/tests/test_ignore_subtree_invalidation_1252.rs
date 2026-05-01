//! Regression tests for issue #1252: `splice_ignore_subtrees` must clear
//! `cached_html` on the spliced node so a stale serialization does not
//! survive a conditional re-render that wraps an ignored subtree.
//!
//! The bug: after `splice_ignore_subtrees`, the new tree carried over the
//! OLD `cached_html`. If the surrounding markup changed (e.g., a wrapping
//! conditional toggled), `to_html()` returned the cached old HTML — even
//! though the ignored subtree's *contents* had themselves been edited
//! upstream and never re-cached.
//!
//! Fix: after splicing children/IDs, set `new.cached_html = None` so
//! `cache_ignore_subtree_html` (or `to_html()` itself) recomputes on the
//! next render.

use djust_vdom::{cache_ignore_subtree_html, parse_html, splice_ignore_subtrees, VNode};

/// After `splice_ignore_subtrees`, the new node's `cached_html` must be
/// `None` (not copied from old). This is the load-bearing assertion: the
/// audit's documented failure mode is "stale cache survives".
#[test]
fn test_splice_clears_cached_html_on_ignored_node() {
    // Build the OLD tree with an ignored subtree that has been cached.
    let old_html = r#"<div><div dj-update="ignore"><span>old</span></div></div>"#;
    let mut old = parse_html(old_html).unwrap();
    cache_ignore_subtree_html(&mut old);

    // Sanity-check: the old tree's ignored child has a cached HTML string.
    let old_ignored = &old.children[0];
    assert_eq!(
        old_ignored.attrs.get("dj-update").map(String::as_str),
        Some("ignore")
    );
    assert!(
        old_ignored.cached_html.is_some(),
        "precondition: cache_ignore_subtree_html should have populated cached_html"
    );

    // Build a NEW tree (same shape) — fresh parse, no cache yet.
    let new_html = r#"<div><div dj-update="ignore"><span>doesn't matter</span></div></div>"#;
    let mut new = parse_html(new_html).unwrap();
    assert!(
        new.children[0].cached_html.is_none(),
        "precondition: freshly-parsed tree has no cache"
    );

    // Splice. The new node's children + djust_id should come from old,
    // BUT cached_html must be cleared so it gets recomputed.
    splice_ignore_subtrees(&old, &mut new);

    let new_ignored = &new.children[0];
    assert_eq!(
        new_ignored.attrs.get("dj-update").map(String::as_str),
        Some("ignore")
    );
    assert_eq!(
        new_ignored.cached_html, None,
        "REGRESSION #1252: splice_ignore_subtrees must clear cached_html, \
         not copy stale cache from old. Otherwise to_html() returns the \
         OLD serialization even after the surrounding markup changes."
    );
}

/// End-to-end scenario: the cache invalidation matters when `to_html()`
/// runs on a tree where the OLD ignored subtree had a stale cache that
/// no longer reflects current content.
#[test]
fn test_splice_recomputes_html_after_invalidation() {
    // Build OLD tree. Cache it. Then mutate the cached node's text via the
    // cached HTML directly (simulating a stale cache).
    let old_html = r#"<div><div dj-update="ignore"><span>STALE</span></div></div>"#;
    let mut old = parse_html(old_html).unwrap();
    cache_ignore_subtree_html(&mut old);

    // Forge a stale cache: pretend the cache holds out-of-date content.
    // (This simulates the bug surface — the cache no longer matches the
    // children, but splicing copies it forward anyway.)
    old.children[0].cached_html =
        Some(r#"<div dj-update="ignore"><span>VERY-STALE-CACHE</span></div>"#.to_string());

    // Splice into a fresh tree.
    let new_html = r#"<div><div dj-update="ignore"><span>STALE</span></div></div>"#;
    let mut new = parse_html(new_html).unwrap();
    splice_ignore_subtrees(&old, &mut new);

    // After the fix: cached_html on the spliced node is None, so to_html()
    // serializes the actual children — which match `old.children` (preserved
    // by splice for ignore semantics) and DO NOT contain the stale string.
    let rendered = new.to_html();
    assert!(
        !rendered.contains("VERY-STALE-CACHE"),
        "REGRESSION #1252: stale cache leaked into render output. Got: {}",
        rendered
    );
}

/// Building block: when there is no `dj-update="ignore"`, behavior is
/// unchanged — recursion descends and no cache is touched.
#[test]
fn test_splice_no_ignore_does_not_touch_cache() {
    let old_html = r#"<div><span>hello</span></div>"#;
    let mut old = parse_html(old_html).unwrap();
    // Synthetic cache on a non-ignored node — should NOT be cleared by splice
    // since splice only touches the matched ignore-marked node.
    old.cached_html = Some("synthetic".to_string());

    let new_html = r#"<div><span>hello</span></div>"#;
    let mut new = parse_html(new_html).unwrap();

    splice_ignore_subtrees(&old, &mut new);
    // The new root has no dj-update attr, so cache stays None.
    assert!(new.cached_html.is_none());
}

/// Helper: construct a small VDOM with an ignored subtree using the
/// builder API so the test does not depend on parser quirks.
#[test]
fn test_splice_clears_cached_html_via_builder() {
    let mut old = VNode::element("div").with_child(
        VNode::element("section")
            .with_attr("dj-update", "ignore")
            .with_child(VNode::text("inner")),
    );
    // Force a cached_html on the ignored node, simulating prior render.
    old.children[0].cached_html = Some("CACHED-OLD".to_string());

    let mut new = VNode::element("div").with_child(
        VNode::element("section")
            .with_attr("dj-update", "ignore")
            .with_child(VNode::text("inner-new")),
    );
    assert!(new.children[0].cached_html.is_none());

    splice_ignore_subtrees(&old, &mut new);

    assert_eq!(
        new.children[0].cached_html, None,
        "REGRESSION #1252: cached_html must be None after splice"
    );
}
