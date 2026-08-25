//! The active render locale's number format (#2221).
//!
//! Django localizes a number on its way into the page: `{{ n }}` for `1234.5`
//! renders `1,234.5`, and under `LANGUAGE_CODE="de"` it renders `1.234,5`. The
//! Rust engine formatted with Rust's defaults and produced `1234.5` for every
//! locale.
//!
//! ## This is not a non-English problem
//!
//! The easy mistake is to file this as "German projects render the wrong
//! separator". `USE_THOUSAND_SEPARATOR` applies **regardless of language**, so
//! the divergence is present in the default English configuration:
//! Django renders `1,234,567` where djust rendered `1234567`.
//!
//! Also worth recording, because the flag name invites the opposite
//! assumption: **`USE_L10N` is inert** in Django 5.2 — verified across the full
//! `USE_L10N` × `USE_THOUSAND_SEPARATOR` × language matrix, where flipping it
//! changed nothing. Django 5.0 removed it as a toggle and localization is
//! always on. Only the active language (decimal separator) and
//! `USE_THOUSAND_SEPARATOR` (grouping) matter, which is why neither this module
//! nor the Python side reads `USE_L10N`.
//!
//! ## Why the parameters come FROM Python
//!
//! The opposite of the timezone fix (#2209), deliberately. Timezone needed a
//! self-contained database (`chrono-tz`) and nothing from Python but a zone
//! name, so the work happens in Rust. Locale needs **Django's own data** —
//! `django/conf/locale/*/formats.py` — and reimplementing that here would be a
//! fork of it rather than a use of it. So Python resolves three values per
//! render and pushes them down; Rust only applies them.
//!
//! Thread-local for the same reason the timezone is: renders run in
//! `sync_to_async` worker threads, and Django's own active language is itself
//! a thread-local.

use std::cell::RefCell;

/// How to render a number, as Django's `numberformat.format` would.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct NumberFormat {
    /// `"."` in en-us, `","` in de.
    pub decimal_sep: String,
    /// `","` in en-us, `"."` in de, U+00A0 in fr.
    pub thousand_sep: String,
    /// Digits per group from the decimal point outwards. Django allows a
    /// sequence (Indian grouping is `[3, 2, 0]`); a scalar `3` arrives as
    /// `[3, 0]`. A `0` entry means "keep using the previous interval".
    pub grouping: Vec<usize>,
    /// `settings.USE_THOUSAND_SEPARATOR`. When false the decimal separator is
    /// still localized — only grouping is suppressed.
    pub use_grouping: bool,
}

thread_local! {
    static NUMBER_FORMAT: RefCell<Option<NumberFormat>> = const { RefCell::new(None) };
}

/// Set this thread's number format. `None` restores unlocalized rendering,
/// which is what an embedder with no Django settings gets.
pub fn set_number_format(fmt: Option<NumberFormat>) {
    NUMBER_FORMAT.with(|c| *c.borrow_mut() = fmt);
}

/// This thread's number format, if any.
pub fn number_format() -> Option<NumberFormat> {
    NUMBER_FORMAT.with(|c| c.borrow().clone())
}

/// Localize a plain numeric string (`"-1234.5"`) the way Django would.
///
/// Takes the already-rendered digits rather than a number so it can serve both
/// callers without duplicating float formatting: `{{ n }}` passes `Display`'s
/// output and `floatformat` passes its fixed-precision output, and neither has
/// to agree with the other about rounding.
///
/// Returns the input unchanged when no format is active, so every call site is
/// a no-op by default.
pub fn localize_number(rendered: &str) -> String {
    let Some(fmt) = number_format() else {
        return rendered.to_string();
    };
    localize_number_with(rendered, &fmt)
}

/// The formatting itself, split out so it can be tested without touching the
/// thread-local.
pub fn localize_number_with(rendered: &str, fmt: &NumberFormat) -> String {
    // Anything that is not a plain decimal number passes through untouched —
    // notably `inf`, `NaN` and exponent forms, which have no separators to
    // place and which Django's own fast paths also leave alone.
    let (sign, body) = match rendered.strip_prefix('-') {
        Some(rest) => ("-", rest),
        None => ("", rendered),
    };
    if body.is_empty() || !body.chars().all(|c| c.is_ascii_digit() || c == '.') {
        return rendered.to_string();
    }

    let (int_part, dec_part) = match body.split_once('.') {
        Some((i, d)) => (i, Some(d)),
        None => (body, None),
    };

    // Django: `use_grouping = ... and grouping != 0`. A leading 0 disables
    // grouping outright, which is how locales without digit grouping are
    // expressed.
    let grouped = if fmt.use_grouping && fmt.grouping.first().copied().unwrap_or(0) != 0 {
        group_digits(int_part, &fmt.grouping, &fmt.thousand_sep)
    } else {
        int_part.to_string()
    };

    match dec_part {
        Some(d) => format!("{sign}{grouped}{}{d}", fmt.decimal_sep),
        None => format!("{sign}{grouped}"),
    }
}

/// Django's grouping walk, digit by digit from the right.
///
/// Mirrors `django/utils/numberformat.py` rather than assuming groups of three:
/// the interval list is consumed as it goes, and `intervals.pop(0) or
/// active_interval` means a `0` entry KEEPS the previous width rather than
/// disabling grouping mid-number. Indian grouping (`[3, 2, 0]`) is the case
/// that distinguishes a faithful port from a three-at-a-time loop —
/// `1234567` becomes `12,34,567`, not `1,234,567`.
fn group_digits(int_part: &str, grouping: &[usize], sep: &str) -> String {
    let mut intervals = grouping.to_vec();
    if intervals.is_empty() {
        return int_part.to_string();
    }
    let mut active = intervals.remove(0);
    let mut out: Vec<String> = Vec::new();
    let mut count = 0usize;
    for ch in int_part.chars().rev() {
        if count > 0 && count == active {
            if !intervals.is_empty() {
                let next = intervals.remove(0);
                if next != 0 {
                    active = next;
                }
            }
            out.push(sep.chars().rev().collect());
            count = 0;
        }
        out.push(ch.to_string());
        count += 1;
    }
    out.concat().chars().rev().collect()
}
