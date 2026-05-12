//! Full HTML round-trip stability torture (#1416).
//!
//! ## What this exercises
//!
//! The chain VDOM → `to_html()` → `parse_html()` → diff against the
//! reparsed tree. The invariant is: the structural shape of the tree
//! (tags, attrs, text content, comment markers, nested children
//! shape) survives a full HTML serialize/parse round-trip.
//!
//! ## Why this matters
//!
//! Production uses both halves of this chain:
//!
//! - `to_html()` serializes server-side VDOMs to HTML for full-page
//!   renders, for `InsertSubtree.html` payloads (`dj-if` body
//!   transmission to the client), and for some `Replace` patch
//!   payloads.
//! - `parse_html()` parses incoming HTML for the next render and
//!   constructs a fresh VDOM that the diff engine compares against
//!   the previous one.
//!
//! If the round-trip drifts — e.g. a comment gets dropped, an
//! attribute value gets corrupted, a void-element close-tag breaks the
//! shape — then either:
//!
//! 1. The client receives HTML that doesn't reconstruct to the same
//!    tree the server is mirroring (drift between server's
//!    `last_vdom` and the client DOM, the #1408 class of bug); OR
//! 2. Subsequent diff rounds compare a freshly-parsed tree against
//!    a previous-render tree that no longer matches reality, emitting
//!    patches that target stale handles.
//!
//! ## What's complementary coverage
//!
//! Existing tests cover individual aspects of `to_html()` (raw-text
//! contexts, attribute escaping, SVG attrs) and individual aspects of
//! `parse_html()` (id stamping). This file is the cross-cutting
//! round-trip torture: it asserts the COMPOSITE of the two functions
//! preserves the shape for representative tree variants.
//!
//! Note: dj-ids are reassigned during parse (the counter resets in
//! `parse_html`), so the assertion compares structural shape only —
//! tag, attrs (minus dj-id), text content, nested children shape, and
//! comment markers. Stripping dj-ids is the standard pattern for
//! comparing pre- and post-parse shapes (see the parser's id stamping
//! in `parser.rs`).

use djust_vdom::{parse_html, VNode};

mod common;
use common::{dj_if_close, dj_if_open, elem, elem_with_text, IdGen};

// =============================================================================
// Structural comparison (ignores dj-id assignment differences)
// =============================================================================

/// Return a clone of `node` with all `djust_id`s, `dj-id` attrs,
/// `key`s, and `cached_html` stripped, so two trees can be compared
/// structurally regardless of where in the id-counter sequence they
/// were built.
///
/// `key` is stripped because the in-memory `VNode.key` field is not
/// serialized as an HTML attribute by `to_html()` (the parser stamps
/// `key` from the `dj-key` attribute on the way back, so a tree built
/// directly via `.with_key(...)` without a `dj-key` attr will not have
/// its key survive the round-trip — that's a serialization-shape
/// limitation, not a tree-shape bug).
fn strip_ids(node: &VNode) -> VNode {
    let mut clone = node.clone();
    clone.djust_id = None;
    clone.attrs.remove("dj-id");
    clone.key = None;
    clone.cached_html = None;
    clone.children = clone.children.iter().map(strip_ids).collect();
    clone
}

/// Assert that the round-trip `to_html() → parse_html()` preserves the
/// structural shape of the tree, ignoring dj-id assignment differences.
fn assert_round_trip(scenario: &str, original: &VNode) {
    let html = original.to_html();
    let reparsed = parse_html(&html).expect("parse_html should succeed on serialized output");

    let want = strip_ids(original);
    let got = strip_ids(&reparsed);

    assert_eq!(
        want,
        got,
        "[{}] round-trip drift\n\
         original_html  = {}\n\
         reparsed_html  = {}",
        scenario,
        html,
        reparsed.to_html()
    );
}

// =============================================================================
// Scenarios
// =============================================================================

#[test]
fn round_trip_plain_elements() {
    let c = IdGen::new();
    let tree = elem("div", &c)
        .with_child(elem("span", &c))
        .with_child(elem("p", &c));
    assert_round_trip("plain_elements", &tree);
}

#[test]
fn round_trip_nested_with_text() {
    let c = IdGen::new();
    let tree = elem("div", &c)
        .with_child(elem_with_text("h1", "Hello, world!", &c))
        .with_child(
            elem("section", &c)
                .with_child(elem_with_text("p", "Paragraph one", &c))
                .with_child(elem_with_text("p", "Paragraph two", &c)),
        );
    assert_round_trip("nested_with_text", &tree);
}

#[test]
fn round_trip_dj_if_boundary() {
    let c = IdGen::new();
    let tree = elem("div", &c).with_children(vec![
        elem_with_text("h2", "Header", &c),
        dj_if_open("if-section-A"),
        elem_with_text("div", "branch A body", &c),
        elem_with_text("p", "branch A footer", &c),
        dj_if_close(),
        elem_with_text("footer", "Stable footer", &c),
    ]);
    assert_round_trip("dj_if_boundary", &tree);
}

#[test]
fn round_trip_nested_dj_if_boundaries() {
    let c = IdGen::new();
    let tree = elem("div", &c).with_children(vec![
        dj_if_open("if-outer"),
        elem_with_text("div", "outer-body-pre", &c),
        dj_if_open("if-inner"),
        elem_with_text("p", "inner-body", &c),
        dj_if_close(),
        elem_with_text("div", "outer-body-post", &c),
        dj_if_close(),
    ]);
    assert_round_trip("nested_dj_if_boundaries", &tree);
}

#[test]
fn round_trip_keyed_children() {
    // Use `dj-key` ATTR to mark keys, since the parser stamps
    // `VNode.key` from that attribute. The in-memory `with_key()` field
    // alone is stripped by `to_html()` (see `strip_ids` comment); the
    // production wire format uses `dj-key=...`.
    let c = IdGen::new();
    let mut tree = elem("ul", &c);
    let mut kids = Vec::new();
    for i in 0..5 {
        let li =
            elem_with_text("li", &format!("item-{}", i), &c).with_attr("dj-key", format!("k{}", i));
        kids.push(li);
    }
    tree = tree.with_children(kids);
    assert_round_trip("keyed_children", &tree);
}

#[test]
fn round_trip_dj_update_ignore_subtree() {
    let c = IdGen::new();
    let tree = elem("div", &c).with_children(vec![
        elem("section", &c)
            .with_attr("dj-update", "ignore")
            .with_children(vec![
                elem_with_text("p", "static content one", &c),
                elem_with_text("p", "static content two", &c),
            ]),
        elem_with_text("p", "dynamic content", &c),
    ]);
    assert_round_trip("dj_update_ignore_subtree", &tree);
}

#[test]
fn round_trip_mixed_attrs() {
    let c = IdGen::new();
    let tree = elem("div", &c)
        .with_attr("class", "container")
        .with_attr("data-foo", "bar")
        .with_attr("aria-label", "main region")
        .with_attr("role", "main")
        .with_child(
            elem("a", &c)
                .with_attr("href", "/path?a=1&b=2")
                .with_attr("title", "Link \"with\" quotes")
                .with_child(VNode::text("click me")),
        );
    assert_round_trip("mixed_attrs", &tree);
}

#[test]
fn round_trip_text_with_html_entities() {
    // Adjacent text nodes are coalesced by html5ever into a single
    // text node during parse — that's standard HTML parser behavior.
    // To test entity round-tripping, wrap each text in its own element
    // so they survive as distinct children.
    let c = IdGen::new();
    let tree = elem("div", &c)
        .with_child(elem("span", &c).with_child(VNode::text("a < b & c > d")))
        .with_child(elem("span", &c).with_child(VNode::text("\u{00A0}preserved nbsp")))
        .with_child(elem("span", &c).with_child(VNode::text("quotes \"inside\" text")));
    assert_round_trip("text_with_html_entities", &tree);
}
