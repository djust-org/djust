//! A scientific-form number's COEFFICIENT is localized; its exponent is not (#2242).
//!
//! Past Django's `>200`-digit cutoff a `Decimal` renders in scientific form
//! (`crates/djust_core/src/lib.rs`'s `expand_decimal_exponent`, rule 2). Django
//! localizes the coefficient of that form; `localize_number_with` used to bail
//! on any string containing an `e`, so `1.230E-250` rendered `1.230e-250` under
//! `de` where Django gives `1,230e-250`.
//!
//! Every expectation here is `django/utils/numberformat.py`'s scientific branch
//! read verbatim, not inferred from the issue's table:
//!
//! ```python
//! number = "{:e}".format(number)
//! coefficient, exponent = number.split("e")
//! coefficient = format(coefficient, decimal_sep, decimal_pos, grouping, ...)
//! return "{}e{}".format(coefficient, exponent)
//! ```
//!
//! The two facts that reading it settles and guessing would not: the exponent
//! is **re-emitted verbatim** — never localized, never grouped, sign kept — and
//! the coefficient goes through the FULL path rather than a decimal-separator
//! swap.
//!
//! The end-to-end differential against a live Django lives in
//! `python/tests/test_scientific_localization_2242.py`; this file pins the
//! formatting rule itself, without a `Decimal` or a thread-local in the way.

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
    // U+00A0 NO-BREAK SPACE, as in the #2221 suite — a plain space would pass
    // while shipping the wrong byte.
    fmt(",", "\u{a0}", vec![3, 0], true)
}

#[test]
fn the_coefficient_is_localized() {
    // The issue's own two rows, and the case that makes it more than an
    // i18n nicety: English is unchanged, so a regression here is invisible in
    // the default configuration.
    assert_eq!(localize_number_with("1.230e-250", &de()), "1,230e-250");
    assert_eq!(localize_number_with("-1.5e+300", &de()), "-1,5e+300");
    assert_eq!(localize_number_with("1.230e-250", &fr()), "1,230e-250");
    assert_eq!(localize_number_with("1.230e-250", &en()), "1.230e-250");
}

#[test]
fn the_exponent_is_re_emitted_verbatim() {
    // Django rejoins with `"{}e{}".format(coefficient, exponent)` — the
    // exponent never re-enters `format()`. So its own `.`-free digits are NOT
    // grouped even when they are long enough to be, and its sign survives.
    //
    // `e+1234567` is the case that distinguishes "passed through" from
    // "localized too": a localized exponent would read `e+1.234.567` under
    // `de`.
    assert_eq!(localize_number_with("1.5e+1234567", &de()), "1,5e+1234567");
    assert_eq!(localize_number_with("1.5e-1234567", &de()), "1,5e-1234567");
    // No explicit sign is equally untouched — Rust's own f64 `Display` emits
    // `1e300`, without the `+` Python writes.
    assert_eq!(localize_number_with("1.5e300", &de()), "1,5e300");
}

#[test]
fn a_coefficient_with_no_fraction_is_unchanged() {
    // `format(Decimal('0E-250'), 'e')` is `0e-250`: a bare coefficient with
    // nothing for the decimal separator to replace. Django returns it as-is,
    // in every locale.
    assert_eq!(localize_number_with("0e-250", &de()), "0e-250");
    assert_eq!(localize_number_with("9e+250", &fr()), "9e+250");
    assert_eq!(localize_number_with("-9e+250", &de()), "-9e+250");
}

#[test]
fn the_coefficient_takes_the_full_path_including_grouping() {
    // Django recurses into `format()` for the coefficient, so grouping applies.
    // `"{:e}"` leaves exactly one digit before the point, so no `Decimal` can
    // reach this — but recursing rather than special-casing the separator is
    // what keeps the two arms from drifting (#1646), and this pins that it
    // really is the same path.
    assert_eq!(
        localize_number_with("1234567.5e+10", &en()),
        "1,234,567.5e+10"
    );
    assert_eq!(
        localize_number_with("1234567.5e+10", &de()),
        "1.234.567,5e+10"
    );
    // Indian grouping, the shape that separates a faithful port from a
    // three-at-a-time loop.
    let indian = fmt(".", ",", vec![3, 2, 0], true);
    assert_eq!(
        localize_number_with("1234567e+10", &indian),
        "12,34,567e+10"
    );
}

#[test]
fn grouping_off_still_localizes_the_coefficients_separator() {
    // `USE_THOUSAND_SEPARATOR=False` suppresses grouping only; the decimal
    // separator is still the locale's.
    let de_nogroup = fmt(",", ".", vec![3, 0], false);
    assert_eq!(
        localize_number_with("1.230e-250", &de_nogroup),
        "1,230e-250"
    );
}

#[test]
fn a_trailing_e_is_not_an_exponent() {
    // MECHANISM 2, the exponent-shape guard, tested where mechanism 1 cannot
    // reach: these all contain an `e` but no exponent after it, so the whole
    // string must pass through as it did before #2242. Without the guard the
    // coefficient arm would localize the part before the `e` and emit
    // `1,5exyz` / `1,5e`.
    for input in ["1.5e", "1.5exyz", "1.5e+", "1.5e-", "1.5e+x", "e", "1.5E"] {
        assert_eq!(
            localize_number_with(input, &de()),
            input,
            "a non-exponent `e` must pass through unchanged: {input:?}"
        );
    }
}

#[test]
fn a_non_numeric_coefficient_passes_through() {
    // `expand_decimal_exponent` returns its input untouched for a
    // `Value::Decimal` holding non-digits — reachable, because the binary tag
    // lets one hold any string — so `localize_number` really does see these.
    // The rejoin is byte-exact, so they come back as they went in, uppercase
    // `E` included.
    for input in ["abce+5", "abcE+5", "xyze-200", "-abce+5"] {
        assert_eq!(localize_number_with(input, &de()), input);
    }
}

#[test]
fn the_non_scientific_path_is_untouched() {
    // The guard on the whole change: every ordinary value must render exactly
    // as it did before #2242. `inf` / `NaN` carry no `e` and so never enter the
    // scientific arm at all.
    assert_eq!(localize_number_with("1234.56", &de()), "1.234,56");
    assert_eq!(localize_number_with("19.99", &de()), "19,99");
    assert_eq!(localize_number_with("0.000000001", &de()), "0,000000001");
    assert_eq!(
        localize_number_with("1234567", &fr()),
        "1\u{a0}234\u{a0}567"
    );
    assert_eq!(localize_number_with("-1234.5", &en()), "-1,234.5");
    assert_eq!(localize_number_with("inf", &de()), "inf");
    assert_eq!(localize_number_with("NaN", &de()), "NaN");
    assert_eq!(localize_number_with("", &de()), "");
}

#[test]
fn an_uppercase_exponent_marker_is_handled_the_same_way() {
    // Not reachable from a `Decimal` — `expand_decimal_exponent` emits
    // lowercase — but the split accepts both, so a tag-supplied `1.5E+300`
    // does not silently keep the `.` a German page would not want. Pinned so
    // the behaviour is a decision rather than an accident.
    assert_eq!(localize_number_with("1.5E+300", &de()), "1,5E+300");
}
