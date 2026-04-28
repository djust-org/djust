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

/// Block handler entry: (end_tag_name, Python handler object).
type BlockHandlerEntry = (String, Py<PyAny>);

/// Global registry for block tag handlers (tags with children).
///
/// Maps opening tag name -> (end_tag_name, handler).
/// Handlers must implement a `render(args, content, context)` method:
/// - `args`: list of strings from the opening tag
/// - `content`: pre-rendered HTML string of the block body
/// - `context`: dict of template context variables
static BLOCK_TAG_HANDLERS: Lazy<Mutex<HashMap<String, BlockHandlerEntry>>> =
    Lazy::new(|| Mutex::new(HashMap::new()));

/// Global registry for assign tag handlers (context-mutating tags).
///
/// Handlers implement `render(args, context) -> dict[str, Any]`. The
/// returned dict is merged into the template context for siblings
/// following the tag in the same render iteration.
static ASSIGN_TAG_HANDLERS: Lazy<Mutex<HashMap<String, Py<PyAny>>>> =
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
// Block Tag Handler API (Python-callable)
// ============================================================================

/// Register a block tag handler for a custom template block tag.
///
/// Block tags wrap content like `{% modal %}...{% endmodal %}`.
/// The handler receives the pre-rendered HTML of the block body as `content`.
///
/// The handler must be a Python object with a `render(self, args, content, context)` method:
/// - `args`: List of string arguments from the opening tag
/// - `content`: Pre-rendered HTML string of the block body
/// - `context`: Dictionary of template context variables
/// - Returns: String to insert in the rendered output
///
/// # Arguments
///
/// * `name` - Opening tag name (e.g., "modal", "card")
/// * `end_tag` - Closing tag name (e.g., "endmodal", "endcard")
/// * `handler` - Python handler object with `render` method
///
/// # Known constraints
///
/// * **No parent-tag propagation** (issue #804). When a block tag's
///   children include another block tag, the inner tag's output is
///   rendered to a string and embedded in `content`. The inner
///   handler is not informed that it is nested inside a parent
///   handler. Handlers that need nesting awareness should stash a
///   hint on the template context in the outer handler and read it
///   back in the inner handler — automatic propagation is not yet
///   implemented.
///
/// * **No loader access from handlers** (issue #803). The
///   `FilesystemTemplateLoader` is not currently exposed through the
///   Rust-to-Python bridge, so block handlers cannot call
///   `{% render_template name=... %}`-style template loads. Workaround:
///   pre-render child templates in the view and pass the result via
///   context.
#[pyfunction]
pub fn register_block_tag_handler(
    py: Python<'_>,
    name: String,
    end_tag: String,
    handler: Py<PyAny>,
) -> PyResult<()> {
    let handler_ref = handler.bind(py);
    if !handler_ref.hasattr("render")? {
        return Err(PyErr::new::<pyo3::exceptions::PyTypeError, _>(
            "Block tag handler must have a 'render' method",
        ));
    }

    let mut registry = BLOCK_TAG_HANDLERS.lock().map_err(|e| {
        PyErr::new::<pyo3::exceptions::PyRuntimeError, _>(format!("Registry lock error: {e}"))
    })?;

    registry.insert(name, (end_tag, handler));
    Ok(())
}

/// Unregister a block tag handler.
#[pyfunction]
pub fn unregister_block_tag_handler(name: &str) -> PyResult<bool> {
    let mut registry = BLOCK_TAG_HANDLERS.lock().map_err(|e| {
        PyErr::new::<pyo3::exceptions::PyRuntimeError, _>(format!("Registry lock error: {e}"))
    })?;

    Ok(registry.remove(name).is_some())
}

/// Check if a block tag handler is registered.
#[pyfunction]
pub fn has_block_tag_handler(name: &str) -> PyResult<bool> {
    let registry = BLOCK_TAG_HANDLERS.lock().map_err(|e| {
        PyErr::new::<pyo3::exceptions::PyRuntimeError, _>(format!("Registry lock error: {e}"))
    })?;

    Ok(registry.contains_key(name))
}

/// Clear all block tag handlers (primarily for testing).
#[pyfunction]
pub fn clear_block_tag_handlers() -> PyResult<()> {
    let mut registry = BLOCK_TAG_HANDLERS.lock().map_err(|e| {
        PyErr::new::<pyo3::exceptions::PyRuntimeError, _>(format!("Registry lock error: {e}"))
    })?;

    registry.clear();
    Ok(())
}

// ============================================================================
// Internal Rust API (for use by parser and renderer)
// ============================================================================

/// Check if a block tag handler exists and return the end tag name (internal Rust API).
///
/// Returns `Some(end_tag_name)` if a block handler is registered, `None` otherwise.
pub fn block_handler_exists(name: &str) -> Option<String> {
    BLOCK_TAG_HANDLERS
        .lock()
        .map(|registry| registry.get(name).map(|(end_tag, _)| end_tag.clone()))
        .unwrap_or(None)
}

/// Call a registered Python block handler with args, content, and context.
///
/// The handler's `render(args, content, context)` method is called and the
/// returned string is inserted into the rendered output.
///
/// Back-compat shim around [`call_block_handler_with_py_sidecar`] —
/// equivalent to passing `None` for the raw Python sidecar.
pub fn call_block_handler(
    name: &str,
    args: &[String],
    content: &str,
    context: &HashMap<String, djust_core::Value>,
) -> Result<String, String> {
    call_block_handler_with_py_sidecar(name, args, content, context, None)
}

/// Variant of [`call_block_handler`] that additionally injects raw
/// Python objects from the [`Context::raw_py_objects`] sidecar into
/// the handler's ``context`` dict.
///
/// Mirrors [`call_handler_with_py_sidecar`] (extended in PR #1166)
/// for `Node::CustomTag`. Block handlers (``modal``, ``card`` …)
/// that need access to Python-only objects in the parent's render
/// context (notably ``request`` / ``view``) can read those keys from
/// the dict directly. Sidecar values overwrite same-named JSON keys
/// so a Python model instance wins over a normalized dict snapshot.
///
/// Existing block handlers that ignore the extra keys are unaffected.
pub fn call_block_handler_with_py_sidecar(
    name: &str,
    args: &[String],
    content: &str,
    context: &HashMap<String, djust_core::Value>,
    raw_py_objects: Option<&HashMap<String, pyo3::PyObject>>,
) -> Result<String, String> {
    let handler = {
        let registry = BLOCK_TAG_HANDLERS
            .lock()
            .map_err(|e| format!("Registry lock error: {e}"))?;

        let (_, handler_ref) = registry
            .get(name)
            .ok_or_else(|| format!("No block handler registered for tag: {name}"))?;

        Python::with_gil(|py| handler_ref.clone_ref(py))
    };

    Python::with_gil(|py| {
        use pyo3::IntoPyObject;

        let py_args = pyo3::types::PyList::new(py, args)
            .map_err(|e| format!("Failed to create args list: {e}"))?;

        let py_content = content
            .into_pyobject(py)
            .map_err(|e| format!("Failed to convert content: {e}"))?;

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

        // Inject raw Python sidecar objects (e.g. ``request``, ``view``)
        // so block handlers needing full Python context can reach them.
        // Overwrites same-named JSON entries — the Python object is the
        // source of truth.
        if let Some(raw) = raw_py_objects {
            for (key, obj) in raw {
                py_context
                    .set_item(key, obj.bind(py))
                    .map_err(|e| format!("Failed to set raw context key '{key}': {e}"))?;
            }
        }

        let handler_ref = handler.bind(py);
        let result = handler_ref
            .call_method1("render", (py_args, py_content, py_context))
            .map_err(|e| {
                let traceback = e
                    .traceback(py)
                    .map(|tb| tb.format().unwrap_or_default())
                    .unwrap_or_default();
                format!(
                    "Block handler '{}' raised exception: {}\n{}",
                    name,
                    e.value(py),
                    traceback
                )
            })?;

        result
            .extract::<String>()
            .map_err(|_| format!("Block handler '{name}' render() must return a string"))
    })
}

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
    call_handler_with_py_sidecar(name, args, context, None)
}

/// Variant of [`call_handler`] that additionally injects raw Python
/// objects from the [`Context::raw_py_objects`] sidecar into the
/// handler's ``context`` dict.
///
/// Existing tag handlers (``url``, ``static``, ``dj_flash`` …) only
/// look at JSON-friendly context keys, so the additional Python
/// objects are inert noise to them. Handlers that *do* need access to
/// Python-only context (e.g. ``live_render``, which needs the parent
/// ``view`` and the ``request`` object to delegate to the Django
/// template tag) read those keys from the dict directly.
///
/// Sidecar values overwrite same-named JSON keys so that a Python
/// model instance wins over a normalized dict snapshot — the Python
/// handler nearly always wants the live object, not the projection.
pub fn call_handler_with_py_sidecar(
    name: &str,
    args: &[String],
    context: &HashMap<String, djust_core::Value>,
    raw_py_objects: Option<&HashMap<String, pyo3::PyObject>>,
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

        // Inject raw Python sidecar objects (e.g. ``request``, ``view``)
        // so handlers that need full Python context (notably the
        // ``live_render`` lazy=True path) can reach them. Overwrites
        // same-named JSON entries — the Python object is the source of
        // truth for downstream handlers.
        if let Some(raw) = raw_py_objects {
            for (key, obj) in raw {
                py_context
                    .set_item(key, obj.bind(py))
                    .map_err(|e| format!("Failed to set raw context key '{key}': {e}"))?;
            }
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

// ============================================================================
// Assign Tag Handler API (context-mutating tags)
// ============================================================================

/// Register a Python assign-tag handler for a custom template tag.
///
/// Assign tags mutate the template context rather than emitting HTML.
/// Example: `{% assign_slot user_card %}` — the handler returns a
/// dict whose keys become context variables visible to subsequent
/// sibling nodes in the template.
///
/// The handler must be a Python object with a `render(args, context)`
/// method that returns a `dict[str, Any]`. Non-dict return values
/// are treated as an empty dict (no-op) and logged by the caller.
///
/// # Arguments
///
/// * `name` - Tag name (e.g., "assign_slot")
/// * `handler` - Python handler object with `render` method
#[pyfunction]
pub fn register_assign_tag_handler(
    py: Python<'_>,
    name: String,
    handler: Py<PyAny>,
) -> PyResult<()> {
    let handler_ref = handler.bind(py);
    if !handler_ref.hasattr("render")? {
        return Err(PyErr::new::<pyo3::exceptions::PyTypeError, _>(
            "Assign tag handler must have a 'render' method",
        ));
    }

    let mut registry = ASSIGN_TAG_HANDLERS.lock().map_err(|e| {
        PyErr::new::<pyo3::exceptions::PyRuntimeError, _>(format!("Registry lock error: {e}"))
    })?;

    registry.insert(name, handler);
    Ok(())
}

/// Unregister an assign tag handler.
#[pyfunction]
pub fn unregister_assign_tag_handler(name: &str) -> PyResult<bool> {
    let mut registry = ASSIGN_TAG_HANDLERS.lock().map_err(|e| {
        PyErr::new::<pyo3::exceptions::PyRuntimeError, _>(format!("Registry lock error: {e}"))
    })?;
    Ok(registry.remove(name).is_some())
}

/// Check if an assign tag handler is registered.
#[pyfunction]
pub fn has_assign_tag_handler(name: &str) -> PyResult<bool> {
    let registry = ASSIGN_TAG_HANDLERS.lock().map_err(|e| {
        PyErr::new::<pyo3::exceptions::PyRuntimeError, _>(format!("Registry lock error: {e}"))
    })?;
    Ok(registry.contains_key(name))
}

/// Clear all registered assign tag handlers (primarily for testing).
#[pyfunction]
pub fn clear_assign_tag_handlers() -> PyResult<()> {
    let mut registry = ASSIGN_TAG_HANDLERS.lock().map_err(|e| {
        PyErr::new::<pyo3::exceptions::PyRuntimeError, _>(format!("Registry lock error: {e}"))
    })?;
    registry.clear();
    Ok(())
}

/// Internal Rust API — does an assign tag handler exist for this name?
pub fn assign_handler_exists(name: &str) -> bool {
    ASSIGN_TAG_HANDLERS
        .lock()
        .map(|registry| registry.contains_key(name))
        .unwrap_or(false)
}

/// Call a registered Python assign-tag handler with args and context.
///
/// Returns a map of context updates to merge into the surrounding
/// render context. Error strings bubble up through
/// [`crate::renderer`] as `DjangoRustError::TemplateError`.
///
/// Back-compat shim around [`call_assign_handler_with_py_sidecar`] —
/// equivalent to passing `None` for the raw Python sidecar.
pub fn call_assign_handler(
    name: &str,
    args: &[String],
    context: &HashMap<String, djust_core::Value>,
) -> Result<HashMap<String, djust_core::Value>, String> {
    call_assign_handler_with_py_sidecar(name, args, context, None)
}

/// Variant of [`call_assign_handler`] that additionally injects raw
/// Python objects from the [`Context::raw_py_objects`] sidecar into
/// the handler's ``context`` dict.
///
/// Mirrors [`call_handler_with_py_sidecar`] (extended in PR #1166)
/// for `Node::CustomTag`. Assign handlers needing access to
/// Python-only context (e.g. ``request`` / ``view``) can read those
/// keys from the dict directly. Sidecar values overwrite same-named
/// JSON keys.
///
/// Existing assign handlers that ignore the extra keys are
/// unaffected.
pub fn call_assign_handler_with_py_sidecar(
    name: &str,
    args: &[String],
    context: &HashMap<String, djust_core::Value>,
    raw_py_objects: Option<&HashMap<String, pyo3::PyObject>>,
) -> Result<HashMap<String, djust_core::Value>, String> {
    let handler = {
        let registry = ASSIGN_TAG_HANDLERS
            .lock()
            .map_err(|e| format!("Registry lock error: {e}"))?;
        let handler_ref = registry
            .get(name)
            .ok_or_else(|| format!("No assign handler registered for tag: {name}"))?;
        Python::with_gil(|py| handler_ref.clone_ref(py))
    };

    Python::with_gil(|py| {
        use pyo3::IntoPyObject;

        let py_args = pyo3::types::PyList::new(py, args)
            .map_err(|e| format!("Failed to create args list: {e}"))?;

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

        // Inject raw Python sidecar objects (e.g. ``request``, ``view``)
        // so assign handlers needing full Python context can reach them.
        // Overwrites same-named JSON entries.
        if let Some(raw) = raw_py_objects {
            for (key, obj) in raw {
                py_context
                    .set_item(key, obj.bind(py))
                    .map_err(|e| format!("Failed to set raw context key '{key}': {e}"))?;
            }
        }

        let handler_ref = handler.bind(py);
        let result = handler_ref
            .call_method1("render", (py_args, py_context))
            .map_err(|e| {
                let traceback = e
                    .traceback(py)
                    .map(|tb| tb.format().unwrap_or_default())
                    .unwrap_or_default();
                format!(
                    "Assign handler '{}' raised exception: {}\n{}",
                    name,
                    e.value(py),
                    traceback
                )
            })?;

        // Handlers may legitimately return None or something dict-like
        // but not a dict. Treat any extraction failure as an empty
        // merge (no-op) so a misbehaving handler can't crash the
        // whole render. Warn once per handler when the coercion fails
        // so the developer sees the silent-empty pattern rather than
        // hunting for why their assign tag didn't set anything (#805).
        match result.extract::<HashMap<String, djust_core::Value>>() {
            Ok(map) => Ok(map),
            Err(err) => {
                // None is the documented "no context updates" sentinel;
                // don't warn on it — that's the deliberate "I did work
                // but have nothing to merge" path.
                let is_none = result.is_none();
                if !is_none {
                    let type_name = result
                        .get_type()
                        .qualname()
                        .map(|s| s.to_string())
                        .unwrap_or_else(|_| "<unknown>".to_string());
                    eprintln!(
                        "[djust] assign tag handler '{}' returned a non-dict value \
                         (type = {}); treating as empty merge. \
                         Handlers must return a dict[str, Any] mapping of context updates, \
                         or None. Coercion error: {}",
                        name, type_name, err
                    );
                }
                Ok(HashMap::new())
            }
        }
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
