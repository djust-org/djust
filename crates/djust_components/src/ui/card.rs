/*!
Card Component

A card component for displaying content with:
- Optional header and footer
- Body content
- Variants and borders
*/

use crate::html::element;
use crate::{Component, ComponentError, Framework};
use ahash::AHashMap as HashMap;
use djust_core::Value;

/// Card variant
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum CardVariant {
    Default,
    Primary,
    Secondary,
    Success,
    Danger,
    Warning,
    Info,
    Light,
    Dark,
}

/// Card component
pub struct Card {
    pub id: Option<String>,
    pub variant: CardVariant,
    pub border: bool,
    pub shadow: bool,
    pub header: Option<String>,
    pub body: String,
    pub footer: Option<String>,
}

impl Card {
    /// Create a new card
    pub fn new(body: impl Into<String>) -> Self {
        Self {
            id: None,
            variant: CardVariant::Default,
            border: true,
            shadow: false,
            header: None,
            body: body.into(),
            footer: None,
        }
    }

    /// Set variant
    pub fn variant(mut self, variant: CardVariant) -> Self {
        self.variant = variant;
        self
    }

    /// Set header
    pub fn header(mut self, header: impl Into<String>) -> Self {
        self.header = Some(header.into());
        self
    }

    /// Set footer
    pub fn footer(mut self, footer: impl Into<String>) -> Self {
        self.footer = Some(footer.into());
        self
    }

    /// Enable/disable border
    pub fn border(mut self, border: bool) -> Self {
        self.border = border;
        self
    }

    /// Enable/disable shadow
    pub fn shadow(mut self, shadow: bool) -> Self {
        self.shadow = shadow;
        self
    }

    /// Set ID
    pub fn with_id(mut self, id: impl Into<String>) -> Self {
        self.id = Some(id.into());
        self
    }

    // Render methods for different frameworks
    fn render_bootstrap(&self) -> String {
        let mut card_classes = vec!["card"];

        // Variant
        match self.variant {
            CardVariant::Default => {}
            CardVariant::Primary => card_classes.push("border-primary"),
            CardVariant::Secondary => card_classes.push("border-secondary"),
            CardVariant::Success => card_classes.push("border-success"),
            CardVariant::Danger => card_classes.push("border-danger"),
            CardVariant::Warning => card_classes.push("border-warning"),
            CardVariant::Info => card_classes.push("border-info"),
            CardVariant::Light => card_classes.push("border-light"),
            CardVariant::Dark => card_classes.push("border-dark"),
        }

        // Shadow
        if self.shadow {
            card_classes.push("shadow");
        }

        // Border
        if !self.border {
            card_classes.push("border-0");
        }

        let mut card = element("div").classes(card_classes);

        if let Some(ref id) = self.id {
            card = card.attr("id", id);
        }

        let mut parts = Vec::new();

        // Header
        if let Some(ref header) = self.header {
            let header_classes = match self.variant {
                CardVariant::Primary => vec!["card-header", "bg-primary", "text-white"],
                CardVariant::Secondary => vec!["card-header", "bg-secondary", "text-white"],
                CardVariant::Success => vec!["card-header", "bg-success", "text-white"],
                CardVariant::Danger => vec!["card-header", "bg-danger", "text-white"],
                CardVariant::Warning => vec!["card-header", "bg-warning"],
                CardVariant::Info => vec!["card-header", "bg-info", "text-white"],
                CardVariant::Light => vec!["card-header", "bg-light"],
                CardVariant::Dark => vec!["card-header", "bg-dark", "text-white"],
                CardVariant::Default => vec!["card-header"],
            };

            parts.push(element("div").classes(header_classes).child(header).build());
        }

        // Body
        parts.push(element("div").class("card-body").child(&self.body).build());

        // Footer
        if let Some(ref footer) = self.footer {
            parts.push(element("div").class("card-footer").child(footer).build());
        }

        card.child(parts.join("\n")).build()
    }

    fn render_tailwind(&self) -> String {
        let mut card_classes = vec!["rounded-lg", "overflow-hidden"];

        if self.border {
            card_classes.push("border");
            match self.variant {
                CardVariant::Default => card_classes.push("border-gray-200"),
                CardVariant::Primary => card_classes.push("border-blue-500"),
                CardVariant::Secondary => card_classes.push("border-gray-500"),
                CardVariant::Success => card_classes.push("border-green-500"),
                CardVariant::Danger => card_classes.push("border-red-500"),
                CardVariant::Warning => card_classes.push("border-yellow-500"),
                CardVariant::Info => card_classes.push("border-cyan-500"),
                CardVariant::Light => card_classes.push("border-gray-100"),
                CardVariant::Dark => card_classes.push("border-gray-800"),
            }
        }

        if self.shadow {
            card_classes.push("shadow-lg");
        }

        let mut card = element("div").classes(card_classes);

        if let Some(ref id) = self.id {
            card = card.attr("id", id);
        }

        let mut parts = Vec::new();

        // Header
        if let Some(ref header) = self.header {
            let header_classes = match self.variant {
                CardVariant::Default => {
                    vec!["px-6", "py-4", "bg-gray-50", "border-b", "border-gray-200"]
                }
                CardVariant::Primary => vec!["px-6", "py-4", "bg-blue-600", "text-white"],
                CardVariant::Secondary => vec!["px-6", "py-4", "bg-gray-600", "text-white"],
                CardVariant::Success => vec!["px-6", "py-4", "bg-green-600", "text-white"],
                CardVariant::Danger => vec!["px-6", "py-4", "bg-red-600", "text-white"],
                CardVariant::Warning => vec!["px-6", "py-4", "bg-yellow-500"],
                CardVariant::Info => vec!["px-6", "py-4", "bg-cyan-600", "text-white"],
                CardVariant::Light => vec!["px-6", "py-4", "bg-gray-100"],
                CardVariant::Dark => vec!["px-6", "py-4", "bg-gray-800", "text-white"],
            };

            parts.push(element("div").classes(header_classes).child(header).build());
        }

        // Body
        parts.push(
            element("div")
                .classes(vec!["px-6", "py-4"])
                .child(&self.body)
                .build(),
        );

        // Footer
        if let Some(ref footer) = self.footer {
            parts.push(
                element("div")
                    .classes(vec![
                        "px-6",
                        "py-4",
                        "bg-gray-50",
                        "border-t",
                        "border-gray-200",
                    ])
                    .child(footer)
                    .build(),
            );
        }

        card.child(parts.join("\n")).build()
    }

    fn render_plain(&self) -> String {
        let mut card_classes = vec!["card".to_string()];

        match self.variant {
            CardVariant::Default => {}
            variant => {
                let variant_str = format!("card-{variant:?}").to_lowercase();
                card_classes.push(variant_str);
            }
        }

        if self.shadow {
            card_classes.push("card-shadow".to_string());
        }

        let mut card = element("div").classes(card_classes);

        if let Some(ref id) = self.id {
            card = card.attr("id", id);
        }

        let mut parts = Vec::new();

        // Header
        if let Some(ref header) = self.header {
            parts.push(element("div").class("card-header").child(header).build());
        }

        // Body
        parts.push(element("div").class("card-body").child(&self.body).build());

        // Footer
        if let Some(ref footer) = self.footer {
            parts.push(element("div").class("card-footer").child(footer).build());
        }

        card.child(parts.join("\n")).build()
    }
}

impl Component for Card {
    fn type_name(&self) -> &'static str {
        "Card"
    }

    fn id(&self) -> &str {
        self.id.as_deref().unwrap_or("")
    }

    fn get_state(&self) -> HashMap<String, Value> {
        let mut state = HashMap::default();
        state.insert("body".to_string(), Value::String(self.body.clone()));
        state
    }

    fn set_state(&mut self, mut state: HashMap<String, Value>) {
        if let Some(Value::String(body)) = state.remove("body") {
            self.body = body;
        }
    }

    fn handle_event(
        &mut self,
        event: &str,
        _params: HashMap<String, Value>,
    ) -> Result<(), ComponentError> {
        Err(ComponentError::EventError(format!(
            "Card component does not handle events: {event}"
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

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_card_basic() {
        let card = Card::new("Card content");
        let html = card.render(Framework::Bootstrap5).unwrap();
        assert!(html.contains("class=\"card\""));
        assert!(html.contains("Card content"));
    }

    #[test]
    fn test_card_with_header() {
        let card = Card::new("Body").header("Header");

        let html = card.render(Framework::Bootstrap5).unwrap();
        assert!(html.contains("card-header"));
        assert!(html.contains("Header"));
    }

    #[test]
    fn test_card_with_footer() {
        let card = Card::new("Body").footer("Footer");

        let html = card.render(Framework::Bootstrap5).unwrap();
        assert!(html.contains("card-footer"));
        assert!(html.contains("Footer"));
    }

    #[test]
    fn test_card_variant() {
        let card = Card::new("Content").variant(CardVariant::Primary);

        let html = card.render(Framework::Bootstrap5).unwrap();
        assert!(html.contains("border-primary"));
    }
}
