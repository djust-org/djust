/*!
Modal Component

A modal/dialog overlay component with:
- Header, body, and footer sections
- Multiple sizes
- Close button and backdrop
*/

use crate::html::element;
use crate::{Component, ComponentError, Framework};
use ahash::AHashMap as HashMap;
use djust_core::Value;

/// Modal size
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum ModalSize {
    Small,
    Medium,
    Large,
    ExtraLarge,
}

/// Modal component
pub struct Modal {
    pub id: String,
    pub title: Option<String>,
    pub body: String,
    pub footer: Option<String>,
    pub size: ModalSize,
    pub centered: bool,
    pub scrollable: bool,
}

impl Modal {
    /// Create a new modal
    pub fn new(id: impl Into<String>, body: impl Into<String>) -> Self {
        Self {
            id: id.into(),
            title: None,
            body: body.into(),
            footer: None,
            size: ModalSize::Medium,
            centered: false,
            scrollable: false,
        }
    }

    /// Set title
    pub fn title(mut self, title: impl Into<String>) -> Self {
        self.title = Some(title.into());
        self
    }

    /// Set footer
    pub fn footer(mut self, footer: impl Into<String>) -> Self {
        self.footer = Some(footer.into());
        self
    }

    /// Set size
    pub fn size(mut self, size: ModalSize) -> Self {
        self.size = size;
        self
    }

    /// Center vertically
    pub fn centered(mut self, centered: bool) -> Self {
        self.centered = centered;
        self
    }

    /// Make scrollable
    pub fn scrollable(mut self, scrollable: bool) -> Self {
        self.scrollable = scrollable;
        self
    }

    // Render methods for different frameworks
    fn render_bootstrap(&self) -> String {
        let modal_classes = vec!["modal", "fade"];
        let mut dialog_classes = vec!["modal-dialog"];

        // Size
        match self.size {
            ModalSize::Small => dialog_classes.push("modal-sm"),
            ModalSize::Medium => {}
            ModalSize::Large => dialog_classes.push("modal-lg"),
            ModalSize::ExtraLarge => dialog_classes.push("modal-xl"),
        }

        if self.centered {
            dialog_classes.push("modal-dialog-centered");
        }

        if self.scrollable {
            dialog_classes.push("modal-dialog-scrollable");
        }

        let modal = element("div")
            .classes(modal_classes)
            .attr("id", &self.id)
            .attr("tabindex", "-1")
            .attr("aria-labelledby", format!("{}Label", self.id))
            .attr("aria-hidden", "true");

        let dialog = element("div").classes(dialog_classes);

        let mut content_parts = Vec::new();

        // Header
        if let Some(ref title) = self.title {
            let header = format!(
                r#"<div class="modal-header">
                    <h5 class="modal-title" id="{}Label">{}</h5>
                    <button type="button" class="btn-close" data-bs-dismiss="modal" aria-label="Close"></button>
                </div>"#,
                self.id, title
            );
            content_parts.push(header);
        }

        // Body
        let body = format!(r#"<div class="modal-body">{}</div>"#, self.body);
        content_parts.push(body);

        // Footer
        if let Some(ref footer) = self.footer {
            let footer_html = format!(r#"<div class="modal-footer">{footer}</div>"#);
            content_parts.push(footer_html);
        }

        let content = element("div")
            .class("modal-content")
            .child(content_parts.join("\n"))
            .build();

        let dialog_html = dialog.child(&content).build();
        modal.child(&dialog_html).build()
    }

    fn render_tailwind(&self) -> String {
        // Backdrop
        let backdrop = element("div")
            .classes(vec![
                "fixed",
                "inset-0",
                "bg-gray-600",
                "bg-opacity-50",
                "z-40",
            ])
            .attr(
                "onclick",
                format!(
                    "document.getElementById('{}').classList.add('hidden')",
                    self.id
                ),
            )
            .build();

        // Dialog container classes
        let mut container_classes = vec!["fixed", "inset-0", "z-50", "overflow-y-auto"];

        // Modal dialog classes
        let mut dialog_classes = vec![
            "relative",
            "mx-auto",
            "my-8",
            "bg-white",
            "rounded-lg",
            "shadow-xl",
        ];

        // Size
        match self.size {
            ModalSize::Small => dialog_classes.push("max-w-sm"),
            ModalSize::Medium => dialog_classes.push("max-w-lg"),
            ModalSize::Large => dialog_classes.push("max-w-2xl"),
            ModalSize::ExtraLarge => dialog_classes.push("max-w-4xl"),
        }

        if self.centered {
            container_classes.push("flex");
            container_classes.push("items-center");
        }

        let container = element("div")
            .classes(container_classes)
            .attr("id", &self.id);

        let dialog = element("div").classes(dialog_classes);

        let mut content_parts = Vec::new();

        // Header
        if let Some(ref title) = self.title {
            content_parts.push(
                element("div")
                    .classes(vec!["px-6", "py-4", "border-b", "border-gray-200", "flex", "justify-between", "items-center"])
                    .child(format!(
                        r#"<h3 class="text-xl font-semibold text-gray-900">{}</h3>
                        <button type="button" class="text-gray-400 hover:text-gray-600" onclick="document.getElementById('{}').classList.add('hidden')">
                            <svg class="w-6 h-6" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                                <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M6 18L18 6M6 6l12 12"></path>
                            </svg>
                        </button>"#,
                        title, self.id
                    ))
                    .build()
            );
        }

        // Body
        content_parts.push(
            element("div")
                .classes(vec!["px-6", "py-4"])
                .child(&self.body)
                .build(),
        );

        // Footer
        if let Some(ref footer) = self.footer {
            content_parts.push(
                element("div")
                    .classes(vec!["px-6", "py-4", "border-t", "border-gray-200"])
                    .child(footer)
                    .build(),
            );
        }

        let dialog_html = dialog.child(content_parts.join("\n")).build();
        let container_html = container.child(&dialog_html).build();

        format!("{backdrop}\n{container_html}")
    }

    fn render_plain(&self) -> String {
        let mut modal_classes = vec!["modal".to_string()];

        match self.size {
            ModalSize::Small => modal_classes.push("modal-sm".to_string()),
            ModalSize::Medium => {}
            ModalSize::Large => modal_classes.push("modal-lg".to_string()),
            ModalSize::ExtraLarge => modal_classes.push("modal-xl".to_string()),
        }

        let modal = element("div").classes(modal_classes).attr("id", &self.id);

        let mut content_parts = Vec::new();

        // Header
        if let Some(ref title) = self.title {
            content_parts.push(
                element("div")
                    .class("modal-header")
                    .child(format!(
                        r#"<h3>{title}</h3><button type="button" class="close" aria-label="Close"><span>&times;</span></button>"#
                    ))
                    .build()
            );
        }

        // Body
        content_parts.push(element("div").class("modal-body").child(&self.body).build());

        // Footer
        if let Some(ref footer) = self.footer {
            content_parts.push(element("div").class("modal-footer").child(footer).build());
        }

        modal.child(content_parts.join("\n")).build()
    }
}

impl Component for Modal {
    fn type_name(&self) -> &'static str {
        "Modal"
    }

    fn id(&self) -> &str {
        &self.id
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
            "Modal component does not handle events: {event}"
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
    fn test_modal_basic() {
        let modal = Modal::new("testModal", "Modal content");
        let html = modal.render(Framework::Bootstrap5).unwrap();
        assert!(html.contains("class=\"modal fade\""));
        assert!(html.contains("Modal content"));
    }

    #[test]
    fn test_modal_with_title() {
        let modal = Modal::new("testModal", "Body").title("Modal Title");

        let html = modal.render(Framework::Bootstrap5).unwrap();
        assert!(html.contains("Modal Title"));
        assert!(html.contains("modal-title"));
    }

    #[test]
    fn test_modal_with_footer() {
        let modal = Modal::new("testModal", "Body").footer("Footer content");

        let html = modal.render(Framework::Bootstrap5).unwrap();
        assert!(html.contains("modal-footer"));
        assert!(html.contains("Footer content"));
    }

    #[test]
    fn test_modal_sizes() {
        let modal = Modal::new("testModal", "Body").size(ModalSize::Large);

        let html = modal.render(Framework::Bootstrap5).unwrap();
        assert!(html.contains("modal-lg"));
    }
}
