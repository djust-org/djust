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
//! conversion needs real transition data, so `chrono-tz` is a dependency rather
//! than an arithmetic offset. Measured cost before adopting: **+1.16 MB raw**
//! on the extension, **+136 KB compressed** — the compressed figure being what
//! a wheel actually ships.

use std::cell::RefCell;

use chrono_tz::Tz;

thread_local! {
    /// The zone `date`/`time` formatting converts into, or `None` for "leave
    /// the value in the offset it arrived with" (`USE_TZ = False`, or no
    /// Django settings at all when this crate is embedded directly).
    static ACTIVE_TZ: RefCell<Option<Tz>> = const { RefCell::new(None) };
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
        Some(n) => match n.parse::<Tz>() {
            Ok(tz) => {
                ACTIVE_TZ.with(|c| *c.borrow_mut() = Some(tz));
                true
            }
            Err(_) => false,
        },
    }
}

/// The active zone for this thread, if any.
pub fn active_timezone() -> Option<Tz> {
    ACTIVE_TZ.with(|c| *c.borrow())
}

/// The active zone's IANA name, for asserting the wiring took effect from
/// Python. A setter with no getter cannot be tested end to end (#2017).
pub fn active_timezone_name() -> Option<String> {
    active_timezone().map(|tz| tz.name().to_string())
}
