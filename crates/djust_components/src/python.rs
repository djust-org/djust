/*!
PyO3 bindings for Python integration.

Exposes Rust components to Python with a Pythonic API.
*/

use pyo3::prelude::*;
use pyo3::types::PyDict;
use crate::ui::{Button, button::*};
use crate::{Component, Framework};

/// Python wrapper for Button component
#[pyclass(name = "RustButton")]
pub struct PyButton {
    inner: Button,
}

#[pymethods]
impl PyButton {
    /// Create a new button
    #[new]
    #[pyo3(signature = (id, label, **kwargs))]
    fn new(py: Python, id: String, label: String, kwargs: Option<Bound<'_, PyDict>>) -> PyResult<Self> {
        let mut button = Button::new(id, label);

        // Process kwargs if provided
        if let Some(kw) = kwargs {
            if let Ok(Some(variant)) = kw.get_item("variant") {
                if let Ok(v) = variant.extract::<String>() {
                    button.set_variant(parse_variant(&v));
                }
            }

            if let Ok(Some(size)) = kw.get_item("size") {
                if let Ok(s) = size.extract::<String>() {
                    button.size = parse_size(&s);
                }
            }

            if let Ok(Some(outline)) = kw.get_item("outline") {
                if let Ok(o) = outline.extract::<bool>() {
                    button.outline = o;
                }
            }

            if let Ok(Some(disabled)) = kw.get_item("disabled") {
                if let Ok(d) = disabled.extract::<bool>() {
                    button.disabled = d;
                }
            }

            if let Ok(Some(full_width)) = kw.get_item("full_width") {
                if let Ok(fw) = full_width.extract::<bool>() {
                    button.full_width = fw;
                }
            }

            if let Ok(Some(icon)) = kw.get_item("icon") {
                if let Ok(i) = icon.extract::<String>() {
                    button.icon = Some(i);
                }
            }

            if let Ok(Some(on_click)) = kw.get_item("on_click") {
                if let Ok(handler) = on_click.extract::<String>() {
                    button.on_click = Some(handler);
                }
            }
        }

        Ok(PyButton { inner: button })
    }

    /// Get component ID
    #[getter]
    fn id(&self) -> String {
        self.inner.id().to_string()
    }

    /// Get/set label
    #[getter]
    fn label(&self) -> String {
        self.inner.label.clone()
    }

    #[setter]
    fn set_label(&mut self, label: String) {
        self.inner.set_label(label);
    }

    /// Get/set disabled
    #[getter]
    fn disabled(&self) -> bool {
        self.inner.disabled
    }

    #[setter]
    fn set_disabled(&mut self, disabled: bool) {
        self.inner.set_disabled(disabled);
    }

    /// Set variant
    fn variant(&mut self, variant: String) -> PyResult<()> {
        self.inner.set_variant(parse_variant(&variant));
        Ok(())
    }

    /// Render to HTML (auto-detects framework from config)
    fn render(&self) -> PyResult<String> {
        // Default to Bootstrap5 for now
        // TODO: Get framework from djust config
        self.inner.render(Framework::Bootstrap5)
            .map_err(|e| PyErr::new::<pyo3::exceptions::PyRuntimeError, _>(format!("{}", e)))
    }

    /// Render with specific framework
    fn render_with_framework(&self, framework: String) -> PyResult<String> {
        let fw = Framework::from_str(&framework);
        self.inner.render(fw)
            .map_err(|e| PyErr::new::<pyo3::exceptions::PyRuntimeError, _>(format!("{}", e)))
    }

    /// Builder pattern - return self for chaining
    fn with_variant(mut slf: PyRefMut<Self>, variant: String) -> PyRefMut<Self> {
        slf.inner.set_variant(parse_variant(&variant));
        slf
    }

    fn with_size(mut slf: PyRefMut<Self>, size: String) -> PyRefMut<Self> {
        slf.inner.size = parse_size(&size);
        slf
    }

    fn with_outline(mut slf: PyRefMut<Self>, outline: bool) -> PyRefMut<Self> {
        slf.inner.outline = outline;
        slf
    }

    fn with_disabled(mut slf: PyRefMut<Self>, disabled: bool) -> PyRefMut<Self> {
        slf.inner.disabled = disabled;
        slf
    }

    fn with_icon(mut slf: PyRefMut<Self>, icon: String) -> PyRefMut<Self> {
        slf.inner.icon = Some(icon);
        slf
    }

    fn with_on_click(mut slf: PyRefMut<Self>, handler: String) -> PyRefMut<Self> {
        slf.inner.on_click = Some(handler);
        slf
    }

    fn __repr__(&self) -> String {
        format!("<RustButton id='{}' label='{}'>", self.inner.id(), self.inner.label)
    }
}

/// Parse variant string to enum
fn parse_variant(s: &str) -> ButtonVariant {
    match s.to_lowercase().as_str() {
        "primary" => ButtonVariant::Primary,
        "secondary" => ButtonVariant::Secondary,
        "success" => ButtonVariant::Success,
        "danger" => ButtonVariant::Danger,
        "warning" => ButtonVariant::Warning,
        "info" => ButtonVariant::Info,
        "light" => ButtonVariant::Light,
        "dark" => ButtonVariant::Dark,
        "link" => ButtonVariant::Link,
        _ => ButtonVariant::Primary,
    }
}

/// Parse size string to enum
fn parse_size(s: &str) -> ButtonSize {
    match s.to_lowercase().as_str() {
        "sm" | "small" => ButtonSize::Small,
        "lg" | "large" => ButtonSize::Large,
        _ => ButtonSize::Medium,
    }
}

/// Register Python module
#[pymodule]
pub fn _rust_components(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add_class::<PyButton>()?;
    Ok(())
}
