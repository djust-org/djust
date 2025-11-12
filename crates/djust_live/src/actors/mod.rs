//! Actor-based state management for djust LiveView
//!
//! This module provides a Tokio actor-based system for managing LiveView sessions.

pub mod error;
pub mod messages;
pub mod session;
pub mod view;

// Re-exports
pub use error::ActorError;
pub use messages::*;
pub use session::{SessionActor, SessionActorHandle};
pub use view::{ViewActor, ViewActorHandle};
