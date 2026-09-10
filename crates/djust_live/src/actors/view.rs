//! ViewActor - Manages a single LiveView instance's state and rendering
//!
//! The ViewActor owns a RustLiveViewBackend and processes messages to update
//! state and render HTML with VDOM diffs. Each LiveView instance has its own
//! ViewActor, providing isolated state and concurrent rendering.

use super::component::{ComponentActor, ComponentActorHandle};
use super::error::ActorError;
use super::messages::{RenderResult, ViewMsg};
use crate::RustLiveViewBackend;
use djust_core::{RenderEnv, Value};
use indexmap::IndexMap;
use pyo3::prelude::*;
use pyo3::types::{PyAnyMethods, PyDict, PyDictMethods};
use std::collections::HashMap;
use tokio::sync::mpsc;
use tracing::{debug, info, warn};

/// ViewActor manages a LiveView instance's state and rendering
///
/// Phase 5: Includes Python view instance for event handler callbacks
/// Phase 8: Includes child ComponentActors for LiveComponents
pub struct ViewActor {
    view_path: String,
    receiver: mpsc::Receiver<ViewMsg>,
    sender: mpsc::Sender<ViewMsg>, // Phase 8.2: For creating child component handles
    backend: RustLiveViewBackend,
    /// Python LiveView instance for calling event handlers
    /// Set via SetPythonView message after actor creation
    python_view: Option<Py<PyAny>>,
    /// Child component actors (Phase 8)
    /// Keyed by component_id for routing messages
    components: IndexMap<String, ComponentActorHandle>,
}

/// Handle for sending messages to a ViewActor
#[derive(Clone)]
pub struct ViewActorHandle {
    sender: mpsc::Sender<ViewMsg>,
    view_path: String,
}

impl ViewActor {
    /// Create a new ViewActor with a given view path
    ///
    /// Returns the actor and a handle for sending messages.
    /// The actor should be spawned with `tokio::spawn(actor.run())`.
    ///
    /// # Arguments
    ///
    /// * `view_path` - The Python path to the LiveView class (e.g. "app.views.Counter")
    ///
    /// # Example
    ///
    /// ```rust,ignore
    /// let (actor, handle) = ViewActor::new("app.views.Counter".to_string());
    /// tokio::spawn(actor.run());
    ///
    /// // Use handle to send messages
    /// handle.update_state(updates).await?;
    /// ```
    pub fn new(view_path: String) -> (Self, ViewActorHandle) {
        // LIMITATION: Backend created with empty template
        // This renders an empty document. Templates should be:
        // - Passed in constructor (`with_template`), OR
        // - Loaded via separate set_template() method
        // The session actor's mount path still uses this constructor, so a
        // `use_actors=True` view's actor render is an empty document today
        // (the state it holds is real, the HTML is not).
        Self::with_template(view_path, String::new())
    }

    /// Create a new ViewActor whose backend renders `template_source`.
    ///
    /// The template-carrying constructor the LIMITATION note on `new` asks
    /// for; the mount path does not use it yet, so it is what the #2592 tests
    /// use to make the actor's state OBSERVABLE through a real render.
    pub fn with_template(view_path: String, template_source: String) -> (Self, ViewActorHandle) {
        Self::with_template_and_dirs(view_path, template_source, Vec::new())
    }

    /// `with_template` plus the template directories `{% include %}` /
    /// `{% extends %}` resolve against (#2599 — the session mount's shape).
    pub fn with_template_and_dirs(
        view_path: String,
        template_source: String,
        template_dirs: Vec<String>,
    ) -> (Self, ViewActorHandle) {
        let (tx, rx) = mpsc::channel(50); // Bounded channel for backpressure

        info!(view_path = %view_path, "Creating ViewActor");

        let backend = if template_dirs.is_empty() {
            RustLiveViewBackend::new_rust(template_source)
        } else {
            RustLiveViewBackend::new_rust_with_dirs(template_source, template_dirs)
        };

        let actor = ViewActor {
            view_path: view_path.clone(),
            receiver: rx,
            sender: tx.clone(), // Phase 8.2: Store sender for creating child component handles
            backend,
            python_view: None,           // Phase 5: Set via SetPythonView message
            components: IndexMap::new(), // Phase 8: Child components
        };

        let handle = ViewActorHandle {
            sender: tx,
            view_path,
        };

        (actor, handle)
    }

    /// Set this view's render environment (ADR-029, #2741) — the timezone,
    /// number formats and ADR-027 flag captured on the thread that pushed
    /// them — so every render this actor does on its tokio worker installs
    /// them instead of reading the worker's compiled defaults. Called by
    /// `SessionActor::handle_mount` before the actor is spawned; a test that
    /// builds the actor directly calls it the same way.
    pub fn set_render_env(&mut self, env: Option<RenderEnv>) {
        self.backend.set_render_env_rust(env);
    }

    /// Main actor loop - processes messages until shutdown
    ///
    /// This method runs the actor's event loop, processing messages from the
    /// channel until a `Shutdown` message is received or the channel closes.
    pub async fn run(mut self) {
        info!(view_path = %self.view_path, "ViewActor started");

        while let Some(msg) = self.receiver.recv().await {
            match msg {
                ViewMsg::UpdateState { updates, reply } => {
                    debug!(
                        view_path = %self.view_path,
                        num_updates = updates.len(),
                        "UpdateState"
                    );
                    self.handle_update_state(updates, reply);
                }

                ViewMsg::RetainStateKeys { keys, reply } => {
                    debug!(
                        view_path = %self.view_path,
                        num_keys = keys.len(),
                        "RetainStateKeys"
                    );
                    self.handle_retain_state_keys(keys, reply);
                }

                ViewMsg::Render { reply } => {
                    debug!(view_path = %self.view_path, "Render");
                    self.handle_render(reply);
                }

                ViewMsg::RenderWithDiff { reply } => {
                    debug!(view_path = %self.view_path, "RenderWithDiff");
                    self.handle_render_with_diff(reply);
                }

                ViewMsg::SetPythonView { view, reply } => {
                    debug!(view_path = %self.view_path, "SetPythonView");
                    self.handle_set_python_view(view, reply);
                }

                ViewMsg::Event {
                    event_name,
                    params,
                    reply,
                } => {
                    debug!(
                        view_path = %self.view_path,
                        event = %event_name,
                        "Event"
                    );
                    self.handle_event(event_name, params, reply);
                }

                // Phase 8: Component management messages
                ViewMsg::CreateComponent {
                    component_id,
                    template_string,
                    initial_props,
                    python_component, // Phase 8.2
                    reply,
                } => {
                    debug!(
                        view_path = %self.view_path,
                        component_id = %component_id,
                        "CreateComponent"
                    );
                    self.handle_create_component(
                        component_id,
                        template_string,
                        initial_props,
                        python_component,
                        reply,
                    )
                    .await;
                }

                ViewMsg::ComponentEvent {
                    component_id,
                    event_name,
                    params,
                    reply,
                } => {
                    debug!(
                        view_path = %self.view_path,
                        component_id = %component_id,
                        event = %event_name,
                        "ComponentEvent"
                    );
                    self.handle_component_event(component_id, event_name, params, reply)
                        .await;
                }

                ViewMsg::UpdateComponentProps {
                    component_id,
                    props,
                    reply,
                } => {
                    debug!(
                        view_path = %self.view_path,
                        component_id = %component_id,
                        "UpdateComponentProps"
                    );
                    self.handle_update_component_props(component_id, props, reply)
                        .await;
                }

                ViewMsg::RemoveComponent {
                    component_id,
                    reply,
                } => {
                    debug!(
                        view_path = %self.view_path,
                        component_id = %component_id,
                        "RemoveComponent"
                    );
                    self.handle_remove_component(component_id, reply).await;
                }

                ViewMsg::ComponentEventFromChild {
                    component_id,
                    event_name,
                    data,
                } => {
                    debug!(
                        view_path = %self.view_path,
                        component_id = %component_id,
                        event = %event_name,
                        "Received event from child component"
                    );
                    self.handle_component_event_from_child(component_id, event_name, data)
                        .await;
                }

                ViewMsg::Reset => {
                    debug!(view_path = %self.view_path, "Reset");
                    self.backend.reset_rust();
                }

                ViewMsg::Shutdown => {
                    info!(view_path = %self.view_path, "Shutting down");
                    // Shutdown all child components
                    for (component_id, component_handle) in self.components.drain(..) {
                        debug!(component_id = %component_id, "Shutting down component");
                        component_handle.shutdown().await;
                    }
                    break;
                }
            }
        }

        info!(view_path = %self.view_path, "ViewActor stopped");
    }

    /// Handle UpdateState message.
    ///
    /// A MERGE, deliberately — this is the delta entry, the actor-side
    /// `RustLiveView.update_state`. Its only production caller is the session
    /// actor's mount (`session.rs` `handle_mount`), which sends the initial
    /// context into a FRESH backend, so there is nothing to retain there. A
    /// caller that keeps an actor across syncs pairs it with
    /// `handle_retain_state_keys` (#2592), the way the bridge pairs
    /// `update_state` with `retain_state_keys` (#2564).
    fn handle_update_state(
        &mut self,
        updates: HashMap<String, Value>,
        reply: tokio::sync::oneshot::Sender<Result<(), ActorError>>,
    ) {
        self.backend.update_state_rust(updates);
        let _ = reply.send(Ok(()));
    }

    /// Handle RetainStateKeys message (#2592): drop every state key absent
    /// from `keys` and reply with the removed keys.
    fn handle_retain_state_keys(
        &mut self,
        keys: Vec<String>,
        reply: tokio::sync::oneshot::Sender<Result<Vec<String>, ActorError>>,
    ) {
        let removed = self.backend.retain_state_keys_rust(keys);
        let _ = reply.send(Ok(removed));
    }

    /// Handle Render message
    fn handle_render(&mut self, reply: tokio::sync::oneshot::Sender<Result<String, ActorError>>) {
        let result = self
            .backend
            .render_rust()
            .map_err(|e| ActorError::template(e.to_string()));
        let _ = reply.send(result);
    }

    /// Handle RenderWithDiff message
    fn handle_render_with_diff(
        &mut self,
        reply: tokio::sync::oneshot::Sender<Result<RenderResult, ActorError>>,
    ) {
        let result = self
            .backend
            .render_with_diff_rust()
            .map(|(html, patches, version)| RenderResult {
                html,
                patches,
                version,
            })
            .map_err(|e| ActorError::template(e.to_string()));

        let _ = reply.send(result);
    }

    /// Handle SetPythonView message (Phase 5)
    ///
    /// Store reference to Python LiveView instance for calling event handlers.
    fn handle_set_python_view(
        &mut self,
        view: Py<PyAny>,
        reply: tokio::sync::oneshot::Sender<Result<(), ActorError>>,
    ) {
        self.python_view = Some(view);
        let _ = reply.send(Ok(()));
    }

    /// Handle Event message (Phase 5.3)
    ///
    /// Calls Python event handler, syncs state, and renders with diff.
    /// This is the core of Phase 5 - integrating Python event handlers with actor system.
    fn handle_event(
        &mut self,
        event_name: String,
        params: HashMap<String, Value>,
        reply: tokio::sync::oneshot::Sender<Result<RenderResult, ActorError>>,
    ) {
        // Phase 5.3: Call Python event handler
        let result = self.call_python_handler(&event_name, &params);

        // If handler call succeeded, sync state and render
        let render_result = match result {
            Ok(()) => {
                // Sync state from Python to Rust backend
                if let Err(e) = self.sync_state_from_python() {
                    warn!(
                        view_path = %self.view_path,
                        error = %e,
                        "Failed to sync state from Python"
                    );
                }

                // Render with diff
                self.backend
                    .render_with_diff_rust()
                    .map(|(html, patches, version)| RenderResult {
                        html,
                        patches,
                        version,
                    })
                    .map_err(|e| ActorError::template(e.to_string()))
            }
            Err(e) => {
                // Handler call failed - still try to render current state
                warn!(
                    view_path = %self.view_path,
                    event = %event_name,
                    error = %e,
                    "Python event handler failed"
                );

                // Return error but include current rendered state
                self.backend
                    .render_with_diff_rust()
                    .map(|(html, patches, version)| RenderResult {
                        html,
                        patches,
                        version,
                    })
                    .map_err(|e| ActorError::template(e.to_string()))
            }
        };

        let _ = reply.send(render_result);
    }

    /// Call Python event handler (Phase 5.3)
    ///
    /// Calls the specified method on the Python LiveView instance with the given parameters.
    fn call_python_handler(
        &self,
        event_name: &str,
        params: &HashMap<String, Value>,
    ) -> Result<(), ActorError> {
        // If no Python view is set, return error
        let python_view = self
            .python_view
            .as_ref()
            .ok_or_else(|| ActorError::Python("No Python view set".to_string()))?;

        // Call Python handler with GIL
        Python::attach(|py| {
            let view = python_view.bind(py);

            // Get the handler method
            let handler = view.getattr(event_name).map_err(|e| {
                ActorError::Python(format!(
                    "Handler '{}' not found on {}: {}",
                    event_name, self.view_path, e
                ))
            })?;

            // Convert params to Python dict
            let params_dict = PyDict::new(py);
            for (key, value) in params {
                params_dict
                    .set_item(
                        key,
                        value.into_pyobject(py).map_err(|e| {
                            ActorError::Python(format!("Failed to convert param '{key}': {e}"))
                        })?,
                    )
                    .map_err(|e| ActorError::Python(format!("Failed to set param '{key}': {e}")))?;
            }

            // Call handler(**params)
            handler.call((), Some(&params_dict)).map_err(|e| {
                ActorError::Python(format!(
                    "Error in {}.{}(): {}",
                    self.view_path, event_name, e
                ))
            })?;

            Ok::<_, ActorError>(())
        })
    }

    /// Sync state from Python view to Rust backend (Phase 5.3)
    ///
    /// Calls get_context_data() on the Python view and updates the Rust backend state.
    fn sync_state_from_python(&mut self) -> Result<(), ActorError> {
        let python_view = match &self.python_view {
            Some(view) => view,
            None => return Ok(()), // No Python view, nothing to sync
        };

        Python::attach(|py| {
            let view = python_view.bind(py);

            // Get context_data (calls view.get_context_data())
            let context_method = view.getattr("get_context_data").map_err(|e| {
                ActorError::Python(format!(
                    "get_context_data() not found on {}: {}",
                    self.view_path, e
                ))
            })?;

            let context_dict = context_method.call0().map_err(|e| {
                ActorError::Python(format!("Error calling get_context_data(): {e}"))
            })?;

            let context_dict = context_dict.cast::<PyDict>().map_err(|e| {
                ActorError::Python(format!("get_context_data() did not return dict: {e}"))
            })?;

            // Convert to HashMap and update backend
            // Snapshotted into an owned `Vec` BEFORE any recursive
            // `value.extract::<Value>()` call (#2510 sibling). The INNER
            // conversion is already fixed (djust_core's `impl FromPyObject
            // for Value`), but this OUTER `context_dict.iter()` is still a
            // LIVE PyO3 iterator: if any value's conversion runs Python
            // that mutates `context_dict` itself, the live iterator's
            // invariant breaks regardless of the inner fix.
            let pairs: Vec<(pyo3::Bound<'_, pyo3::PyAny>, pyo3::Bound<'_, pyo3::PyAny>)> =
                context_dict.iter().collect();
            let mut state = HashMap::with_capacity(pairs.len());
            for (key, value) in pairs {
                let key_str: String = key.extract().map_err(|e| {
                    ActorError::Python(format!("Failed to extract key as string: {e}"))
                })?;

                let rust_value = value.extract::<Value>().map_err(|e| {
                    ActorError::Python(format!("Failed to convert value for key '{key_str}': {e}"))
                })?;

                state.insert(key_str, rust_value);
            }

            // Full-context truth (#2592, the actor twin of #2564). `state` IS
            // the whole `get_context_data()`, so every backend key it lacks is
            // a key the view stopped carrying — and `update_state_rust`
            // merges, so it cannot see an absence: `del self.secret` (or the
            // `if self.show: ctx["secret"] = …` gate flipping closed) kept
            // the last value in the actor's state. Retain BEFORE the merge,
            // exactly as the bridge does (`rust_bridge.py`). The removed keys
            // also join any pending changed set inside `retain_state_keys_rust`.
            //
            // No `static_assigns` exemption here, unlike the bridge: the flag
            // that makes `get_context_data` skip them (`_static_assigns_sent`)
            // is set only by `_sync_state_to_rust`, which the actor path never
            // runs, so this context always carries them.
            let removed = self
                .backend
                .retain_state_keys_rust(state.keys().cloned().collect());
            if !removed.is_empty() {
                debug!(
                    view_path = %self.view_path,
                    removed = ?removed,
                    "Dropped state keys absent from the context"
                );
            }
            self.backend.update_state_rust(state);
            Ok::<_, ActorError>(())
        })
    }

    // ========================================================================
    // Phase 8: Component Management Methods
    // ========================================================================

    /// Handle CreateComponent message (Phase 8.2: Added python_component)
    ///
    /// Creates a new ComponentActor, spawns it, and stores the handle.
    async fn handle_create_component(
        &mut self,
        component_id: String,
        template_string: String,
        initial_props: HashMap<String, Value>,
        python_component: Option<Py<PyAny>>, // Phase 8.2
        reply: tokio::sync::oneshot::Sender<Result<String, ActorError>>,
    ) {
        // Create parent handle for SendToParent (Phase 8.2)
        let parent_handle = ViewActorHandle {
            sender: self.sender.clone(),
            view_path: self.view_path.clone(),
        };

        // Create ComponentActor with parent handle
        let result = ComponentActor::new(
            component_id.clone(),
            template_string,
            initial_props,
            Some(parent_handle),
        );

        let response = match result {
            Ok((mut actor, handle)) => {
                // ADR-029 (#2741): a component render applies the SAME
                // per-view config as its parent — the ADR-024 auto-call
                // flag and the render environment — instead of a bare
                // `Context::from_dict` that reads every default.
                actor.set_render_config(
                    self.backend.template_auto_call_rust(),
                    self.backend.render_env_rust().cloned(),
                );
                // Spawn the component actor
                tokio::spawn(actor.run());

                // Phase 8.2: Set Python component instance if provided
                if let Some(py_component) = python_component {
                    if let Err(e) = handle.set_python_component(py_component).await {
                        warn!(
                            view_path = %self.view_path,
                            component_id = %component_id,
                            error = %e,
                            "Failed to set Python component instance"
                        );
                    }
                }

                // Get initial rendered HTML
                let html_result = handle.render().await;

                // Store the handle
                self.components.insert(component_id.clone(), handle);

                html_result
            }
            Err(e) => Err(e),
        };

        let _ = reply.send(response);
    }

    /// Handle ComponentEvent message (Phase 8)
    ///
    /// Routes an event to a specific child component.
    async fn handle_component_event(
        &mut self,
        component_id: String,
        event_name: String,
        params: HashMap<String, Value>,
        reply: tokio::sync::oneshot::Sender<Result<String, ActorError>>,
    ) {
        // Look up component handle
        let result = match self.components.get(&component_id) {
            Some(handle) => {
                // Forward event to component
                handle.event(event_name, params).await
            }
            None => Err(ActorError::ComponentNotFound(format!(
                "Component '{}' not found in view '{}'",
                component_id, self.view_path
            ))),
        };

        let _ = reply.send(result);
    }

    /// Handle UpdateComponentProps message (Phase 8)
    ///
    /// Updates props for a specific child component.
    async fn handle_update_component_props(
        &mut self,
        component_id: String,
        props: HashMap<String, Value>,
        reply: tokio::sync::oneshot::Sender<Result<String, ActorError>>,
    ) {
        // Look up component handle
        let result = match self.components.get(&component_id) {
            Some(handle) => {
                // Update component props
                handle.update_props(props).await
            }
            None => Err(ActorError::ComponentNotFound(format!(
                "Component '{}' not found in view '{}'",
                component_id, self.view_path
            ))),
        };

        let _ = reply.send(result);
    }

    /// Handle RemoveComponent message (Phase 8)
    ///
    /// Removes a child component and shuts it down.
    async fn handle_remove_component(
        &mut self,
        component_id: String,
        reply: tokio::sync::oneshot::Sender<Result<(), ActorError>>,
    ) {
        // Remove component from map
        // Use shift_remove to preserve IndexMap insertion order
        let result = match self.components.shift_remove(&component_id) {
            Some(handle) => {
                // Shutdown the component
                handle.shutdown().await;
                Ok(())
            }
            None => Err(ActorError::ComponentNotFound(format!(
                "Component '{}' not found in view '{}'",
                component_id, self.view_path
            ))),
        };

        let _ = reply.send(result);
    }

    /// Handle ComponentEventFromChild message (Phase 8.2)
    ///
    /// Called when a child component sends an event to its parent via send_parent().
    /// If a Python view is set, tries to call handle_component_event() method.
    async fn handle_component_event_from_child(
        &mut self,
        component_id: String,
        event_name: String,
        data: HashMap<String, Value>,
    ) {
        // If no Python view is set, just log and ignore
        let python_view = match &self.python_view {
            Some(view) => view,
            None => {
                debug!(
                    view_path = %self.view_path,
                    component_id = %component_id,
                    event = %event_name,
                    "No Python view set, ignoring component event"
                );
                return;
            }
        };

        // Try to call handle_component_event(component_id, event_name, data) on Python view
        Python::attach(|py| {
            let view = python_view.bind(py);

            // Try to get handle_component_event method
            match view.getattr("handle_component_event") {
                Ok(handler) => {
                    // Convert data to Python dict
                    let data_dict = PyDict::new(py);

                    // Populate dict
                    for (key, value) in &data {
                        let py_value = match value.into_pyobject(py) {
                            Ok(v) => v,
                            Err(e) => {
                                warn!(
                                    view_path = %self.view_path,
                                    component_id = %component_id,
                                    error = %e,
                                    "Failed to convert value to Python"
                                );
                                return;
                            }
                        };
                        if let Err(e) = data_dict.set_item(key, py_value) {
                            warn!(
                                view_path = %self.view_path,
                                component_id = %component_id,
                                error = %e,
                                "Failed to set item in Python dict"
                            );
                            return;
                        }
                    }

                    // Call handler(component_id, event_name, data)
                    if let Err(e) =
                        handler.call1((component_id.clone(), event_name.clone(), data_dict))
                    {
                        warn!(
                            view_path = %self.view_path,
                            component_id = %component_id,
                            event = %event_name,
                            error = %e,
                            "Error calling handle_component_event"
                        );
                    } else {
                        debug!(
                            view_path = %self.view_path,
                            component_id = %component_id,
                            event = %event_name,
                            "Successfully called handle_component_event"
                        );
                    }
                }
                Err(_) => {
                    // Method doesn't exist - this is fine, component events are optional
                    debug!(
                        view_path = %self.view_path,
                        component_id = %component_id,
                        event = %event_name,
                        "No handle_component_event method, ignoring event"
                    );
                }
            }
        });
    }
}

impl ViewActorHandle {
    /// Update the view's state
    ///
    /// # Arguments
    ///
    /// * `updates` - HashMap of key-value pairs to update in the state
    ///
    /// # Errors
    ///
    /// Returns `ActorError::Shutdown` if the actor has been shutdown.
    pub async fn update_state(&self, updates: HashMap<String, Value>) -> Result<(), ActorError> {
        let (tx, rx) = tokio::sync::oneshot::channel();

        self.sender
            .send(ViewMsg::UpdateState { updates, reply: tx })
            .await
            .map_err(|_| ActorError::Shutdown)?;

        rx.await.map_err(|_| ActorError::Shutdown)?
    }

    /// Drop every state key absent from `keys`, returning the removed keys
    /// (#2592).
    ///
    /// The truth half of [`update_state`](Self::update_state), which merges
    /// and so keeps a key the caller stopped sending. Call it with the FULL
    /// key set before each `update_state` on an actor that outlives one sync.
    ///
    /// # Errors
    ///
    /// Returns `ActorError::Shutdown` if the actor has been shutdown.
    pub async fn retain_state_keys(&self, keys: Vec<String>) -> Result<Vec<String>, ActorError> {
        let (tx, rx) = tokio::sync::oneshot::channel();

        self.sender
            .send(ViewMsg::RetainStateKeys { keys, reply: tx })
            .await
            .map_err(|_| ActorError::Shutdown)?;

        rx.await.map_err(|_| ActorError::Shutdown)?
    }

    /// Render the view to HTML
    ///
    /// # Errors
    ///
    /// Returns:
    /// - `ActorError::Shutdown` if the actor has been shutdown
    /// - `ActorError::Template` if template rendering fails
    pub async fn render(&self) -> Result<String, ActorError> {
        let (tx, rx) = tokio::sync::oneshot::channel();

        self.sender
            .send(ViewMsg::Render { reply: tx })
            .await
            .map_err(|_| ActorError::Shutdown)?;

        rx.await.map_err(|_| ActorError::Shutdown)?
    }

    /// Render the view and compute VDOM diff
    ///
    /// Returns the rendered HTML, optional patches, and version number.
    ///
    /// # Errors
    ///
    /// Returns:
    /// - `ActorError::Shutdown` if the actor has been shutdown
    /// - `ActorError::Template` if template rendering fails
    pub async fn render_with_diff(&self) -> Result<RenderResult, ActorError> {
        let (tx, rx) = tokio::sync::oneshot::channel();

        self.sender
            .send(ViewMsg::RenderWithDiff { reply: tx })
            .await
            .map_err(|_| ActorError::Shutdown)?;

        rx.await.map_err(|_| ActorError::Shutdown)?
    }

    /// Set the Python view instance for event handler callbacks (Phase 5)
    ///
    /// # Arguments
    ///
    /// * `view` - Python LiveView instance
    ///
    /// # Errors
    ///
    /// Returns `ActorError::Shutdown` if the actor has been shutdown.
    pub async fn set_python_view(&self, view: Py<PyAny>) -> Result<(), ActorError> {
        let (tx, rx) = tokio::sync::oneshot::channel();

        self.sender
            .send(ViewMsg::SetPythonView { view, reply: tx })
            .await
            .map_err(|_| ActorError::Shutdown)?;

        rx.await.map_err(|_| ActorError::Shutdown)?
    }

    /// Handle an event by calling Python event handler (Phase 5)
    ///
    /// # Arguments
    ///
    /// * `event_name` - Name of the event handler method to call
    /// * `params` - Event parameters to pass to the handler
    ///
    /// # Errors
    ///
    /// Returns:
    /// - `ActorError::Shutdown` if the actor has been shutdown
    /// - `ActorError::Template` if template rendering fails
    pub async fn event(
        &self,
        event_name: String,
        params: HashMap<String, Value>,
    ) -> Result<RenderResult, ActorError> {
        let (tx, rx) = tokio::sync::oneshot::channel();

        self.sender
            .send(ViewMsg::Event {
                event_name,
                params,
                reply: tx,
            })
            .await
            .map_err(|_| ActorError::Shutdown)?;

        rx.await.map_err(|_| ActorError::Shutdown)?
    }

    /// Reset the view's state
    ///
    /// Note: This is a fire-and-forget operation (no response).
    pub async fn reset(&self) -> Result<(), ActorError> {
        self.sender
            .send(ViewMsg::Reset)
            .await
            .map_err(|_| ActorError::Shutdown)
    }

    // ========================================================================
    // Phase 8: Component Management API
    // ========================================================================

    /// Create a child ComponentActor (Phase 8.2: Added python_component)
    ///
    /// # Arguments
    ///
    /// * `component_id` - Unique identifier for this component
    /// * `template_string` - Template for rendering the component
    /// * `initial_props` - Initial component state/props
    /// * `python_component` - Optional Python component instance for event handlers (Phase 8.2)
    ///
    /// # Returns
    ///
    /// Returns the initial rendered HTML of the component.
    ///
    /// # Errors
    ///
    /// Returns:
    /// - `ActorError::Shutdown` if the actor has been shutdown
    /// - `ActorError::Template` if component creation or rendering fails
    pub async fn create_component(
        &self,
        component_id: String,
        template_string: String,
        initial_props: HashMap<String, Value>,
        python_component: Option<Py<PyAny>>, // Phase 8.2
    ) -> Result<String, ActorError> {
        let (tx, rx) = tokio::sync::oneshot::channel();

        self.sender
            .send(ViewMsg::CreateComponent {
                component_id,
                template_string,
                initial_props,
                python_component, // Phase 8.2
                reply: tx,
            })
            .await
            .map_err(|_| ActorError::Shutdown)?;

        rx.await.map_err(|_| ActorError::Shutdown)?
    }

    /// Route event to a specific child component (Phase 8)
    ///
    /// # Arguments
    ///
    /// * `component_id` - ID of the component to send event to
    /// * `event_name` - Name of the event handler to call
    /// * `params` - Event parameters
    ///
    /// # Returns
    ///
    /// Returns the rendered HTML after the component handles the event.
    ///
    /// # Errors
    ///
    /// Returns:
    /// - `ActorError::Shutdown` if the actor has been shutdown
    /// - `ActorError::NotFound` if the component doesn't exist
    /// - `ActorError::Template` if rendering fails
    pub async fn component_event(
        &self,
        component_id: String,
        event_name: String,
        params: HashMap<String, Value>,
    ) -> Result<String, ActorError> {
        let (tx, rx) = tokio::sync::oneshot::channel();

        self.sender
            .send(ViewMsg::ComponentEvent {
                component_id,
                event_name,
                params,
                reply: tx,
            })
            .await
            .map_err(|_| ActorError::Shutdown)?;

        rx.await.map_err(|_| ActorError::Shutdown)?
    }

    /// Update props for a specific child component (Phase 8)
    ///
    /// # Arguments
    ///
    /// * `component_id` - ID of the component to update
    /// * `props` - New props to merge into component state
    ///
    /// # Returns
    ///
    /// Returns the rendered HTML after updating props.
    ///
    /// # Errors
    ///
    /// Returns:
    /// - `ActorError::Shutdown` if the actor has been shutdown
    /// - `ActorError::NotFound` if the component doesn't exist
    /// - `ActorError::Template` if rendering fails
    pub async fn update_component_props(
        &self,
        component_id: String,
        props: HashMap<String, Value>,
    ) -> Result<String, ActorError> {
        let (tx, rx) = tokio::sync::oneshot::channel();

        self.sender
            .send(ViewMsg::UpdateComponentProps {
                component_id,
                props,
                reply: tx,
            })
            .await
            .map_err(|_| ActorError::Shutdown)?;

        rx.await.map_err(|_| ActorError::Shutdown)?
    }

    /// Remove a child component (Phase 8)
    ///
    /// # Arguments
    ///
    /// * `component_id` - ID of the component to remove
    ///
    /// # Errors
    ///
    /// Returns:
    /// - `ActorError::Shutdown` if the actor has been shutdown
    /// - `ActorError::NotFound` if the component doesn't exist
    pub async fn remove_component(&self, component_id: String) -> Result<(), ActorError> {
        let (tx, rx) = tokio::sync::oneshot::channel();

        self.sender
            .send(ViewMsg::RemoveComponent {
                component_id,
                reply: tx,
            })
            .await
            .map_err(|_| ActorError::Shutdown)?;

        rx.await.map_err(|_| ActorError::Shutdown)?
    }

    /// Receive event from child component (Phase 8.2)
    ///
    /// This is called by child ComponentActors when they send events to their parent.
    /// Fire-and-forget (no response) since components don't wait for parent handling.
    ///
    /// # Arguments
    ///
    /// * `component_id` - ID of the child component sending the event
    /// * `event_name` - Name of the event
    /// * `data` - Event data
    pub async fn send_component_event_from_child(
        &self,
        component_id: String,
        event_name: String,
        data: HashMap<String, Value>,
    ) {
        let _ = self
            .sender
            .send(ViewMsg::ComponentEventFromChild {
                component_id,
                event_name,
                data,
            })
            .await;
    }

    /// Shutdown the actor gracefully
    ///
    /// Note: This is a fire-and-forget operation (no response).
    pub async fn shutdown(&self) {
        let _ = self.sender.send(ViewMsg::Shutdown).await;
    }

    /// Get the view path
    pub fn view_path(&self) -> &str {
        &self.view_path
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[tokio::test]
    async fn test_view_actor_creation() {
        let (actor, handle) = ViewActor::new("test.view".to_string());
        tokio::spawn(actor.run());

        assert_eq!(handle.view_path(), "test.view");

        handle.shutdown().await;
    }

    #[tokio::test]
    async fn test_view_actor_update_state() {
        let (actor, handle) = ViewActor::new("test.view".to_string());
        tokio::spawn(actor.run());

        let mut updates = HashMap::new();
        updates.insert("count".to_string(), Value::Integer(42));

        let result = handle.update_state(updates).await;
        assert!(result.is_ok());

        handle.shutdown().await;
    }

    #[tokio::test]
    async fn test_view_actor_reset() {
        let (actor, handle) = ViewActor::new("test.view".to_string());
        tokio::spawn(actor.run());

        // Update state
        let mut updates = HashMap::new();
        updates.insert("count".to_string(), Value::Integer(42));
        handle.update_state(updates).await.unwrap();

        // Reset
        let result = handle.reset().await;
        assert!(result.is_ok());

        handle.shutdown().await;
    }

    #[tokio::test]
    async fn test_view_actor_shutdown() {
        let (actor, handle) = ViewActor::new("test.view".to_string());
        let task = tokio::spawn(actor.run());

        handle.shutdown().await;

        // Wait for actor to stop
        let _ = tokio::time::timeout(tokio::time::Duration::from_secs(1), task).await;
    }

    #[tokio::test]
    async fn test_view_actor_handle_clone() {
        let (actor, handle) = ViewActor::new("test.view".to_string());
        tokio::spawn(actor.run());

        let handle2 = handle.clone();
        assert_eq!(handle.view_path(), handle2.view_path());

        // Both handles should work
        let mut updates = HashMap::new();
        updates.insert("a".to_string(), Value::Integer(1));
        assert!(handle.update_state(updates).await.is_ok());

        let mut updates = HashMap::new();
        updates.insert("b".to_string(), Value::Integer(2));
        assert!(handle2.update_state(updates).await.is_ok());

        handle.shutdown().await;
    }

    #[tokio::test]
    async fn test_view_actor_after_shutdown() {
        let (actor, handle) = ViewActor::new("test.view".to_string());
        tokio::spawn(actor.run());

        handle.shutdown().await;

        // Give actor time to shutdown
        tokio::time::sleep(tokio::time::Duration::from_millis(100)).await;

        // Subsequent operations should fail
        let mut updates = HashMap::new();
        updates.insert("count".to_string(), Value::Integer(1));
        let result = handle.update_state(updates).await;
        assert!(result.is_err());
    }

    // ------------------------------------------------------------------
    // #2592: a key deleted from the Python context stops rendering — the
    // actor twin of #2564, against the REAL `sync_state_from_python` path
    // (a real Python object under an embedded interpreter, driven through
    // `Event`), on an actor whose backend carries a template so the state is
    // observable through a render. `with_template` exists for that: the
    // mount path's `new` renders an empty document (see the LIMITATION note).
    // ------------------------------------------------------------------

    const SECRET: &str = "SECRET-A";

    /// Compile a Python class from `body` and return one instance.
    fn python_instance(class_body: &str) -> Py<PyAny> {
        Python::initialize();
        Python::attach(|py| {
            let code = std::ffi::CString::new(class_body).unwrap();
            let module = pyo3::types::PyModule::from_code(py, &code, c"v2592.py", c"v2592")
                .expect("fixture compiles");
            module
                .getattr("V")
                .unwrap()
                .call0()
                .expect("fixture instantiates")
                .unbind()
        })
    }

    /// The #2564 delete-then-render shape: `k` is in the context only while
    /// the attribute exists. `touch` is a no-op handler so the first render
    /// goes through the same `Event → sync_state_from_python` path as the
    /// deletion does.
    fn delete_view(make_value: &str) -> Py<PyAny> {
        python_instance(&format!(
            "class V:\n\
             \x20   def __init__(self):\n\
             \x20       self.k = {make_value}\n\
             \x20   def get_context_data(self):\n\
             \x20       return {{'k': self.k}} if hasattr(self, 'k') else {{}}\n\
             \x20   def touch(self):\n\
             \x20       pass\n\
             \x20   def forget(self):\n\
             \x20       del self.k\n\
             \x20   def restore(self):\n\
             \x20       self.k = 'SECRET-B'\n"
        ))
    }

    async fn actor_with(template: &str, view: Py<PyAny>) -> ViewActorHandle {
        let (actor, handle) = ViewActor::with_template("t2592.V".to_string(), template.to_string());
        tokio::spawn(actor.run());
        handle.set_python_view(view).await.unwrap();
        handle
    }

    async fn event_html(handle: &ViewActorHandle, name: &str) -> String {
        handle
            .event(name.to_string(), HashMap::new())
            .await
            .expect("the handler exists and the render succeeds")
            .html
    }

    #[tokio::test]
    async fn a_key_deleted_from_the_python_context_stops_rendering_2592() {
        for (make_value, template) in [
            (format!("'{SECRET}'"), "{{ k }}"),
            (format!("{{'secret': '{SECRET}'}}"), "{{ k.secret }}"),
        ] {
            let handle = actor_with(template, delete_view(&make_value)).await;
            let html = event_html(&handle, "touch").await;
            assert!(
                html.contains(SECRET),
                "premise: renders while present: {html:?}"
            );

            let html = event_html(&handle, "forget").await;
            assert!(
                !html.contains(SECRET),
                "[{template}] a deleted key kept rendering its last value: {html:?}"
            );
            // The plain render agrees with the diff render.
            assert_eq!(handle.render().await.unwrap(), "");
            handle.shutdown().await;
        }
    }

    #[tokio::test]
    async fn a_key_that_comes_back_renders_again_2592() {
        let handle = actor_with("{{ k }}", delete_view(&format!("'{SECRET}'"))).await;
        event_html(&handle, "touch").await;
        event_html(&handle, "forget").await;
        assert_eq!(handle.render().await.unwrap(), "");
        let html = event_html(&handle, "restore").await;
        assert!(
            html.contains("SECRET-B"),
            "removal is not a tombstone: {html:?}"
        );
        handle.shutdown().await;
    }

    #[tokio::test]
    async fn the_if_gated_region_is_empty_after_the_gate_flips_2592() {
        let view = python_instance(&format!(
            "class V:\n\
             \x20   def __init__(self):\n\
             \x20       self.show = True\n\
             \x20   def get_context_data(self):\n\
             \x20       ctx = {{'n': 0}}\n\
             \x20       if self.show:\n\
             \x20           ctx['secret'] = '{SECRET}'\n\
             \x20       return ctx\n\
             \x20   def touch(self):\n\
             \x20       pass\n\
             \x20   def hide(self):\n\
             \x20       self.show = False\n"
        ));
        let handle = actor_with(
            "{{ n }}{% if secret %}<span>{{ secret }}</span>{% endif %}",
            view,
        )
        .await;
        let html = event_html(&handle, "touch").await;
        assert!(
            html.contains("<span"),
            "premise: gated content renders while open"
        );

        let html = event_html(&handle, "hide").await;
        assert!(
            !html.contains(SECRET),
            "the gate flipped and the content survived: {html:?}"
        );
        assert!(!html.contains("<span"), "{html:?}");
        assert!(
            html.contains('0'),
            "the surviving key still renders: {html:?}"
        );
        handle.shutdown().await;
    }

    #[tokio::test]
    async fn retain_state_keys_message_returns_the_removed_keys_2592() {
        let (actor, handle) =
            ViewActor::with_template("t2592.V".to_string(), "{{ a }}|{{ b }}".to_string());
        tokio::spawn(actor.run());
        let mut updates = HashMap::new();
        updates.insert("a".to_string(), Value::Integer(1));
        updates.insert("b".to_string(), Value::Integer(2));
        handle.update_state(updates).await.unwrap();
        assert_eq!(handle.render().await.unwrap(), "1|2");

        let removed = handle
            .retain_state_keys(vec!["b".to_string(), "never-present".to_string()])
            .await
            .unwrap();
        assert_eq!(removed, vec!["a".to_string()]);
        assert_eq!(handle.render().await.unwrap(), "|2");
        assert!(
            handle
                .retain_state_keys(vec!["b".to_string()])
                .await
                .unwrap()
                .is_empty(),
            "a second call has nothing left to drop"
        );
        handle.shutdown().await;
    }

    #[tokio::test]
    async fn update_state_stays_a_merge_2592() {
        // The delta entry keeps its contract; the truth half is a separate
        // message. A caller that wants replace semantics sends both.
        let (actor, handle) =
            ViewActor::with_template("t2592.V".to_string(), "{{ a }}|{{ b }}".to_string());
        tokio::spawn(actor.run());
        let mut first = HashMap::new();
        first.insert("a".to_string(), Value::Integer(1));
        handle.update_state(first).await.unwrap();
        let mut second = HashMap::new();
        second.insert("b".to_string(), Value::Integer(2));
        handle.update_state(second).await.unwrap();
        assert_eq!(handle.render().await.unwrap(), "1|2");
        handle.shutdown().await;
    }

    // ------------------------------------------------------------------
    // #2741 / ADR-029 — does a render on a tokio WORKER thread apply the
    // render environment the configuring thread captured, or the worker's
    // thread-local compiled default?
    //
    // `RESOLVE_LAZY` is `thread_local!` (`djust_core/src/lib.rs`) and nothing
    // in `actors/` ever pushed it, so before ADR-029 the actor rendered with
    // the worker's default whatever Python had configured (PR #2751 observed
    // it). The fix carries the environment as a FIELD on the backend
    // (`ViewActor::set_render_env`, set by `SessionActor::handle_mount` from
    // the value the Python-facing `mount` captured on its own thread) and has
    // every render entry install it under a `RenderEnvGuard`.
    //
    // The probe template renders `X` under `resolve_lazy=true` and `Y` under
    // `false` (`renderer.rs`: a `Missing` operand under `ignore_failures`
    // becomes `None` only when the flag is on, so `default_if_none` fires
    // only then) — measured through `_rust.render_template`, not cited.
    //
    // The whole difficulty is the thread. A bare `#[tokio::test]` is the
    // current_thread flavour: the actor is polled on the test thread, shares
    // its thread-local, and would "prove" a fix that reached nothing. So the
    // harness builds a multi_thread runtime with exactly ONE worker, spawns
    // the actor there, and PROVES the worker is a different OS thread by
    // running a control task on the same (only) worker that reports its
    // `ThreadId` and its own reading of `djust_core::resolve_lazy()` — before
    // AND after the render, so the guard's restore is observed too.
    // ------------------------------------------------------------------

    const RESOLVE_LAZY_PROBE: &str = r#"{% firstof nope|default_if_none:"X" "Y" %}"#;

    /// What one single-worker runtime observed: the worker's thread id, the
    /// worker's own reading of the flag before and after the render, and
    /// the actor's rendered probe.
    struct WorkerProbe {
        worker_tid: std::thread::ThreadId,
        worker_flag_before: bool,
        worker_flag_after: bool,
        html: String,
    }

    fn worker_reading(rt: &tokio::runtime::Runtime) -> (std::thread::ThreadId, bool) {
        rt.block_on(rt.spawn(async { (std::thread::current().id(), djust_core::resolve_lazy()) }))
            .unwrap()
    }

    fn probe_on_single_worker(
        on_thread_start: Option<fn()>,
        env: Option<RenderEnv>,
    ) -> WorkerProbe {
        let mut builder = tokio::runtime::Builder::new_multi_thread();
        builder.worker_threads(1).enable_all();
        if let Some(hook) = on_thread_start {
            builder.on_thread_start(hook);
        }
        let rt = builder.build().unwrap();
        assert_eq!(rt.metrics().num_workers(), 1, "harness premise: one worker");

        // Control: the only worker reports where it is and what it sees.
        let (worker_tid, worker_flag_before) = worker_reading(&rt);

        // Subject: the actor, spawned onto that same (only) worker, carrying
        // (or not) the environment the way `SessionActor::handle_mount` sets it.
        let (mut actor, handle) =
            ViewActor::with_template("t2741.V".to_string(), RESOLVE_LAZY_PROBE.to_string());
        actor.set_render_env(env);
        rt.spawn(actor.run());
        let html = rt.block_on(async {
            let html = handle.render().await.unwrap();
            handle.shutdown().await;
            html
        });
        // Control again: the render must have left the worker's cell as it
        // found it (the guard's restore), whatever it installed meanwhile.
        let (tid_after, worker_flag_after) = worker_reading(&rt);
        assert_eq!(tid_after, worker_tid, "harness premise: same single worker");
        rt.shutdown_timeout(std::time::Duration::from_secs(5));
        WorkerProbe {
            worker_tid,
            worker_flag_before,
            worker_flag_after,
            html,
        }
    }

    /// The #2751 probe, FLIPPED (ADR-029 §3 step 1): it used to pin the
    /// defect (`X` on the worker); it now asserts the CONFIGURED answer on
    /// the proven-distinct worker. The thread-distinctness harness is kept
    /// verbatim because it is the only thing that proves the fix reached the
    /// worker thread rather than the test thread.
    #[test]
    fn actor_render_on_a_worker_thread_applies_the_captured_render_env_2741() {
        // The configuring thread says `false` — the non-default value — and
        // captures it, exactly as the Python-facing `mount` does.
        djust_core::set_resolve_lazy(false);
        assert!(
            !djust_core::resolve_lazy(),
            "setter took effect on this thread"
        );
        let setter_tid = std::thread::current().id();
        let env = djust_templates::render_env::capture();
        assert!(!env.resolve_lazy, "the capture saw the configured value");

        // Distinctness is asserted, not assumed.
        let observed = probe_on_single_worker(None, Some(env.clone()));
        assert_ne!(
            observed.worker_tid, setter_tid,
            "harness premise: the worker must be a different OS thread"
        );
        // (a) the worker's OWN cell is still the compiled default — nothing
        // pushed on that thread — which is what makes (b) the field's doing:
        assert!(
            observed.worker_flag_before,
            "premise: the worker's ambient cell reads the default before the render"
        );
        // (b) the render answered with the CONFIGURED value.
        assert_eq!(
            observed.html, "Y",
            "actor render on the worker: {:?} (X = worker default true, Y = configured false)",
            observed.html
        );
        // (c) and the guard put the worker's cell back afterwards.
        assert!(
            observed.worker_flag_after,
            "the render must leave the worker's cell as it found it (RenderEnvGuard)"
        );

        // Gate-off (#1468), the mechanism this test exists for: the SAME
        // harness with no environment on the actor is the pre-ADR-029 shape
        // and must still render the worker's default. If this renders `Y`
        // too, the field is not what decided (b).
        let unconfigured = probe_on_single_worker(None, None);
        assert_ne!(unconfigured.worker_tid, setter_tid);
        assert_eq!(
            unconfigured.html, "X",
            "gate-off: with no env on the actor the worker's default must decide, got {:?}",
            unconfigured.html
        );

        // The #2751 gate-off, kept: setting the flag ON THE WORKER flips the
        // probe as well, so the harness observes the flag and not a constant.
        let flipped = probe_on_single_worker(Some(|| djust_core::set_resolve_lazy(false)), None);
        assert_ne!(flipped.worker_tid, setter_tid);
        assert!(
            !flipped.worker_flag_before,
            "on_thread_start set false on the worker"
        );
        assert_eq!(flipped.html, "Y");

        // Leave the test thread as we found it (other tests share it).
        djust_core::set_resolve_lazy(true);
    }

    /// The hand-down (ADR-029): a child `ComponentActor` created through the
    /// view renders with the PARENT's environment, on the same distinct
    /// worker. `{{ v }}` for `1234.5` is `1234,5` only under a `,`-decimal
    /// number format nobody ever pushed on the worker.
    #[test]
    fn a_child_component_inherits_the_views_render_env_2741() {
        let env = RenderEnv {
            number_format: Some(djust_core::locale::NumberFormat {
                decimal_sep: ",".into(),
                thousand_sep: "\u{a0}".into(),
                grouping: vec![3, 0],
                use_grouping: false,
            }),
            ..RenderEnv::default()
        };
        let rt = tokio::runtime::Builder::new_multi_thread()
            .worker_threads(1)
            .enable_all()
            .build()
            .unwrap();
        let setter_tid = std::thread::current().id();
        let worker_tid = rt
            .block_on(rt.spawn(async { std::thread::current().id() }))
            .unwrap();
        assert_ne!(worker_tid, setter_tid, "harness premise: a distinct worker");

        let render_child = |env: Option<RenderEnv>| {
            let (mut actor, handle) =
                ViewActor::with_template("t2741.V".to_string(), "parent".to_string());
            actor.set_render_env(env);
            rt.spawn(actor.run());
            rt.block_on(async {
                let mut props = HashMap::new();
                props.insert("v".to_string(), Value::Float(1234.5));
                let html = handle
                    .create_component("c".to_string(), "{{ v }}".to_string(), props, None)
                    .await
                    .unwrap();
                handle.shutdown().await;
                html
            })
        };
        assert_eq!(
            render_child(None),
            "1234.5",
            "premise: no env, the worker default"
        );
        assert_eq!(
            render_child(Some(env)),
            "1234,5",
            "the parent's env did not reach the child component's render"
        );
        rt.shutdown_timeout(std::time::Duration::from_secs(5));
    }
}
