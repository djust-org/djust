//! Wire-protocol JSON snapshot tests for `Patch` variants (#1419).
//!
//! Patch JSON is a contract with the client
//! (`python/djust/static/djust/src/12-vdom-patch.js`). Existing tests
//! verify patch-level *semantics* (this diff produces this patch
//! sequence) but don't pin the *JSON shape*. A field rename
//! (e.g., `d` → `parent_d`) or a default-value change wouldn't fail any
//! existing test, but it would silently break every deployed client
//! running an older bundle.
//!
//! These tests pin the JSON for every Patch variant + the VNode
//! struct. They are deliberately literal-string snapshots — when one
//! fails, that's a wire-protocol break and the failure should show up
//! as a `cargo test` red, not as a production client crash.
//!
//! When intentionally bumping the wire shape (rare; coordinate with
//! the client team and bump a contract version), update the snapshot
//! AND the client. Both must change in the same release.

use std::collections::HashMap;

use djust_vdom::{Patch, VNode};

fn elem(tag: &str) -> VNode {
    VNode {
        tag: tag.to_string(),
        attrs: HashMap::new(),
        children: Vec::new(),
        text: None,
        key: None,
        djust_id: None,
        cached_html: None,
    }
}

fn elem_with_id(tag: &str, djust_id: &str) -> VNode {
    let mut v = elem(tag);
    v.djust_id = Some(djust_id.to_string());
    v
}

#[test]
fn snapshot_vnode_minimal() {
    let v = elem("div");
    let json = serde_json::to_string(&v).unwrap();
    // attrs / children render as empty containers; text/key/djust_id
    // skipped via `Option::is_none` defaults from serde.
    assert_eq!(
        json, r#"{"tag":"div","attrs":{},"children":[],"text":null,"key":null}"#,
        "VNode wire shape changed — coordinate with client (12-vdom-patch.js) before bumping"
    );
}

#[test]
fn snapshot_vnode_with_djust_id() {
    let v = elem_with_id("span", "1a");
    let json = serde_json::to_string(&v).unwrap();
    assert_eq!(
        json, r#"{"tag":"span","attrs":{},"children":[],"text":null,"key":null,"djust_id":"1a"}"#,
        "VNode.djust_id wire field changed"
    );
}

#[test]
fn snapshot_vnode_text_node() {
    let v = VNode {
        tag: "#text".to_string(),
        attrs: HashMap::new(),
        children: Vec::new(),
        text: Some("hello".to_string()),
        key: None,
        djust_id: None,
        cached_html: None,
    };
    let json = serde_json::to_string(&v).unwrap();
    assert_eq!(
        json,
        r##"{"tag":"#text","attrs":{},"children":[],"text":"hello","key":null}"##
    );
}

#[test]
fn snapshot_vnode_cached_html_is_skipped() {
    // `cached_html` is `#[serde(skip)]` — must NOT appear in wire JSON.
    let mut v = elem("div");
    v.cached_html = Some("<div>cached</div>".to_string());
    let json = serde_json::to_string(&v).unwrap();
    assert!(
        !json.contains("cached_html"),
        "cached_html leaked into wire JSON: {json}"
    );
}

#[test]
fn snapshot_patch_set_text() {
    let p = Patch::SetText {
        path: vec![0, 1],
        d: Some("3z".to_string()),
        text: "hello".to_string(),
    };
    let json = serde_json::to_string(&p).unwrap();
    assert_eq!(
        json, r#"{"type":"SetText","path":[0,1],"d":"3z","text":"hello"}"#,
        "SetText wire shape changed"
    );
}

#[test]
fn snapshot_patch_set_text_no_djust_id() {
    // d is Option — when None, must be skipped via
    // `skip_serializing_if = Option::is_none`.
    let p = Patch::SetText {
        path: vec![0],
        d: None,
        text: "x".to_string(),
    };
    let json = serde_json::to_string(&p).unwrap();
    assert_eq!(json, r#"{"type":"SetText","path":[0],"text":"x"}"#);
}

#[test]
fn snapshot_patch_set_attr() {
    let p = Patch::SetAttr {
        path: vec![],
        d: Some("4m".to_string()),
        key: "class".to_string(),
        value: "active".to_string(),
    };
    let json = serde_json::to_string(&p).unwrap();
    assert_eq!(
        json,
        r#"{"type":"SetAttr","path":[],"d":"4m","key":"class","value":"active"}"#
    );
}

#[test]
fn snapshot_patch_remove_attr() {
    let p = Patch::RemoveAttr {
        path: vec![2],
        d: None,
        key: "disabled".to_string(),
    };
    let json = serde_json::to_string(&p).unwrap();
    assert_eq!(json, r#"{"type":"RemoveAttr","path":[2],"key":"disabled"}"#);
}

#[test]
fn snapshot_patch_replace() {
    let p = Patch::Replace {
        path: vec![0],
        d: Some("1a".to_string()),
        node: elem_with_id("span", "1a"),
    };
    let json = serde_json::to_string(&p).unwrap();
    assert_eq!(
        json,
        r#"{"type":"Replace","path":[0],"d":"1a","node":{"tag":"span","attrs":{},"children":[],"text":null,"key":null,"djust_id":"1a"}}"#
    );
}

#[test]
fn snapshot_patch_insert_child_with_ref_d() {
    let p = Patch::InsertChild {
        path: vec![0, 1],
        d: Some("2y".to_string()),
        index: 3,
        node: elem("li"),
        ref_d: Some("2z".to_string()),
    };
    let json = serde_json::to_string(&p).unwrap();
    assert_eq!(
        json,
        r#"{"type":"InsertChild","path":[0,1],"d":"2y","index":3,"node":{"tag":"li","attrs":{},"children":[],"text":null,"key":null},"ref_d":"2z"}"#
    );
}

#[test]
fn snapshot_patch_insert_child_no_ref_d() {
    let p = Patch::InsertChild {
        path: vec![0],
        d: Some("2y".to_string()),
        index: 0,
        node: elem("li"),
        ref_d: None,
    };
    let json = serde_json::to_string(&p).unwrap();
    assert!(
        !json.contains("ref_d"),
        "ref_d should be skipped when None: {json}"
    );
}

#[test]
fn snapshot_patch_remove_child_with_child_d() {
    let p = Patch::RemoveChild {
        path: vec![0],
        d: Some("2y".to_string()),
        index: 5,
        child_d: Some("5m".to_string()),
    };
    let json = serde_json::to_string(&p).unwrap();
    assert_eq!(
        json,
        r#"{"type":"RemoveChild","path":[0],"d":"2y","index":5,"child_d":"5m"}"#
    );
}

#[test]
fn snapshot_patch_move_child() {
    let p = Patch::MoveChild {
        path: vec![],
        d: Some("1a".to_string()),
        from: 2,
        to: 0,
        child_d: Some("3z".to_string()),
    };
    let json = serde_json::to_string(&p).unwrap();
    assert_eq!(
        json,
        r#"{"type":"MoveChild","path":[],"d":"1a","from":2,"to":0,"child_d":"3z"}"#
    );
}

#[test]
fn snapshot_patch_insert_subtree() {
    let p = Patch::InsertSubtree {
        id: "if-a3b1c2d4-1".to_string(),
        path: vec![0],
        d: Some("2y".to_string()),
        index: 1,
        html: r#"<!--dj-if id="if-a3b1c2d4-1"--><div>x</div><!--/dj-if-->"#.to_string(),
    };
    let json = serde_json::to_string(&p).unwrap();
    assert_eq!(
        json,
        r##"{"type":"InsertSubtree","id":"if-a3b1c2d4-1","path":[0],"d":"2y","index":1,"html":"<!--dj-if id=\"if-a3b1c2d4-1\"--><div>x</div><!--/dj-if-->"}"##
    );
}

#[test]
fn snapshot_patch_remove_subtree() {
    let p = Patch::RemoveSubtree {
        id: "if-a3b1c2d4-0".to_string(),
    };
    let json = serde_json::to_string(&p).unwrap();
    assert_eq!(json, r#"{"type":"RemoveSubtree","id":"if-a3b1c2d4-0"}"#);
}

#[test]
fn snapshot_all_variants_round_trip_via_serde() {
    // Sanity: every variant we emit must also DESERIALIZE cleanly via
    // `serde_json::from_str`. (The client does this for testing; the
    // production client uses a small custom parser, but `serde_json`
    // round-trip is a useful invariant.)
    let patches: Vec<Patch> = vec![
        Patch::SetText {
            path: vec![0],
            d: Some("a".to_string()),
            text: "t".to_string(),
        },
        Patch::SetAttr {
            path: vec![],
            d: None,
            key: "k".to_string(),
            value: "v".to_string(),
        },
        Patch::RemoveAttr {
            path: vec![1],
            d: Some("b".to_string()),
            key: "k".to_string(),
        },
        Patch::Replace {
            path: vec![0],
            d: None,
            node: elem("p"),
        },
        Patch::InsertChild {
            path: vec![0],
            d: Some("c".to_string()),
            index: 0,
            node: elem("li"),
            ref_d: None,
        },
        Patch::RemoveChild {
            path: vec![0],
            d: Some("d".to_string()),
            index: 1,
            child_d: Some("e".to_string()),
        },
        Patch::MoveChild {
            path: vec![],
            d: None,
            from: 0,
            to: 1,
            child_d: None,
        },
        Patch::InsertSubtree {
            id: "x".to_string(),
            path: vec![0],
            d: Some("y".to_string()),
            index: 0,
            html: "<!--dj-if id=\"x\"--><span/><!--/dj-if-->".to_string(),
        },
        Patch::RemoveSubtree {
            id: "x".to_string(),
        },
    ];
    let json = serde_json::to_string(&patches).unwrap();
    let parsed: Vec<Patch> = serde_json::from_str(&json).unwrap();
    assert_eq!(parsed.len(), patches.len());
}
