//! Django-parity gaps in built-in filters that persist with a LITERAL argument (#2203).
//!
//! These are distinct from #2202, which fixed built-ins ignoring a *bare
//! identifier* argument. Everything here fails with a plain literal too, so
//! argument resolution is not involved.
//!
//! ## `date` / `time` cannot parse a naive datetime — the headline
//!
//! `format_date` accepts RFC3339 or a bare `%Y-%m-%d` date, and `format_time`
//! delegates straight to it. A Python `datetime` reaches the filter as
//! `"2026-08-22 14:30:00"` — space-separated, no offset — which matches
//! neither, so the parse fails and the filter returns its input unchanged.
//!
//! That breaks the single most common use of both filters. A `DateField`
//! happens to work (it stringifies to `2026-08-22`), which is why this
//! survived: the failure is invisible unless the value is a `datetime`.
//!
//! ## `truncatewords` / `truncatechars` use the wrong ellipsis
//!
//! Django appends `…` (U+2026, one character). djust appends `...` (three).
//! For `truncatechars` this also changes the arithmetic: Django reserves ONE
//! character for the ellipsis inside the limit, djust reserves three, so
//! `truncatechars:5` yields `abcd…` in Django and `ab...` here.
//!
//! ## `add` implements only one of Django's three branches
//!
//! Django is `int(value) + int(arg)`, falling back to `value + arg`, falling
//! back to `""`. djust parses the argument as `i64` and defaults to **0** on
//! failure, so a float argument silently adds nothing and string concatenation
//! does not happen at all.
//!
//! ## Deliberately NOT covered here
//!
//! `divisibleby` (`true` vs `True`), `slice` (`[List]`), and a `Null` argument
//! (`""` vs `None`) are not filter bugs — they are `impl Display for Value`,
//! which governs EVERY `{{ var }}`, not just filter output. Changing it is a
//! design decision with a real back-compat hazard (`var x = {{ flag }};`
//! renders valid JS today and would not under Django's `True`), so it is
//! tracked separately rather than smuggled in behind a filter fix.

use djust_core::{Context, Value};
use djust_templates::Template;

fn render(source: &str, ctx: &Context) -> String {
    let t = Template::new(source).expect("template should parse");
    t.render(ctx).expect("template should render")
}

fn ctx_with(key: &str, value: Value) -> Context {
    let mut c = Context::new();
    c.set(key.to_string(), value);
    c
}

/// How a Python `datetime` arrives at the filter.
const NAIVE_DATETIME: &str = "2026-08-22 14:30:00";

// ---------------------------------------------------------------------------
// date / time on a naive datetime
// ---------------------------------------------------------------------------

#[test]
fn date_formats_a_naive_datetime() {
    let ctx = ctx_with("v", Value::String(NAIVE_DATETIME.into()));
    assert_eq!(render(r#"{{ v|date:"Y-m-d" }}"#, &ctx), "2026-08-22");
}

#[test]
fn time_formats_a_naive_datetime() {
    let ctx = ctx_with("v", Value::String(NAIVE_DATETIME.into()));
    assert_eq!(render(r#"{{ v|time:"H:i" }}"#, &ctx), "14:30");
}

#[test]
fn date_can_render_time_components_of_a_naive_datetime() {
    // Django's `date` is not date-only — it accepts time codes too.
    let ctx = ctx_with("v", Value::String(NAIVE_DATETIME.into()));
    assert_eq!(render(r#"{{ v|date:"H:i" }}"#, &ctx), "14:30");
}

#[test]
fn a_date_only_value_still_works() {
    // Guard: the existing `%Y-%m-%d` path must keep working. It is why this
    // bug went unnoticed — a DateField renders correctly.
    let ctx = ctx_with("v", Value::String("2026-08-22".into()));
    assert_eq!(render(r#"{{ v|date:"Y-m-d" }}"#, &ctx), "2026-08-22");
}

#[test]
fn an_rfc3339_value_still_works() {
    // Guard: the original accepted format.
    let ctx = ctx_with("v", Value::String("2026-08-22T14:30:00+00:00".into()));
    assert_eq!(render(r#"{{ v|date:"Y-m-d" }}"#, &ctx), "2026-08-22");
}

#[test]
fn an_unparseable_value_is_still_returned_unchanged() {
    // Guard: the fail-soft contract. A parse failure must keep returning the
    // input rather than raising or emitting an empty string.
    let ctx = ctx_with("v", Value::String("not a date at all".into()));
    assert_eq!(render(r#"{{ v|date:"Y-m-d" }}"#, &ctx), "not a date at all");
}

// ---------------------------------------------------------------------------
// truncate ellipsis
// ---------------------------------------------------------------------------

#[test]
fn truncatewords_uses_djangos_ellipsis() {
    let ctx = ctx_with("v", Value::String("one two three".into()));
    assert_eq!(render("{{ v|truncatewords:2 }}", &ctx), "one two …");
}

#[test]
fn truncatechars_uses_djangos_ellipsis_and_arithmetic() {
    // Django reserves ONE char for `…` inside the limit: 4 chars + `…` = 5.
    let ctx = ctx_with("v", Value::String("abcdefghij".into()));
    assert_eq!(render("{{ v|truncatechars:5 }}", &ctx), "abcd…");
}

#[test]
fn truncation_below_the_limit_is_untouched() {
    // Guard: no ellipsis when nothing was cut.
    let ctx = ctx_with("v", Value::String("one two".into()));
    assert_eq!(render("{{ v|truncatewords:5 }}", &ctx), "one two");
    assert_eq!(render("{{ v|truncatechars:99 }}", &ctx), "one two");
}

// ---------------------------------------------------------------------------
// add
// ---------------------------------------------------------------------------

#[test]
fn add_coerces_a_float_argument_like_django() {
    // Django: int(5) + int(1.5) == 6. djust parsed i64, failed, and added 0.
    let ctx = ctx_with("v", Value::Integer(5));
    assert_eq!(render("{{ v|add:1.5 }}", &ctx), "6");
}

#[test]
fn add_concatenates_strings_when_the_int_branch_fails() {
    let ctx = ctx_with("v", Value::String("a".into()));
    assert_eq!(render(r#"{{ v|add:"b" }}"#, &ctx), "ab");
}

#[test]
fn add_coerces_numeric_strings_before_concatenating() {
    // Django tries the INT branch first, so "4" + "3" is 7, not "43".
    let ctx = ctx_with("v", Value::String("4".into()));
    assert_eq!(render(r#"{{ v|add:"3" }}"#, &ctx), "7");
}

#[test]
fn add_still_adds_plain_integers() {
    // Guard: the one case that already worked.
    let ctx = ctx_with("v", Value::Integer(5));
    assert_eq!(render("{{ v|add:2 }}", &ctx), "7");
}
