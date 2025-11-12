//! ViewActor - Manages a single LiveView instance's state and rendering
//!
//! The ViewActor owns a RustLiveViewBackend and processes messages to update
//! state and render HTML with VDOM diffs. Each LiveView instance has its own
//! ViewActor, providing isolated state and concurrent rendering.

use super::error::ActorError;
use super::messages::{RenderResult, ViewMsg};
use crate::RustLiveViewBackend;
use djust_core::Value;
use std::collections::HashMap;
use tokio::sync::mpsc;
use tracing::{debug, info};

/// ViewActor manages a LiveView instance's state and rendering
pub struct ViewActor {
    view_path: String,
    receiver: mpsc::Receiver<ViewMsg>,
    backend: RustLiveViewBackend,
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
            backend,
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

                ViewMsg::Reset => {
                    debug!(view_path = %self.view_path, "Reset");
                    self.backend.reset_rust();
                }

                ViewMsg::Shutdown => {
                    info!(view_path = %self.view_path, "Shutting down");
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
    pub async fn update_state(
        &self,
        updates: HashMap<String, Value>,
    ) -> Result<(), ActorError> {
        let (tx, rx) = tokio::sync::oneshot::channel();

        self.sender
            .send(ViewMsg::UpdateState {
                updates,
                reply: tx,
            })
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

    /// Reset the view's state
    ///
    /// Note: This is a fire-and-forget operation (no response).
    pub async fn reset(&self) -> Result<(), ActorError> {
        self.sender
            .send(ViewMsg::Reset)
            .await
            .map_err(|_| ActorError::Shutdown)
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
