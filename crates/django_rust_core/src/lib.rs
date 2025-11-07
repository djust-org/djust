//! Core utilities and types for django-rust-live
//!
//! This crate provides foundational data structures and utilities used across
//! the django-rust-live ecosystem.

use pyo3::prelude::*;
use serde::{Deserialize, Serialize};
use std::collections::HashMap;

pub mod context;
pub mod errors;
pub mod serialization;

pub use context::Context;
pub use errors::{DjangoRustError, Result};

/// A value that can be used in Django templates
#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(untagged)]
pub enum Value {
    Null,
    Bool(bool),
    Integer(i64),
    Float(f64),
    String(String),
    List(Vec<Value>),
    Object(HashMap<String, Value>),
}

impl Value {
    pub fn is_truthy(&self) -> bool {
        match self {
            Value::Null => false,
            Value::Bool(b) => *b,
            Value::Integer(i) => *i != 0,
            Value::Float(f) => *f != 0.0,
            Value::String(s) => !s.is_empty(),
            Value::List(l) => !l.is_empty(),
            Value::Object(o) => !o.is_empty(),
        }
    }

    pub fn to_string(&self) -> String {
        match self {
            Value::Null => String::new(),
            Value::Bool(b) => b.to_string(),
            Value::Integer(i) => i.to_string(),
            Value::Float(f) => f.to_string(),
            Value::String(s) => s.clone(),
            Value::List(_) => "[List]".to_string(),
            Value::Object(_) => "[Object]".to_string(),
        }
    }
}

impl<'py> FromPyObject<'py> for Value {
    fn extract_bound(ob: &pyo3::Bound<'py, PyAny>) -> PyResult<Self> {
        if ob.is_none() {
            Ok(Value::Null)
        } else if let Ok(b) = ob.extract::<bool>() {
            Ok(Value::Bool(b))
        } else if let Ok(i) = ob.extract::<i64>() {
            Ok(Value::Integer(i))
        } else if let Ok(f) = ob.extract::<f64>() {
            Ok(Value::Float(f))
        } else if let Ok(s) = ob.extract::<String>() {
            Ok(Value::String(s))
        } else if let Ok(list) = ob.extract::<Vec<Value>>() {
            Ok(Value::List(list))
        } else if let Ok(dict) = ob.extract::<HashMap<String, Value>>() {
            Ok(Value::Object(dict))
        } else {
            Ok(Value::String(ob.str()?.to_string()))
        }
    }
}

impl ToPyObject for Value {
    fn to_object(&self, py: Python) -> PyObject {
        match self {
            Value::Null => py.None(),
            Value::Bool(b) => b.to_object(py),
            Value::Integer(i) => i.to_object(py),
            Value::Float(f) => f.to_object(py),
            Value::String(s) => s.to_object(py),
            Value::List(l) => l.to_object(py),
            Value::Object(o) => o.to_object(py),
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_value_truthy() {
        assert!(!Value::Null.is_truthy());
        assert!(Value::Bool(true).is_truthy());
        assert!(!Value::Bool(false).is_truthy());
        assert!(Value::Integer(1).is_truthy());
        assert!(!Value::Integer(0).is_truthy());
        assert!(Value::String("hello".to_string()).is_truthy());
        assert!(!Value::String("".to_string()).is_truthy());
    }
}
