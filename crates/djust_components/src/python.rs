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

/// Python wrapper for Input component
#[pyclass(name = "RustInput")]
pub struct PyInput {
    inner: crate::ui::Input,
}

#[pymethods]
impl PyInput {
    #[new]
    #[pyo3(signature = (id, **kwargs))]
    fn new(_py: Python, id: String, kwargs: Option<Bound<'_, PyDict>>) -> PyResult<Self> {
        let mut input = crate::ui::Input::new(id);

        if let Some(kw) = kwargs {
            if let Ok(Some(input_type)) = kw.get_item("inputType") {
                if let Ok(t) = input_type.extract::<String>() {
                    input.input_type = parse_input_type(&t);
                }
            }

            if let Ok(Some(size)) = kw.get_item("size") {
                if let Ok(s) = size.extract::<String>() {
                    input.size = parse_input_size(&s);
                }
            }

            if let Ok(Some(name)) = kw.get_item("name") {
                if let Ok(n) = name.extract::<String>() {
                    input.name = Some(n);
                }
            }

            if let Ok(Some(value)) = kw.get_item("value") {
                if let Ok(v) = value.extract::<String>() {
                    input.value = Some(v);
                }
            }

            if let Ok(Some(placeholder)) = kw.get_item("placeholder") {
                if let Ok(p) = placeholder.extract::<String>() {
                    input.placeholder = Some(p);
                }
            }

            if let Ok(Some(disabled)) = kw.get_item("disabled") {
                if let Ok(d) = disabled.extract::<bool>() {
                    input.disabled = d;
                }
            }

            if let Ok(Some(required)) = kw.get_item("required") {
                if let Ok(r) = required.extract::<bool>() {
                    input.required = r;
                }
            }

            if let Ok(Some(on_input)) = kw.get_item("onInput") {
                if let Ok(handler) = on_input.extract::<String>() {
                    input.on_input = Some(handler);
                }
            }

            if let Ok(Some(on_change)) = kw.get_item("onChange") {
                if let Ok(handler) = on_change.extract::<String>() {
                    input.on_change = Some(handler);
                }
            }
        }

        Ok(PyInput { inner: input })
    }

    #[getter]
    fn id(&self) -> String {
        self.inner.id().to_string()
    }

    #[getter]
    fn value(&self) -> Option<String> {
        self.inner.value.clone()
    }

    #[setter]
    fn set_value(&mut self, value: Option<String>) {
        self.inner.set_value(value);
    }

    fn render(&self) -> PyResult<String> {
        self.inner.render(crate::Framework::Bootstrap5)
            .map_err(|e| PyErr::new::<pyo3::exceptions::PyRuntimeError, _>(format!("{}", e)))
    }

    fn render_with_framework(&self, framework: String) -> PyResult<String> {
        let fw = crate::Framework::from_str(&framework);
        self.inner.render(fw)
            .map_err(|e| PyErr::new::<pyo3::exceptions::PyRuntimeError, _>(format!("{}", e)))
    }
}

fn parse_input_type(s: &str) -> crate::ui::input::InputType {
    use crate::ui::input::InputType;
    match s.to_lowercase().as_str() {
        "email" => InputType::Email,
        "password" => InputType::Password,
        "number" => InputType::Number,
        "tel" => InputType::Tel,
        "url" => InputType::Url,
        "search" => InputType::Search,
        "date" => InputType::Date,
        "time" => InputType::Time,
        "datetime" => InputType::DateTime,
        "color" => InputType::Color,
        "file" => InputType::File,
        _ => InputType::Text,
    }
}

fn parse_input_size(s: &str) -> crate::ui::input::InputSize {
    use crate::ui::input::InputSize;
    match s.to_lowercase().as_str() {
        "sm" | "small" => InputSize::Small,
        "lg" | "large" => InputSize::Large,
        _ => InputSize::Medium,
    }
}

/// Python wrapper for Text component
#[pyclass(name = "RustText")]
pub struct PyText {
    inner: crate::ui::Text,
}

#[pymethods]
impl PyText {
    #[new]
    #[pyo3(signature = (content, **kwargs))]
    fn new(_py: Python, content: String, kwargs: Option<Bound<'_, PyDict>>) -> PyResult<Self> {
        let mut text = crate::ui::Text::new(content);

        if let Some(kw) = kwargs {
            if let Ok(Some(element)) = kw.get_item("element") {
                if let Ok(e) = element.extract::<String>() {
                    text.element = parse_text_element(&e);
                }
            }

            if let Ok(Some(color)) = kw.get_item("color") {
                if let Ok(c) = color.extract::<String>() {
                    text.color = Some(parse_text_color(&c));
                }
            }

            if let Ok(Some(weight)) = kw.get_item("weight") {
                if let Ok(w) = weight.extract::<String>() {
                    text.weight = parse_font_weight(&w);
                }
            }

            if let Ok(Some(for_input)) = kw.get_item("forInput") {
                if let Ok(f) = for_input.extract::<String>() {
                    text.for_input = Some(f);
                }
            }

            if let Ok(Some(id)) = kw.get_item("id") {
                if let Ok(i) = id.extract::<String>() {
                    text.id = Some(i);
                }
            }
        }

        Ok(PyText { inner: text })
    }

    #[getter]
    fn content(&self) -> String {
        self.inner.content.clone()
    }

    #[setter]
    fn set_content(&mut self, content: String) {
        self.inner.set_content(content);
    }

    fn render(&self) -> PyResult<String> {
        self.inner.render(crate::Framework::Bootstrap5)
            .map_err(|e| PyErr::new::<pyo3::exceptions::PyRuntimeError, _>(format!("{}", e)))
    }

    fn render_with_framework(&self, framework: String) -> PyResult<String> {
        let fw = crate::Framework::from_str(&framework);
        self.inner.render(fw)
            .map_err(|e| PyErr::new::<pyo3::exceptions::PyRuntimeError, _>(format!("{}", e)))
    }
}

fn parse_text_element(s: &str) -> crate::ui::text::TextElement {
    use crate::ui::text::TextElement;
    match s.to_lowercase().as_str() {
        "p" | "paragraph" => TextElement::Paragraph,
        "span" => TextElement::Span,
        "label" => TextElement::Label,
        "div" => TextElement::Div,
        "h1" => TextElement::H1,
        "h2" => TextElement::H2,
        "h3" => TextElement::H3,
        "h4" => TextElement::H4,
        "h5" => TextElement::H5,
        "h6" => TextElement::H6,
        _ => TextElement::Span,
    }
}

fn parse_text_color(s: &str) -> crate::ui::text::TextColor {
    use crate::ui::text::TextColor;
    match s.to_lowercase().as_str() {
        "primary" => TextColor::Primary,
        "secondary" => TextColor::Secondary,
        "success" => TextColor::Success,
        "danger" => TextColor::Danger,
        "warning" => TextColor::Warning,
        "info" => TextColor::Info,
        "light" => TextColor::Light,
        "dark" => TextColor::Dark,
        "muted" => TextColor::Muted,
        _ => TextColor::Dark,
    }
}

fn parse_font_weight(s: &str) -> crate::ui::text::FontWeight {
    use crate::ui::text::FontWeight;
    match s.to_lowercase().as_str() {
        "bold" => FontWeight::Bold,
        "light" => FontWeight::Light,
        _ => FontWeight::Normal,
    }
}

/// Register Python module
#[pymodule]
pub fn _rust_components(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add_class::<PyButton>()?;
    m.add_class::<PyInput>()?;
    m.add_class::<PyText>()?;
    Ok(())
}
