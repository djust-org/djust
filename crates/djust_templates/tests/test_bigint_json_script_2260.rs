//! `json_script` emits a big int as a bare JSON number — but only its digits (#2260).
//!
//! `json.dumps(12345678901234567890)` is a BARE number with every digit: JSON's
//! grammar has no precision ceiling, and quoting it would change the type client
//! code reads back. So the `BigInt` arm of `value_to_json` writes the payload
//! verbatim rather than through `json_string_body`.
//!
//! That is only safe because the payload is validated first, and the reason is
//! the one the `Decimal` arm above it already documents: since #2214 gave binary
//! encodings a tag, a variant carrying a `String` can be DESERIALIZED holding an
//! arbitrary one. A `Value::BigInt` is therefore not guaranteed to hold digits
//! just because `FromPyObject` only ever builds it from `str(int)`.
//!
//! The first version of the `Decimal` arm reasoned itself exempt on exactly that
//! ground — "`str(Decimal)` yields only digits, `.`, sign, `E`/`e`" — and was
//! true about the values it considered and wrong about the type. This file is
//! the pin that stops the same argument being made again one variant over.

use djust_core::{Context, Value};
use djust_templates::Template;

fn render(v: Value) -> String {
    let mut ctx = Context::new();
    ctx.set("p".to_string(), v);
    Template::new("{{ p|json_script:'x' }}")
        .expect("template should parse")
        .render(&ctx)
        .expect("template should render")
}

#[test]
fn a_real_big_int_is_a_bare_json_number_with_every_digit() {
    let out = render(Value::BigInt("12345678901234567890".into()));
    assert!(
        out.contains(">12345678901234567890<"),
        "expected a bare number, got {out}"
    );
    // Not quoted: `json.dumps` of an int is a number, and a string would change
    // the type `JSON.parse` hands the page.
    assert!(!out.contains("\"12345678901234567890\""), "{out}");
    assert!(render(Value::BigInt("-12345678901234567890".into())).contains(">-1234"));
}

#[test]
fn a_forged_payload_cannot_inject_json_structure() {
    // Every one of these is a valid `Value::BigInt` as far as the type is
    // concerned, and none is `str(int)`. Emitted bare, the first would close the
    // value and open a new key in the object client code parses.
    for payload in [
        "1,\"admin\":true",
        "1} , {\"x\":2",
        "abc",
        "",
        "1e5",
        "1.5",
        "007",
        "  1  ",
        "</script>",
    ] {
        let out = render(Value::BigInt(payload.to_string()));
        assert!(
            !out.contains(&format!(">{payload}<")),
            "{payload:?} was emitted bare: {out}"
        );
        // It takes the escaped-string path instead, so the payload survives as
        // DATA rather than being dropped.
        assert!(
            out.contains('"'),
            "{payload:?} should have been quoted: {out}"
        );
    }
}

#[test]
fn the_literal_rule_is_jsons_own_int_grammar() {
    // `007` and `  1  ` are rejected above even though they parse as integers in
    // Rust, because JSON's grammar forbids leading zeros and whitespace inside a
    // number — and `str(int)` produces neither, so nothing legitimate is lost.
    // A plain `all(is_ascii_digit)` check would have passed `007`.
    assert!(render(Value::BigInt("0".into())).contains(">0<"));
    assert!(!render(Value::BigInt("00".into())).contains(">00<"));
}
