//! djust - Reactive server-side rendering for Django
//!
//! This is the main crate that ties together templates, virtual DOM, and
//! provides Python bindings for reactive server-side rendering.

// PyResult type annotations are required by PyO3 API
#![allow(clippy::useless_conversion)]
// Parameter only used in recursion for Python value conversion
#![allow(clippy::only_used_in_recursion)]

// Actor system module
pub mod actors;

use actors::{ActorSupervisor, SessionActorHandle};
use dashmap::DashMap;
use djust_core::{Context, Value};
use djust_templates::Template;
use djust_vdom::{diff, parse_html, VNode};
use once_cell::sync::Lazy;
use pyo3::prelude::*;
use pyo3::types::{PyBytes, PyDict, PyList};
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
static SUPERVISOR: Lazy<Arc<ActorSupervisor>> = Lazy::new(|| {
    let supervisor = Arc::new(ActorSupervisor::new(Duration::from_secs(3600)));
    supervisor
});

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
            timestamp: 0.0, // Will be set on first serialization
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
        Ok(PyBytes::new_bound(py, &bytes).into())
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
        Ok(Self {
            template_source: serializable.template_source,
            state: serializable.state,
            last_vdom: serializable.last_vdom,
            version: serializable.version,
            timestamp: serializable.timestamp,
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
        Self::new(template_source)
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
                let dict = PyDict::new_bound(py);
                dict.set_item("html", result.html)?;
                dict.set_item("session_id", result.session_id)?;
                dict.set_item("view_id", result.view_id)?; // Phase 6: Return view_id
                Ok(dict.into_py(py))
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
                let dict = PyDict::new_bound(py);

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
                Ok(dict.into_py(py))
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
            Ok(Py::new(py, SessionActorHandlePy { handle })?.into_py(py))
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

    Err(PyErr::new::<pyo3::exceptions::PyTypeError, _>(format!(
        "Cannot convert Python type to Value"
    )))
}

/// Python module
#[pymodule]
fn _rust(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add_class::<RustLiveViewBackend>()?;
    m.add_function(wrap_pyfunction!(render_template, m)?)?;
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

    Ok(())
}
