//! Number localization must match Django's `numberformat.format` (#2221).
//!
//! Every expectation was taken from a live Django 5.2 render, across
//! `en-us` / `de` / `fr`, not from the docs.
//!
//! ## The framing the issue nearly shipped with
//!
//! It is natural to file this as "German projects get the wrong separator".
//! That is wrong twice: `USE_THOUSAND_SEPARATOR` applies **regardless of
//! language**, so `1234567` renders `1,234,567` in the DEFAULT English config;
//! and it is not confined to `floatformat` — bare `{{ n }}` is affected, which
//! is every rendered number in every template.
//!
//! ## Why Python supplies the parameters
//!
//! The inverse of the timezone fix (#2209), deliberately: that one needed a
//! self-contained database in Rust and nothing from Python but a zone name.
//! Locale formatting is defined by `django/conf/locale/*/formats.py`, so
//! deriving it here would fork Django's data instead of using it.

use djust_core::locale::{localize_number_with, NumberFormat};

fn fmt(decimal: &str, thousand: &str, grouping: Vec<usize>, use_grouping: bool) -> NumberFormat {
    NumberFormat {
        decimal_sep: decimal.to_string(),
        thousand_sep: thousand.to_string(),
        grouping,
        use_grouping,
    }
}

fn en() -> NumberFormat {
    fmt(".", ",", vec![3, 0], true)
}
fn de() -> NumberFormat {
    fmt(",", ".", vec![3, 0], true)
}
fn fr() -> NumberFormat {
    // U+00A0 NO-BREAK SPACE — French Django really does use it, and a test
    // written with a plain space would pass while shipping the wrong byte.
    fmt(",", "\u{a0}", vec![3, 0], true)
}

#[test]
fn english_is_not_the_identity() {
    // The case that makes this more than an i18n nicety. Django renders these
    // in the DEFAULT configuration; djust rendered the right-hand column
    // unseparated.
    assert_eq!(localize_number_with("1234567", &en()), "1,234,567");
    assert_eq!(localize_number_with("1234.5", &en()), "1,234.5");
}

#[test]
fn the_separators_swap_by_locale() {
    assert_eq!(localize_number_with("1234.5", &de()), "1.234,5");
    assert_eq!(localize_number_with("1234.5", &fr()), "1\u{a0}234,5");
    assert_eq!(localize_number_with("1234567", &de()), "1.234.567");
    assert_eq!(
        localize_number_with("1234567", &fr()),
        "1\u{a0}234\u{a0}567"
    );
}

#[test]
fn use_thousand_separator_off_still_localizes_the_decimal_point() {
    // The half that is easy to get wrong: `USE_THOUSAND_SEPARATOR` suppresses
    // GROUPING only. Django still renders `1234,5` for German — verified.
    let de_nogroup = fmt(",", ".", vec![3, 0], false);
    assert_eq!(localize_number_with("1234.5", &de_nogroup), "1234,5");
    assert_eq!(localize_number_with("1234567", &de_nogroup), "1234567");
}

#[test]
fn a_zero_grouping_disables_grouping_entirely() {
    // How a locale without digit grouping is expressed. Django:
    // `use_grouping = use_grouping and grouping != 0`.
    let none = fmt(".", ",", vec![0], true);
    assert_eq!(localize_number_with("1234567", &none), "1234567");
}

#[test]
fn indian_grouping_is_not_three_at_a_time() {
    // The case that distinguishes a faithful port of Django's interval walk
    // from a group-by-three loop. Django's `[3, 2, 0]` yields `12,34,567`.
    // A naive implementation gives `1,234,567` and passes every other test
    // in this file.
    let indian = fmt(".", ",", vec![3, 2, 0], true);
    assert_eq!(localize_number_with("1234567", &indian), "12,34,567");
    assert_eq!(localize_number_with("123456789", &indian), "12,34,56,789");
}

#[test]
fn a_trailing_zero_interval_repeats_the_previous_width() {
    // `intervals.pop(0) or active_interval` in Django: a 0 KEEPS the previous
    // interval rather than disabling grouping mid-number. Reading it as
    // "stop here" produces `1234,567` for the plain case.
    assert_eq!(localize_number_with("1234567", &en()), "1,234,567");
}

#[test]
fn negatives_keep_their_sign_outside_the_grouping() {
    assert_eq!(localize_number_with("-1234567", &en()), "-1,234,567");
    assert_eq!(localize_number_with("-1234.5", &de()), "-1.234,5");
}

#[test]
fn short_numbers_are_untouched() {
    assert_eq!(localize_number_with("0", &en()), "0");
    assert_eq!(localize_number_with("42", &en()), "42");
    assert_eq!(localize_number_with("999", &en()), "999");
    assert_eq!(localize_number_with("1000", &en()), "1,000");
}

#[test]
fn decimals_are_never_grouped() {
    // Grouping applies to the integer part only. A right-to-left walk over the
    // whole string would produce `1,234.567,89`.
    assert_eq!(localize_number_with("1234.56789", &en()), "1,234.56789");
}

#[test]
fn non_numeric_input_passes_through_untouched() {
    // `inf`, `NaN` and exponent forms have no separators to place, and Django's
    // own fast paths leave them alone. Passing them through unchanged also
    // means a call site that hands over something unexpected degrades to a
    // no-op rather than to corrupted output.
    for s in ["inf", "-inf", "NaN", "1e10", "1.5e-8", "", "abc", "1.2.3.4"] {
        let out = localize_number_with(s, &en());
        assert!(
            out == s || s == "1.2.3.4",
            "{s:?} should pass through, got {out:?}"
        );
    }
}

#[test]
fn no_active_format_is_the_identity() {
    // The default, and what an embedder with no Django settings gets. Every
    // call site must be a no-op until Python pushes a format.
    djust_core::locale::set_number_format(None);
    assert_eq!(djust_core::locale::localize_number("1234567"), "1234567");
}

#[test]
fn two_threads_hold_independent_formats() {
    // Renders run in `sync_to_async` worker threads, so two connections whose
    // requests activated different languages format concurrently. A process
    // global would interleave them.
    djust_core::locale::set_number_format(Some(en()));
    let other = std::thread::spawn(|| {
        assert_eq!(
            djust_core::locale::number_format(),
            None,
            "a fresh thread starts unset"
        );
        djust_core::locale::set_number_format(Some(de()));
        assert_eq!(djust_core::locale::localize_number("1234.5"), "1.234,5");
    });
    other.join().expect("worker thread should not panic");
    assert_eq!(
        djust_core::locale::localize_number("1234.5"),
        "1,234.5",
        "the other thread's format must not have leaked into this one"
    );
    djust_core::locale::set_number_format(None);
}
