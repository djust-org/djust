//! Tests for `<!--dj-if id="if-N"-->` boundary markers around `{% if %}`
//! blocks containing element nodes.
//!
//! Foundation 1 of 3 toward issue #1358 (re-open of #256 Option A —
//! keyed VDOM diff for conditional subtrees). This iter adds the
//! marker emission only — the client-side patch applier (Iter 2) and
//! the Rust VDOM differ that uses the markers as keyed boundaries
//! (Iter 3) follow in subsequent PRs.
//!
//! Marker shape (Option B — pair per `Node::If`):
//!
//! ```html
//! <!--dj-if id="if-N"-->...rendered body...<!--/dj-if-->
//! ```
//!
//! - IDs are assigned at parse time by `assign_if_marker_ids` walking
//!   the AST in document order. Stable across re-renders of the same
//!   parsed source.
//! - The `cond=` attribute is intentionally OMITTED for safety
//!   (condition strings could contain `--` or `>` that would close
//!   the comment early). Iter 3's differ keys off the `id` alone.
//! - Pure-text conditionals (text-only true/false bodies) skip
//!   emission — text positions are sibling-stable already.
//! - The legacy `<!--dj-if-->` placeholder for false-no-else
//!   (issue #295) is preserved unchanged for pure-text conditionals.
//! - HTML attribute context (`{% if %}` inside an unclosed tag) skips
//!   marker emission — comments inside attribute values produce
//!   malformed HTML (issue #380).

use djust_core::{Context, Value};
use djust_templates::Template;

fn render(source: &str, ctx: &Context) -> String {
    let t = Template::new(source).expect("template should parse");
    t.render(ctx).expect("template should render")
}

fn ctx_bool(key: &str, val: bool) -> Context {
    let mut c = Context::new();
    c.set(key.to_string(), Value::Bool(val));
    c
}

// ---------------------------------------------------------------------------
// Element-bearing if blocks emit boundary markers
// ---------------------------------------------------------------------------

#[test]
fn markers_emitted_around_simple_element_if_true() {
    let result = render(
        "{% if show %}<div>foo</div>{% endif %}",
        &ctx_bool("show", true),
    );
    assert_eq!(
        result,
        r#"<!--dj-if id="if-0"--><div>foo</div><!--/dj-if-->"#
    );
}

#[test]
fn markers_emitted_around_simple_element_if_false_no_else() {
    // Element-bearing if with no else and false condition: emit the
    // pair with empty body. The closing comment adjacent to the
    // opening serves the sibling-stability role that the legacy
    // single-comment placeholder served for text-only conditionals.
    let result = render(
        "{% if show %}<div>foo</div>{% endif %}",
        &ctx_bool("show", false),
    );
    assert_eq!(result, r#"<!--dj-if id="if-0"--><!--/dj-if-->"#);
}

#[test]
fn markers_emitted_around_element_if_else_true() {
    let result = render(
        "{% if show %}<div>A</div>{% else %}<span>B</span>{% endif %}",
        &ctx_bool("show", true),
    );
    assert_eq!(result, r#"<!--dj-if id="if-0"--><div>A</div><!--/dj-if-->"#);
}

#[test]
fn markers_emitted_around_element_if_else_false() {
    let result = render(
        "{% if show %}<div>A</div>{% else %}<span>B</span>{% endif %}",
        &ctx_bool("show", false),
    );
    assert_eq!(
        result,
        r#"<!--dj-if id="if-0"--><span>B</span><!--/dj-if-->"#
    );
}

#[test]
fn markers_around_when_only_true_branch_has_elements() {
    // True branch has elements; false branch is pure text. Both
    // renders should be wrapped because the if as a whole is
    // element-bearing.
    let template = "{% if show %}<div>yes</div>{% else %}plain text{% endif %}";

    let result_true = render(template, &ctx_bool("show", true));
    assert_eq!(
        result_true,
        r#"<!--dj-if id="if-0"--><div>yes</div><!--/dj-if-->"#
    );

    let result_false = render(template, &ctx_bool("show", false));
    assert_eq!(
        result_false,
        r#"<!--dj-if id="if-0"-->plain text<!--/dj-if-->"#
    );
}

// ---------------------------------------------------------------------------
// Pure-text conditionals — no markers emitted, legacy placeholder preserved
// ---------------------------------------------------------------------------

#[test]
fn no_markers_for_pure_text_if_true() {
    let result = render("{% if show %}foo{% endif %}", &ctx_bool("show", true));
    assert_eq!(result, "foo");
    assert!(!result.contains(r#"<!--dj-if id="#));
    assert!(!result.contains("<!--/dj-if-->"));
}

#[test]
fn no_markers_for_pure_text_if_false_keeps_legacy_placeholder() {
    // Issue #295 / DJE-053 behavior preserved: text-only bodies
    // continue to emit `<!--dj-if-->` for sibling stability. The new
    // boundary markers are NOT layered on top.
    let result = render("{% if show %}foo{% endif %}", &ctx_bool("show", false));
    assert_eq!(result, "<!--dj-if-->");
    assert!(!result.contains(r#"<!--dj-if id="#));
}

#[test]
fn no_markers_for_pure_text_if_else() {
    let result = render(
        "{% if show %}yes{% else %}no{% endif %}",
        &ctx_bool("show", false),
    );
    assert_eq!(result, "no");
    assert!(!result.contains("dj-if"));
}

// ---------------------------------------------------------------------------
// Elif chain — nested marker pairs (Option B: pair per Node::If)
// ---------------------------------------------------------------------------

#[test]
fn elif_chain_first_branch_fires() {
    // `if A elif B else` chain: parser nests an inner If(B) inside
    // outer's false_nodes. When A is true, only the outer If fires,
    // so we get one pair around A's body.
    let template =
        "{% if a %}<div>A</div>{% elif b %}<div>B</div>{% else %}<div>C</div>{% endif %}";
    let mut c = Context::new();
    c.set("a".to_string(), Value::Bool(true));
    c.set("b".to_string(), Value::Bool(false));
    let result = render(template, &c);
    assert_eq!(result, r#"<!--dj-if id="if-0"--><div>A</div><!--/dj-if-->"#);
}

#[test]
fn elif_chain_second_branch_fires() {
    // When elif's condition fires, both the outer (false-then-render-
    // false_nodes) and the inner If wrap.
    let template =
        "{% if a %}<div>A</div>{% elif b %}<div>B</div>{% else %}<div>C</div>{% endif %}";
    let mut c = Context::new();
    c.set("a".to_string(), Value::Bool(false));
    c.set("b".to_string(), Value::Bool(true));
    let result = render(template, &c);
    // Outer If(if-0) renders false_nodes (which is the inner If),
    // inner If(if-1) renders <div>B</div>.
    assert_eq!(
        result,
        r#"<!--dj-if id="if-0"--><!--dj-if id="if-1"--><div>B</div><!--/dj-if--><!--/dj-if-->"#
    );
}

#[test]
fn elif_chain_else_branch_fires() {
    let template =
        "{% if a %}<div>A</div>{% elif b %}<div>B</div>{% else %}<div>C</div>{% endif %}";
    let mut c = Context::new();
    c.set("a".to_string(), Value::Bool(false));
    c.set("b".to_string(), Value::Bool(false));
    let result = render(template, &c);
    // Outer (if-0, A false) renders inner; inner (if-1, B false)
    // renders C (its false_nodes).
    assert_eq!(
        result,
        r#"<!--dj-if id="if-0"--><!--dj-if id="if-1"--><div>C</div><!--/dj-if--><!--/dj-if-->"#
    );
}

// ---------------------------------------------------------------------------
// Nested ifs — distinct IDs in document order
// ---------------------------------------------------------------------------

#[test]
fn nested_if_distinct_ids() {
    let template = "{% if a %}<div>{% if b %}<span>x</span>{% endif %}</div>{% endif %}";
    let mut c = Context::new();
    c.set("a".to_string(), Value::Bool(true));
    c.set("b".to_string(), Value::Bool(true));
    let result = render(template, &c);
    // Outer is if-0, inner is if-1 (document order).
    assert_eq!(
        result,
        r#"<!--dj-if id="if-0"--><div><!--dj-if id="if-1"--><span>x</span><!--/dj-if--></div><!--/dj-if-->"#
    );
}

// ---------------------------------------------------------------------------
// {% for %}{% if %} — same ID across iterations (parse-time stable)
// ---------------------------------------------------------------------------

#[test]
fn for_if_iteration_stable_id() {
    // The if inside the for body has marker_id assigned ONCE at parse
    // time. Each loop iteration emits the SAME id.
    let template =
        "{% for i in items %}{% if i.show %}<div>{{ i.name }}</div>{% endif %}{% endfor %}";
    let mut c = Context::new();
    let mut item1 = std::collections::HashMap::new();
    item1.insert("show".to_string(), Value::Bool(true));
    item1.insert("name".to_string(), Value::String("a".to_string()));
    let mut item2 = std::collections::HashMap::new();
    item2.insert("show".to_string(), Value::Bool(true));
    item2.insert("name".to_string(), Value::String("b".to_string()));
    c.set(
        "items".to_string(),
        Value::List(vec![Value::Object(item1), Value::Object(item2)]),
    );
    let result = render(template, &c);
    // Both iterations use if-0 (the parser only saw ONE Node::If).
    assert_eq!(
        result,
        r#"<!--dj-if id="if-0"--><div>a</div><!--/dj-if--><!--dj-if id="if-0"--><div>b</div><!--/dj-if-->"#
    );
}

// ---------------------------------------------------------------------------
// ID stability across renders
// ---------------------------------------------------------------------------

#[test]
fn id_stable_across_renders() {
    let source = "{% if show %}<div>foo</div>{% endif %}";
    let template = Template::new(source).expect("template parses");
    let c1 = ctx_bool("show", true);
    let c2 = ctx_bool("show", true);
    let r1 = template.render(&c1).unwrap();
    let r2 = template.render(&c2).unwrap();
    assert_eq!(r1, r2);
    assert!(r1.contains(r#"<!--dj-if id="if-0"-->"#));
}

#[test]
fn id_stable_across_separate_template_instances() {
    let source = "{% if show %}<div>foo</div>{% endif %}";
    let t1 = Template::new(source).expect("first parse");
    let t2 = Template::new(source).expect("second parse");
    let c = ctx_bool("show", true);
    let r1 = t1.render(&c).unwrap();
    let r2 = t2.render(&c).unwrap();
    assert_eq!(r1, r2);
}

// ---------------------------------------------------------------------------
// HTML attribute context — markers SKIPPED (issue #380)
// ---------------------------------------------------------------------------

#[test]
fn no_markers_in_attribute_context() {
    // {% if %} inside an HTML attribute value must not emit comment
    // markers (existing or new). Existing behavior for issue #380 is
    // preserved.
    let template = r#"<a class="nav-link {% if active %}active{% endif %}">link</a>"#;

    let mut c = Context::new();
    c.set("active".to_string(), Value::Bool(false));
    let r_false = render(template, &c);
    assert!(
        !r_false.contains("dj-if"),
        "no dj-if comments allowed inside attribute: {r_false}"
    );

    c.set("active".to_string(), Value::Bool(true));
    let r_true = render(template, &c);
    assert!(
        !r_true.contains("dj-if"),
        "no dj-if comments allowed inside attribute even when truthy: {r_true}"
    );
    assert!(r_true.contains(r#"class="nav-link active""#));
}

// ---------------------------------------------------------------------------
// Sibling positions stable when condition flips
// ---------------------------------------------------------------------------

#[test]
fn sibling_position_stable_for_element_if() {
    // When condition flips truthy/falsy on an element-bearing if,
    // the marker pair anchors the same position. This is the property
    // Iter 3's VDOM differ relies on.
    let template = "<div>{% if show %}<span>A</span>{% endif %}<i>B</i></div>";
    let mut c = Context::new();
    c.set("show".to_string(), Value::Bool(false));
    let r_false = render(template, &c);
    assert_eq!(
        r_false,
        r#"<div><!--dj-if id="if-0"--><!--/dj-if--><i>B</i></div>"#
    );

    c.set("show".to_string(), Value::Bool(true));
    let r_true = render(template, &c);
    assert_eq!(
        r_true,
        r#"<div><!--dj-if id="if-0"--><span>A</span><!--/dj-if--><i>B</i></div>"#
    );
}

// ---------------------------------------------------------------------------
// Sequential ifs at top level get distinct IDs
// ---------------------------------------------------------------------------

#[test]
fn sequential_ifs_get_distinct_ids() {
    let template = "{% if a %}<div>X</div>{% endif %}{% if b %}<div>Y</div>{% endif %}";
    let mut c = Context::new();
    c.set("a".to_string(), Value::Bool(true));
    c.set("b".to_string(), Value::Bool(true));
    let result = render(template, &c);
    assert_eq!(
        result,
        r#"<!--dj-if id="if-0"--><div>X</div><!--/dj-if--><!--dj-if id="if-1"--><div>Y</div><!--/dj-if-->"#
    );
}

// ---------------------------------------------------------------------------
// Mutation-after-capture (Action #1039 discipline): re-rendering after
// changing the context must NOT produce different IDs for the same
// parsed template.
// ---------------------------------------------------------------------------

#[test]
fn id_unchanged_after_context_mutation() {
    let source = "{% if show %}<div>foo</div>{% endif %}";
    let template = Template::new(source).expect("parse");

    let mut c = Context::new();
    c.set("show".to_string(), Value::Bool(true));
    let r1 = template.render(&c).unwrap();

    // Mutate the same context — re-render the same template.
    c.set("show".to_string(), Value::Bool(false));
    let r2 = template.render(&c).unwrap();

    // Both must reference the same id="if-0" — the marker is keyed
    // by parse-time position, not render state.
    assert!(r1.contains(r#"id="if-0""#));
    assert!(r2.contains(r#"id="if-0""#));
}
