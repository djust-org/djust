//! Core utilities and types for djust
//!
//! This crate provides foundational data structures and utilities used across
//! the djust ecosystem.

use pyo3::prelude::*;
use pyo3::types::{PyAnyMethods, PyDict, PyList};
use serde::de::{self, MapAccess, SeqAccess, Visitor};
use serde::{Deserialize, Deserializer, Serialize};
use std::collections::HashMap;
use std::fmt;

pub mod context;
pub mod errors;
pub mod serialization;

pub use context::Context;
pub use errors::{DjangoRustError, Result};

/// A value that can be used in Django templates
///
/// Uses a custom `Deserialize` implementation instead of `#[serde(untagged)]`
/// to correctly distinguish maps from arrays during MessagePack deserialization.
/// With `#[serde(untagged)]`, `rmp_serde` could deserialize a msgpack map as
/// `List` because the untagged deserializer tries variants in declaration order
/// and msgpack maps can be reinterpreted as sequences of pairs (#612).
#[derive(Debug, Clone, Serialize)]
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

/// Custom Deserialize that uses the deserializer's type hints to distinguish
/// maps from sequences, fixing dict→list corruption in MessagePack round-trips (#612).
impl<'de> Deserialize<'de> for Value {
    fn deserialize<D>(deserializer: D) -> std::result::Result<Self, D::Error>
    where
        D: Deserializer<'de>,
    {
        struct ValueVisitor;

        impl<'de> Visitor<'de> for ValueVisitor {
            type Value = Value;

            fn expecting(&self, formatter: &mut fmt::Formatter) -> fmt::Result {
                formatter.write_str("a JSON/MessagePack value")
            }

            fn visit_unit<E>(self) -> std::result::Result<Value, E>
            where
                E: de::Error,
            {
                Ok(Value::Null)
            }

            fn visit_none<E>(self) -> std::result::Result<Value, E>
            where
                E: de::Error,
            {
                Ok(Value::Null)
            }

            fn visit_some<D>(self, deserializer: D) -> std::result::Result<Value, D::Error>
            where
                D: Deserializer<'de>,
            {
                Deserialize::deserialize(deserializer)
            }

            fn visit_bool<E>(self, v: bool) -> std::result::Result<Value, E>
            where
                E: de::Error,
            {
                Ok(Value::Bool(v))
            }

            fn visit_i64<E>(self, v: i64) -> std::result::Result<Value, E>
            where
                E: de::Error,
            {
                Ok(Value::Integer(v))
            }

            fn visit_u64<E>(self, v: u64) -> std::result::Result<Value, E>
            where
                E: de::Error,
            {
                Ok(Value::Integer(v as i64))
            }

            fn visit_f64<E>(self, v: f64) -> std::result::Result<Value, E>
            where
                E: de::Error,
            {
                Ok(Value::Float(v))
            }

            fn visit_str<E>(self, v: &str) -> std::result::Result<Value, E>
            where
                E: de::Error,
            {
                Ok(Value::String(v.to_owned()))
            }

            fn visit_string<E>(self, v: String) -> std::result::Result<Value, E>
            where
                E: de::Error,
            {
                Ok(Value::String(v))
            }

            fn visit_seq<A>(self, mut seq: A) -> std::result::Result<Value, A::Error>
            where
                A: SeqAccess<'de>,
            {
                let mut items = Vec::new();
                while let Some(item) = seq.next_element()? {
                    items.push(item);
                }
                Ok(Value::List(items))
            }

            fn visit_map<A>(self, mut map: A) -> std::result::Result<Value, A::Error>
            where
                A: MapAccess<'de>,
            {
                let mut obj = HashMap::new();
                while let Some((key, value)) = map.next_entry()? {
                    obj.insert(key, value);
                }
                Ok(Value::Object(obj))
            }
        }

        deserializer.deserialize_any(ValueVisitor)
    }
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
}

// Implement Display trait instead of inherent to_string method
//
// For serialized Django-model dicts the Python-side serializer sets
// `"__str__": str(obj)` on every dict it produces (see
// `python/djust/serialization.py::_serialize_model_safely`). This
// matches Django's default template semantics: `{{ obj }}` in a
// Django template calls `str(obj)`, so a rendered FK like
// `{{ claim.claimant }}` produces the claimant's `__str__`, not a
// placeholder.
//
// Before the #968 fix the Rust renderer ignored the `__str__` key
// and emitted the literal `"[Object]"` for any dict, breaking the
// Django semantic for LiveView templates. The current
// implementation checks for a `"__str__"` entry first and renders
// its string value when present, falling back to `"[Object]"` only
// for dicts that weren't produced by the model serializer.
impl fmt::Display for Value {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            Value::Null => write!(f, ""),
            Value::Bool(b) => write!(f, "{b}"),
            Value::Integer(i) => write!(f, "{i}"),
            Value::Float(fl) => write!(f, "{fl}"),
            Value::String(s) => write!(f, "{s}"),
            Value::List(_) => write!(f, "[List]"),
            Value::Object(o) => match o.get("__str__") {
                Some(Value::String(s)) => write!(f, "{s}"),
                _ => write!(f, "[Object]"),
            },
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
            // For arbitrary Python objects (e.g. Django model instances), try to
            // extract public attributes from __dict__ so that template expressions
            // like `{{ obj.name }}` or `{{ obj.path }}` work without requiring
            // callers to manually convert to dicts.
            if let Ok(obj_dict) = ob.getattr("__dict__") {
                if let Ok(items) = obj_dict.extract::<HashMap<String, pyo3::Bound<'py, PyAny>>>() {
                    let mut map = HashMap::new();
                    for (k, v) in items {
                        // Skip private/dunder attrs and Django's internal _state
                        if k.starts_with('_') {
                            continue;
                        }
                        if let Ok(val) = v.extract::<Value>() {
                            map.insert(k, val);
                        }
                    }
                    if !map.is_empty() {
                        return Ok(Value::Object(map));
                    }
                }
            }
            Ok(Value::String(ob.str()?.to_string()))
        }
    }
}

/// Convert Value to Python object using the new IntoPyObject trait.
impl<'py> IntoPyObject<'py> for Value {
    type Target = PyAny;
    type Output = Bound<'py, Self::Target>;
    type Error = PyErr;

    fn into_pyobject(self, py: Python<'py>) -> std::result::Result<Self::Output, Self::Error> {
        match self {
            Value::Null => Ok(py.None().into_bound(py)),
            Value::Bool(b) => Ok(b.into_pyobject(py)?.to_owned().into_any()),
            Value::Integer(i) => Ok(i.into_pyobject(py)?.to_owned().into_any()),
            Value::Float(f) => Ok(f.into_pyobject(py)?.to_owned().into_any()),
            Value::String(s) => Ok(s.into_pyobject(py)?.to_owned().into_any()),
            Value::List(l) => {
                let py_list = PyList::empty(py);
                for item in l {
                    py_list.append(item.into_pyobject(py)?)?;
                }
                Ok(py_list.into_any())
            }
            Value::Object(o) => {
                let py_dict = PyDict::new(py);
                for (k, v) in o {
                    py_dict.set_item(k, v.into_pyobject(py)?)?;
                }
                Ok(py_dict.into_any())
            }
        }
    }
}

/// Convert &Value to Python object (clones the value).
impl<'py> IntoPyObject<'py> for &Value {
    type Target = PyAny;
    type Output = Bound<'py, Self::Target>;
    type Error = PyErr;

    fn into_pyobject(self, py: Python<'py>) -> std::result::Result<Self::Output, Self::Error> {
        self.clone().into_pyobject(py)
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

    /// #968 — `Value::Object` with a `"__str__"` key renders that
    /// string, matching Django's default `{{ obj }}` semantics.
    /// Serialized Django-model dicts carry `"__str__": str(obj)` from
    /// `python/djust/serialization.py::_serialize_model_safely`; the
    /// Rust Display impl previously dropped it and emitted `[Object]`.
    #[test]
    fn test_display_object_with_str_key() {
        let mut map = HashMap::new();
        map.insert("id".to_string(), Value::Integer(1));
        map.insert(
            "__str__".to_string(),
            Value::String("<Claim: 2026PD000075>".to_string()),
        );
        let obj = Value::Object(map);
        assert_eq!(obj.to_string(), "<Claim: 2026PD000075>");
    }

    /// Fallback: plain dicts without a `"__str__"` key keep rendering
    /// as `"[Object]"` — non-model data (e.g. a context dict passed
    /// directly from user code) was never meant to hit `__str__`
    /// semantics.
    #[test]
    fn test_display_object_without_str_key() {
        let mut map = HashMap::new();
        map.insert("a".to_string(), Value::Integer(1));
        map.insert("b".to_string(), Value::Integer(2));
        let obj = Value::Object(map);
        assert_eq!(obj.to_string(), "[Object]");
    }

    /// Edge: `"__str__"` key present but not a `String` (e.g. an
    /// upstream bug produces `"__str__": null`). Fall back to
    /// `"[Object]"` rather than emit `null` or crash.
    #[test]
    fn test_display_object_str_key_non_string_falls_back() {
        let mut map = HashMap::new();
        map.insert("__str__".to_string(), Value::Null);
        let obj = Value::Object(map);
        assert_eq!(obj.to_string(), "[Object]");
    }

    /// Empty string `"__str__"` is still a valid override — Django
    /// template would render an empty string if `str(obj) == ""`,
    /// and the Rust engine must match.
    #[test]
    fn test_display_object_empty_str_key() {
        let mut map = HashMap::new();
        map.insert("__str__".to_string(), Value::String("".to_string()));
        let obj = Value::Object(map);
        assert_eq!(obj.to_string(), "");
    }

    /// Regression-lock: bare `[List]` fallback for lists unchanged.
    #[test]
    fn test_display_list_unchanged() {
        let list = Value::List(vec![Value::Integer(1), Value::Integer(2)]);
        assert_eq!(list.to_string(), "[List]");
    }
}
