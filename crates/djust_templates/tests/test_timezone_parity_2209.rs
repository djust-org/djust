//! `date`/`time` must render in `settings.TIME_ZONE`, the way Django does (#2209).
//!
//! Every expectation below was taken from a live Django 5.2 render of the same
//! value under `USE_TZ=True, TIME_ZONE="America/New_York"` — not from what this
//! implementation produces. The probe that generated them rendered
//! `{{ v|date:"<code>" }}` through Django's own engine for five value shapes ×
//! twenty-six format codes; the rows worth pinning are here.
//!
//! ## The bug
//!
//! The engine did no timezone conversion at any layer. Under `USE_TZ=True` the
//! serializer hands Rust `2026-08-22T23:30:00+00:00` and the filter formatted
//! that offset verbatim, so a New York project rendered 23:30 where Django
//! renders 19:30. Four hours, in the configuration `djust new` generates —
//! the scaffold sets `USE_TZ = True` (`scaffolding/templates.py:171`).
//!
//! ## Why a real tz database and not an offset
//!
//! The two `aware_*` cases differ by an hour of offset for the same zone
//! (`-0400` in August, `-0500` in January). A single render can contain both —
//! any table of timestamps spanning six months does — so a fixed per-render
//! offset would be correct for one row and wrong for the next. That is the
//! whole argument for the `chrono-tz` dependency, and
//! `winter_and_summer_disagree_within_one_render` is the test that would go red
//! if someone later "optimised" it back to an offset.
//!
//! ## Isolation
//!
//! The active zone is a thread-local, so these tests do NOT need the serial
//! mutex `test_display_django_parity_2203.rs` uses for its process-global flag —
//! Rust runs them on parallel threads and each gets its own zone.
//! `two_threads_hold_independent_zones` pins that property rather than assuming
//! it, because if the storage ever became a process global these tests would
//! start flaking against each other rather than failing honestly.

use djust_templates::timezone::{active_timezone_name, set_active_timezone};
use djust_templates::Template;

use djust_core::{Context, Value};

const NY: &str = "America/New_York";

/// UTC instants chosen either side of a DST transition.
const AWARE_SUMMER: &str = "2026-08-22T23:30:00+00:00";
const AWARE_WINTER: &str = "2026-01-15T23:30:00+00:00";
/// No offset — a naive datetime, which Django does NOT shift.
const NAIVE: &str = "2026-08-22T23:30:00";

fn render_in(tz: Option<&str>, source: &str, value: &str) -> String {
    assert!(
        set_active_timezone(tz),
        "test setup: {tz:?} should be a known zone"
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

// ---------------------------------------------------------------------------
// The bug itself.
// ---------------------------------------------------------------------------

#[test]
fn an_aware_datetime_renders_in_the_active_zone() {
    // Django: '2026-08-22 19:30'. Pre-fix djust: '2026-08-22 23:30'.
    assert_eq!(
        render_in(Some(NY), r#"{{ v|date:"Y-m-d H:i" }}"#, AWARE_SUMMER),
        "2026-08-22 19:30"
    );
}

#[test]
fn the_time_filter_converts_too() {
    // `time` delegates to `format_date`, so it would be easy to fix one and not
    // the other. Django: '19:30'.
    assert_eq!(
        render_in(Some(NY), r#"{{ v|time:"H:i" }}"#, AWARE_SUMMER),
        "19:30"
    );
}

#[test]
fn winter_and_summer_disagree_within_one_render() {
    // The case that rules out a fixed per-render offset: same zone, same
    // template, one hour of difference in the offset. Django renders 19:30 and
    // 18:30 respectively.
    assert!(set_active_timezone(Some(NY)));
    let mut ctx = Context::new();
    ctx.set("s".to_string(), Value::String(AWARE_SUMMER.to_string()));
    ctx.set("w".to_string(), Value::String(AWARE_WINTER.to_string()));
    let out = Template::new(r#"{{ s|date:"H:i" }}|{{ w|date:"H:i" }}"#)
        .unwrap()
        .render(&ctx)
        .unwrap();
    set_active_timezone(None);
    assert_eq!(out, "19:30|18:30");
}

#[test]
fn a_naive_datetime_is_not_shifted() {
    // Django does NOT apply `localtime` to a naive value — it is already
    // understood to be local. Shifting it would move every timestamp in a
    // `USE_TZ = False` project, which is where naive datetimes are the norm.
    // Django: '2026-08-22 23:30'.
    assert_eq!(
        render_in(Some(NY), r#"{{ v|date:"Y-m-d H:i" }}"#, NAIVE),
        "2026-08-22 23:30"
    );
}

#[test]
fn no_active_zone_leaves_the_value_alone() {
    // `USE_TZ = False`, or this crate embedded with no Django settings. This is
    // the pre-#2209 behaviour, and it must stay reachable — it is what the
    // Python side selects when `settings.USE_TZ` is false.
    assert_eq!(
        render_in(None, r#"{{ v|date:"Y-m-d H:i" }}"#, AWARE_SUMMER),
        "2026-08-22 23:30"
    );
}

// ---------------------------------------------------------------------------
// The timezone format codes. Before #2209 these had no zone to report, so they
// fell through to the catch-all and rendered as their own letter — `H:i T`
// produced "19:30 T".
// ---------------------------------------------------------------------------

#[test]
fn the_zone_abbreviation_codes_follow_the_transition() {
    // Django: 'EDT' in August, 'EST' in January — for both `T` and `e`.
    assert_eq!(
        render_in(Some(NY), r#"{{ v|date:"T" }}"#, AWARE_SUMMER),
        "EDT"
    );
    assert_eq!(
        render_in(Some(NY), r#"{{ v|date:"T" }}"#, AWARE_WINTER),
        "EST"
    );
    assert_eq!(
        render_in(Some(NY), r#"{{ v|date:"e" }}"#, AWARE_SUMMER),
        "EDT"
    );
    assert_eq!(
        render_in(Some(NY), r#"{{ v|date:"e" }}"#, AWARE_WINTER),
        "EST"
    );
}

#[test]
fn e_is_empty_for_a_naive_value_but_t_is_not() {
    // Django's naive semantics are NOT uniform across the zone codes, which is
    // why each is handled rather than sharing one branch: `e` renders '' for a
    // naive value while `T` renders the DEFAULT zone's abbreviation for that
    // local time. Verified against Django, which gives '' and 'EDT'.
    assert_eq!(render_in(Some(NY), r#"{{ v|date:"e" }}"#, NAIVE), "");
    assert_eq!(render_in(Some(NY), r#"{{ v|date:"T" }}"#, NAIVE), "EDT");
}

#[test]
fn the_offset_codes_report_the_active_zone() {
    // Django: '-0400'/'-14400' in August, '-0500'/'-18000' in January.
    assert_eq!(
        render_in(Some(NY), r#"{{ v|date:"O" }}"#, AWARE_SUMMER),
        "-0400"
    );
    assert_eq!(
        render_in(Some(NY), r#"{{ v|date:"Z" }}"#, AWARE_SUMMER),
        "-14400"
    );
    assert_eq!(
        render_in(Some(NY), r#"{{ v|date:"O" }}"#, AWARE_WINTER),
        "-0500"
    );
    assert_eq!(
        render_in(Some(NY), r#"{{ v|date:"Z" }}"#, AWARE_WINTER),
        "-18000"
    );
}

#[test]
fn a_naive_value_reports_the_default_zones_offset_not_utc() {
    // The bug the differential caught that inspection did not: the naive branch
    // left the value stamped with the "+0000" its parse produced, so `O`
    // rendered '+0000' where Django renders '-0400'. The wall clock was already
    // correct, which is exactly why reading the code did not surface it.
    assert_eq!(render_in(Some(NY), r#"{{ v|date:"O" }}"#, NAIVE), "-0400");
    assert_eq!(render_in(Some(NY), r#"{{ v|date:"Z" }}"#, NAIVE), "-14400");
}

#[test]
fn the_epoch_code_is_the_real_instant() {
    // Django: 1787441400 for the aware value. For the NAIVE one Django gives
    // 1787455800 — it interprets the local wall clock in the default zone, so
    // the two differ by the offset even though they read the same.
    assert_eq!(
        render_in(Some(NY), r#"{{ v|date:"U" }}"#, AWARE_SUMMER),
        "1787441400"
    );
    assert_eq!(
        render_in(Some(NY), r#"{{ v|date:"U" }}"#, NAIVE),
        "1787455800"
    );
}

#[test]
fn the_dst_flag_tracks_the_zones_own_standard_offset() {
    // Django: '1' in August, '0' in January.
    assert_eq!(
        render_in(Some(NY), r#"{{ v|date:"I" }}"#, AWARE_SUMMER),
        "1"
    );
    assert_eq!(
        render_in(Some(NY), r#"{{ v|date:"I" }}"#, AWARE_WINTER),
        "0"
    );
}

#[test]
fn the_dst_flag_is_right_in_the_southern_hemisphere() {
    // The case that rules out anchoring "is DST" on January: Sydney is +1100 in
    // January (DST) and +1000 in July (standard), the reverse of New York. A
    // January-anchored implementation reports every Sydney instant backwards.
    // Both instants below are the SAME UTC times as the cases above.
    assert_eq!(
        render_in(
            Some("Australia/Sydney"),
            r#"{{ v|date:"I" }}"#,
            AWARE_WINTER
        ),
        "1"
    );
    assert_eq!(
        render_in(
            Some("Australia/Sydney"),
            r#"{{ v|date:"I" }}"#,
            AWARE_SUMMER
        ),
        "0"
    );
}

#[test]
fn a_zone_without_dst_never_reports_dst() {
    assert_eq!(
        render_in(Some("UTC"), r#"{{ v|date:"I" }}"#, AWARE_SUMMER),
        "0"
    );
    assert_eq!(
        render_in(Some("Asia/Kolkata"), r#"{{ v|date:"I" }}"#, AWARE_SUMMER),
        "0"
    );
}

#[test]
fn a_half_hour_zone_converts_correctly() {
    // Guard against an implementation that stores whole-hour offsets. Kolkata
    // is +05:30, so 23:30Z is 05:00 the next day.
    assert_eq!(
        render_in(
            Some("Asia/Kolkata"),
            r#"{{ v|date:"Y-m-d H:i" }}"#,
            AWARE_SUMMER
        ),
        "2026-08-23 05:00"
    );
}

// ---------------------------------------------------------------------------
// Setter contract.
// ---------------------------------------------------------------------------

#[test]
fn an_unknown_zone_is_refused_without_disturbing_the_current_one() {
    // The Python side logs and carries on when this returns false, so it must
    // not have half-applied anything. A settings typo should not take a page
    // down, and it must not silently switch the zone either.
    assert!(set_active_timezone(Some(NY)));
    assert!(!set_active_timezone(Some("Not/AZone")));
    assert_eq!(active_timezone_name().as_deref(), Some(NY));
    set_active_timezone(None);
    assert_eq!(active_timezone_name(), None);
}

#[test]
fn the_getter_reports_what_the_setter_set() {
    // A setter with no getter cannot be tested end to end (#2017), and the
    // Python side asserts the wiring took effect through this.
    assert!(set_active_timezone(Some("Asia/Tokyo")));
    assert_eq!(active_timezone_name().as_deref(), Some("Asia/Tokyo"));
    set_active_timezone(None);
}

#[test]
fn two_threads_hold_independent_zones() {
    // The property that lets every test in this file skip the serial mutex, and
    // the one that matters in production: djust renders run in `sync_to_async`
    // worker threads, so two connections whose requests activated different
    // zones render concurrently. A process global would interleave them.
    set_active_timezone(Some(NY));
    let other = std::thread::spawn(|| {
        assert_eq!(active_timezone_name(), None, "a fresh thread starts unset");
        set_active_timezone(Some("Asia/Tokyo"));
        // 23:30 UTC is 08:30 the next day in Tokyo.
        let out = render_in(
            Some("Asia/Tokyo"),
            r#"{{ v|date:"Y-m-d H:i" }}"#,
            AWARE_SUMMER,
        );
        assert_eq!(out, "2026-08-23 08:30");
    });
    other.join().expect("worker thread should not panic");
    assert_eq!(
        active_timezone_name().as_deref(),
        Some(NY),
        "the other thread's zone must not have leaked into this one"
    );
    set_active_timezone(None);
}
