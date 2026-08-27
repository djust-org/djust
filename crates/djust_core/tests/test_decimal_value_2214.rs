//! `Value::Decimal` carries exact digits and behaves numerically (#2214).
//!
//! Every expectation here was taken from a live Python/Django run, not from
//! what this implementation produces. The end-to-end template parity lives in
//! `python/tests/test_decimal_precision_2214.py` as a differential against real
//! Django; this file covers the pieces a Rust test can reach directly.

use djust_core::Value;
use indexmap::IndexMap;

fn dec(s: &str) -> Value {
    Value::Decimal(s.to_string())
}

/// Serial guard for the process-global `django_value_repr`.
///
/// EVERY test that reads `Display` must hold it, not only the one that toggles:
/// Rust runs a binary's tests on parallel threads, so a default-ON reader that
/// skipped the lock would race the OFF test. Lifted from
/// `test_display_django_parity_2203.rs`, where taking the lock in only one
/// state made roughly one run in three red — including, memorably, the
/// determinism guard failing non-deterministically.
static FLAG_LOCK: std::sync::Mutex<()> = std::sync::Mutex::new(());

struct FlagGuard(#[allow(dead_code)] std::sync::MutexGuard<'static, ()>);

impl FlagGuard {
    fn on() -> Self {
        let g = FLAG_LOCK.lock().unwrap_or_else(|e| e.into_inner());
        djust_core::set_django_value_repr(true);
        FlagGuard(g)
    }

    fn off() -> Self {
        let g = FLAG_LOCK.lock().unwrap_or_else(|e| e.into_inner());
        djust_core::set_django_value_repr(false);
        FlagGuard(g)
    }
}

impl Drop for FlagGuard {
    /// Restores the default even on panic — without this one genuine failure
    /// leaves the flag OFF and cascades into unrelated tests.
    fn drop(&mut self) {
        djust_core::set_django_value_repr(true);
    }
}

#[test]
fn a_decimal_renders_its_exact_digits() {
    let _g = FlagGuard::on();
    // `str(Decimal('19.99'))` is `'19.99'` — `str()` on a Decimal never
    // reformats, so rendering the digits verbatim IS the parity.
    assert_eq!(dec("19.99").to_string(), "19.99");
    // The whole point: 29 significant digits do not fit in a binary double.
    // As a Float this rendered `1.2345678901234567e19`.
    assert_eq!(
        dec("12345678901234567890.123456789").to_string(),
        "12345678901234567890.123456789"
    );
    // Trailing zeros are significant to Decimal and `str()` keeps them.
    assert_eq!(dec("0.00").to_string(), "0.00");
    assert_eq!(dec("-3.50").to_string(), "-3.50");
}

#[test]
fn a_nested_decimal_renders_the_constructor_form() {
    let _g = FlagGuard::on();
    // `str([Decimal('19.99')])` is `"[Decimal('19.99')]"` — the str/repr split
    // that stops containers reusing Display for their elements (#2203).
    assert_eq!(
        Value::List(vec![dec("19.99")]).to_string(),
        "[Decimal('19.99')]"
    );
    let mut m = IndexMap::new();
    m.insert("p".to_string(), dec("19.99"));
    assert_eq!(Value::Object(m).to_string(), "{'p': Decimal('19.99')}");
}

#[test]
fn truthiness_matches_python() {
    // `bool(Decimal('0.00'))` is False; `bool(Decimal('0.01'))` is True.
    assert!(!dec("0").is_truthy());
    assert!(!dec("0.00").is_truthy());
    assert!(!dec("-0.0").is_truthy());
    assert!(dec("0.01").is_truthy());
    assert!(dec("19.99").is_truthy());
    assert!(dec("-3.5").is_truthy());
}

#[test]
fn an_unparseable_decimal_stays_truthy_rather_than_vanishing() {
    // Defensive: `is_truthy` parses, and a value it cannot parse must not
    // silently read as empty — a falsy answer would flip a `{% if %}` branch.
    assert!(dec("NaN").is_truthy());
    assert!(dec("not-a-number-at-all").is_truthy());
}

#[test]
fn as_f64_is_the_single_numeric_rule() {
    // One definition, so the ~8 numeric consumption sites cannot drift (#1646).
    assert_eq!(dec("19.99").as_f64(), Some(19.99));
    assert_eq!(dec("-3.5").as_f64(), Some(-3.5));
    assert_eq!(Value::Integer(7).as_f64(), Some(7.0));
    assert_eq!(Value::Float(1.5).as_f64(), Some(1.5));
    // Deliberately NOT strings: widening the numeric rule to strings would make
    // `{% if "5" == 5 %}` true, where Django says false.
    assert_eq!(Value::String("5".into()).as_f64(), None);
    assert_eq!(Value::Missing.as_f64(), None);
    assert_eq!(dec("garbage").as_f64(), None);
}

#[test]
fn json_still_encodes_a_bare_string() {
    // Human-readable formats stay untagged: this is what the browser receives,
    // and a wrapper object here would be the wrong JSON.
    let encoded = serde_json::to_string(&dec("12345678901234567890.123456789")).unwrap();
    assert_eq!(encoded, "\"12345678901234567890.123456789\"");
}

#[test]
fn a_decimal_survives_a_msgpack_round_trip() {
    // The half the first version of this file did not test, and the half that
    // was broken (#2240 review). It asserted only the ENCODE direction and
    // stayed green while `SerializableViewState.state` — which round-trips
    // through msgpack on EVERY read of the default `InMemoryStateBackend` —
    // silently degraded `Decimal` to `String`. One cache hit undid the whole
    // fix: `floatformat` stopped rounding, `{% if p > 10 %}` took the wrong
    // branch, `bool(Decimal('0.00'))` flipped true.
    //
    // A test that asserts one direction of a round trip is the #2135 shape: it
    // cannot fail for the thing it exists to guard.
    for raw in [
        "19.99",
        "0.00",
        "-3.50",
        "12345678901234567890.123456789",
        "1E-9",
    ] {
        let bytes = rmp_serde::to_vec(&dec(raw)).unwrap();
        let back: Value = rmp_serde::from_slice(&bytes).unwrap();
        assert!(
            matches!(&back, Value::Decimal(d) if d == raw),
            "msgpack round trip degraded {raw:?} to {back:?}"
        );
    }
}

#[test]
fn the_decimal_tag_does_not_capture_an_ordinary_dict() {
    // `visit_map` treats a one-key map under the tag as a Decimal, so check the
    // near misses: a different key, the right key with a non-string payload,
    // and the right key alongside another.
    let mut plain = IndexMap::new();
    plain.insert("price".to_string(), Value::String("19.99".into()));
    let mut wrong_type = IndexMap::new();
    wrong_type.insert(djust_core::decimal_tag().to_string(), Value::Integer(5));
    let mut extra_key = IndexMap::new();
    extra_key.insert(
        djust_core::decimal_tag().to_string(),
        Value::String("1".into()),
    );
    extra_key.insert("other".to_string(), Value::Integer(2));

    for v in [
        Value::Object(plain),
        Value::Object(wrong_type),
        Value::Object(extra_key),
    ] {
        let bytes = rmp_serde::to_vec(&v).unwrap();
        let back: Value = rmp_serde::from_slice(&bytes).unwrap();
        assert!(
            matches!(back, Value::Object(_)),
            "an ordinary dict was captured as a Decimal: {v:?} -> {back:?}"
        );
    }
}

#[test]
fn an_exponent_form_decimal_expands_for_display() {
    // Django renders through `"{:f}".format(...)`, not `str()`. Verified
    // against Django by a 6,901-case randomized sweep; these are the shapes
    // that sweep found, kept as a fast in-suite guard.
    //
    // `repr` keeps the exponent form — `repr(Decimal('1E-9'))` really is
    // `Decimal('1E-9')` — so the two renderings differ on purpose.
    assert_eq!(dec("1E-9").to_string(), "0.000000001");
    assert_eq!(dec("6E-10").to_string(), "0.0000000006");
    assert_eq!(dec("9.08E-9").to_string(), "0.00000000908");
    assert_eq!(dec("4E+1").to_string(), "40");
    assert_eq!(dec("1E+3").to_string(), "1000");
    assert_eq!(dec("-1E-9").to_string(), "-0.000000001");
    assert_eq!(dec("19.99").to_string(), "19.99");
    // Non-finite forms have no exponent and pass through, as `format(d, 'f')`
    // does. Django itself raises on these; djust rendering them is a stated
    // divergence pinned on the Python side.
    assert_eq!(dec("NaN").to_string(), "NaN");
    assert_eq!(dec("Infinity").to_string(), "Infinity");

    assert_eq!(
        Value::List(vec![dec("1E-9")]).to_string(),
        "[Decimal('1E-9')]"
    );
}

#[test]
fn the_legacy_display_path_is_not_lossy() {
    // `django_value_repr` is the #2203 *repr* switch. Restoring the #2214
    // precision loss through it would make a rendering-parity flag silently
    // destroy digits, so the OFF path keeps them too.
    let _g = FlagGuard::off();
    assert_eq!(
        dec("12345678901234567890.123456789").to_string(),
        "12345678901234567890.123456789"
    );
    // The EXPANSION half of the same arm, which the digits above do not reach:
    // that value has no exponent form, so dropping `expand_decimal_exponent`
    // here left the whole suite green in both profiles. On the previous release
    // this rendered `0.000000001`, because the value was an f64 (#2240 round 8).
    assert_eq!(dec("1E-9").to_string(), "0.000000001");
    // `_g`'s Drop restores the default.
}

// ---------------------------------------------------------------------------
// The two early-return guards, and the `"0"` floor. All three were claimed
// unreachable or left unmentioned; all three are load-bearing (#2240 round 4).
// ---------------------------------------------------------------------------

#[test]
fn the_empty_and_punctuation_guard_is_load_bearing() {
    let _g = FlagGuard::on();
    // A previous version asserted this guard was "measured to be so:
    // no input distinguishes" it. That measurement ran only the guard-ON arm
    // and called it a comparison — no control. With the guard removed these
    // render `0`, `-0`, `0`, ... because the general path treats an absent
    // coefficient as zero.
    //
    // Reachable: the binary tag lets a `Value::Decimal` hold any string, so
    // these are not hypothetical.
    for raw in ["", ".", "-", "+", "E+5", "e5", ".E+5", "-.", "-E+5", "+."] {
        assert_eq!(
            dec(raw).to_string(),
            raw,
            "a Decimal holding {raw:?} must pass through unchanged"
        );
    }
}

#[test]
fn the_non_digit_guard_is_load_bearing() {
    let _g = FlagGuard::on();
    // Distinct from the guard above: these have a coefficient AND an exponent,
    // so they reach the digit check rather than the empty check. With it
    // removed, `abcE+5` renders `abc00000` and `xyzE+200` renders `x.yze+202`
    // — the exponent machinery applied to letters.
    for raw in ["abcE+5", "xyzE+200", "NaNE+5", "InfinityE+3", "abcE-5"] {
        assert_eq!(dec(raw).to_string(), raw);
    }
}

#[test]
fn an_all_zero_coefficient_over_the_cutoff_does_not_panic() {
    let _g = FlagGuard::on();
    // The `"0"` floor in `significant` is the only thing between these and a
    // hard render crash: stripping every zero leaves an empty string, and the
    // scientific branch does `significant.split_at(1)` on it —
    // "end byte index 1 is out of bounds for string of length 0".
    //
    // These are ordinary Decimals that Django renders fine. The floor's comment
    // gave only the `as_tuple() -> (0,)` rationale and never this, and no test
    // covered it, so a future simplification would have shipped a panic.
    for (raw, expected) in [
        ("0E-250", "0e-250"),
        ("0E+250", "0e+250"),
        ("-0E-250", "-0e-250"),
        ("0E-201", "0e-201"),
        ("0E+201", "0e+201"),
    ] {
        assert_eq!(dec(raw).to_string(), expected, "for {raw}");
    }
}
