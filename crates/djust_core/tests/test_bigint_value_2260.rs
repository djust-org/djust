//! A Python `int` past `i64` keeps its digits (#2260).
//!
//! `Value::Integer` is an `i64`; a Python `int` is arbitrary-precision. Past
//! `2**63 - 1` the `i64` arm of `FromPyObject` failed and the next arm that
//! matched was `extract::<f64>()`, so `12345678901234567890` reached the
//! renderer as a binary double and rendered `12345678901234567000`.
//!
//! `Value::BigInt(String)` is the fix — the same shape `Value::Decimal` took in
//! #2214, and a SEPARATE variant for two reasons that are pinned below:
//! `py_repr` (a nested `Decimal` renders `Decimal('123')` where an `int`
//! renders `123`) and `IntoPyObject` (a `Decimal` leaves the process as a
//! `decimal.Decimal`, so a view attribute would stop being an `int`).
//!
//! The PyO3-boundary half lives in `python/tests/test_big_int_value_2260.py`,
//! which needs a real interpreter. Everything here is variant behaviour: the
//! digit invariant, `Display`, both serde encodings, and the round trip.

use djust_core::Value;
use indexmap::IndexMap;

// `Display` reads a process-global; every test in this binary must hold the
// lock, not only the one that toggles it. See `tests/value_repr_flag/mod.rs`.
mod value_repr_flag;
use value_repr_flag::FlagGuard;

const BIG: &str = "12345678901234567890";
const HUGE: &str = "1234567890123456789012345678901234567890123456789012345678901234567890";

#[test]
fn display_writes_every_digit() {
    let _g = FlagGuard::on();
    // `numberformat.format` short-circuits an `int` before either the
    // >200-digit cut-off or the exponent rule, so `str(number)` is the whole
    // answer however many digits there are.
    assert_eq!(Value::BigInt(BIG.into()).to_string(), BIG);
    assert_eq!(Value::BigInt(HUGE.into()).to_string(), HUGE);
    assert_eq!(
        Value::BigInt(format!("-{BIG}")).to_string(),
        format!("-{BIG}")
    );
}

#[test]
fn a_nested_big_int_renders_bare_digits_where_a_decimal_renders_its_constructor() {
    let _g = FlagGuard::on();
    // The first of the two reasons this is not `Value::Decimal`. `repr(int)` is
    // the digits; `repr(Decimal('123'))` is `Decimal('123')`, and `{{ p }}` on a
    // LIST renders its elements through `repr`.
    let as_int = Value::List(vec![Value::BigInt(BIG.into())]).to_string();
    let as_decimal = Value::List(vec![Value::Decimal(BIG.into())]).to_string();
    assert_eq!(as_int, format!("[{BIG}]"));
    assert_eq!(as_decimal, format!("[Decimal('{BIG}')]"));
    assert_ne!(
        as_int, as_decimal,
        "sharing the Decimal variant would render this wrong"
    );
}

#[test]
fn as_f64_is_lossy_on_purpose_and_truthiness_is_not() {
    // Same contract as `Decimal`: arithmetic and comparison behave exactly as
    // they did when this value simply WAS the double, so nothing that reads
    // `as_f64` changes answer. Only rendering and transport gain the digits.
    let v = Value::BigInt(BIG.into());
    assert_eq!(v.as_f64(), Some(12345678901234567890.0));
    assert!(v.is_truthy());
    // Truthiness is written on the DIGITS, not through a parse: a 400-digit
    // value parses to `inf`, and `-0` must not read as zero either.
    assert!(Value::BigInt(format!("1{}", "0".repeat(400))).is_truthy());
    assert!(Value::BigInt(format!("-{BIG}")).is_truthy());
}

#[test]
fn msgpack_round_trips_the_variant_and_the_digits() {
    // The load-bearing encoding: `SerializableViewState.state` goes through
    // msgpack on EVERY read of the default state backend, so an untagged big
    // int comes back as `Value::String` — rendering the same but no longer an
    // `int` on the way back to Python, and taking the wrong branch in
    // `{% if p > 10 %}`. #2214's own fix shipped with an ENCODE-only test that
    // stayed green through exactly this (#2135), so this asserts the DECODE.
    for raw in [BIG, HUGE, "-12345678901234567890"] {
        let v = Value::BigInt(raw.to_string());
        let bytes = rmp_serde::to_vec(&v).expect("msgpack encode");
        let back: Value = rmp_serde::from_slice(&bytes).expect("msgpack decode");
        assert!(
            matches!(&back, Value::BigInt(d) if d == raw),
            "{raw} came back as {back:?}, not a BigInt"
        );
    }
}

#[test]
fn the_binary_tag_is_distinct_from_the_decimal_one() {
    // Sharing `DECIMAL_TAG` would put the value back through the Decimal path
    // on the way out — a `decimal.Decimal` where an `int` went in.
    assert_ne!(djust_core::bigint_tag(), djust_core::decimal_tag());
    let d = rmp_serde::to_vec(&Value::Decimal(BIG.into())).expect("encode");
    let i = rmp_serde::to_vec(&Value::BigInt(BIG.into())).expect("encode");
    assert_ne!(d, i, "the two variants must not encode identically");
    let back_d: Value = rmp_serde::from_slice(&d).expect("decode");
    let back_i: Value = rmp_serde::from_slice(&i).expect("decode");
    assert!(matches!(back_d, Value::Decimal(_)), "{back_d:?}");
    assert!(matches!(back_i, Value::BigInt(_)), "{back_i:?}");
}

#[test]
fn json_carries_the_digits_as_a_string_rather_than_a_lossy_number() {
    // The `Decimal` precedent, verbatim. Scope, checked rather than assumed:
    // this arm is reached only through `serialization::to_json`/`from_json`,
    // which no caller in this workspace uses — the CLIENT-facing JSON is
    // `json_script`'s own `value_to_json`, which emits bare digits and is
    // pinned in `djust_templates/tests/test_bigint_json_script_2260.rs`. So a
    // string here is about `from_json` being able to read it back without a
    // tag, not about what a browser parses.
    let json = serde_json::to_string(&Value::BigInt(BIG.into())).expect("json");
    assert_eq!(json, format!("\"{BIG}\""));
    assert!(
        !json.contains("__djust"),
        "the tag is a BINARY-only encoding"
    );
    // Round-trips as a String, which is what untagged means and why the binary
    // half needs the tag. Asserted so the asymmetry is a decision on the record
    // rather than a surprise.
    let back: Value = serde_json::from_str(&json).expect("json decode");
    assert!(matches!(&back, Value::String(s) if s == BIG), "{back:?}");
}

#[test]
fn a_user_dict_with_the_tag_name_but_a_second_key_is_still_a_dict() {
    // Same discrimination `DECIMAL_TAG` uses: exactly one key, that key, a
    // string payload. Anything else is a real dict.
    let mut m = IndexMap::new();
    m.insert(
        djust_core::bigint_tag().to_string(),
        Value::String(BIG.into()),
    );
    m.insert("other".to_string(), Value::Integer(1));
    let bytes = rmp_serde::to_vec(&Value::Object(m)).expect("encode");
    let back: Value = rmp_serde::from_slice(&bytes).expect("decode");
    assert!(matches!(back, Value::Object(_)), "{back:?}");

    // Wrong payload type, one key: still a dict.
    let mut wrong = IndexMap::new();
    wrong.insert(djust_core::bigint_tag().to_string(), Value::Integer(5));
    let bytes = rmp_serde::to_vec(&Value::Object(wrong)).expect("encode");
    let back: Value = rmp_serde::from_slice(&bytes).expect("decode");
    assert!(matches!(back, Value::Object(_)), "{back:?}");
}

#[test]
fn the_legacy_display_path_keeps_the_digits_too() {
    // `django_value_repr` is the #2203 REPR switch. Restoring a precision loss
    // through a rendering-parity flag would make the flag silently lossy —
    // the same call the `Decimal` arm makes.
    let _g = FlagGuard::off();
    assert_eq!(Value::BigInt(BIG.into()).to_string(), BIG);
}
