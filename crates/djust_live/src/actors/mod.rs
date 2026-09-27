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

/// Drop `value` while attached to the interpreter (#3228).
///
/// Actors run on tokio workers that are not attached to Python. pyo3 cannot
/// decref a `Py<...>` dropped on such a thread: it queues the decref and
/// applies it at the next pyo3 entry on any thread, so the object outlives
/// the Rust owner for an unbounded time. Every actor-owned value that can hold
/// a `Py<...>` (a view, a component, the contract module, or a buffered
/// message carrying one) is dropped through here, so the decref, and any
/// `__del__` it triggers, happens before this returns.
///
/// `try_attach` never initializes the interpreter and refuses to attach while
/// it is finalizing. In both cases the value is dropped detached, as before:
/// no `Py<...>` exists without an interpreter, and at finalization there is
/// nothing left to release.
pub(crate) fn drop_attached<T>(value: T) {
    let mut value = Some(value);
    let _ = pyo3::Python::try_attach(|_| drop(value.take()));
    drop(value);
}

/// `map_err` target for a send to a stopped actor whose message may carry a
/// `Py<...>`: the undelivered message is dropped attached (#3228).
pub(crate) fn send_failed<T>(error: tokio::sync::mpsc::error::SendError<T>) -> ActorError {
    drop_attached(error);
    ActorError::Shutdown
}

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

#[cfg(test)]
pub(crate) mod test_support {
    use pyo3::prelude::*;
    use std::sync::atomic::{AtomicBool, Ordering};
    use std::sync::Arc;

    /// A Python object whose `__del__` sets the returned flag (#3228).
    ///
    /// Reading the flag does not enter pyo3, so a decref pyo3 queued instead
    /// of applying leaves it `false`: the flag is true only once the object
    /// has really been freed. The object has `get_context_data` so it can
    /// stand in for a view.
    pub(crate) fn released_probe() -> (Py<PyAny>, Arc<AtomicBool>) {
        Python::initialize();
        let flag = Arc::new(AtomicBool::new(false));
        let set = flag.clone();
        let probe = Python::attach(|py| {
            let module = pyo3::types::PyModule::from_code(
                py,
                c"class Probe:\n    def get_context_data(self):\n        return {}\n    def __del__(self):\n        self.on_release()\n",
                c"probe3228.py",
                c"probe3228",
            )
            .expect("probe compiles");
            let probe = module
                .getattr("Probe")
                .and_then(|cls| cls.call0())
                .expect("probe instantiates");
            let on_release = pyo3::types::PyCFunction::new_closure(
                py,
                None,
                None,
                move |_args, _kwargs| -> PyResult<()> {
                    set.store(true, Ordering::SeqCst);
                    Ok(())
                },
            )
            .expect("callback builds");
            probe
                .setattr("on_release", on_release)
                .expect("callback attaches");
            probe.unbind()
        });
        (probe, flag)
    }

    pub(crate) fn is_released(flag: &AtomicBool) -> bool {
        flag.load(Ordering::SeqCst)
    }
}
