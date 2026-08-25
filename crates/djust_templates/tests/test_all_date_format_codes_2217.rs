//! Every Django `date` format code, pinned against Django's own output (#2217).
//!
//! Nine codes — `b c f L o r S t u w W z` and friends — were unimplemented and
//! fell through the formatter's catch-all, rendering as **their own letter**:
//! `{{ v|date:"jS F Y" }}` produced `22S August 2026`.
//!
//! ## Why the gap was invisible
//!
//! Rendering an unknown character as itself is also the **correct** behaviour
//! for a literal, and Django does the same. So an unimplemented code is
//! indistinguishable from an intentional one by inspection, and only a
//! differential against Django separates them. That is the argument for pinning
//! the WHOLE set rather than the nine that were missing: a table with a hole in
//! it looks exactly like a table without one (v1.0.0rc4 retro finding #1 —
//! a coverage suite must enumerate every variant of the surface it covers).
//!
//! ## The three values
//!
//! Chosen so no code is exercised only in its easy case:
//!
//! * `AUG_PM` — afternoon, microseconds, EDT, 22nd (an `nd` ordinal), week 34
//! * `JAN_AM` — morning, single-digit month and day (`3` vs `03`), EST, week 1
//! * `LEAP` — 29 Feb 2028, so `L` is `True` and `t` is 29 rather than 28
//!
//! Every expectation below is a live Django 5.2 render of the same instant
//! under `TIME_ZONE="America/New_York"`.

use djust_core::{Context, Value};
use djust_templates::timezone::set_active_timezone;
use djust_templates::Template;

/// 2026-08-22 23:30:45.123456 UTC → 19:30:45 EDT.
const AUG_PM: &str = "2026-08-22T23:30:45.123456+00:00";
/// 2026-01-03 09:05:00 UTC → 04:05:00 EST.
const JAN_AM: &str = "2026-01-03T09:05:00+00:00";
/// 2028-02-29 12:00:00 UTC → 07:00:00 EST, in a leap year.
const LEAP: &str = "2028-02-29T12:00:00+00:00";

fn render_code(code: &str, value: &str) -> String {
    assert!(set_active_timezone(Some("America/New_York")));
    let mut ctx = Context::new();
    ctx.set("v".to_string(), Value::String(value.to_string()));
    let out = Template::new(&format!(r#"{{{{ v|date:"{code}" }}}}"#))
        .expect("template should parse")
        .render(&ctx)
        .expect("template should render");
    set_active_timezone(None);
    out
}

/// (code, AUG_PM, JAN_AM, LEAP) — all 38, from Django.
const TABLE: &[(&str, &str, &str, &str)] = &[
    ("a", "p.m.", "a.m.", "a.m."),
    ("A", "PM", "AM", "AM"),
    ("b", "aug", "jan", "feb"),
    (
        "c",
        "2026-08-22T19:30:45.123456-04:00",
        "2026-01-03T04:05:00-05:00",
        "2028-02-29T07:00:00-05:00",
    ),
    ("d", "22", "03", "29"),
    ("D", "Sat", "Sat", "Tue"),
    ("e", "EDT", "EST", "EST"),
    ("f", "7:30", "4:05", "7"),
    ("F", "August", "January", "February"),
    ("g", "7", "4", "7"),
    ("G", "19", "4", "7"),
    ("h", "07", "04", "07"),
    ("H", "19", "04", "07"),
    ("i", "30", "05", "00"),
    ("I", "1", "0", "0"),
    ("j", "22", "3", "29"),
    ("l", "Saturday", "Saturday", "Tuesday"),
    ("L", "False", "False", "True"),
    ("m", "08", "01", "02"),
    ("M", "Aug", "Jan", "Feb"),
    ("n", "8", "1", "2"),
    ("N", "Aug.", "Jan.", "Feb."),
    ("o", "2026", "2026", "2028"),
    ("O", "-0400", "-0500", "-0500"),
    ("P", "7:30 p.m.", "4:05 a.m.", "7 a.m."),
    (
        "r",
        "Sat, 22 Aug 2026 19:30:45 -0400",
        "Sat, 03 Jan 2026 04:05:00 -0500",
        "Tue, 29 Feb 2028 07:00:00 -0500",
    ),
    ("s", "45", "00", "00"),
    ("S", "nd", "rd", "th"),
    ("t", "31", "31", "29"),
    ("T", "EDT", "EST", "EST"),
    ("u", "123456", "000000", "000000"),
    ("U", "1787441445", "1767431100", "1835438400"),
    ("w", "6", "6", "2"),
    ("W", "34", "1", "9"),
    ("y", "26", "26", "28"),
    ("Y", "2026", "2026", "2028"),
    ("z", "234", "3", "60"),
    ("Z", "-14400", "-18000", "-18000"),
];

#[test]
fn every_format_code_matches_django() {
    let mut wrong = Vec::new();
    for (code, aug, jan, leap) in TABLE {
        for (value, expected, label) in [
            (AUG_PM, *aug, "AUG_PM"),
            (JAN_AM, *jan, "JAN_AM"),
            (LEAP, *leap, "LEAP"),
        ] {
            let got = render_code(code, value);
            if got != expected {
                wrong.push(format!(
                    "  {code:?} on {label}: expected {expected:?}, got {got:?}"
                ));
            }
        }
    }
    assert!(
        wrong.is_empty(),
        "{} of {} format-code renders diverge from Django:\n{}",
        wrong.len(),
        TABLE.len() * 3,
        wrong.join("\n")
    );
}

#[test]
fn the_table_covers_every_code_django_recognises() {
    // Guard the guard. A table missing a code looks exactly like a complete
    // one — which is the very property that let nine codes sit unimplemented —
    // so the SET is asserted rather than the count (#1125).
    let covered: std::collections::BTreeSet<&str> = TABLE.iter().map(|(c, ..)| *c).collect();
    let django: std::collections::BTreeSet<&str> = "aAbcdDefFgGhHiIjlLmMnNoOPrsStTuUwWyYzZ"
        .split("")
        .filter(|s| !s.is_empty())
        .collect();
    assert_eq!(
        covered, django,
        "the pinned set drifted from Django's recognised format characters"
    );
}

#[test]
fn no_code_renders_as_its_own_letter() {
    // The shape of the bug: an unimplemented code fell through the catch-all
    // and emitted itself. This would go red for ANY future code that regresses
    // that way, without needing to know which.
    //
    // Compares against the code itself rather than searching for the letter,
    // so a legitimate output that happens to CONTAIN it (`D` gives `Sat`) is
    // not a false alarm.
    for (code, ..) in TABLE {
        let got = render_code(code, AUG_PM);
        assert_ne!(
            &got, code,
            "{code:?} rendered as its own letter — it is unimplemented and \
             falling through the catch-all"
        );
    }
}

#[test]
fn an_escaped_letter_still_renders_as_itself() {
    // The counterpart: a BACKSLASH-escaped code must emit the literal letter.
    // Now that every code is implemented, this is the only way to get one.
    assert_eq!(render_code(r"\Y-\m", AUG_PM), "Y-m");
    assert_eq!(render_code(r"\j\S", AUG_PM), "jS");
}

#[test]
fn ordinals_cover_the_eleven_twelve_thirteen_exception() {
    // `S` is the code most likely to be written by hand as
    // `["th","st","nd","rd"][n % 10]`, which gets the 11th/12th/13th wrong —
    // they end in 1/2/3 but take `th`.
    for (day, suffix) in [
        (1, "st"),
        (2, "nd"),
        (3, "rd"),
        (4, "th"),
        (11, "th"),
        (12, "th"),
        (13, "th"),
        (21, "st"),
        (22, "nd"),
        (23, "rd"),
        (30, "th"),
        (31, "st"),
    ] {
        let value = format!("2026-01-{day:02}T12:00:00+00:00");
        assert_eq!(render_code("S", &value), suffix, "day {day}");
    }
}

#[test]
fn iso_week_and_week_year_are_not_the_calendar_ones() {
    // Added after gate-off: mutations replacing `W` with `ordinal/7 + 1` and
    // `o` with the calendar year both survived the table above, because none
    // of its three values discriminates. That is the enumerate-every-variant
    // rule failing inside the very test written to enforce it — three
    // carefully chosen values are still a sample, and ISO week arithmetic only
    // diverges at year boundaries.
    //
    // 2027-01-01 is a Friday, so ISO puts it in week 53 of 2026: `o` and `Y`
    // disagree, and `W` is 53 where a naive day-of-year division gives 1.
    assert_eq!(render_code("Y", "2027-01-01T12:00:00+00:00"), "2027");
    assert_eq!(render_code("o", "2027-01-01T12:00:00+00:00"), "2026");
    assert_eq!(render_code("W", "2027-01-01T12:00:00+00:00"), "53");

    // 2021-01-03 is a Sunday — the last day of ISO week 53 of 2020.
    assert_eq!(render_code("o", "2021-01-03T12:00:00+00:00"), "2020");
    assert_eq!(render_code("W", "2021-01-03T12:00:00+00:00"), "53");

    // And a mid-year date where the naive division is simply off by one.
    assert_eq!(render_code("W", "2026-06-15T12:00:00+00:00"), "25");
}

#[test]
fn the_ap_month_abbreviations_are_not_percent_b_plus_a_period() {
    // Django's `N` is Associated Press style, and AP does not abbreviate the
    // SHORT months at all — March through July are spelled out, and September
    // is `Sept.` rather than `Sep.`. So `%b` + "." is wrong for six of twelve
    // months.
    //
    // Found by a randomized sweep, NOT by the table above: its three values are
    // January, August and February, and all three happen to be months where
    // `%b` + "." is right. The table looked complete because it enumerated
    // every CODE — while sampling only three values per code, which is the same
    // blind spot one level down.
    for (month, expected) in [
        (1, "Jan."),
        (2, "Feb."),
        (3, "March"),
        (4, "April"),
        (5, "May"),
        (6, "June"),
        (7, "July"),
        (8, "Aug."),
        (9, "Sept."),
        (10, "Oct."),
        (11, "Nov."),
        (12, "Dec."),
    ] {
        let value = format!("2026-{month:02}-15T12:00:00+00:00");
        assert_eq!(render_code("N", &value), expected, "month {month}");
    }
}

#[test]
fn every_month_renders_correctly_for_the_month_name_codes() {
    // The same sampling lesson applied to `N`'s neighbours: `b`, `M` and `F`
    // are exercised for three months by the table, so all twelve are pinned
    // here. `b` is lowercase, `M` is the plain three-letter form (`Sep`, not
    // `Sept`), and `F` is the full name.
    for (month, b, m, f) in [
        (1, "jan", "Jan", "January"),
        (2, "feb", "Feb", "February"),
        (3, "mar", "Mar", "March"),
        (4, "apr", "Apr", "April"),
        (5, "may", "May", "May"),
        (6, "jun", "Jun", "June"),
        (7, "jul", "Jul", "July"),
        (8, "aug", "Aug", "August"),
        (9, "sep", "Sep", "September"),
        (10, "oct", "Oct", "October"),
        (11, "nov", "Nov", "November"),
        (12, "dec", "Dec", "December"),
    ] {
        let value = format!("2026-{month:02}-15T12:00:00+00:00");
        assert_eq!(render_code("b", &value), b, "b, month {month}");
        assert_eq!(render_code("M", &value), m, "M, month {month}");
        assert_eq!(render_code("F", &value), f, "F, month {month}");
    }
}
