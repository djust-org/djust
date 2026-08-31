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
        // `None` — no `__len__`, which is what a `timedelta` answers. Slot 5
        // is an OPTION since #2477, not a boolean, so the "three consecutive
        // booleans" hazard is now a bool / option / bool sandwich; the shift
        // tests below are written against that.
        len: None,
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
        // NON-`None`, and non-empty, for the reason the attribute map is: an
        // absent slot is the default every older width restores to, so a
        // fixture carrying one could not tell "slot 10 round-tripped" from
        // "slot 10 was dropped" (#2477/#2489).
        items: Some(vec![Value::String("a".to_string()), Value::Integer(2)]),
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
        match e.len {
            Some(n) => Value::Integer(n as i64),
            // A `nil` reads back as `Missing`, which is what the reader's own
            // catch-all arm treats as "no `__len__`".
            None => Value::Missing,
        },
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
    v.push(match &e.items {
        Some(items) => Value::List(items.clone()),
        None => Value::Missing,
    });
    v
}

#[test]
fn the_payload_is_ten_slots_in_the_documented_order() {
    let e = sample();
    // `Value` has no `PartialEq` (the renderer's own tests note it), so match
    // the variant and compare the `Encoded`, which does derive one.
    assert_eq!(parts_of(&e).len(), 10, "the payload width moved");
    match decode_parts(parts_of(&e)) {
        Value::Encoded(back) => assert_eq!(*back, e),
        other => panic!("the ten-slot payload did not read as an Encoded: {other:?}"),
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
        // `None` joined this sweep when #2484 closed the codec gap that used
        // to keep it out — the two variants no longer share one `nil`, so
        // both belong here and both must survive.
        ("none", Value::None),
        ("missing", Value::Missing),
        ("decimal", Value::Decimal("1.50".to_string())),
        (
            "bigint",
            Value::BigInt("123456789012345678901234567890".to_string()),
        ),
        // The nested elements carry BOTH sentinels since #2484: a list is a
        // second door onto the same codec arm, and the pair has to stay
        // distinct through it too.
        (
            "list",
            Value::List(vec![Value::Integer(1), Value::Missing, Value::None]),
        ),
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
    assert_eq!(checked, 14, "the shape sweep shrank");

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
fn a_none_attribute_survives_the_round_trip_as_a_none_2484() {
    // This test was pinned in the DIVERGING direction until #2484, because the
    // gap was the CODEC's rather than this slot's: `impl Serialize for Value`
    // wrote `Missing | None` as ONE msgpack `nil` and `visit_unit` read every
    // `nil` back as `Missing`. The two variants are deliberately DISTINCT
    // (#2203) — `None` renders `"None"` and `Missing` renders `""` — so a
    // `None` anywhere in a value that round-tripped through the state backend
    // came back rendering the empty string.
    //
    // #2484 closed it by tagging the RARE variant: `Missing` gets
    // `MISSING_TAG` in binary formats and `None` keeps the bare `nil`, so the
    // common value's bytes are unchanged and an old reader still sees exactly
    // what it saw. Flipped here rather than deleted, so the same two halves
    // that measured the gap now measure its closure.
    let e = Encoded {
        attrs: attrs_of(&[("tzinfo", Value::None)]),
        ..sample()
    };
    let back = round_trip(&e);
    assert!(
        matches!(back.attrs.get("tzinfo"), Some(Value::None)),
        "a None attribute came back as {:?}",
        back.attrs.get("tzinfo"),
    );

    // The same value with no `Encoded` involved at all — which is what made
    // "pre-existing" a measurement rather than a claim, and now makes
    // "closed for the whole codec" one.
    let plain = Value::Object(attrs_of(&[("a", Value::None)]));
    let bytes = rmp_serde::to_vec(&plain).expect("encode");
    match rmp_serde::from_slice::<Value>(&bytes).expect("decode") {
        Value::Object(map) => assert!(
            matches!(map.get("a"), Some(Value::None)),
            "a plain Object's None came back as {:?}",
            map.get("a"),
        ),
        other => panic!("expected an Object: {other:?}"),
    }
}

#[test]
fn an_empty_attribute_map_is_written_and_read_as_empty() {
    // The `opaque_value` shape: no attributes, still ten slots. Written
    // unconditionally rather than skipped, which is what keeps the slots
    // aligned (#1541).
    let e = Encoded {
        attrs: attrs_of(&[]),
        ..sample()
    };
    assert_eq!(
        parts_of(&e).len(),
        10,
        "an empty map must not drop its slot"
    );
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
    // non-default in `sample()`, and both must come back wrong. (Slot 9, the
    // items, is in the same class and gets its own swap test below.)
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
fn every_permutation_of_the_bits_len_items_and_key_round_trips() {
    // 2 truthy x 3 len x 2 iterable x 3 items x 2 key. The small slots are
    // what a one-slot shift would scramble, so sweeping them is what makes the
    // pin mechanical rather than a single sample.
    //
    // `len` gets THREE values, not two, because `Some(0)` and `Some(n)` are
    // different answers to `{{ p|length }}` and `None` is a third — the
    // widening #2477 made. `items` likewise: `None` ("never enumerated") and
    // `Some(vec![])` ("no items") are different statements and a codec that
    // collapsed them would answer `{% for %}` wrong for one of them.
    let mut checked = 0;
    for truthy in [false, true] {
        for len in [None, Some(0usize), Some(3usize)] {
            for iterable in [false, true] {
                for items in [
                    None,
                    Some(vec![]),
                    Some(vec![Value::String("<b>".to_string()), Value::Integer(1)]),
                ] {
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
                            len,
                            iterable,
                            items: items.clone(),
                            cmp_key: key,
                            ..sample()
                        };
                        assert_eq!(round_trip(&e), e, "round trip lost a field: {e:?}");
                        checked += 1;
                    }
                }
            }
        }
    }
    assert_eq!(checked, 72, "the permutation sweep shrank");
}

#[test]
fn an_absent_item_list_and_an_empty_one_do_not_collapse() {
    // The sharpest thing the sweep above asserts, spelled on its own because
    // it is the fact `iter_values` branches on (#2477/#2489): `None` means the
    // conversion never enumerated the object and `Some(vec![])` means it did
    // and there was nothing. `{% for %}` renders the `{% empty %}` block for
    // the second; the first falls through to the `iterable` bit.
    let absent = Encoded {
        items: None,
        ..sample()
    };
    let empty = Encoded {
        items: Some(vec![]),
        ..sample()
    };
    assert_ne!(absent, empty, "the two must not be equal to begin with");
    assert!(round_trip(&absent).items.is_none());
    assert!(round_trip(&empty).items.is_some_and(|i| i.is_empty()));
    assert_ne!(
        round_trip(&absent),
        round_trip(&empty),
        "the codec collapsed `None` and `Some(vec![])` into one answer",
    );
}

#[test]
fn a_swap_of_the_attribute_and_item_slots_is_detectable() {
    // Non-vacuity for slot 9, on the same argument as slots 7 and 8: a map and
    // a list both survive the reader's `match`, so only the VALUES catch a
    // swap. Both are non-default in `sample()`.
    let e = sample();
    assert!(
        !e.attrs.is_empty() && e.items.as_ref().is_some_and(|i| !i.is_empty()),
        "the sample stopped distinguishing the attribute and item slots",
    );
    let mut swapped = parts_of(&e);
    swapped.swap(8, 9);
    match decode_parts(swapped) {
        Value::Encoded(back) => {
            assert_ne!(
                *back, e,
                "a swap of the attribute and item slots was invisible"
            );
            assert!(
                back.attrs.is_empty(),
                "a list must not read as an attribute map"
            );
            assert!(back.items.is_none(), "a map must not read as items");
        }
        other => panic!("expected an Encoded with both slots lost: {other:?}"),
    }
}

#[test]
fn a_malformed_item_slot_reads_as_absent_rather_than_guessed() {
    // Same fail-to-absent the key and attribute slots take.
    let e = sample();
    for bad in [
        Value::Missing,
        Value::None,
        Value::String("['a']".to_string()),
        Value::Integer(3),
        Value::Bool(true),
        Value::Object(attrs_of(&[("a", Value::Integer(1))])),
    ] {
        let mut parts = parts_of(&e);
        parts[9] = bad.clone();
        match decode_parts(parts) {
            Value::Encoded(back) => assert!(
                back.items.is_none(),
                "a malformed item slot must read as absent, not as a guess: {bad:?}",
            ),
            other => panic!("expected an Encoded: {other:?}"),
        }
    }
}

#[test]
fn a_malformed_len_slot_reads_as_absent_rather_than_guessed() {
    let e = sample();
    for bad in [
        Value::Missing,
        Value::None,
        Value::String("3".to_string()),
        Value::Bool(true),
        Value::Float(3.0),
        Value::Integer(-1),
        Value::List(vec![Value::Integer(3)]),
    ] {
        let mut parts = parts_of(&e);
        parts[4] = bad.clone();
        match decode_parts(parts) {
            Value::Encoded(back) => assert_eq!(
                back.len, None,
                "a malformed len slot must read as absent, not as a guess: {bad:?}",
            ),
            other => panic!("expected an Encoded: {other:?}"),
        }
    }
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
    // Non-vacuity for every assertion above. Drop slot 4 (`len`) to shift
    // every later slot down one and prove the payload is refused rather than
    // silently misread.
    let e = sample();
    assert!(
        e.truthy && e.len.is_none() && e.iterable,
        "the sample stopped distinguishing the slots around `len`",
    );
    let mut shifted = parts_of(&e);
    // Now NINE elements — a real width since #2481, so this case does not
    // refuse on width alone. It refuses on TYPES: with slot 4 gone, `iterable`
    // (a bool) sits where the nine-arm wants `sized_empty` — which it happens
    // to accept — and `repr` (a string) sits where it wants a BOOL, which it
    // does not.
    shifted.remove(4);
    assert_eq!(shifted.len(), 9, "the shifted payload must be a REAL width");
    match decode_parts(shifted) {
        Value::Object(_) => {}
        other => panic!("a type-misaligned 9-element payload must not forge an Encoded: {other:?}"),
    }

    // And the sharper case: keep the width at 10 but SWAP the two BOOLEAN
    // slots that a widened `len` left adjacent-but-one — `truthy` (3) and
    // `iterable` (5). Every type still matches, so only the VALUES can catch
    // it, and the sample gives them different values for exactly that reason.
    let distinct = Encoded {
        truthy: true,
        iterable: false,
        ..sample()
    };
    let mut swapped = parts_of(&distinct);
    swapped.swap(3, 5);
    match decode_parts(swapped) {
        Value::Encoded(back) => {
            assert_ne!(*back, distinct, "a swap of two boolean slots was invisible");
            assert!(!back.truthy && back.iterable);
        }
        other => panic!("expected an Encoded with swapped bits: {other:?}"),
    }

    // And a swap of `len` with the `repr` STRING two slots over, which the
    // ten-arm must refuse on type rather than read as a length.
    let mut typed = parts_of(&e);
    typed.swap(4, 6);
    match decode_parts(typed) {
        Value::Object(_) => {}
        other => panic!("a string in the len slot must not forge an Encoded: {other:?}"),
    }
}

#[test]
fn the_older_widths_still_read_with_the_documented_fallbacks() {
    let e = sample();
    let full = parts_of(&e);
    // A LEGACY payload is not a truncation of the current one: slot 4 widened
    // from the #2466 boolean to `len(o)` in #2477/#2489, so every width below
    // 10 has a `Bool` there. Truncating `parts_of` would put a `Missing` in a
    // slot the older arms type-check as a bool, and the test would then be
    // measuring that mismatch rather than the fallbacks it is named for.
    let legacy = |n: usize, sized_empty: bool| {
        let mut v = full[..n].to_vec();
        if n > 4 {
            v[4] = Value::Bool(sized_empty);
        }
        v
    };

    // NINE — the #2481 shape: everything but the enumerated items, which
    // restore ABSENT, and `len` read off the BOOLEAN slot 4 carried. An entry
    // written by that build could only ever have been `len == Some(0)` or no
    // `__len__` at all, because the pre-#2477 gate declined everything else —
    // so the boolean restores EXACTLY, not half-way.
    for (sized_empty, expect) in [(false, None), (true, Some(0usize))] {
        match decode_parts(legacy(9, sized_empty)) {
            Value::Encoded(back) => {
                assert_eq!(
                    back.len, expect,
                    "a 9-element read must restore len from the bool"
                );
                assert!(
                    back.items.is_none(),
                    "a 9-element read must not invent items"
                );
                assert!(
                    djust_core::values_structurally_equal(
                        &Value::Object(back.attrs.clone()),
                        &Value::Object(e.attrs.clone()),
                    ),
                    "a 9-element read must keep the attributes",
                );
            }
            other => panic!("9 elements must read: {other:?}"),
        }
    }

    // EIGHT — the #2471/#2472 shape: everything but the attribute map, which
    // restores EMPTY. `{{ dt.year }}` resolved to nothing before #2481, so an
    // entry written by that build keeps answering exactly what it answered
    // rather than half-way between.
    match decode_parts(legacy(8, false)) {
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
    match decode_parts(legacy(6, false)) {
        Value::Encoded(back) => {
            assert_eq!(back.truthy, e.truthy);
            assert_eq!(back.len, None, "the 6-element bool slot 4 is `false`");
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
    match decode_parts(legacy(4, false)) {
        Value::Encoded(back) => {
            assert_eq!(back.truthy, e.truthy);
            assert!(back.len.is_none() && !back.iterable);
            assert_eq!(back.repr, e.display);
            assert_eq!(back.cmp_key, None);
            assert!(back.attrs.is_empty());
        }
        other => panic!("4 elements must read: {other:?}"),
    }

    // THREE — the #2448 shape: truthiness derived from the display.
    match decode_parts(legacy(3, false)) {
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
        match decode_parts(legacy(n, false)) {
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
