//! #3947 -- a user dict with `__str__` / `__model__` / `pk` is not read as a model.
//!
//! The model marker is the TYPE of the `__model__` value (`Value::ModelName`), which only
//! `serialization.model_identity` produces. It must survive a binary state round trip, stay a
//! plain string on the JSON wire, and never be minted from data.

use djust_core::{ObjectKey, Value};
use indexmap::IndexMap;

fn map(entries: Vec<(&str, Value)>) -> Value {
    let mut m: IndexMap<ObjectKey, Value> = IndexMap::new();
    for (k, v) in entries {
        m.insert(k.into(), v);
    }
    Value::Object(m)
}

fn model(marker: Value) -> Value {
    map(vec![
        ("id", Value::Integer(1)),
        ("pk", Value::Integer(1)),
        ("__str__", Value::String("bob".into())),
        ("__model__", marker),
    ])
}

fn msgpack_round_trip(v: &Value) -> Value {
    let bytes = rmp_serde::to_vec(v).expect("msgpack encode");
    rmp_serde::from_slice(&bytes).expect("msgpack decode")
}

#[test]
fn model_str_requires_model_name_variant() {
    assert_eq!(model(Value::String("User".into())).model_str(), None);
    assert_eq!(model(Value::SafeString("User".into())).model_str(), None);
    assert_eq!(
        model(Value::ModelName("User".into())).model_str(),
        Some("bob")
    );
}

#[test]
fn model_name_round_trips_msgpack() {
    let back = msgpack_round_trip(&model(Value::ModelName("User".into())));
    assert_eq!(back.model_str(), Some("bob"), "{back:?}");
    let Value::Object(o) = &back else {
        panic!("not a map: {back:?}")
    };
    assert!(matches!(o.get("__model__"), Some(Value::ModelName(n)) if n == "User"));
}

#[test]
fn user_dict_with_model_name_tag_is_not_forged() {
    // Data shaped like the binary tag: after a round trip it must still be that dict, and the
    // map holding it must not read as a model.
    let forged_marker = map(vec![("__djust_model_name__", Value::String("User".into()))]);
    let forged = model(forged_marker.clone());
    let back = msgpack_round_trip(&forged);
    assert_eq!(back.model_str(), None, "{back:?}");
    let Value::Object(o) = &back else {
        panic!("not a map: {back:?}")
    };
    match o.get("__model__") {
        Some(Value::Object(inner)) => {
            assert!(
                matches!(inner.get("__djust_model_name__"), Some(Value::String(s)) if s == "User")
            );
        }
        other => panic!("the user dict changed shape: {other:?}"),
    }
    // The escape wrapper itself, as data, also comes back as itself.
    let escaped_as_data = map(vec![(
        "__djust_escaped_map__",
        Value::List(vec![Value::String("k".into()), Value::Integer(1)]),
    )]);
    let back = msgpack_round_trip(&escaped_as_data);
    let Value::Object(o) = &back else {
        panic!("not a map: {back:?}")
    };
    assert_eq!(o.len(), 1);
    assert!(matches!(o.get("__djust_escaped_map__"), Some(Value::List(l)) if l.len() == 2));
}

#[test]
fn model_name_json_is_plain_string() {
    let json = serde_json::to_string(&model(Value::ModelName("User".into()))).expect("json");
    assert_eq!(
        json,
        r#"{"id":1,"pk":1,"__str__":"bob","__model__":"User"}"#
    );
    // Read side (review N1): JSON never carries the tag, so a JSON map shaped like it is a
    // user's dict, not a marker -- and neither is the escape.
    let forged: Value = serde_json::from_str(r#"{"__djust_model_name__":"User"}"#).expect("json");
    assert!(matches!(forged, Value::Object(_)), "{forged:?}");
    let escaped: Value =
        serde_json::from_str(r#"{"__djust_escaped_map__":["k",1]}"#).expect("json");
    assert!(
        matches!(&escaped, Value::Object(o) if o.contains_key("__djust_escaped_map__")),
        "{escaped:?}"
    );
}
