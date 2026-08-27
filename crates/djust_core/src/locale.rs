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
    static UNLOCALIZED_NUMBER_FORMAT: RefCell<Option<NumberFormat>> = const { RefCell::new(None) };
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

/// Set this thread's `use_l10n=False` number format (#2266).
///
/// The SECOND format, not a variation on the first. Django's `get_format`
/// branches before it ever looks at the active language:
///
/// ```python
/// if use_l10n is False:
///     return getattr(settings, format_type)   # the RAW setting
/// ```
///
/// so `floatformat`'s `u` suffix reads `settings.DECIMAL_SEPARATOR` /
/// `THOUSAND_SEPARATOR` / `NUMBER_GROUPING` directly and the active locale's
/// `formats.py` never participates. The two cannot be derived from each other:
/// under `de` with no overrides the localized separator is `,` and the
/// unlocalized one is `.`, and under `DECIMAL_SEPARATOR="!"` with
/// `LANGUAGE_CODE="en"` it is the other way round. Both are therefore resolved
/// on the Python side and pushed down together (`render_env.apply_number_format`).
///
/// `use_grouping` is always FALSE on this one, and that is Django's arithmetic
/// rather than a simplification: `numberformat.format` computes
/// `use_grouping = (use_l10n or ...) and USE_THOUSAND_SEPARATOR`, which is
/// `False` whenever `use_l10n` is `False`, and only then ORs in
/// `force_grouping`. So `u` never groups and `gu` groups iff the RAW
/// `NUMBER_GROUPING` is non-zero — measured: with `NUMBER_GROUPING` left at its
/// default `0`, Django renders `6666.67` for `"2gu"`, not `6,666.67`.
pub fn set_unlocalized_number_format(fmt: Option<NumberFormat>) {
    UNLOCALIZED_NUMBER_FORMAT.with(|c| *c.borrow_mut() = fmt);
}

/// This thread's `use_l10n=False` number format, if any.
pub fn unlocalized_number_format() -> Option<NumberFormat> {
    UNLOCALIZED_NUMBER_FORMAT.with(|c| c.borrow().clone())
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
    localize_number_forced(rendered, false)
}

/// [`localize_number`] with Django's `force_grouping` flag (#2253).
///
/// `floatformat`'s `g` suffix means *"group by the THOUSAND_SEPARATOR whether
/// or not `USE_THOUSAND_SEPARATOR` is on"*, which in `numberformat.format` is
/// `use_grouping = (use_l10n and USE_THOUSAND_SEPARATOR) or force_grouping`.
/// Note the `and grouping != 0` that follows it in Django survives: a locale
/// with no digit grouping stays ungrouped even under `g`.
///
/// [`localize_number`] delegates here rather than the two having parallel
/// bodies, so the plain and forced paths cannot drift (#1646).
pub fn localize_number_forced(rendered: &str, force_grouping: bool) -> String {
    apply_active_format(number_format(), rendered, force_grouping)
}

/// [`localize_number_forced`] against the `use_l10n=False` format (#2266).
///
/// What `floatformat`'s `u`/`gu` suffixes reach for. Returns the digits
/// unchanged when no unlocalized format has been pushed, which is what an
/// embedder with no Django settings gets and what every caller got before
/// #2266 — the change is additive, never a re-format of something that already
/// agreed.
pub fn localize_number_unlocalized(rendered: &str, force_grouping: bool) -> String {
    apply_active_format(unlocalized_number_format(), rendered, force_grouping)
}

/// The shared body of the two `localize_number_*` entry points.
///
/// Extracted rather than copied so the localized and unlocalized arms cannot
/// drift in how they apply `force_grouping` or handle an absent format
/// (#1646) — the exact drift class that put the `u` gap here in the first
/// place.
fn apply_active_format(fmt: Option<NumberFormat>, rendered: &str, force_grouping: bool) -> String {
    let Some(mut fmt) = fmt else {
        return rendered.to_string();
    };
    fmt.use_grouping |= force_grouping;
    localize_number_with(rendered, &fmt)
}

/// The formatting itself, split out so it can be tested without touching the
/// thread-local.
///
/// Handles both the plain form (`-1234.5`) and the SCIENTIFIC form
/// (`1.230e-250`), which is what a `Decimal` past Django's >200-digit cutoff
/// renders as (#2242).
pub fn localize_number_with(rendered: &str, fmt: &NumberFormat) -> String {
    // Django's scientific branch, mirrored rather than approximated
    // (`django/utils/numberformat.py`):
    //
    //     number = "{:e}".format(number)
    //     coefficient, exponent = number.split("e")
    //     coefficient = format(coefficient, ...)   # the SAME localisation path
    //     return "{}e{}".format(coefficient, exponent)
    //
    // Two things that reading the table in #2242 would not have told us, and
    // that only reading Django gives:
    //
    // * the **exponent passes through verbatim** — sign and all. It is not
    //   localized and not reformatted, so `1,230e-250` under `de`, never
    //   `1,230e-250,0` or a grouped `e-1.250`.
    // * the coefficient goes through the FULL path, grouping included. That is
    //   a no-op in practice — `{:e}` leaves exactly one digit before the point
    //   — but recursing rather than special-casing is what keeps the two arms
    //   from drifting (#1646).
    //
    // Everything a non-numeric coefficient would have done before is preserved:
    // `localize_plain` returns its input unchanged for anything that is not
    // digits-and-a-point, and the rejoin is byte-exact, so `abcE+5` still comes
    // back as `abcE+5`.
    if let Some(i) = rendered.find(['e', 'E']) {
        let (coefficient, suffix) = rendered.split_at(i);
        // `suffix` keeps the `e`/`E`. Only treat this as scientific when what
        // follows is an exponent: an optional sign then at least one digit.
        // Without the check `1.5exyz` would be localized to `1,5exyz` — Django
        // does that only because its string branch never looks for an `e` at
        // all, and inheriting the accident is worse than passing it through.
        let exp_digits = suffix[1..].strip_prefix(['+', '-']).unwrap_or(&suffix[1..]);
        if !exp_digits.is_empty() && exp_digits.bytes().all(|b| b.is_ascii_digit()) {
            return format!("{}{}", localize_plain(coefficient, fmt), suffix);
        }
        return rendered.to_string();
    }
    localize_plain(rendered, fmt)
}

/// The non-exponent form — Django's `format()` below its scientific branch.
fn localize_plain(rendered: &str, fmt: &NumberFormat) -> String {
    // Anything that is not a plain decimal number passes through untouched —
    // notably `inf` and `NaN`, which have no separators to place and which
    // Django's own fast paths also leave alone. Exponent forms were in that
    // list until #2242; they are now split above and reach here as their
    // coefficient.
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
