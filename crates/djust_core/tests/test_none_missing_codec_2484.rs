//! `Value::None` and `Value::Missing` are two values, not one `nil` (#2484).
//!
//! # The defect
//!
//! The two variants are deliberately DISTINCT (#2203): `None` renders `"None"`
//! as `str(None)` does, `Missing` renders `""` as Django's `string_if_invalid`
//! does. `impl Serialize for Value` wrote both as one msgpack `nil` and
//! `visit_unit` read every `nil` back as `Missing`, so every `None` in
//! `SerializableViewState.state` — which is what a state backend round-trips
//! through on EVERY read — came back a `Missing`. `{{ p }}` rendered Django's
//! `"None"` on the first render and the EMPTY STRING after one cache hit.
//!
//! # The encoding decision this file pins
//!
//! The four sibling tags (`DECIMAL_TAG` #2214, `BIGINT_TAG` #2260, `TUPLE_TAG`
//! #2276, `ENCODED_TAG` #2448) each gave a NEW spelling to a value that
//! previously had a different one. This one is not that: it separates two
//! values that shared a spelling, so it has to choose WHICH of the two moves.
//!
//! It moves `Missing`, and the reason is compatibility rather than taste. A
//! state blob outlives a deploy, so both cross-version directions matter:
//!
//! | | what it reads | rendered |
//! |---|---|---|
//! | OLD payload (`nil`), NEW reader | `Value::None` | `"None"` — **fixed** |
//! | NEW payload (`nil`), OLD reader | `Value::Missing` | `""` — unchanged, today's behaviour |
//!
//! Tagging `None` instead would have made every old reader see a one-key
//! `Value::Object` where it saw `nil` — a dict spelling from `{{ p }}` and the
//! TRUE branch from `{% if p %}`, for the most common value in any blob. That
//! is worse than the defect.
//!
//! The second row is only sound because a `None` still encodes as the SAME
//! BYTES it always did. `the_none_encoding_is_byte_identical_to_the_pre_fix_one`
//! is what makes that a measurement: an old reader's behaviour on new bytes is
//! entirely determined by the bytes not having moved, and that is assertable
//! here without running an old build.
//!
//! # Why a tag at all
//!
//! `Missing` cannot reach this serializer today — it is a render-time sentinel
//! (`renderer.rs`'s `resolve(...)?.unwrap_or(Value::Missing)`) and
//! `RustLiveView::state` is filled only through `FromPyObject`, which has no
//! arm producing one. Simply reading `nil` as `None` with no tag would work for
//! every value that exists today and leave the codec lossy in the other
//! direction, ready to reopen this defect with the opposite sign the moment a
//! `Missing` did become reachable. The tag costs 21 bytes on a value nothing
//! emits and makes the codec injective. The Python side measures the
//! "nothing emits one" half — `test_none_missing_state_round_trip_2484.py::
//! test_a_missing_cannot_enter_state_through_the_python_conversion`.

use djust_core::Value;

fn enc(v: &Value) -> Vec<u8> {
    rmp_serde::to_vec(v).expect("msgpack encode")
}

fn dec(bytes: &[u8]) -> Value {
    rmp_serde::from_slice::<Value>(bytes).expect("msgpack decode")
}

fn round_trip(v: &Value) -> Value {
    dec(&enc(v))
}

/// `Value` has no `PartialEq`, so identify a variant by its rendered spelling
/// plus a discriminant check where the spellings would not separate them.
fn variant(v: &Value) -> &'static str {
    match v {
        Value::Missing => "Missing",
        Value::None => "None",
        Value::Bool(_) => "Bool",
        Value::Integer(_) => "Integer",
        Value::Float(_) => "Float",
        Value::String(_) => "String",
        Value::List(_) => "List",
        Value::Tuple(_) => "Tuple",
        Value::NamedTuple { .. } => "NamedTuple",
        Value::Object(_) => "Object",
        Value::Decimal(_) => "Decimal",
        Value::BigInt(_) => "BigInt",
        Value::Encoded(_) => "Encoded",
        Value::DictView { .. } => "DictView",
    }
}

#[test]
fn a_top_level_none_comes_back_a_none_and_a_missing_comes_back_a_missing() {
    assert_eq!(variant(&round_trip(&Value::None)), "None");
    assert_eq!(variant(&round_trip(&Value::Missing)), "Missing");
    // The rendered spelling is the thing a template author sees, so assert it
    // rather than only the variant name.
    assert_eq!(round_trip(&Value::None).to_string(), "None");
    assert_eq!(round_trip(&Value::Missing).to_string(), "");
}

#[test]
fn the_codec_is_injective_on_the_pair() {
    // The one-line statement of the defect: before #2484 these two byte
    // strings were equal.
    assert_ne!(
        enc(&Value::None),
        enc(&Value::Missing),
        "the two variants share an encoding again — every None in state will \
         render '' after one cache hit",
    );
}

#[test]
fn the_none_encoding_is_byte_identical_to_the_pre_fix_one() {
    // THE compatibility assertion, and the reason the tag went on `Missing`.
    // A pre-fix build wrote a `None` as one msgpack `nil` (0xc0); an old
    // reader's behaviour on bytes a new writer produces is entirely determined
    // by those bytes being the same ones. Written as the literal byte rather
    // than as "whatever we emit", because comparing our output to our output
    // would assert nothing.
    assert_eq!(
        enc(&Value::None),
        vec![0xc0_u8],
        "the common value's encoding moved — every OLD reader in a rolling \
         deploy now sees something it has never seen",
    );

    // And the whole point: the byte a pre-fix build WROTE now reads as `None`,
    // which is the value it was written from. `FromPyObject` maps Python
    // `None` to `Value::None` and has no arm producing a `Missing`, so a
    // pre-fix `nil` in a state blob can only have come from a Python `None`.
    assert_eq!(variant(&dec(&[0xc0])), "None");
    assert_eq!(dec(&[0xc0]).to_string(), "None");
}

#[test]
fn the_missing_encoding_is_the_documented_tagged_map() {
    // Pin the NEW spelling literally — a one-key msgpack map whose key is
    // `MISSING_TAG` and whose payload is `nil`. Spelled out byte by byte so a
    // change to the tag name or the payload shape cannot pass by agreeing with
    // itself.
    let tag = djust_core::missing_tag();
    assert_eq!(tag, "__djust_missing__");
    let mut want = vec![0x81_u8]; // fixmap, 1 entry
    want.push(0xa0 | (tag.len() as u8)); // fixstr, 17 bytes
    want.extend_from_slice(tag.as_bytes());
    want.push(0xc0); // nil payload
    assert_eq!(enc(&Value::Missing), want);
    assert_eq!(enc(&Value::Missing).len(), 20);
}

#[test]
fn the_pair_stays_distinct_through_every_container_that_can_hold_it() {
    // A list, a dict, a nested dict, and an `Encoded`'s attribute map are four
    // separate doors onto the same codec arm; the issue's scope line is that
    // the gap is the CODEC's, so every door has to be checked rather than the
    // top-level one standing in for the rest (#1543-adjacent: a suite must
    // enumerate every variant of the surface it covers).
    let inner = || vec![Value::None, Value::Missing];

    match round_trip(&Value::List(inner())) {
        Value::List(items) => {
            assert_eq!(variant(&items[0]), "None");
            assert_eq!(variant(&items[1]), "Missing");
        }
        other => panic!("expected a List: {}", variant(&other)),
    }

    match round_trip(&Value::Tuple(inner())) {
        Value::Tuple(items) => {
            assert_eq!(variant(&items[0]), "None");
            assert_eq!(variant(&items[1]), "Missing");
        }
        other => panic!("expected a Tuple: {}", variant(&other)),
    }

    let obj = |pairs: Vec<(&str, Value)>| {
        Value::Object(
            pairs
                .into_iter()
                .map(|(k, v)| (djust_core::ObjectKey::Str(k.to_string()), v))
                .collect(),
        )
    };
    match round_trip(&obj(vec![("a", Value::None), ("b", Value::Missing)])) {
        Value::Object(map) => {
            assert_eq!(variant(map.get("a").expect("a")), "None");
            assert_eq!(variant(map.get("b").expect("b")), "Missing");
        }
        other => panic!("expected an Object: {}", variant(&other)),
    }

    // Two deep, which is the `{{ d.a.b }}` shape.
    match round_trip(&obj(vec![("a", obj(vec![("b", Value::None)]))])) {
        Value::Object(map) => match map.get("a").expect("a") {
            Value::Object(inner) => assert_eq!(variant(inner.get("b").expect("b")), "None"),
            other => panic!("expected a nested Object: {}", variant(other)),
        },
        other => panic!("expected an Object: {}", variant(&other)),
    }
}

#[test]
fn a_user_dict_cannot_forge_a_missing_by_near_miss() {
    // Same deliberate ugliness as the four sibling tags: exactly one key, that
    // key, and a `nil` payload. Anything else is a real dict and stays one.
    let tag = djust_core::missing_tag().to_string();
    let obj = |pairs: Vec<(String, Value)>| {
        Value::Object(
            pairs
                .into_iter()
                .map(|(k, v)| (djust_core::ObjectKey::Str(k), v))
                .collect(),
        )
    };

    // Right key, WRONG payload.
    for wrong in [
        Value::Integer(1),
        Value::String(String::new()),
        Value::Bool(false),
        Value::List(vec![]),
    ] {
        let v = obj(vec![(tag.clone(), wrong)]);
        assert_eq!(
            variant(&round_trip(&v)),
            "Object",
            "a dict under the tag with a non-nil payload was read as a Missing",
        );
    }

    // Right key, right payload, but a SECOND key — not a one-key map.
    let two = obj(vec![
        (tag.clone(), Value::None),
        ("other".to_string(), Value::Integer(1)),
    ]);
    assert_eq!(variant(&round_trip(&two)), "Object");

    // Nearly the tag.
    for near in ["__djust_missing", "_djust_missing__", "__djust_missing__x"] {
        let v = obj(vec![(near.to_string(), Value::None)]);
        assert_eq!(
            variant(&round_trip(&v)),
            "Object",
            "a near-miss key ({near}) was read as a Missing",
        );
    }
}

#[test]
fn the_missing_tag_does_not_collide_with_the_other_four() {
    // Both halves: a distinct NAME, and a payload shape that separates it even
    // if a name were ever reused. `MISSING_TAG`'s payload is `nil`; the other
    // four are two strings and two lists.
    let tags = [
        djust_core::missing_tag(),
        djust_core::decimal_tag(),
        djust_core::bigint_tag(),
        djust_core::tuple_tag(),
        djust_core::encoded_tag(),
    ];
    let mut seen = std::collections::HashSet::new();
    for t in tags {
        assert!(seen.insert(t), "two tags share the name {t}");
        assert!(t.starts_with('_'), "{t} must be `_`-prefixed");
    }
    assert_eq!(seen.len(), 5);

    // And a value under each of the other four still decodes to ITS variant,
    // so the new arm's position in the `if` chain did not shadow one.
    assert_eq!(
        variant(&round_trip(&Value::Decimal("1.50".to_string()))),
        "Decimal"
    );
    assert_eq!(
        variant(&round_trip(&Value::BigInt("1".repeat(30)))),
        "BigInt"
    );
    assert_eq!(
        variant(&round_trip(&Value::Tuple(vec![Value::Integer(1)]))),
        "Tuple"
    );
}

#[test]
fn json_keeps_one_null_for_both_and_reads_it_as_none() {
    // The human-readable half is deliberately unchanged and deliberately
    // lossy, exactly as `TUPLE_TAG`'s is: `json.dumps` has one `null`, so
    // matching Django means both variants stay `null`. What DID change is
    // which variant a `null` reads back as, and `None` is the right one —
    // `json.loads("null")` is Python's `None`.
    assert_eq!(
        djust_core::serialization::to_json(&Value::None).unwrap(),
        "null"
    );
    assert_eq!(
        djust_core::serialization::to_json(&Value::Missing).unwrap(),
        "null"
    );
    let back = djust_core::serialization::from_json("null").unwrap();
    assert_eq!(variant(&back), "None");
    assert_eq!(back.to_string(), "None");
}

#[test]
fn no_field_in_this_crate_is_conditionally_skipped() {
    // Guards the #1541 / #1538 hazard the sibling wire-position file guards,
    // restated here because #2484 adds a serializer arm: a
    // `skip_serializing_if` on a non-trailing optional shifts every later slot
    // in a POSITIONAL msgpack payload. There is not one in the crate, and the
    // new arm did not introduce one.
    let src = include_str!("../src/lib.rs");
    assert!(
        !src.contains("skip_serializing_if"),
        "a conditional skip appeared in djust_core — re-read #1541 before \
         keeping it",
    );
}
