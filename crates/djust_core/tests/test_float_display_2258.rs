//! `Display` for `Value::Float` is `numberformat.format`, not Rust's `{}` (#2258).
//!
//! Django's `{{ }}` path for a float is two steps:
//!
//! ```text
//! if isinstance(number, float) and "e" in str(number).lower():
//!     number = Decimal(str(number))
//! if isinstance(number, Decimal):   # >200-digit cut-off, else "{:f}"
//! else:                             str_number = str(number)
//! ```
//!
//! Rust's `{}` is neither step: it never uses exponent notation and spells the
//! non-finite values `NaN`/`inf` where Python gives `nan`/`inf`. The `{:.1}`
//! guard #2203 added was a partial hand-port of the trailing-`.0` case and
//! could see neither.
//!
//! Every expected value below was READ OFF a live Django 5.2 render, not
//! derived — the whole reason the two-step rule is easy to get wrong is that it
//! spells `1e20` and `1e300` differently for a reason that is about the DIGIT
//! COUNT, not the variant. The Python differential lives in
//! `python/tests/test_float_display_2258.py`, which renders the same table
//! through Django itself so it cannot drift from this one.

use djust_core::Value;

// `Display` reads a process-global; every test in this binary must hold the
// lock, not only the one that toggles it. See `tests/value_repr_flag/mod.rs`.
mod value_repr_flag;
use value_repr_flag::FlagGuard;

fn render(f: f64) -> String {
    Value::Float(f).to_string()
}

#[test]
fn the_three_shapes_the_issue_named() {
    let _g = FlagGuard::on();
    // Past Django's `abs(exponent) + len(digits) > 200` cut-off, so scientific.
    assert_eq!(render(1e300), "1e+300");
    // Rust writes `NaN`; Python writes `nan`.
    assert_eq!(render(f64::NAN), "nan");
    // `inf` already agreed, and must keep agreeing.
    assert_eq!(render(f64::INFINITY), "inf");
    assert_eq!(render(f64::NEG_INFINITY), "-inf");
}

#[test]
fn the_cut_off_is_the_digit_count_not_the_exponent_form() {
    let _g = FlagGuard::on();
    // The trap: `str(1e20)` IS exponent form, but `numberformat` turns it into a
    // Decimal and EXPANDS it, because 20 + 1 digits is under the cut-off. Only
    // past 200 does Django keep the exponent. Rendering `1e+20` here — the
    // obvious reading of "match Python's repr" — would be wrong.
    assert_eq!(render(1e20), "100000000000000000000");
    assert_eq!(render(1e16), "10000000000000000");
    assert_eq!(render(1e100), format!("1{}", "0".repeat(100)));
    // 199 + 1 = 200, not > 200: still expanded.
    assert_eq!(render(1e199), format!("1{}", "0".repeat(199)));
    // 200 + 1 = 201: scientific.
    assert_eq!(render(1e200), "1e+200");
    assert_eq!(render(1e-300), "1e-300");
    assert_eq!(render(5e-324), "5e-324");
    // Just inside, from the small side.
    assert_eq!(render(1e-199), format!("0.{}1", "0".repeat(198)));
}

#[test]
fn the_ordinary_shapes_are_unchanged() {
    let _g = FlagGuard::on();
    // The `.0` case #2203 fixed, and everything a template renders every day.
    assert_eq!(render(1.0), "1.0");
    assert_eq!(render(-1.0), "-1.0");
    assert_eq!(render(0.0), "0.0");
    assert_eq!(render(-0.0), "-0.0");
    assert_eq!(render(19.99), "19.99");
    assert_eq!(render(1e15), "1000000000000000.0");
    assert_eq!(render(1e-4), "0.0001");
    assert_eq!(render(1e-5), "0.00001");
    assert_eq!(render(123456789.125), "123456789.125");
}

#[test]
fn the_legacy_repr_path_is_untouched() {
    // `django_value_repr = False` is the #2203 escape hatch for a template that
    // embeds a value in a script block. #2258 is a Django-PARITY fix and belongs
    // to the ON arm only; the OFF arm keeps Rust's spelling, which is what the
    // flag promises.
    let _g = FlagGuard::off();
    assert_eq!(
        render(f64::NAN),
        "NaN",
        "the OFF path must keep the pre-1.2 rendering"
    );
    assert_eq!(
        render(1e300).len(),
        301,
        "the OFF path must keep Rust's expansion"
    );
    assert_eq!(render(1.0), "1", "and its integral-float spelling");
}
