//! `Value::Encoded`'s msgpack payload is POSITIONAL — pin the slots (#2471/#2472).
//!
//! # Why this file exists
//!
//! Two PRs appended an element to the same positional tuple in the same
//! release: #2466 added `sized_empty` / `iterable`, and #2471/#2472 added
//! `repr` / `cmp_key`. That is exactly the merge whose naive resolution
//! silently corrupts an encoding — the struct compiles, both sides of a
//! same-process test use the same field order, and every existing test stays
//! green while a state entry written by one build deserializes into garbage on
//! another.
//!
//! Before this file the `Encoded` payload was pinned only from PYTHON
//! (`test_encoded_truthiness_2458.py`, via `RustLiveView.serialize_msgpack`),
//! and only for ONE value. Nothing pinned the slot assignment across the
//! permutations, and nothing pinned the nested key's own shape.
//!
//! # The three structural facts, verified rather than assumed
//!
//! The canon (#1541 / #1538) is about `#[derive(Serialize, Deserialize)]`
//! structs, where `rmp-serde` writes a positional array and a
//! `skip_serializing_if` on a non-trailing optional shifts every later slot.
//! None of that machinery is in play here, and it is worth writing down which
//! parts do not apply and why:
//!
//! 1. **`Encoded` does not derive `Serialize`/`Deserialize`.** It derives only
//!    `Debug, Clone, PartialEq`. The encoding is the hand-written
//!    `impl Serialize for Value`, which writes an explicit tuple under
//!    `ENCODED_TAG`, and the hand-written `Deserialize` that slice-matches it.
//! 2. **No field carries `skip_serializing_if`** — there is not one in
//!    `djust_core` at all. `cmp_key` is written UNCONDITIONALLY, as msgpack
//!    `nil` or a three-element array, so the "optional drops its slot" hazard
//!    cannot arise: `None` costs one byte and the slots stay aligned.
//! 3. **`CmpKey` does not derive `Serialize` either.** It is mapped explicitly
//!    to `(u8, i64, i64)` at the call site, so the nested shape is a tuple this
//!    file pins rather than a derived struct encoding.
//!
//! `test_no_field_is_conditionally_skipped` asserts (2) against the source, so
//! the reasoning above cannot quietly stop being true.
//!
//! # The slot ORDER, and why it is this one
//!
//! ```text
//!   0 type_name    #2448
//!   1 display      #2448
//!   2 json         #2448
//!   3 truthy       #2458
//!   4 sized_empty  #2466
//!   5 iterable     #2466
//!   6 repr         #2472
//!   7 cmp_key      #2471   nil, or [domain, hi, lo]
//!   8 attrs        #2481   a MAP of attribute name -> Value
//! ```
//!
//! Every widening APPENDS. That is what lets the reader accept 9, 8, 6, 4 and 3
//! elements from older writers, and it is why this merge put #2471/#2472's two
//! fields AFTER #2466's rather than in struct-declaration order — and #2481's
//! after those.
//!
//! # Non-vacuity
//!
//! Slots 3, 4 and 5 are three consecutive BOOLEANS, so a one-slot shift among
//! them type-checks and would deserialize into a plausible struct. Every
//! fixture below therefore gives them DISTINCT values, and
//! `test_a_one_slot_shift_is_detectable` proves the assertions can actually see
//! a shift rather than merely passing over one.
//!
//! Slots 7 and 8 need the same treatment for a different reason: both are
//! "structured or nil", so a swap between them does NOT change the payload's
//! width or trip any type check — it silently loses both. The sample therefore
//! carries a non-empty attribute map AND a key, and
//! `test_a_swap_of_the_key_and_attribute_slots_is_detectable` proves the
//! assertions see it.
//!
//! # The attribute map's own shape
//!
//! It is a msgpack MAP, so it reads back through the same `visit_map` every
//! other value takes and arrives as a `Value::Object` — which is exactly what
//! `Encoded::attrs` holds, so there is no second decoding to keep in step. A
//! user dict cannot forge an `Encoded` through it: the four `_TAG` constants
//! all begin with `_`, and every producer of this map skips `_`-prefixed
//! names.

use djust_core::{Encoded, Value};

fn tag() -> String {
    djust_core::encoded_tag().to_string()
}

/// A fully-populated `Encoded`, with the three consecutive booleans DISTINCT so
/// a slot shift among them cannot pass.
fn sample() -> Encoded {
    Encoded {
        type_name: "datetime.timedelta".to_string(),
        display: "0:01:30".to_string(),
        json: "P0DT00H01M30S".to_string(),
        truthy: true,
        sized_empty: false,
        iterable: true,
        repr: "datetime.timedelta(seconds=90)".to_string(),
        cmp_key: Some(djust_core::CmpKey {
            domain: djust_core::CMP_DOMAIN_TIMEDELTA,
            hi: -7,
            lo: 90_000_000,
        }),
        // NON-EMPTY, and heterogeneous. An empty map is the default every
        // older width restores to, so a fixture carrying one could not tell
        // "slot 9 round-tripped" from "slot 9 was dropped" (#2481).
        attrs: attrs_of(&[
            ("days", Value::Integer(-7)),
            ("seconds", Value::Integer(90)),
            ("microseconds", Value::Integer(0)),
        ]),
    }
}

/// An attribute map from `(name, value)` pairs.
fn attrs_of(pairs: &[(&str, Value)]) -> indexmap::IndexMap<djust_core::ObjectKey, Value> {
    pairs
        .iter()
        .map(|(k, v)| {
            (
                djust_core::object_key::ObjectKey::Str((*k).to_string()),
                v.clone(),
            )
        })
        .collect()
}

fn round_trip(e: &Encoded) -> Encoded {
    let bytes = rmp_serde::to_vec(&Value::Encoded(Box::new(e.clone()))).expect("msgpack encode");
    match rmp_serde::from_slice::<Value>(&bytes).expect("msgpack decode") {
        Value::Encoded(back) => *back,
        other => panic!("not an Encoded after the round trip: {other:?}"),
    }
}

/// Rebuild a payload of `parts` under the tag and decode it.
fn decode_parts(parts: Vec<Value>) -> Value {
    let mut map = indexmap::IndexMap::new();
    map.insert(
        djust_core::object_key::ObjectKey::Str(tag()),
        Value::List(parts),
    );
    let bytes = rmp_serde::to_vec(&Value::Object(map)).expect("encode forged payload");
    rmp_serde::from_slice::<Value>(&bytes).expect("decode forged payload")
}

fn parts_of(e: &Encoded) -> Vec<Value> {
    let mut v = vec![
        Value::String(e.type_name.clone()),
        Value::String(e.display.clone()),
        Value::String(e.json.clone()),
        Value::Bool(e.truthy),
        Value::Bool(e.sized_empty),
        Value::Bool(e.iterable),
        Value::String(e.repr.clone()),
    ];
    v.push(match e.cmp_key {
        Some(k) => Value::List(vec![
            Value::Integer(i64::from(k.domain)),
            Value::Integer(k.hi),
            Value::Integer(k.lo),
        ]),
        None => Value::None,
    });
    v.push(Value::Object(e.attrs.clone()));
    v
}

#[test]
fn the_payload_is_nine_slots_in_the_documented_order() {
    let e = sample();
    // `Value` has no `PartialEq` (the renderer's own tests note it), so match
    // the variant and compare the `Encoded`, which does derive one.
    assert_eq!(parts_of(&e).len(), 9, "the payload width moved");
    match decode_parts(parts_of(&e)) {
        Value::Encoded(back) => assert_eq!(*back, e),
        other => panic!("the nine-slot payload did not read as an Encoded: {other:?}"),
    }
}

#[test]
fn the_attribute_map_carries_every_value_shape_it_can_hold() {
    // `Encoded::attrs` holds whatever `Value::extract` made of the attribute,
    // so the codec has to carry every shape a `Value` can be — not just the
    // integers a `timedelta` happens to produce. #2478 widens the producers to
    // arbitrary `__dict__` values, which is when the rest of these arrive.
    //
    // Sweeping the shapes is what pins that a `Tuple` inside the map keeps its
    // own tag (#2276) and that `Missing` and `None` stay distinct (#2203) —
    // both are one `Value` arm away from collapsing.
    let shapes: Vec<(&str, Value)> = vec![
        ("int", Value::Integer(-2026)),
        ("float", Value::Float(1.5)),
        ("bool", Value::Bool(true)),
        ("str", Value::String("UTC".to_string())),
        // `Value::None` is deliberately NOT here — see
        // `a_none_attribute_comes_back_as_missing_a_pre_existing_codec_gap`.
        ("missing", Value::Missing),
        ("decimal", Value::Decimal("1.50".to_string())),
        (
            "bigint",
            Value::BigInt("123456789012345678901234567890".to_string()),
        ),
        // The nested elements avoid `Value::None` for the reason
        // `a_none_attribute_comes_back_as_missing_a_pre_existing_codec_gap`
        // records — the gap is the CODEC's, not this slot's, and pinning it
        // once is enough.
        ("list", Value::List(vec![Value::Integer(1), Value::Missing])),
        (
            "tuple",
            Value::Tuple(vec![Value::Integer(1), Value::Bool(false)]),
        ),
        (
            "object",
            Value::Object(attrs_of(&[("a", Value::Integer(1))])),
        ),
        ("nested_encoded", Value::Encoded(Box::new(sample()))),
        ("empty_list", Value::List(vec![])),
        ("empty_object", Value::Object(attrs_of(&[]))),
    ];
    let mut checked = 0;
    for (name, shape) in &shapes {
        let e = Encoded {
            attrs: attrs_of(&[(name, shape.clone())]),
            ..sample()
        };
        assert_eq!(round_trip(&e), e, "the {name} attribute did not survive");
        checked += 1;
    }
    assert_eq!(checked, 13, "the shape sweep shrank");

    // And all of them at once, so ORDER is pinned too — the map is an
    // `IndexMap` because Python's attribute order is what a caller sees, and a
    // `HashMap` here would reorder per process.
    let all = Encoded {
        attrs: attrs_of(
            &shapes
                .iter()
                .map(|(k, v)| (*k, v.clone()))
                .collect::<Vec<_>>(),
        ),
        ..sample()
    };
    let back = round_trip(&all);
    assert_eq!(back, all);
    assert_eq!(
        back.attrs.keys().collect::<Vec<_>>(),
        all.attrs.keys().collect::<Vec<_>>(),
        "the attribute map came back reordered",
    );
}

#[test]
fn a_none_attribute_comes_back_as_missing_a_pre_existing_codec_gap() {
    // Pinned in the DIVERGING direction, because it is not this slot's bug and
    // is not fixed here.
    //
    // `impl Serialize for Value` writes `Missing | None` as ONE msgpack `nil`
    // and `visit_unit` reads every `nil` back as `Missing`. The two variants
    // are deliberately DISTINCT (#2203) — `None` renders `"None"` and `Missing`
    // renders `""` — so a `None` anywhere in a value that round-trips through
    // the state backend comes back rendering the empty string.
    //
    // It predates the attribute map and is not reached through it alone: the
    // second half of this test shows a PLAIN `Value::Object` losing it too, so
    // `{{ d.a }}` on `{"a": None}` has always answered `None` on the first
    // render and `""` after one cache hit. #2481 adds one more reachable
    // instance (`{{ dt.tzinfo }}` on a NAIVE datetime) and improves the
    // pre-round-trip answer there from `""` to Django's `"None"`; it neither
    // causes nor worsens the round-trip half. Filed separately.
    let e = Encoded {
        attrs: attrs_of(&[("tzinfo", Value::None)]),
        ..sample()
    };
    let back = round_trip(&e);
    assert!(
        matches!(back.attrs.get("tzinfo"), Some(Value::Missing)),
        "the codec gap moved — a None attribute now reads as {:?}",
        back.attrs.get("tzinfo"),
    );

    // The same loss, with no `Encoded` involved at all — which is what makes
    // "pre-existing" a measurement rather than a claim.
    let plain = Value::Object(attrs_of(&[("a", Value::None)]));
    let bytes = rmp_serde::to_vec(&plain).expect("encode");
    match rmp_serde::from_slice::<Value>(&bytes).expect("decode") {
        Value::Object(map) => assert!(
            matches!(map.get("a"), Some(Value::Missing)),
            "a plain Object's None survived — the gap this pins is closed, so \
             the `none` case belongs back in the shape sweep above",
        ),
        other => panic!("expected an Object: {other:?}"),
    }
}

#[test]
fn an_empty_attribute_map_is_written_and_read_as_empty() {
    // The `falsy_opaque` shape: no attributes, still nine slots. Written
    // unconditionally rather than skipped, which is what keeps the slots
    // aligned (#1541).
    let e = Encoded {
        attrs: attrs_of(&[]),
        ..sample()
    };
    assert_eq!(parts_of(&e).len(), 9, "an empty map must not drop its slot");
    assert_eq!(round_trip(&e), e);
    assert!(round_trip(&e).attrs.is_empty());
}

#[test]
fn a_malformed_attribute_slot_reads_as_absent_rather_than_guessed() {
    // Same fail-to-absent the key slot takes: anything that is not a map is
    // not a payload this crate wrote, so it restores NO attributes rather than
    // a guess at some.
    let e = sample();
    for bad in [
        Value::None,
        Value::String("days=3".to_string()),
        Value::Integer(3),
        Value::List(vec![Value::String("days".to_string()), Value::Integer(3)]),
        Value::Bool(true),
    ] {
        let mut parts = parts_of(&e);
        parts[8] = bad.clone();
        match decode_parts(parts) {
            Value::Encoded(back) => assert!(
                back.attrs.is_empty(),
                "a malformed attribute slot must read as absent, not as a guess: {bad:?}",
            ),
            other => panic!("expected an Encoded: {other:?}"),
        }
    }
}

#[test]
fn a_swap_of_the_key_and_attribute_slots_is_detectable() {
    // Non-vacuity for slots 7 and 8. Neither is a string or a bool, so a swap
    // survives every type check the reader makes and the width is unchanged —
    // the VALUES are the only thing that can catch it. Both are therefore
    // non-default in `sample()`, and both must come back wrong.
    let e = sample();
    assert!(
        e.cmp_key.is_some() && !e.attrs.is_empty(),
        "the sample stopped distinguishing the key and attribute slots",
    );
    let mut swapped = parts_of(&e);
    swapped.swap(7, 8);
    match decode_parts(swapped) {
        Value::Encoded(back) => {
            assert_ne!(
                *back, e,
                "a swap of the key and attribute slots was invisible"
            );
            assert_eq!(back.cmp_key, None, "a map must not read as a key");
            assert!(back.attrs.is_empty(), "a key must not read as attributes");
        }
        other => panic!("expected an Encoded with both slots lost: {other:?}"),
    }
}

#[test]
fn every_variant_is_structurally_equal_to_its_own_clone() {
    // `values_structurally_equal`'s wildcard arm is LAST, so a `Value` variant
    // added without an arm compares UNEQUAL TO ITSELF — and every `assert_eq!`
    // in this file is written against that comparison. One case per variant,
    // so the omission fails HERE, once, rather than as a confusing round-trip
    // failure somewhere else.
    let every: Vec<(&str, Value)> = vec![
        ("Missing", Value::Missing),
        ("None", Value::None),
        ("Bool", Value::Bool(false)),
        ("Integer", Value::Integer(0)),
        ("Float", Value::Float(0.0)),
        ("String", Value::String(String::new())),
        ("Decimal", Value::Decimal("0".to_string())),
        ("BigInt", Value::BigInt("0".to_string())),
        ("List", Value::List(vec![Value::Integer(1)])),
        ("Tuple", Value::Tuple(vec![Value::Integer(1)])),
        (
            "Object",
            Value::Object(attrs_of(&[("a", Value::Integer(1))])),
        ),
        (
            "DictView",
            Value::DictView {
                kind: djust_core::DictViewKind::Items,
                items: vec![Value::Integer(1)],
            },
        ),
        ("Encoded", Value::Encoded(Box::new(sample()))),
    ];
    for (name, v) in &every {
        assert!(
            djust_core::values_structurally_equal(v, &v.clone()),
            "{name} is not structurally equal to its own clone — it has no arm",
        );
    }

    // And the pairs that must stay DISTINCT, each of which is a decision a
    // careless arm would undo.
    for (label, a, b) in [
        ("Missing vs None (#2203)", Value::Missing, Value::None),
        (
            "List vs Tuple (#2276)",
            Value::List(vec![Value::Integer(1)]),
            Value::Tuple(vec![Value::Integer(1)]),
        ),
        (
            "DictView vs its items (#2340)",
            Value::DictView {
                kind: djust_core::DictViewKind::Items,
                items: vec![Value::Integer(1)],
            },
            Value::List(vec![Value::Integer(1)]),
        ),
        (
            "Integer vs Float — NOT Django's == (values_equal says equal)",
            Value::Integer(1),
            Value::Float(1.0),
        ),
    ] {
        assert!(
            !djust_core::values_structurally_equal(&a, &b),
            "{label}: these must not compare structurally equal",
        );
    }
}

#[test]
fn every_permutation_of_the_three_bits_and_the_key_round_trips() {
    // 2^3 bit combinations x {no key, key}. The bits are what a one-slot shift
    // would scramble, so sweeping them is what makes the pin mechanical rather
    // than a single sample.
    let mut checked = 0;
    for truthy in [false, true] {
        for sized_empty in [false, true] {
            for iterable in [false, true] {
                for key in [
                    None,
                    Some(djust_core::CmpKey {
                        domain: djust_core::CMP_DOMAIN_DATETIME_AWARE,
                        hi: 737_425,
                        lo: 11_045_000_000,
                    }),
                ] {
                    let e = Encoded {
                        truthy,
                        sized_empty,
                        iterable,
                        cmp_key: key,
                        ..sample()
                    };
                    assert_eq!(round_trip(&e), e, "round trip lost a field: {e:?}");
                    checked += 1;
                }
            }
        }
    }
    assert_eq!(checked, 16, "the permutation sweep shrank");
}

#[test]
fn the_nested_key_carries_negative_and_extreme_limbs() {
    // `timedelta.min`/`max` scale, and a negative `hi` — the shapes a smaller
    // integer type or an unsigned one would silently mangle. `lo` is
    // microseconds-within-a-day and `hi` is days, which is the split that keeps
    // both inside an `i64` (see `CmpKey`'s doc).
    for (hi, lo) in [
        (0_i64, 0_i64),
        (-1, 86_399_999_999),
        (999_999_999, 999_999),
        (-999_999_999, 0),
        (i64::MAX, i64::MAX),
        (i64::MIN, 0),
    ] {
        let e = Encoded {
            cmp_key: Some(djust_core::CmpKey {
                domain: djust_core::CMP_DOMAIN_TIMEDELTA,
                hi,
                lo,
            }),
            ..sample()
        };
        assert_eq!(round_trip(&e), e, "({hi}, {lo}) did not survive");
    }
}

#[test]
fn every_domain_constant_round_trips() {
    // `domain` is a `u8` on the struct and an `i64` on the wire, so the
    // narrowing on the way back is a real conversion with a real failure mode.
    for domain in [
        djust_core::CMP_DOMAIN_TIMEDELTA,
        djust_core::CMP_DOMAIN_DATE,
        djust_core::CMP_DOMAIN_DATETIME_NAIVE,
        djust_core::CMP_DOMAIN_DATETIME_AWARE,
        djust_core::CMP_DOMAIN_TIME_NAIVE,
    ] {
        let e = Encoded {
            cmp_key: Some(djust_core::CmpKey {
                domain,
                hi: 1,
                lo: 2,
            }),
            ..sample()
        };
        assert_eq!(round_trip(&e), e, "domain {domain} did not survive");
    }
}

#[test]
fn a_one_slot_shift_is_detectable() {
    // Non-vacuity for every assertion above. Slots 3/4/5 are three consecutive
    // booleans; if the fixtures gave them the same value a shift would be
    // invisible. Drop slot 4 to shift 5 into it and prove the answers change.
    let e = sample();
    assert!(
        e.truthy && !e.sized_empty && e.iterable,
        "the sample stopped distinguishing the three boolean slots",
    );
    let mut shifted = parts_of(&e);
    // Now EIGHT elements — which since #2471/#2472 is a real width, so this
    // case no longer refuses on width alone. It refuses on TYPES: with slot 4
    // gone, `iterable` (a bool) sits where `sized_empty` belongs and `repr` (a
    // string) sits where `iterable` belongs, and the eight-arm's pattern wants
    // a bool there. That is a stronger statement than the width check it was,
    // and it is why the assertion is spelled as "not an Encoded" rather than
    // "wrong width".
    shifted.remove(4);
    assert_eq!(shifted.len(), 8, "the shifted payload must be a REAL width");
    match decode_parts(shifted) {
        Value::Object(_) => {}
        other => panic!("a type-misaligned 8-element payload must not forge an Encoded: {other:?}"),
    }

    // And the sharper case: keep the width at 9 but SWAP two boolean slots.
    // Every type still matches, so only the VALUES can catch it.
    let mut swapped = parts_of(&e);
    swapped.swap(4, 5);
    match decode_parts(swapped) {
        Value::Encoded(back) => {
            assert_ne!(*back, e, "a swap of two boolean slots was invisible");
            assert!(back.sized_empty && !back.iterable);
        }
        other => panic!("expected an Encoded with swapped bits: {other:?}"),
    }
}

#[test]
fn the_older_widths_still_read_with_the_documented_fallbacks() {
    let e = sample();
    let full = parts_of(&e);

    // EIGHT — the #2471/#2472 shape: everything but the attribute map, which
    // restores EMPTY. `{{ dt.year }}` resolved to nothing before #2481, so an
    // entry written by that build keeps answering exactly what it answered
    // rather than half-way between.
    match decode_parts(full[..8].to_vec()) {
        Value::Encoded(back) => {
            assert_eq!(back.repr, e.repr);
            assert_eq!(
                back.cmp_key, e.cmp_key,
                "an 8-element read must keep the key"
            );
            assert!(
                back.attrs.is_empty(),
                "an 8-element read must not invent attributes"
            );
        }
        other => panic!("8 elements must read: {other:?}"),
    }

    // SIX — the #2466 shape: `repr` falls back to `display`, no key.
    match decode_parts(full[..6].to_vec()) {
        Value::Encoded(back) => {
            assert_eq!(back.truthy, e.truthy);
            assert_eq!(back.sized_empty, e.sized_empty);
            assert_eq!(back.iterable, e.iterable);
            assert_eq!(
                back.repr, e.display,
                "a 6-element read must not invent a repr"
            );
            assert_eq!(back.cmp_key, None, "a 6-element read must not invent a key");
            assert!(back.attrs.is_empty());
        }
        other => panic!("6 elements must read: {other:?}"),
    }

    // FOUR — the #2458 shape: the two #2466 bits are false, same fallbacks.
    match decode_parts(full[..4].to_vec()) {
        Value::Encoded(back) => {
            assert_eq!(back.truthy, e.truthy);
            assert!(!back.sized_empty && !back.iterable);
            assert_eq!(back.repr, e.display);
            assert_eq!(back.cmp_key, None);
            assert!(back.attrs.is_empty());
        }
        other => panic!("4 elements must read: {other:?}"),
    }

    // THREE — the #2448 shape: truthiness derived from the display.
    match decode_parts(full[..3].to_vec()) {
        Value::Encoded(back) => {
            assert_eq!(back.truthy, !e.display.is_empty());
            assert_eq!(back.repr, e.display);
            assert_eq!(back.cmp_key, None);
            assert!(back.attrs.is_empty());
        }
        other => panic!("3 elements must read: {other:?}"),
    }

    // FIVE and SEVEN are NOT shapes this crate ever wrote, so they must fall
    // through to a plain dict rather than being read as a truncated Encoded —
    // which is what keeps a user dict under this key from forging one. (One,
    // two and zero are covered by the same rule and are not spelled out; the
    // interesting widths are the ones ADJACENT to a real shape.)
    for n in [5usize, 7] {
        match decode_parts(full[..n].to_vec()) {
            Value::Object(_) => {}
            other => panic!("{n} elements must NOT forge an Encoded: {other:?}"),
        }
    }
}

#[test]
fn a_malformed_key_reads_as_absent_rather_than_guessed() {
    let e = sample();
    for bad in [
        Value::String("1,2,3".to_string()),
        Value::Integer(1),
        Value::List(vec![Value::Integer(1), Value::Integer(2)]),
        Value::List(vec![
            Value::Integer(-1), // not a u8
            Value::Integer(1),
            Value::Integer(2),
        ]),
        Value::List(vec![
            Value::String("1".to_string()),
            Value::Integer(1),
            Value::Integer(2),
        ]),
    ] {
        let mut parts = parts_of(&e);
        parts[7] = bad.clone();
        match decode_parts(parts) {
            Value::Encoded(back) => assert_eq!(
                back.cmp_key, None,
                "a malformed key must read as absent, not as a guess: {bad:?}"
            ),
            other => panic!("expected an Encoded: {other:?}"),
        }
    }
}

#[test]
fn test_no_field_is_conditionally_skipped() {
    // The structural fact the module doc leans on: no `skip_serializing_if`
    // anywhere in this crate, so no optional can drop its positional slot
    // (#1541/#1538). A future `derive`-based rewrite that adds one has to
    // come here and think about ordering.
    let src = include_str!("../src/lib.rs");
    assert!(
        !src.contains("skip_serializing_if"),
        "a `skip_serializing_if` appeared in djust_core — on a NON-TRAILING \
         optional it shifts every later positional slot on read, and \
         `#[serde(default)]` does not save it (#1541). Read the module doc of \
         this test before adding one.",
    );
    // And `Encoded`/`CmpKey` still do not derive their own encodings, which is
    // what makes the hand-written tuple above the single source of the shape.
    // `Encoded` derives NEITHER a Serialize nor (since #2481) a PartialEq:
    // the encoding is the hand-written tuple, and the equality is the
    // hand-written impl that compares the attribute map through
    // `values_structurally_equal`. Pinned as the exact derive line, so a
    // future `#[derive(..., Serialize)]` in ANY field order trips it.
    let derive_line = src
        .split("pub struct Encoded {")
        .next()
        .expect("the source has a `pub struct Encoded`")
        .trim_end()
        .lines()
        .last()
        .expect("a derive line above `pub struct Encoded`")
        .trim()
        .to_string();
    assert_eq!(
        derive_line, "#[derive(Debug, Clone)]",
        "`Encoded`'s derives moved. A derived Serialize would put the payload \
         order in two places; a derived PartialEq cannot compile against \
         `attrs` (a `Value` has no `PartialEq`, deliberately — see \
         `values_structurally_equal`).",
    );
}
