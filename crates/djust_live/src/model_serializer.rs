//! Fast model serializer for Django models
//!
//! This module provides a high-performance serializer that receives pre-extracted
//! field data from Python and converts it to JSON. The key insight is that Python
//! extracts fields safely (checking prefetch caches) while Rust does the fast
//! HashMap construction and JSON serialization.

use pyo3::prelude::*;
use pyo3::types::{PyDict, PyList, PyTuple};
use serde_json::{Map, Value as JsonValue};

/// Fast model serializer that receives pre-extracted field data from Python.
///
/// Python extracts: {field_name: value, ...} with prefetch metadata
/// Rust does: fast HashMap construction and JSON serialization
///
/// This is 5-10x faster than Python's json.dumps() with DjangoJSONEncoder
/// because Rust handles the JSON construction and string building.
///
/// # Arguments
///
/// * `models_data` - List of Python dicts containing pre-extracted field data
///
/// # Returns
///
/// JSON string containing serialized models
///
/// # Example
///
/// ```python
/// from djust._rust import serialize_models_fast
///
/// # Extract field data safely in Python (checking prefetch caches)
/// models_data = [
///     {"id": 1, "name": "John", "email": "john@example.com"},
///     {"id": 2, "name": "Jane", "email": "jane@example.com"},
/// ]
///
/// json_str = serialize_models_fast(models_data)
/// ```
#[pyfunction]
pub fn serialize_models_fast(py: Python<'_>, models_data: &Bound<'_, PyList>) -> PyResult<String> {
    let mut results = Vec::with_capacity(models_data.len());

    for item in models_data.iter() {
        // Each item should be a dict of field data
        if let Ok(dict) = item.downcast::<PyDict>() {
            let serialized = python_dict_to_json(py, dict)?;
            results.push(serialized);
        } else {
            // Skip non-dict items
            continue;
        }
    }

    serde_json::to_string(&results)
        .map_err(|e| PyErr::new::<pyo3::exceptions::PyValueError, _>(e.to_string()))
}

/// Convert a Python dict to serde_json::Value
fn python_dict_to_json(py: Python<'_>, dict: &Bound<'_, PyDict>) -> PyResult<JsonValue> {
    let mut map = Map::new();

    for (key, value) in dict.iter() {
        let key_str: String = key.extract()?;
        let json_val = python_to_json(py, &value)?;
        map.insert(key_str, json_val);
    }

    Ok(JsonValue::Object(map))
}

/// Convert Python value to serde_json::Value
///
/// Handles all common Python types efficiently:
/// - None -> null
/// - bool -> true/false
/// - int -> number
/// - float -> number
/// - str -> string
/// - dict -> object (recursive)
/// - list/tuple -> array (recursive)
/// - Other -> string representation
fn python_to_json(py: Python<'_>, obj: &Bound<'_, PyAny>) -> PyResult<JsonValue> {
    // Fast path: None
    if obj.is_none() {
        return Ok(JsonValue::Null);
    }

    // Handle bool (MUST be before int, since bool is subclass of int in Python!)
    if let Ok(b) = obj.extract::<bool>() {
        return Ok(JsonValue::Bool(b));
    }

    // Handle int
    if let Ok(i) = obj.extract::<i64>() {
        return Ok(JsonValue::Number(i.into()));
    }

    // Handle float
    if let Ok(f) = obj.extract::<f64>() {
        if let Some(num) = serde_json::Number::from_f64(f) {
            return Ok(JsonValue::Number(num));
        } else {
            // Handle NaN/Infinity by converting to null
            return Ok(JsonValue::Null);
        }
    }

    // Handle string
    if let Ok(s) = obj.extract::<String>() {
        return Ok(JsonValue::String(s));
    }

    // Handle dict (nested object)
    if let Ok(dict) = obj.downcast::<PyDict>() {
        return python_dict_to_json(py, dict);
    }

    // Handle list
    if let Ok(list) = obj.downcast::<PyList>() {
        let items: Vec<JsonValue> = list
            .iter()
            .map(|item| python_to_json(py, &item))
            .collect::<PyResult<_>>()?;
        return Ok(JsonValue::Array(items));
    }

    // Handle tuple (same as list)
    if let Ok(tuple) = obj.downcast::<PyTuple>() {
        let items: Vec<JsonValue> = tuple
            .iter()
            .map(|item| python_to_json(py, &item))
            .collect::<PyResult<_>>()?;
        return Ok(JsonValue::Array(items));
    }

    // Fallback: string representation
    Ok(JsonValue::String(obj.str()?.to_string()))
}

/// Serialize a list of model dicts to Python list of dicts
///
/// This variant returns a Python list instead of JSON string, which is
/// more efficient when the result will be used in Python templates.
///
/// # Arguments
///
/// * `models_data` - List of Python dicts containing pre-extracted field data
///
/// # Returns
///
/// Python list of processed dicts (with type normalization)
#[pyfunction]
pub fn serialize_models_to_list(
    py: Python<'_>,
    models_data: &Bound<'_, PyList>,
) -> PyResult<Py<PyList>> {
    let result_list = PyList::empty(py);

    for item in models_data.iter() {
        // Pass through dicts after normalization
        if let Ok(dict) = item.downcast::<PyDict>() {
            let normalized = normalize_dict(py, dict)?;
            result_list.append(normalized)?;
        }
    }

    Ok(result_list.into())
}

/// Normalize a Python dict (convert Django types to JSON-compatible types)
fn normalize_dict(py: Python<'_>, dict: &Bound<'_, PyDict>) -> PyResult<Py<PyDict>> {
    let result = PyDict::new(py);

    for (key, value) in dict.iter() {
        let key_str: String = key.extract()?;
        let normalized_value = normalize_value(py, &value)?;
        result.set_item(key_str, normalized_value)?;
    }

    Ok(result.into())
}

/// Normalize a Python value to JSON-compatible form
fn normalize_value(py: Python<'_>, value: &Bound<'_, PyAny>) -> PyResult<PyObject> {
    // Fast path: common primitives pass through as-is
    if value.is_none() {
        return Ok(py.None());
    }

    // Check if it's a simple type that needs no conversion
    if value.is_instance_of::<pyo3::types::PyBool>()
        || value.is_instance_of::<pyo3::types::PyInt>()
        || value.is_instance_of::<pyo3::types::PyFloat>()
        || value.is_instance_of::<pyo3::types::PyString>()
    {
        return Ok(value.clone().unbind());
    }

    // Handle nested dict
    if let Ok(dict) = value.downcast::<PyDict>() {
        return normalize_dict(py, dict).map(|d| d.into_any());
    }

    // Handle list
    if let Ok(list) = value.downcast::<PyList>() {
        let result_list = PyList::empty(py);
        for item in list.try_iter()? {
            let normalized = normalize_value(py, &item?)?;
            result_list.append(normalized)?;
        }
        return Ok(result_list.unbind().into());
    }

    // Handle tuple (convert to list for JSON compatibility)
    if let Ok(tuple) = value.downcast::<PyTuple>() {
        let result_list = PyList::empty(py);
        for item in tuple.iter() {
            let normalized = normalize_value(py, &item)?;
            result_list.append(normalized)?;
        }
        return Ok(result_list.unbind().into());
    }

    // For other types, convert to string
    Ok(value.str()?.unbind().into())
}
