//! Tests for Jinja2-style inline conditionals in {{ }} expressions
//!
//! Syntax: {{ true_expr if condition else false_expr }}
//! The else branch is optional (defaults to empty string).

use djust_core::{Context, Value};
use djust_templates::Template;

// ---------------------------------------------------------------------------
// Helper
// ---------------------------------------------------------------------------

fn render(source: &str, ctx: &Context) -> String {
    let t = Template::new(source).expect("template should parse");
    t.render(ctx).expect("template should render")
}

fn ctx() -> Context {
    Context::new()
}

fn ctx_bool(key: &str, val: bool) -> Context {
    let mut c = Context::new();
    c.set(key.to_string(), Value::Bool(val));
    c
}

fn ctx_str(key: &str, val: &str) -> Context {
    let mut c = Context::new();
    c.set(key.to_string(), Value::String(val.to_string()));
    c
}

fn ctx_int(key: &str, val: i64) -> Context {
    let mut c = Context::new();
    c.set(key.to_string(), Value::Integer(val));
    c
}

// ===========================================================================
// Basic true/false evaluation
// ===========================================================================

#[test]
fn inline_if_true_branch() {
    let c = ctx_bool("is_active", true);
    assert_eq!(
        render(r#"{{ "active" if is_active else "" }}"#, &c),
        "active"
    );
}

#[test]
fn inline_if_false_branch() {
    let c = ctx_bool("is_active", false);
    assert_eq!(
        render(r#"{{ "active" if is_active else "inactive" }}"#, &c),
        "inactive"
    );
}

#[test]
fn inline_if_no_else_true() {
    let c = ctx_bool("show", true);
    assert_eq!(render(r#"{{ "visible" if show }}"#, &c), "visible");
}

#[test]
fn inline_if_no_else_false_gives_empty() {
    let c = ctx_bool("show", false);
    assert_eq!(render(r#"{{ "visible" if show }}"#, &c), "");
}

// ===========================================================================
// Variable expressions in true/false branches
// ===========================================================================

#[test]
fn inline_if_variable_in_true_branch() {
    let mut c = Context::new();
    c.set("show".to_string(), Value::Bool(true));
    c.set("name".to_string(), Value::String("Alice".to_string()));
    assert_eq!(render("{{ name if show else \"Guest\" }}", &c), "Alice");
}

#[test]
fn inline_if_variable_in_false_branch() {
    let mut c = Context::new();
    c.set("show".to_string(), Value::Bool(false));
    c.set("fallback".to_string(), Value::String("nobody".to_string()));
    assert_eq!(
        render(r#"{{ "Alice" if show else fallback }}"#, &c),
        "nobody"
    );
}

// ===========================================================================
// Comparison operators in condition
// ===========================================================================

#[test]
fn inline_if_comparison_greater_than_true() {
    let c = ctx_int("count", 5);
    assert_eq!(
        render(r#"{{ "many" if count > 0 else "none" }}"#, &c),
        "many"
    );
}

#[test]
fn inline_if_comparison_greater_than_false() {
    let c = ctx_int("count", 0);
    assert_eq!(
        render(r#"{{ "many" if count > 0 else "none" }}"#, &c),
        "none"
    );
}

#[test]
fn inline_if_equality() {
    let c = ctx_str("mode", "dark");
    assert_eq!(
        render(
            r#"{{ "dark-theme" if mode == "dark" else "light-theme" }}"#,
            &c
        ),
        "dark-theme"
    );
}

#[test]
fn inline_if_inequality() {
    let c = ctx_str("status", "ok");
    assert_eq!(render(r#"{{ "error" if status != "ok" else "" }}"#, &c), "");
}

// ===========================================================================
// In HTML attribute context (the primary use-case)
// ===========================================================================

#[test]
fn inline_if_in_class_attribute_true() {
    let c = ctx_bool("is_selected", true);
    assert_eq!(
        render(
            r#"<li class="{{ "selected" if is_selected else "" }}">item</li>"#,
            &c
        ),
        r#"<li class="selected">item</li>"#
    );
}

#[test]
fn inline_if_in_class_attribute_false() {
    let c = ctx_bool("is_selected", false);
    assert_eq!(
        render(
            r#"<li class="{{ "selected" if is_selected else "" }}">item</li>"#,
            &c
        ),
        r#"<li class="">item</li>"#
    );
}

#[test]
fn inline_if_disabled_attribute() {
    let c = ctx_bool("is_locked", true);
    assert_eq!(
        render(
            r#"<button {{ "disabled" if is_locked else "" }}>click</button>"#,
            &c
        ),
        r#"<button disabled>click</button>"#
    );
}

#[test]
fn inline_if_multiple_in_template() {
    let mut c = Context::new();
    c.set("active".to_string(), Value::Bool(true));
    c.set("error".to_string(), Value::Bool(false));
    let tmpl =
        r#"<div class="{{ "active" if active else "" }} {{ "error" if error else "" }}"></div>"#;
    assert_eq!(render(tmpl, &c), r#"<div class="active "></div>"#);
}

// ===========================================================================
// XSS — auto-escaping applies to the selected branch output
// ===========================================================================

#[test]
fn inline_if_escapes_variable_output() {
    let mut c = Context::new();
    c.set("show".to_string(), Value::Bool(true));
    c.set(
        "val".to_string(),
        Value::String("<script>xss</script>".to_string()),
    );
    assert_eq!(
        render("{{ val if show else \"\" }}", &c),
        "&lt;script&gt;xss&lt;/script&gt;"
    );
}

#[test]
fn inline_if_escapes_literal_output() {
    // Literal strings in branches are treated as plain text, not safe HTML
    let c = ctx_bool("show", true);
    // Angle brackets in literals should be escaped
    assert_eq!(
        render(r#"{{ "<b>bold</b>" if show else "" }}"#, &c),
        "&lt;b&gt;bold&lt;/b&gt;"
    );
}

// ===========================================================================
// Edge cases
// ===========================================================================

#[test]
fn inline_if_undefined_variable_condition_is_falsy() {
    // Undefined variable → falsy → else branch
    assert_eq!(
        render(r#"{{ "yes" if defined_nowhere else "no" }}"#, &ctx()),
        "no"
    );
}

#[test]
fn inline_if_empty_string_condition_is_falsy() {
    let c = ctx_str("val", "");
    assert_eq!(render(r#"{{ "yes" if val else "no" }}"#, &c), "no");
}

#[test]
fn inline_if_nonempty_string_condition_is_truthy() {
    let c = ctx_str("val", "hello");
    assert_eq!(render(r#"{{ "yes" if val else "no" }}"#, &c), "yes");
}

#[test]
fn inline_if_zero_is_falsy() {
    let c = ctx_int("n", 0);
    assert_eq!(render(r#"{{ "nonzero" if n else "zero" }}"#, &c), "zero");
}

#[test]
fn inline_if_nonzero_is_truthy() {
    let c = ctx_int("n", 42);
    assert_eq!(render(r#"{{ "nonzero" if n else "zero" }}"#, &c), "nonzero");
}

// ===========================================================================
// Block tags inside attribute values — must NOT emit <!--dj-if--> (issue #388)
// ===========================================================================

/// `{% if %}` inside a class attribute value must compile and render without
/// emitting `<!--dj-if-->` comment anchors. Such comments cannot exist inside
/// HTML attribute values (browsers ignore them), which was causing VDOM path
/// index mismatches. Regression test for issue #388.
#[test]
fn block_if_in_class_attr_no_comment_anchors() {
    let src = r#"<a class="nav-link {% if active %}active{% endif %}">link</a>"#;

    let mut c = Context::new();
    c.set("active".to_string(), Value::Bool(false));
    let result = render(src, &c);
    assert!(
        !result.contains("<!--"),
        "{{% if %}} inside attribute value must not emit comment anchors: {result}"
    );
    assert!(
        result.contains(r#"class="nav-link "#),
        "false branch must leave class without extra text: {result}"
    );

    c.set("active".to_string(), Value::Bool(true));
    let result2 = render(src, &c);
    assert!(
        result2.contains(r#"class="nav-link active""#),
        "true branch must render content inside attribute: {result2}"
    );
}

/// Verify that `{% if %}` inside an attribute value does NOT shift VDOM path
/// indices for sibling elements. Regression test for issue #388.
#[test]
fn block_if_in_class_attr_does_not_shift_vdom_paths() {
    // The second <span> must be at VDOM child index 1, not 2.
    // If a <!--dj-if--> comment were emitted inside the first span's class
    // attribute, it would become part of the attribute string (not a DOM node),
    // but the server would still count it as a path index — causing all
    // subsequent sibling patches to target the wrong node.
    let src = r#"<div><span class="label {% if is_active %}active{% endif %}">Status</span><span>{{ counter }}</span></div>"#;

    let mut c = Context::new();
    c.set("is_active".to_string(), Value::Bool(false));
    c.set("counter".to_string(), Value::Integer(1));
    let result = render(src, &c);

    // No comment anchors anywhere
    assert!(
        !result.contains("<!--dj-if-->"),
        "{{% if %}} in attribute must not emit comment anchors: {result}"
    );
    // Second span renders correctly
    assert!(result.contains("<span>1</span>"), "got: {result}");

    let mut c2 = Context::new();
    c2.set("is_active".to_string(), Value::Bool(true));
    c2.set("counter".to_string(), Value::Integer(2));
    let result2 = render(src, &c2);
    assert!(
        result2.contains(r#"class="label active""#),
        "got: {result2}"
    );
    assert!(result2.contains("<span>2</span>"), "got: {result2}");
}
