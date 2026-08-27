//! `stringformat:"d"` prints `int(value)`, not a saturated `i64` (#2265).
//!
//! The `d`/`i` arm computed an `i64` through `as_f64()`, so it carried BOTH of
//! the losses #2253 had already fixed one filter over: a double holds ~15
//! significant digits, so `int()` was off by one from 2^53 up, and `as i64`
//! SATURATES, so every value past 2^63 rendered `9223372036854775807` — a
//! fabricated constant where an id or a money column was meant.
//!
//! Django is `("%" + arg) % value`, catching `(ValueError, TypeError)` and
//! returning `""`. CPython's `%d` takes an `int`, a `bool`, a FINITE `float` or
//! a FINITE `Decimal` and truncates toward zero; it raises `TypeError` for a
//! `str`/`None`/list/dict and `ValueError` for a NaN. Every expectation below
//! was read off CPython 3.12 and confirmed against a live Django render; the
//! Python-side differential is in `python/tests/test_stringformat_int_2265.py`.

use djust_core::{Context, Value};
use djust_templates::Template;

fn render(source: &str, v: Value) -> String {
    let mut ctx = Context::new();
    ctx.set("p".to_string(), v);
    Template::new(source)
        .expect("template should parse")
        .render(&ctx)
        .expect("template should render")
}

fn d(v: Value) -> String {
    render("{{ p|stringformat:\"d\" }}", v)
}

#[test]
fn the_three_groups_the_issue_measured() {
    // Group 1: `int()` through an f64 was off by one from 2^53 up.
    assert_eq!(
        d(Value::Decimal("9007199254740993".into())),
        "9007199254740993"
    );
    // Group 2: past 2^63, `as i64` saturated to a fabricated constant.
    assert_eq!(
        d(Value::Decimal("12345678901234567890.123456789".into())),
        "12345678901234567890"
    );
    // Group 2, sharpest: a value with no plausible reading as `i64::MAX`.
    let wide = d(Value::Decimal("1E+400".into()));
    assert_eq!(wide.len(), 401, "`%d` prints all 401 digits");
    assert!(wide.starts_with('1'));
    // Group 3: Django's `%d` RAISES for a non-numeric and the filter returns "".
    for v in [
        Value::String("abc".into()),
        Value::String("1.5".into()),
        // A NUMERIC string too: `"%d" % "42"` raises. The old
        // `parse::<i64>()` fallback disagreed in the other direction.
        Value::String("42".into()),
        Value::None,
        Value::Missing,
        Value::List(vec![Value::Integer(1)]),
    ] {
        assert_eq!(d(v.clone()), "", "{v:?} should render empty");
    }
}

#[test]
fn a_python_int_past_i64_prints_its_digits() {
    // The #2260 half arriving at this filter: with `Value::BigInt` the digits
    // are exact BEFORE the filter runs, and the filter must not throw them away
    // on the way out. Fixing either issue alone leaves this cell wrong.
    assert_eq!(
        d(Value::BigInt("12345678901234567890".into())),
        "12345678901234567890"
    );
    assert_eq!(
        d(Value::BigInt("-12345678901234567890".into())),
        "-12345678901234567890"
    );
}

#[test]
fn a_float_is_the_binary_value_and_not_a_saturated_i64() {
    // `"%d" % 1e300` is the exact binary expansion, which is neither
    // `i64::MAX` nor `10**300`.
    let out = d(Value::Float(1e300));
    assert_eq!(out.len(), 301);
    assert!(
        out.starts_with("1000000000000000052504760255204420248704"),
        "got {out}"
    );
    assert_eq!(d(Value::Float(1.5)), "1");
    assert_eq!(d(Value::Float(-1.5)), "-1");
    assert_eq!(d(Value::Float(-0.0)), "0");
    // `int(nan)` is a ValueError and `int(inf)` an OverflowError. Django catches
    // only the first, so it 500s on the second; djust renders "" for both rather
    // than 500ing on a value it previously rendered. Documented divergence.
    assert_eq!(d(Value::Float(f64::NAN)), "");
    assert_eq!(d(Value::Float(f64::INFINITY)), "");
    assert_eq!(d(Value::Decimal("NaN".into())), "");
}

#[test]
fn zero_padding_is_sign_aware() {
    // `"%05d" % -1` is `-0001`. A `{:0>width$}` on the formatted string pads in
    // FRONT of the minus and gives `000-1`, which is what was here.
    assert_eq!(
        render("{{ p|stringformat:\"05d\" }}", Value::Integer(-1)),
        "-0001"
    );
    assert_eq!(
        render("{{ p|stringformat:\"05d\" }}", Value::Integer(1)),
        "00001"
    );
    assert_eq!(
        render(
            "{{ p|stringformat:\"05d\" }}",
            Value::Decimal("-19.99".into())
        ),
        "-0019"
    );
    // Space padding right-aligns, sign included, and never truncates.
    assert_eq!(
        render("{{ p|stringformat:\"5d\" }}", Value::Integer(-1)),
        "   -1"
    );
    assert_eq!(
        render("{{ p|stringformat:\"2d\" }}", Value::Integer(12345)),
        "12345"
    );
    // `i` is `%i`, an alias of `%d`.
    assert_eq!(
        render("{{ p|stringformat:\"i\" }}", Value::Integer(-1)),
        "-1"
    );
    // A bool is an int to `%d`.
    assert_eq!(d(Value::Bool(true)), "1");
    assert_eq!(d(Value::Bool(false)), "0");
}

#[test]
fn a_decimal_past_the_int_str_digit_limit_is_empty_rather_than_a_hang() {
    // CPython's `sys.get_int_max_str_digits()` is 4300 by default, and past it
    // `"%d" % d` raises `ValueError` — which Django's `except` DOES catch, so
    // `""` is real parity here rather than a fail-soft.
    assert_eq!(d(Value::Decimal("1E+5000".into())), "");
    // And it is what bounds the allocation: twelve bytes that would otherwise
    // ask for 400 MB. CPython hangs on this one; djust returns.
    assert_eq!(d(Value::Decimal("1E+400000000".into())), "");
}
