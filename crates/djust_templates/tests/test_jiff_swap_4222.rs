//! The chrono -> jiff swap keeps the date filters' contract (#4222).
//!
//! `chrono` and `chrono-tz` were replaced by `jiff` because RustSec marks both
//! unmaintained. A library swap is exactly where a parser quietly widens or a
//! range quietly shrinks, so each test here pins one of those:
//!
//! * the RFC 3339 grammar is chrono's, not jiff's more liberal ISO 8601 one;
//! * zone names stay case-sensitive (jiff's lookup is not);
//! * the far end of Python's calendar still renders, although
//!   `jiff::Timestamp` stops ~26 hours before `datetime.max`;
//! * the far-future DST rule applies (chrono-tz stopped at its last
//!   precomputed transition; Django's `zoneinfo` and jiff follow the rule).
//!
//! Every expected string was checked against a Django 5.2 render.

use djust_core::{Context, Value};
use djust_templates::filters::django_timesince_for_tests as timesince;
use djust_templates::timezone::{active_timezone_name, set_active_timezone};
use djust_templates::Template;

const NY: &str = "America/New_York";

fn render_in(tz: Option<&str>, source: &str, value: &str) -> String {
    assert!(
        set_active_timezone(tz),
        "test setup: {tz:?} should be known"
    );
    let mut ctx = Context::new();
    ctx.set("v".to_string(), Value::String(value.to_string()));
    let out = Template::new(source)
        .expect("template should parse")
        .render(&ctx)
        .expect("template should render");
    set_active_timezone(None);
    out
}

#[test]
fn rfc3339_grammar_is_not_widened_to_jiffs_iso8601() {
    // chrono refused all of these, so the filter rendered Django's answer for
    // a non-date (`""`). jiff's own parser would accept every one.
    for refused in [
        "2026-08-22T14:30+05:00",     // no seconds, with an offset
        "2026-08-22T14:30:00+0500",   // offset without a colon
        "2026-08-22T14:30:00+05",     // hour-only offset
        "2026-08-22T14:30:00Z[UTC]",  // RFC 9557 annotation
        "2026-08-22T14:30:00.+05:00", // a dot with no digits
        "2026-08-22T14:30:00+24:00",  // offset out of range
    ] {
        assert_eq!(
            render_in(None, r#"{{ v|date:"Y-m-d H:i" }}"#, refused),
            "",
            "{refused:?} must stay unparsed"
        );
    }
    // ...and the shapes chrono accepted still parse: lowercase `t`/`z`, a
    // space separator, and fractional digits past the ninth (dropped).
    for accepted in [
        "2026-08-22t14:30:00z",
        "2026-08-22 14:30:00Z",
        "2026-08-22T14:30:00.1234567891+00:00",
    ] {
        assert_eq!(
            render_in(None, r#"{{ v|date:"Y-m-d H:i" }}"#, accepted),
            "2026-08-22 14:30",
            "{accepted:?} must still parse"
        );
    }
}

#[test]
fn zone_names_stay_case_sensitive() {
    // jiff finds `america/new_york`; chrono-tz and Python's zoneinfo do not,
    // so Django would have refused this TIME_ZONE long before a render.
    assert!(!set_active_timezone(Some("america/new_york")));
    assert_eq!(active_timezone_name(), None);
    // A backward-compatible link name is still a real zone.
    assert!(set_active_timezone(Some("US/Eastern")));
    assert_eq!(active_timezone_name().as_deref(), Some("US/Eastern"));
    set_active_timezone(None);
}

#[test]
fn the_last_day_of_the_calendar_still_renders() {
    // `jiff::Timestamp` ends at 9999-12-30T22:00Z; Python's datetime does not.
    // Django: '9999-12-31 18:59 EST -0500'.
    assert_eq!(
        render_in(
            Some(NY),
            r#"{{ v|date:"Y-m-d H:i T O" }}"#,
            "9999-12-31T23:59:00+00:00"
        ),
        "9999-12-31 18:59 EST -0500"
    );
    // Naive, at the very last second, with no zone to convert into.
    assert_eq!(
        render_in(
            None,
            r#"{{ v|date:"Y-m-d H:i:s U" }}"#,
            "9999-12-31 23:59:59"
        ),
        "9999-12-31 23:59:59 253402300799"
    );
}

#[test]
fn the_far_future_follows_the_zones_dst_rule() {
    // Django: '2100-07-01 08:00 EDT -0400 1' and '9999-07-01 08:00 EDT -0400 1'.
    assert_eq!(
        render_in(
            Some(NY),
            r#"{{ v|date:"Y-m-d H:i T O I" }}"#,
            "2100-07-01T12:00:00+00:00"
        ),
        "2100-07-01 08:00 EDT -0400 1"
    );
    assert_eq!(
        render_in(
            Some(NY),
            r#"{{ v|date:"Y-m-d H:i T O I" }}"#,
            "9999-07-01T12:00:00+00:00"
        ),
        "9999-07-01 08:00 EDT -0400 1"
    );
}

#[test]
fn a_negative_offset_without_an_active_zone_names_itself() {
    // `{% localtime off %}` / USE_TZ=False: Python's
    // `timezone(timedelta(hours=-7)).tzname(None)` is 'UTC-07:00'.
    assert_eq!(
        render_in(
            None,
            r#"{{ v|date:"H:i O Z e c" }}"#,
            "2026-08-22T14:30:00-07:00"
        ),
        "14:30 -0700 -25200 UTC-07:00 2026-08-22T14:30:00-07:00"
    );
}

#[test]
fn timesince_reaches_the_end_of_the_calendar() {
    let at = |y: i16, m: i8, d: i8| jiff::civil::date(y, m, d).at(0, 0, 0, 0);
    // Django: timesince(datetime(9990, 1, 31), datetime(9999, 12, 31)).
    assert_eq!(
        timesince(at(9990, 1, 31), at(9999, 12, 31)),
        "9\u{a0}years, 11\u{a0}months"
    );
}

// ---------------------------------------------------------------------------
// Django-parity edges the chrono port also got wrong, found by the same
// differential and fixed with the swap. Expected strings are Django 5.2's.
// ---------------------------------------------------------------------------

const ZONE_CODES: &str = r#"{{ v|date:"H:i|e|T|O|Z|I|U|r" }}"#;

#[test]
fn a_naive_wall_clock_in_a_dst_gap_blanks_the_zone_codes() {
    // 02:30 never happened in New York on 2026-03-08. Django's
    // `_datetime_ambiguous_or_imaginary` leaves `self.timezone = None`, so the
    // zone codes are empty; `U` and `r` read it with fold=0, the
    // PRE-transition offset (-0500). chrono's port printed `+0000` for O/r.
    assert_eq!(
        render_in(Some(NY), ZONE_CODES, "2026-03-08T02:30:00"),
        "02:30||||||1772955000|Sun, 08 Mar 2026 02:30:00 -0500"
    );
}

#[test]
fn a_naive_wall_clock_in_a_dst_fold_blanks_the_zone_codes() {
    // 01:30 happened twice. fold=0 is the earlier one, EDT. The chrono port
    // reported `EDT -0400 -14400 1` where Django reports nothing.
    assert_eq!(
        render_in(Some(NY), ZONE_CODES, "2026-11-01T01:30:00"),
        "01:30||||||1793511000|Sun, 01 Nov 2026 01:30:00 -0400"
    );
}

#[test]
fn an_aware_instant_whose_local_time_is_in_a_fold_blanks_the_zone_codes() {
    // Both instants are 01:30 local; Django blanks the zone codes for both
    // while `U` and `r` keep each instant's own offset.
    assert_eq!(
        render_in(Some(NY), ZONE_CODES, "2026-11-01T05:30:00+00:00"),
        "01:30||||||1793511000|Sun, 01 Nov 2026 01:30:00 -0400"
    );
    assert_eq!(
        render_in(Some(NY), ZONE_CODES, "2026-11-01T06:30:00+00:00"),
        "01:30||||||1793514600|Sun, 01 Nov 2026 01:30:00 -0500"
    );
    // One minute either side of the fold is unambiguous again.
    assert_eq!(
        render_in(Some(NY), ZONE_CODES, "2026-11-01T04:29:00+00:00"),
        "00:29|EDT|EDT|-0400|-14400|1|1793507340|Sun, 01 Nov 2026 00:29:00 -0400"
    );
}

#[test]
fn a_local_mean_time_offset_prints_its_seconds_like_python() {
    // New York before 1883 is LMT, -04:56:02. Django's `O` truncates the
    // seconds; `c` (isoformat) and `r` (strftime %z) print them. chrono
    // rounded all three to the minute.
    assert_eq!(
        render_in(
            Some(NY),
            r#"{{ v|date:"H:i:s|T|O|Z|c|r" }}"#,
            "1880-01-01T12:00:00+00:00"
        ),
        "07:03:58|LMT|-0456|-17762|1880-01-01T07:03:58-04:56:02|Thu, 01 Jan 1880 07:03:58 -045602"
    );
}

#[test]
fn y_is_zero_padded_and_u_truncates_toward_zero() {
    // Django: `"%04d" % year` and `int(dt.timestamp())`.
    assert_eq!(
        render_in(
            None,
            r#"{{ v|date:"Y-m-d|U" }}"#,
            "0001-01-01T00:00:00+00:00"
        ),
        "0001-01-01|-62135596800"
    );
    assert_eq!(
        render_in(None, r#"{{ v|date:"U" }}"#, "1969-12-31T23:59:59.5+00:00"),
        "0"
    );
}

#[test]
fn naive_string_acceptance_matches_the_chrono_parser_within_pythons_range() {
    // Measured: chrono's `parse_from_str` and jiff's `strptime` agree on every
    // shape here except the ones marked; those are settled to chrono's answer
    // or, where chrono accepted a year no Python `datetime` can hold, refused.
    let fmt = r#"{{ v|date:"Y-m-d H:i:s.u" }}"#;
    for (input, expected) in [
        // One-digit fields, a space before a number, a doubled separator.
        ("2026-1-1 1:2", "2026-01-01 01:02:00.000000"),
        ("2026-1-1", "2026-01-01 00:00:00.000000"),
        ("2026- 01-01 01:02:03", "2026-01-01 01:02:03.000000"),
        ("2026-01-01  01:02:03", "2026-01-01 01:02:03.000000"),
        ("+2026-01-01 00:00:00", "2026-01-01 00:00:00.000000"),
        ("026-01-01 00:00:00", "0026-01-01 00:00:00.000000"),
        // chrono kept the first nine fractional digits; jiff alone refuses.
        (
            "2026-01-01T01:02:03.1234567891",
            "2026-01-01 01:02:03.123456",
        ),
        (
            "2026-01-01 01:02:03.9999999999",
            "2026-01-01 01:02:03.999999",
        ),
    ] {
        assert_eq!(render_in(None, fmt, input), expected, "{input:?}");
    }
    for refused in [
        // Refused by both parsers.
        "2026-01-01 24:00:00",
        "2026-02-29 00:00:00",
        "2026-01-01T01:02:03.",
        "12026-01-01 00:00:00",
        // chrono accepted these; no Python datetime has year 0, a negative
        // year or year 12026, so a string spelling one never came from Python.
        "+12026-01-01 00:00:00",
        "0000-01-01",
        "0-01-01",
        "-2026-01-01 00:00:00",
        "0000-01-01T00:00:00Z",
    ] {
        assert_eq!(
            render_in(None, fmt, refused),
            "",
            "{refused:?} must be refused"
        );
    }
    // A bare time keeps chrono's leniency too.
    assert_eq!(
        render_in(None, r#"{{ v|time:"H:i:s" }}"#, "1:2"),
        "01:02:00"
    );
}

#[test]
fn u_is_pythons_correctly_rounded_float_truncated() {
    // `int(dt.timestamp())`, where `timestamp()` is an int/int true division.
    // 2.5e17 microseconds is past f64's 53 bits, so `.999999` rounds up.
    assert_eq!(
        render_in(
            None,
            r#"{{ v|date:"U" }}"#,
            "9998-09-28T10:10:25.999999+00:00"
        ),
        "253362593426"
    );
    // ...while the same fraction at a 2026 magnitude does not.
    assert_eq!(
        render_in(
            None,
            r#"{{ v|date:"U" }}"#,
            "2026-01-01T00:00:00.999999+00:00"
        ),
        "1767225600"
    );
    // Pre-1970 with a fraction truncates toward zero (Django: -2186096890).
    assert_eq!(
        render_in(
            None,
            r#"{{ v|date:"U" }}"#,
            "1900-09-22T22:51:49.123456+00:00"
        ),
        "-2186096890"
    );
}
