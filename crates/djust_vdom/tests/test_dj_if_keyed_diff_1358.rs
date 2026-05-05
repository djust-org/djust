//! Regression tests for #1358: keyed VDOM diff for `{% if %}` conditional
//! subtrees (Capability of v0.9.4-1; re-open of #256 Option A).
//!
//! ## Failure mode (pre-Iter 3)
//!
//! When a `{% if %}` block toggled between branches and the branches differ
//! in element shape, the position-based VDOM diff would emit Replace /
//! Insert / Remove patches whose `path` indices reflected the OLD tree's
//! sibling positions. Once the conditional flipped, sibling positions in the
//! NEW tree shifted, and the patches mis-targeted siblings → the client
//! logged "patch failed: node not found at path=..." → recovery HTML →
//! page reload. Reproduced in production at NYC Claims tab-switch
//! (cited in the #1358 issue body, ~17.5% error rate).
//!
//! ## Fix (Iter 3)
//!
//! The Rust template renderer (Iter 1, PR #1363) wraps element-bearing
//! `{% if %}` blocks in `<!--dj-if id="if-<8hex>-N"-->...<!--/dj-if-->`
//! marker pairs. The Rust VDOM parser (Iter 1) preserves these as comment
//! nodes. Iter 3 (this PR) teaches the differ to recognize the markers as
//! KEYED units: pair-match by id rather than by sibling position.
//!
//! - id only in OLD → emit `RemoveSubtree { id }`
//! - id only in NEW → emit `InsertSubtree { id, html, ... }`
//! - id in BOTH → recurse into the inner body (markers stay static)
//! - non-boundary siblings → diffed by RELATIVE position among non-boundary
//!   siblings (boundary shifts no longer cascade).
//!
//! ## Test discipline (Action #1196)
//!
//! Each case in this file must FAIL on `main` (pre-Iter-3): the differ at
//! head doesn't recognize id-bearing dj-if markers as keyed boundaries,
//! so it never emits `Patch::RemoveSubtree` / `Patch::InsertSubtree` (those
//! variants don't exist on main). Tests assert on the new patch variants
//! via `matches!` — pre-Iter-3 those assertions would fail to compile (and
//! after the variant existed but pre-fix, would assert false because the
//! differ would emit Replace/Insert/Remove instead).

use djust_vdom::diff::diff_nodes;
use djust_vdom::{Patch, VNode};

/// Helper: build a `<!--dj-if id="X"-->` open-marker comment node.
fn dj_if_open(id: &str) -> VNode {
    VNode {
        tag: "#comment".to_string(),
        attrs: Default::default(),
        children: Vec::new(),
        text: Some(format!("dj-if id=\"{}\"", id)),
        key: None,
        djust_id: None,
        cached_html: None,
    }
}

/// Helper: build a `<!--/dj-if-->` close-marker comment node.
fn dj_if_close() -> VNode {
    VNode {
        tag: "#comment".to_string(),
        attrs: Default::default(),
        children: Vec::new(),
        text: Some("/dj-if".to_string()),
        key: None,
        djust_id: None,
        cached_html: None,
    }
}

/// Count patches matching a predicate.
fn count<F: Fn(&Patch) -> bool>(patches: &[Patch], pred: F) -> usize {
    patches.iter().filter(|p| pred(p)).count()
}

// =============================================================================
// Case 1: #1358 reproducer — tab-switch with different conditional branches
// =============================================================================

/// End-to-end #1358 reproducer:
///
/// Tab-switch via `dj-patch` on a view with `{% if active_tab == "overview" %}
/// ... {% elif active_tab == "details" %} ... {% endif %}`. Iter 1 emits a
/// distinct boundary id per branch (the marker_id is assigned in document
/// order at parse time, and the renderer emits the id of the branch whose
/// condition fired).
///
/// Pre-fix: the differ would emit Replace / SetAttr patches whose paths
/// reflected the OLD tree, mis-targeting siblings after the flip.
///
/// Post-fix: differ emits exactly one RemoveSubtree(if-X-0) and one
/// InsertSubtree(if-X-1) — the marker pair is replaced atomically, the
/// client locates the old marker pair by id (NOT by position) and
/// removes the bracketed range, then parses + inserts the new marker
/// pair's HTML.
#[test]
fn test_1358_reproducer_tab_switch_different_branches() {
    let old = VNode::element("div")
        .with_djust_id("root")
        .with_children(vec![
            dj_if_open("if-deadbeef-0"),
            VNode::element("div")
                .with_djust_id("tab-a-content")
                .with_attr("class", "tab-overview")
                .with_child(VNode::text("Overview content")),
            dj_if_close(),
        ]);

    let new = VNode::element("div")
        .with_djust_id("root")
        .with_children(vec![
            dj_if_open("if-deadbeef-1"),
            VNode::element("div")
                .with_djust_id("tab-b-content")
                .with_attr("class", "tab-details")
                .with_child(VNode::text("Details content")),
            dj_if_close(),
        ]);

    let patches = diff_nodes(&old, &new, &[]);

    // Pre-Iter-3 the differ would have emitted Replace patches and the
    // path-shift cascade would have caused mis-targeting on the client.
    // Post-Iter-3 we expect exactly one RemoveSubtree + one InsertSubtree.
    assert_eq!(
        count(&patches, |p| matches!(p, Patch::RemoveSubtree { .. })),
        1,
        "Expected 1 RemoveSubtree, got patches: {:?}",
        patches
    );
    assert_eq!(
        count(&patches, |p| matches!(p, Patch::InsertSubtree { .. })),
        1,
        "Expected 1 InsertSubtree, got patches: {:?}",
        patches
    );

    // Verify the RemoveSubtree carries the OLD boundary id.
    let remove = patches
        .iter()
        .find(|p| matches!(p, Patch::RemoveSubtree { .. }))
        .unwrap();
    if let Patch::RemoveSubtree { id } = remove {
        assert_eq!(id, "if-deadbeef-0", "RemoveSubtree should target old id");
    }

    // Verify the InsertSubtree carries the NEW boundary id + full marker pair.
    let insert = patches
        .iter()
        .find(|p| matches!(p, Patch::InsertSubtree { .. }))
        .unwrap();
    if let Patch::InsertSubtree {
        id,
        html,
        path,
        index,
        d,
    } = insert
    {
        assert_eq!(id, "if-deadbeef-1", "InsertSubtree should target new id");
        assert!(
            html.starts_with("<!--dj-if id=\"if-deadbeef-1\"-->"),
            "html should start with open marker, got: {}",
            html
        );
        assert!(
            html.ends_with("<!--/dj-if-->"),
            "html should end with close marker, got: {}",
            html
        );
        assert!(
            html.contains("tab-details"),
            "html should contain new branch content, got: {}",
            html
        );
        // path is parent-path = []; index is open-marker absolute index in NEW.
        assert_eq!(path, &Vec::<usize>::new(), "path should be parent path");
        assert_eq!(*index, 0, "index should be the open marker's position");
        assert_eq!(
            d,
            &Some("root".to_string()),
            "d should be parent's djust_id"
        );
    }
}

// =============================================================================
// Case 2: Conditional flips OFF (boundary disappears)
// =============================================================================

#[test]
fn test_conditional_flip_remove_only() {
    // OLD has a boundary; NEW does not. Differ emits RemoveSubtree only.
    let old = VNode::element("div")
        .with_djust_id("root")
        .with_children(vec![
            dj_if_open("if-X-0"),
            VNode::element("p")
                .with_djust_id("p1")
                .with_child(VNode::text("conditional")),
            dj_if_close(),
        ]);

    let new = VNode::element("div").with_djust_id("root");

    let patches = diff_nodes(&old, &new, &[]);

    assert_eq!(
        count(&patches, |p| matches!(p, Patch::RemoveSubtree { .. })),
        1,
        "Expected 1 RemoveSubtree, got: {:?}",
        patches
    );
    assert_eq!(
        count(&patches, |p| matches!(p, Patch::InsertSubtree { .. })),
        0,
        "Expected 0 InsertSubtree, got: {:?}",
        patches
    );

    let remove = patches
        .iter()
        .find(|p| matches!(p, Patch::RemoveSubtree { .. }))
        .unwrap();
    if let Patch::RemoveSubtree { id } = remove {
        assert_eq!(id, "if-X-0");
    }
}

// =============================================================================
// Case 3: Conditional flips ON (boundary appears)
// =============================================================================

#[test]
fn test_conditional_flip_insert_only() {
    // NEW has a boundary; OLD does not. Differ emits InsertSubtree only.
    let old = VNode::element("div").with_djust_id("root");

    let new = VNode::element("div")
        .with_djust_id("root")
        .with_children(vec![
            dj_if_open("if-Y-0"),
            VNode::element("p")
                .with_djust_id("p1")
                .with_child(VNode::text("now conditional")),
            dj_if_close(),
        ]);

    let patches = diff_nodes(&old, &new, &[]);

    assert_eq!(
        count(&patches, |p| matches!(p, Patch::InsertSubtree { .. })),
        1,
        "Expected 1 InsertSubtree, got: {:?}",
        patches
    );
    assert_eq!(
        count(&patches, |p| matches!(p, Patch::RemoveSubtree { .. })),
        0,
        "Expected 0 RemoveSubtree, got: {:?}",
        patches
    );

    let insert = patches
        .iter()
        .find(|p| matches!(p, Patch::InsertSubtree { .. }))
        .unwrap();
    if let Patch::InsertSubtree {
        id, html, index, ..
    } = insert
    {
        assert_eq!(id, "if-Y-0");
        assert!(html.contains("if-Y-0"));
        assert!(html.contains("now conditional"));
        assert_eq!(*index, 0);
    }
}

// =============================================================================
// Case 4: Same id, inner content changed → recurse, NOT subtree replace
// =============================================================================

#[test]
fn test_matched_boundary_inner_text_change_recurses() {
    // Boundary id="if-Z-0" appears in BOTH trees; only the inner text changed.
    // Differ should NOT emit Remove/Insert subtree — it should recurse and
    // emit a SetText patch for the inner text node.
    let old = VNode::element("div")
        .with_djust_id("root")
        .with_children(vec![
            dj_if_open("if-Z-0"),
            VNode::element("p")
                .with_djust_id("inner-p")
                .with_child(VNode::text("old text")),
            dj_if_close(),
        ]);

    let new = VNode::element("div")
        .with_djust_id("root")
        .with_children(vec![
            dj_if_open("if-Z-0"),
            VNode::element("p")
                .with_djust_id("inner-p")
                .with_child(VNode::text("new text")),
            dj_if_close(),
        ]);

    let patches = diff_nodes(&old, &new, &[]);

    assert_eq!(
        count(&patches, |p| matches!(p, Patch::RemoveSubtree { .. })),
        0,
        "Same-id boundary must not emit RemoveSubtree, got: {:?}",
        patches
    );
    assert_eq!(
        count(&patches, |p| matches!(p, Patch::InsertSubtree { .. })),
        0,
        "Same-id boundary must not emit InsertSubtree, got: {:?}",
        patches
    );

    // Should emit a SetText patch for the inner text node.
    assert!(
        patches
            .iter()
            .any(|p| matches!(p, Patch::SetText { text, .. } if text == "new text")),
        "Expected SetText patch with 'new text', got: {:?}",
        patches
    );
}

// =============================================================================
// Case 5: Same id, identical inner → no patches
// =============================================================================

#[test]
fn test_matched_boundary_identical_inner_no_patches() {
    let inner = VNode::element("p")
        .with_djust_id("inner-p")
        .with_child(VNode::text("static"));

    let old = VNode::element("div")
        .with_djust_id("root")
        .with_children(vec![dj_if_open("if-W-0"), inner.clone(), dj_if_close()]);

    let new = VNode::element("div")
        .with_djust_id("root")
        .with_children(vec![dj_if_open("if-W-0"), inner, dj_if_close()]);

    let patches = diff_nodes(&old, &new, &[]);

    assert!(
        patches.is_empty(),
        "Identical trees with matched boundaries should produce 0 patches, got: {:?}",
        patches
    );
}

// =============================================================================
// Case 6: Nested boundaries — inner flips, outer stays
// =============================================================================

#[test]
fn test_nested_boundaries_inner_flip_only() {
    // Outer boundary id="outer-0" wraps inner boundary id="inner-0".
    // Inner flips (inner-0 → inner-1); outer stays.
    // Expected: RemoveSubtree(inner-0) + InsertSubtree(inner-1), no patches
    // touching outer-0.
    let old = VNode::element("div")
        .with_djust_id("root")
        .with_children(vec![
            dj_if_open("outer-0"),
            VNode::element("section")
                .with_djust_id("sec")
                .with_children(vec![
                    dj_if_open("inner-0"),
                    VNode::element("span")
                        .with_djust_id("inner-content")
                        .with_child(VNode::text("inner A")),
                    dj_if_close(),
                ]),
            dj_if_close(),
        ]);

    let new = VNode::element("div")
        .with_djust_id("root")
        .with_children(vec![
            dj_if_open("outer-0"),
            VNode::element("section")
                .with_djust_id("sec")
                .with_children(vec![
                    dj_if_open("inner-1"),
                    VNode::element("span")
                        .with_djust_id("inner-content-b")
                        .with_child(VNode::text("inner B")),
                    dj_if_close(),
                ]),
            dj_if_close(),
        ]);

    let patches = diff_nodes(&old, &new, &[]);

    // Exactly one RemoveSubtree + one InsertSubtree, both targeting INNER ids.
    assert_eq!(
        count(&patches, |p| matches!(p, Patch::RemoveSubtree { .. })),
        1,
        "Expected 1 RemoveSubtree, got: {:?}",
        patches
    );
    assert_eq!(
        count(&patches, |p| matches!(p, Patch::InsertSubtree { .. })),
        1,
        "Expected 1 InsertSubtree, got: {:?}",
        patches
    );

    let remove = patches
        .iter()
        .find_map(|p| match p {
            Patch::RemoveSubtree { id } => Some(id),
            _ => None,
        })
        .unwrap();
    assert_eq!(remove, "inner-0", "Should target inner boundary id");

    let insert_id = patches
        .iter()
        .find_map(|p| match p {
            Patch::InsertSubtree { id, .. } => Some(id),
            _ => None,
        })
        .unwrap();
    assert_eq!(insert_id, "inner-1", "Should insert inner-1 boundary");

    // No patches mentioning outer-0 at the subtree level.
    let outer_subtree_patches = patches
        .iter()
        .filter(|p| {
            matches!(p,
                Patch::RemoveSubtree { id } if id == "outer-0"
            ) || matches!(p,
                Patch::InsertSubtree { id, .. } if id == "outer-0"
            )
        })
        .count();
    assert_eq!(
        outer_subtree_patches, 0,
        "Outer boundary should not be subtree-replaced"
    );
}

// =============================================================================
// Case 7: Sibling-shift regression — patches BEFORE/AFTER a flipping
// conditional aren't disrupted (the original #1358 failure mode).
// =============================================================================

#[test]
fn test_sibling_shift_does_not_cascade() {
    // Layout: <div>
    //   <header>...</header>          ← static sibling BEFORE conditional
    //   <!--dj-if id="X"-->...<!--/dj-if-->
    //   <footer>...</footer>          ← static sibling AFTER conditional
    // </div>
    //
    // The conditional flips id (X-0 → X-1), changing the inner content
    // shape AND boundary count count (in either case the boundaries
    // are both length-3 marker-content-marker — but when content shapes
    // differ, position-based diff cascades into mis-targeted patches on
    // the static siblings).
    //
    // With the keyed diff, the boundary's id normalizes positions so
    // header/footer SetText patches (if any) target their actual
    // siblings — not phantom positions.
    let old = VNode::element("div")
        .with_djust_id("root")
        .with_children(vec![
            VNode::element("header")
                .with_djust_id("hdr")
                .with_child(VNode::text("Header v1")),
            dj_if_open("if-X-0"),
            VNode::element("p")
                .with_djust_id("inner-old")
                .with_child(VNode::text("alpha")),
            dj_if_close(),
            VNode::element("footer")
                .with_djust_id("ftr")
                .with_child(VNode::text("Footer v1")),
        ]);

    let new = VNode::element("div")
        .with_djust_id("root")
        .with_children(vec![
            VNode::element("header")
                .with_djust_id("hdr")
                .with_child(VNode::text("Header v2")),
            dj_if_open("if-X-1"),
            VNode::element("section") // DIFFERENT tag in the conditional
                .with_djust_id("inner-new")
                .with_children(vec![VNode::element("h1")
                    .with_djust_id("h1")
                    .with_child(VNode::text("beta"))]),
            dj_if_close(),
            VNode::element("footer")
                .with_djust_id("ftr")
                .with_child(VNode::text("Footer v2")),
        ]);

    let patches = diff_nodes(&old, &new, &[]);

    // Boundary should subtree-flip.
    assert_eq!(
        count(
            &patches,
            |p| matches!(p, Patch::RemoveSubtree { id } if id == "if-X-0")
        ),
        1,
        "Expected RemoveSubtree(if-X-0), got: {:?}",
        patches
    );
    assert_eq!(
        count(
            &patches,
            |p| matches!(p, Patch::InsertSubtree { id, .. } if id == "if-X-1")
        ),
        1,
        "Expected InsertSubtree(if-X-1), got: {:?}",
        patches
    );

    // CRITICAL: header + footer SetText patches must target the right text
    // nodes via paths that point at the NEW tree's actual positions —
    // even though the boundary content shape differs (3 markers vs 5
    // elements would be the position-cascading scenario).
    //
    // Production text nodes don't carry djust_id (only elements do), so
    // SetText patches use `d: None` and rely on path-based resolution.
    // Path = [parent_child_idx, text_child_idx_within_parent].
    //
    // In the NEW tree:
    //   children[0] = <header>, header's children[0] = text "Header v2"
    //     → path = [0, 0]
    //   children[1] = open marker, [2] = <section>, [3] = close marker
    //   children[4] = <footer>, footer's children[0] = text "Footer v2"
    //     → path = [4, 0]
    //
    // Pre-Iter-3, the boundary's content shape difference would have
    // caused mis-aligned position pairing for the footer (and the
    // existing legacy-placeholder special-case at `diff_nodes:165-241`
    // would have emitted the WRONG patches because the boundary spans
    // 3 children, not 1).
    let header_text_patch = patches.iter().find(|p| {
        matches!(p,
            Patch::SetText { path, text, .. }
            if path.as_slice() == [0, 0] && text == "Header v2"
        )
    });
    assert!(
        header_text_patch.is_some(),
        "Header SetText must target path=[0, 0] (NEW tree's header text), \
         got patches: {:?}",
        patches
    );

    let footer_text_patch = patches.iter().find(|p| {
        matches!(p,
            Patch::SetText { path, text, .. }
            if path.as_slice() == [4, 0] && text == "Footer v2"
        )
    });
    assert!(
        footer_text_patch.is_some(),
        "Footer SetText must target path=[4, 0] (NEW tree's footer text, \
         after boundary), got patches: {:?}",
        patches
    );
}

// =============================================================================
// Case 8: Empty boundary — no patches when both empty + same id
// =============================================================================

#[test]
fn test_empty_boundary_same_id_no_patches() {
    let old = VNode::element("div")
        .with_djust_id("root")
        .with_children(vec![dj_if_open("if-empty-0"), dj_if_close()]);

    let new = VNode::element("div")
        .with_djust_id("root")
        .with_children(vec![dj_if_open("if-empty-0"), dj_if_close()]);

    let patches = diff_nodes(&old, &new, &[]);

    assert!(
        patches.is_empty(),
        "Empty boundaries with same id should produce 0 patches, got: {:?}",
        patches
    );
}

#[test]
fn test_empty_boundary_different_ids_replace() {
    // Both empty, but ids differ → RemoveSubtree(old) + InsertSubtree(new).
    let old = VNode::element("div")
        .with_djust_id("root")
        .with_children(vec![dj_if_open("if-empty-0"), dj_if_close()]);

    let new = VNode::element("div")
        .with_djust_id("root")
        .with_children(vec![dj_if_open("if-empty-1"), dj_if_close()]);

    let patches = diff_nodes(&old, &new, &[]);

    assert_eq!(
        count(&patches, |p| matches!(p, Patch::RemoveSubtree { .. })),
        1
    );
    assert_eq!(
        count(&patches, |p| matches!(p, Patch::InsertSubtree { .. })),
        1
    );
}

// =============================================================================
// Case 9: Wire-format check — JSON shape matches Iter 2's expected fields
// =============================================================================

#[test]
fn test_remove_subtree_json_wire_format() {
    let patch = Patch::RemoveSubtree {
        id: "if-abc-0".to_string(),
    };
    let json = serde_json::to_string(&patch).unwrap();
    // Must match the wire shape Iter 2 client expects:
    //   {"type": "RemoveSubtree", "id": "if-abc-0"}
    assert!(
        json.contains("\"type\":\"RemoveSubtree\""),
        "Wire format missing type discriminator: {}",
        json
    );
    assert!(
        json.contains("\"id\":\"if-abc-0\""),
        "Wire format missing id field: {}",
        json
    );
}

#[test]
fn test_insert_subtree_json_wire_format() {
    let patch = Patch::InsertSubtree {
        id: "if-xyz-1".to_string(),
        path: vec![0, 1],
        d: Some("parent-id".to_string()),
        index: 3,
        html: "<!--dj-if id=\"if-xyz-1\"--><span>hi</span><!--/dj-if-->".to_string(),
    };
    let json = serde_json::to_string(&patch).unwrap();
    // Must match Iter 2's expected fields:
    //   {"type": "InsertSubtree", "id": "...", "path": [...],
    //    "d": "...", "index": N, "html": "..."}
    assert!(json.contains("\"type\":\"InsertSubtree\""), "{}", json);
    assert!(json.contains("\"id\":\"if-xyz-1\""), "{}", json);
    assert!(json.contains("\"path\":[0,1]"), "{}", json);
    assert!(json.contains("\"d\":\"parent-id\""), "{}", json);
    assert!(json.contains("\"index\":3"), "{}", json);
    assert!(
        json.contains("\"html\":\"<!--dj-if id="),
        "html field missing or malformed: {}",
        json
    );
}

#[test]
fn test_insert_subtree_omits_d_when_none() {
    // d is `Option<String>`; when None, the wire format should omit it
    // entirely (matching the existing pattern for InsertChild.d, which
    // uses `#[serde(skip_serializing_if = "Option::is_none")]`).
    let patch = Patch::InsertSubtree {
        id: "if-q-0".to_string(),
        path: vec![],
        d: None,
        index: 0,
        html: "<!--dj-if id=\"if-q-0\"--><!--/dj-if-->".to_string(),
    };
    let json = serde_json::to_string(&patch).unwrap();
    assert!(
        !json.contains("\"d\":"),
        "d should be omitted when None, got: {}",
        json
    );
}

// =============================================================================
// Case 10: Backward-compat — legacy bare `<!--dj-if-->` placeholder unaffected
// =============================================================================

#[test]
fn test_legacy_bare_dj_if_placeholder_still_uses_legacy_path() {
    // Legacy bare `<!--dj-if-->` placeholder (issue #295) has NO id and is
    // handled by the existing diff path (`diff_nodes` lines ~165-241):
    // when an old `<!--dj-if-->` is replaced with real content, emit
    // RemoveChild + InsertChild (NOT RemoveSubtree/InsertSubtree).
    let legacy_placeholder = VNode {
        tag: "#comment".to_string(),
        attrs: Default::default(),
        children: Vec::new(),
        text: Some("dj-if".to_string()), // No id
        key: None,
        djust_id: None,
        cached_html: None,
    };

    let old = VNode::element("div")
        .with_djust_id("root")
        .with_children(vec![legacy_placeholder]);

    let new = VNode::element("div")
        .with_djust_id("root")
        .with_children(vec![VNode::element("p")
            .with_djust_id("p1")
            .with_child(VNode::text("real content"))]);

    let patches = diff_nodes(&old, &new, &[]);

    // Legacy path: should NOT emit subtree patches (no id-bearing boundary).
    assert_eq!(
        count(&patches, |p| matches!(p, Patch::RemoveSubtree { .. })),
        0,
        "Legacy bare dj-if must not trigger keyed-subtree path, got: {:?}",
        patches
    );
    assert_eq!(
        count(&patches, |p| matches!(p, Patch::InsertSubtree { .. })),
        0,
        "Legacy bare dj-if must not trigger keyed-subtree path, got: {:?}",
        patches
    );
    // Legacy path emits InsertChild + RemoveChild (the existing #295 fix).
    assert!(
        patches
            .iter()
            .any(|p| matches!(p, Patch::InsertChild { .. })),
        "Legacy path expected InsertChild, got: {:?}",
        patches
    );
}

// =============================================================================
// Case 11: end-to-end via parse_html — proves the parser predicate +
// differ predicate agree.
// =============================================================================

#[test]
fn test_end_to_end_via_parse_html_tab_switch() {
    // Use parse_html to prove the parser-side predicate
    // (`parser.rs:494-499`) and the differ-side predicate
    // (`diff.rs:dj_if_open_id`) agree on what counts as a boundary marker.
    use djust_vdom::{diff, parse_html, parse_html_continue, reset_id_counter};

    let old_html =
        r#"<div><!--dj-if id="if-aabbccdd-0"--><div class="a">A</div><!--/dj-if--></div>"#;
    let new_html =
        r#"<div><!--dj-if id="if-aabbccdd-1"--><div class="b">B</div><!--/dj-if--></div>"#;

    reset_id_counter();
    let old_vdom = parse_html(old_html).unwrap();
    let new_vdom = parse_html_continue(new_html).unwrap();
    let patches = diff(&old_vdom, &new_vdom);

    assert!(
        patches
            .iter()
            .any(|p| matches!(p, Patch::RemoveSubtree { id } if id == "if-aabbccdd-0")),
        "End-to-end: expected RemoveSubtree(if-aabbccdd-0) in patches: {:?}",
        patches
    );
    assert!(
        patches
            .iter()
            .any(|p| matches!(p, Patch::InsertSubtree { id, .. } if id == "if-aabbccdd-1")),
        "End-to-end: expected InsertSubtree(if-aabbccdd-1) in patches: {:?}",
        patches
    );

    // No mis-targeted Replace patches.
    assert_eq!(
        count(&patches, |p| matches!(p, Patch::Replace { .. })),
        0,
        "End-to-end: differ should NOT fall back to Replace for keyed flip, got: {:?}",
        patches
    );
}
