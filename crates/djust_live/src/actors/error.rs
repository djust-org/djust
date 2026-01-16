//! Error types for the actor system

use thiserror::Error;

/// Errors that can occur in the actor system
#[derive(Debug, Error, Clone)]
pub enum ActorError {
    /// Actor mailbox is full (backpressure)
    #[error("Actor mailbox full")]
    MailboxFull,

    /// Actor has been shutdown
    #[error("Actor shutdown")]
    Shutdown,

    /// Template rendering error
    #[error("Template error: {0}")]
    Template(String),

    /// VDOM diffing error
    #[error("VDOM error: {0}")]
    Vdom(String),

    /// Timeout waiting for response
    #[error("Timeout waiting for response")]
    Timeout,

    /// View not found
    #[error("View not found: {0}")]
    ViewNotFound(String),

    /// Component not found (Phase 8)
    #[error("Component not found: {0}")]
    ComponentNotFound(String),

    /// Python error (Phase 5)
    #[error("Python error: {0}")]
    Python(String),

    /// Serialization error
    #[error("Serialization error: {0}")]
    Serialization(String),

    /// Generic error
    #[error("Actor error: {0}")]
    Other(String),
}

impl ActorError {
    /// Create a template error
    pub fn template<S: Into<String>>(msg: S) -> Self {
        ActorError::Template(msg.into())
    }

    /// Create a VDOM error
    pub fn vdom<S: Into<String>>(msg: S) -> Self {
        ActorError::Vdom(msg.into())
    }

    /// Create a serialization error
    pub fn serialization<S: Into<String>>(msg: S) -> Self {
        ActorError::Serialization(msg.into())
    }

    /// Create a generic error
    pub fn other<S: Into<String>>(msg: S) -> Self {
        ActorError::Other(msg.into())
    }
}

/// Type alias for Results in the actor system
pub type Result<T> = std::result::Result<T, ActorError>;

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_error_display() {
        let err = ActorError::MailboxFull;
        assert_eq!(err.to_string(), "Actor mailbox full");

        let err = ActorError::template("Invalid syntax");
        assert_eq!(err.to_string(), "Template error: Invalid syntax");

        let err = ActorError::Shutdown;
        assert_eq!(err.to_string(), "Actor shutdown");
    }

    #[test]
    fn test_error_clone() {
        let err1 = ActorError::Timeout;
        let err2 = err1.clone();
        assert_eq!(err1.to_string(), err2.to_string());
    }
}
