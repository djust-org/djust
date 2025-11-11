/*!
Button Component

A versatile button component with:
- Multiple variants (primary, secondary, success, danger, warning, info, link)
- Multiple sizes (sm, md, lg)
- Outline style
- Disabled state
- Icons
- Full width
- Event handlers
*/

use crate::html::element;
use crate::{Component, ComponentBuilder, ComponentError, Framework};
use ahash::AHashMap as HashMap;
use djust_core::Value;

/// Button variant
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum ButtonVariant {
    Primary,
    Secondary,
    Success,
    Danger,
    Warning,
    Info,
    Light,
    Dark,
    Link,
}

impl ButtonVariant {
    fn as_bootstrap_class(&self) -> &'static str {
        match self {
            Self::Primary => "primary",
            Self::Secondary => "secondary",
            Self::Success => "success",
            Self::Danger => "danger",
            Self::Warning => "warning",
            Self::Info => "info",
            Self::Light => "light",
            Self::Dark => "dark",
            Self::Link => "link",
        }
    }

    fn as_tailwind_classes(&self) -> &'static str {
        match self {
            Self::Primary => "bg-blue-600 hover:bg-blue-700 text-white",
            Self::Secondary => "bg-gray-600 hover:bg-gray-700 text-white",
            Self::Success => "bg-green-600 hover:bg-green-700 text-white",
            Self::Danger => "bg-red-600 hover:bg-red-700 text-white",
            Self::Warning => "bg-yellow-500 hover:bg-yellow-600 text-white",
            Self::Info => "bg-cyan-600 hover:bg-cyan-700 text-white",
            Self::Light => "bg-gray-200 hover:bg-gray-300 text-gray-900",
            Self::Dark => "bg-gray-900 hover:bg-black text-white",
            Self::Link => "text-blue-600 hover:text-blue-800 hover:underline",
        }
    }
}

/// Button size
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum ButtonSize {
    Small,
    Medium,
    Large,
}

impl ButtonSize {
    fn as_bootstrap_class(&self) -> &'static str {
        match self {
            Self::Small => "btn-sm",
            Self::Medium => "",
            Self::Large => "btn-lg",
        }
    }

    fn as_tailwind_classes(&self) -> &'static str {
        match self {
            Self::Small => "px-2.5 py-1.5 text-sm",
            Self::Medium => "px-4 py-2 text-base",
            Self::Large => "px-6 py-3 text-lg",
        }
    }
}

/// Button component
pub struct Button {
    pub(crate) id: String,
    pub label: String,
    pub variant: ButtonVariant,
    pub size: ButtonSize,
    pub outline: bool,
    pub disabled: bool,
    pub full_width: bool,
    pub icon: Option<String>,
    pub button_type: String,
    pub on_click: Option<String>,
}

impl Button {
    /// Create a new button with default settings
    pub fn new(id: impl Into<String>, label: impl Into<String>) -> Self {
        Self {
            id: id.into(),
            label: label.into(),
            variant: ButtonVariant::Primary,
            size: ButtonSize::Medium,
            outline: false,
            disabled: false,
            full_width: false,
            icon: None,
            button_type: "button".to_string(),
            on_click: None,
        }
    }

    /// Create a builder for the button
    pub fn builder(id: impl Into<String>) -> ButtonBuilder {
        ButtonBuilder {
            id: id.into(),
            label: String::new(),
            variant: ButtonVariant::Primary,
            size: ButtonSize::Medium,
            outline: false,
            disabled: false,
            full_width: false,
            icon: None,
            button_type: "button".to_string(),
            on_click: None,
        }
    }

    // Setters for Python integration
    pub fn set_label(&mut self, label: String) {
        self.label = label;
    }

    pub fn set_variant(&mut self, variant: ButtonVariant) {
        self.variant = variant;
    }

    pub fn set_disabled(&mut self, disabled: bool) {
        self.disabled = disabled;
    }

    // Render methods for different frameworks
    fn render_bootstrap(&self) -> String {
        let mut classes = vec!["btn".to_string()];

        // Add variant with proper prefix
        let variant_prefix = if self.outline { "btn-outline-" } else { "btn-" };
        classes.push(format!(
            "{}{}",
            variant_prefix,
            self.variant.as_bootstrap_class()
        ));

        // Add size class if not medium
        let size_class = self.size.as_bootstrap_class();
        if !size_class.is_empty() {
            classes.push(size_class.to_string());
        }

        if self.full_width {
            classes.push("w-100".to_string());
        }

        let mut button = element("button")
            .classes(classes)
            .attr("type", &self.button_type)
            .attr("id", &self.id);

        if self.disabled {
            button = button.attr("disabled", "disabled");
        }

        if let Some(ref on_click) = self.on_click {
            button = button.attr("@click", on_click);
        }

        // Add icon if present
        let mut content = String::new();
        if let Some(ref icon) = self.icon {
            content.push_str(icon);
            content.push(' ');
        }
        content.push_str(&self.label);

        button.text(content).build()
    }

    fn render_tailwind(&self) -> String {
        let mut classes = vec![
            "rounded",
            "font-medium",
            "transition-colors",
            "duration-200",
            "focus:outline-none",
            "focus:ring-2",
            "focus:ring-offset-2",
        ];

        classes.push(self.variant.as_tailwind_classes());
        classes.push(self.size.as_tailwind_classes());

        if self.full_width {
            classes.push("w-full");
        }

        if self.disabled {
            classes.push("opacity-50");
            classes.push("cursor-not-allowed");
        }

        if self.outline {
            classes.push("border");
            classes.push("border-current");
            classes.push("bg-transparent");
        }

        let mut button = element("button")
            .classes(classes.iter().map(|s| s.to_string()))
            .attr("type", &self.button_type)
            .attr("id", &self.id);

        if self.disabled {
            button = button.attr("disabled", "disabled");
        }

        if let Some(ref on_click) = self.on_click {
            button = button.attr("@click", on_click);
        }

        // Add icon if present
        let mut content = String::new();
        if let Some(ref icon) = self.icon {
            content.push_str(icon);
            content.push(' ');
        }
        content.push_str(&self.label);

        button.text(content).build()
    }

    fn render_plain(&self) -> String {
        let mut classes = vec!["button".to_string()];

        classes.push(format!("button-{}", self.variant.as_bootstrap_class()));

        match self.size {
            ButtonSize::Small => classes.push("button-sm".to_string()),
            ButtonSize::Medium => {}
            ButtonSize::Large => classes.push("button-lg".to_string()),
        }

        if self.outline {
            classes.push("button-outline".to_string());
        }

        if self.full_width {
            classes.push("button-block".to_string());
        }

        let mut button = element("button")
            .classes(classes)
            .attr("type", &self.button_type)
            .attr("id", &self.id);

        if self.disabled {
            button = button.attr("disabled", "disabled");
        }

        if let Some(ref on_click) = self.on_click {
            button = button.attr("@click", on_click);
        }

        // Add icon if present
        let mut content = String::new();
        if let Some(ref icon) = self.icon {
            content.push_str(icon);
            content.push(' ');
        }
        content.push_str(&self.label);

        button.text(content).build()
    }
}

impl Component for Button {
    fn type_name(&self) -> &'static str {
        "Button"
    }

    fn id(&self) -> &str {
        &self.id
    }

    fn get_state(&self) -> HashMap<String, Value> {
        let mut state = HashMap::default();
        state.insert("label".to_string(), Value::String(self.label.clone()));
        state.insert("disabled".to_string(), Value::Bool(self.disabled));
        state.insert(
            "variant".to_string(),
            Value::String(self.variant.as_bootstrap_class().to_string()),
        );
        state
    }

    fn set_state(&mut self, mut state: HashMap<String, Value>) {
        if let Some(Value::String(label)) = state.remove("label") {
            self.label = label;
        }
        if let Some(Value::Bool(disabled)) = state.remove("disabled") {
            self.disabled = disabled;
        }
    }

    fn handle_event(
        &mut self,
        event: &str,
        _params: HashMap<String, Value>,
    ) -> Result<(), ComponentError> {
        match event {
            "click" if self.on_click.is_some() => {
                // Event handling is done by the LiveView system
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

/// Builder for Button
pub struct ButtonBuilder {
    id: String,
    label: String,
    variant: ButtonVariant,
    size: ButtonSize,
    outline: bool,
    disabled: bool,
    full_width: bool,
    icon: Option<String>,
    button_type: String,
    on_click: Option<String>,
}

impl ButtonBuilder {
    pub fn label(mut self, label: impl Into<String>) -> Self {
        self.label = label.into();
        self
    }

    pub fn variant(mut self, variant: ButtonVariant) -> Self {
        self.variant = variant;
        self
    }

    pub fn size(mut self, size: ButtonSize) -> Self {
        self.size = size;
        self
    }

    pub fn outline(mut self, outline: bool) -> Self {
        self.outline = outline;
        self
    }

    pub fn disabled(mut self, disabled: bool) -> Self {
        self.disabled = disabled;
        self
    }

    pub fn full_width(mut self, full_width: bool) -> Self {
        self.full_width = full_width;
        self
    }

    pub fn icon(mut self, icon: impl Into<String>) -> Self {
        self.icon = Some(icon.into());
        self
    }

    pub fn button_type(mut self, button_type: impl Into<String>) -> Self {
        self.button_type = button_type.into();
        self
    }

    pub fn on_click(mut self, handler: impl Into<String>) -> Self {
        self.on_click = Some(handler.into());
        self
    }
}

impl ComponentBuilder for ButtonBuilder {
    type Component = Button;

    fn build(self) -> Button {
        Button {
            id: self.id,
            label: self.label,
            variant: self.variant,
            size: self.size,
            outline: self.outline,
            disabled: self.disabled,
            full_width: self.full_width,
            icon: self.icon,
            button_type: self.button_type,
            on_click: self.on_click,
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_button_builder() {
        let button = Button::builder("test-btn")
            .label("Click Me")
            .variant(ButtonVariant::Primary)
            .size(ButtonSize::Large)
            .build();

        assert_eq!(button.id(), "test-btn");
        assert_eq!(button.label, "Click Me");
    }

    #[test]
    fn test_button_render_bootstrap() {
        let mut button = Button::new("btn1", "Submit");
        button.variant = ButtonVariant::Success;
        button.size = ButtonSize::Large;

        let html = button.render(Framework::Bootstrap5).unwrap();
        assert!(html.contains("btn"));
        assert!(html.contains("success"));
        assert!(html.contains("btn-lg"));
        assert!(html.contains("Submit"));
    }
}
