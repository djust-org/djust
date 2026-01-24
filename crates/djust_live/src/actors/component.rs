//! ComponentActor - Manages individual LiveComponent instances
//!
//! ComponentActors are child actors of ViewActors, managing the state and rendering
//! of individual LiveComponent instances. They enable:
//! - Granular component-level updates (only re-render changed components)
//! - Component isolation (each component has own message queue)
//! - Parent-child communication via events
//! - Independent component lifecycles

use super::error::ActorError;
use djust_core::{Context, Value};
use djust_templates::Template;
use djust_vdom::{diff, parse_html, VNode};
use pyo3::types::{PyAnyMethods, PyDictMethods};
use pyo3::{FromPyObject, IntoPyObject};
use std::collections::HashMap;
use std::sync::Arc;
use tokio::sync::mpsc;
use tracing::{debug, info, warn};

/// Messages that ComponentActor can receive
#[derive(Debug)]
pub enum ComponentMsg {
    /// Update props from parent ViewActor
    UpdateProps {
        props: HashMap<String, Value>,
        reply: tokio::sync::oneshot::Sender<Result<String, ActorError>>,
    },

    /// Handle an event within this component
    Event {
        event_name: String,
        params: HashMap<String, Value>,
        reply: tokio::sync::oneshot::Sender<Result<String, ActorError>>,
    },

    /// Send event to parent ViewActor
    SendToParent {
        event_name: String,
        data: HashMap<String, Value>,
    },

    /// Get current rendered HTML
    Render {
        reply: tokio::sync::oneshot::Sender<Result<String, ActorError>>,
    },

    /// Set Python component instance for event handlers (Phase 8.2)
    SetPythonComponent {
        component: pyo3::Py<pyo3::PyAny>,
        reply: tokio::sync::oneshot::Sender<Result<(), ActorError>>,
    },

    /// Shutdown this component
    Shutdown,
}

/// ComponentActor manages a single LiveComponent instance
pub struct ComponentActor {
    /// Unique identifier for this component
    component_id: String,
    /// Template string for rendering
    _template_string: String, // Reserved for future use
    /// Parsed template (cached)
    template: Arc<Template>,
    /// Current component state/props
    state: HashMap<String, Value>,
    /// Last rendered VDOM (for diffing)
    last_vdom: Option<VNode>,
    /// Render version counter
    version: u64,
    /// Message receiver
    receiver: mpsc::Receiver<ComponentMsg>,
    /// Optional Python component instance for event handlers
    python_component: Option<pyo3::Py<pyo3::PyAny>>,
    /// Optional handle to parent ViewActor for SendToParent (Phase 8.2)
    parent_handle: Option<super::view::ViewActorHandle>,
}

/// Handle for sending messages to ComponentActor
#[derive(Clone)]
pub struct ComponentActorHandle {
    sender: mpsc::Sender<ComponentMsg>,
    component_id: String,
}

impl ComponentActor {
    /// Create a new ComponentActor
    ///
    /// # Arguments
    ///
    /// * `component_id` - Unique identifier for this component
    /// * `template_string` - Template for rendering
    /// * `initial_props` - Initial component state
    /// * `parent_handle` - Optional handle to parent ViewActor for SendToParent (Phase 8.2)
    ///
    /// # Returns
    ///
    /// Returns the actor and a handle for sending messages.
    /// The actor should be spawned with `tokio::spawn(actor.run())`.
    pub fn new(
        component_id: String,
        template_string: String,
        initial_props: HashMap<String, Value>,
        parent_handle: Option<super::view::ViewActorHandle>,
    ) -> Result<(Self, ComponentActorHandle), ActorError> {
        // Parse template once
        let template = Template::new(&template_string)
            .map_err(|e| ActorError::Template(format!("Failed to parse template: {e}")))?;

        let (tx, rx) = mpsc::channel(20); // Smaller capacity for components

        info!(
            component_id = %component_id,
            "Creating ComponentActor"
        );

        let actor = ComponentActor {
            component_id: component_id.clone(),
            _template_string: template_string,
            template: Arc::new(template),
            state: initial_props,
            last_vdom: None,
            version: 0,
            receiver: rx,
            python_component: None,
            parent_handle,
        };

        let handle = ComponentActorHandle {
            sender: tx,
            component_id,
        };

        Ok((actor, handle))
    }

    /// Main actor loop - processes messages until shutdown
    pub async fn run(mut self) {
        info!(component_id = %self.component_id, "ComponentActor started");

        while let Some(msg) = self.receiver.recv().await {
            match msg {
                ComponentMsg::UpdateProps { props, reply } => {
                    debug!(
                        component_id = %self.component_id,
                        "Handling UpdateProps"
                    );
                    let result = self.handle_update_props(props).await;
                    let _ = reply.send(result);
                }

                ComponentMsg::Event {
                    event_name,
                    params,
                    reply,
                } => {
                    debug!(
                        component_id = %self.component_id,
                        event = %event_name,
                        "Handling Event"
                    );
                    let result = self.handle_event(event_name, params).await;
                    let _ = reply.send(result);
                }

                ComponentMsg::SendToParent { event_name, data } => {
                    debug!(
                        component_id = %self.component_id,
                        event = %event_name,
                        "Forwarding event to parent ViewActor"
                    );
                    // Phase 8.2: Forward to parent ViewActor
                    if let Some(ref parent) = self.parent_handle {
                        let component_id = self.component_id.clone();
                        let _ = parent
                            .send_component_event_from_child(component_id, event_name, data)
                            .await;
                    } else {
                        warn!(
                            component_id = %self.component_id,
                            event = %event_name,
                            "No parent handle set, cannot forward event"
                        );
                    }
                }

                ComponentMsg::Render { reply } => {
                    debug!(component_id = %self.component_id, "Handling Render");
                    let result = self.render();
                    let _ = reply.send(result);
                }

                ComponentMsg::SetPythonComponent { component, reply } => {
                    debug!(
                        component_id = %self.component_id,
                        "Setting Python component instance"
                    );
                    self.python_component = Some(component);
                    let _ = reply.send(Ok(()));
                }

                ComponentMsg::Shutdown => {
                    info!(component_id = %self.component_id, "Shutting down");
                    break;
                }
            }
        }

        info!(component_id = %self.component_id, "ComponentActor stopped");
    }

    /// Set Python component instance for event handling
    pub fn set_python_component(&mut self, python_component: pyo3::Py<pyo3::PyAny>) {
        self.python_component = Some(python_component);
    }

    /// Handle props update from parent
    async fn handle_update_props(
        &mut self,
        props: HashMap<String, Value>,
    ) -> Result<String, ActorError> {
        // Update state with new props
        self.state.extend(props);

        // Re-render with new props
        self.render()
    }

    /// Handle event within component (Phase 8.2: Now with Python handler support)
    async fn handle_event(
        &mut self,
        event_name: String,
        params: HashMap<String, Value>,
    ) -> Result<String, ActorError> {
        // Phase 8.2: Call Python event handler if available
        let result = self.call_python_handler(&event_name, &params);

        // If handler call succeeded, sync state from Python
        if result.is_ok() {
            if let Err(e) = self.sync_state_from_python() {
                warn!(
                    component_id = %self.component_id,
                    error = %e,
                    "Failed to sync state from Python"
                );
            }
        } else {
            // No Python handler or handler failed - fall back to simple state update
            debug!(
                component_id = %self.component_id,
                event = %event_name,
                "No Python handler available, using fallback"
            );
            self.state.extend(params);
        }

        // Re-render after state change
        self.render()
    }

    /// Call Python event handler (Phase 8.2)
    ///
    /// Calls the specified method on the Python component instance with the given parameters.
    fn call_python_handler(
        &self,
        event_name: &str,
        params: &HashMap<String, Value>,
    ) -> Result<(), ActorError> {
        use pyo3::types::PyDict;
        use pyo3::Python;

        // If no Python component is set, return error (will use fallback)
        let python_component = self
            .python_component
            .as_ref()
            .ok_or_else(|| ActorError::Python("No Python component set".to_string()))?;

        // Call Python handler with GIL
        Python::with_gil(|py| {
            let component = python_component.bind(py);

            // Get the handler method
            let handler = component.getattr(event_name).map_err(|e| {
                ActorError::Python(format!(
                    "Handler '{}' not found on component '{}': {}",
                    event_name, self.component_id, e
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
                    "Error in component '{}' handler '{}': {}",
                    self.component_id, event_name, e
                ))
            })?;

            Ok::<_, ActorError>(())
        })
    }

    /// Sync state from Python component to Rust state (Phase 8.2)
    ///
    /// Calls get_context_data() on the Python component and updates the Rust state.
    fn sync_state_from_python(&mut self) -> Result<(), ActorError> {
        use pyo3::types::PyDict;
        use pyo3::Python;

        let python_component = match &self.python_component {
            Some(component) => component,
            None => return Ok(()), // No Python component, nothing to sync
        };

        Python::with_gil(|py| {
            let component = python_component.bind(py);

            // Get context_data (calls component.get_context_data())
            let context_method = component.getattr("get_context_data").map_err(|e| {
                ActorError::Python(format!(
                    "get_context_data() not found on component '{}': {}",
                    self.component_id, e
                ))
            })?;

            let context_dict = context_method.call0().map_err(|e| {
                ActorError::Python(format!("Error calling get_context_data(): {e}"))
            })?;

            let context_dict = context_dict.downcast::<PyDict>().map_err(|e| {
                ActorError::Python(format!("get_context_data() did not return dict: {e}"))
            })?;

            // Convert to HashMap and update state
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

            self.state = state;
            Ok::<_, ActorError>(())
        })
    }

    /// Render component with current state (Phase 8.2: Now with VDOM diffing)
    fn render(&mut self) -> Result<String, ActorError> {
        // Create context from state
        let context = Context::from_dict(self.state.clone());

        // Render template
        let html = self
            .template
            .render(&context)
            .map_err(|e| ActorError::Template(format!("Render failed: {e}")))?;

        // Parse to VDOM
        let new_vdom = parse_html(&html)
            .map_err(|e| ActorError::Vdom(format!("Failed to parse HTML: {e}")))?;

        // Phase 8.2: Compute VDOM diff if we have a previous render
        if let Some(ref old_vdom) = self.last_vdom {
            let patches = diff(old_vdom, &new_vdom);
            debug!(
                component_id = %self.component_id,
                version = %self.version,
                num_patches = %patches.len(),
                "Generated VDOM patches for component update"
            );
            // TODO: In future, return patches to client for efficient DOM updates
        }

        // Store for future diffs
        self.last_vdom = Some(new_vdom);
        self.version += 1;

        Ok(html)
    }
}

impl ComponentActorHandle {
    /// Update component props
    pub async fn update_props(&self, props: HashMap<String, Value>) -> Result<String, ActorError> {
        let (tx, rx) = tokio::sync::oneshot::channel();

        self.sender
            .send(ComponentMsg::UpdateProps { props, reply: tx })
            .await
            .map_err(|_| ActorError::Shutdown)?;

        rx.await.map_err(|_| ActorError::Shutdown)?
    }

    /// Send event to component
    pub async fn event(
        &self,
        event_name: String,
        params: HashMap<String, Value>,
    ) -> Result<String, ActorError> {
        let (tx, rx) = tokio::sync::oneshot::channel();

        self.sender
            .send(ComponentMsg::Event {
                event_name,
                params,
                reply: tx,
            })
            .await
            .map_err(|_| ActorError::Shutdown)?;

        rx.await.map_err(|_| ActorError::Shutdown)?
    }

    /// Get current rendered HTML
    pub async fn render(&self) -> Result<String, ActorError> {
        let (tx, rx) = tokio::sync::oneshot::channel();

        self.sender
            .send(ComponentMsg::Render { reply: tx })
            .await
            .map_err(|_| ActorError::Shutdown)?;

        rx.await.map_err(|_| ActorError::Shutdown)?
    }

    /// Set Python component instance for event handling
    pub async fn set_python_component(
        &self,
        component: pyo3::Py<pyo3::PyAny>,
    ) -> Result<(), ActorError> {
        let (tx, rx) = tokio::sync::oneshot::channel();

        self.sender
            .send(ComponentMsg::SetPythonComponent {
                component,
                reply: tx,
            })
            .await
            .map_err(|_| ActorError::Shutdown)?;

        rx.await.map_err(|_| ActorError::Shutdown)?
    }

    /// Send event to parent ViewActor
    pub async fn send_to_parent(&self, event_name: String, data: HashMap<String, Value>) {
        // Fire and forget - parent may or may not be listening
        let _ = self
            .sender
            .send(ComponentMsg::SendToParent { event_name, data })
            .await;
    }

    /// Shutdown component
    pub async fn shutdown(&self) {
        let _ = self.sender.send(ComponentMsg::Shutdown).await;
    }

    /// Get component ID
    pub fn component_id(&self) -> &str {
        &self.component_id
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[tokio::test]
    async fn test_component_actor_creation() {
        let template = "<div>{{ message }}</div>".to_string();
        let mut props = HashMap::new();
        props.insert("message".to_string(), Value::String("Hello".to_string()));

        let result = ComponentActor::new("test-comp".to_string(), template, props, None);
        assert!(result.is_ok());

        let (actor, handle) = result.unwrap();
        assert_eq!(handle.component_id(), "test-comp");

        tokio::spawn(actor.run());
        handle.shutdown().await;
    }

    #[tokio::test]
    async fn test_component_render() {
        let template = "<div>{{ message }}</div>".to_string();
        let mut props = HashMap::new();
        props.insert("message".to_string(), Value::String("Hello".to_string()));

        let (actor, handle) =
            ComponentActor::new("test-comp".to_string(), template, props, None).unwrap();
        tokio::spawn(actor.run());

        let html = handle.render().await.unwrap();
        assert!(html.contains("Hello"));

        handle.shutdown().await;
    }

    #[tokio::test]
    async fn test_component_update_props() {
        let template = "<div>{{ message }}</div>".to_string();
        let mut props = HashMap::new();
        props.insert("message".to_string(), Value::String("Hello".to_string()));

        let (actor, handle) =
            ComponentActor::new("test-comp".to_string(), template, props, None).unwrap();
        tokio::spawn(actor.run());

        // Initial render
        let html1 = handle.render().await.unwrap();
        assert!(html1.contains("Hello"));

        // Update props
        let mut new_props = HashMap::new();
        new_props.insert("message".to_string(), Value::String("Goodbye".to_string()));
        let html2 = handle.update_props(new_props).await.unwrap();
        assert!(html2.contains("Goodbye"));

        handle.shutdown().await;
    }

    #[tokio::test]
    async fn test_component_event() {
        let template = "<div>Count: {{ count }}</div>".to_string();
        let mut props = HashMap::new();
        props.insert("count".to_string(), Value::Integer(0));

        let (actor, handle) =
            ComponentActor::new("test-comp".to_string(), template, props, None).unwrap();
        tokio::spawn(actor.run());

        // Trigger event (simplified - just updates state)
        let mut params = HashMap::new();
        params.insert("count".to_string(), Value::Integer(5));
        let html = handle.event("increment".to_string(), params).await.unwrap();
        assert!(html.contains("5"));

        handle.shutdown().await;
    }

    #[tokio::test]
    async fn test_component_send_to_parent() {
        let template = "<div>{{ message }}</div>".to_string();
        let props = HashMap::new();

        let (actor, handle) =
            ComponentActor::new("test-comp".to_string(), template, props, None).unwrap();
        tokio::spawn(actor.run());

        // Should not panic or block
        handle
            .send_to_parent("child_event".to_string(), HashMap::new())
            .await;

        handle.shutdown().await;
    }
}
