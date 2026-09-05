//! Error types for djust

use pyo3::exceptions::PyRuntimeError;
use pyo3::prelude::*;
use thiserror::Error;

pub type Result<T> = std::result::Result<T, DjangoRustError>;

#[derive(Error, Debug)]
pub enum DjangoRustError {
    #[error("Template error: {0}")]
    TemplateError(String),

    /// A syntax refusal discovered while resolving a template's origin.
    #[error("Template error: {0}")]
    TemplateSyntax(String),

    #[error("Template error: {message}")]
    TemplateSyntaxAt {
        message: String,
        start: usize,
        end: usize,
    },

    /// Locate a runtime failure without changing its exception category.
    #[error("{error}")]
    RuntimeAt {
        error: Box<DjangoRustError>,
        start: usize,
        end: usize,
    },

    /// Source provenance for an error raised while compiling or rendering a template.
    #[error("{error}")]
    TemplateSource {
        error: Box<DjangoRustError>,
        template_source: String,
        origin: String,
    },

    /// A failed filesystem lookup, retaining the paths actually searched.
    /// Keep metadata separate from display text so Python need not parse paths.
    #[error("Template error: Template not found: {name}\nSearched in:\n{}", tried.iter().map(|path| format!("  - {path}")).collect::<Vec<_>>().join("\n"))]
    TemplateNotFound { name: String, tried: Vec<String> },

    /// Lookup exhausted after excluding sources already used by inheritance.
    #[error("Template error: Template not found: {name}")]
    TemplateHistoryNotFound {
        name: String,
        tried: Vec<String>,
        skipped: Vec<String>,
    },

    /// Django Engine.select_template reports candidate names without origins.
    #[error("Template error: Template not found: {name}")]
    TemplateSelectionNotFound { name: String },

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
            DjangoRustError::RuntimeAt { error, start, end } => {
                let error: PyErr = (*error).into();
                Python::attach(|py| {
                    let value = error.value(py);
                    if !value.hasattr("djust_token_span").unwrap_or(false) {
                        let _ = value.setattr("djust_token_span", (start, end));
                    }
                });
                error
            }
            DjangoRustError::TemplateSource {
                error,
                template_source,
                origin,
            } => {
                let error: PyErr = (*error).into();
                Python::attach(|py| {
                    let value = error.value(py);
                    if !value.hasattr("djust_template_source").unwrap_or(false) {
                        let _ = value.setattr("djust_template_source", template_source);
                        let _ = value.setattr("djust_template_origin", origin);
                    }
                });
                error
            }

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
            DjangoRustError::TemplateSyntax(ref message)
            | DjangoRustError::TemplateSyntaxAt { ref message, .. } => {
                let error = PyRuntimeError::new_err(err.to_string());
                let span = err.span();
                Python::attach(|py| {
                    let _ = error
                        .value(py)
                        .setattr("djust_template_syntax_message", message);
                    if let Some(span) = span {
                        let _ = error.value(py).setattr("djust_token_span", span);
                    }
                });
                error
            }
            DjangoRustError::TemplateNotFound {
                ref name,
                ref tried,
            }
            | DjangoRustError::TemplateHistoryNotFound {
                ref name,
                ref tried,
                ..
            } => {
                let error = PyRuntimeError::new_err(err.to_string());
                Python::attach(|py| {
                    let value = error.value(py);
                    let _ = value.setattr("djust_missing_template_name", name);
                    let _ = value.setattr("djust_tried_template_paths", tried.clone());
                    if let DjangoRustError::TemplateHistoryNotFound { ref skipped, .. } = err {
                        let _ = value.setattr("djust_skipped_template_paths", skipped.clone());
                    }
                });
                error
            }
            DjangoRustError::TemplateSelectionNotFound { name } => {
                let error =
                    PyRuntimeError::new_err(format!("Template error: Template not found: {name}"));
                Python::attach(|py| {
                    let _ = error.value(py).setattr("djust_missing_template_name", name);
                });
                error
            }
            other => {
                let error = PyRuntimeError::new_err(other.to_string());
                if let Some(span) = other.span() {
                    Python::attach(|py| {
                        let _ = error.value(py).setattr("djust_token_span", span);
                    });
                }
                error
            }
        }
    }
}

impl DjangoRustError {
    /// Keep loaded-template source beside the error; never replace an inner origin.
    pub fn with_template_source(self, source: &str, origin: &str) -> Self {
        if matches!(self, Self::TemplateSource { .. }) {
            self
        } else {
            Self::TemplateSource {
                error: Box::new(self),
                template_source: source.to_string(),
                origin: origin.to_string(),
            }
        }
    }

    /// Classify parser failures at a loader boundary without changing user exceptions.
    pub fn into_template_syntax(self) -> Self {
        match self {
            Self::TemplateSource {
                error,
                template_source,
                origin,
            } => Self::TemplateSource {
                error: Box::new(error.into_template_syntax()),
                template_source,
                origin,
            },
            Self::TemplateError(message) => Self::TemplateSyntax(message),
            Self::TemplateErrorAt {
                message,
                start,
                end,
            } => Self::TemplateSyntaxAt {
                message,
                start,
                end,
            },
            other => other,
        }
    }

    /// The byte span of the offending token, when this error knows one (#2557).
    pub fn span(&self) -> Option<(usize, usize)> {
        match self {
            Self::TemplateSource { error, .. } => error.span(),
            Self::RuntimeAt { start, end, .. } => Some((*start, *end)),
            DjangoRustError::TemplateErrorAt { start, end, .. }
            | DjangoRustError::TemplateSyntaxAt { start, end, .. } => Some((*start, *end)),
            _ => None,
        }
    }

    /// Locate an error without replacing an existing location or discarding
    /// its exception category.
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
            (
                other @ (Self::TemplateSource { .. }
                | Self::RuntimeAt { .. }
                | Self::TemplateErrorAt { .. }
                | Self::TemplateSyntaxAt { .. }),
                _,
            ) => other,
            (other, Some((start, end))) => Self::RuntimeAt {
                error: Box::new(other),
                start,
                end,
            },
            (other, _) => other,
        }
    }
}

impl From<PyErr> for DjangoRustError {
    fn from(err: PyErr) -> Self {
        DjangoRustError::PythonError(err.to_string())
    }
}
