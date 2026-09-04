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
fn a_datetime_local_form_value_is_parsed() {
    // `<input type="datetime-local">` submits `YYYY-MM-DDTHH:MM` — `T`
    // separator, seconds omitted. This is the no-seconds case that actually
    // occurs in practice.
    //
    // The first pass of this fix got it backwards: it accepted
    // `"2026-08-22 14:30"` (space, no seconds), which Python never produces —
    // `str(datetime(...,14,30))` is `"2026-08-22 14:30:00"` and
    // `str(time(14,30))` is `"14:30:00"`, both with seconds — while leaving the
    // `T` form unhandled. Caught by disconfirming the comment that justified it.
    let ctx = ctx_with("v", Value::String("2026-08-22T14:30".into()));
    assert_eq!(render(r#"{{ v|date:"H:i" }}"#, &ctx), "14:30");
    assert_eq!(render(r#"{{ v|date:"Y-m-d" }}"#, &ctx), "2026-08-22");
}

#[test]
fn a_timezone_aware_datetime_keeps_its_offset() {
    // Django defaults to USE_TZ=True, so an aware datetime is the common case.
    //
    // A NON-UTC offset is deliberate. The first version used `+00:00`, which
    // pins nothing: the naive branch's `.and_utc()` also yields `+00:00`, so
    // the assertion passed identically whether the offset was honoured or
    // discarded — decorative by the #1859 test ("would this go red if the thing
    // it pins actually drifted?"). With `+05:00` the two answers differ.
    //
    // chrono's `parse_from_rfc3339` accepts a SPACE separator, so an
    // offset-bearing string never reaches the naive parsers; and if it did,
    // `NaiveDateTime::parse_from_str` rejects the trailing offset as `TooLong`,
    // so the failure mode is fail-soft rather than a silent wrong time.
    let ctx = ctx_with("v", Value::String("2026-08-22 14:30:00+05:00".into()));
    assert_eq!(render(r#"{{ v|time:"H:i" }}"#, &ctx), "14:30");
    assert_eq!(render(r#"{{ v|date:"Y-m-d" }}"#, &ctx), "2026-08-22");

    // Separately and pre-existing: Django's `date`/`time` are
    // `expects_localtime=True` and convert to `settings.TIME_ZONE`, which
    // `format_date` never does — it reads wall-clock fields directly. Harmless
    // for every format code this engine supports (there is no timezone code),
    // but it means "Django parity for aware datetimes" holds only when
    // TIME_ZONE is UTC. Out of scope here; recorded so the claim is not
    // overstated.
}

#[test]
fn an_unparseable_value_renders_djangos_own_answer() {
    // This asserted the input came back — "the fail-soft contract" — until
    // #2359 measured what Django does. Django's `date` ends
    // `except AttributeError: return ""`, so returning the input was the more
    // permissive direction: it put unparsed upstream data on the page for
    // every `{{ p|date }}` over a non-date.
    let ctx = ctx_with("v", Value::String("not a date at all".into()));
    assert_eq!(render(r#"{{ v|date:"Y-m-d" }}"#, &ctx), "");

    // Still fail-SOFT, which is the half of the old contract that was right:
    // a parse failure renders, it does not raise. And what it renders is not
    // unconditionally empty — a format with no specifier never touches the
    // value, so its literal text comes back (`django_literal_only_format`).
    assert_eq!(render(r#"{{ v|date:"1-1" }}"#, &ctx), "1-1");
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

#[test]
fn add_does_not_overflow() {
    // Python's ints are arbitrary-precision, so Django cannot overflow here.
    // `i64` can, and plain `+` PANICS in a debug build while silently WRAPPING
    // in release — `{{ max|add:1 }}` produced a negative number. Widening the
    // coercion to floats and numeric strings widened that surface:
    // `f64::INFINITY as i64` saturates to `i64::MAX`, so `{{ 5|add:inf }}`
    // wrapped to -9223372036854775804 in release and panicked in debug.
    //
    // This test would PANIC rather than fail without `checked_add`, since the
    // test profile has debug assertions on — which is precisely how it was
    // found.
    //
    // The give-up POINT has moved twice. #2253 made the arithmetic `i128` and
    // carried a sum outside `i64` as exact digits, so `i64::MAX + 1` became the
    // answer Django gives instead of the input unchanged. #2260 removed the
    // width entirely — `add_int_digits` is arbitrary-precision, as Python's
    // `int` is — so there is no overflow left to guard, only inputs `int()`
    // itself refuses. What has not moved, and is what this test is for, is that
    // no width may ever produce a wrapped or fabricated number.
    let ctx = ctx_with("v", Value::Integer(i64::MAX));
    let out = render("{{ v|add:1 }}", &ctx);
    assert!(
        !out.starts_with('-'),
        "adding 1 to i64::MAX produced a negative number: {out}"
    );
    assert_eq!(out, "9223372036854775808", "i64::MAX + 1, exactly");

    // Past i128 there IS an answer now, and it is Django's: 251 digits, verified
    // against a live `{{ v|add:1 }}` render. Before #2260 this returned the
    // input unchanged (`1e+250`), which the comment above called a fail-soft and
    // which was really the width showing through.
    let far = ctx_with("v", Value::Decimal("1E+250".to_string()));
    let sum = render("{{ v|add:1 }}", &far);
    assert_eq!(
        sum.len(),
        251,
        "10**250 + 1 has 251 digits, got {}",
        sum.len()
    );
    assert!(sum.starts_with('1') && sum.ends_with('1'), "got {sum}");
    assert_eq!(sum.matches('0').count(), 249);

    // The remaining fail-soft is an operand `int()` itself refuses. Django
    // RAISES here (`str(int)` past `sys.get_int_max_str_digits()` is a
    // `ValueError` its `except` does not catch); djust renders rather than
    // 500ing, and the digits are never fabricated.
    //
    // WHAT it renders moved in #2359, from the input to `""` — the answer
    // Django's own third branch gives for every value that reaches it without
    // raising. This width is past even that, so there is no Django output to
    // agree with; `""` is the less permissive of the two things djust could
    // put on the page.
    let too_wide = ctx_with("v", Value::Decimal("1E+5000".to_string()));
    assert_eq!(render("{{ v|add:1 }}", &too_wide), "");

    // Same via the widened float path.
    let mut c2 = Context::new();
    c2.set("v".to_string(), Value::Integer(5));
    c2.set("inf".to_string(), Value::Float(f64::INFINITY));
    // Django's int(inf) raises OverflowError; no fabricated sum may render.
    let error = Template::new("{{ v|add:inf }}")
        .unwrap()
        .render(&c2)
        .unwrap_err();
    assert!(error.to_string().contains("OverflowError"));
}

// ---------------------------------------------------------------------------
// Cases added after Stage 11 review — each closes a gap the first pass left.
// ---------------------------------------------------------------------------

#[test]
fn a_datetime_with_microseconds_is_parsed() {
    // The shape production actually emits. djust serializes datetimes with
    // `.isoformat()` (python/djust/serialization.py:311), and
    // `datetime.now().isoformat()` carries microseconds —
    // "2026-08-23T01:31:25.488631". None of the first pass's four formats had a
    // fractional-seconds directive, and chrono rejects trailing input, so the
    // parse failed and the filter returned its input verbatim: exactly the bug
    // this file exists to fix, still live for every `auto_now_add` timestamp.
    //
    // Aware datetimes were fine already (RFC3339 handles fractions), which is
    // why every other test here passed.
    for v in [
        "2026-08-22T14:30:00.123456",
        "2026-08-22 14:30:00.123456",
        "2026-08-22T14:30:00.5",
    ] {
        let ctx = ctx_with("v", Value::String(v.into()));
        assert_eq!(
            render(r#"{{ v|date:"Y-m-d" }}"#, &ctx),
            "2026-08-22",
            "for {v}"
        );
        assert_eq!(render(r#"{{ v|time:"H:i" }}"#, &ctx), "14:30", "for {v}");
    }
}

#[test]
fn add_concatenates_a_quoted_float_string_like_django() {
    // Python's `int("1.5")` RAISES, so Django falls through to concatenation:
    // `{{ "1.5"|add:"1.5" }}` is "1.51.5", not 3. A first pass accepted "1.5"
    // via an f64 fallback and returned 2 — a FABRICATED number where Django
    // produces text, which is worse than the inert wrong answer it replaced.
    //
    // Quoting is what separates the two: a quoted "1.5" is a string to int(),
    // an unquoted 1.5 is a float literal.
    let ctx = ctx_with("v", Value::String("1.5".into()));
    assert_eq!(render(r#"{{ v|add:"1.5" }}"#, &ctx), "1.51.5");
}

#[test]
fn add_truncates_a_float_value_like_int() {
    // The mechanism behind this PR's largest silent change to rendered output:
    // `{{ 1.5|add:2 }}` was 3.5 before and is 3 now. Django says 3, so the new
    // value is correct — but review found NOTHING pinned it. Neutering the
    // Float arm of `as_int` left the whole suite green.
    let ctx = ctx_with("v", Value::Float(1.5));
    assert_eq!(render("{{ v|add:2 }}", &ctx), "3");
}

#[test]
fn add_coerces_bools_like_int() {
    // `int(True)` is 1, so Django's first branch handles bools.
    let mut c = Context::new();
    c.set("t".to_string(), Value::Bool(true));
    c.set("f".to_string(), Value::Bool(false));
    assert_eq!(render("{{ t|add:1 }}", &c), "2");
    assert_eq!(render("{{ f|add:1 }}", &c), "1");
}

#[test]
fn truncatechars_zero_yields_nothing() {
    // Django's `Truncator.chars` opens with `if length <= 0: return ""`, so a
    // limit of 0 yields nothing — not a bare ellipsis.
    let ctx = ctx_with("v", Value::String("abcdefghij".into()));
    assert_eq!(render("{{ v|truncatechars:0 }}", &ctx), "");
    // Guard the neighbour, which Django DOES render as a lone ellipsis.
    assert_eq!(render("{{ v|truncatechars:1 }}", &ctx), "…");
}

#[test]
fn the_html_truncators_use_the_same_ellipsis_as_their_plain_twins() {
    // #1646: `truncatechars` and `truncatechars_html` are the same filter for
    // different input, and disagreed on the same page — the _html pair kept
    // both halves of the bug (wrong glyph AND a three-character reservation).
    //
    // The reservation is the subtle one: `"…".len()` is 3 BYTES, exactly like
    // `"..."`, so swapping the constant alone silently preserves the old
    // arithmetic. This asserts the CHARACTER count, which is what would catch
    // that.
    let ctx = ctx_with(
        "v",
        Value::String("<p>Hello <b>world</b> this is long</p>".into()),
    );
    let out = render("{{ v|truncatechars_html:11 }}", &ctx);
    assert!(out.contains('…'), "expected the ellipsis char in {out:?}");
    assert!(
        !out.contains("..."),
        "three-dot ellipsis survived in {out:?}"
    );

    let ctx2 = ctx_with(
        "v",
        Value::String("<p>one two <b>three four</b> five six</p>".into()),
    );
    let out2 = render("{{ v|truncatewords_html:3 }}", &ctx2);
    assert!(out2.contains('…'), "expected the ellipsis char in {out2:?}");
    assert!(
        !out2.contains("..."),
        "three-dot ellipsis survived in {out2:?}"
    );
}
