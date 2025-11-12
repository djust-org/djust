//! djust - Reactive server-side rendering for Django
//!
//! This is the main crate that ties together templates, virtual DOM, and
//! provides Python bindings for reactive server-side rendering.

// PyResult type annotations are required by PyO3 API
#![allow(clippy::useless_conversion)]
// Parameter only used in recursion for Python value conversion
#![allow(clippy::only_used_in_recursion)]

use dashmap::DashMap;
use djust_core::{Context, Value};
use djust_templates::Template;
use djust_vdom::{diff, parse_html, VNode};
use once_cell::sync::Lazy;
use pyo3::prelude::*;
use pyo3::types::{PyBytes, PyDict, PyList};
use std::collections::HashMap;
use std::path::PathBuf;
use std::sync::Arc;

/// Global template cache - parse once, reuse for all sessions
/// Using Arc<Template> for cheap cloning across threads
static TEMPLATE_CACHE: Lazy<DashMap<String, Arc<Template>>> = Lazy::new(DashMap::new);

/// A LiveView component that manages state and rendering (Rust backend)
#[pyclass(name = "RustLiveView")]
pub struct RustLiveViewBackend {
    template_source: String,
    state: HashMap<String, Value>,
    last_vdom: Option<VNode>,
    /// Version number incremented on each render, used for VDOM synchronization
    version: u64,
}

#[pymethods]
impl RustLiveViewBackend {
    #[new]
    fn new(template_source: String) -> Self {
        Self {
            template_source,
            state: HashMap::new(),
            last_vdom: None,
            version: 0,
        }
    }

    /// Set a state variable
    fn set_state(&mut self, key: String, value: Value) {
        self.state.insert(key, value);
    }

    /// Update state with a dictionary
    fn update_state(&mut self, updates: HashMap<String, Value>) {
        self.state.extend(updates);
    }

    /// Update the template source while preserving VDOM state
    /// This allows dynamic templates to change without losing diffing capability
    fn update_template(&mut self, new_template_source: String) {
        self.template_source = new_template_source;
    }

    /// Get current state
    fn get_state(&self, py: Python) -> PyResult<PyObject> {
        let dict = PyDict::new_bound(py);
        for (k, v) in &self.state {
            dict.set_item(k, v.to_object(py))?;
        }
        Ok(dict.into())
    }

    /// Render the template and return HTML
    fn render(&mut self) -> PyResult<String> {
        // Get template from cache or parse and cache it
        let template_arc = if let Some(cached) = TEMPLATE_CACHE.get(&self.template_source) {
            cached.clone()
        } else {
            let template = Template::new(&self.template_source)?;
            let arc = Arc::new(template);
            TEMPLATE_CACHE.insert(self.template_source.clone(), arc.clone());
            arc
        };

        let context = Context::from_dict(self.state.clone());
        let html = template_arc.render(&context)?;
        Ok(html)
    }

    /// Render and compute diff from last render
    /// Returns a tuple of (html, patches_json, version)
    fn render_with_diff(&mut self) -> PyResult<(String, Option<String>, u64)> {
        // Get template from cache or parse and cache it
        let template_arc = if let Some(cached) = TEMPLATE_CACHE.get(&self.template_source) {
            cached.clone()
        } else {
            let template = Template::new(&self.template_source)?;
            let arc = Arc::new(template);
            TEMPLATE_CACHE.insert(self.template_source.clone(), arc.clone());
            arc
        };

        let context = Context::from_dict(self.state.clone());
        let html = template_arc.render(&context)?;

        // Parse new HTML to VDOM
        let new_vdom = parse_html(&html)
            .map_err(|e| PyErr::new::<pyo3::exceptions::PyRuntimeError, _>(e.to_string()))?;

        // Compute diff if we have a previous render
        let patches =
            if let Some(old_vdom) = &self.last_vdom {
                let patches = diff(old_vdom, &new_vdom);
                if !patches.is_empty() {
                    Some(serde_json::to_string(&patches).map_err(|e| {
                        PyErr::new::<pyo3::exceptions::PyValueError, _>(e.to_string())
                    })?)
                } else {
                    None
                }
            } else {
                None
            };

        self.last_vdom = Some(new_vdom);
        self.version += 1;

        Ok((html, patches, self.version))
    }

    /// Render and return patches as MessagePack bytes
    fn render_binary_diff(&mut self, py: Python) -> PyResult<(String, Option<PyObject>, u64)> {
        // Get template from cache or parse and cache it
        let template_arc = if let Some(cached) = TEMPLATE_CACHE.get(&self.template_source) {
            cached.clone()
        } else {
            let template = Template::new(&self.template_source)?;
            let arc = Arc::new(template);
            TEMPLATE_CACHE.insert(self.template_source.clone(), arc.clone());
            arc
        };

        let context = Context::from_dict(self.state.clone());
        let html = template_arc.render(&context)?;

        let new_vdom = parse_html(&html)
            .map_err(|e| PyErr::new::<pyo3::exceptions::PyRuntimeError, _>(e.to_string()))?;

        let patches_bytes = if let Some(old_vdom) = &self.last_vdom {
            let patches = diff(old_vdom, &new_vdom);
            if !patches.is_empty() {
                let bytes = rmp_serde::to_vec(&patches)
                    .map_err(|e| PyErr::new::<pyo3::exceptions::PyValueError, _>(e.to_string()))?;
                Some(PyBytes::new_bound(py, &bytes).into())
            } else {
                None
            }
        } else {
            None
        };

        self.last_vdom = Some(new_vdom);
        self.version += 1;

        Ok((html, patches_bytes, self.version))
    }

    /// Reset the view state
    fn reset(&mut self) {
        self.last_vdom = None;
        self.version = 0;
    }
}

/// Fast template rendering
#[pyfunction]
fn render_template(template_source: String, context: HashMap<String, Value>) -> PyResult<String> {
    // Get template from cache or parse and cache it
    let template_arc = if let Some(cached) = TEMPLATE_CACHE.get(&template_source) {
        cached.clone()
    } else {
        let template = Template::new(&template_source)?;
        let arc = Arc::new(template);
        TEMPLATE_CACHE.insert(template_source.clone(), arc.clone());
        arc
    };

    let ctx = Context::from_dict(context);
    Ok(template_arc.render(&ctx)?)
}

/// Compute diff between two HTML strings
#[pyfunction]
fn diff_html(old_html: String, new_html: String) -> PyResult<String> {
    let old = parse_html(&old_html)
        .map_err(|e| PyErr::new::<pyo3::exceptions::PyRuntimeError, _>(e.to_string()))?;
    let new = parse_html(&new_html)
        .map_err(|e| PyErr::new::<pyo3::exceptions::PyRuntimeError, _>(e.to_string()))?;

    let patches = diff(&old, &new);
    serde_json::to_string(&patches)
        .map_err(|e| PyErr::new::<pyo3::exceptions::PyValueError, _>(e.to_string()))
}

/// Fast JSON serialization for Python objects
/// Converts Python list/dict to JSON string using Rust's serde_json
///
/// Benefits:
/// - Releases Python GIL during serialization (better for concurrent workloads)
/// - More memory efficient for large datasets
/// - Similar performance to Python json.dumps for small datasets
#[pyfunction]
fn fast_json_dumps(py: Python, obj: &Bound<'_, PyAny>) -> PyResult<String> {
    // Convert Python object to serde_json::Value
    let value = python_to_json_value(py, obj)?;

    // Release GIL and serialize to JSON string
    py.allow_threads(|| {
        serde_json::to_string(&value).map_err(|e| {
            PyErr::new::<pyo3::exceptions::PyValueError, _>(format!(
                "JSON serialization error: {e}"
            ))
        })
    })
}

/// Helper function to convert Python objects to serde_json::Value
fn python_to_json_value(py: Python, obj: &Bound<'_, PyAny>) -> PyResult<serde_json::Value> {
    use serde_json::Value as JsonValue;

    if obj.is_none() {
        Ok(JsonValue::Null)
    } else if let Ok(b) = obj.extract::<bool>() {
        Ok(JsonValue::Bool(b))
    } else if let Ok(i) = obj.extract::<i64>() {
        Ok(JsonValue::Number(i.into()))
    } else if let Ok(f) = obj.extract::<f64>() {
        Ok(serde_json::Number::from_f64(f)
            .map(JsonValue::Number)
            .unwrap_or(JsonValue::Null))
    } else if let Ok(s) = obj.extract::<String>() {
        Ok(JsonValue::String(s))
    } else if let Ok(list) = obj.downcast::<PyList>() {
        let mut vec = Vec::new();
        for item in list.iter() {
            vec.push(python_to_json_value(py, &item)?);
        }
        Ok(JsonValue::Array(vec))
    } else if let Ok(dict) = obj.downcast::<PyDict>() {
        let mut map = serde_json::Map::new();
        for (key, value) in dict.iter() {
            let key_str = key.extract::<String>()?;
            map.insert(key_str, python_to_json_value(py, &value)?);
        }
        Ok(JsonValue::Object(map))
    } else {
        // Try to convert to string as fallback
        let s = obj.str()?.extract::<String>()?;
        Ok(JsonValue::String(s))
    }
}

/// Resolve template inheritance
///
/// Given a template path and list of template directories, resolves
/// {% extends %} and {% block %} tags to produce a final merged template string.
///
/// # Arguments
/// * `template_path` - Path to the child template (e.g., "products.html")
/// * `template_dirs` - List of directories to search for templates
///
/// # Returns
/// The merged template string with all inheritance resolved
#[pyfunction]
fn resolve_template_inheritance(
    template_path: String,
    template_dirs: Vec<String>,
) -> PyResult<String> {
    use djust_vdom::template::{resolve_inheritance, TemplateLoader};

    // Convert string paths to PathBuf
    let dirs: Vec<PathBuf> = template_dirs.iter().map(PathBuf::from).collect();

    // Create template loader
    let mut loader = TemplateLoader::new(dirs);

    // Resolve inheritance
    let resolved = resolve_inheritance(&template_path, &mut loader)
        .map_err(|e| PyErr::new::<pyo3::exceptions::PyRuntimeError, _>(e.to_string()))?;

    Ok(resolved)
}

/// Python module
#[pymodule]
fn _rust(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add_class::<RustLiveViewBackend>()?;
    m.add_function(wrap_pyfunction!(render_template, m)?)?;
    m.add_function(wrap_pyfunction!(diff_html, m)?)?;
    m.add_function(wrap_pyfunction!(fast_json_dumps, m)?)?;
    m.add_function(wrap_pyfunction!(resolve_template_inheritance, m)?)?;

    // Add pure Rust components (stateless, high-performance ~1μs rendering)
    m.add_class::<djust_components::RustAlert>()?;
    m.add_class::<djust_components::RustAvatar>()?;
    m.add_class::<djust_components::RustBadge>()?;
    m.add_class::<djust_components::RustButton>()?;
    m.add_class::<djust_components::RustCard>()?;
    m.add_class::<djust_components::RustDivider>()?;
    m.add_class::<djust_components::RustIcon>()?;
    m.add_class::<djust_components::RustModal>()?;
    m.add_class::<djust_components::RustProgress>()?;
    m.add_class::<djust_components::RustRange>()?;
    m.add_class::<djust_components::RustSpinner>()?;
    m.add_class::<djust_components::RustSwitch>()?;
    m.add_class::<djust_components::RustTextArea>()?;
    m.add_class::<djust_components::RustToast>()?;
    m.add_class::<djust_components::RustTooltip>()?;

    Ok(())
}
