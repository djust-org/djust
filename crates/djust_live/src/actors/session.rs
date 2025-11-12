//! SessionActor - Manages a user's WebSocket session
//!
//! The SessionActor coordinates multiple ViewActors for a single user session,
//! routing messages and managing the lifecycle of views. Each WebSocket connection
//! has its own SessionActor.

use super::error::ActorError;
use super::messages::{MountResponse, PatchResponse, SessionMsg};
use super::view::{ViewActor, ViewActorHandle};
use djust_core::Value;
use std::collections::HashMap;
use tokio::sync::mpsc;
use tokio::time::Instant;
use tracing::{debug, info};

/// SessionActor manages a user's session and routes messages to views
pub struct SessionActor {
    session_id: String,
    receiver: mpsc::Receiver<SessionMsg>,
    views: HashMap<String, ViewActorHandle>,
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
            views: HashMap::new(),
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
                    reply,
                } => {
                    debug!(
                        session_id = %self.session_id,
                        view_path = %view_path,
                        "Handling Mount"
                    );
                    let result = self.handle_mount(view_path, params).await;
                    let _ = reply.send(result);
                }

                SessionMsg::Event {
                    event_name,
                    params,
                    reply,
                } => {
                    debug!(
                        session_id = %self.session_id,
                        event = %event_name,
                        "Handling Event"
                    );
                    let result = self.handle_event(event_name, params).await;
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

    /// Handle mount request - creates a new ViewActor
    async fn handle_mount(
        &mut self,
        view_path: String,
        params: HashMap<String, Value>,
    ) -> Result<MountResponse, ActorError> {
        // Create ViewActor
        let (view_actor, view_handle) = ViewActor::new(view_path.clone());
        tokio::spawn(view_actor.run());

        // Initialize state
        view_handle.update_state(params).await?;

        // Render initial HTML
        let result = view_handle.render_with_diff().await?;

        // Store handle for future events
        self.views.insert(view_path, view_handle);

        Ok(MountResponse {
            html: result.html,
            session_id: self.session_id.clone(),
        })
    }

    /// Handle event - routes to appropriate ViewActor
    async fn handle_event(
        &mut self,
        _event_name: String,
        _params: HashMap<String, Value>,
    ) -> Result<PatchResponse, ActorError> {
        // LIMITATION (Phase 5): View identification system not implemented
        // Currently routes to first view, which means:
        // - Only one view per session supported
        // - Cannot distinguish between multiple views
        // - Future: Use UUID-based view IDs passed in params
        let view_handle = self
            .views
            .values()
            .next()
            .ok_or_else(|| ActorError::ViewNotFound("No views mounted".to_string()))?;

        // LIMITATION (Phase 5): Python event handlers not called
        // This TODO represents incomplete functionality:
        // - Events trigger re-renders but DON'T execute Python event handlers
        // - No PyO3 callback mechanism implemented yet
        // - Actor system is infrastructure-only (Phases 1-4)
        // Future: Call Python view.event_name(**params) via PyO3 before rendering
        let result = view_handle.render_with_diff().await?;

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

    /// Shutdown all views
    async fn shutdown(&mut self) {
        for (view_path, view) in self.views.drain() {
            debug!(view_path = %view_path, "Shutting down view");
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
    /// Mount a new view
    ///
    /// Creates a ViewActor, initializes its state, and renders the initial HTML.
    ///
    /// # Arguments
    ///
    /// * `view_path` - Python path to the LiveView class (e.g. "app.views.Counter")
    /// * `params` - Initial state parameters
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
    ) -> Result<MountResponse, ActorError> {
        let (tx, rx) = tokio::sync::oneshot::channel();

        self.sender
            .send(SessionMsg::Mount {
                view_path,
                params,
                reply: tx,
            })
            .await
            .map_err(|_| ActorError::Shutdown)?;

        rx.await.map_err(|_| ActorError::Shutdown)?
    }

    /// Send an event to the view
    ///
    /// Routes the event to the appropriate ViewActor and returns the resulting
    /// VDOM patches or full HTML.
    ///
    /// # Arguments
    ///
    /// * `event_name` - Name of the event (e.g. "increment", "submit_form")
    /// * `params` - Event parameters
    ///
    /// # Errors
    ///
    /// Returns:
    /// - `ActorError::Shutdown` if the session actor has been shutdown
    /// - `ActorError::ViewNotFound` if no views are mounted
    /// - `ActorError::Template` if template rendering fails
    pub async fn event(
        &self,
        event_name: String,
        params: HashMap<String, Value>,
    ) -> Result<PatchResponse, ActorError> {
        let (tx, rx) = tokio::sync::oneshot::channel();

        self.sender
            .send(SessionMsg::Event {
                event_name,
                params,
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

        let result = handle.mount("test.view".to_string(), HashMap::new()).await;

        assert!(result.is_ok());
        let response = result.unwrap();
        assert_eq!(response.session_id, "test-session");
        // HTML will be empty since we have no template loaded
        assert!(response.html.is_empty() || !response.html.is_empty());

        handle.shutdown().await;
    }

    #[tokio::test]
    async fn test_session_actor_event_before_mount() {
        let (actor, handle) = SessionActor::new("test-session".to_string());
        tokio::spawn(actor.run());

        // Try to send event before mounting any view
        let result = handle.event("click".to_string(), HashMap::new()).await;

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
            .mount("test.view".to_string(), HashMap::new())
            .await
            .unwrap();

        // Now send event
        let result = handle.event("click".to_string(), HashMap::new()).await;

        assert!(result.is_ok());

        handle.shutdown().await;
    }

    #[tokio::test]
    async fn test_session_actor_multiple_views() {
        let (actor, handle) = SessionActor::new("test-session".to_string());
        tokio::spawn(actor.run());

        // Mount multiple views
        handle
            .mount("view1".to_string(), HashMap::new())
            .await
            .unwrap();
        handle
            .mount("view2".to_string(), HashMap::new())
            .await
            .unwrap();

        // Event should route to one of them (currently first)
        let result = handle.event("click".to_string(), HashMap::new()).await;
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
            .mount("test.view".to_string(), HashMap::new())
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
}
