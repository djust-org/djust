//! Error types for djust

use pyo3::exceptions::PyRuntimeError;
use pyo3::prelude::*;
use thiserror::Error;

pub type Result<T> = std::result::Result<T, DjangoRustError>;

#[derive(Error, Debug)]
pub enum DjangoRustError {
    #[error("Template error: {0}")]
    TemplateError(String),

    /// Django's `VariableDoesNotExist`, kept as its own variant because ONE
    /// construct treats it differently from every other render error (#2328).
    ///
    /// `django.template.defaulttags.IfNode.render` wraps its condition in
    /// `except VariableDoesNotExist: match = None` — so `{% if p|f:missing %}`
    /// takes the false branch, while `{{ p|f:missing }}`, `{% for %}` and
    /// `{% with %}` all propagate. It does NOT catch the `ValueError` from an
    /// unparseable argument, so a single "template error" kind cannot express
    /// the distinction and `{% if %}` would swallow real failures with it.
    ///
    /// Crosses to Python as a `RuntimeError` like every other variant; the
    /// distinction is consumed inside the renderer.
    #[error("Template error: {0}")]
    VariableDoesNotExist(String),

    #[error("Context error: {0}")]
    ContextError(String),

    #[error("Serialization error: {0}")]
    SerializationError(String),

    #[error("VDOM error: {0}")]
    VdomError(String),

    #[error("WebSocket error: {0}")]
    WebSocketError(String),

    #[error("Python error: {0}")]
    PythonError(String),

    #[error("IO error: {0}")]
    IoError(#[from] std::io::Error),
}

impl From<DjangoRustError> for PyErr {
    fn from(err: DjangoRustError) -> PyErr {
        PyRuntimeError::new_err(err.to_string())
    }
}

impl From<PyErr> for DjangoRustError {
    fn from(err: PyErr) -> Self {
        DjangoRustError::PythonError(err.to_string())
    }
}
