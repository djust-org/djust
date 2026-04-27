//! Custom filter registry for project-defined Django ``@register.filter`` callables.
//!
//! Mirrors the design of [`crate::registry`] (which dispatches custom *tags*),
//! but for filters. Built-in Django filters live as native-Rust matches in
//! [`crate::filters`]; project-level custom filters that come from
//! ``@register.filter`` in a Django app's ``templatetags/`` package are
//! registered here at engine bootstrap time.
//!
//! # Lazy vs eager
//!
//! This implementation is **eager** — Python registers each filter callable
//! exactly once via [`register_custom_filter`], typically by walking
//! ``template.engines['django'].engine.template_libraries`` at import time.
//! At render time, [`apply_custom_filter`] performs a HashMap lookup
//! followed by a GIL acquire + Python call. The eager design matches the
//! existing tag-handler pattern in [`crate::registry`] and avoids a
//! per-render GIL acquisition for "is this a known filter name?" probes.
//!
//! Memory cost: one entry per project filter. ~50 bytes of `String` +
//! `Py<PyAny>` + `FilterMeta` per registration. Even projects with hundreds
//! of custom filters fit comfortably.
//!
//! # Filter signature
//!
//! Django filter callables accept ``(value, arg=None)`` and return a
//! string (or a SafeString when ``is_safe=True``). ``needs_autoescape=True``
//! filters additionally accept ``autoescape`` as a kwarg.
//!
//! - ``value`` — the filtered expression's current `Value`, converted to
//!   the appropriate Python type (str/int/float/bool/None/list/dict).
//! - ``arg`` — for one-argument filters, the resolved argument:
//!     - quoted literals (``"foo"``) are passed as ``str``,
//!     - bare identifiers are resolved against the template context. If
//!       the context resolves to a primitive, it's passed as that type;
//!       otherwise as the value's natural Python representation.
//! - return — the result. ``is_safe=True`` filters' results bypass
//!   auto-escape via [`is_custom_filter_safe`] consulted by the renderer.

use crate::Value;
use djust_core::Context;
use once_cell::sync::Lazy;
use pyo3::prelude::*;
use pyo3::types::PyDict;
use std::collections::HashMap;
use std::sync::Mutex;

/// Per-filter metadata mirroring Django's filter object attributes.
#[derive(Debug, Clone, Default)]
pub struct FilterMeta {
    /// ``filter.is_safe`` — when true, the renderer must NOT HTML-escape
    /// the filter's output. The Python callable is expected to return
    /// already-escaped content (e.g. via ``mark_safe``).
    pub is_safe: bool,
    /// ``filter.needs_autoescape`` — when true, the dispatcher passes
    /// ``autoescape=True`` as a kwarg so the filter can branch on the
    /// surrounding autoescape policy.
    pub needs_autoescape: bool,
}

struct FilterEntry {
    callable: Py<PyAny>,
    meta: FilterMeta,
}

/// Global registry mapping filter names to Python callables + metadata.
static FILTER_REGISTRY: Lazy<Mutex<HashMap<String, FilterEntry>>> =
    Lazy::new(|| Mutex::new(HashMap::new()));

/// Register a project-defined custom filter from Python.
///
/// # Arguments
///
/// * ``name`` — filter name as used in templates (``{{ x|name }}``).
/// * ``callable`` — Django filter callable (``(value, arg=None) -> str``).
/// * ``is_safe`` — Django filter's ``is_safe`` attribute (skip auto-escape).
/// * ``needs_autoescape`` — Django filter's ``needs_autoescape`` attribute
///   (pass ``autoescape=True`` as kwarg).
///
/// Re-registering an existing name overwrites — matching Django's behaviour
/// when a Library is re-imported.
#[pyfunction]
#[pyo3(signature = (name, callable, is_safe=false, needs_autoescape=false))]
pub fn register_custom_filter(
    name: String,
    callable: Py<PyAny>,
    is_safe: bool,
    needs_autoescape: bool,
) -> PyResult<()> {
    let mut registry = FILTER_REGISTRY.lock().map_err(|e| {
        PyErr::new::<pyo3::exceptions::PyRuntimeError, _>(format!("Filter registry lock: {e}"))
    })?;
    registry.insert(
        name,
        FilterEntry {
            callable,
            meta: FilterMeta {
                is_safe,
                needs_autoescape,
            },
        },
    );
    Ok(())
}

/// Unregister a custom filter (returns ``true`` if a filter was removed).
#[pyfunction]
pub fn unregister_custom_filter(name: &str) -> PyResult<bool> {
    let mut registry = FILTER_REGISTRY.lock().map_err(|e| {
        PyErr::new::<pyo3::exceptions::PyRuntimeError, _>(format!("Filter registry lock: {e}"))
    })?;
    Ok(registry.remove(name).is_some())
}

/// Check if a custom filter is registered (intended for tests + diagnostics).
#[pyfunction]
pub fn has_custom_filter(name: &str) -> PyResult<bool> {
    let registry = FILTER_REGISTRY.lock().map_err(|e| {
        PyErr::new::<pyo3::exceptions::PyRuntimeError, _>(format!("Filter registry lock: {e}"))
    })?;
    Ok(registry.contains_key(name))
}

/// Clear all registered custom filters (primarily for tests).
#[pyfunction]
pub fn clear_custom_filters() -> PyResult<()> {
    let mut registry = FILTER_REGISTRY.lock().map_err(|e| {
        PyErr::new::<pyo3::exceptions::PyRuntimeError, _>(format!("Filter registry lock: {e}"))
    })?;
    registry.clear();
    Ok(())
}

/// List all registered custom filter names (for diagnostics).
#[pyfunction]
pub fn get_registered_custom_filters() -> PyResult<Vec<String>> {
    let registry = FILTER_REGISTRY.lock().map_err(|e| {
        PyErr::new::<pyo3::exceptions::PyRuntimeError, _>(format!("Filter registry lock: {e}"))
    })?;
    Ok(registry.keys().cloned().collect())
}

// ============================================================================
// Internal Rust API (called from filters.rs / renderer.rs)
// ============================================================================

/// Returns ``true`` if a registered custom filter has ``is_safe=True``.
///
/// The renderer consults this alongside the hardcoded built-in
/// ``safe_output_filters`` list to decide whether to skip auto-escape.
pub fn is_custom_filter_safe(name: &str) -> bool {
    FILTER_REGISTRY
        .lock()
        .map(|reg| reg.get(name).map(|e| e.meta.is_safe).unwrap_or(false))
        .unwrap_or(false)
}

/// Returns ``true`` if any custom filter is registered for the given name.
pub fn custom_filter_exists(name: &str) -> bool {
    FILTER_REGISTRY
        .lock()
        .map(|reg| reg.contains_key(name))
        .unwrap_or(false)
}

/// Apply a custom filter callable to a value with an optional argument.
///
/// Called from [`crate::filters::apply_filter_with_context`] when the
/// built-in filter match falls through. Returns ``None`` if no custom
/// filter is registered for the name (so the caller can fall through to
/// the standard ``Unknown filter`` error).
///
/// Argument resolution: when ``arg`` is provided as a non-empty string
/// after ``strip_filter_arg_quotes``, this function inspects the original
/// arg string for surrounding quotes:
/// - quoted (``"foo"`` or ``'foo'``) — passed to Python as a literal string
///   (with quotes already stripped by the caller).
/// - bare identifier — resolved against ``context`` first; if a binding
///   exists, the resolved `Value` is passed. Otherwise the bare identifier
///   string itself is passed (mirroring Django's tolerant behaviour where
///   filters accept literal arg text when no binding matches).
///
/// This split is the same convention `crate::filters::apply_filter_with_context`
/// already uses for built-ins like ``date`` (literal format string) vs
/// callers passing context-resolved values.
pub fn apply_custom_filter(
    name: &str,
    value: &Value,
    arg: Option<&str>,
    context: Option<&Context>,
    arg_was_quoted: bool,
) -> Option<Result<Value, String>> {
    let (callable, meta) = {
        let registry = FILTER_REGISTRY.lock().ok()?;
        let entry = registry.get(name)?;
        // clone_ref under the GIL; meta is plain Copy-ish.
        let callable = Python::with_gil(|py| entry.callable.clone_ref(py));
        (callable, entry.meta.clone())
    };

    let result = Python::with_gil(|py| -> Result<Value, String> {
        use pyo3::IntoPyObject;

        let py_value = value
            .clone()
            .into_pyobject(py)
            .map_err(|e| format!("Failed to convert filter input value: {e}"))?;

        // Resolve the arg into a Python object. Quoted literals → string;
        // bare identifiers → context resolve, fall back to the raw string
        // when not found.
        let py_arg: Option<pyo3::Bound<'_, PyAny>> = match arg {
            None => None,
            Some(s) if arg_was_quoted => {
                // Quoted literal — pass as plain string.
                Some(
                    s.into_pyobject(py)
                        .map_err(|e| format!("Failed to convert filter arg: {e}"))?
                        .into_any(),
                )
            }
            Some(s) => {
                // Bare identifier — try context resolution first.
                if let Some(ctx) = context {
                    if let Some(resolved) = ctx.resolve(s) {
                        Some(
                            resolved
                                .into_pyobject(py)
                                .map_err(|e| format!("Failed to convert resolved filter arg: {e}"))?
                                .into_any(),
                        )
                    } else {
                        // No binding — pass the raw identifier as a string,
                        // matching Django's tolerant default.
                        Some(
                            s.into_pyobject(py)
                                .map_err(|e| format!("Failed to convert filter arg: {e}"))?
                                .into_any(),
                        )
                    }
                } else {
                    Some(
                        s.into_pyobject(py)
                            .map_err(|e| format!("Failed to convert filter arg: {e}"))?
                            .into_any(),
                    )
                }
            }
        };

        let callable_ref = callable.bind(py);

        // Build kwargs: needs_autoescape filters get ``autoescape=True``.
        let kwargs = if meta.needs_autoescape {
            let kw = PyDict::new(py);
            kw.set_item("autoescape", true)
                .map_err(|e| format!("Failed to set autoescape kwarg: {e}"))?;
            Some(kw)
        } else {
            None
        };

        let py_result = match (py_arg, kwargs) {
            (Some(arg_obj), Some(kw)) => callable_ref
                .call((py_value, arg_obj), Some(&kw))
                .map_err(|e| format_py_err(py, name, &e))?,
            (Some(arg_obj), None) => callable_ref
                .call1((py_value, arg_obj))
                .map_err(|e| format_py_err(py, name, &e))?,
            (None, Some(kw)) => callable_ref
                .call((py_value,), Some(&kw))
                .map_err(|e| format_py_err(py, name, &e))?,
            (None, None) => callable_ref
                .call1((py_value,))
                .map_err(|e| format_py_err(py, name, &e))?,
        };

        // Convert back to Value. Filters typically return strings or
        // SafeStrings; via ``FromPyObject for Value`` either becomes
        // ``Value::String``. Rare numeric/bool returns also extract.
        py_result
            .extract::<Value>()
            .map_err(|_| format!("Custom filter '{name}' returned a non-convertible value"))
    });

    Some(result)
}

fn format_py_err(py: Python<'_>, name: &str, err: &PyErr) -> String {
    let traceback = err
        .traceback(py)
        .map(|tb| tb.format().unwrap_or_default())
        .unwrap_or_default();
    format!(
        "Custom filter '{}' raised exception: {}\n{}",
        name,
        err.value(py),
        traceback
    )
}
