/*!
Input Component

A versatile input component with:
- Multiple types (text, email, password, number, tel, url, search, etc.)
- Multiple sizes (sm, md, lg)
- Validation states (valid, invalid)
- Disabled state
- Placeholder and value
- Event handlers
*/

use crate::html::element;
use crate::{Component, ComponentBuilder, ComponentError, Framework};
use ahash::AHashMap as HashMap;
use djust_core::Value;

/// Input type
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum InputType {
    Text,
    Email,
    Password,
    Number,
    Tel,
    Url,
    Search,
    Date,
    Time,
    DateTime,
    Color,
    File,
}

impl InputType {
    pub fn as_str(&self) -> &'static str {
        match self {
            Self::Text => "text",
            Self::Email => "email",
            Self::Password => "password",
            Self::Number => "number",
            Self::Tel => "tel",
            Self::Url => "url",
            Self::Search => "search",
            Self::Date => "date",
            Self::Time => "time",
            Self::DateTime => "datetime-local",
            Self::Color => "color",
            Self::File => "file",
        }
    }
}

/// Input size
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum InputSize {
    Small,
    Medium,
    Large,
}

impl InputSize {
    fn as_bootstrap_class(&self) -> &'static str {
        match self {
            Self::Small => "form-control-sm",
            Self::Medium => "",
            Self::Large => "form-control-lg",
        }
    }

    fn as_tailwind_classes(&self) -> &'static str {
        match self {
            Self::Small => "px-2 py-1 text-sm",
            Self::Medium => "px-3 py-2 text-base",
            Self::Large => "px-4 py-3 text-lg",
        }
    }
}

/// Validation state
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum ValidationState {
    None,
    Valid,
    Invalid,
}

/// Input component
pub struct Input {
    pub(crate) id: String,
    pub input_type: InputType,
    pub size: InputSize,
    pub name: Option<String>,
    pub value: Option<String>,
    pub placeholder: Option<String>,
    pub disabled: bool,
    pub readonly: bool,
    pub required: bool,
    pub validation_state: ValidationState,
    pub on_input: Option<String>,
    pub on_change: Option<String>,
    pub on_blur: Option<String>,
    pub min: Option<String>,
    pub max: Option<String>,
    pub step: Option<String>,
}

impl Input {
    /// Create a new input with default settings
    pub fn new(id: impl Into<String>) -> Self {
        Self {
            id: id.into(),
            input_type: InputType::Text,
            size: InputSize::Medium,
            name: None,
            value: None,
            placeholder: None,
            disabled: false,
            readonly: false,
            required: false,
            validation_state: ValidationState::None,
            on_input: None,
            on_change: None,
            on_blur: None,
            min: None,
            max: None,
            step: None,
        }
    }

    /// Create a builder for the input
    pub fn builder(id: impl Into<String>) -> InputBuilder {
        InputBuilder {
            id: id.into(),
            input_type: InputType::Text,
            size: InputSize::Medium,
            name: None,
            value: None,
            placeholder: None,
            disabled: false,
            readonly: false,
            required: false,
            validation_state: ValidationState::None,
            on_input: None,
            on_change: None,
            on_blur: None,
            min: None,
            max: None,
            step: None,
        }
    }

    // Setters for Python integration
    pub fn set_value(&mut self, value: Option<String>) {
        self.value = value;
    }

    pub fn set_disabled(&mut self, disabled: bool) {
        self.disabled = disabled;
    }

    // Render methods for different frameworks
    fn render_bootstrap(&self) -> String {
        let mut classes = vec!["form-control".to_string()];

        let size_class = self.size.as_bootstrap_class();
        if !size_class.is_empty() {
            classes.push(size_class.to_string());
        }

        match self.validation_state {
            ValidationState::Valid => classes.push("is-valid".to_string()),
            ValidationState::Invalid => classes.push("is-invalid".to_string()),
            ValidationState::None => {}
        }

        let mut input = element("input")
            .classes(classes)
            .attr("type", self.input_type.as_str())
            .attr("id", &self.id);

        if let Some(ref name) = self.name {
            input = input.attr("name", name);
        }

        if let Some(ref value) = self.value {
            input = input.attr("value", value);
        }

        if let Some(ref placeholder) = self.placeholder {
            input = input.attr("placeholder", placeholder);
        }

        if self.disabled {
            input = input.attr("disabled", "disabled");
        }

        if self.readonly {
            input = input.attr("readonly", "readonly");
        }

        if self.required {
            input = input.attr("required", "required");
        }

        if let Some(ref on_input) = self.on_input {
            input = input.attr("@input", on_input);
        }

        if let Some(ref on_change) = self.on_change {
            input = input.attr("@change", on_change);
        }

        if let Some(ref on_blur) = self.on_blur {
            input = input.attr("@blur", on_blur);
        }

        if let Some(ref min) = self.min {
            input = input.attr("min", min);
        }

        if let Some(ref max) = self.max {
            input = input.attr("max", max);
        }

        if let Some(ref step) = self.step {
            input = input.attr("step", step);
        }

        input.self_closing().build()
    }

    fn render_tailwind(&self) -> String {
        let mut classes = vec![
            "block",
            "w-full",
            "rounded-md",
            "border-gray-300",
            "shadow-sm",
            "focus:border-blue-500",
            "focus:ring-blue-500",
        ];

        classes.push(self.size.as_tailwind_classes());

        if self.disabled {
            classes.push("bg-gray-100");
            classes.push("cursor-not-allowed");
        }

        match self.validation_state {
            ValidationState::Valid => {
                classes.push("border-green-500");
                classes.push("focus:border-green-500");
            }
            ValidationState::Invalid => {
                classes.push("border-red-500");
                classes.push("focus:border-red-500");
            }
            ValidationState::None => {}
        }

        let mut input = element("input")
            .classes(classes.iter().map(|s| s.to_string()))
            .attr("type", self.input_type.as_str())
            .attr("id", &self.id);

        if let Some(ref name) = self.name {
            input = input.attr("name", name);
        }

        if let Some(ref value) = self.value {
            input = input.attr("value", value);
        }

        if let Some(ref placeholder) = self.placeholder {
            input = input.attr("placeholder", placeholder);
        }

        if self.disabled {
            input = input.attr("disabled", "disabled");
        }

        if self.readonly {
            input = input.attr("readonly", "readonly");
        }

        if self.required {
            input = input.attr("required", "required");
        }

        if let Some(ref on_input) = self.on_input {
            input = input.attr("@input", on_input);
        }

        if let Some(ref on_change) = self.on_change {
            input = input.attr("@change", on_change);
        }

        if let Some(ref on_blur) = self.on_blur {
            input = input.attr("@blur", on_blur);
        }

        if let Some(ref min) = self.min {
            input = input.attr("min", min);
        }

        if let Some(ref max) = self.max {
            input = input.attr("max", max);
        }

        if let Some(ref step) = self.step {
            input = input.attr("step", step);
        }

        input.self_closing().build()
    }

    fn render_plain(&self) -> String {
        let mut classes = vec!["input".to_string()];

        match self.size {
            InputSize::Small => classes.push("input-sm".to_string()),
            InputSize::Medium => {}
            InputSize::Large => classes.push("input-lg".to_string()),
        }

        match self.validation_state {
            ValidationState::Valid => classes.push("input-valid".to_string()),
            ValidationState::Invalid => classes.push("input-invalid".to_string()),
            ValidationState::None => {}
        }

        let mut input = element("input")
            .classes(classes)
            .attr("type", self.input_type.as_str())
            .attr("id", &self.id);

        if let Some(ref name) = self.name {
            input = input.attr("name", name);
        }

        if let Some(ref value) = self.value {
            input = input.attr("value", value);
        }

        if let Some(ref placeholder) = self.placeholder {
            input = input.attr("placeholder", placeholder);
        }

        if self.disabled {
            input = input.attr("disabled", "disabled");
        }

        if self.readonly {
            input = input.attr("readonly", "readonly");
        }

        if self.required {
            input = input.attr("required", "required");
        }

        if let Some(ref on_input) = self.on_input {
            input = input.attr("@input", on_input);
        }

        if let Some(ref on_change) = self.on_change {
            input = input.attr("@change", on_change);
        }

        if let Some(ref on_blur) = self.on_blur {
            input = input.attr("@blur", on_blur);
        }

        if let Some(ref min) = self.min {
            input = input.attr("min", min);
        }

        if let Some(ref max) = self.max {
            input = input.attr("max", max);
        }

        if let Some(ref step) = self.step {
            input = input.attr("step", step);
        }

        input.self_closing().build()
    }
}

impl Component for Input {
    fn type_name(&self) -> &'static str {
        "Input"
    }

    fn id(&self) -> &str {
        &self.id
    }

    fn get_state(&self) -> HashMap<String, Value> {
        let mut state = HashMap::default();
        if let Some(ref value) = self.value {
            state.insert("value".to_string(), Value::String(value.clone()));
        }
        state.insert("disabled".to_string(), Value::Bool(self.disabled));
        state
    }

    fn set_state(&mut self, mut state: HashMap<String, Value>) {
        if let Some(Value::String(value)) = state.remove("value") {
            self.value = Some(value);
        }
        if let Some(Value::Bool(disabled)) = state.remove("disabled") {
            self.disabled = disabled;
        }
    }

    fn handle_event(
        &mut self,
        event: &str,
        params: HashMap<String, Value>,
    ) -> Result<(), ComponentError> {
        match event {
            "input" | "change" if self.on_input.is_some() || self.on_change.is_some() => {
                // Extract value from params
                if let Some(Value::String(value)) = params.get("value") {
                    self.value = Some(value.clone());
                }
                Ok(())
            }
            _ => Err(ComponentError::EventError(format!(
                "Unknown event: {event}"
            ))),
        }
    }

    fn render(&self, framework: Framework) -> Result<String, ComponentError> {
        Ok(match framework {
            Framework::Bootstrap5 => self.render_bootstrap(),
            Framework::Tailwind => self.render_tailwind(),
            Framework::Plain => self.render_plain(),
        })
    }
}

/// Builder for Input
pub struct InputBuilder {
    id: String,
    input_type: InputType,
    size: InputSize,
    name: Option<String>,
    value: Option<String>,
    placeholder: Option<String>,
    disabled: bool,
    readonly: bool,
    required: bool,
    validation_state: ValidationState,
    on_input: Option<String>,
    on_change: Option<String>,
    on_blur: Option<String>,
    min: Option<String>,
    max: Option<String>,
    step: Option<String>,
}

impl InputBuilder {
    pub fn input_type(mut self, input_type: InputType) -> Self {
        self.input_type = input_type;
        self
    }

    pub fn size(mut self, size: InputSize) -> Self {
        self.size = size;
        self
    }

    pub fn name(mut self, name: impl Into<String>) -> Self {
        self.name = Some(name.into());
        self
    }

    pub fn value(mut self, value: impl Into<String>) -> Self {
        self.value = Some(value.into());
        self
    }

    pub fn placeholder(mut self, placeholder: impl Into<String>) -> Self {
        self.placeholder = Some(placeholder.into());
        self
    }

    pub fn disabled(mut self, disabled: bool) -> Self {
        self.disabled = disabled;
        self
    }

    pub fn readonly(mut self, readonly: bool) -> Self {
        self.readonly = readonly;
        self
    }

    pub fn required(mut self, required: bool) -> Self {
        self.required = required;
        self
    }

    pub fn validation_state(mut self, state: ValidationState) -> Self {
        self.validation_state = state;
        self
    }

    pub fn on_input(mut self, handler: impl Into<String>) -> Self {
        self.on_input = Some(handler.into());
        self
    }

    pub fn on_change(mut self, handler: impl Into<String>) -> Self {
        self.on_change = Some(handler.into());
        self
    }

    pub fn on_blur(mut self, handler: impl Into<String>) -> Self {
        self.on_blur = Some(handler.into());
        self
    }

    pub fn min(mut self, min: impl Into<String>) -> Self {
        self.min = Some(min.into());
        self
    }

    pub fn max(mut self, max: impl Into<String>) -> Self {
        self.max = Some(max.into());
        self
    }

    pub fn step(mut self, step: impl Into<String>) -> Self {
        self.step = Some(step.into());
        self
    }
}

impl ComponentBuilder for InputBuilder {
    type Component = Input;

    fn build(self) -> Input {
        Input {
            id: self.id,
            input_type: self.input_type,
            size: self.size,
            name: self.name,
            value: self.value,
            placeholder: self.placeholder,
            disabled: self.disabled,
            readonly: self.readonly,
            required: self.required,
            validation_state: self.validation_state,
            on_input: self.on_input,
            on_change: self.on_change,
            on_blur: self.on_blur,
            min: self.min,
            max: self.max,
            step: self.step,
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_input_builder() {
        let input = Input::builder("test-input")
            .input_type(InputType::Email)
            .placeholder("Enter email")
            .required(true)
            .build();

        assert_eq!(input.id(), "test-input");
        assert_eq!(input.input_type, InputType::Email);
    }

    #[test]
    fn test_input_render_bootstrap() {
        let mut input = Input::new("email-input");
        input.input_type = InputType::Email;
        input.placeholder = Some("user@example.com".to_string());

        let html = input.render(Framework::Bootstrap5).unwrap();
        assert!(html.contains("form-control"));
        assert!(html.contains("type=\"email\""));
        assert!(html.contains("placeholder=\"user@example.com\""));
    }
}
