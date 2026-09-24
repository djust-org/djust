//! Actor-based state management for djust LiveView
//!
//! This module provides a Tokio actor-based system for managing LiveView sessions.

pub mod component;
pub mod error;
pub mod messages;
pub mod session;
pub mod supervisor;
pub mod view;

// Re-exports
pub use component::{ComponentActor, ComponentActorHandle};
pub use error::ActorError;
pub use messages::*;
pub use session::{SessionActor, SessionActorHandle};
pub use supervisor::{ActorSupervisor, SupervisorStats};
pub use view::{ViewActor, ViewActorHandle};

/// Native actors can run bare Python objects without initializing Django. Once
/// the Python framework is loaded, use its single parameter boundary. Never
/// fall back after a framework validation/import/lookup failure.
pub(super) fn prepare_python_handler_call<'py>(
    py: pyo3::Python<'py>,
    handler: &pyo3::Bound<'py, pyo3::PyAny>,
    params: &pyo3::Bound<'py, pyo3::types::PyDict>,
) -> Result<
    (
        pyo3::Bound<'py, pyo3::types::PyTuple>,
        pyo3::Bound<'py, pyo3::types::PyDict>,
    ),
    ActorError,
> {
    use pyo3::types::PyAnyMethods;
    let modules = py
        .import("sys")
        .and_then(|sys| sys.getattr("modules"))
        .map_err(|_| ActorError::InvalidParameters)?;
    let module = modules
        .call_method1("get", ("djust.validation",))
        .map_err(|_| ActorError::InvalidParameters)?;
    if module.is_none() {
        if handler
            .hasattr("_djust_decorators")
            .map_err(|_| ActorError::InvalidParameters)?
        {
            return Err(ActorError::InvalidParameters);
        }
        return Ok((pyo3::types::PyTuple::empty(py), params.clone()));
    }
    module
        .getattr("actor_handler_arguments")
        .and_then(|prepare| prepare.call1((handler, params)))
        .and_then(|prepared| prepared.extract())
        .map_err(|_| ActorError::InvalidParameters)
}
