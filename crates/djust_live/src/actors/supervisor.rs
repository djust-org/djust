//! ActorSupervisor - Manages actor lifecycle, cleanup, and health monitoring
//!
//! The ActorSupervisor is responsible for:
//! - Creating and managing SessionActor instances
//! - TTL-based session cleanup (removes idle sessions)
//! - Health monitoring (pings sessions to detect failures)
//! - Providing stats and metrics

use super::session::{SessionActor, SessionActorHandle};
use dashmap::DashMap;
use std::sync::Arc;
use std::time::Duration;
use tokio::time::{interval, Instant};
use tracing::{debug, info, warn};

/// Session metadata tracked by supervisor
struct SessionInfo {
    handle: SessionActorHandle,
    _created_at: Instant, // Reserved for metrics/monitoring
    last_activity: Instant,
}

/// Manages actor lifecycle, cleanup, and restart
pub struct ActorSupervisor {
    /// Thread-safe map of session_id -> SessionInfo
    sessions: Arc<DashMap<String, SessionInfo>>,
    /// Time-to-live for idle sessions
    ttl: Duration,
}

/// Statistics about the actor system
#[derive(Debug, Clone)]
pub struct SupervisorStats {
    pub active_sessions: usize,
    pub ttl_secs: u64,
}

impl ActorSupervisor {
    /// Create a new supervisor with the given TTL
    ///
    /// # Arguments
    ///
    /// * `ttl` - Time-to-live for idle sessions. Sessions older than this will be cleaned up.
    ///
    /// # Example
    ///
    /// ```rust,ignore
    /// use std::time::Duration;
    /// use std::sync::Arc;
    ///
    /// let supervisor = Arc::new(ActorSupervisor::new(Duration::from_secs(3600)));
    /// supervisor.clone().start();
    /// ```
    pub fn new(ttl: Duration) -> Self {
        info!(ttl_secs = ttl.as_secs(), "Creating ActorSupervisor");

        ActorSupervisor {
            sessions: Arc::new(DashMap::new()),
            ttl,
        }
    }

    /// Start supervisor background tasks
    ///
    /// Spawns two tasks:
    /// - Cleanup task: Runs every 60 seconds to remove expired sessions
    /// - Health check task: Runs every 30 seconds to ping sessions
    ///
    /// This should be called once after creating the supervisor.
    pub fn start(self: Arc<Self>) {
        info!("Starting ActorSupervisor background tasks");

        // Spawn TTL cleanup task
        let cleanup_supervisor = Arc::clone(&self);
        tokio::spawn(async move {
            cleanup_supervisor.cleanup_task().await;
        });

        // Spawn health monitoring task
        let health_supervisor = Arc::clone(&self);
        tokio::spawn(async move {
            health_supervisor.health_check_task().await;
        });
    }

    /// Get or create a session actor
    ///
    /// If a session with the given ID exists, returns its handle and updates last_activity.
    /// Otherwise, creates a new SessionActor, spawns it, and returns the handle.
    ///
    /// # Arguments
    ///
    /// * `session_id` - Unique identifier for the session
    ///
    /// # Example
    ///
    /// ```rust,ignore
    /// let handle = supervisor.get_or_create_session("user-123".to_string()).await;
    /// ```
    pub async fn get_or_create_session(&self, session_id: String) -> SessionActorHandle {
        // Check if session already exists
        if let Some(mut entry) = self.sessions.get_mut(&session_id) {
            // Update last activity time
            entry.last_activity = Instant::now();
            debug!(session_id = %session_id, "Reusing existing session");
            return entry.handle.clone();
        }

        // Create new session actor
        let (actor, handle) = SessionActor::new(session_id.clone());
        tokio::spawn(actor.run());

        let now = Instant::now();
        self.sessions.insert(
            session_id.clone(),
            SessionInfo {
                handle: handle.clone(),
                _created_at: now,
                last_activity: now,
            },
        );

        info!(
            session_id = %session_id,
            total_sessions = self.sessions.len(),
            "Session created"
        );

        handle
    }

    /// Remove a session
    ///
    /// Shuts down the SessionActor and removes it from the registry.
    ///
    /// # Arguments
    ///
    /// * `session_id` - The session to remove
    pub async fn remove_session(&self, session_id: &str) {
        if let Some((_, info)) = self.sessions.remove(session_id) {
            info.handle.shutdown().await;
            info!(
                session_id = %session_id,
                remaining_sessions = self.sessions.len(),
                "Session removed"
            );
        }
    }

    /// Get supervisor statistics
    pub fn stats(&self) -> SupervisorStats {
        SupervisorStats {
            active_sessions: self.sessions.len(),
            ttl_secs: self.ttl.as_secs(),
        }
    }

    /// TTL-based cleanup task
    ///
    /// Runs every 60 seconds, checking for sessions that have exceeded their TTL
    /// based on last_activity time. Expired sessions are shut down and removed.
    async fn cleanup_task(&self) {
        let mut tick_interval = interval(Duration::from_secs(60));

        loop {
            tick_interval.tick().await;

            let now = Instant::now();
            let mut expired = Vec::new();

            // Find expired sessions
            for entry in self.sessions.iter() {
                let idle_time = now.duration_since(entry.value().last_activity);
                if idle_time > self.ttl {
                    expired.push(entry.key().clone());
                }
            }

            // Remove expired sessions
            if !expired.is_empty() {
                warn!(
                    expired_count = expired.len(),
                    ttl_secs = self.ttl.as_secs(),
                    "Cleaning up expired sessions"
                );

                for session_id in expired {
                    self.remove_session(&session_id).await;
                }
            } else if !self.sessions.is_empty() {
                debug!(
                    active_sessions = self.sessions.len(),
                    "Cleanup task completed - no expired sessions"
                );
            }
        }
    }

    /// Health check task
    ///
    /// Runs every 30 seconds, pinging all active sessions with a 5-second timeout.
    /// Sessions that fail to respond are removed.
    async fn health_check_task(&self) {
        let mut tick_interval = interval(Duration::from_secs(30));

        loop {
            tick_interval.tick().await;

            if self.sessions.is_empty() {
                continue;
            }

            debug!(
                active_sessions = self.sessions.len(),
                "Running health checks"
            );

            let mut failed = Vec::new();

            // Check health of all sessions
            for entry in self.sessions.iter() {
                let session_id = entry.key().clone();
                let handle = entry.value().handle.clone();

                // Ping with timeout
                let result = tokio::time::timeout(Duration::from_secs(5), handle.ping()).await;

                if result.is_err() || result.unwrap().is_err() {
                    warn!(
                        session_id = %session_id,
                        "Health check failed - session not responding"
                    );
                    failed.push(session_id);
                }
            }

            // Remove failed sessions
            for session_id in failed {
                self.remove_session(&session_id).await;
            }
        }
    }

    /// Graceful shutdown of all sessions
    ///
    /// Shuts down all active sessions and clears the registry.
    /// This should be called before application shutdown.
    pub async fn shutdown_all(&self) {
        info!(
            active_sessions = self.sessions.len(),
            "Shutting down all sessions"
        );

        let session_ids: Vec<String> = self.sessions.iter().map(|e| e.key().clone()).collect();

        for session_id in session_ids {
            self.remove_session(&session_id).await;
        }

        info!("All sessions shut down");
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[tokio::test]
    async fn test_supervisor_creation() {
        let supervisor = ActorSupervisor::new(Duration::from_secs(3600));
        let stats = supervisor.stats();

        assert_eq!(stats.active_sessions, 0);
        assert_eq!(stats.ttl_secs, 3600);
    }

    #[tokio::test]
    async fn test_get_or_create_session() {
        let supervisor = Arc::new(ActorSupervisor::new(Duration::from_secs(3600)));

        // Create session
        let handle1 = supervisor
            .get_or_create_session("test-session".to_string())
            .await;
        assert_eq!(supervisor.stats().active_sessions, 1);

        // Get same session
        let handle2 = supervisor
            .get_or_create_session("test-session".to_string())
            .await;
        assert_eq!(supervisor.stats().active_sessions, 1);

        // Verify same session
        assert_eq!(handle1.session_id(), handle2.session_id());

        // Cleanup
        supervisor.remove_session("test-session").await;
        assert_eq!(supervisor.stats().active_sessions, 0);
    }

    #[tokio::test]
    async fn test_multiple_sessions() {
        let supervisor = Arc::new(ActorSupervisor::new(Duration::from_secs(3600)));

        // Create multiple sessions
        for i in 0..10 {
            supervisor
                .get_or_create_session(format!("session-{i}"))
                .await;
        }

        assert_eq!(supervisor.stats().active_sessions, 10);

        // Remove all
        for i in 0..10 {
            supervisor.remove_session(&format!("session-{i}")).await;
        }

        assert_eq!(supervisor.stats().active_sessions, 0);
    }

    #[tokio::test]
    async fn test_shutdown_all() {
        let supervisor = Arc::new(ActorSupervisor::new(Duration::from_secs(3600)));

        // Create sessions
        for i in 0..5 {
            supervisor
                .get_or_create_session(format!("session-{i}"))
                .await;
        }

        assert_eq!(supervisor.stats().active_sessions, 5);

        // Shutdown all
        supervisor.shutdown_all().await;

        assert_eq!(supervisor.stats().active_sessions, 0);
    }

    #[tokio::test]
    async fn test_ttl_cleanup() {
        let supervisor = Arc::new(ActorSupervisor::new(Duration::from_millis(100)));

        // Create session
        supervisor
            .get_or_create_session("test-session".to_string())
            .await;
        assert_eq!(supervisor.stats().active_sessions, 1);

        // Wait for TTL to expire
        tokio::time::sleep(Duration::from_millis(150)).await;

        // Manually trigger cleanup (normally done by background task)
        let now = Instant::now();
        let mut expired = Vec::new();
        for entry in supervisor.sessions.iter() {
            let idle_time = now.duration_since(entry.value().last_activity);
            if idle_time > supervisor.ttl {
                expired.push(entry.key().clone());
            }
        }

        for session_id in expired {
            supervisor.remove_session(&session_id).await;
        }

        assert_eq!(supervisor.stats().active_sessions, 0);
    }
}
