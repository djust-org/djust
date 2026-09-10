//! An aware datetime's nested `utcoffset` / `dst` / `tzinfo` are built SLIM (#2770).
//!
//! # What changed, and what did not
//!
//! Before #2770 an aware `datetime` cost ~21 µs to convert against ~9 µs naive,
//! and ~11 µs of the difference was three NESTED conversions inside
//! `django_json_encoded`'s two name tables: the `timedelta` returned by
//! `utcoffset()`, the `timedelta` returned by `dst()`, and the `tzinfo` object
//! itself — each a full `str` / `repr` / `DjangoJSONEncoder` / `bool` /
//! `isinstance` sweep through the interpreter, of which the readers consumed
//! one string and one bit.
//!
//! - The two `timedelta`s are now built by `slim_timedelta_encoded` from
//!   their three limbs in Rust. Their msgpack shape is UNCHANGED — the same
//!   eleven-slot `ENCODED_TAG` payload, byte for byte — so this half is not a
//!   wire change. This file pins the equality against CPython's own strings
//!   over a randomized sweep, because a hand port of `str(timedelta)` is a
//!   transcription and a transcription is only as good as its differential
//!   (#2231's lesson: a curated table hides the case it did not think of).
//! - The `tzinfo` slot is now `str(tz)` — ONE plain string where the nested
//!   `Encoded` of a `ZoneInfo` used to sit. That IS a msgpack-shape change of
//!   the state payload, and this file pins it in both encodings the value has:
//!   the msgpack state payload (where it appears) and the human-readable JSON
//!   (where `attrs` never appears at all, so no client sees the change).
//!
//! # The #1538 / #1541 rule, stated for this payload
//!
//! `Encoded` is a hand-written positional tuple under `ENCODED_TAG`, not a
//! serde derive, and no slot is conditionally skipped
//! (`test_encoded_wire_positions_2471_2472.rs::test_no_field_is_conditionally_skipped`).
//! #2770 adds NO slot and moves NO slot: slot 8 stays the attribute MAP, and
//! only the VALUE under one key of that map changes type (a map → a string).
//! The reader reads a map by key, never by position, so an old-shape entry
//! under `"tzinfo"` is still a `Value` the reader accepts — and
//! `Encoded::temporal_object` reads either shape through the same `str(tz)`.
//!
//! # Non-vacuity
//!
//! Every pin here was watched go red: `timedelta_str`'s plural branch
//! flipped, the `total_seconds` division rounded through `f32`, and the
//! `tzinfo` carrier reverted to `attr.extract::<Value>()` each fail exactly
//! one named test below.

use djust_core::{
    serialization, slim_timedelta_encoded, CmpKey, Encoded, Value, CMP_DOMAIN_TIMEDELTA,
};
use indexmap::IndexMap;
use pyo3::prelude::*;
use pyo3::types::PyDict;

fn timedelta_cls(py: Python<'_>) -> Bound<'_, PyAny> {
    py.import("datetime").unwrap().getattr("timedelta").unwrap()
}

fn make_timedelta<'py>(py: Python<'py>, days: i64, seconds: i64, micros: i64) -> Bound<'py, PyAny> {
    let kwargs = PyDict::new(py);
    kwargs.set_item("days", days).unwrap();
    kwargs.set_item("seconds", seconds).unwrap();
    kwargs.set_item("microseconds", micros).unwrap();
    timedelta_cls(py).call((), Some(&kwargs)).unwrap()
}

fn str_of(ob: &Bound<'_, PyAny>) -> String {
    ob.str().unwrap().extract().unwrap()
}

fn repr_of(ob: &Bound<'_, PyAny>) -> String {
    ob.repr().unwrap().extract().unwrap()
}

/// A tiny deterministic LCG so the sweep needs no `rand` dev-dependency and
/// replays byte-for-byte from the seed.
struct Lcg(u64);

impl Lcg {
    fn next(&mut self) -> u64 {
        self.0 = self
            .0
            .wrapping_mul(6364136223846793005)
            .wrapping_add(1442695040888963407);
        self.0 >> 11
    }
    fn range(&mut self, lo: i64, hi: i64) -> i64 {
        lo + (self.next() % ((hi - lo + 1) as u64)) as i64
    }
}

/// Every slot of the slim `Encoded`, against the live object's own answers.
fn assert_slim_matches(py: Python<'_>, td: &Bound<'_, PyAny>, label: &str) {
    let slim = slim_timedelta_encoded(td, &timedelta_cls(py))
        .unwrap_or_else(|| panic!("{label}: slim path declined an exact timedelta"));
    assert_eq!(slim.type_name, "datetime.timedelta", "{label}: type_name");
    assert_eq!(slim.display, str_of(td), "{label}: str()");
    assert_eq!(slim.repr, repr_of(td), "{label}: repr()");
    assert_eq!(slim.truthy, td.is_truthy().unwrap(), "{label}: bool()");
    assert_eq!(slim.len, None, "{label}: len");
    assert!(!slim.iterable, "{label}: iterable");
    assert!(slim.items.is_none(), "{label}: items");
    assert_eq!(slim.eq_class, None, "{label}: eq_class");
    assert!(
        slim.live.is_some(),
        "{label}: the live handle is attached (#2741)"
    );
    assert!(!slim.display_safe, "{label}: display_safe");

    let days: i64 = td.getattr("days").unwrap().extract().unwrap();
    let seconds: i64 = td.getattr("seconds").unwrap().extract().unwrap();
    let micros: i64 = td.getattr("microseconds").unwrap().extract().unwrap();
    let total: f64 = td.call_method0("total_seconds").unwrap().extract().unwrap();
    assert_eq!(
        slim.cmp_key,
        Some(CmpKey {
            domain: CMP_DOMAIN_TIMEDELTA,
            hi: days,
            lo: seconds * 1_000_000 + micros,
        }),
        "{label}: cmp_key"
    );
    // The map, in the order the two producers write it (#2481 pins the order
    // from Python; this is the same order from Rust).
    let keys: Vec<&str> = slim.attrs.keys().map(|k| k.as_str().unwrap()).collect();
    assert_eq!(
        keys,
        ["days", "seconds", "microseconds", "total_seconds"],
        "{label}: attrs order"
    );
    assert!(
        matches!(slim.attrs.get("days"), Some(Value::Integer(d)) if *d == days),
        "{label}: days"
    );
    assert!(
        matches!(slim.attrs.get("seconds"), Some(Value::Integer(s)) if *s == seconds),
        "{label}: seconds"
    );
    assert!(
        matches!(slim.attrs.get("microseconds"), Some(Value::Integer(m)) if *m == micros),
        "{label}: micros"
    );
    match slim.attrs.get("total_seconds") {
        // BIT equality, not approximate: the slot is what a template renders
        // and what a state entry carries.
        Some(Value::Float(f)) => assert_eq!(f.to_bits(), total.to_bits(), "{label}: total_seconds"),
        other => panic!("{label}: total_seconds is {other:?}"),
    }
}

#[test]
fn the_slim_timedelta_matches_cpython_over_a_randomized_sweep() {
    Python::initialize();
    Python::attach(|py| {
        // The hand-picked boundaries first: zero, ±1 second (the `-1 day,
        // 23:59:59` normalisation), the singular/plural `day` branch on both
        // signs, a microsecond-only value, and the largest offset Python
        // accepts on either side of a day.
        let named: &[(i64, i64, i64)] = &[
            (0, 0, 0),
            (0, 1, 0),
            (0, -1, 0),
            (1, 0, 0),
            (-1, 0, 0),
            (2, 0, 5),
            (-2, 0, 5),
            (0, 0, 1),
            (0, 86_399, 999_999),
            (0, -86_399, -999_999),
            (0, 32_400, 0),
            (0, -9_000, 0),
        ];
        for &(d, s, u) in named {
            let td = make_timedelta(py, d, s, u);
            assert_slim_matches(py, &td, &format!("named ({d}, {s}, {u})"));
        }
        // Then the sweep: 3,000 values, biased to the offset range a
        // `utcoffset()` / `dst()` can take (within ±1 day) but reaching past
        // it, with microseconds present in a third of the cases.
        let mut rng = Lcg(0x2770);
        for i in 0..3_000 {
            let days = rng.range(-3, 3);
            let seconds = rng.range(-86_399, 86_399);
            let micros = if i % 3 == 0 {
                rng.range(-999_999, 999_999)
            } else {
                0
            };
            let td = make_timedelta(py, days, seconds, micros);
            assert_slim_matches(
                py,
                &td,
                &format!("sweep #{i} ({days}, {seconds}, {micros})"),
            );
        }
    });
}

#[test]
fn the_slim_json_is_djangos_duration_iso_string() {
    // `DjangoJSONEncoder.default` is not importable on this embedded
    // interpreter (no Django on `sys.path` in the Rust test venv — see
    // `test_datetime_live_handle_2741.py`), so the JSON slot is pinned here
    // against values MEASURED from Django 5's `duration_iso_string` and
    // pinned again, over a randomized sweep and against live Django, in
    // `test_aware_datetime_slim_2770.py` (the nested payload equals the
    // full-path payload of the same value, JSON slot included).
    Python::initialize();
    Python::attach(|py| {
        let cases: &[((i64, i64, i64), &str)] = &[
            ((0, 0, 0), "P0DT00H00M00S"),
            ((0, 32_400, 0), "P0DT09H00M00S"),
            ((0, -9_000, 0), "-P0DT02H30M00S"),
            ((0, -1, 0), "-P0DT00H00M01S"),
            ((2, 0, 5), "P2DT00H00M00.000005S"),
            ((0, 3_600, 0), "P0DT01H00M00S"),
        ];
        for &((d, s, u), want) in cases {
            let td = make_timedelta(py, d, s, u);
            let slim = slim_timedelta_encoded(&td, &timedelta_cls(py)).unwrap();
            assert_eq!(slim.json, want, "json for ({d}, {s}, {u})");
        }
    });
}

#[test]
fn a_subclass_a_non_timedelta_and_an_out_of_range_value_decline_the_slim_path() {
    Python::initialize();
    Python::attach(|py| {
        let cls = timedelta_cls(py);
        // A subclass may override `__str__` / `__bool__` / `__repr__`; only
        // the full path measures those, so the slim one must decline.
        let sub = py
            .import("builtins")
            .unwrap()
            .getattr("type")
            .unwrap()
            .call1(("MyDelta", (cls.clone(),), PyDict::new(py)))
            .unwrap();
        let kwargs = PyDict::new(py);
        kwargs.set_item("seconds", 90).unwrap();
        let sub_value = sub.call((), Some(&kwargs)).unwrap();
        assert!(
            slim_timedelta_encoded(&sub_value, &cls).is_none(),
            "subclass"
        );

        // Not a timedelta at all.
        let not = py
            .import("datetime")
            .unwrap()
            .getattr("date")
            .unwrap()
            .call1((2026, 3, 4))
            .unwrap();
        assert!(slim_timedelta_encoded(&not, &cls).is_none(), "date");

        // Past the exact-`f64` bound (2**53 µs ≈ 104,249 days): the full
        // path's `total_seconds()` is CPython's correctly-rounded big-int
        // division, which the slim `n as f64 / 1e6` no longer matches.
        let huge = make_timedelta(py, 200_000, 0, 0);
        assert!(
            slim_timedelta_encoded(&huge, &cls).is_none(),
            "out of range"
        );
        // ...and one day under the bound is fine.
        let under = make_timedelta(py, 104_248, 0, 0);
        assert!(
            slim_timedelta_encoded(&under, &cls).is_some(),
            "under the bound"
        );
    });
}

/// An `Encoded` aware datetime in the #2770 shape, as `django_json_encoded`
/// now builds it: `tzinfo` a plain string, `utcoffset` / `dst` slim
/// timedeltas.
fn aware_datetime(py: Python<'_>) -> Encoded {
    let offset = make_timedelta(py, 0, 7_200, 0);
    let dst = make_timedelta(py, 0, 3_600, 0);
    let cls = timedelta_cls(py);
    let mut attrs = IndexMap::new();
    // `year` + `hour` + `microsecond` are what `temporal_kind` keys a
    // `datetime` on; without them the restore would try `date.fromisoformat`.
    attrs.insert("year".into(), Value::Integer(2026));
    attrs.insert("hour".into(), Value::Integer(5));
    attrs.insert("microsecond".into(), Value::Integer(0));
    attrs.insert("fold".into(), Value::Integer(0));
    attrs.insert("tzinfo".into(), Value::String("Europe/Berlin".to_string()));
    attrs.insert(
        "utcoffset".into(),
        Value::Encoded(Box::new(slim_timedelta_encoded(&offset, &cls).unwrap())),
    );
    attrs.insert("tzname".into(), Value::String("CEST".to_string()));
    attrs.insert(
        "dst".into(),
        Value::Encoded(Box::new(slim_timedelta_encoded(&dst, &cls).unwrap())),
    );
    Encoded {
        type_name: "datetime.datetime".to_string(),
        // The `T` separator rather than `str(dt)`'s space: the embedded
        // interpreter `cargo test` links may predate 3.11, whose
        // `fromisoformat` is the first to accept the space form.
        display: "2026-07-04T05:06:07+02:00".to_string(),
        json: "2026-07-04T05:06:07+02:00".to_string(),
        truthy: true,
        len: None,
        iterable: false,
        repr:
            "datetime.datetime(2026, 7, 4, 5, 6, 7, tzinfo=zoneinfo.ZoneInfo(key='Europe/Berlin'))"
                .to_string(),
        cmp_key: Some(CmpKey {
            domain: djust_core::CMP_DOMAIN_DATETIME_AWARE,
            hi: 739_801,
            lo: 11_167_000_000,
        }),
        attrs,
        items: None,
        eq_class: None,
        live: None,
        display_safe: false,
        str_raised: false,
    }
}

#[test]
fn the_msgpack_state_payload_carries_tzinfo_as_a_string_and_the_deltas_as_encodeds() {
    Python::initialize();
    Python::attach(|py| {
        let value = Value::Encoded(Box::new(aware_datetime(py)));
        let bytes = serialization::to_msgpack(&value).unwrap();
        // Decoded GENERICALLY rather than through `Value`'s own reader, which
        // accepts both shapes and so could not pin which one was WRITTEN.
        let raw: serde_json::Value = rmp_serde::from_slice(&bytes).unwrap();
        let payload = &raw[djust_core::encoded_tag()];
        let slots = payload
            .as_array()
            .expect("an ENCODED_TAG payload is a positional array");
        assert_eq!(slots.len(), 11, "slot count is unchanged by #2770");
        let attrs = slots[8].as_object().expect("slot 8 is the attribute MAP");
        // The shape change: a STRING, not a nested `ENCODED_TAG` map.
        assert_eq!(attrs["tzinfo"], serde_json::json!("Europe/Berlin"));
        // The two deltas keep their pre-#2770 shape exactly: a nested
        // eleven-slot payload whose first slot names the type.
        for name in ["utcoffset", "dst"] {
            let nested = attrs[name][djust_core::encoded_tag()]
                .as_array()
                .unwrap_or_else(|| panic!("{name} is a nested ENCODED_TAG payload"));
            assert_eq!(nested.len(), 11, "{name}: nested slot count");
            assert_eq!(
                nested[0],
                serde_json::json!("datetime.timedelta"),
                "{name}: type_name"
            );
        }
        assert_eq!(
            attrs["utcoffset"][djust_core::encoded_tag()][1],
            serde_json::json!("2:00:00")
        );
        assert_eq!(
            attrs["dst"][djust_core::encoded_tag()][1],
            serde_json::json!("1:00:00")
        );

        // And the reader restores it, string and all.
        let back = serialization::from_msgpack(&bytes).unwrap();
        let Value::Encoded(back) = back else {
            panic!("not an Encoded")
        };
        assert!(matches!(back.attrs.get("tzinfo"), Some(Value::String(s)) if s == "Europe/Berlin"));
        assert!(
            matches!(back.attrs.get("utcoffset"), Some(Value::Encoded(e)) if e.display == "2:00:00")
        );
    });
}

#[test]
fn the_json_encoding_carries_no_attribute_map_so_no_client_sees_the_change() {
    // The issue's claim "client JSON never carries `attrs`" — verified here
    // rather than assumed. The human-readable `Serialize` arm writes an
    // `Encoded` as its DISPLAY string, and nothing else.
    Python::initialize();
    Python::attach(|py| {
        let value = Value::Encoded(Box::new(aware_datetime(py)));
        let json = serialization::to_json(&value).unwrap();
        assert_eq!(json, "\"2026-07-04T05:06:07+02:00\"");
        assert!(!json.contains("tzinfo") && !json.contains("utcoffset"));
    });
}

#[test]
fn temporal_object_restores_the_zone_from_either_tzinfo_shape() {
    // Rolling deploy (#2770 (d)): a pre-#2770 process wrote the zone as a
    // nested `Encoded` whose `display` is `str(tz)`; the new reader accepts
    // that AND the new plain string, through one `str(tz)`.
    Python::initialize();
    Python::attach(|py| {
        let new_shape = aware_datetime(py);
        let mut old_shape = aware_datetime(py);
        old_shape.attrs.insert(
            "tzinfo".into(),
            Value::Encoded(Box::new(Encoded {
                type_name: "ZoneInfo".to_string(),
                display: "Europe/Berlin".to_string(),
                json: "Europe/Berlin".to_string(),
                truthy: true,
                len: None,
                iterable: false,
                repr: "zoneinfo.ZoneInfo(key='Europe/Berlin')".to_string(),
                cmp_key: None,
                attrs: IndexMap::new(),
                items: None,
                eq_class: None,
                live: None,
                display_safe: false,
                str_raised: false,
            })),
        );
        for (label, e) in [("new", new_shape), ("old", old_shape)] {
            let restored = e
                .temporal_object(py)
                .unwrap()
                .unwrap_or_else(|| panic!("{label}"));
            let key: String = restored
                .getattr("tzinfo")
                .unwrap()
                .getattr("key")
                .unwrap()
                .extract()
                .unwrap();
            assert_eq!(key, "Europe/Berlin", "{label} shape restores a ZoneInfo");
            let tzname: String = restored.call_method0("tzname").unwrap().extract().unwrap();
            assert_eq!(tzname, "CEST", "{label}: tzname() after restore");
        }
    });
}

/// The body of one `fn name(` in `lib.rs`, by brace matching from its first
/// `{` — the same source-pin shape `filters.rs`'s `value_to_json` guard uses.
fn fn_body(source: &str, name: &str) -> String {
    let start = source
        .find(&format!("fn {name}("))
        .unwrap_or_else(|| panic!("`fn {name}(` not found in lib.rs"));
    let open = start + source[start..].find('{').expect("a body");
    let mut depth = 0usize;
    for (i, c) in source[open..].char_indices() {
        match c {
            '{' => depth += 1,
            '}' => {
                depth -= 1;
                if depth == 0 {
                    return source[open..open + i + 1].to_string();
                }
            }
            _ => {}
        }
    }
    panic!("`fn {name}(` has no closing brace");
}

#[test]
fn the_two_producers_route_the_three_names_through_the_slim_builders() {
    // The slim path is BYTE-EQUAL to the full one for the two deltas, so no
    // payload assertion can tell whether `collect_called_attrs` still calls
    // `slim_timedelta_encoded` (gating it off leaves every payload test
    // green — that is the point of the design). The mechanism is therefore
    // pinned at the source: the producer must call the builder, and the
    // µs/object table in the PR is the behavioural witness.
    let source = include_str!("../src/lib.rs");
    let called = fn_body(source, "collect_called_attrs");
    assert!(
        called.contains("slim_timedelta_encoded(&result, timedelta_cls)"),
        "collect_called_attrs no longer builds its timedelta results slim:\n{called}"
    );
    let named = fn_body(source, "collect_named_attrs");
    assert!(
        named.contains("slim_tzinfo(&attr)"),
        "collect_named_attrs no longer carries `tzinfo` as str(tz):\n{named}"
    );
    // ...and nowhere else: the two builders have exactly these two callers
    // (#1125 — a set, not a floor).
    assert_eq!(
        source.matches("slim_timedelta_encoded(").count(),
        2,
        "definition + one call"
    );
    assert_eq!(
        source.matches("slim_tzinfo(").count(),
        2,
        "definition + one call"
    );
}
