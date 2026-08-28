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
use indexmap::IndexMap;

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
    // `Value::None`, not `Missing` (#2203): the filter fires for a present
    // Python None. `Missing` is an ABSENT variable, which Django renders as ""
    // before the filter ever runs. This test is about ARGUMENT resolution, so
    // it needs a value that actually triggers the fallback.
    let ctx = ctx_with("e", Value::None, Value::String("X".into()));
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
fn an_unresolvable_bare_identifier_raises() {
    // #2202 left this as a DELIBERATE DIVERGENCE: Django raises
    // `VariableDoesNotExist` for an unresolvable filter argument, and djust
    // fell back to the argument's raw text, so `{{ n|pluralize:es }}` rendered
    // the literal word "es" and `{{ e|default:nosuchvar }}` rendered
    // "nosuchvar". The defence was that raising would turn a silent
    // wrong-output bug into a site-wide 500 on upgrade.
    //
    // #2328 closed it. Measurement settled the scope — Django raises for ALL
    // TWENTY-NINE argument-taking built-ins and djust for none of them — and
    // the 500 argument turned out not to hold: `LiveViewConsumer.receive`
    // catches a render error and sends a safe error frame without dropping the
    // socket, so the degradation decision is already made, once, at the
    // transport, where it can be environment-aware.
    let mut c = Context::new();
    c.set("n".to_string(), Value::Integer(2));
    let t = Template::new("{{ n|pluralize:es }}").expect("template should parse");
    let err = t.render(&c).expect_err("an unresolvable arg must raise");
    assert!(
        err.to_string().contains("does not resolve"),
        "message should name the failure: {err}"
    );
    assert!(
        err.to_string().contains("es"),
        "message should name the identifier: {err}"
    );

    let mut c2 = Context::new();
    c2.set("e".to_string(), Value::String(String::new()));
    assert!(Template::new("{{ e|default:nosuchvar }}")
        .expect("template should parse")
        .render(&c2)
        .is_err());
}

#[test]
fn a_dotted_path_arg_resolves() {
    // Django resolves dotted paths in filter args. This is the exact shape the
    // djust.org bug had: `{{ post.featured_image_alt|default:post.title }}`.
    let mut c = Context::new();
    let mut post = IndexMap::new();
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

// ---------------------------------------------------------------------------
// #2328 — the literal escape hatch that keeps the raise above from firing on
// every numeric argument. Django's `Variable.__init__` decides this, and each
// row here was measured against Django 5.2 rather than reasoned about.
// ---------------------------------------------------------------------------

/// Every spelling Django resolves WITHOUT a context lookup.
///
/// `7.` is the sharp one: `float("7.")` succeeds in Python, and Django rejects
/// it anyway with an explicit `if var[-1] == ".": raise ValueError`. Dropping
/// that guard would silently make a trailing dot a literal here and a lookup
/// there.
#[test]
fn a_literal_argument_is_not_a_lookup() {
    let mut c = Context::new();
    c.set("a".to_string(), Value::Integer(5));
    for arg in ["7", "-3", "+3", "7.5", ".5", "1e3", "1_0", "07"] {
        let source = format!("{{{{ a|add:{arg} }}}}");
        let t = Template::new(&source).expect("template should parse");
        assert!(
            t.render(&c).is_ok(),
            "{arg} is a Django literal and must not be looked up"
        );
    }
}

/// The near-misses, which ARE lookups in Django and must raise.
#[test]
fn a_non_literal_argument_is_a_lookup() {
    let mut c = Context::new();
    c.set("a".to_string(), Value::Integer(5));
    for arg in ["7.", "0x10", "nan", "inf", "es"] {
        let source = format!("{{{{ a|add:{arg} }}}}");
        let t = Template::new(&source).expect("template should parse");
        assert!(
            t.render(&c).is_err(),
            "{arg} is a Django lookup and must raise"
        );
    }
}

/// `True` / `False` / `None` are not literals — they are keys of Django's
/// `Context.builtins`, so they RESOLVE. djust's `Context` does not carry them
/// (a separate, pre-existing gap), and they are exempted from the raise so
/// this fix does not turn that wrong answer into a hard error.
#[test]
fn the_context_builtins_do_not_raise() {
    let mut c = Context::new();
    c.set("a".to_string(), Value::Integer(5));
    for arg in ["True", "False", "None"] {
        let source = format!("{{{{ a|add:{arg} }}}}");
        let t = Template::new(&source).expect("template should parse");
        assert!(t.render(&c).is_ok(), "{arg} must not raise");
    }
}

/// The chokepoint's `Raise` arm, through the real render path.
#[test]
fn an_unparseable_numeric_argument_raises_naming_the_filter() {
    let mut c = Context::new();
    c.set("p".to_string(), Value::String("ab".into()));
    let t = Template::new(r#"{{ p|center:"nope" }}"#).expect("template should parse");
    let err = t.render(&c).expect_err("int(\"nope\") has no answer");
    let message = err.to_string();
    assert!(
        message.contains("center"),
        "should name the filter: {message}"
    );
    assert!(
        message.contains("nope"),
        "should name the argument: {message}"
    );
}

/// The chokepoint's `ReturnInput` arm, which must NOT raise on the same input.
/// Both arms reachable, or the policy parameter is decorative.
#[test]
fn a_return_input_filter_gives_its_value_back() {
    let mut c = Context::new();
    c.set("p".to_string(), Value::String("abcdefghij".into()));
    assert_eq!(render(r#"{{ p|truncatechars:"nope" }}"#, &c), "abcdefghij");
}

/// `int()`'s spellings, which every scattered `parse::<usize>` refused.
#[test]
fn the_chokepoint_carries_pythons_int_spellings() {
    let mut c = Context::new();
    c.set("p".to_string(), Value::String("ab".into()));
    assert_eq!(render(r#"{{ p|ljust:" 5 " }}"#, &c), "ab   ");
    assert_eq!(render(r#"{{ p|ljust:"+5" }}"#, &c), "ab   ");
    assert_eq!(render(r#"{{ p|ljust:"1_0" }}"#, &c), "ab        ");
}

/// A bare float truncates (`int(2.7)`); a QUOTED one raises (`int("2.7")`).
/// One character of template syntax separates them.
#[test]
fn the_quoting_hint_separates_truncation_from_a_raise() {
    let mut c = Context::new();
    c.set("p".to_string(), Value::String("aa bb".into()));
    assert_eq!(render("{{ p|wordwrap:2.7 }}", &c), "aa\nbb");
    assert!(Template::new(r#"{{ p|wordwrap:"2.7" }}"#)
        .expect("template should parse")
        .render(&c)
        .is_err());
}

/// `format!("{s:<width$}")` PANICS past Rust's formatter width cap, which is a
/// `PanicException` across the PyO3 boundary rather than an error any caller
/// can handle. Both pad filters build their padding explicitly now.
#[test]
fn a_large_width_pads_instead_of_panicking() {
    let mut c = Context::new();
    c.set("p".to_string(), Value::String("ab".into()));
    assert_eq!(render(r#"{{ p|ljust:"70000" }}"#, &c).len(), 70000);
    assert_eq!(render(r#"{{ p|rjust:"70000" }}"#, &c).len(), 70000);
}
