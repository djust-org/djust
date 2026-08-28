//! `stringformat` with an EMPTY spec renders `""` and does not panic (#2343).
//!
//! This lives in Rust as well as in
//! `python/tests/test_panic_boundary_2343.py` because the two builds fail
//! DIFFERENTLY and only one of them is exercised by the Python suite. The
//! shipped extension is a RELEASE build, where `spec.len() - 1` on an empty
//! spec wraps to `usize::MAX` and the slice panics with
//! `end byte index 18446744073709551615 is out of bounds`. `cargo test` is a
//! DEBUG build, where the same expression traps first as
//! `attempt to subtract with overflow`. Same blast radius, different message —
//! and a regression that only reappeared in debug would be invisible to a
//! suite that only ever loads the release `.so`.
//!
//! Django's answer is `""`: the filter body is `("%" + arg) % value`, so an
//! empty arg makes the format string `"%"`, and a `%` that ends the format is
//! `ValueError: incomplete format` — caught by the filter's own
//! `except (ValueError, TypeError)`. Verified against Django 5.2.16; the
//! Python file is where that comparison is run live.

use djust_core::{Context, Value};
use djust_templates::Template;

fn render(source: &str, value: Value) -> String {
    let mut context = Context::new();
    context.set("p".to_string(), value);
    Template::new(source)
        .expect("template parses")
        .render(&context)
        .expect("render succeeds")
}

/// Every arm `apply_stringformat` dispatches on carries the same
/// `&spec[..spec.len() - 1]`, so the guard belongs above the dispatch rather
/// than in the `'s'` arm the `unwrap_or('s')` default happened to select.
/// These values reach different arms once a real conversion character is
/// present, which is what makes them worth spending here.
#[test]
fn empty_spec_is_empty_for_every_value_kind() {
    for value in [
        Value::Integer(42),
        Value::Integer(-1),
        Value::Float(1.5),
        Value::String("abc".to_string()),
        Value::String(String::new()),
        Value::Bool(true),
        Value::Bool(false),
        Value::None,
        Value::Missing,
        Value::List(vec![Value::Integer(1)]),
    ] {
        assert_eq!(
            render("{{ p|stringformat:\"\" }}", value.clone()),
            "",
            "empty spec on {value:?}"
        );
    }
}

/// A non-ASCII spec exercises the byte-vs-char distinction the slice depends
/// on: `spec.len()` counts BYTES, and only a spec whose final character is
/// one byte wide can be sliced at `len() - 1` at all. Every conversion
/// character `apply_stringformat` matches is ASCII, so a spec ending in a
/// multi-byte character reaches the catch-all arm, which does not slice.
#[test]
fn a_multibyte_spec_does_not_slice_mid_character() {
    assert_eq!(
        render("{{ p|stringformat:\"中\" }}", Value::Integer(42)),
        "42"
    );
    assert_eq!(
        render("{{ p|stringformat:\"中s\" }}", Value::Integer(42)),
        "42"
    );
}

/// The guard must not swallow a spec that HAS a conversion — without this,
/// `return String::new()` for every spec would satisfy the cases above.
#[test]
fn a_real_conversion_is_unaffected() {
    assert_eq!(
        render("{{ p|stringformat:\"s\" }}", Value::Integer(42)),
        "42"
    );
    assert_eq!(
        render("{{ p|stringformat:\"d\" }}", Value::Integer(42)),
        "42"
    );
    assert_eq!(
        render("{{ p|stringformat:\"05d\" }}", Value::Integer(42)),
        "00042"
    );
    assert_eq!(
        render(
            "{{ p|stringformat:\"6s\" }}",
            Value::String("ab".to_string())
        ),
        "    ab"
    );
}
