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
// Case 1: Two SEPARATE `{% if %}` blocks — one disappears, another appears
// =============================================================================

/// Two separate `{% if %}` blocks at the same nesting level: the first
/// (id=if-deadbeef-0) is rendered in OLD only, the second (id=if-deadbeef-1)
/// is rendered in NEW only. This corresponds to e.g. two distinct top-level
/// `{% if %}` blocks where one toggles off and an unrelated one toggles on.
///
/// (Note: this fixture is NOT an `{% elif %}` cascade. An elif cascade
/// produces NESTED markers with the OUTER id present in both OLD and NEW
/// — see `test_elif_cascade_a_to_b_flip` and friends below for that
/// scenario, addressed by the recursive pre-pass in PR #1365's fix.)
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
fn test_two_separate_ifs_flip() {
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
//
// The boundary content length DIFFERS between OLD and NEW (3 children inside
// OLD's boundary, 1 inside NEW's), so the absolute index of the post-
// boundary footer SHIFTS: position 6 in OLD vs position 4 in NEW. This is
// the exact class of position-cascade that pre-Iter-3 broke on.
// =============================================================================

#[test]
fn test_sibling_shift_does_not_cascade() {
    // Layout — DIFFERENT lengths inside the boundary force the post-
    // boundary footer's absolute index to shift between OLD (idx=6) and
    // NEW (idx=4). Pre-fix, the footer SetText would mis-target.
    //
    // OLD: <div>
    //   children[0] <header>...                      ← static sibling
    //   children[1] <!--dj-if id="X-0"-->            ← open marker
    //   children[2..=4] <p>, <p>, <p>                ← 3 inner items
    //   children[5] <!--/dj-if-->                    ← close marker
    //   children[6] <footer>...                      ← static sibling
    // </div>
    //
    // NEW: <div>
    //   children[0] <header>...                      ← static sibling
    //   children[1] <!--dj-if id="X-1"-->            ← open marker
    //   children[2] <section>...                     ← 1 inner item
    //   children[3] <!--/dj-if-->                    ← close marker
    //   children[4] <footer>...                      ← static sibling, idx SHIFTED
    // </div>
    let old = VNode::element("div")
        .with_djust_id("root")
        .with_children(vec![
            VNode::element("header")
                .with_djust_id("hdr")
                .with_child(VNode::text("Header v1")),
            dj_if_open("if-X-0"),
            VNode::element("p")
                .with_djust_id("inner-old-1")
                .with_child(VNode::text("alpha")),
            VNode::element("p")
                .with_djust_id("inner-old-2")
                .with_child(VNode::text("beta")),
            VNode::element("p")
                .with_djust_id("inner-old-3")
                .with_child(VNode::text("gamma")),
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
            VNode::element("section") // DIFFERENT tag and shape in the conditional
                .with_djust_id("inner-new")
                .with_children(vec![VNode::element("h1")
                    .with_djust_id("h1")
                    .with_child(VNode::text("delta"))]),
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

    // CRITICAL: the boundary span has DIFFERENT lengths in OLD vs NEW (5
    // children vs 3 children inside the marker pair). This means the
    // post-boundary footer's absolute index shifts: idx=6 in OLD → idx=4
    // in NEW. The keyed-boundary pre-pass MUST pair non-boundary siblings
    // by RELATIVE position (header→header, footer→footer) so SetText
    // patches target the NEW tree's actual positions.
    //
    // In the NEW tree:
    //   children[0] = <header>, children[0].children[0] = text "Header v2"
    //     → path = [0, 0]
    //   children[1] = open, [2] = <section>, [3] = close
    //   children[4] = <footer>, children[4].children[0] = text "Footer v2"
    //     → path = [4, 0]   ← NOT [6, 0] (OLD index)
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
        "Footer SetText must target path=[4, 0] (NEW tree's footer text \
         after boundary content shrunk from 3 children to 1), got patches: {:?}",
        patches
    );

    // Defensive: must NOT have a patch targeting OLD's footer absolute
    // index [6, 0] — that would prove the position-shift cascade.
    let phantom_footer = patches
        .iter()
        .find(|p| matches!(p, Patch::SetText { path, .. } if path.as_slice() == [6, 0]));
    assert!(
        phantom_footer.is_none(),
        "No patch should target the OLD tree's footer absolute idx [6, 0] \
         — that would be the position-shift cascade bug, got: {:?}",
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
    use serde_json::json;
    let patch = Patch::RemoveSubtree {
        id: "if-abc-0".to_string(),
    };
    let serialized = serde_json::to_value(&patch).unwrap();
    // Compare via parsed `Value` shape (Action #1199) — substring matching
    // is brittle against field-order changes and incidental escaping.
    let expected = json!({
        "type": "RemoveSubtree",
        "id": "if-abc-0",
    });
    assert_eq!(
        serialized, expected,
        "RemoveSubtree wire shape mismatch: got {}, expected {}",
        serialized, expected
    );
}

#[test]
fn test_insert_subtree_json_wire_format() {
    use serde_json::json;
    let patch = Patch::InsertSubtree {
        id: "if-xyz-1".to_string(),
        path: vec![0, 1],
        d: Some("parent-id".to_string()),
        index: 3,
        html: "<!--dj-if id=\"if-xyz-1\"--><span>hi</span><!--/dj-if-->".to_string(),
    };
    let serialized = serde_json::to_value(&patch).unwrap();
    let expected = json!({
        "type": "InsertSubtree",
        "id": "if-xyz-1",
        "path": [0, 1],
        "d": "parent-id",
        "index": 3,
        "html": "<!--dj-if id=\"if-xyz-1\"--><span>hi</span><!--/dj-if-->",
    });
    assert_eq!(
        serialized, expected,
        "InsertSubtree wire shape mismatch: got {}, expected {}",
        serialized, expected
    );
}

#[test]
fn test_insert_subtree_omits_d_when_none() {
    use serde_json::json;
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
    let serialized = serde_json::to_value(&patch).unwrap();
    let expected = json!({
        "type": "InsertSubtree",
        "id": "if-q-0",
        "path": [],
        "index": 0,
        "html": "<!--dj-if id=\"if-q-0\"--><!--/dj-if-->",
        // No "d" key — omitted when None.
    });
    assert_eq!(
        serialized, expected,
        "InsertSubtree should omit `d` field when None: got {}, expected {}",
        serialized, expected
    );
    // Defensive: parsed shape has no `d` key.
    assert!(
        serialized.get("d").is_none(),
        "Serialized InsertSubtree must not contain a `d` key when d is None"
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

// =============================================================================
// Cases 12–16: `{% if %}/{% elif %}/{% else %}` CASCADE — the canonical
// failure mode that PR #1365 Stage 11 found in the first iter.
//
// Iter 1's parser desugars `{% if A %}...{% elif B %}...{% endif %}` as
// `Node::If { condition: A, true_nodes: ..., false_nodes: [Node::If { B }] }`.
// Each If gets its own `marker_id` in document order. When A is true, the
// outer If's true branch renders → `<!--dj-if id="if-X-0"-->...<!--/dj-if-->`.
// When A is false, the outer If's `false_nodes` (which contains the nested
// If(B)) renders → `<!--dj-if id="if-X-0"--><!--dj-if id="if-X-1"-->...
// <!--/dj-if--><!--/dj-if-->` (NESTED markers with the OUTER id present in
// BOTH OLD and NEW).
//
// Pre-fix (Stage 11 reproducer): the matched-id A's body was iterated
// element-by-element via `diff_nodes`, treating the inner B markers as
// ordinary VNodes. Result: step 2 emits InsertSubtree(B) AND step 3 emits
// overlapping Replace + InsertChild patches for the same content. Both
// applied = corrupt DOM.
//
// Post-fix: `dj_if_pre_pass_inner` recurses into A's body. The recursion
// finds B as a top-level pair within the body slice and emits exactly one
// InsertSubtree(B) — no overlapping element-by-element patches. The body's
// non-boundary content (if any) is paired by relative position and removed
// via RemoveChild.
// =============================================================================

#[test]
fn test_elif_cascade_a_to_b_flip() {
    // Scenario: `{% if A %}<div>A-content</div>{% elif B %}<div>B-content
    // </div>{% endif %}` flips from A=true to (A=false, B=true).
    //
    // OLD (A true): outer wraps just A's content.
    //   <!--dj-if id="A"--><div>A-content</div><!--/dj-if-->
    //
    // NEW (A false, B true): outer wraps the nested If(B), which wraps
    // B's content.
    //   <!--dj-if id="A"--><!--dj-if id="B"--><div>B-content</div><!--/dj-if--><!--/dj-if-->
    //
    // Expected post-fix:
    //   - Outer A is matched in BOTH (recurse into body).
    //   - Recursion finds B as a top-level pair in NEW's body, NOT in
    //     OLD's body → emits InsertSubtree(B).
    //   - OLD's body's non-boundary <div>A-content</div> is paired against
    //     NEW's body's empty non-boundary set → RemoveChild for A-content.
    //   - NO Replace or bare InsertChild for B's markers/content.
    let old = VNode::element("div")
        .with_djust_id("root")
        .with_children(vec![
            dj_if_open("A"),
            VNode::element("div")
                .with_djust_id("a-content")
                .with_child(VNode::text("A-content")),
            dj_if_close(),
        ]);

    let new = VNode::element("div")
        .with_djust_id("root")
        .with_children(vec![
            dj_if_open("A"),
            dj_if_open("B"),
            VNode::element("div")
                .with_djust_id("b-content")
                .with_child(VNode::text("B-content")),
            dj_if_close(),
            dj_if_close(),
        ]);

    let patches = diff_nodes(&old, &new, &[]);

    // Critical anti-overlap assertions:
    assert_eq!(
        count(&patches, |p| matches!(p, Patch::Replace { .. })),
        0,
        "elif cascade must NOT emit Replace patches (would overlap with \
         InsertSubtree). Got: {:?}",
        patches
    );
    assert_eq!(
        count(&patches, |p| matches!(p, Patch::InsertChild { .. })),
        0,
        "elif cascade must NOT emit bare InsertChild patches (would overlap \
         with InsertSubtree(B)). Got: {:?}",
        patches
    );

    // Exactly one InsertSubtree, targeting B.
    assert_eq!(
        count(&patches, |p| matches!(p, Patch::InsertSubtree { .. })),
        1,
        "Expected exactly 1 InsertSubtree(B). Got: {:?}",
        patches
    );
    let insert = patches
        .iter()
        .find_map(|p| match p {
            Patch::InsertSubtree {
                id, html, index, ..
            } => Some((id, html, *index)),
            _ => None,
        })
        .unwrap();
    assert_eq!(insert.0, "B", "InsertSubtree should target nested B id");
    assert!(
        insert.1.starts_with("<!--dj-if id=\"B\"-->"),
        "InsertSubtree.html should start with B's open marker, got: {}",
        insert.1
    );
    assert!(
        insert.1.ends_with("<!--/dj-if-->"),
        "InsertSubtree.html should end with close marker, got: {}",
        insert.1
    );
    // index = absolute position of B's open marker in the FULL parent's
    // children array. NEW children = [open(A), open(B), <div>, close, close],
    // so B's open is at index 1 (absolute, in the parent's children list).
    assert_eq!(
        insert.2, 1,
        "InsertSubtree.index must be B's absolute index in parent's children"
    );

    // No RemoveSubtree (A still present in both).
    assert_eq!(
        count(&patches, |p| matches!(p, Patch::RemoveSubtree { .. })),
        0,
        "Outer A is matched in both — must not emit RemoveSubtree. Got: {:?}",
        patches
    );

    // Exactly one RemoveChild for the old <div>A-content</div>.
    let remove_a = patches.iter().find_map(|p| match p {
        Patch::RemoveChild { child_d, .. } => Some(child_d.clone()),
        _ => None,
    });
    assert_eq!(
        remove_a,
        Some(Some("a-content".to_string())),
        "Expected RemoveChild for the old A-content div. Got: {:?}",
        patches
    );
}

#[test]
fn test_elif_cascade_b_to_a_flip() {
    // Symmetric direction (SHOULD-FIX #4 in PR #1365 Stage 11):
    // OLD has nested A→B, NEW collapses to A only.
    //
    // OLD: <!--dj-if id="A"--><!--dj-if id="B"--><div>B-content</div><!--/dj-if--><!--/dj-if-->
    // NEW: <!--dj-if id="A"--><div>A-content</div><!--/dj-if-->
    //
    // Expected: A matches → recurse into body. B is a top-level pair in
    // OLD's body, NOT in NEW's body → RemoveSubtree(B). OLD's body has
    // no non-boundary children; NEW's body has <div>A-content</div> as
    // non-boundary → InsertChild(A-content) at the right absolute index.
    let old = VNode::element("div")
        .with_djust_id("root")
        .with_children(vec![
            dj_if_open("A"),
            dj_if_open("B"),
            VNode::element("div")
                .with_djust_id("b-content")
                .with_child(VNode::text("B-content")),
            dj_if_close(),
            dj_if_close(),
        ]);

    let new = VNode::element("div")
        .with_djust_id("root")
        .with_children(vec![
            dj_if_open("A"),
            VNode::element("div")
                .with_djust_id("a-content")
                .with_child(VNode::text("A-content")),
            dj_if_close(),
        ]);

    let patches = diff_nodes(&old, &new, &[]);

    // Critical anti-overlap assertions: no Replace and no InsertSubtree
    // for A (A still present).
    assert_eq!(
        count(&patches, |p| matches!(p, Patch::Replace { .. })),
        0,
        "Symmetric cascade must NOT emit Replace patches. Got: {:?}",
        patches
    );
    let outer_a_subtree_patches = patches
        .iter()
        .filter(|p| {
            matches!(p, Patch::InsertSubtree { id, .. } if id == "A")
                || matches!(p, Patch::RemoveSubtree { id } if id == "A")
        })
        .count();
    assert_eq!(
        outer_a_subtree_patches, 0,
        "Outer A is matched in both — must not subtree-flip. Got: {:?}",
        patches
    );

    // Exactly one RemoveSubtree, targeting B.
    assert_eq!(
        count(&patches, |p| matches!(p, Patch::RemoveSubtree { .. })),
        1,
        "Expected exactly 1 RemoveSubtree(B). Got: {:?}",
        patches
    );
    let remove_id = patches
        .iter()
        .find_map(|p| match p {
            Patch::RemoveSubtree { id } => Some(id.clone()),
            _ => None,
        })
        .unwrap();
    assert_eq!(remove_id, "B", "RemoveSubtree should target nested B id");

    // Exactly one InsertChild for the new <div>A-content</div> at the
    // right absolute position. NEW children = [open(A), <div>A-content</div>,
    // close], so A-content's absolute index is 1.
    let insert_a = patches.iter().find_map(|p| match p {
        Patch::InsertChild { index, node, .. } => {
            if node.djust_id.as_deref() == Some("a-content") {
                Some(*index)
            } else {
                None
            }
        }
        _ => None,
    });
    assert_eq!(
        insert_a,
        Some(1),
        "Expected InsertChild for A-content at abs index 1. Got: {:?}",
        patches
    );
}

#[test]
fn test_elif_cascade_a_to_else_via_inner_change() {
    // Scenario: `{% if A %}X{% elif B %}Y{% else %}Z{% endif %}` flips from
    // (A=false, B=true) to (A=false, B=false → else fires).
    //
    // BOTH render the outer A marker AND the nested B marker — the only
    // difference is the innermost CONTENT. So matched ids cascade: A
    // matches in both, recurse; B matches in both, recurse; innermost
    // content differs → SetText (or similar inner-element patch).
    //
    // Tests that the recursion descends through TWO nesting levels without
    // emitting subtree-flip patches.
    //
    // OLD: <!--dj-if A--><!--dj-if B--><span>Y</span><!--/dj-if--><!--/dj-if-->
    // NEW: <!--dj-if A--><!--dj-if B--><span>Z</span><!--/dj-if--><!--/dj-if-->
    let old = VNode::element("div")
        .with_djust_id("root")
        .with_children(vec![
            dj_if_open("A"),
            dj_if_open("B"),
            VNode::element("span")
                .with_djust_id("inner-span")
                .with_child(VNode::text("Y")),
            dj_if_close(),
            dj_if_close(),
        ]);

    let new = VNode::element("div")
        .with_djust_id("root")
        .with_children(vec![
            dj_if_open("A"),
            dj_if_open("B"),
            VNode::element("span")
                .with_djust_id("inner-span")
                .with_child(VNode::text("Z")),
            dj_if_close(),
            dj_if_close(),
        ]);

    let patches = diff_nodes(&old, &new, &[]);

    // Both A and B match in both trees → no subtree-flip patches.
    assert_eq!(
        count(&patches, |p| matches!(p, Patch::RemoveSubtree { .. })),
        0,
        "Matched outer+inner ids: must not emit RemoveSubtree. Got: {:?}",
        patches
    );
    assert_eq!(
        count(&patches, |p| matches!(p, Patch::InsertSubtree { .. })),
        0,
        "Matched outer+inner ids: must not emit InsertSubtree. Got: {:?}",
        patches
    );
    assert_eq!(
        count(&patches, |p| matches!(p, Patch::Replace { .. })),
        0,
        "Matched outer+inner: must not fall back to Replace. Got: {:?}",
        patches
    );

    // The innermost text change should produce a SetText patch.
    assert!(
        patches
            .iter()
            .any(|p| matches!(p, Patch::SetText { text, .. } if text == "Z")),
        "Expected SetText('Z') for innermost text change, got: {:?}",
        patches
    );
}

#[test]
fn test_elif_cascade_with_extra_siblings() {
    // Cascade + sibling content that shifts relative position. The static
    // <header>/<footer> siblings sit OUTSIDE the outer A boundary. When
    // A's body changes shape (cascade flip A → A+B), the boundary's total
    // span grows from 3 children to 5 children, shifting the footer's
    // absolute index. The keyed pre-pass must pair siblings by RELATIVE
    // position so footer's SetText still targets the correct path.
    //
    // OLD (A true):
    //   children[0] = <header>v1</header>
    //   children[1..3] = <!--dj-if A--><div>A</div><!--/dj-if-->
    //   children[4] = <footer>v1</footer>
    //
    // NEW (A false, B true):
    //   children[0] = <header>v2</header>
    //   children[1..5] = <!--dj-if A--><!--dj-if B--><div>B</div><!--/dj-if--><!--/dj-if-->
    //   children[6] = <footer>v2</footer>
    //   ^^ footer abs index shifted from 4 → 6
    let old = VNode::element("div")
        .with_djust_id("root")
        .with_children(vec![
            VNode::element("header")
                .with_djust_id("hdr")
                .with_child(VNode::text("Header v1")),
            dj_if_open("A"),
            VNode::element("div")
                .with_djust_id("a-content")
                .with_child(VNode::text("A")),
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
            dj_if_open("A"),
            dj_if_open("B"),
            VNode::element("div")
                .with_djust_id("b-content")
                .with_child(VNode::text("B")),
            dj_if_close(),
            dj_if_close(),
            VNode::element("footer")
                .with_djust_id("ftr")
                .with_child(VNode::text("Footer v2")),
        ]);

    let patches = diff_nodes(&old, &new, &[]);

    // No Replace / no overlap patches.
    assert_eq!(
        count(&patches, |p| matches!(p, Patch::Replace { .. })),
        0,
        "Cascade-with-siblings must NOT emit Replace patches. Got: {:?}",
        patches
    );

    // A is matched (no subtree-flip on A); B is new (InsertSubtree(B)).
    let outer_a = patches
        .iter()
        .filter(|p| {
            matches!(p, Patch::RemoveSubtree { id } if id == "A")
                || matches!(p, Patch::InsertSubtree { id, .. } if id == "A")
        })
        .count();
    assert_eq!(outer_a, 0, "Outer A must not subtree-flip");

    let insert_b = patches
        .iter()
        .find_map(|p| match p {
            Patch::InsertSubtree { id, index, .. } if id == "B" => Some(*index),
            _ => None,
        })
        .expect("Expected InsertSubtree(B)");
    // B's open marker is at absolute index 2 in NEW (after header + outer-A-open).
    assert_eq!(
        insert_b, 2,
        "InsertSubtree(B).index must be B's abs index in parent's children"
    );

    // CRITICAL: header/footer SetText paths must reflect NEW tree's
    // actual absolute positions. Pre-fix, footer SetText would target
    // OLD's index [4, 0] instead of NEW's [6, 0].
    assert!(
        patches
            .iter()
            .any(|p| matches!(p, Patch::SetText { path, text, .. }
                              if path.as_slice() == [0, 0] && text == "Header v2")),
        "Header SetText must target NEW tree's path [0, 0], got: {:?}",
        patches
    );
    assert!(
        patches
            .iter()
            .any(|p| matches!(p, Patch::SetText { path, text, .. }
                              if path.as_slice() == [6, 0] && text == "Footer v2")),
        "Footer SetText must target NEW tree's path [6, 0] (after cascade \
         expansion), got: {:?}",
        patches
    );

    // Defensive: no patch targets OLD's footer abs index [4, 0].
    assert!(
        !patches
            .iter()
            .any(|p| matches!(p, Patch::SetText { path, text, .. }
                                         if path.as_slice() == [4, 0] && text == "Footer v2")),
        "No patch should target OLD tree's footer path [4, 0] — that would \
         be the position-shift cascade bug. Got: {:?}",
        patches
    );
}

#[test]
fn test_elif_cascade_three_branches_a_to_c() {
    // Three-branch cascade: `{% if A %}{% elif B %}{% elif C %}{% endif %}`.
    // Iter 1's parser desugars into nested If(A → If(B → If(C))).
    // marker_ids (in document order): A=if-X-0, B=if-X-1, C=if-X-2.
    //
    // Flip from A=true to (A=false, B=false, C=true). OLD has just A's
    // marker; NEW has A wrapping B wrapping C (three nesting levels).
    //
    // Tests that the recursive pre-pass correctly handles MORE THAN ONE
    // level of nesting introduced in a single flip.
    let old = VNode::element("div")
        .with_djust_id("root")
        .with_children(vec![
            dj_if_open("A"),
            VNode::element("p")
                .with_djust_id("a-content")
                .with_child(VNode::text("A")),
            dj_if_close(),
        ]);

    let new = VNode::element("div")
        .with_djust_id("root")
        .with_children(vec![
            dj_if_open("A"),
            dj_if_open("B"),
            dj_if_open("C"),
            VNode::element("p")
                .with_djust_id("c-content")
                .with_child(VNode::text("C")),
            dj_if_close(),
            dj_if_close(),
            dj_if_close(),
        ]);

    let patches = diff_nodes(&old, &new, &[]);

    // No Replace patches (would indicate the recursion fell through to
    // element-by-element pairing and treated B's markers as ordinary VNodes).
    assert_eq!(
        count(&patches, |p| matches!(p, Patch::Replace { .. })),
        0,
        "Three-level cascade must NOT emit Replace patches. Got: {:?}",
        patches
    );

    // The recursion should emit a single InsertSubtree(B) — the OUTERMOST
    // newly-introduced boundary at A's body level. The C boundary is INSIDE
    // B's HTML (which is rendered into InsertSubtree.html as a single string),
    // so we don't expect a separate InsertSubtree(C). Only one keyed insert.
    let insert_subtree_ids: Vec<String> = patches
        .iter()
        .filter_map(|p| match p {
            Patch::InsertSubtree { id, .. } => Some(id.clone()),
            _ => None,
        })
        .collect();
    assert_eq!(
        insert_subtree_ids,
        vec!["B".to_string()],
        "Expected exactly InsertSubtree(B) — C is contained in B's HTML. \
         Got InsertSubtree ids: {:?}, full patches: {:?}",
        insert_subtree_ids,
        patches
    );

    // The InsertSubtree(B).html must contain BOTH B's and C's markers,
    // proving C is bundled inside B's rendered HTML (the wire-protocol
    // shape that the client expects — one parse + insert per top-level
    // boundary).
    let b_html = patches
        .iter()
        .find_map(|p| match p {
            Patch::InsertSubtree { id, html, .. } if id == "B" => Some(html.clone()),
            _ => None,
        })
        .unwrap();
    assert!(
        b_html.starts_with("<!--dj-if id=\"B\"-->"),
        "B's html must start with B's open marker, got: {}",
        b_html
    );
    assert!(
        b_html.contains("<!--dj-if id=\"C\"-->"),
        "B's html must include nested C's open marker, got: {}",
        b_html
    );
    assert!(
        b_html.ends_with("<!--/dj-if-->"),
        "B's html must end with a close marker, got: {}",
        b_html
    );
}
