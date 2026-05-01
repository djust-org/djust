//! Regression tests for issue #1253: defense-in-depth validation of
//! user-supplied `dj-id` attributes.
//!
//! Background: every parsed element receives a server-generated base62
//! compact ID. If a template author hand-writes `<div dj-id="...">`, the
//! parser already silently drops it and replaces with the server ID
//! (parser.rs ~line 397). This regression test pins that contract — the
//! resulting node always has a base62 djust_id, never the user-supplied
//! value, regardless of how malformed the input is.
//!
//! After the fix, malformed user-supplied `dj-id` values also emit a
//! debug-level log line for observability, but the parsing contract
//! itself does not change (server-generated ID always wins).

use djust_vdom::parse_html;

fn is_base62(s: &str) -> bool {
    !s.is_empty() && s.chars().all(|c| c.is_ascii_alphanumeric())
}

#[test]
fn test_user_supplied_dj_id_with_spaces_rejected() {
    // User-supplied dj-id with spaces — definitely not base62.
    let html = r#"<div dj-id="invalid value with spaces"><span>x</span></div>"#;
    let vnode = parse_html(html).unwrap();

    // The root is the <html> wrapper from html5ever; descend to find <div>.
    fn find_div(node: &djust_vdom::VNode) -> Option<&djust_vdom::VNode> {
        if node.tag == "div" {
            return Some(node);
        }
        for child in &node.children {
            if let Some(d) = find_div(child) {
                return Some(d);
            }
        }
        None
    }
    let div = find_div(&vnode).expect("expected to find <div> in parsed output");

    // The dj-id attribute on the resulting node MUST be a server-generated
    // base62 string, NOT the user-supplied "invalid value with spaces".
    let djust_id = div
        .djust_id
        .as_deref()
        .expect("element should have a server-generated djust_id");
    assert!(
        is_base62(djust_id),
        "REGRESSION #1253: parser must replace user-supplied dj-id with \
         a server-generated base62 ID. Got: {:?}",
        djust_id
    );

    let attr_dj_id = div.attrs.get("dj-id").map(String::as_str).unwrap_or("");
    assert!(
        is_base62(attr_dj_id),
        "REGRESSION #1253: dj-id attribute must be a base62 string, \
         not the user-supplied value. Got: {:?}",
        attr_dj_id
    );
    assert_eq!(
        attr_dj_id, djust_id,
        "dj-id attribute and djust_id field must match"
    );
    assert_ne!(attr_dj_id, "invalid value with spaces");
}

#[test]
fn test_user_supplied_dj_id_with_special_chars_rejected() {
    let html = r#"<div dj-id="<script>alert(1)</script>"></div>"#;
    let vnode = parse_html(html).unwrap();
    fn find_div(node: &djust_vdom::VNode) -> Option<&djust_vdom::VNode> {
        if node.tag == "div" {
            return Some(node);
        }
        for child in &node.children {
            if let Some(d) = find_div(child) {
                return Some(d);
            }
        }
        None
    }
    let div = find_div(&vnode).expect("expected <div> in parsed output");
    let attr_dj_id = div.attrs.get("dj-id").map(String::as_str).unwrap_or("");
    assert!(
        is_base62(attr_dj_id),
        "user-supplied dj-id with HTML must be replaced by server-generated base62. Got: {:?}",
        attr_dj_id
    );
}

#[test]
fn test_user_supplied_dj_id_empty_rejected() {
    let html = r#"<div dj-id=""></div>"#;
    let vnode = parse_html(html).unwrap();
    fn find_div(node: &djust_vdom::VNode) -> Option<&djust_vdom::VNode> {
        if node.tag == "div" {
            return Some(node);
        }
        for child in &node.children {
            if let Some(d) = find_div(child) {
                return Some(d);
            }
        }
        None
    }
    let div = find_div(&vnode).expect("expected <div> in parsed output");
    let attr_dj_id = div.attrs.get("dj-id").map(String::as_str).unwrap_or("");
    assert!(
        is_base62(attr_dj_id),
        "empty user-supplied dj-id must be replaced. Got: {:?}",
        attr_dj_id
    );
}

#[test]
fn test_valid_base62_user_dj_id_still_rejected_for_safety() {
    // Even a syntactically-valid base62 string from the user should be
    // replaced by server-generated. The server is the source of truth.
    let html = r#"<div dj-id="abc123"></div>"#;
    let vnode = parse_html(html).unwrap();
    fn find_div(node: &djust_vdom::VNode) -> Option<&djust_vdom::VNode> {
        if node.tag == "div" {
            return Some(node);
        }
        for child in &node.children {
            if let Some(d) = find_div(child) {
                return Some(d);
            }
        }
        None
    }
    let div = find_div(&vnode).expect("expected <div> in parsed output");
    let attr_dj_id = div.attrs.get("dj-id").map(String::as_str).unwrap_or("");
    assert!(
        is_base62(attr_dj_id),
        "dj-id must be base62. Got: {:?}",
        attr_dj_id
    );
    // The server ID counter starts at 0 in fresh parses; assert that the
    // user's "abc123" did not survive verbatim.
    // (The actual server ID for the only element in this parse should be a
    // small base62 number like "0" or "1".)
    assert_ne!(
        attr_dj_id, "abc123",
        "user-supplied dj-id, even if valid base62, must be overwritten"
    );
}
