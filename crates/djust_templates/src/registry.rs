//! Tag Handler Registry for Django-compatible template tags
//!
//! This module provides a registry for custom template tag handlers that can be
//! implemented in Python. This enables Django-specific tags like `{% url %}` and
//! `{% static %}` to be handled by Python callbacks while keeping built-in tags
//! (if, for, block) as fast native Rust implementations.
//!
//! # Architecture
//!
//! ```text
//! Template: {% url 'post' post.slug %}
//!     |-> Rust parser encounters "url" tag
//!     |-> Not in built-in match -> check Python registry
//!     |-> Found UrlTagHandler -> create Node::CustomTag
//!     |-> Rust renderer hits Node::CustomTag
//!     |-> Acquires GIL, calls Python handler with args + context
//!     |-> Handler calls Django's reverse()
//!     |-> Returns "/posts/my-slug/"
//! Final HTML with correct URL
//! ```
//!
//! # Performance
//!
//! - Built-in tags: Zero overhead (native Rust match)
//! - Custom tags: ~15-50µs per call (GIL acquisition + Python callback)

use once_cell::sync::Lazy;
use pyo3::prelude::*;
use std::collections::HashMap;
use std::sync::Mutex;

/// Global registry mapping tag names to Python handler objects.
///
/// Thread-safe via Mutex. Handlers must implement a `render(args, context)` method
/// that returns a string.
static TAG_HANDLERS: Lazy<Mutex<HashMap<String, Py<PyAny>>>> =
    Lazy::new(|| Mutex::new(HashMap::new()));

/// Register a Python tag handler for a custom template tag.
///
/// The handler must be a Python object with a `render(self, args, context)` method:
/// - `args`: List of string arguments from the template tag
/// - `context`: Dictionary of template context variables
/// - Returns: String to insert in the rendered output
///
/// # Arguments
///
/// * `name` - Tag name (e.g., "url", "static")
/// * `handler` - Python handler object with `render` method
///
/// # Example
///
/// ```python
/// from djust._rust import register_tag_handler
///
/// class UrlTagHandler:
///     def render(self, args, context):
///         url_name = args[0].strip("'\"")
///         return reverse(url_name)
///
/// register_tag_handler("url", UrlTagHandler())
/// ```
#[pyfunction]
pub fn register_tag_handler(py: Python<'_>, name: String, handler: Py<PyAny>) -> PyResult<()> {
    // Verify handler has render method
    let handler_ref = handler.bind(py);
    if !handler_ref.hasattr("render")? {
        return Err(PyErr::new::<pyo3::exceptions::PyTypeError, _>(
            "Handler must have a 'render' method",
        ));
    }

    let mut registry = TAG_HANDLERS.lock().map_err(|e| {
        PyErr::new::<pyo3::exceptions::PyRuntimeError, _>(format!("Registry lock error: {e}"))
    })?;

    registry.insert(name, handler);
    Ok(())
}

/// Unregister a tag handler.
///
/// Returns true if a handler was removed, false if no handler existed for the name.
#[pyfunction]
pub fn unregister_tag_handler(name: &str) -> PyResult<bool> {
    let mut registry = TAG_HANDLERS.lock().map_err(|e| {
        PyErr::new::<pyo3::exceptions::PyRuntimeError, _>(format!("Registry lock error: {e}"))
    })?;

    Ok(registry.remove(name).is_some())
}

/// Check if a handler is registered for a tag name.
#[pyfunction]
pub fn has_tag_handler(name: &str) -> PyResult<bool> {
    let registry = TAG_HANDLERS.lock().map_err(|e| {
        PyErr::new::<pyo3::exceptions::PyRuntimeError, _>(format!("Registry lock error: {e}"))
    })?;

    Ok(registry.contains_key(name))
}

/// Get a list of all registered tag names.
#[pyfunction]
pub fn get_registered_tags() -> PyResult<Vec<String>> {
    let registry = TAG_HANDLERS.lock().map_err(|e| {
        PyErr::new::<pyo3::exceptions::PyRuntimeError, _>(format!("Registry lock error: {e}"))
    })?;

    Ok(registry.keys().cloned().collect())
}

/// Clear all registered handlers (primarily for testing).
#[pyfunction]
pub fn clear_tag_handlers() -> PyResult<()> {
    let mut registry = TAG_HANDLERS.lock().map_err(|e| {
        PyErr::new::<pyo3::exceptions::PyRuntimeError, _>(format!("Registry lock error: {e}"))
    })?;

    registry.clear();
    Ok(())
}

// ============================================================================
// Internal Rust API (for use by parser and renderer)
// ============================================================================

/// Check if a handler exists for the given tag name (internal Rust API).
///
/// This is used by the parser to decide whether to create a CustomTag node.
pub fn handler_exists(name: &str) -> bool {
    TAG_HANDLERS
        .lock()
        .map(|registry| registry.contains_key(name))
        .unwrap_or(false)
}

/// Call a registered Python handler with args and context (internal Rust API).
///
/// This is used by the renderer to execute custom tag handlers.
///
/// # Arguments
///
/// * `name` - Tag name
/// * `args` - Arguments from the template tag as strings
/// * `context` - Template context as a HashMap (will be converted to Python dict)
///
/// # Returns
///
/// The rendered string from the handler, or an error if:
/// - No handler is registered for the tag
/// - Handler doesn't have a `render` method
/// - Handler's `render` method raises an exception
/// - Handler's `render` method doesn't return a string
pub fn call_handler(
    name: &str,
    args: &[String],
    context: &HashMap<String, djust_core::Value>,
) -> Result<String, String> {
    // Get handler from registry
    let handler = {
        let registry = TAG_HANDLERS
            .lock()
            .map_err(|e| format!("Registry lock error: {e}"))?;

        // Clone the Py<PyAny> using Python::with_gil
        let handler_ref = registry
            .get(name)
            .ok_or_else(|| format!("No handler registered for tag: {name}"))?;

        Python::with_gil(|py| handler_ref.clone_ref(py))
    };

    // Acquire GIL and call Python handler
    Python::with_gil(|py| {
        use pyo3::IntoPyObject;

        // Convert args to Python list
        let py_args = pyo3::types::PyList::new(py, args)
            .map_err(|e| format!("Failed to create args list: {e}"))?;

        // Convert context to Python dict
        let py_context = pyo3::types::PyDict::new(py);
        for (key, value) in context {
            let py_value = value
                .clone()
                .into_pyobject(py)
                .map_err(|e| format!("Failed to convert value for key '{key}': {e}"))?;
            py_context
                .set_item(key, py_value)
                .map_err(|e| format!("Failed to set context key '{key}': {e}"))?;
        }

        // Call handler.render(args, context)
        let handler_ref = handler.bind(py);
        let result = handler_ref
            .call_method1("render", (py_args, py_context))
            .map_err(|e| {
                // Extract Python exception details
                let traceback = e
                    .traceback(py)
                    .map(|tb| tb.format().unwrap_or_default())
                    .unwrap_or_default();
                format!(
                    "Handler '{}' raised exception: {}\n{}",
                    name,
                    e.value(py),
                    traceback
                )
            })?;

        // Extract string result
        result
            .extract::<String>()
            .map_err(|_| format!("Handler '{name}' render() must return a string"))
    })
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_handler_exists_empty() {
        // Clear any existing handlers
        clear_tag_handlers().unwrap();

        assert!(!handler_exists("url"));
        assert!(!handler_exists("static"));
    }

    #[test]
    fn test_get_registered_tags_empty() {
        clear_tag_handlers().unwrap();

        let tags = get_registered_tags().unwrap();
        assert!(tags.is_empty());
    }
}
