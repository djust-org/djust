//! ViewActor - Manages a single LiveView instance's state and rendering
//!
//! The ViewActor owns a RustLiveViewBackend and processes messages to update
//! state and render HTML with VDOM diffs. Each LiveView instance has its own
//! ViewActor, providing isolated state and concurrent rendering.

use super::component::{ComponentActor, ComponentActorHandle};
use super::error::ActorError;
use super::messages::{RenderResult, ViewMsg};
use crate::RustLiveViewBackend;
use djust_core::Value;
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
        let (tx, rx) = mpsc::channel(50); // Bounded channel for backpressure

        info!(view_path = %view_path, "Creating ViewActor");

        // LIMITATION: Backend created with empty template
        // This will fail on first render attempt. Templates should be:
        // - Passed in constructor, OR
        // - Loaded via separate set_template() method
        // Current design assumes template will be set before rendering (not enforced)
        let backend = RustLiveViewBackend::new_rust(String::new());

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

    /// Handle UpdateState message
    fn handle_update_state(
        &mut self,
        updates: HashMap<String, Value>,
        reply: tokio::sync::oneshot::Sender<Result<(), ActorError>>,
    ) {
        self.backend.update_state_rust(updates);
        let _ = reply.send(Ok(()));
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
        Python::with_gil(|py| {
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

        Python::with_gil(|py| {
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

            let context_dict = context_dict.downcast::<PyDict>().map_err(|e| {
                ActorError::Python(format!("get_context_data() did not return dict: {e}"))
            })?;

            // Convert to HashMap and update backend
            let mut state = HashMap::new();
            for (key, value) in context_dict.iter() {
                let key_str: String = key.extract().map_err(|e| {
                    ActorError::Python(format!("Failed to extract key as string: {e}"))
                })?;

                let rust_value = Value::extract_bound(&value).map_err(|e| {
                    ActorError::Python(format!("Failed to convert value for key '{key_str}': {e}"))
                })?;

                state.insert(key_str, rust_value);
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
            Ok((actor, handle)) => {
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
        Python::with_gil(|py| {
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
}
