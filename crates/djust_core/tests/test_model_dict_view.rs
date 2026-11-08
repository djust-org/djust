//! A serialized Django model is not a mapping to a template.
//!
//! A model reaches the engine as a `Value::Object` carrying a `"__str__"` string (the marker
//! `Value::object_str` reads). `{{ model.items }}`, `.keys` and `.values` are empty in Django
//! (a model has no such attributes), and so are they for a live model handle, so the map must
//! not answer its entries: they are the serializer's. A named read, an ordinary dict and a field
//! that happens to be named `items` are unchanged.

use djust_core::{Context, DictViewKind, ObjectKey, Value};
use indexmap::IndexMap;

fn model_value() -> Value {
    let mut map = IndexMap::new();
    for (key, value) in [
        ("id", Value::Integer(1)),
        ("pk", Value::Integer(1)),
        ("__str__", Value::String("Account:a1".to_string())),
        ("__model__", Value::String("Account".to_string())),
        ("owner_code", Value::String("SENTINEL-A".to_string())),
        ("get_note_text", Value::String("SENTINEL-B".to_string())),
    ] {
        map.insert(ObjectKey::Str(key.to_string()), value);
    }
    Value::Object(map)
}

fn model_ctx() -> Context {
    let mut ctx = Context::new();
    ctx.set("r".to_string(), model_value());
    ctx.set("rows".to_string(), Value::List(vec![model_value()]));
    ctx
}

#[test]
fn a_serialized_model_has_no_dict_methods() {
    let ctx = model_ctx();
    for path in [
        "r.items",
        "r.keys",
        "r.values",
        "rows.0.items",
        "rows.0.keys",
        "rows.0.values",
    ] {
        assert!(
            ctx.resolve(path).unwrap().is_none(),
            "{path} resolved to {:?}",
            ctx.resolve(path)
        );
    }
}

#[test]
fn a_named_read_of_a_serialized_model_is_unchanged() {
    let ctx = model_ctx();
    assert_eq!(
        ctx.resolve("r.owner_code").unwrap().unwrap().to_string(),
        "SENTINEL-A"
    );
    assert_eq!(ctx.resolve("r.id").unwrap().unwrap().to_string(), "1");
}

#[test]
fn a_field_named_items_on_a_serialized_model_still_wins() {
    let mut map = IndexMap::new();
    map.insert(
        ObjectKey::Str("__str__".to_string()),
        Value::String("Doc".to_string()),
    );
    map.insert(ObjectKey::Str("items".to_string()), Value::Integer(5));
    let mut ctx = Context::new();
    ctx.set("d".to_string(), Value::Object(map));
    assert_eq!(ctx.resolve("d.items").unwrap().unwrap().to_string(), "5");
}

#[test]
fn an_ordinary_dict_still_answers_its_entries() {
    let mut map = IndexMap::new();
    map.insert(ObjectKey::Str("a".to_string()), Value::Integer(1));
    map.insert(ObjectKey::Str("b".to_string()), Value::Integer(2));
    let mut ctx = Context::new();
    ctx.set("d".to_string(), Value::Object(map));
    match ctx.resolve("d.items").unwrap().unwrap() {
        Value::DictView { kind, items } => {
            assert_eq!(kind, DictViewKind::Items);
            assert_eq!(items.len(), 2);
        }
        other => panic!("expected a dict view, got {other:?}"),
    }
    match ctx.resolve("d.keys").unwrap().unwrap() {
        Value::DictView { items, .. } => assert_eq!(items.len(), 2),
        other => panic!("expected a dict view, got {other:?}"),
    }
}

#[test]
fn a_model_with_a_safe_text_has_no_dict_methods_either() {
    let mut map = IndexMap::new();
    for (key, value) in [
        ("pk", Value::Integer(1)),
        ("__str__", Value::SafeString("<b>a1</b>".to_string())),
        ("__model__", Value::String("Safe".to_string())),
        ("owner_code", Value::String("SENTINEL-A".to_string())),
    ] {
        map.insert(ObjectKey::Str(key.to_string()), value);
    }
    let mut ctx = Context::new();
    ctx.set("r".to_string(), Value::Object(map));
    for path in ["r.items", "r.keys", "r.values"] {
        assert!(ctx.resolve(path).unwrap().is_none(), "{path}");
    }
}

/// A dict that only carries a text `__str__` key (user data) is not a model: it keeps its views.
#[test]
fn a_dict_with_only_a_text_key_keeps_its_dict_methods() {
    let mut map = IndexMap::new();
    map.insert(
        ObjectKey::Str("__str__".to_string()),
        Value::String("lbl".to_string()),
    );
    map.insert(
        ObjectKey::Str("k1".to_string()),
        Value::String("v1".to_string()),
    );
    let mut ctx = Context::new();
    ctx.set("d".to_string(), Value::Object(map));
    for path in ["d.items", "d.keys", "d.values"] {
        match ctx.resolve(path).unwrap().unwrap() {
            Value::DictView { items, .. } => assert_eq!(items.len(), 2, "{path}"),
            other => panic!("{path}: expected a dict view, got {other:?}"),
        }
    }
}
