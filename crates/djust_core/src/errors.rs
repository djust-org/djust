//! Error types for djust

use pyo3::exceptions::PyRuntimeError;
use pyo3::prelude::*;
use thiserror::Error;

pub type Result<T> = std::result::Result<T, DjangoRustError>;

#[derive(Error, Debug)]
pub enum DjangoRustError {
    #[error("Template error: {0}")]
    TemplateError(String),

    /// A template error that knows WHERE in the source it happened (#2557).
    ///
    /// `start`/`end` are BYTE offsets of the offending token in the template
    /// source — Django's `Token.position`, which `Template.get_exception_info`
    /// turns into the `template_debug` dict the technical-500 page renders
    /// (`name`, `line`, `during`, `source_lines`, `top`, `bottom`). Without a
    /// position, a template error reaches the developer with no location at
    /// all, which on a large template is a bisect by hand.
    ///
    /// `Display` is deliberately IDENTICAL to [`Self::TemplateError`]: the
    /// span is carried beside the message, never inside it, so every existing
    /// assertion on an error string keeps passing and the only observable
    /// difference is the extra location a caller can now ask for.
    #[error("Template error: {message}")]
    TemplateErrorAt {
        message: String,
        start: usize,
        end: usize,
    },

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

    /// A Python exception raised by user code during a template lookup,
    /// carried WHOLE rather than stringified (#2508 review).
    ///
    /// `From<PyErr>` below flattens to `PythonError(String)`, which is fine
    /// for an internal failure but destroys the type — and Django's handler
    /// chain dispatches on exactly that: `PermissionDenied` is a 403 and
    /// `Http404` is a 404, both of which arrived as an unhandled
    /// `RuntimeError` (a 500) before this variant existed. The attribute walk
    /// uses this variant so a property raising `PermissionDenied` still
    /// renders as 403.
    #[error("{0}")]
    PythonException(PyErr),

    #[error("IO error: {0}")]
    IoError(#[from] std::io::Error),
}

impl From<DjangoRustError> for PyErr {
    fn from(err: DjangoRustError) -> PyErr {
        match err {
            // Hand the caller back the exception user code actually raised,
            // so Django's handler chain still sees `PermissionDenied` /
            // `Http404` / a custom exception and dispatches on its type.
            DjangoRustError::PythonException(e) => {
                // Preserve provenance as well as identity. The Python backend
                // must not wrap an arbitrary user exception merely because
                // its class isn't one of Django's HTTP exceptions.
                Python::attach(|py| {
                    if let Ok(dict) = e.value(py).getattr("__dict__") {
                        let _ = dict.set_item("_djust_python_exception", true);
                    }
                });
                e
            }
            other => PyRuntimeError::new_err(other.to_string()),
        }
    }
}

impl DjangoRustError {
    /// The byte span of the offending token, when this error knows one (#2557).
    pub fn span(&self) -> Option<(usize, usize)> {
        match self {
            DjangoRustError::TemplateErrorAt { start, end, .. } => Some((*start, *end)),
            _ => None,
        }
    }

    /// Attach `span` to a plain [`Self::TemplateError`], leaving every other
    /// variant — an already-located error included — untouched (#2557).
    ///
    /// The "already-located wins" rule is what makes the INNERMOST enclosing
    /// token the one reported: the deepest `parse_token` frame attaches first
    /// and each outer frame declines to overwrite it, so a bad tag nested in
    /// three `{% if %}` blocks still points at the bad tag rather than at the
    /// outermost `{% if %}`.
    #[must_use]
    pub fn at(self, span: Option<(usize, usize)>) -> Self {
        match (self, span) {
            (DjangoRustError::TemplateError(message), Some((start, end))) => {
                DjangoRustError::TemplateErrorAt {
                    message,
                    start,
                    end,
                }
            }
            (DjangoRustError::PythonException(error), Some(span)) => {
                Python::attach(|py| {
                    if let Ok(dict) = error.value(py).getattr("__dict__") {
                        if dict.get_item("djust_token_span").is_err() {
                            let _ = dict.set_item("djust_token_span", span);
                        }
                    }
                });
                DjangoRustError::PythonException(error)
            }
            (other, _) => other,
        }
    }
}

impl From<PyErr> for DjangoRustError {
    fn from(err: PyErr) -> Self {
        DjangoRustError::PythonError(err.to_string())
    }
}
