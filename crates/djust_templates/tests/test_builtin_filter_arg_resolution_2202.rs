//! Regression test for issue #2202 — built-in filter arguments must resolve
//! bare identifiers against the context, the way Django does.
//!
//! Django's `Variable`/`FilterExpression` machinery resolves a filter argument
//! as a *variable* unless it is quoted: `{{ x|default:fallback }}` looks up
//! `fallback` in the context, and only `{{ x|default:"fallback" }}` is the
//! literal string. djust's built-in filters used the argument as a raw string
//! and never consulted the context, so `{{ x|default:fallback }}` rendered the
//! literal text `fallback`.
//!
//! The failure is silent: the template renders, nothing raises, and the output
//! looks plausible. It was found on djust.org, where
//! `{{ post.featured_image_alt|default:post.title }}` shipped `alt="post.title"`
//! on every post with an empty alt — an accessibility defect nobody noticed.
//!
//! **Custom filters were already correct** (`filter_registry.rs` resolves bare
//! identifiers via `Context::resolve`), so this was parallel-path drift (#1646)
//! on the filter-argument axis: two implementations of "resolve a filter arg",
//! one right and one wrong. The fix routes built-ins through the same
//! resolution rather than adding a second correct copy.
//!
//! These cases drive the REAL render path (`Template::new(...).render(&ctx)`)
//! rather than calling `apply_filter` directly, because `arg_was_quoted` — the
//! flag that distinguishes a quoted literal from an identifier — is computed in
//! `renderer.rs` and threaded down. A direct `apply_filter` call would bypass
//! the very plumbing under test.

use djust_core::{Context, Value};
use djust_templates::Template;

fn render(source: &str, ctx: &Context) -> String {
    let t = Template::new(source).expect("template should parse");
    t.render(ctx).expect("template should render")
}

/// Context carrying, for each case, the value under test plus the argument
/// held in a context variable named `v`.
fn ctx_with(value_key: &str, value: Value, arg: Value) -> Context {
    let mut c = Context::new();
    c.set(value_key.to_string(), value);
    c.set("v".to_string(), arg);
    c
}

// ---------------------------------------------------------------------------
// The built-ins whose literal-arg behaviour already matches Django, so a
// bare-identifier failure isolates cleanly to argument resolution.
//
// TEN filters are fixed in total. The tenth, `date`, is pinned in
// `python/tests/test_template_filters.py::TestBuiltinFilterArgResolution2202`
// because it needs a real `datetime` object: `Value` has no date variant, and
// an ISO string passes through the filter unformatted, so a Rust-level case
// would assert nothing about argument resolution.
// ---------------------------------------------------------------------------

#[test]
fn default_resolves_a_bare_identifier_arg() {
    let ctx = ctx_with("e", Value::String(String::new()), Value::String("X".into()));
    assert_eq!(render("{{ e|default:v }}", &ctx), "X");
}

#[test]
fn default_if_none_resolves_a_bare_identifier_arg() {
    let ctx = ctx_with("e", Value::Null, Value::String("X".into()));
    assert_eq!(render("{{ e|default_if_none:v }}", &ctx), "X");
}

#[test]
fn add_resolves_a_bare_identifier_arg() {
    let ctx = ctx_with("a", Value::Integer(5), Value::Integer(7));
    assert_eq!(render("{{ a|add:v }}", &ctx), "12");
}

#[test]
fn join_resolves_a_bare_identifier_arg() {
    // The sharpest case: pre-fix this rendered "pvq" — the identifier's own
    // text spliced in as the separator.
    let ctx = ctx_with(
        "a",
        Value::List(vec![Value::String("p".into()), Value::String("q".into())]),
        Value::String("-".into()),
    );
    assert_eq!(render("{{ a|join:v }}", &ctx), "p-q");
}

#[test]
fn cut_resolves_a_bare_identifier_arg() {
    // Pre-fix this silently no-opped, returning the input unchanged.
    let ctx = ctx_with(
        "a",
        Value::String("hello".into()),
        Value::String("l".into()),
    );
    assert_eq!(render("{{ a|cut:v }}", &ctx), "heo");
}

#[test]
fn yesno_resolves_a_bare_identifier_arg() {
    let ctx = ctx_with("a", Value::Bool(true), Value::String("Y,N".into()));
    assert_eq!(render("{{ a|yesno:v }}", &ctx), "Y");
}

#[test]
fn floatformat_resolves_a_bare_identifier_arg() {
    // Not 3.14159 — clippy::approx_constant rejects PI-shaped literals.
    let ctx = ctx_with("a", Value::Float(1.23456), Value::Integer(2));
    assert_eq!(render("{{ a|floatformat:v }}", &ctx), "1.23");
}

#[test]
fn pluralize_resolves_a_bare_identifier_arg() {
    let ctx = ctx_with("a", Value::Integer(2), Value::String("es".into()));
    assert_eq!(render("{{ a|pluralize:v }}", &ctx), "es");
}

#[test]
fn stringformat_resolves_a_bare_identifier_arg() {
    let ctx = ctx_with("a", Value::Integer(42), Value::String("03d".into()));
    assert_eq!(render("{{ a|stringformat:v }}", &ctx), "042");
}

// ---------------------------------------------------------------------------
// Guards — the behaviours the fix must NOT change.
// ---------------------------------------------------------------------------

#[test]
fn a_quoted_arg_stays_a_literal() {
    // A quoted argument must never be looked up, even when a context variable
    // of that name exists. Without this, the fix would break every template
    // whose literal happens to collide with a context key.
    let mut c = Context::new();
    c.set("e".to_string(), Value::String(String::new()));
    c.set(
        "X".to_string(),
        Value::String("WRONG - resolved a literal".into()),
    );
    assert_eq!(render(r#"{{ e|default:"X" }}"#, &c), "X");
    assert_eq!(render("{{ e|default:'X' }}", &c), "X");
}

#[test]
fn an_unresolvable_bare_identifier_falls_back_to_its_raw_text() {
    // This is a DELIBERATE DIVERGENCE from Django, not parity. Django raises
    // `VariableDoesNotExist` for an unresolvable filter argument (verified:
    // `{{ n|pluralize:es }}` with no `es` in context raises rather than
    // rendering). djust falls back to the argument's raw text instead.
    //
    // Kept because `{{ n|pluralize:es }}` "works" today only by that accident,
    // templates in the wild rely on it, and it is what djust's own
    // custom-filter path already does. Raising here would turn a silent
    // wrong-output bug into a site-wide 500 on upgrade — a strictly worse
    // trade for a parity fix.
    let mut c = Context::new();
    c.set("n".to_string(), Value::Integer(2));
    assert_eq!(render("{{ n|pluralize:es }}", &c), "es");

    let mut c2 = Context::new();
    c2.set("e".to_string(), Value::String(String::new()));
    assert_eq!(render("{{ e|default:nosuchvar }}", &c2), "nosuchvar");
}

#[test]
fn a_dotted_path_arg_resolves() {
    // Django resolves dotted paths in filter args. This is the exact shape the
    // djust.org bug had: `{{ post.featured_image_alt|default:post.title }}`.
    let mut c = Context::new();
    let mut post = std::collections::HashMap::new();
    post.insert("alt".to_string(), Value::String(String::new()));
    post.insert("title".to_string(), Value::String("The Title".into()));
    c.set("post".to_string(), Value::Object(post));
    assert_eq!(render("{{ post.alt|default:post.title }}", &c), "The Title");
}

#[test]
fn a_numeric_literal_arg_still_works() {
    // Bare numerics are not identifiers; they must not be context-resolved
    // into nothing. `{{ a|add:7 }}` has no `7` key in context.
    let mut c = Context::new();
    c.set("a".to_string(), Value::Integer(5));
    assert_eq!(render("{{ a|add:7 }}", &c), "12");
    c.set("f".to_string(), Value::Float(1.23456));
    assert_eq!(render("{{ f|floatformat:2 }}", &c), "1.23");
}
