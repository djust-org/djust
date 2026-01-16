//! SessionActor - Manages a user's WebSocket session
//!
//! The SessionActor coordinates multiple ViewActors for a single user session,
//! routing messages and managing the lifecycle of views. Each WebSocket connection
//! has its own SessionActor.

use super::error::ActorError;
use super::messages::{MountResponse, PatchResponse, SessionMsg};
use super::view::{ViewActor, ViewActorHandle};
use djust_core::Value;
use indexmap::IndexMap;
use std::collections::HashMap;
use tokio::sync::mpsc;
use tokio::time::Instant;
use tracing::{debug, info};
use uuid::Uuid;

/// SessionActor manages a user's session and routes messages to views
pub struct SessionActor {
    session_id: String,
    receiver: mpsc::Receiver<SessionMsg>,
    /// Views stored in insertion order (IndexMap) to ensure deterministic
    /// backward compatibility when routing events without explicit view_id
    views: IndexMap<String, ViewActorHandle>,
    created_at: Instant,
    last_activity: Instant,
}

/// Handle for sending messages to a SessionActor
#[derive(Clone)]
pub struct SessionActorHandle {
    sender: mpsc::Sender<SessionMsg>,
    session_id: String,
}

impl SessionActor {
    /// Create a new SessionActor for a given session ID
    ///
    /// Returns the actor and a handle for sending messages.
    /// The actor should be spawned with `tokio::spawn(actor.run())`.
    ///
    /// # Arguments
    ///
    /// * `session_id` - Unique identifier for this session (usually from WebSocket)
    ///
    /// # Example
    ///
    /// ```rust,ignore
    /// let (actor, handle) = SessionActor::new("user-session-123".to_string());
    /// tokio::spawn(actor.run());
    ///
    /// // Mount a view
    /// let response = handle.mount("app.views.Counter", HashMap::new()).await?;
    /// ```
    pub fn new(session_id: String) -> (Self, SessionActorHandle) {
        let (tx, rx) = mpsc::channel(100); // Larger capacity for session-level messages

        info!(session_id = %session_id, "Creating SessionActor");

        let now = Instant::now();
        let actor = SessionActor {
            session_id: session_id.clone(),
            receiver: rx,
            views: IndexMap::new(),
            created_at: now,
            last_activity: now,
        };

        let handle = SessionActorHandle {
            sender: tx,
            session_id,
        };

        (actor, handle)
    }

    /// Main actor loop - processes messages until shutdown
    pub async fn run(mut self) {
        info!(session_id = %self.session_id, "SessionActor started");

        while let Some(msg) = self.receiver.recv().await {
            self.last_activity = Instant::now();

            match msg {
                SessionMsg::Mount {
                    view_path,
                    params,
                    python_view,
                    reply,
                } => {
                    debug!(
                        session_id = %self.session_id,
                        view_path = %view_path,
                        "Handling Mount"
                    );
                    let result = self.handle_mount(view_path, params, python_view).await;
                    let _ = reply.send(result);
                }

                SessionMsg::Event {
                    event_name,
                    params,
                    view_id,
                    reply,
                } => {
                    debug!(
                        session_id = %self.session_id,
                        event = %event_name,
                        view_id = ?view_id,
                        "Handling Event"
                    );
                    let result = self.handle_event(event_name, params, view_id).await;
                    let _ = reply.send(result);
                }

                SessionMsg::Unmount { view_id, reply } => {
                    debug!(
                        session_id = %self.session_id,
                        view_id = %view_id,
                        "Handling Unmount"
                    );
                    let result = self.handle_unmount(view_id).await;
                    let _ = reply.send(result);
                }

                // Phase 8: Component management message handlers
                SessionMsg::CreateComponent {
                    view_id,
                    component_id,
                    template_string,
                    initial_props,
                    python_component, // Phase 8.2
                    reply,
                } => {
                    debug!(
                        session_id = %self.session_id,
                        view_id = %view_id,
                        component_id = %component_id,
                        "Handling CreateComponent"
                    );
                    let result = self
                        .handle_create_component(
                            view_id,
                            component_id,
                            template_string,
                            initial_props,
                            python_component,
                        )
                        .await;
                    let _ = reply.send(result);
                }

                SessionMsg::ComponentEvent {
                    view_id,
                    component_id,
                    event_name,
                    params,
                    reply,
                } => {
                    debug!(
                        session_id = %self.session_id,
                        view_id = %view_id,
                        component_id = %component_id,
                        event = %event_name,
                        "Handling ComponentEvent"
                    );
                    let result = self
                        .handle_component_event(view_id, component_id, event_name, params)
                        .await;
                    let _ = reply.send(result);
                }

                SessionMsg::UpdateComponentProps {
                    view_id,
                    component_id,
                    props,
                    reply,
                } => {
                    debug!(
                        session_id = %self.session_id,
                        view_id = %view_id,
                        component_id = %component_id,
                        "Handling UpdateComponentProps"
                    );
                    let result = self
                        .handle_update_component_props(view_id, component_id, props)
                        .await;
                    let _ = reply.send(result);
                }

                SessionMsg::RemoveComponent {
                    view_id,
                    component_id,
                    reply,
                } => {
                    debug!(
                        session_id = %self.session_id,
                        view_id = %view_id,
                        component_id = %component_id,
                        "Handling RemoveComponent"
                    );
                    let result = self.handle_remove_component(view_id, component_id).await;
                    let _ = reply.send(result);
                }

                SessionMsg::Ping { reply } => {
                    debug!(session_id = %self.session_id, "Ping");
                    let _ = reply.send(());
                }

                SessionMsg::Shutdown => {
                    info!(session_id = %self.session_id, "Shutting down");
                    self.shutdown().await;
                    break;
                }
            }
        }

        let lifetime_secs = self.created_at.elapsed().as_secs();
        info!(
            session_id = %self.session_id,
            lifetime_secs = lifetime_secs,
            "SessionActor stopped"
        );
    }

    /// Handle mount request - creates a new ViewActor (Phase 6: Now uses UUID)
    async fn handle_mount(
        &mut self,
        view_path: String,
        params: HashMap<String, Value>,
        python_view: Option<pyo3::Py<pyo3::PyAny>>,
    ) -> Result<MountResponse, ActorError> {
        // Phase 6: Generate unique view ID
        let view_id = Uuid::new_v4().to_string();

        info!(
            session_id = %self.session_id,
            view_id = %view_id,
            view_path = %view_path,
            "Creating new view"
        );

        // Create ViewActor
        let (view_actor, view_handle) = ViewActor::new(view_path.clone());
        tokio::spawn(view_actor.run());

        // Phase 5: Set Python view instance if provided
        if let Some(python_view) = python_view {
            view_handle.set_python_view(python_view).await?;
        }

        // Initialize state
        view_handle.update_state(params).await?;

        // Render initial HTML
        let result = view_handle.render_with_diff().await?;

        // Phase 6: Store handle with UUID key
        self.views.insert(view_id.clone(), view_handle);

        Ok(MountResponse {
            html: result.html,
            session_id: self.session_id.clone(),
            view_id,
        })
    }

    /// Handle event - routes to appropriate ViewActor (Phase 6: Now uses UUID)
    async fn handle_event(
        &mut self,
        event_name: String,
        params: HashMap<String, Value>,
        view_id: Option<String>,
    ) -> Result<PatchResponse, ActorError> {
        // Phase 6: Route by view_id
        let view_handle = if let Some(id) = view_id {
            // Explicit view_id provided - route to specific view
            self.views
                .get(&id)
                .ok_or_else(|| ActorError::ViewNotFound(format!("View not found: {id}")))?
        } else {
            // No view_id - backward compatibility: route to first view
            // This maintains Phase 5 behavior for existing code
            self.views
                .values()
                .next()
                .ok_or_else(|| ActorError::ViewNotFound("No views mounted".to_string()))?
        };

        // Phase 5: ViewActor handles event by calling Python handler
        let result = view_handle.event(event_name, params).await?;

        // Check if patches exist before moving
        let has_patches = result.patches.is_some();

        Ok(PatchResponse {
            patches: result.patches,
            html: if !has_patches {
                Some(result.html)
            } else {
                None
            },
            version: result.version,
        })
    }

    /// Handle unmount request - removes a specific view (Phase 6)
    async fn handle_unmount(&mut self, view_id: String) -> Result<(), ActorError> {
        if let Some(view_handle) = self.views.shift_remove(&view_id) {
            info!(
                session_id = %self.session_id,
                view_id = %view_id,
                "Unmounting view"
            );
            view_handle.shutdown().await;
            Ok(())
        } else {
            Err(ActorError::ViewNotFound(format!(
                "View not found: {view_id}"
            )))
        }
    }

    // ========================================================================
    // Phase 8: Component Management Handler Methods
    // ========================================================================

    /// Handle create component request (Phase 8.2: Added python_component)
    async fn handle_create_component(
        &self,
        view_id: String,
        component_id: String,
        template_string: String,
        initial_props: HashMap<String, Value>,
        python_component: Option<pyo3::Py<pyo3::PyAny>>, // Phase 8.2
    ) -> Result<String, ActorError> {
        let view_handle = self
            .views
            .get(&view_id)
            .ok_or_else(|| ActorError::ViewNotFound(format!("View not found: {view_id}")))?;

        view_handle
            .create_component(
                component_id,
                template_string,
                initial_props,
                python_component,
            )
            .await
    }

    /// Handle component event request (Phase 8)
    async fn handle_component_event(
        &self,
        view_id: String,
        component_id: String,
        event_name: String,
        params: HashMap<String, Value>,
    ) -> Result<String, ActorError> {
        let view_handle = self
            .views
            .get(&view_id)
            .ok_or_else(|| ActorError::ViewNotFound(format!("View not found: {view_id}")))?;

        view_handle
            .component_event(component_id, event_name, params)
            .await
    }

    /// Handle update component props request (Phase 8)
    async fn handle_update_component_props(
        &self,
        view_id: String,
        component_id: String,
        props: HashMap<String, Value>,
    ) -> Result<String, ActorError> {
        let view_handle = self
            .views
            .get(&view_id)
            .ok_or_else(|| ActorError::ViewNotFound(format!("View not found: {view_id}")))?;

        view_handle
            .update_component_props(component_id, props)
            .await
    }

    /// Handle remove component request (Phase 8)
    async fn handle_remove_component(
        &self,
        view_id: String,
        component_id: String,
    ) -> Result<(), ActorError> {
        let view_handle = self
            .views
            .get(&view_id)
            .ok_or_else(|| ActorError::ViewNotFound(format!("View not found: {view_id}")))?;

        view_handle.remove_component(component_id).await
    }

    /// Shutdown all views
    async fn shutdown(&mut self) {
        for (view_id, view) in self.views.drain(..) {
            debug!(view_id = %view_id, "Shutting down view");
            view.shutdown().await;
        }
    }

    /// Get session age
    pub fn age(&self) -> std::time::Duration {
        self.created_at.elapsed()
    }

    /// Get idle time
    pub fn idle_time(&self) -> std::time::Duration {
        self.last_activity.elapsed()
    }
}

impl SessionActorHandle {
    /// Mount a new view (Phase 5: Now accepts Python view instance)
    ///
    /// Creates a ViewActor, initializes its state, and renders the initial HTML.
    ///
    /// # Arguments
    ///
    /// * `view_path` - Python path to the LiveView class (e.g. "app.views.Counter")
    /// * `params` - Initial state parameters
    /// * `python_view` - Optional Python LiveView instance for event handler callbacks
    ///
    /// # Errors
    ///
    /// Returns:
    /// - `ActorError::Shutdown` if the session actor has been shutdown
    /// - `ActorError::Template` if template rendering fails
    pub async fn mount(
        &self,
        view_path: String,
        params: HashMap<String, Value>,
        python_view: Option<pyo3::Py<pyo3::PyAny>>,
    ) -> Result<MountResponse, ActorError> {
        let (tx, rx) = tokio::sync::oneshot::channel();

        self.sender
            .send(SessionMsg::Mount {
                view_path,
                params,
                python_view,
                reply: tx,
            })
            .await
            .map_err(|_| ActorError::Shutdown)?;

        rx.await.map_err(|_| ActorError::Shutdown)?
    }

    /// Send an event to the view (Phase 6: Now supports view_id)
    ///
    /// Routes the event to the appropriate ViewActor and returns the resulting
    /// VDOM patches or full HTML.
    ///
    /// # Arguments
    ///
    /// * `event_name` - Name of the event (e.g. "increment", "submit_form")
    /// * `params` - Event parameters
    /// * `view_id` - Optional view ID for routing (if None, routes to first view for backward compat)
    ///
    /// # Errors
    ///
    /// Returns:
    /// - `ActorError::Shutdown` if the session actor has been shutdown
    /// - `ActorError::ViewNotFound` if no views are mounted or view_id not found
    /// - `ActorError::Template` if template rendering fails
    pub async fn event(
        &self,
        event_name: String,
        params: HashMap<String, Value>,
        view_id: Option<String>,
    ) -> Result<PatchResponse, ActorError> {
        let (tx, rx) = tokio::sync::oneshot::channel();

        self.sender
            .send(SessionMsg::Event {
                event_name,
                params,
                view_id,
                reply: tx,
            })
            .await
            .map_err(|_| ActorError::Shutdown)?;

        rx.await.map_err(|_| ActorError::Shutdown)?
    }

    /// Unmount a specific view (Phase 6)
    ///
    /// Shuts down a specific ViewActor and removes it from the session.
    ///
    /// # Arguments
    ///
    /// * `view_id` - The UUID of the view to unmount
    ///
    /// # Errors
    ///
    /// Returns:
    /// - `ActorError::Shutdown` if the session actor has been shutdown
    /// - `ActorError::ViewNotFound` if the view_id is not found
    pub async fn unmount(&self, view_id: String) -> Result<(), ActorError> {
        let (tx, rx) = tokio::sync::oneshot::channel();

        self.sender
            .send(SessionMsg::Unmount { view_id, reply: tx })
            .await
            .map_err(|_| ActorError::Shutdown)?;

        rx.await.map_err(|_| ActorError::Shutdown)?
    }

    // ========================================================================
    // Phase 8: Component Management API
    // ========================================================================

    /// Create a component in a specific view (Phase 8)
    ///
    /// # Arguments
    ///
    /// * `view_id` - ID of the view to create the component in
    /// * `component_id` - Unique identifier for the component
    /// * `template_string` - Template for rendering the component
    /// * `initial_props` - Initial component state/props
    ///
    /// # Returns
    ///
    /// Returns the initial rendered HTML of the component.
    ///
    /// # Errors
    ///
    /// Returns:
    /// - `ActorError::Shutdown` if the session actor has been shutdown
    /// - `ActorError::ViewNotFound` if the view_id is not found
    /// - `ActorError::Template` if component creation or rendering fails
    pub async fn create_component(
        &self,
        view_id: String,
        component_id: String,
        template_string: String,
        initial_props: HashMap<String, Value>,
        python_component: Option<pyo3::Py<pyo3::PyAny>>, // Phase 8.2
    ) -> Result<String, ActorError> {
        let (tx, rx) = tokio::sync::oneshot::channel();

        self.sender
            .send(SessionMsg::CreateComponent {
                view_id,
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

    /// Route event to a specific component (Phase 8)
    ///
    /// # Arguments
    ///
    /// * `view_id` - ID of the view containing the component
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
    /// - `ActorError::Shutdown` if the session actor has been shutdown
    /// - `ActorError::ViewNotFound` if the view_id is not found
    /// - `ActorError::ComponentNotFound` if the component_id is not found
    /// - `ActorError::Template` if rendering fails
    pub async fn component_event(
        &self,
        view_id: String,
        component_id: String,
        event_name: String,
        params: HashMap<String, Value>,
    ) -> Result<String, ActorError> {
        let (tx, rx) = tokio::sync::oneshot::channel();

        self.sender
            .send(SessionMsg::ComponentEvent {
                view_id,
                component_id,
                event_name,
                params,
                reply: tx,
            })
            .await
            .map_err(|_| ActorError::Shutdown)?;

        rx.await.map_err(|_| ActorError::Shutdown)?
    }

    /// Update props for a specific component (Phase 8)
    ///
    /// # Arguments
    ///
    /// * `view_id` - ID of the view containing the component
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
    /// - `ActorError::Shutdown` if the session actor has been shutdown
    /// - `ActorError::ViewNotFound` if the view_id is not found
    /// - `ActorError::ComponentNotFound` if the component_id is not found
    /// - `ActorError::Template` if rendering fails
    pub async fn update_component_props(
        &self,
        view_id: String,
        component_id: String,
        props: HashMap<String, Value>,
    ) -> Result<String, ActorError> {
        let (tx, rx) = tokio::sync::oneshot::channel();

        self.sender
            .send(SessionMsg::UpdateComponentProps {
                view_id,
                component_id,
                props,
                reply: tx,
            })
            .await
            .map_err(|_| ActorError::Shutdown)?;

        rx.await.map_err(|_| ActorError::Shutdown)?
    }

    /// Remove a component (Phase 8)
    ///
    /// # Arguments
    ///
    /// * `view_id` - ID of the view containing the component
    /// * `component_id` - ID of the component to remove
    ///
    /// # Errors
    ///
    /// Returns:
    /// - `ActorError::Shutdown` if the session actor has been shutdown
    /// - `ActorError::ViewNotFound` if the view_id is not found
    /// - `ActorError::ComponentNotFound` if the component_id is not found
    pub async fn remove_component(
        &self,
        view_id: String,
        component_id: String,
    ) -> Result<(), ActorError> {
        let (tx, rx) = tokio::sync::oneshot::channel();

        self.sender
            .send(SessionMsg::RemoveComponent {
                view_id,
                component_id,
                reply: tx,
            })
            .await
            .map_err(|_| ActorError::Shutdown)?;

        rx.await.map_err(|_| ActorError::Shutdown)?
    }

    /// Health check ping
    ///
    /// Verifies that the session actor is still responsive.
    ///
    /// # Errors
    ///
    /// Returns `ActorError::Shutdown` if the session actor has been shutdown.
    pub async fn ping(&self) -> Result<(), ActorError> {
        let (tx, rx) = tokio::sync::oneshot::channel();

        self.sender
            .send(SessionMsg::Ping { reply: tx })
            .await
            .map_err(|_| ActorError::Shutdown)?;

        rx.await.map_err(|_| ActorError::Shutdown)?;
        Ok(())
    }

    /// Shutdown the session gracefully
    ///
    /// Shuts down all child ViewActors and then the SessionActor itself.
    pub async fn shutdown(&self) {
        let _ = self.sender.send(SessionMsg::Shutdown).await;
    }

    /// Get the session ID
    pub fn session_id(&self) -> &str {
        &self.session_id
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[tokio::test]
    async fn test_session_actor_creation() {
        let (actor, handle) = SessionActor::new("test-session".to_string());
        tokio::spawn(actor.run());

        assert_eq!(handle.session_id(), "test-session");

        handle.shutdown().await;
    }

    #[tokio::test]
    async fn test_session_actor_ping() {
        let (actor, handle) = SessionActor::new("test-session".to_string());
        tokio::spawn(actor.run());

        let result = handle.ping().await;
        assert!(result.is_ok());

        handle.shutdown().await;
    }

    #[tokio::test]
    async fn test_session_actor_mount() {
        let (actor, handle) = SessionActor::new("test-session".to_string());
        tokio::spawn(actor.run());

        let result = handle
            .mount("test.view".to_string(), HashMap::new(), None)
            .await;

        assert!(result.is_ok());
        let response = result.unwrap();
        assert_eq!(response.session_id, "test-session");
        assert!(!response.view_id.is_empty()); // Phase 6: view_id is now returned
                                               // HTML will be empty since we have no template loaded
        assert!(response.html.is_empty() || !response.html.is_empty());

        handle.shutdown().await;
    }

    #[tokio::test]
    async fn test_session_actor_event_before_mount() {
        let (actor, handle) = SessionActor::new("test-session".to_string());
        tokio::spawn(actor.run());

        // Try to send event before mounting any view
        let result = handle
            .event("click".to_string(), HashMap::new(), None)
            .await;

        // Should fail with ViewNotFound error
        assert!(result.is_err());
        if let Err(ActorError::ViewNotFound(_)) = result {
            // Expected
        } else {
            panic!("Expected ViewNotFound error");
        }

        handle.shutdown().await;
    }

    #[tokio::test]
    async fn test_session_actor_event_after_mount() {
        let (actor, handle) = SessionActor::new("test-session".to_string());
        tokio::spawn(actor.run());

        // Mount view first
        handle
            .mount("test.view".to_string(), HashMap::new(), None)
            .await
            .unwrap();

        // Now send event (backward compat: no view_id)
        let result = handle
            .event("click".to_string(), HashMap::new(), None)
            .await;

        assert!(result.is_ok());

        handle.shutdown().await;
    }

    #[tokio::test]
    async fn test_session_actor_multiple_views() {
        let (actor, handle) = SessionActor::new("test-session".to_string());
        tokio::spawn(actor.run());

        // Mount multiple views
        let view1 = handle
            .mount("view1".to_string(), HashMap::new(), None)
            .await
            .unwrap();
        let _view2 = handle
            .mount("view2".to_string(), HashMap::new(), None)
            .await
            .unwrap();

        // Phase 6: Event with explicit view_id routes to specific view
        let result = handle
            .event(
                "click".to_string(),
                HashMap::new(),
                Some(view1.view_id.clone()),
            )
            .await;
        assert!(result.is_ok());

        // Event without view_id routes to first view (backward compat)
        let result = handle
            .event("click".to_string(), HashMap::new(), None)
            .await;
        assert!(result.is_ok());

        handle.shutdown().await;
    }

    #[tokio::test]
    async fn test_session_actor_handle_clone() {
        let (actor, handle) = SessionActor::new("test-session".to_string());
        tokio::spawn(actor.run());

        let handle2 = handle.clone();
        assert_eq!(handle.session_id(), handle2.session_id());

        // Both handles should work
        assert!(handle.ping().await.is_ok());
        assert!(handle2.ping().await.is_ok());

        handle.shutdown().await;
    }

    #[tokio::test]
    async fn test_session_actor_shutdown() {
        let (actor, handle) = SessionActor::new("test-session".to_string());
        let task = tokio::spawn(actor.run());

        // Mount a view
        handle
            .mount("test.view".to_string(), HashMap::new(), None)
            .await
            .unwrap();

        handle.shutdown().await;

        // Wait for actor to stop
        let _ = tokio::time::timeout(tokio::time::Duration::from_secs(1), task).await;
    }

    #[tokio::test]
    async fn test_session_actor_after_shutdown() {
        let (actor, handle) = SessionActor::new("test-session".to_string());
        tokio::spawn(actor.run());

        handle.shutdown().await;

        // Give actor time to shutdown
        tokio::time::sleep(tokio::time::Duration::from_millis(100)).await;

        // Subsequent operations should fail
        let result = handle.ping().await;
        assert!(result.is_err());
    }

    #[tokio::test]
    async fn test_session_actor_unmount() {
        let (actor, handle) = SessionActor::new("test-session".to_string());
        tokio::spawn(actor.run());

        // Mount two views
        let view1 = handle
            .mount("view1".to_string(), HashMap::new(), None)
            .await
            .unwrap();
        let view2 = handle
            .mount("view2".to_string(), HashMap::new(), None)
            .await
            .unwrap();

        // Unmount view1
        let result = handle.unmount(view1.view_id.clone()).await;
        assert!(result.is_ok());

        // Event to view1 should fail
        let result = handle
            .event(
                "click".to_string(),
                HashMap::new(),
                Some(view1.view_id.clone()),
            )
            .await;
        assert!(result.is_err());

        // Event to view2 should still work
        let result = handle
            .event(
                "click".to_string(),
                HashMap::new(),
                Some(view2.view_id.clone()),
            )
            .await;
        assert!(result.is_ok());

        handle.shutdown().await;
    }

    #[tokio::test]
    async fn test_session_actor_unmount_nonexistent() {
        let (actor, handle) = SessionActor::new("test-session".to_string());
        tokio::spawn(actor.run());

        // Try to unmount non-existent view
        let result = handle.unmount("nonexistent-uuid".to_string()).await;
        assert!(result.is_err());
        if let Err(ActorError::ViewNotFound(_)) = result {
            // Expected
        } else {
            panic!("Expected ViewNotFound error");
        }

        handle.shutdown().await;
    }
}
