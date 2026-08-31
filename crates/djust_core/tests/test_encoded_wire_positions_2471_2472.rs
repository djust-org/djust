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
//! ```
//!
//! Every widening APPENDS. That is what lets the reader accept 8, 6, 4 and 3
//! elements from older writers, and it is why this merge put #2471/#2472's two
//! fields AFTER #2466's rather than in struct-declaration order.
//!
//! # Non-vacuity
//!
//! Slots 3, 4 and 5 are three consecutive BOOLEANS, so a one-slot shift among
//! them type-checks and would deserialize into a plausible struct. Every
//! fixture below therefore gives them DISTINCT values, and
//! `test_a_one_slot_shift_is_detectable` proves the assertions can actually see
//! a shift rather than merely passing over one.

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
    }
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
    v
}

#[test]
fn the_payload_is_eight_slots_in_the_documented_order() {
    let e = sample();
    // `Value` has no `PartialEq` (the renderer's own tests note it), so match
    // the variant and compare the `Encoded`, which does derive one.
    match decode_parts(parts_of(&e)) {
        Value::Encoded(back) => assert_eq!(*back, e),
        other => panic!("the eight-slot payload did not read as an Encoded: {other:?}"),
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
    shifted.remove(4); // now 7 elements: no width matches, so it is NOT an Encoded
    match decode_parts(shifted) {
        Value::Object(_) => {}
        other => panic!("a 7-element payload must not forge an Encoded: {other:?}"),
    }

    // And the sharper case: keep the width at 8 but SWAP two boolean slots.
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
        }
        other => panic!("4 elements must read: {other:?}"),
    }

    // THREE — the #2448 shape: truthiness derived from the display.
    match decode_parts(full[..3].to_vec()) {
        Value::Encoded(back) => {
            assert_eq!(back.truthy, !e.display.is_empty());
            assert_eq!(back.repr, e.display);
            assert_eq!(back.cmp_key, None);
        }
        other => panic!("3 elements must read: {other:?}"),
    }

    // FIVE and SEVEN are NOT shapes this crate ever wrote, so they must fall
    // through to a plain dict rather than being read as a truncated Encoded —
    // which is what keeps a user dict under this key from forging one.
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
    assert!(
        !src.contains("#[derive(Debug, Clone, PartialEq, Serialize"),
        "`Encoded` gained a derived Serialize — the payload order is now in two \
         places",
    );
}
