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
}
