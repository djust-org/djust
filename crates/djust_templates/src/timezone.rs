//! The active render timezone (#2209).
//!
//! Django applies `timezone.localtime()` to an aware datetime before formatting
//! it, so `{{ obj.created|date:"H:i" }}` shows the time in `settings.TIME_ZONE`.
//! The Rust engine did no conversion at all: it formatted whatever offset the
//! serializer handed it, which under `USE_TZ=True` is UTC. Every rendered
//! timestamp was off by the UTC offset — in the configuration `djust new`
//! generates, since the scaffold sets `USE_TZ = True`
//! (`scaffolding/templates.py:171`).
//!
//! ## Why a thread-local and not a `Context` field
//!
//! The obvious place is `Context`, which already carries per-render config
//! (`auto_call`, ADR-024). It was rejected for a reason worth recording: there
//! are seven `Context` construction sites feeding a render, `set_auto_call` is
//! called at only three of them, and the other two
//! (`djust_live/src/lib.rs:1496`, `:1537`) already silently miss it. Adding a
//! second setter every construction site must remember is adding a second
//! instance of a drift that has already happened once (#1646).
//!
//! A thread-local is also what Django itself does — `timezone._active` is a
//! `Local()`, and `get_current_timezone()` reads it. Mirroring that shape means
//! per-request `timezone.activate()` works the same way here as there, and it
//! is *correct under concurrency* in a way a process global would not be:
//! djust renders run in `sync_to_async` worker threads, so two connections with
//! different activated zones render concurrently on different threads.
//!
//! ## Why a bundled tz database
//!
//! A fixed UTC offset passed per render would be free and wrong: one render can
//! straddle a DST boundary (`America/New_York` is -0500 in January and -0400 in
//! August, and a table of timestamps spanning six months hits both). Correct
//! conversion needs real transition data, so a compiled tz database is a
//! dependency rather than an arithmetic offset. Measured cost before adopting
//! (`chrono-tz`, #2209): **+1.16 MB raw** on the extension, **+136 KB
//! compressed** — the compressed figure being what a wheel actually ships.
//! Since #4222 the database is `jiff`'s bundled copy (`tzdb-bundle-always`):
//! `chrono`/`chrono-tz` were marked unmaintained, and `jiff` is one crate for
//! both the arithmetic and the zones. Bundled, not read from the host's
//! `/usr/share/zoneinfo`, so a slim container image renders the same zones.

use std::cell::RefCell;

use jiff::tz::TimeZone;

thread_local! {
    /// The zone `date`/`time` formatting converts into, or `None` for "leave
    /// the value in the offset it arrived with" (`USE_TZ = False`, or no
    /// Django settings at all when this crate is embedded directly).
    static ACTIVE_TZ: RefCell<Option<TimeZone>> = const { RefCell::new(None) };
}

/// Set the active zone for THIS thread. `None` disables conversion.
///
/// Returns `false` when `name` is `Some` but not a recognised IANA zone, having
/// left the previous value untouched. The caller decides what to do with that —
/// the Python side logs once and carries on unconverted, because a bad
/// `TIME_ZONE` should not take a page down. Note Django would itself have
/// raised on such a value long before this point; the guard exists for embedders
/// and for a `TIME_ZONE` naming a zone this tzdata vintage does not carry.
pub fn set_active_timezone(name: Option<&str>) -> bool {
    match name {
        None => {
            ACTIVE_TZ.with(|c| *c.borrow_mut() = None);
            true
        }
        Some(n) => match parse_zone(n) {
            Some(tz) => {
                ACTIVE_TZ.with(|c| *c.borrow_mut() = Some(tz));
                true
            }
            None => false,
        },
    }
}

/// Look up an IANA zone name EXACTLY as spelled.
///
/// `jiff`'s lookup is case-insensitive (`america/new_york` finds
/// `America/New_York`), while `chrono-tz`'s `FromStr` — and Python's
/// `zoneinfo` on a case-sensitive filesystem, which is what Django validates
/// `TIME_ZONE` with — are not. Accepting a spelling Django would refuse is a
/// silent widening, so the canonical name must round-trip. Used for both the
/// active zone and the default-zone lookup, one rule for both (#4222).
pub fn parse_zone(name: &str) -> Option<TimeZone> {
    let tz = TimeZone::get(name).ok()?;
    (tz.iana_name() == Some(name)).then_some(tz)
}

/// The active zone for this thread, if any.
///
/// A `jiff` `TimeZone` is a cheap reference-counted handle, so the clone is not
/// a copy of the zone's transition table.
pub fn active_timezone() -> Option<TimeZone> {
    ACTIVE_TZ.with(|c| c.borrow().clone())
}

/// The active zone's IANA name, for asserting the wiring took effect from
/// Python. A setter with no getter cannot be tested end to end (#2017).
pub fn active_timezone_name() -> Option<String> {
    ACTIVE_TZ.with(|c| {
        c.borrow()
            .as_ref()
            .and_then(|tz| tz.iana_name().map(str::to_string))
    })
}
