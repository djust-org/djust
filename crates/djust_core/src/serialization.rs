//! Fast serialization utilities for transferring data between Rust and Python

use crate::{errors::DjangoRustError, errors::Result, Value};
use rmp_serde;
use serde_json;

/// Serialize a value to JSON
pub fn to_json(value: &Value) -> Result<String> {
    serde_json::to_string(value).map_err(|e| DjangoRustError::SerializationError(e.to_string()))
}

/// Deserialize a value from JSON
pub fn from_json(json: &str) -> Result<Value> {
    serde_json::from_str(json).map_err(|e| DjangoRustError::SerializationError(e.to_string()))
}

/// Serialize a value to MessagePack (binary)
pub fn to_msgpack(value: &Value) -> Result<Vec<u8>> {
    rmp_serde::to_vec(value).map_err(|e| DjangoRustError::SerializationError(e.to_string()))
}

/// Deserialize a value from MessagePack (binary)
pub fn from_msgpack(bytes: &[u8]) -> Result<Value> {
    rmp_serde::from_slice(bytes).map_err(|e| DjangoRustError::SerializationError(e.to_string()))
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_json_roundtrip() {
        let original = Value::String("Hello, World!".to_string());
        let json = to_json(&original).unwrap();
        let deserialized = from_json(&json).unwrap();

        match deserialized {
            Value::String(s) => assert_eq!(s, "Hello, World!"),
            _ => panic!("Expected string"),
        }
    }

    #[test]
    fn test_msgpack_roundtrip() {
        let original = Value::Integer(42);
        let bytes = to_msgpack(&original).unwrap();
        let deserialized = from_msgpack(&bytes).unwrap();

        match deserialized {
            Value::Integer(i) => assert_eq!(i, 42),
            _ => panic!("Expected integer"),
        }
    }

    /// Regression test for #612: dict state deserialized as list after Rust state sync.
    /// With #[serde(untagged)], rmp_serde could deserialize a msgpack map as List
    /// because the untagged deserializer tried variants in declaration order.
    #[test]
    fn test_msgpack_dict_roundtrip_not_list() {
        use std::collections::HashMap;

        let mut map = HashMap::new();
        map.insert("key1".to_string(), Value::String("value1".to_string()));
        map.insert("key2".to_string(), Value::Integer(42));
        map.insert(
            "nested".to_string(),
            Value::Object({
                let mut inner = HashMap::new();
                inner.insert("a".to_string(), Value::Bool(true));
                inner
            }),
        );
        let original = Value::Object(map);

        let bytes = to_msgpack(&original).unwrap();
        let deserialized = from_msgpack(&bytes).unwrap();

        match &deserialized {
            Value::Object(obj) => {
                assert_eq!(obj.len(), 3);
                assert!(matches!(obj.get("key1"), Some(Value::String(s)) if s == "value1"));
                assert!(matches!(obj.get("key2"), Some(Value::Integer(42))));
                match obj.get("nested") {
                    Some(Value::Object(inner)) => {
                        assert!(matches!(inner.get("a"), Some(Value::Bool(true))));
                    }
                    other => panic!("Expected nested Object, got {:?}", other),
                }
            }
            other => panic!(
                "Expected Object after msgpack round-trip, got {:?} (this is the #612 bug)",
                other
            ),
        }
    }

    /// Ensure list values still deserialize correctly as lists (not as objects).
    #[test]
    fn test_msgpack_list_stays_list() {
        let original = Value::List(vec![
            Value::Integer(1),
            Value::String("two".to_string()),
            Value::Bool(false),
        ]);

        let bytes = to_msgpack(&original).unwrap();
        let deserialized = from_msgpack(&bytes).unwrap();

        match &deserialized {
            Value::List(items) => {
                assert_eq!(items.len(), 3);
                assert!(matches!(&items[0], Value::Integer(1)));
                assert!(matches!(&items[1], Value::String(s) if s == "two"));
                assert!(matches!(&items[2], Value::Bool(false)));
            }
            other => panic!("Expected List, got {:?}", other),
        }
    }

    /// Dict with dict values round-trips correctly through JSON too.
    #[test]
    fn test_json_dict_roundtrip() {
        use std::collections::HashMap;

        let mut map = HashMap::new();
        map.insert("name".to_string(), Value::String("test".to_string()));
        map.insert("count".to_string(), Value::Integer(5));
        let original = Value::Object(map);

        let json = to_json(&original).unwrap();
        let deserialized = from_json(&json).unwrap();

        match &deserialized {
            Value::Object(obj) => {
                assert_eq!(obj.len(), 2);
                assert!(matches!(obj.get("name"), Some(Value::String(s)) if s == "test"));
                assert!(matches!(obj.get("count"), Some(Value::Integer(5))));
            }
            other => panic!("Expected Object, got {:?}", other),
        }
    }
}
