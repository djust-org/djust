//! `date`/`time` on a bare `datetime.time` (#2216).
//!
//! Every expectation was taken from a live Django 5.2 render — including the
//! enumeration of which of the 38 format characters a bare time supports, which
//! does not follow from the docs and was produced by running all of them.
//!
//! ## The bug
//!
//! No parse branch matched a time-only string, so `date`/`time` returned their
//! **input verbatim**: `{{ v|time:"H:i" }}` on a `TimeField` rendered
//! `23:30:00` where Django renders `23:30`. Exactly the class #2203 fixed for
//! datetimes, still live for a different type — the format list carried four
//! datetime shapes and one date-only shape and no time-only shape at all.
//!
//! It hid well: for `H:i:s` the input happens to equal the output, so the most
//! obvious test one would write passes against the broken code.
//!
//! ## Three rules that look alike and are not
//!
//! | code group | on a bare time |
//! |---|---|
//! | `a A c f g G h H i P s u` | formatted normally |
//! | `e T O Z` (timezone) | **empty in place**, rest still formats |
//! | date codes | **the WHOLE render is empty** |
//!
//! Conflating the last two gives `"23:30 "` where Django gives `""`. And the
//! timezone rule differs from the naive-DATETIME rule one line away, where the
//! default zone IS reported — `naive_datetime_still_reports_its_zone` pins that,
//! because reusing the datetime rule here invents a zone from the epoch date a
//! time is anchored on.

use djust_core::{Context, Value};
use djust_templates::timezone::set_active_timezone;
use djust_templates::Template;

fn render(source: &str, value: &str) -> String {
    let mut ctx = Context::new();
    ctx.set("v".to_string(), Value::String(value.to_string()));
    Template::new(source)
        .expect("template should parse")
        .render(&ctx)
        .expect("template should render")
}

const EVENING: &str = "23:30:00";
const MORNING: &str = "09:05:30";

#[test]
fn the_time_filter_formats_instead_of_echoing_its_input() {
    // Django: '23:30'. Pre-fix: '23:30:00' — the input, unchanged.
    assert_eq!(render(r#"{{ v|time:"H:i" }}"#, EVENING), "23:30");
    assert_eq!(render(r#"{{ v|time:"H:i" }}"#, MORNING), "09:05");
}

#[test]
fn the_date_filter_takes_time_codes_on_a_time_too() {
    // Django applies the same formatter to both filters.
    assert_eq!(render(r#"{{ v|date:"H:i" }}"#, EVENING), "23:30");
    assert_eq!(render(r#"{{ v|date:"g:i A" }}"#, EVENING), "11:30 PM");
    assert_eq!(render(r#"{{ v|date:"g:i A" }}"#, MORNING), "9:05 AM");
    assert_eq!(render(r#"{{ v|date:"P" }}"#, EVENING), "11:30 p.m.");
    assert_eq!(render(r#"{{ v|date:"P" }}"#, MORNING), "9:05 a.m.");
}

#[test]
fn a_seconds_format_would_pass_against_the_broken_code() {
    // Kept as a REMINDER, not as coverage: for `H:i:s` the echoed input equals
    // the correct output, so this case cannot distinguish fixed from broken.
    // It is the test someone reaching for the obvious assertion would write.
    assert_eq!(render(r#"{{ v|time:"H:i:s" }}"#, EVENING), "23:30:00");
}

#[test]
fn minutes_only_parses_too() {
    // An `<input type="time">` submits `HH:MM` with no seconds, the same shape
    // that made the no-seconds datetime case load-bearing in #2203.
    assert_eq!(render(r#"{{ v|time:"H:i" }}"#, "23:30"), "23:30");
}

// ---------------------------------------------------------------------------
// A date code empties EVERYTHING.
// ---------------------------------------------------------------------------

#[test]
fn a_date_code_empties_the_whole_render() {
    // Django's `TimeFormat` has no attribute to answer a date code with; it
    // raises, the filter swallows it, and the result is ''. Not a partial
    // render with the date part missing.
    assert_eq!(render(r#"{{ v|date:"Y-m-d" }}"#, EVENING), "");
    assert_eq!(render(r#"{{ v|time:"Y-m-d" }}"#, EVENING), "");
}

#[test]
fn one_date_code_poisons_an_otherwise_valid_format() {
    // The case that distinguishes "empty the whole thing" from "skip that
    // code": Django gives '' here, NOT '23:30 '.
    assert_eq!(render(r#"{{ v|date:"H:i Y" }}"#, EVENING), "");
}

#[test]
fn an_escaped_date_letter_is_a_literal_and_does_not_empty() {
    // `\Y` is the letter Y, not the year code, so the render proceeds.
    assert_eq!(render(r#"{{ v|date:"\Y H:i" }}"#, EVENING), "Y 23:30");
}

// ---------------------------------------------------------------------------
// Timezone codes: empty IN PLACE, and only for a bare time.
// ---------------------------------------------------------------------------

#[test]
fn timezone_codes_are_empty_in_place_not_whole_render() {
    // Django: '23:30 ' — the trailing space survives, the abbreviation does
    // not. Distinguishes this rule from the date-code rule above.
    assert!(set_active_timezone(Some("America/New_York")));
    for code in ["T", "e", "O", "Z"] {
        let out = render(&format!(r#"{{{{ v|date:"H:i {code}" }}}}"#), EVENING);
        assert_eq!(out, "23:30 ", "for code {code}");
    }
    set_active_timezone(None);
}

#[test]
fn naive_datetime_still_reports_its_zone() {
    // The regression guard for the rule above. A naive DATETIME does report the
    // default zone (#2209), and only a bare TIME does not — an implementation
    // that suppressed both would silently undo #2209 for every naive datetime,
    // and one that suppressed neither invents a zone from the epoch date a bare
    // time is anchored on.
    assert!(set_active_timezone(Some("America/New_York")));
    assert_eq!(
        render(r#"{{ v|date:"H:i T" }}"#, "2026-08-22T23:30:00"),
        "23:30 EDT"
    );
    assert_eq!(
        render(r#"{{ v|date:"H:i O" }}"#, "2026-08-22T23:30:00"),
        "23:30 -0400"
    );
    set_active_timezone(None);
}

#[test]
fn a_bare_time_is_never_shifted_by_the_active_zone() {
    // It has no instant to convert. The anchor date exists only so the
    // `Timelike` accessors work; it must never reach the output.
    assert!(set_active_timezone(Some("Asia/Tokyo")));
    assert_eq!(render(r#"{{ v|time:"H:i" }}"#, EVENING), "23:30");
    set_active_timezone(None);
}

// ---------------------------------------------------------------------------
// `a` — found by the same differential, on datetimes as well as times.
// ---------------------------------------------------------------------------

#[test]
fn lowercase_a_has_the_periods_django_uses() {
    // Django's `a` is 'a.m.'/'p.m.'; only the uppercase `A` is bare AM/PM.
    // djust emitted 'am'/'pm' for both. Not a time-only defect — pinned here
    // for a datetime too, since that is where it also occurs.
    assert_eq!(render(r#"{{ v|date:"a" }}"#, EVENING), "p.m.");
    assert_eq!(render(r#"{{ v|date:"a" }}"#, MORNING), "a.m.");
    assert_eq!(render(r#"{{ v|date:"A" }}"#, EVENING), "PM");
    assert_eq!(render(r#"{{ v|date:"a" }}"#, "2026-08-22T23:30:00"), "p.m.");
    assert_eq!(render(r#"{{ v|date:"A" }}"#, "2026-08-22T09:30:00"), "AM");
}

// ---------------------------------------------------------------------------
// Guard: the other value shapes are untouched.
// ---------------------------------------------------------------------------

#[test]
fn datetimes_and_dates_are_unaffected() {
    // The new parse branch runs LAST, so it can only catch strings the existing
    // branches reject. A date is still a date and a datetime still a datetime.
    assert_eq!(
        render(r#"{{ v|date:"Y-m-d H:i" }}"#, "2026-08-22T23:30:00"),
        "2026-08-22 23:30"
    );
    assert_eq!(
        render(r#"{{ v|date:"Y-m-d" }}"#, "2026-08-22"),
        "2026-08-22"
    );
    // And a date-only value keeps its existing fail-soft midnight behaviour —
    // Django raises TypeError here. Deliberately unchanged; see #2216.
    assert_eq!(render(r#"{{ v|date:"H:i" }}"#, "2026-08-22"), "00:00");
}
