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
fn the_digits_survive_a_json_round_trip() {
    // `#[serde(untagged)]`, so a Decimal encodes as its bare string — which is
    // what puts the exact digits on the wire.
    let encoded = serde_json::to_string(&dec("12345678901234567890.123456789")).unwrap();
    assert_eq!(encoded, "\"12345678901234567890.123456789\"");
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
    // `_g`'s Drop restores the default.
}
