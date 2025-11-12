/*!
PyO3 bindings for Python integration.

Exposes Rust components to Python with a Pythonic API.
*/

// PyResult type annotations are required by PyO3 API - false positive warnings
#![allow(clippy::useless_conversion)]
// Complex component constructors - will refactor to builder pattern in follow-up
#![allow(clippy::too_many_arguments)]
// Nested if-let patterns are clearer for error handling
#![allow(clippy::collapsible_match)]

use crate::ui::{button::*, Button};
use crate::{Component, Framework};
use pyo3::prelude::*;
use pyo3::types::PyDict;

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
    fn new(
        _py: Python,
        id: String,
        label: String,
        kwargs: Option<Bound<'_, PyDict>>,
    ) -> PyResult<Self> {
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
        self.inner
            .render(Framework::Bootstrap5)
            .map_err(|e| PyErr::new::<pyo3::exceptions::PyRuntimeError, _>(format!("{e}")))
    }

    /// Render with specific framework
    fn render_with_framework(&self, framework: String) -> PyResult<String> {
        let fw = framework.parse().unwrap();
        self.inner
            .render(fw)
            .map_err(|e| PyErr::new::<pyo3::exceptions::PyRuntimeError, _>(format!("{e}")))
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
        format!(
            "<RustButton id='{}' label='{}'>",
            self.inner.id(),
            self.inner.label
        )
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
        self.inner
            .render(crate::Framework::Bootstrap5)
            .map_err(|e| PyErr::new::<pyo3::exceptions::PyRuntimeError, _>(format!("{e}")))
    }

    fn render_with_framework(&self, framework: String) -> PyResult<String> {
        let fw = framework.parse().unwrap();
        self.inner
            .render(fw)
            .map_err(|e| PyErr::new::<pyo3::exceptions::PyRuntimeError, _>(format!("{e}")))
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
        self.inner
            .render(crate::Framework::Bootstrap5)
            .map_err(|e| PyErr::new::<pyo3::exceptions::PyRuntimeError, _>(format!("{e}")))
    }

    fn render_with_framework(&self, framework: String) -> PyResult<String> {
        let fw = framework.parse().unwrap();
        self.inner
            .render(fw)
            .map_err(|e| PyErr::new::<pyo3::exceptions::PyRuntimeError, _>(format!("{e}")))
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

/// Python wrapper for Card component
#[pyclass(name = "RustCard")]
pub struct PyCard {
    inner: crate::ui::Card,
}

#[pymethods]
impl PyCard {
    #[new]
    #[pyo3(signature = (body, **kwargs))]
    fn new(_py: Python, body: String, kwargs: Option<Bound<'_, PyDict>>) -> PyResult<Self> {
        let mut card = crate::ui::Card::new(body);

        if let Some(kw) = kwargs {
            if let Ok(Some(variant)) = kw.get_item("variant") {
                if let Ok(v) = variant.extract::<String>() {
                    card.variant = parse_card_variant(&v);
                }
            }

            if let Ok(Some(header)) = kw.get_item("header") {
                if let Ok(h) = header.extract::<String>() {
                    card.header = Some(h);
                }
            }

            if let Ok(Some(footer)) = kw.get_item("footer") {
                if let Ok(f) = footer.extract::<String>() {
                    card.footer = Some(f);
                }
            }

            if let Ok(Some(border)) = kw.get_item("border") {
                if let Ok(b) = border.extract::<bool>() {
                    card.border = b;
                }
            }

            if let Ok(Some(shadow)) = kw.get_item("shadow") {
                if let Ok(s) = shadow.extract::<bool>() {
                    card.shadow = s;
                }
            }

            if let Ok(Some(id)) = kw.get_item("id") {
                if let Ok(i) = id.extract::<String>() {
                    card.id = Some(i);
                }
            }
        }

        Ok(PyCard { inner: card })
    }

    #[getter]
    fn body(&self) -> String {
        self.inner.body.clone()
    }

    #[setter]
    fn set_body(&mut self, body: String) {
        self.inner.body = body;
    }

    fn render(&self) -> PyResult<String> {
        self.inner
            .render(crate::Framework::Bootstrap5)
            .map_err(|e| PyErr::new::<pyo3::exceptions::PyRuntimeError, _>(format!("{e}")))
    }

    fn render_with_framework(&self, framework: String) -> PyResult<String> {
        let fw = framework.parse().unwrap();
        self.inner
            .render(fw)
            .map_err(|e| PyErr::new::<pyo3::exceptions::PyRuntimeError, _>(format!("{e}")))
    }

    fn __repr__(&self) -> String {
        "<RustCard>".to_string()
    }
}

fn parse_card_variant(s: &str) -> crate::ui::card::CardVariant {
    use crate::ui::card::CardVariant;
    match s.to_lowercase().as_str() {
        "primary" => CardVariant::Primary,
        "secondary" => CardVariant::Secondary,
        "success" => CardVariant::Success,
        "danger" => CardVariant::Danger,
        "warning" => CardVariant::Warning,
        "info" => CardVariant::Info,
        "light" => CardVariant::Light,
        "dark" => CardVariant::Dark,
        _ => CardVariant::Default,
    }
}

/// Python wrapper for Alert component
#[pyclass(name = "RustAlert")]
pub struct PyAlert {
    inner: crate::ui::Alert,
}

#[pymethods]
impl PyAlert {
    #[new]
    #[pyo3(signature = (message, **kwargs))]
    fn new(_py: Python, message: String, kwargs: Option<Bound<'_, PyDict>>) -> PyResult<Self> {
        let mut alert = crate::ui::Alert::new(message);

        if let Some(kw) = kwargs {
            if let Ok(Some(variant)) = kw.get_item("variant") {
                if let Ok(v) = variant.extract::<String>() {
                    alert.variant = parse_alert_variant(&v);
                }
            }

            if let Ok(Some(dismissible)) = kw.get_item("dismissible") {
                if let Ok(d) = dismissible.extract::<bool>() {
                    alert.dismissible = d;
                }
            }

            if let Ok(Some(icon)) = kw.get_item("icon") {
                if let Ok(i) = icon.extract::<String>() {
                    alert.icon = Some(i);
                }
            }

            if let Ok(Some(id)) = kw.get_item("id") {
                if let Ok(i) = id.extract::<String>() {
                    alert.id = Some(i);
                }
            }
        }

        Ok(PyAlert { inner: alert })
    }

    #[getter]
    fn message(&self) -> String {
        self.inner.message.clone()
    }

    #[setter]
    fn set_message(&mut self, message: String) {
        self.inner.message = message;
    }

    fn render(&self) -> PyResult<String> {
        self.inner
            .render(crate::Framework::Bootstrap5)
            .map_err(|e| PyErr::new::<pyo3::exceptions::PyRuntimeError, _>(format!("{e}")))
    }

    fn render_with_framework(&self, framework: String) -> PyResult<String> {
        let fw = framework.parse().unwrap();
        self.inner
            .render(fw)
            .map_err(|e| PyErr::new::<pyo3::exceptions::PyRuntimeError, _>(format!("{e}")))
    }

    fn __repr__(&self) -> String {
        "<RustAlert>".to_string()
    }
}

fn parse_alert_variant(s: &str) -> crate::ui::alert::AlertVariant {
    use crate::ui::alert::AlertVariant;
    match s.to_lowercase().as_str() {
        "primary" => AlertVariant::Primary,
        "secondary" => AlertVariant::Secondary,
        "success" => AlertVariant::Success,
        "danger" => AlertVariant::Danger,
        "warning" => AlertVariant::Warning,
        "info" => AlertVariant::Info,
        "light" => AlertVariant::Light,
        "dark" => AlertVariant::Dark,
        _ => AlertVariant::Info,
    }
}

/// Python wrapper for Modal component
#[pyclass(name = "RustModal")]
pub struct PyModal {
    inner: crate::ui::Modal,
}

#[pymethods]
impl PyModal {
    #[new]
    #[pyo3(signature = (id, body, **kwargs))]
    fn new(
        _py: Python,
        id: String,
        body: String,
        kwargs: Option<Bound<'_, PyDict>>,
    ) -> PyResult<Self> {
        let mut modal = crate::ui::Modal::new(id, body);

        if let Some(kw) = kwargs {
            if let Ok(Some(title)) = kw.get_item("title") {
                if let Ok(t) = title.extract::<String>() {
                    modal.title = Some(t);
                }
            }

            if let Ok(Some(footer)) = kw.get_item("footer") {
                if let Ok(f) = footer.extract::<String>() {
                    modal.footer = Some(f);
                }
            }

            if let Ok(Some(size)) = kw.get_item("size") {
                if let Ok(s) = size.extract::<String>() {
                    modal.size = parse_modal_size(&s);
                }
            }

            if let Ok(Some(centered)) = kw.get_item("centered") {
                if let Ok(c) = centered.extract::<bool>() {
                    modal.centered = c;
                }
            }

            if let Ok(Some(scrollable)) = kw.get_item("scrollable") {
                if let Ok(s) = scrollable.extract::<bool>() {
                    modal.scrollable = s;
                }
            }
        }

        Ok(PyModal { inner: modal })
    }

    #[getter]
    fn body(&self) -> String {
        self.inner.body.clone()
    }

    #[setter]
    fn set_body(&mut self, body: String) {
        self.inner.body = body;
    }

    fn render(&self) -> PyResult<String> {
        self.inner
            .render(crate::Framework::Bootstrap5)
            .map_err(|e| PyErr::new::<pyo3::exceptions::PyRuntimeError, _>(format!("{e}")))
    }

    fn render_with_framework(&self, framework: String) -> PyResult<String> {
        let fw = framework.parse().unwrap();
        self.inner
            .render(fw)
            .map_err(|e| PyErr::new::<pyo3::exceptions::PyRuntimeError, _>(format!("{e}")))
    }

    fn __repr__(&self) -> String {
        format!("<RustModal id='{}'>", self.inner.id)
    }
}

fn parse_modal_size(s: &str) -> crate::ui::modal::ModalSize {
    use crate::ui::modal::ModalSize;
    match s.to_lowercase().as_str() {
        "sm" | "small" => ModalSize::Small,
        "lg" | "large" => ModalSize::Large,
        "xl" | "extralarge" => ModalSize::ExtraLarge,
        _ => ModalSize::Medium,
    }
}

// ===== Dropdown =====

#[pyclass(name = "RustDropdown")]
pub struct PyDropdown {
    inner: crate::ui::Dropdown,
}

#[pymethods]
impl PyDropdown {
    #[new]
    #[pyo3(signature = (id, **kwargs))]
    fn new(_py: Python, id: String, kwargs: Option<Bound<'_, PyDict>>) -> PyResult<Self> {
        let mut dropdown = crate::ui::Dropdown::new(id);

        if let Some(kwargs) = kwargs {
            // items: list of dicts with 'label' and 'value'
            if let Ok(items) = kwargs.get_item("items") {
                if let Some(items_list) = items {
                    if let Ok(items_py) = items_list.downcast::<pyo3::types::PyList>() {
                        let mut items_vec = Vec::new();
                        for item in items_py.iter() {
                            if let Ok(item_dict) = item.downcast::<PyDict>() {
                                if let (Ok(Some(label)), Ok(Some(value))) =
                                    (item_dict.get_item("label"), item_dict.get_item("value"))
                                {
                                    let label_str: String = label.extract()?;
                                    let value_str: String = value.extract()?;
                                    items_vec.push(crate::ui::dropdown::DropdownItem {
                                        label: label_str,
                                        value: value_str,
                                    });
                                }
                            }
                        }
                        dropdown.items = items_vec;
                    }
                }
            }

            if let Ok(Some(selected)) = kwargs.get_item("selected") {
                dropdown.selected = Some(selected.extract()?);
            }

            if let Ok(Some(variant)) = kwargs.get_item("variant") {
                let variant_str: String = variant.extract()?;
                dropdown.variant = parse_dropdown_variant(&variant_str);
            }

            if let Ok(Some(size)) = kwargs.get_item("size") {
                let size_str: String = size.extract()?;
                dropdown.size = parse_dropdown_size(&size_str);
            }

            if let Ok(Some(disabled)) = kwargs.get_item("disabled") {
                dropdown.disabled = disabled.extract()?;
            }

            if let Ok(Some(placeholder)) = kwargs.get_item("placeholder") {
                dropdown.placeholder = Some(placeholder.extract()?);
            }
        }

        Ok(PyDropdown { inner: dropdown })
    }

    fn render(&self) -> PyResult<String> {
        self.inner
            .render(crate::Framework::Bootstrap5)
            .map_err(|e| PyErr::new::<pyo3::exceptions::PyRuntimeError, _>(e.to_string()))
    }

    fn render_with_framework(&self, framework: String) -> PyResult<String> {
        let fw = framework.parse().unwrap();
        self.inner
            .render(fw)
            .map_err(|e| PyErr::new::<pyo3::exceptions::PyRuntimeError, _>(e.to_string()))
    }

    #[getter]
    fn id(&self) -> PyResult<String> {
        Ok(self.inner.id.clone())
    }

    #[getter]
    fn selected(&self) -> PyResult<Option<String>> {
        Ok(self.inner.selected.clone())
    }

    #[setter]
    fn set_selected(&mut self, value: Option<String>) {
        self.inner.selected = value;
    }

    fn __repr__(&self) -> PyResult<String> {
        Ok(format!("<RustDropdown id='{}'>", self.inner.id))
    }
}

fn parse_dropdown_variant(s: &str) -> crate::ui::dropdown::DropdownVariant {
    use crate::ui::dropdown::DropdownVariant;
    match s.to_lowercase().as_str() {
        "primary" => DropdownVariant::Primary,
        "secondary" => DropdownVariant::Secondary,
        "success" => DropdownVariant::Success,
        "danger" => DropdownVariant::Danger,
        "warning" => DropdownVariant::Warning,
        "info" => DropdownVariant::Info,
        "light" => DropdownVariant::Light,
        "dark" => DropdownVariant::Dark,
        _ => DropdownVariant::Primary,
    }
}

fn parse_dropdown_size(s: &str) -> crate::ui::dropdown::DropdownSize {
    use crate::ui::dropdown::DropdownSize;
    match s.to_lowercase().as_str() {
        "sm" | "small" => DropdownSize::Small,
        "lg" | "large" => DropdownSize::Large,
        _ => DropdownSize::Medium,
    }
}

// ===== Tabs =====

#[pyclass(name = "RustTabs")]
pub struct PyTabs {
    inner: crate::ui::Tabs,
}

#[pymethods]
impl PyTabs {
    #[new]
    #[pyo3(signature = (id, **kwargs))]
    fn new(_py: Python, id: String, kwargs: Option<Bound<'_, PyDict>>) -> PyResult<Self> {
        let mut tabs = crate::ui::Tabs::new(id);

        if let Some(kwargs) = kwargs {
            // tabs: list of dicts with 'id', 'label', and 'content'
            if let Ok(tabs_item) = kwargs.get_item("tabs") {
                if let Some(tabs_list) = tabs_item {
                    if let Ok(tabs_py) = tabs_list.downcast::<pyo3::types::PyList>() {
                        let mut tabs_vec = Vec::new();
                        for tab in tabs_py.iter() {
                            if let Ok(tab_dict) = tab.downcast::<PyDict>() {
                                if let (Ok(Some(id)), Ok(Some(label)), Ok(Some(content))) = (
                                    tab_dict.get_item("id"),
                                    tab_dict.get_item("label"),
                                    tab_dict.get_item("content"),
                                ) {
                                    let id_str: String = id.extract()?;
                                    let label_str: String = label.extract()?;
                                    let content_str: String = content.extract()?;
                                    tabs_vec.push(crate::ui::tabs::TabItem {
                                        id: id_str,
                                        label: label_str,
                                        content: content_str,
                                    });
                                }
                            }
                        }
                        if !tabs_vec.is_empty() && tabs.active.is_empty() {
                            tabs.active = tabs_vec[0].id.clone();
                        }
                        tabs.tabs = tabs_vec;
                    }
                }
            }

            if let Ok(Some(active)) = kwargs.get_item("active") {
                tabs.active = active.extract()?;
            }

            if let Ok(Some(variant)) = kwargs.get_item("variant") {
                let variant_str: String = variant.extract()?;
                tabs.variant = parse_tab_variant(&variant_str);
            }

            if let Ok(Some(vertical)) = kwargs.get_item("vertical") {
                tabs.vertical = vertical.extract()?;
            }
        }

        Ok(PyTabs { inner: tabs })
    }

    fn render(&self) -> PyResult<String> {
        self.inner
            .render(crate::Framework::Bootstrap5)
            .map_err(|e| PyErr::new::<pyo3::exceptions::PyRuntimeError, _>(e.to_string()))
    }

    fn render_with_framework(&self, framework: String) -> PyResult<String> {
        let fw = framework.parse().unwrap();
        self.inner
            .render(fw)
            .map_err(|e| PyErr::new::<pyo3::exceptions::PyRuntimeError, _>(e.to_string()))
    }

    #[getter]
    fn id(&self) -> PyResult<String> {
        Ok(self.inner.id.clone())
    }

    #[getter]
    fn active(&self) -> PyResult<String> {
        Ok(self.inner.active.clone())
    }

    #[setter]
    fn set_active(&mut self, value: String) {
        self.inner.active = value;
    }

    fn __repr__(&self) -> PyResult<String> {
        Ok(format!("<RustTabs id='{}'>", self.inner.id))
    }
}

fn parse_tab_variant(s: &str) -> crate::ui::tabs::TabVariant {
    use crate::ui::tabs::TabVariant;
    match s.to_lowercase().as_str() {
        "pills" => TabVariant::Pills,
        "underline" => TabVariant::Underline,
        _ => TabVariant::Default,
    }
}

// ===== Divider =====

#[pyclass(name = "RustDivider")]
pub struct PyDivider {
    inner: crate::simple::RustDivider,
}

#[pymethods]
impl PyDivider {
    #[new]
    #[pyo3(signature = (text=None, style="solid", margin="md"))]
    fn new(_py: Python, text: Option<String>, style: &str, margin: &str) -> PyResult<Self> {
        let divider = crate::simple::RustDivider::new(text, style, margin);
        Ok(PyDivider { inner: divider })
    }

    fn render(&self) -> PyResult<String> {
        Ok(self.inner.render())
    }

    fn __str__(&self) -> PyResult<String> {
        Ok(self.inner.render())
    }

    fn __repr__(&self) -> PyResult<String> {
        Ok(self.inner.__repr__())
    }
}

// ===== Icon =====

#[pyclass(name = "RustIcon")]
pub struct PyIcon {
    inner: crate::simple::RustIcon,
}

#[pymethods]
impl PyIcon {
    #[new]
    #[pyo3(signature = (name, library="bootstrap", size="md", color=None, label=None))]
    fn new(
        _py: Python,
        name: String,
        library: &str,
        size: &str,
        color: Option<String>,
        label: Option<String>,
    ) -> PyResult<Self> {
        let icon = crate::simple::RustIcon::new(name, library, size, color, label);
        Ok(PyIcon { inner: icon })
    }

    fn render(&self) -> PyResult<String> {
        Ok(self.inner.render())
    }

    fn __str__(&self) -> PyResult<String> {
        Ok(self.inner.render())
    }

    fn __repr__(&self) -> PyResult<String> {
        Ok(self.inner.__repr__())
    }
}

/// Register Python module
#[pymodule]
pub fn _rust_components(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add_class::<PyButton>()?;
    m.add_class::<PyInput>()?;
    m.add_class::<PyText>()?;
    m.add_class::<PyCard>()?;
    m.add_class::<PyAlert>()?;
    m.add_class::<PyModal>()?;
    m.add_class::<PyDropdown>()?;
    m.add_class::<PyTabs>()?;
    m.add_class::<PyDivider>()?;
    m.add_class::<PyIcon>()?;
    Ok(())
}
