//! `timesince`/`timeuntil` output must match Django's (#2228).
//!
//! Every expectation was taken from a live Django 5.2 `django.utils.timesince`
//! call. The previous implementation diverged on **every** input, including the
//! aware values that always parsed (#2227 fixed only the parse):
//!
//! | elapsed | Django | before |
//! |---|---|---|
//! | 30 s | `0 minutes` | `30 seconds` |
//! | 2 h 30 m | `2 hours, 30 minutes` | `2 hours` |
//! | 10 d | `1 week, 3 days` | `1 week` |
//! | 400 d | `1 year, 1 month` | `1 year` |
//!
//! Three defects at once: the count/unit separator is U+00A0, up to **two
//! adjacent** units are shown, and the smallest unit is the minute.
//!
//! ## Why these test the inner function, not the filter
//!
//! The filter compares against "now", so a test through it can only assert
//! coarse buckets and would be a coin flip near boundaries (#1795).
//! `django_timesince` takes both datetimes, so every case here is **fully
//! deterministic** and can pin the exact string — including the calendar cases,
//! which is where a fixed-seconds approximation drifts and where a
//! "now"-relative test could never reach.
//!
//! The filter-level plumbing is covered by `test_duration_filters_2227.rs`.

use djust_templates::filters::django_timesince_for_tests as timesince;

fn at(y: i32, m: u32, d: u32, h: u32, mi: u32) -> chrono::NaiveDateTime {
    chrono::NaiveDate::from_ymd_opt(y, m, d)
        .unwrap()
        .and_hms_opt(h, mi, 0)
        .unwrap()
}

const NBSP: char = '\u{a0}';

// ---------------------------------------------------------------------------
// The separator.
// ---------------------------------------------------------------------------

#[test]
fn the_separator_is_a_no_break_space_not_an_ordinary_one() {
    // Django's `avoid_wrapping`, so a count and its unit never break across a
    // line. Asserted as the CODEPOINT: a test written with an ordinary space
    // reads identically and passes while shipping the wrong byte into every
    // page.
    let out = timesince(at(2026, 1, 1, 0, 0), at(2026, 1, 1, 2, 0));
    assert_eq!(out, format!("2{NBSP}hours"));
    assert!(!out.contains(' '), "an ordinary space leaked in: {out:?}");
}

#[test]
fn the_unit_join_is_an_ordinary_comma_space() {
    // Only the count/unit pair is protected from wrapping; the join between
    // two units is a normal ", " and SHOULD be breakable.
    let out = timesince(at(2026, 1, 1, 0, 0), at(2026, 1, 1, 2, 30));
    assert_eq!(out, format!("2{NBSP}hours, 30{NBSP}minutes"));
}

// ---------------------------------------------------------------------------
// Depth: two adjacent units.
// ---------------------------------------------------------------------------

#[test]
fn two_adjacent_units_are_shown() {
    assert_eq!(
        timesince(at(2026, 1, 1, 0, 0), at(2026, 1, 4, 5, 0)),
        format!("3{NBSP}days, 5{NBSP}hours")
    );
    assert_eq!(
        timesince(at(2026, 1, 1, 0, 0), at(2026, 1, 11, 0, 0)),
        format!("1{NBSP}week, 3{NBSP}days")
    );
}

#[test]
fn the_walk_stops_at_a_zero_rather_than_skipping_to_the_next_non_zero() {
    // "Adjacent" is the load-bearing word. Exactly one year apart is
    // `1 year` — NOT `1 year, 0 months`, and not `1 year` plus whatever
    // non-zero unit comes later. An implementation that collected the first two
    // NON-ZERO partials would produce `1 year, 5 days` here, which Django's own
    // docstring calls out as impossible output.
    assert_eq!(
        timesince(at(2026, 1, 1, 0, 0), at(2027, 1, 6, 0, 0)),
        format!("1{NBSP}year")
    );
}

#[test]
fn never_more_than_two_units() {
    // 1 year, 1 month and change — the third unit is dropped.
    let out = timesince(at(2026, 1, 1, 0, 0), at(2027, 2, 9, 7, 0));
    assert_eq!(out, format!("1{NBSP}year, 1{NBSP}month"));
    assert_eq!(out.matches(',').count(), 1);
}

// ---------------------------------------------------------------------------
// Seconds are ignored entirely.
// ---------------------------------------------------------------------------

#[test]
fn under_a_minute_is_zero_minutes_not_seconds() {
    // Django's smallest unit is the minute; djust reported seconds, so a fresh
    // value read `30 seconds` where Django reads `0 minutes`.
    assert_eq!(
        timesince(at(2026, 1, 1, 0, 0), at(2026, 1, 1, 0, 0)),
        format!("0{NBSP}minutes")
    );
    let thirty_seconds = at(2026, 1, 1, 0, 0) + chrono::Duration::seconds(30);
    assert_eq!(
        timesince(at(2026, 1, 1, 0, 0), thirty_seconds),
        format!("0{NBSP}minutes")
    );
}

#[test]
fn a_future_value_reads_zero_minutes() {
    // Django does NOT measure backwards here; `since <= 0` returns `0 minutes`.
    //
    // This caught a real defect in the first pass, which swapped the two
    // datetimes inside the shared body so both filters could reuse it. That
    // made a future value report the elapsed time in the wrong direction —
    // `1 year` instead of `0 minutes`. `timeuntil` swaps at its own call site
    // instead, which is what Django's `reversed=True` does.
    assert_eq!(
        timesince(at(2027, 1, 1, 0, 0), at(2026, 1, 1, 0, 0)),
        format!("0{NBSP}minutes")
    );
}

// ---------------------------------------------------------------------------
// Calendar-aware years and months — the part an approximation cannot reach.
// ---------------------------------------------------------------------------

#[test]
fn the_two_cases_from_djangos_own_docstring() {
    // Django documents these as exactly "1 year, 1 month" apart despite deltas
    // of 393 and 397 days. Dividing by a fixed 2629746 seconds gets both wrong,
    // and no "now"-relative test could pin them.
    assert_eq!(
        timesince(at(2013, 2, 10, 0, 0), at(2014, 3, 10, 0, 0)),
        format!("1{NBSP}year, 1{NBSP}month")
    );
    assert_eq!(
        timesince(at(2007, 8, 10, 0, 0), at(2008, 9, 10, 0, 0)),
        format!("1{NBSP}year, 1{NBSP}month")
    );
}

#[test]
fn a_month_is_a_calendar_month_not_thirty_days() {
    // February is 28 days and January is 31; both are "1 month".
    assert_eq!(
        timesince(at(2026, 2, 1, 0, 0), at(2026, 3, 1, 0, 0)),
        format!("1{NBSP}month")
    );
    assert_eq!(
        timesince(at(2026, 1, 1, 0, 0), at(2026, 2, 1, 0, 0)),
        format!("1{NBSP}month")
    );
}

#[test]
fn a_day_short_of_a_month_is_not_a_month() {
    // The back-off when the day-of-month has not come round yet.
    assert_eq!(
        timesince(at(2026, 1, 15, 0, 0), at(2026, 2, 14, 0, 0)),
        format!("4{NBSP}weeks, 2{NBSP}days")
    );
}

#[test]
fn the_time_of_day_backs_the_month_off_too() {
    // Same day-of-month, but an hour short: still not a full month.
    let out = timesince(at(2026, 1, 15, 12, 0), at(2026, 2, 15, 11, 0));
    assert!(
        out.starts_with(&format!("4{NBSP}weeks")),
        "an hour short of a month must not round up to one month, got {out:?}"
    );
}

#[test]
fn the_february_pivot_clamps_the_day_of_month() {
    // Django's MONTHS_DAYS carries 28 for February with no leap-year case, so a
    // source date late in the month pivots to Feb 28 even in a leap year (2028
    // is one). Reproduced deliberately rather than "fixed": parity is the
    // point, and correcting it here would make djust disagree with Django on
    // exactly these dates.
    //
    // Note what the clamp does NOT do, which is where my own first expectation
    // was wrong: Jan 31 -> Mar 1 is `1 month`, not `1 month, 2 days`. The pivot
    // lands on Feb 28 and the remainder is 2 days — but `weeks` is 0, and the
    // adjacent-unit walk breaks at a zero before ever reaching days. Verified
    // against Django rather than reasoned about.
    assert_eq!(
        timesince(at(2028, 1, 31, 0, 0), at(2028, 3, 1, 0, 0)),
        format!("1{NBSP}month")
    );
    // Far enough past the pivot for the second unit to be non-zero, so the
    // clamped arithmetic is actually visible in the output.
    assert_eq!(
        timesince(at(2028, 1, 31, 0, 0), at(2028, 3, 10, 0, 0)),
        format!("1{NBSP}month, 1{NBSP}week")
    );
}

#[test]
fn a_year_boundary_rolls_the_pivot_month_correctly() {
    // The `pivot_month > 12` wrap. November plus three months is February of
    // the next year, not month 14.
    assert_eq!(
        timesince(at(2026, 11, 15, 0, 0), at(2027, 2, 15, 0, 0)),
        format!("3{NBSP}months")
    );
}

// ---------------------------------------------------------------------------
// Singular vs plural.
// ---------------------------------------------------------------------------

#[test]
fn one_of_a_unit_is_singular() {
    assert_eq!(
        timesince(at(2026, 1, 1, 0, 0), at(2026, 1, 1, 1, 0)),
        format!("1{NBSP}hour")
    );
    assert_eq!(
        timesince(at(2026, 1, 1, 0, 0), at(2026, 1, 2, 0, 0)),
        format!("1{NBSP}day")
    );
    assert_eq!(
        timesince(at(2026, 1, 1, 0, 0), at(2026, 1, 8, 0, 0)),
        format!("1{NBSP}week")
    );
    assert_eq!(
        timesince(at(2026, 1, 1, 0, 0), at(2027, 1, 1, 0, 0)),
        format!("1{NBSP}year")
    );
    // Zero is PLURAL — `0 minutes`, not `0 minute`.
    assert_eq!(
        timesince(at(2026, 1, 1, 0, 0), at(2026, 1, 1, 0, 0)),
        format!("0{NBSP}minutes")
    );
}
