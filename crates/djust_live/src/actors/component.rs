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
use djust_vdom::{diff, parse_html, Patch, VNode};
use std::collections::HashMap;
use std::sync::Arc;
use tokio::sync::mpsc;
use tracing::{debug, info};

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

    /// Shutdown this component
    Shutdown,
}

/// ComponentActor manages a single LiveComponent instance
pub struct ComponentActor {
    /// Unique identifier for this component
    component_id: String,
    /// Template string for rendering
    template_string: String,
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
    ///
    /// # Returns
    ///
    /// Returns the actor and a handle for sending messages.
    /// The actor should be spawned with `tokio::spawn(actor.run())`.
    pub fn new(
        component_id: String,
        template_string: String,
        initial_props: HashMap<String, Value>,
    ) -> Result<(Self, ComponentActorHandle), ActorError> {
        // Parse template once
        let template = Template::new(&template_string)
            .map_err(|e| ActorError::Template(format!("Failed to parse template: {}", e)))?;

        let (tx, rx) = mpsc::channel(20); // Smaller capacity for components

        info!(
            component_id = %component_id,
            "Creating ComponentActor"
        );

        let actor = ComponentActor {
            component_id: component_id.clone(),
            template_string,
            template: Arc::new(template),
            state: initial_props,
            last_vdom: None,
            version: 0,
            receiver: rx,
            python_component: None,
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
                        "SendToParent (not yet implemented)"
                    );
                    // TODO: Phase 8.2 - Forward to parent ViewActor
                }

                ComponentMsg::Render { reply } => {
                    debug!(component_id = %self.component_id, "Handling Render");
                    let result = self.render();
                    let _ = reply.send(result);
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

    /// Handle event within component
    async fn handle_event(
        &mut self,
        event_name: String,
        params: HashMap<String, Value>,
    ) -> Result<String, ActorError> {
        // TODO: Phase 8.2 - Call Python event handler if available
        if let Some(ref py_component) = self.python_component {
            // Call Python handler
            // For now, just re-render
            debug!(
                component_id = %self.component_id,
                event = %event_name,
                "Python event handler not yet implemented"
            );
        }

        // Update state with event params (simplified for now)
        self.state.extend(params);

        // Re-render after state change
        self.render()
    }

    /// Render component with current state
    fn render(&mut self) -> Result<String, ActorError> {
        // Create context from state
        let context = Context::from_dict(self.state.clone());

        // Render template
        let html = self
            .template
            .render(&context)
            .map_err(|e| ActorError::Template(format!("Render failed: {}", e)))?;

        // Parse to VDOM
        let new_vdom = parse_html(&html)
            .map_err(|e| ActorError::Vdom(format!("Failed to parse HTML: {}", e)))?;

        // Store for future diffs
        self.last_vdom = Some(new_vdom);
        self.version += 1;

        Ok(html)
    }
}

impl ComponentActorHandle {
    /// Update component props
    pub async fn update_props(
        &self,
        props: HashMap<String, Value>,
    ) -> Result<String, ActorError> {
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

        let result = ComponentActor::new("test-comp".to_string(), template, props);
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

        let (actor, handle) = ComponentActor::new("test-comp".to_string(), template, props).unwrap();
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

        let (actor, handle) = ComponentActor::new("test-comp".to_string(), template, props).unwrap();
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

        let (actor, handle) = ComponentActor::new("test-comp".to_string(), template, props).unwrap();
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

        let (actor, handle) = ComponentActor::new("test-comp".to_string(), template, props).unwrap();
        tokio::spawn(actor.run());

        // Should not panic or block
        handle
            .send_to_parent("child_event".to_string(), HashMap::new())
            .await;

        handle.shutdown().await;
    }
}
