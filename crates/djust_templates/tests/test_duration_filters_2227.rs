//! `timesince`/`timeuntil` must parse the shapes a Python datetime arrives in (#2227).
//!
//! Both filters called `DateTime::parse_from_rfc3339` and nothing else, so a
//! **naive** datetime — the normal shape under `USE_TZ = False` — did not parse
//! and the filter returned its input verbatim into the page:
//!
//! ```text
//! Django: '2 hours'        djust: '2026-08-25T12:16:36.074891'
//! ```
//!
//! Third instance of one class in three releases: `date`/`time` learned
//! datetimes in #2203 and bare times in #2216, each time by extending the parse
//! list of the filter in front of us while the neighbouring filters with their
//! own parse went unchecked. The cure is one shared `parse_serialized_datetime`
//! rather than a third correct copy (#1646); its shape coverage is pinned in
//! `filters.rs::parse_shape_tests_2227`.
//!
//! ## Determinism
//!
//! These cases compare against "now", so each offset is chosen to sit **well
//! inside** its unit bucket rather than near a boundary — a value 5 days and 12
//! hours old renders `5 days` for the next twelve hours. Asserting on a value
//! two hours old would be a coin flip near the boundary, which is the flaky
//! shape #1795 exists to prevent.

use djust_core::{Context, Value};
use djust_templates::Template;

/// Django joins a count and its unit with U+00A0 so the pair never wraps
/// (#2228). Asserting the codepoint matters: a test written with an ordinary
/// space reads identically and passes while shipping the wrong byte.
const NBSP: char = '\u{a0}';

fn render(source: &str, value: &str) -> String {
    let mut ctx = Context::new();
    ctx.set("v".to_string(), Value::String(value.to_string()));
    Template::new(source)
        .expect("template should parse")
        .render(&ctx)
        .expect("template should render")
}

/// A naive datetime `hours` in the past, in the shape Python's `.isoformat()`
/// produces — no offset, which is exactly what did not parse.
fn naive_hours_ago(hours: i64) -> String {
    (chrono::Local::now().naive_local() - chrono::Duration::hours(hours))
        .format("%Y-%m-%dT%H:%M:%S%.f")
        .to_string()
}

fn aware_hours_ago(hours: i64) -> String {
    (chrono::Utc::now() - chrono::Duration::hours(hours)).to_rfc3339()
}

#[test]
fn a_naive_datetime_is_measured_instead_of_echoed() {
    // The reported bug. Pre-fix this rendered the ISO string itself.
    let out = render("{{ v|timesince }}", &naive_hours_ago(30));
    assert!(
        out.starts_with(&format!("1{NBSP}day")),
        "expected a measured duration, got {out:?}"
    );
    assert!(!out.contains('T'), "the raw input leaked into the output");
}

#[test]
fn a_naive_datetime_is_compared_against_local_now_not_utc() {
    // The half of the fix that produces a PLAUSIBLE wrong answer rather than an
    // obvious one, so it would have survived review. Django's `timesince`
    // compares a naive value against `datetime.now()` — naive LOCAL time.
    // Comparing against UTC instead is off by the local offset: measured, a
    // naive datetime two hours old reported SIX hours in a UTC-4 zone.
    //
    // Written to fail for any non-zero offset: 30 hours is `1 day` only if the
    // comparison baseline is right. In a UTC-4 zone an off-by-offset error
    // gives 34 hours, still `1 day` — so the sharper case is just below the
    // boundary, where any offset at all crosses it.
    let just_under_a_day = render("{{ v|timesince }}", &naive_hours_ago(23));
    assert_eq!(
        just_under_a_day,
        format!("23{NBSP}hours"),
        "a naive value must be compared against LOCAL now; an off-by-offset \
         comparison lands in a different bucket. Got {just_under_a_day:?}"
    );
}

#[test]
fn an_aware_datetime_still_works() {
    // Guard: the path that already worked must not have moved.
    assert!(render("{{ v|timesince }}", &aware_hours_ago(30)).starts_with(&format!("1{NBSP}day")));
    assert_eq!(
        render("{{ v|timesince }}", &aware_hours_ago(5)),
        format!("5{NBSP}hours")
    );
}

#[test]
fn a_date_only_value_is_measured_too() {
    // A `DateField` is one of `timesince`'s commonest inputs, and it did not
    // parse either. Yesterday is between 24 and 48 hours ago whatever the time
    // of day, so `1 day` holds all day.
    let yesterday = (chrono::Local::now().date_naive() - chrono::Duration::days(1))
        .format("%Y-%m-%d")
        .to_string();
    // Depth-2 adds an hours component that moves through the day, so pin the
    // leading unit rather than the whole string (#1795).
    assert!(
        render("{{ v|timesince }}", &yesterday).starts_with(&format!("1{NBSP}day")),
        "a DateField is one of timesince's commonest inputs and must parse"
    );
}

#[test]
fn a_bare_time_is_refused_rather_than_measured_from_1970() {
    // The trap the `allow_time_only` flag exists for. A bare time is anchored
    // on an arbitrary epoch date inside the formatter, so a parser that
    // accepted it here would report the decades since 1970 — a confidently
    // wrong answer. Django raises; djust falls back to its input, which is the
    // fail-soft convention for an unparseable value.
    let out = render("{{ v|timesince }}", "09:30:00");
    assert_eq!(out, "09:30:00", "got {out:?}");
    assert!(
        !out.contains("year"),
        "a bare time must never be measured against the epoch anchor"
    );
}

#[test]
fn timeuntil_gets_the_same_parse_and_the_same_refusal() {
    // Both filters had the identical defect, so fixing one and not the other
    // would have been the very drift this consolidation retires.
    assert_eq!(
        render("{{ v|timeuntil }}", &naive_hours_ago(30)),
        format!("0{NBSP}minutes")
    );
    assert_eq!(render("{{ v|timeuntil }}", "09:30:00"), "09:30:00");

    let future = (chrono::Local::now().naive_local() + chrono::Duration::hours(30))
        .format("%Y-%m-%dT%H:%M:%S%.f")
        .to_string();
    assert!(render("{{ v|timeuntil }}", &future).starts_with(&format!("1{NBSP}day")));
}

#[test]
fn a_past_value_reads_zero_for_timeuntil() {
    // Django's floor, and unchanged by this PR — pinned because the sign of
    // the duration is now computed by a shared helper rather than inline, and
    // negating the wrong side is an easy slip.
    assert_eq!(
        render("{{ v|timeuntil }}", &aware_hours_ago(5)),
        format!("0{NBSP}minutes")
    );
}
