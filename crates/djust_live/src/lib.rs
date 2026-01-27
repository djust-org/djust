//! djust - Reactive server-side rendering for Django
//!
//! This is the main crate that ties together templates, virtual DOM, and
//! provides Python bindings for reactive server-side rendering.

// PyResult type annotations are required by PyO3 API
#![allow(clippy::useless_conversion)]
// Parameter only used in recursion for Python value conversion
#![allow(clippy::only_used_in_recursion)]
// TODO: Migrate to IntoPyObject when pyo3 stabilizes the new API
// See: https://pyo3.rs/v0.23.0/migration
// TEMP REMOVED: #![allow(deprecated)]

// Actor system module
pub mod actors;

// Fast model serialization for N+1 query prevention
pub mod model_serializer;

use actors::{ActorSupervisor, SessionActorHandle};
use dashmap::DashMap;
use djust_core::{Context, Value};
use djust_templates::inheritance::FilesystemTemplateLoader;
use djust_templates::Template;
use djust_vdom::{diff, parse_html, parse_html_continue, reset_id_counter, VNode};
use once_cell::sync::Lazy;
use pyo3::prelude::*;
use pyo3::types::{PyBytes, PyDict, PyList, PyTuple};
use serde::{Deserialize, Serialize};
use std::collections::HashMap;
use std::path::PathBuf;
use std::sync::Arc;
use std::time::Duration;

/// Global template cache - parse once, reuse for all sessions
/// Using Arc<Template> for cheap cloning across threads
static TEMPLATE_CACHE: Lazy<DashMap<String, Arc<Template>>> = Lazy::new(DashMap::new);

/// Global supervisor for managing actor lifecycle
/// Created once with 1-hour TTL
static SUPERVISOR: Lazy<Arc<ActorSupervisor>> =
    Lazy::new(|| Arc::new(ActorSupervisor::new(Duration::from_secs(3600))));

/// Flag to track if supervisor background tasks have been started
static SUPERVISOR_STARTED: Lazy<std::sync::atomic::AtomicBool> =
    Lazy::new(|| std::sync::atomic::AtomicBool::new(false));

/// Ensure supervisor background tasks are started (idempotent)
fn ensure_supervisor_started() {
    use tracing::info;

    if !SUPERVISOR_STARTED.swap(true, std::sync::atomic::Ordering::SeqCst) {
        // First time - start background tasks
        let ttl_secs = SUPERVISOR.stats().ttl_secs;
        info!(
            ttl_secs = ttl_secs,
            cleanup_interval_secs = 60,
            health_check_interval_secs = 30,
            "Starting ActorSupervisor background tasks"
        );
        SUPERVISOR.clone().start();
    }
}

/// Serializable representation of RustLiveViewBackend for Redis storage
#[derive(Serialize, Deserialize)]
struct SerializableViewState {
    template_source: String,
    state: HashMap<String, Value>,
    last_vdom: Option<VNode>,
    version: u64,
    timestamp: f64, // Unix timestamp for session age tracking
}

/// A LiveView component that manages state and rendering (Rust backend)
#[pyclass(name = "RustLiveView")]
pub struct RustLiveViewBackend {
    template_source: String,
    state: HashMap<String, Value>,
    last_vdom: Option<VNode>,
    /// Version number incremented on each render, used for VDOM synchronization
    version: u64,
    /// Unix timestamp when this view was last serialized (for session age tracking)
    timestamp: f64,
    /// Template directories for {% include %} tag support
    template_dirs: Vec<PathBuf>,
}

#[pymethods]
impl RustLiveViewBackend {
    #[new]
    #[pyo3(signature = (template_source, template_dirs=None))]
    fn new(template_source: String, template_dirs: Option<Vec<String>>) -> Self {
        Self {
            template_source,
            state: HashMap::new(),
            last_vdom: None,
            version: 0,
            timestamp: 0.0, // Will be set on first serialization
            template_dirs: template_dirs
                .unwrap_or_default()
                .into_iter()
                .map(PathBuf::from)
                .collect(),
        }
    }

    /// Set template directories for {% include %} tag support
    fn set_template_dirs(&mut self, dirs: Vec<String>) {
        self.template_dirs = dirs.into_iter().map(PathBuf::from).collect();
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
        let dict = PyDict::new(py);
        for (k, v) in &self.state {
            dict.set_item(k, v.into_pyobject(py)?)?;
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

        // Use template loader for {% include %} support
        let loader = FilesystemTemplateLoader::new(self.template_dirs.clone());
        let html = template_arc.render_with_loader(&context, &loader)?;
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

        // Use template loader for {% include %} support
        let loader = FilesystemTemplateLoader::new(self.template_dirs.clone());
        let html = template_arc.render_with_loader(&context, &loader)?;

        // Parse new HTML to VDOM
        // IMPORTANT: Use parse_html() only for the FIRST render (resets ID counter to 0).
        // For subsequent renders, use parse_html_continue() to ensure newly inserted
        // elements get unique IDs that don't collide with existing elements in the DOM.
        let new_vdom = if self.last_vdom.is_some() {
            // Subsequent render: continue ID sequence to avoid collisions
            parse_html_continue(&html)
        } else {
            // First render: reset ID counter to start fresh
            parse_html(&html)
        }
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

        // Serialize VDOM back to HTML with data-dj-id attributes for reliable patch targeting
        let hydrated_html = new_vdom.to_html();

        self.last_vdom = Some(new_vdom);
        self.version += 1;

        Ok((hydrated_html, patches, self.version))
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

        // Use template loader for {% include %} support
        let loader = FilesystemTemplateLoader::new(self.template_dirs.clone());
        let html = template_arc.render_with_loader(&context, &loader)?;

        // IMPORTANT: Use parse_html_continue() for subsequent renders to avoid ID collisions
        let new_vdom = if self.last_vdom.is_some() {
            parse_html_continue(&html)
        } else {
            parse_html(&html)
        }
        .map_err(|e| PyErr::new::<pyo3::exceptions::PyRuntimeError, _>(e.to_string()))?;

        let patches_bytes = if let Some(old_vdom) = &self.last_vdom {
            let patches = diff(old_vdom, &new_vdom);
            if !patches.is_empty() {
                let bytes = rmp_serde::to_vec(&patches)
                    .map_err(|e| PyErr::new::<pyo3::exceptions::PyValueError, _>(e.to_string()))?;
                Some(PyBytes::new(py, &bytes).into())
            } else {
                None
            }
        } else {
            None
        };

        // Serialize VDOM back to HTML with data-dj-id attributes for reliable patch targeting
        let hydrated_html = new_vdom.to_html();

        self.last_vdom = Some(new_vdom);
        self.version += 1;

        Ok((hydrated_html, patches_bytes, self.version))
    }

    /// Reset the view state
    fn reset(&mut self) {
        self.last_vdom = None;
        self.version = 0;
        // Reset ID counter so next render starts fresh
        reset_id_counter();
    }

    /// Serialize the RustLiveView state to MessagePack bytes
    ///
    /// This enables efficient state persistence to Redis or other storage backends.
    /// Uses MessagePack for compact binary serialization (~30-40% smaller than JSON).
    /// Includes current timestamp for session age tracking.
    ///
    /// Returns: Python bytes object containing the serialized state with timestamp
    fn serialize_msgpack(&self, py: Python) -> PyResult<PyObject> {
        // Get current timestamp
        let ts = std::time::SystemTime::now()
            .duration_since(std::time::UNIX_EPOCH)
            .unwrap()
            .as_secs_f64();

        // Convert to serializable struct
        let serializable = SerializableViewState {
            template_source: self.template_source.clone(),
            state: self.state.clone(),
            last_vdom: self.last_vdom.clone(),
            version: self.version,
            timestamp: ts,
        };

        // Serialize to MessagePack bytes
        let bytes = rmp_serde::to_vec(&serializable).map_err(|e| {
            PyErr::new::<pyo3::exceptions::PyValueError, _>(format!(
                "MessagePack serialization error: {e}"
            ))
        })?;
        Ok(PyBytes::new(py, &bytes).into())
    }

    /// Deserialize a RustLiveView from MessagePack bytes
    ///
    /// Reconstructs a complete RustLiveView instance from bytes previously
    /// serialized with serialize_msgpack().
    ///
    /// Args:
    ///     bytes: Python bytes object containing MessagePack data
    ///
    /// Returns: RustLiveView instance with restored state
    #[staticmethod]
    fn deserialize_msgpack(bytes: &[u8]) -> PyResult<Self> {
        let serializable: SerializableViewState = rmp_serde::from_slice(bytes).map_err(|e| {
            PyErr::new::<pyo3::exceptions::PyValueError, _>(format!(
                "MessagePack deserialization error: {e}"
            ))
        })?;

        // Convert back to RustLiveViewBackend
        // Note: template_dirs must be re-set after deserialization via set_template_dirs()
        Ok(Self {
            template_source: serializable.template_source,
            state: serializable.state,
            last_vdom: serializable.last_vdom,
            version: serializable.version,
            timestamp: serializable.timestamp,
            template_dirs: Vec::new(),
        })
    }

    /// Get the timestamp when this view was last serialized
    ///
    /// Returns: Unix timestamp (seconds since epoch)
    fn get_timestamp(&self) -> f64 {
        self.timestamp
    }
}

// Public Rust API (for use by other Rust crates like djust_actors)
impl RustLiveViewBackend {
    /// Create a new RustLiveViewBackend (Rust API)
    pub fn new_rust(template_source: String) -> Self {
        Self::new(template_source, None)
    }

    /// Create new LiveView with template directories (Rust API)
    pub fn new_rust_with_dirs(template_source: String, template_dirs: Vec<String>) -> Self {
        Self::new(template_source, Some(template_dirs))
    }

    /// Update state (Rust API)
    pub fn update_state_rust(&mut self, updates: HashMap<String, Value>) {
        self.update_state(updates)
    }

    /// Render the template (Rust API)
    pub fn render_rust(&mut self) -> Result<String, djust_core::DjangoRustError> {
        self.render()
            .map_err(|e| djust_core::DjangoRustError::TemplateError(e.to_string()))
    }

    /// Render with diff (Rust API)
    /// Returns (html, patches_json, version)
    pub fn render_with_diff_rust(
        &mut self,
    ) -> Result<(String, Option<Vec<djust_vdom::Patch>>, u64), djust_core::DjangoRustError> {
        let (html, patches_json, version) = self
            .render_with_diff()
            .map_err(|e| djust_core::DjangoRustError::TemplateError(e.to_string()))?;

        let patches = if let Some(json) = patches_json {
            Some(
                serde_json::from_str(&json)
                    .map_err(|e| djust_core::DjangoRustError::TemplateError(e.to_string()))?,
            )
        } else {
            None
        };

        Ok((html, patches, version))
    }

    /// Reset the view state (Rust API)
    pub fn reset_rust(&mut self) {
        self.reset()
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

/// Fast template rendering with template directories for {% include %} support
///
/// This function extends render_template to support {% include %} tags by
/// providing template directories for the Rust renderer to load included templates.
///
/// # Arguments
/// * `template_source` - The template source string to render
/// * `context` - Template context variables
/// * `template_dirs` - List of directories to search for included templates
///
/// # Returns
/// The rendered HTML string
#[pyfunction]
fn render_template_with_dirs(
    template_source: String,
    context: HashMap<String, Value>,
    template_dirs: Vec<String>,
) -> PyResult<String> {
    use djust_templates::inheritance::FilesystemTemplateLoader;

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

    // Create filesystem template loader with the provided directories
    let dirs: Vec<PathBuf> = template_dirs.iter().map(PathBuf::from).collect();
    let loader = FilesystemTemplateLoader::new(dirs);

    // Render with the loader to support {% include %} tags
    Ok(template_arc.render_with_loader(&ctx, &loader)?)
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
    use djust_templates::inheritance::resolve_template_inheritance as resolve;

    // Convert string paths to PathBuf
    let dirs: Vec<PathBuf> = template_dirs.iter().map(PathBuf::from).collect();

    // Resolve inheritance using AST-based implementation
    let resolved = resolve(&template_path, &dirs)
        .map_err(|e| PyErr::new::<pyo3::exceptions::PyRuntimeError, _>(e.to_string()))?;

    Ok(resolved)
}

// ============================================================================
// Actor System Python Bindings
// ============================================================================

use pyo3_async_runtimes::tokio::future_into_py;

/// Python wrapper for SessionActorHandle
///
/// This class provides async methods that can be called from Python's asyncio.
#[pyclass(name = "SessionActorHandle")]
pub struct SessionActorHandlePy {
    handle: SessionActorHandle,
}

#[pymethods]
impl SessionActorHandlePy {
    /// Mount a view (Phase 6: Now returns view_id for routing)
    ///
    /// Creates a ViewActor, initializes its state, and renders the initial HTML.
    ///
    /// Args:
    ///     view_path (str): Python path to the LiveView class (e.g. "app.views.Counter")
    ///     params (dict): Initial state parameters
    ///     python_view (Optional[Any]): Python LiveView instance for event handler callbacks
    ///
    /// Returns:
    ///     dict: {"html": str, "session_id": str, "view_id": str}
    #[pyo3(signature = (view_path, params, python_view=None))]
    fn mount<'py>(
        &self,
        py: Python<'py>,
        view_path: String,
        params: &Bound<'py, PyDict>,
        python_view: Option<Py<PyAny>>,
    ) -> PyResult<Bound<'py, PyAny>> {
        let handle = self.handle.clone();

        // Convert Python dict to Rust HashMap<String, Value>
        let params_rust = python_dict_to_hashmap(params)?;

        future_into_py(py, async move {
            let result = handle
                .mount(view_path, params_rust, python_view)
                .await
                .map_err(|e| PyErr::new::<pyo3::exceptions::PyRuntimeError, _>(e.to_string()))?;

            Python::with_gil(|py| -> PyResult<PyObject> {
                let dict = PyDict::new(py);
                dict.set_item("html", result.html)?;
                dict.set_item("session_id", result.session_id)?;
                dict.set_item("view_id", result.view_id)?; // Phase 6: Return view_id
                Ok(dict.unbind().into())
            })
        })
    }

    /// Handle an event (Phase 6: Now supports view_id routing)
    ///
    /// Routes the event to the appropriate ViewActor and returns the resulting
    /// VDOM patches or full HTML.
    ///
    /// Args:
    ///     event_name (str): Name of the event (e.g. "increment", "submit_form")
    ///     params (dict): Event parameters
    ///     view_id (Optional[str]): View ID for routing. If None, routes to first view (backward compat)
    ///
    /// Returns:
    ///     dict: {"patches": Optional[str], "html": Optional[str], "version": int}
    #[pyo3(signature = (event_name, params, view_id=None))]
    fn event<'py>(
        &self,
        py: Python<'py>,
        event_name: String,
        params: &Bound<'py, PyDict>,
        view_id: Option<String>,
    ) -> PyResult<Bound<'py, PyAny>> {
        let handle = self.handle.clone();

        // Convert Python dict to Rust HashMap<String, Value>
        let params_rust = python_dict_to_hashmap(params)?;

        future_into_py(py, async move {
            let result = handle
                .event(event_name, params_rust, view_id)
                .await
                .map_err(|e| PyErr::new::<pyo3::exceptions::PyRuntimeError, _>(e.to_string()))?;

            Python::with_gil(|py| -> PyResult<PyObject> {
                let dict = PyDict::new(py);

                // Add patches if available
                if let Some(patches) = result.patches {
                    let patches_json = serde_json::to_string(&patches).map_err(|e| {
                        PyErr::new::<pyo3::exceptions::PyValueError, _>(e.to_string())
                    })?;
                    dict.set_item("patches", patches_json)?;
                } else {
                    dict.set_item("patches", py.None())?;
                }

                // Add html if available
                if let Some(html) = result.html {
                    dict.set_item("html", html)?;
                } else {
                    dict.set_item("html", py.None())?;
                }

                dict.set_item("version", result.version)?;
                Ok(dict.unbind().into())
            })
        })
    }

    /// Unmount a specific view (Phase 6)
    ///
    /// Shuts down a specific ViewActor and removes it from the session.
    ///
    /// Args:
    ///     view_id (str): The UUID of the view to unmount
    ///
    /// Returns:
    ///     None
    fn unmount<'py>(&self, py: Python<'py>, view_id: String) -> PyResult<Bound<'py, PyAny>> {
        let handle = self.handle.clone();

        future_into_py(py, async move {
            handle
                .unmount(view_id)
                .await
                .map_err(|e| PyErr::new::<pyo3::exceptions::PyRuntimeError, _>(e.to_string()))?;
            Ok(())
        })
    }

    /// Health check ping
    ///
    /// Verifies that the session actor is still responsive.
    ///
    /// Returns:
    ///     None
    fn ping<'py>(&self, py: Python<'py>) -> PyResult<Bound<'py, PyAny>> {
        let handle = self.handle.clone();

        future_into_py(py, async move {
            handle
                .ping()
                .await
                .map_err(|e| PyErr::new::<pyo3::exceptions::PyRuntimeError, _>(e.to_string()))?;
            Ok(())
        })
    }

    /// Shutdown the session gracefully
    ///
    /// Shuts down all child ViewActors and then the SessionActor itself.
    fn shutdown<'py>(&self, py: Python<'py>) -> PyResult<Bound<'py, PyAny>> {
        let handle = self.handle.clone();

        future_into_py(py, async move {
            handle.shutdown().await;
            Ok(())
        })
    }

    // ========================================================================
    // Phase 8: Component Management Python API
    // ========================================================================

    /// Create a component in a specific view (Phase 8)
    ///
    /// Args:
    ///     view_id (str): ID of the view to create the component in
    ///     component_id (str): Unique identifier for the component
    ///     template_string (str): Template for rendering the component
    ///     initial_props (dict): Initial component state/props
    ///     python_component (Optional[Any]): Python component instance for event handlers (Phase 8.2)
    ///
    /// Returns:
    ///     str: Initial rendered HTML of the component
    #[pyo3(signature = (view_id, component_id, template_string, initial_props, python_component=None))]
    fn create_component<'py>(
        &self,
        py: Python<'py>,
        view_id: String,
        component_id: String,
        template_string: String,
        initial_props: &Bound<'py, PyDict>,
        python_component: Option<Py<PyAny>>,
    ) -> PyResult<Bound<'py, PyAny>> {
        let handle = self.handle.clone();
        let props_rust = python_dict_to_hashmap(initial_props)?;

        future_into_py(py, async move {
            let html = handle
                .create_component(
                    view_id,
                    component_id,
                    template_string,
                    props_rust,
                    python_component,
                )
                .await
                .map_err(|e| PyErr::new::<pyo3::exceptions::PyRuntimeError, _>(e.to_string()))?;

            Ok(html)
        })
    }

    /// Route event to a specific component (Phase 8)
    ///
    /// Args:
    ///     view_id (str): ID of the view containing the component
    ///     component_id (str): ID of the component to send event to
    ///     event_name (str): Name of the event handler to call
    ///     params (dict): Event parameters
    ///
    /// Returns:
    ///     str: Rendered HTML after the component handles the event
    fn component_event<'py>(
        &self,
        py: Python<'py>,
        view_id: String,
        component_id: String,
        event_name: String,
        params: &Bound<'py, PyDict>,
    ) -> PyResult<Bound<'py, PyAny>> {
        let handle = self.handle.clone();
        let params_rust = python_dict_to_hashmap(params)?;

        future_into_py(py, async move {
            let html = handle
                .component_event(view_id, component_id, event_name, params_rust)
                .await
                .map_err(|e| PyErr::new::<pyo3::exceptions::PyRuntimeError, _>(e.to_string()))?;

            Ok(html)
        })
    }

    /// Update props for a specific component (Phase 8)
    ///
    /// Args:
    ///     view_id (str): ID of the view containing the component
    ///     component_id (str): ID of the component to update
    ///     props (dict): New props to merge into component state
    ///
    /// Returns:
    ///     str: Rendered HTML after updating props
    fn update_component_props<'py>(
        &self,
        py: Python<'py>,
        view_id: String,
        component_id: String,
        props: &Bound<'py, PyDict>,
    ) -> PyResult<Bound<'py, PyAny>> {
        let handle = self.handle.clone();
        let props_rust = python_dict_to_hashmap(props)?;

        future_into_py(py, async move {
            let html = handle
                .update_component_props(view_id, component_id, props_rust)
                .await
                .map_err(|e| PyErr::new::<pyo3::exceptions::PyRuntimeError, _>(e.to_string()))?;

            Ok(html)
        })
    }

    /// Remove a component (Phase 8)
    ///
    /// Args:
    ///     view_id (str): ID of the view containing the component
    ///     component_id (str): ID of the component to remove
    ///
    /// Returns:
    ///     None
    fn remove_component<'py>(
        &self,
        py: Python<'py>,
        view_id: String,
        component_id: String,
    ) -> PyResult<Bound<'py, PyAny>> {
        let handle = self.handle.clone();

        future_into_py(py, async move {
            handle
                .remove_component(view_id, component_id)
                .await
                .map_err(|e| PyErr::new::<pyo3::exceptions::PyRuntimeError, _>(e.to_string()))?;

            Ok(())
        })
    }

    /// Get the session ID
    #[getter]
    fn session_id(&self) -> String {
        self.handle.session_id().to_string()
    }
}

/// Create a new session actor
///
/// This function creates a SessionActor, spawns it on the Tokio runtime,
/// and returns a handle wrapped for Python.
///
/// Args:
///     session_id (str): Unique identifier for this session
///
/// Returns:
///     SessionActorHandle: Handle to send messages to the actor
#[pyfunction]
pub fn create_session_actor(py: Python<'_>, session_id: String) -> PyResult<Bound<'_, PyAny>> {
    future_into_py(py, async move {
        // Ensure supervisor background tasks are started (idempotent)
        ensure_supervisor_started();

        // Use global supervisor to get or create session
        let handle = SUPERVISOR.get_or_create_session(session_id).await;

        Python::with_gil(|py| -> PyResult<PyObject> {
            Ok(Py::new(py, SessionActorHandlePy { handle })?.into_any())
        })
    })
}

/// Supervisor statistics exposed to Python
#[pyclass]
#[derive(Debug, Clone)]
pub struct SupervisorStatsPy {
    /// Number of active sessions
    #[pyo3(get)]
    pub active_sessions: usize,
    /// Time-to-live for idle sessions in seconds
    #[pyo3(get)]
    pub ttl_secs: u64,
}

/// Get actor system statistics
///
/// Returns statistics about the actor supervisor including active sessions
/// and configured TTL.
///
/// Returns:
///     SupervisorStats: Object with active_sessions and ttl_secs attributes
#[pyfunction]
pub fn get_actor_stats() -> SupervisorStatsPy {
    let stats = SUPERVISOR.stats();
    SupervisorStatsPy {
        active_sessions: stats.active_sessions,
        ttl_secs: stats.ttl_secs,
    }
}

// Helper functions for Python ↔ Rust conversion

/// Convert Python dict to Rust HashMap<String, Value>
fn python_dict_to_hashmap(dict: &Bound<'_, PyDict>) -> PyResult<HashMap<String, Value>> {
    let mut map = HashMap::new();

    for (key, value) in dict.iter() {
        let key_str = key.extract::<String>()?;
        let rust_value = python_to_value(&value)?;
        map.insert(key_str, rust_value);
    }

    Ok(map)
}

/// Convert Python object to Rust Value
fn python_to_value(obj: &Bound<'_, PyAny>) -> PyResult<Value> {
    // String
    if let Ok(s) = obj.extract::<String>() {
        return Ok(Value::String(s));
    }

    // Integer
    if let Ok(i) = obj.extract::<i64>() {
        return Ok(Value::Integer(i));
    }

    // Float
    if let Ok(f) = obj.extract::<f64>() {
        return Ok(Value::Float(f));
    }

    // Boolean
    if let Ok(b) = obj.extract::<bool>() {
        return Ok(Value::Bool(b));
    }

    // None
    if obj.is_none() {
        return Ok(Value::Null);
    }

    // List
    if let Ok(list) = obj.downcast::<PyList>() {
        let mut vec = Vec::new();
        for item in list.iter() {
            vec.push(python_to_value(&item)?);
        }
        return Ok(Value::List(vec));
    }

    // Dict - recursively convert nested values
    if let Ok(dict) = obj.downcast::<PyDict>() {
        let mut map = HashMap::new();
        for (key, value) in dict.iter() {
            let key_str = key.extract::<String>()?;
            map.insert(key_str, python_to_value(&value)?);
        }
        return Ok(Value::Object(map));
    }

    // Fallback: try to convert to string
    if let Ok(s) = obj.str() {
        let s_str: String = s.extract()?;
        return Ok(Value::String(s_str));
    }

    Err(PyErr::new::<pyo3::exceptions::PyTypeError, _>(
        "Cannot convert Python type to Value".to_string(),
    ))
}

/// Extract template variables for JIT auto-serialization.
///
/// Parses a Django template and returns a dictionary mapping variable names
/// to lists of their access paths. This enables automatic serialization of
/// only the required Django ORM fields for efficient Rust template rendering.
///
/// # Arguments
///
/// * `template` - Template source string
///
/// # Returns
///
/// Dictionary mapping variable names to lists of attribute paths:
/// - Root variables map to empty lists
/// - Nested variables map to their access paths
/// - Paths are deduplicated and sorted alphabetically
///
/// # Raises
///
/// * `ValueError` - If template cannot be parsed (malformed syntax)
///
/// # Behavior
///
/// - **Empty templates**: Returns empty dict `{}`
/// - **Malformed templates**: Raises `ValueError` with parsing error details
/// - **Duplicate paths**: Automatically deduplicated
/// - **Template tags**: Extracts from for/if/with/block tags
/// - **Filters**: Ignores filters but preserves variable paths
///
/// # Example
///
/// ```python
/// from djust._rust import extract_template_variables
///
/// # Basic usage
/// template = "{{ lease.property.name }} {{ lease.tenant.user.email }}"
/// vars = extract_template_variables(template)
/// # Returns: {"lease": ["property.name", "tenant.user.email"]}
///
/// # Empty template
/// vars = extract_template_variables("")
/// # Returns: {}
///
/// # Root variable (no path)
/// vars = extract_template_variables("{{ count }}")
/// # Returns: {"count": []}
///
/// # Malformed template
/// try:
///     vars = extract_template_variables("{% if x")
/// except ValueError as e:
///     print(f"Parse error: {e}")
/// ```
///
/// # Use Case
///
/// ```python
/// class LeaseView(LiveView):
///     template_string = '''
///         {% for lease in expiring_soon %}
///             {{ lease.property.name }}
///             {{ lease.tenant.user.email }}
///         {% endfor %}
///     '''
///
///     def mount(self, request):
///         # Extract required fields
///         vars = extract_template_variables(self.template_string)
///         # vars = {
///         #   'lease': ['property.name', 'tenant.user.email'],
///         #   'expiring_soon': []
///         # }
///
///         # Generate optimized query
///         self.expiring_soon = Lease.objects.select_related(
///             'property', 'tenant__user'
///         ).filter(end_date__lte=timezone.now() + timedelta(days=30))
/// ```
/// Path node for serializer field tree
#[derive(Debug, Clone)]
enum PathNode {
    Leaf,
    Object(std::collections::HashMap<String, PathNode>),
    List(std::collections::HashMap<String, PathNode>),
}

/// Convert serde_json::Value to Python object using PyO3
///
/// This is faster than serializing to JSON string and parsing back!
fn json_value_to_py(py: Python, value: &serde_json::Value) -> PyResult<PyObject> {
    match value {
        serde_json::Value::Null => Ok(py.None()),
        serde_json::Value::Bool(b) => Ok((*b).into_pyobject(py)?.to_owned().into_any().unbind()),
        serde_json::Value::Number(n) => {
            if let Some(i) = n.as_i64() {
                Ok(i.into_pyobject(py)?.to_owned().into_any().unbind())
            } else if let Some(f) = n.as_f64() {
                Ok(f.into_pyobject(py)?.to_owned().into_any().unbind())
            } else {
                Ok(py.None())
            }
        }
        serde_json::Value::String(s) => Ok(s.into_pyobject(py)?.to_owned().into_any().unbind()),
        serde_json::Value::Array(arr) => {
            let py_list = PyList::empty(py);
            for item in arr {
                py_list.append(json_value_to_py(py, item)?)?;
            }
            Ok(py_list.into())
        }
        serde_json::Value::Object(obj) => {
            let py_dict = PyDict::new(py);
            for (key, val) in obj {
                py_dict.set_item(key, json_value_to_py(py, val)?)?;
            }
            Ok(py_dict.into())
        }
    }
}

/// Serialize a QuerySet/list of Django objects based on field paths - FAST!
///
/// This is a Rust-based serializer that extracts only specified fields from Python objects,
/// bypassing Python's overhead for attribute access and JSON encoding.
///
/// Performance: 5-10x faster than Python json.dumps() with DjangoJSONEncoder
///
/// # Arguments
/// * `objects` - List of Python objects (Django model instances)
/// * `field_paths` - List of dot-separated paths (e.g., ["user.email", "active_leases.0.property.name"])
///
/// # Returns
/// Python list of dictionaries (not JSON string!)
///
/// # Example
/// ```python
/// from djust._rust import serialize_queryset
///
/// tenants = Tenant.objects.select_related('user').prefetch_related('active_leases__property')
/// paths = ['user.email', 'user.get_full_name', 'active_leases.0.property.name', 'phone']
/// result_list = serialize_queryset(tenants, paths)  # Returns Python list directly!
/// ```
#[pyfunction(name = "serialize_queryset")]
fn serialize_queryset_py(
    py: Python,
    objects: &Bound<'_, PyList>,
    field_paths: Vec<String>,
) -> PyResult<Py<PyList>> {
    // Parse paths into tree structure for efficient traversal
    let path_tree = build_field_tree(&field_paths);

    // Create Python list to hold results
    let result_list = PyList::empty(py);

    // Iterate over objects
    for obj in objects.iter() {
        let serialized = serialize_object_with_paths(py, &obj, &path_tree)?;
        // Convert serde_json::Value to Python dict
        let py_dict = json_value_to_py(py, &serialized)?;
        result_list.append(py_dict)?;
    }

    Ok(result_list.into())
}

/// Serialize entire context dict to JSON-compatible Python dict
///
/// Handles all Python types efficiently:
/// - Simple types (str, int, float, bool, None): pass through
/// - Lists/tuples: recursively serialize
/// - Dicts: recursively serialize
/// - Components: call .render() and wrap in {"render": ...}
/// - Django types (datetime, Decimal, UUID): convert to strings
#[pyfunction(name = "serialize_context")]
fn serialize_context_py(py: Python, context: &Bound<'_, PyDict>) -> PyResult<Py<PyDict>> {
    let result_dict = PyDict::new(py);

    for (key, value) in context.iter() {
        let key_str: String = key.extract()?;
        let serialized_value = serialize_python_value(py, &value)?;
        result_dict.set_item(key_str, serialized_value)?;
    }

    Ok(result_dict.into())
}

/// Recursively serialize a Python value to JSON-compatible form
fn serialize_python_value(py: Python, value: &Bound<'_, PyAny>) -> PyResult<PyObject> {
    // Fast path: None
    if value.is_none() {
        return Ok(py.None());
    }

    // Get type name for special type handling
    let type_name = value
        .get_type()
        .name()
        .map_or("unknown".to_string(), |s| s.to_string());

    // IMPORTANT: Check compound types (List, Tuple, Dict) BEFORE simple types!
    // Otherwise extract::<String>() will convert them to string repr

    // Lists and tuples: recursively serialize
    if let Ok(list) = value.downcast::<PyList>() {
        let result_list = PyList::empty(py);
        for item in list.iter() {
            let serialized = serialize_python_value(py, &item)?;
            result_list.append(serialized)?;
        }
        return Ok(result_list.into());
    }

    if let Ok(tuple) = value.downcast::<PyTuple>() {
        let result_list = PyList::empty(py);
        for item in tuple.iter() {
            let serialized = serialize_python_value(py, &item)?;
            result_list.append(serialized)?;
        }
        return Ok(result_list.into());
    }

    // Dicts: recursively serialize
    if let Ok(dict) = value.downcast::<PyDict>() {
        let result_dict = PyDict::new(py);
        for (k, v) in dict.iter() {
            let key_str: String = k.extract()?;
            let serialized = serialize_python_value(py, &v)?;
            result_dict.set_item(key_str, serialized)?;
        }
        return Ok(result_dict.into());
    }

    // Django QuerySet: Convert to list which triggers JIT serialization
    if type_name == "QuerySet" {
        // Call list() on the QuerySet to force evaluation
        if let Ok(list_fn) = py.eval(c"list", None, None) {
            if let Ok(as_list) = list_fn.call1((value,)) {
                return serialize_python_value(py, &as_list);
            }
        }
    }

    // Fast path: Simple types (str, int, float, bool)
    // These must come AFTER compound types to avoid converting them to strings
    if let Ok(s) = value.extract::<String>() {
        return Ok(s.into_pyobject(py)?.to_owned().into_any().unbind());
    }
    if let Ok(i) = value.extract::<i64>() {
        return Ok(i.into_pyobject(py)?.to_owned().into_any().unbind());
    }
    if let Ok(f) = value.extract::<f64>() {
        return Ok(f.into_pyobject(py)?.to_owned().into_any().unbind());
    }
    if let Ok(b) = value.extract::<bool>() {
        return Ok(b.into_pyobject(py)?.to_owned().into_any().unbind());
    }

    // Components: Check if object has 'render' method
    if value.hasattr("render")? {
        // Call .render() method
        let render_method = value.getattr("render")?;
        if render_method.is_callable() {
            match render_method.call0() {
                Ok(rendered) => {
                    // Wrap in {"render": ...} dict
                    let wrapper = PyDict::new(py);
                    let rendered_str: String = rendered.extract()?;
                    wrapper.set_item("render", rendered_str)?;
                    return Ok(wrapper.into());
                }
                Err(_) => {
                    // Render failed, fallback to str()
                    let str_repr = value.str()?.to_string();
                    return Ok(str_repr.into_pyobject(py)?.to_owned().into_any().unbind());
                }
            }
        }
    }

    // Django date/time types: convert to ISO strings
    // Check for common Django/Python datetime types
    if type_name == "datetime" || type_name == "date" || type_name == "time" {
        if let Ok(isoformat) = value.call_method0("isoformat") {
            let iso_str: String = isoformat.extract()?;
            return Ok(iso_str.into_pyobject(py)?.to_owned().into_any().unbind());
        }
    }

    // Decimal, UUID: convert to string
    if type_name == "Decimal" || type_name == "UUID" {
        let str_repr = value.str()?.to_string();
        return Ok(str_repr.into_pyobject(py)?.to_owned().into_any().unbind());
    }

    // Django model instances: serialize to dict using model_to_dict + methods
    if value.hasattr("_meta")? {
        // Use Django's model_to_dict to serialize the model fields
        if let Ok(forms_module) = py.import("django.forms.models") {
            if let Ok(model_to_dict_fn) = forms_module.getattr("model_to_dict") {
                if let Ok(model_dict) = model_to_dict_fn.call1((value,)) {
                    // Convert to PyDict so we can add method results
                    if let Ok(result_dict) = model_dict.downcast::<PyDict>() {
                        // Call specific, known-safe get_* methods commonly used in templates
                        // This is safer than calling all get_* methods which can cause infinite recursion
                        let safe_methods = vec![
                            "get_full_name",
                            "get_short_name",
                            "get_absolute_url",
                            "get_username",
                        ];

                        for method_name in safe_methods {
                            if let Ok(attr) = value.getattr(method_name) {
                                if attr.is_callable() {
                                    if let Ok(result) = attr.call0() {
                                        // Convert result to string to avoid recursive serialization
                                        if let Ok(result_str) = result.str() {
                                            let _ = result_dict.set_item(method_name, result_str);
                                        }
                                    }
                                }
                            }
                        }

                        // Recursively serialize the enhanced dict
                        return serialize_python_value(py, result_dict.as_any());
                    }
                }
            }
        }
    }

    // Fallback: convert to string representation
    let str_repr = value.str()?.to_string();
    Ok(str_repr.into_pyobject(py)?.to_owned().into_any().unbind())
}

/// Build a tree structure from flat field paths for efficient nested traversal
fn build_field_tree(paths: &[String]) -> std::collections::HashMap<String, PathNode> {
    use std::collections::HashMap;

    let mut root: HashMap<String, PathNode> = HashMap::new();

    for path in paths {
        let parts: Vec<&str> = path.split('.').collect();
        if parts.is_empty() {
            continue;
        }

        let mut current = &mut root;
        let mut i = 0;

        while i < parts.len() {
            let part = parts[i];

            // Check if next part is numeric index (list access)
            if i + 1 < parts.len() && parts[i + 1].parse::<usize>().is_ok() {
                // This is a list attribute
                let entry = current
                    .entry(part.to_string())
                    .or_insert_with(|| PathNode::List(HashMap::new()));

                match entry {
                    PathNode::List(nested_map) => {
                        // Skip the numeric index and continue with remaining parts
                        i += 2;
                        if i < parts.len() {
                            // Process remaining path within list items
                            let remaining_parts: Vec<&str> = parts[i..].to_vec();
                            let remaining_path = remaining_parts.join(".");

                            // Recursively add remaining path to nested map
                            let mut temp_map = std::mem::take(nested_map);
                            add_path_to_tree(&mut temp_map, &remaining_path);
                            *nested_map = temp_map;
                        }
                        break;
                    }
                    PathNode::Leaf => {
                        // Convert Leaf to List (handles case where 'active_leases' was inserted before 'active_leases.0.property.name')
                        *entry = PathNode::List(HashMap::new());
                        if let PathNode::List(nested_map) = entry {
                            // Skip the numeric index and continue with remaining parts
                            i += 2;
                            if i < parts.len() {
                                // Process remaining path within list items
                                let remaining_parts: Vec<&str> = parts[i..].to_vec();
                                let remaining_path = remaining_parts.join(".");

                                // Recursively add remaining path to nested map
                                let mut temp_map = std::mem::take(nested_map);
                                add_path_to_tree(&mut temp_map, &remaining_path);
                                *nested_map = temp_map;
                            }
                        }
                        break;
                    }
                    _ => {
                        // Other type mismatch - skip this path and continue to next
                        break;
                    }
                }
            } else {
                // Regular attribute
                if i == parts.len() - 1 {
                    // Leaf node
                    current.entry(part.to_string()).or_insert(PathNode::Leaf);
                } else {
                    // Intermediate node
                    let entry = current
                        .entry(part.to_string())
                        .or_insert_with(|| PathNode::Object(HashMap::new()));

                    match entry {
                        PathNode::Object(nested_map) => {
                            current = nested_map;
                        }
                        _ => {
                            return root; // Type mismatch
                        }
                    }
                }
                i += 1;
            }
        }
    }

    root
}

/// Helper to add a path to an existing tree
fn add_path_to_tree(tree: &mut std::collections::HashMap<String, PathNode>, path: &str) {
    use std::collections::HashMap;

    let parts: Vec<&str> = path.split('.').collect();
    if parts.is_empty() {
        return;
    }

    let mut current = tree;
    for (i, part) in parts.iter().enumerate() {
        if i == parts.len() - 1 {
            current.entry(part.to_string()).or_insert(PathNode::Leaf);
        } else {
            let entry = current
                .entry(part.to_string())
                .or_insert_with(|| PathNode::Object(HashMap::new()));
            match entry {
                PathNode::Object(nested_map) => {
                    current = nested_map;
                }
                _ => break,
            }
        }
    }
}

/// Serialize a single Python object based on path tree
fn serialize_object_with_paths(
    py: Python,
    obj: &Bound<'_, PyAny>,
    tree: &std::collections::HashMap<String, PathNode>,
) -> PyResult<serde_json::Value> {
    use serde_json::{Map, Value as JsonValue};

    let mut result = Map::new();

    for (attr_name, node) in tree {
        // Try to access attribute
        let attr_result = obj.getattr(attr_name.as_str());

        if attr_result.is_err() {
            continue; // Attribute doesn't exist, skip
        }

        let attr_value = attr_result?;

        // Check if None
        if attr_value.is_none() {
            result.insert(attr_name.clone(), JsonValue::Null);
            continue;
        }

        match node {
            PathNode::Leaf => {
                // Check if it's a callable (method) - try calling it first
                if attr_value.is_callable() {
                    // It's a method - try calling it
                    match attr_value.call0() {
                        Ok(method_result) => {
                            // Method call succeeded - use the result
                            result.insert(attr_name.clone(), python_to_json(py, &method_result)?);
                        }
                        Err(_) => {
                            // Method call failed - skip this attribute (don't insert null)
                            // This can happen if the method requires arguments
                            continue;
                        }
                    }
                } else {
                    // Not callable - it's a direct attribute value
                    result.insert(attr_name.clone(), python_to_json(py, &attr_value)?);
                }
            }
            PathNode::Object(nested_tree) => {
                // Nested object
                let nested_result = serialize_object_with_paths(py, &attr_value, nested_tree)?;
                result.insert(attr_name.clone(), nested_result);
            }
            PathNode::List(nested_tree) => {
                // Iterate over list and serialize each item
                let mut list_results = Vec::new();

                // For Django QuerySets/Managers, we need to evaluate them first
                // Try to call .all() if it exists (for QuerySets/Managers)
                let iterable = if let Ok(all_method) = attr_value.getattr("all") {
                    // It has .all() method - call it to get the QuerySet
                    if let Ok(queryset) = all_method.call0() {
                        // Convert QuerySet to Python list for iteration
                        if let Ok(list_func) = py.eval(c"list", None, None) {
                            if let Ok(py_list) = list_func.call1((queryset,)) {
                                py_list
                            } else {
                                attr_value.clone()
                            }
                        } else {
                            attr_value.clone()
                        }
                    } else {
                        attr_value.clone()
                    }
                } else {
                    attr_value.clone()
                };

                // Try to iterate
                if let Ok(iterator) = iterable.try_iter() {
                    for item_result in iterator {
                        let item_obj = match item_result {
                            Ok(obj) => obj,
                            Err(_) => continue,
                        };
                        let item_result = serialize_object_with_paths(py, &item_obj, nested_tree)?;
                        list_results.push(item_result);
                    }
                }

                result.insert(attr_name.clone(), JsonValue::Array(list_results));
            }
        }
    }

    Ok(JsonValue::Object(result))
}

/// Convert Python value to JSON value
fn python_to_json(_py: Python, value: &Bound<'_, PyAny>) -> PyResult<serde_json::Value> {
    // Handle None
    if value.is_none() {
        return Ok(serde_json::Value::Null);
    }

    // Try bool first (before int, since bool is subclass of int in Python)
    if let Ok(b) = value.extract::<bool>() {
        return Ok(serde_json::Value::Bool(b));
    }

    // Try int
    if let Ok(i) = value.extract::<i64>() {
        return Ok(serde_json::Value::Number(i.into()));
    }

    // Try float
    if let Ok(f) = value.extract::<f64>() {
        if let Some(num) = serde_json::Number::from_f64(f) {
            return Ok(serde_json::Value::Number(num));
        }
    }

    // Try string (covers str, datetime, UUID, etc via __str__)
    if let Ok(s) = value.extract::<String>() {
        return Ok(serde_json::Value::String(s));
    }

    // Fallback: convert to string
    match value.str() {
        Ok(s) => Ok(serde_json::Value::String(s.to_string())),
        Err(_) => Ok(serde_json::Value::Null),
    }
}

#[pyfunction(name = "extract_template_variables")]
fn extract_template_variables_py(py: Python, template: String) -> PyResult<PyObject> {
    // Call Rust template parser
    let vars_map = djust_templates::extract_template_variables(&template).map_err(|e| {
        PyErr::new::<pyo3::exceptions::PyValueError, _>(format!("Template parsing error: {e}"))
    })?;

    // Convert Rust HashMap to Python dict
    let py_dict = PyDict::new(py);
    for (key, paths) in vars_map {
        let py_list = PyList::new(py, paths.iter().map(|s| s.as_str()))?;
        py_dict.set_item(key, py_list)?;
    }

    Ok(py_dict.into())
}

#[pymodule]
fn _rust(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add_class::<RustLiveViewBackend>()?;
    m.add_function(wrap_pyfunction!(render_template, m)?)?;
    m.add_function(wrap_pyfunction!(render_template_with_dirs, m)?)?;
    m.add_function(wrap_pyfunction!(diff_html, m)?)?;
    m.add_function(wrap_pyfunction!(fast_json_dumps, m)?)?;
    m.add_function(wrap_pyfunction!(resolve_template_inheritance, m)?)?;

    // Actor system exports
    m.add_class::<SessionActorHandlePy>()?;
    m.add_class::<SupervisorStatsPy>()?;
    m.add_function(wrap_pyfunction!(create_session_actor, m)?)?;
    m.add_function(wrap_pyfunction!(get_actor_stats, m)?)?;

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

    // JIT auto-serialization
    m.add_function(wrap_pyfunction!(extract_template_variables_py, m)?)?;
    m.add_function(wrap_pyfunction!(serialize_queryset_py, m)?)?;
    m.add_function(wrap_pyfunction!(serialize_context_py, m)?)?;

    // Fast model serialization (N+1 prevention)
    m.add_function(wrap_pyfunction!(
        model_serializer::serialize_models_fast,
        m
    )?)?;
    m.add_function(wrap_pyfunction!(
        model_serializer::serialize_models_to_list,
        m
    )?)?;

    // Tag handler registry for custom template tags (url, static, etc.)
    m.add_function(wrap_pyfunction!(
        djust_templates::registry::register_tag_handler,
        m
    )?)?;
    m.add_function(wrap_pyfunction!(
        djust_templates::registry::unregister_tag_handler,
        m
    )?)?;
    m.add_function(wrap_pyfunction!(
        djust_templates::registry::has_tag_handler,
        m
    )?)?;
    m.add_function(wrap_pyfunction!(
        djust_templates::registry::get_registered_tags,
        m
    )?)?;
    m.add_function(wrap_pyfunction!(
        djust_templates::registry::clear_tag_handlers,
        m
    )?)?;

    Ok(())
}
