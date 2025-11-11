/*!
Text Component

A versatile text component for labels, headings, and paragraphs with:
- Multiple element types (p, span, label, h1-h6, div)
- Typography variants (heading, body, caption, overline)
- Text colors and alignment
- Font weight and style
*/

use crate::html::element;
use crate::{Component, ComponentBuilder, ComponentError, Framework};
use ahash::AHashMap as HashMap;
use djust_core::Value;

/// Text element type
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum TextElement {
    Paragraph,
    Span,
    Label,
    Div,
    H1,
    H2,
    H3,
    H4,
    H5,
    H6,
}

impl TextElement {
    pub fn as_str(&self) -> &'static str {
        match self {
            Self::Paragraph => "p",
            Self::Span => "span",
            Self::Label => "label",
            Self::Div => "div",
            Self::H1 => "h1",
            Self::H2 => "h2",
            Self::H3 => "h3",
            Self::H4 => "h4",
            Self::H5 => "h5",
            Self::H6 => "h6",
        }
    }
}

/// Typography variant
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum TextVariant {
    Heading,
    Body,
    Caption,
    Overline,
    Lead,
}

/// Text color
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum TextColor {
    Primary,
    Secondary,
    Success,
    Danger,
    Warning,
    Info,
    Light,
    Dark,
    Muted,
}

impl TextColor {
    fn as_bootstrap_class(&self) -> &'static str {
        match self {
            Self::Primary => "text-primary",
            Self::Secondary => "text-secondary",
            Self::Success => "text-success",
            Self::Danger => "text-danger",
            Self::Warning => "text-warning",
            Self::Info => "text-info",
            Self::Light => "text-light",
            Self::Dark => "text-dark",
            Self::Muted => "text-muted",
        }
    }

    fn as_tailwind_class(&self) -> &'static str {
        match self {
            Self::Primary => "text-blue-600",
            Self::Secondary => "text-gray-600",
            Self::Success => "text-green-600",
            Self::Danger => "text-red-600",
            Self::Warning => "text-yellow-600",
            Self::Info => "text-cyan-600",
            Self::Light => "text-gray-300",
            Self::Dark => "text-gray-900",
            Self::Muted => "text-gray-500",
        }
    }
}

/// Text alignment
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum TextAlign {
    Left,
    Center,
    Right,
    Justify,
}

impl TextAlign {
    fn as_bootstrap_class(&self) -> &'static str {
        match self {
            Self::Left => "text-start",
            Self::Center => "text-center",
            Self::Right => "text-end",
            Self::Justify => "text-justify",
        }
    }

    fn as_tailwind_class(&self) -> &'static str {
        match self {
            Self::Left => "text-left",
            Self::Center => "text-center",
            Self::Right => "text-right",
            Self::Justify => "text-justify",
        }
    }
}

/// Font weight
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum FontWeight {
    Normal,
    Bold,
    Light,
}

impl FontWeight {
    fn as_bootstrap_class(&self) -> &'static str {
        match self {
            Self::Normal => "",
            Self::Bold => "fw-bold",
            Self::Light => "fw-light",
        }
    }

    fn as_tailwind_class(&self) -> &'static str {
        match self {
            Self::Normal => "font-normal",
            Self::Bold => "font-bold",
            Self::Light => "font-light",
        }
    }
}

/// Text component
pub struct Text {
    pub id: Option<String>,
    pub content: String,
    pub element: TextElement,
    pub variant: TextVariant,
    pub color: Option<TextColor>,
    pub align: Option<TextAlign>,
    pub weight: FontWeight,
    pub italic: bool,
    pub for_input: Option<String>, // For label elements
}

impl Text {
    /// Create a new text with default settings
    pub fn new(content: impl Into<String>) -> Self {
        Self {
            id: None,
            content: content.into(),
            element: TextElement::Span,
            variant: TextVariant::Body,
            color: None,
            align: None,
            weight: FontWeight::Normal,
            italic: false,
            for_input: None,
        }
    }

    /// Create a label for an input
    pub fn label(for_input: impl Into<String>, content: impl Into<String>) -> Self {
        Self {
            id: None,
            content: content.into(),
            element: TextElement::Label,
            variant: TextVariant::Body,
            color: None,
            align: None,
            weight: FontWeight::Normal,
            italic: false,
            for_input: Some(for_input.into()),
        }
    }

    /// Create a heading
    pub fn heading(level: u8, content: impl Into<String>) -> Self {
        let element = match level {
            1 => TextElement::H1,
            2 => TextElement::H2,
            3 => TextElement::H3,
            4 => TextElement::H4,
            5 => TextElement::H5,
            _ => TextElement::H6,
        };

        Self {
            id: None,
            content: content.into(),
            element,
            variant: TextVariant::Heading,
            color: None,
            align: None,
            weight: FontWeight::Normal,
            italic: false,
            for_input: None,
        }
    }

    /// Create a builder for the text
    pub fn builder(content: impl Into<String>) -> TextBuilder {
        TextBuilder {
            id: None,
            content: content.into(),
            element: TextElement::Span,
            variant: TextVariant::Body,
            color: None,
            align: None,
            weight: FontWeight::Normal,
            italic: false,
            for_input: None,
        }
    }

    // Setters for Python integration
    pub fn set_content(&mut self, content: String) {
        self.content = content;
    }

    // Render methods for different frameworks
    fn render_bootstrap(&self) -> String {
        let mut classes = Vec::new();

        if let Some(color) = self.color {
            classes.push(color.as_bootstrap_class().to_string());
        }

        if let Some(align) = self.align {
            classes.push(align.as_bootstrap_class().to_string());
        }

        let weight_class = self.weight.as_bootstrap_class();
        if !weight_class.is_empty() {
            classes.push(weight_class.to_string());
        }

        if self.italic {
            classes.push("fst-italic".to_string());
        }

        if matches!(self.variant, TextVariant::Lead) {
            classes.push("lead".to_string());
        }

        let mut elem = element(self.element.as_str());

        if !classes.is_empty() {
            elem = elem.classes(classes);
        }

        if let Some(ref id) = self.id {
            elem = elem.attr("id", id);
        }

        if let Some(ref for_input) = self.for_input {
            elem = elem.attr("for", for_input);
        }

        elem.text(&self.content).build()
    }

    fn render_tailwind(&self) -> String {
        let mut classes = Vec::new();

        if let Some(color) = self.color {
            classes.push(color.as_tailwind_class().to_string());
        }

        if let Some(align) = self.align {
            classes.push(align.as_tailwind_class().to_string());
        }

        classes.push(self.weight.as_tailwind_class().to_string());

        if self.italic {
            classes.push("italic".to_string());
        }

        // Add variant-specific classes
        match self.variant {
            TextVariant::Heading => {
                classes.push("text-2xl".to_string());
                classes.push("font-semibold".to_string());
            }
            TextVariant::Body => {
                classes.push("text-base".to_string());
            }
            TextVariant::Caption => {
                classes.push("text-sm".to_string());
                classes.push("text-gray-600".to_string());
            }
            TextVariant::Overline => {
                classes.push("text-xs".to_string());
                classes.push("uppercase".to_string());
                classes.push("tracking-wide".to_string());
            }
            TextVariant::Lead => {
                classes.push("text-lg".to_string());
                classes.push("font-light".to_string());
            }
        }

        let mut elem = element(self.element.as_str());

        if !classes.is_empty() {
            elem = elem.classes(classes);
        }

        if let Some(ref id) = self.id {
            elem = elem.attr("id", id);
        }

        if let Some(ref for_input) = self.for_input {
            elem = elem.attr("for", for_input);
        }

        elem.text(&self.content).build()
    }

    fn render_plain(&self) -> String {
        let mut classes = Vec::new();

        classes.push("text".to_string());

        if let Some(color) = self.color {
            classes.push(format!("text-{color:?}").to_lowercase());
        }

        if self.weight != FontWeight::Normal {
            classes.push(format!("text-{:?}", self.weight).to_lowercase());
        }

        if self.italic {
            classes.push("text-italic".to_string());
        }

        let mut elem = element(self.element.as_str());

        if !classes.is_empty() {
            elem = elem.classes(classes);
        }

        if let Some(ref id) = self.id {
            elem = elem.attr("id", id);
        }

        if let Some(ref for_input) = self.for_input {
            elem = elem.attr("for", for_input);
        }

        elem.text(&self.content).build()
    }
}

impl Component for Text {
    fn type_name(&self) -> &'static str {
        "Text"
    }

    fn id(&self) -> &str {
        self.id.as_deref().unwrap_or("")
    }

    fn get_state(&self) -> HashMap<String, Value> {
        let mut state = HashMap::default();
        state.insert("content".to_string(), Value::String(self.content.clone()));
        state
    }

    fn set_state(&mut self, mut state: HashMap<String, Value>) {
        if let Some(Value::String(content)) = state.remove("content") {
            self.content = content;
        }
    }

    fn handle_event(
        &mut self,
        event: &str,
        _params: HashMap<String, Value>,
    ) -> Result<(), ComponentError> {
        Err(ComponentError::EventError(format!(
            "Text component does not handle events: {event}"
        )))
    }

    fn render(&self, framework: Framework) -> Result<String, ComponentError> {
        Ok(match framework {
            Framework::Bootstrap5 => self.render_bootstrap(),
            Framework::Tailwind => self.render_tailwind(),
            Framework::Plain => self.render_plain(),
        })
    }
}

/// Builder for Text
pub struct TextBuilder {
    id: Option<String>,
    content: String,
    element: TextElement,
    variant: TextVariant,
    color: Option<TextColor>,
    align: Option<TextAlign>,
    weight: FontWeight,
    italic: bool,
    for_input: Option<String>,
}

impl TextBuilder {
    pub fn id(mut self, id: impl Into<String>) -> Self {
        self.id = Some(id.into());
        self
    }

    pub fn element(mut self, element: TextElement) -> Self {
        self.element = element;
        self
    }

    pub fn variant(mut self, variant: TextVariant) -> Self {
        self.variant = variant;
        self
    }

    pub fn color(mut self, color: TextColor) -> Self {
        self.color = Some(color);
        self
    }

    pub fn align(mut self, align: TextAlign) -> Self {
        self.align = Some(align);
        self
    }

    pub fn weight(mut self, weight: FontWeight) -> Self {
        self.weight = weight;
        self
    }

    pub fn italic(mut self, italic: bool) -> Self {
        self.italic = italic;
        self
    }

    pub fn for_input(mut self, for_input: impl Into<String>) -> Self {
        self.for_input = Some(for_input.into());
        self
    }
}

impl ComponentBuilder for TextBuilder {
    type Component = Text;

    fn build(self) -> Text {
        Text {
            id: self.id,
            content: self.content,
            element: self.element,
            variant: self.variant,
            color: self.color,
            align: self.align,
            weight: self.weight,
            italic: self.italic,
            for_input: self.for_input,
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_text_builder() {
        let text = Text::builder("Hello World")
            .color(TextColor::Primary)
            .weight(FontWeight::Bold)
            .build();

        assert_eq!(text.content, "Hello World");
        assert_eq!(text.color, Some(TextColor::Primary));
    }

    #[test]
    fn test_text_label() {
        let label = Text::label("email-input", "Email Address");

        let html = label.render(Framework::Bootstrap5).unwrap();
        assert!(html.contains("<label"));
        assert!(html.contains("for=\"email-input\""));
        assert!(html.contains("Email Address"));
    }

    #[test]
    fn test_text_heading() {
        let heading = Text::heading(1, "Page Title");

        let html = heading.render(Framework::Bootstrap5).unwrap();
        assert!(html.contains("<h1"));
        assert!(html.contains("Page Title"));
    }
}
