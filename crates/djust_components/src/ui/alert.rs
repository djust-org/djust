/*!
Alert Component

An alert/notification component with:
- Multiple variants (success, danger, warning, info)
- Dismissible option
- Icon support
*/

use crate::html::element;
use crate::{Component, ComponentError, Framework};
use ahash::AHashMap as HashMap;
use djust_core::Value;

/// Alert variant
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum AlertVariant {
    Primary,
    Secondary,
    Success,
    Danger,
    Warning,
    Info,
    Light,
    Dark,
}

/// Alert component
pub struct Alert {
    pub id: Option<String>,
    pub variant: AlertVariant,
    pub message: String,
    pub dismissible: bool,
    pub icon: Option<String>,
}

impl Alert {
    /// Create a new alert
    pub fn new(message: impl Into<String>) -> Self {
        Self {
            id: None,
            variant: AlertVariant::Info,
            message: message.into(),
            dismissible: false,
            icon: None,
        }
    }

    /// Create a success alert
    pub fn success(message: impl Into<String>) -> Self {
        Self {
            id: None,
            variant: AlertVariant::Success,
            message: message.into(),
            dismissible: false,
            icon: None,
        }
    }

    /// Create a danger/error alert
    pub fn danger(message: impl Into<String>) -> Self {
        Self {
            id: None,
            variant: AlertVariant::Danger,
            message: message.into(),
            dismissible: false,
            icon: None,
        }
    }

    /// Create a warning alert
    pub fn warning(message: impl Into<String>) -> Self {
        Self {
            id: None,
            variant: AlertVariant::Warning,
            message: message.into(),
            dismissible: false,
            icon: None,
        }
    }

    /// Set variant
    pub fn variant(mut self, variant: AlertVariant) -> Self {
        self.variant = variant;
        self
    }

    /// Make dismissible
    pub fn dismissible(mut self, dismissible: bool) -> Self {
        self.dismissible = dismissible;
        self
    }

    /// Set icon
    pub fn icon(mut self, icon: impl Into<String>) -> Self {
        self.icon = Some(icon.into());
        self
    }

    /// Set ID
    pub fn with_id(mut self, id: impl Into<String>) -> Self {
        self.id = Some(id.into());
        self
    }

    // Render methods for different frameworks
    fn render_bootstrap(&self) -> String {
        let mut classes = vec!["alert"];

        let variant_class = match self.variant {
            AlertVariant::Primary => "alert-primary",
            AlertVariant::Secondary => "alert-secondary",
            AlertVariant::Success => "alert-success",
            AlertVariant::Danger => "alert-danger",
            AlertVariant::Warning => "alert-warning",
            AlertVariant::Info => "alert-info",
            AlertVariant::Light => "alert-light",
            AlertVariant::Dark => "alert-dark",
        };
        classes.push(variant_class);

        if self.dismissible {
            classes.push("alert-dismissible");
            classes.push("fade");
            classes.push("show");
        }

        let mut alert = element("div").classes(classes).attr("role", "alert");

        if let Some(ref id) = self.id {
            alert = alert.attr("id", id);
        }

        let mut content = String::new();

        // Add icon if present
        if let Some(ref icon) = self.icon {
            content.push_str(icon);
            content.push(' ');
        }

        content.push_str(&self.message);

        // Add close button if dismissible
        if self.dismissible {
            content.push_str(
                r#"<button type="button" class="btn-close" data-bs-dismiss="alert" aria-label="Close"></button>"#
            );
        }

        alert.child(&content).build()
    }

    fn render_tailwind(&self) -> String {
        let mut classes = vec!["p-4", "rounded-lg", "relative"];

        let (bg_class, text_class, border_class) = match self.variant {
            AlertVariant::Primary => ("bg-blue-100", "text-blue-800", "border-blue-200"),
            AlertVariant::Secondary => ("bg-gray-100", "text-gray-800", "border-gray-200"),
            AlertVariant::Success => ("bg-green-100", "text-green-800", "border-green-200"),
            AlertVariant::Danger => ("bg-red-100", "text-red-800", "border-red-200"),
            AlertVariant::Warning => ("bg-yellow-100", "text-yellow-800", "border-yellow-200"),
            AlertVariant::Info => ("bg-cyan-100", "text-cyan-800", "border-cyan-200"),
            AlertVariant::Light => ("bg-gray-50", "text-gray-600", "border-gray-100"),
            AlertVariant::Dark => ("bg-gray-800", "text-gray-100", "border-gray-700"),
        };

        classes.push(bg_class);
        classes.push(text_class);
        classes.push("border");
        classes.push(border_class);

        let mut alert = element("div").classes(classes).attr("role", "alert");

        if let Some(ref id) = self.id {
            alert = alert.attr("id", id);
        }

        let mut content = String::new();

        // Add icon if present
        if let Some(ref icon) = self.icon {
            content.push_str("<span class=\"font-bold\">");
            content.push_str(icon);
            content.push_str("</span> ");
        }

        content.push_str(&self.message);

        // Add close button if dismissible
        if self.dismissible {
            content.push_str(
                r#"<button type="button" class="absolute top-2 right-2 text-gray-400 hover:text-gray-600" onclick="this.parentElement.remove()">
                    <svg class="w-4 h-4" fill="currentColor" viewBox="0 0 20 20">
                        <path fill-rule="evenodd" d="M4.293 4.293a1 1 0 011.414 0L10 8.586l4.293-4.293a1 1 0 111.414 1.414L11.414 10l4.293 4.293a1 1 0 01-1.414 1.414L10 11.414l-4.293 4.293a1 1 0 01-1.414-1.414L8.586 10 4.293 5.707a1 1 0 010-1.414z" clip-rule="evenodd"></path>
                    </svg>
                </button>"#
            );
        }

        alert.child(&content).build()
    }

    fn render_plain(&self) -> String {
        let mut classes = vec!["alert".to_string()];

        let variant_str = match self.variant {
            AlertVariant::Primary => "alert-primary",
            AlertVariant::Secondary => "alert-secondary",
            AlertVariant::Success => "alert-success",
            AlertVariant::Danger => "alert-danger",
            AlertVariant::Warning => "alert-warning",
            AlertVariant::Info => "alert-info",
            AlertVariant::Light => "alert-light",
            AlertVariant::Dark => "alert-dark",
        };
        classes.push(variant_str.to_string());

        if self.dismissible {
            classes.push("alert-dismissible".to_string());
        }

        let mut alert = element("div").classes(classes).attr("role", "alert");

        if let Some(ref id) = self.id {
            alert = alert.attr("id", id);
        }

        let mut content = String::new();

        if let Some(ref icon) = self.icon {
            content.push_str(icon);
            content.push(' ');
        }

        content.push_str(&self.message);

        if self.dismissible {
            content.push_str(r#"<button type="button" class="close" aria-label="Close"><span aria-hidden="true">&times;</span></button>"#);
        }

        alert.child(&content).build()
    }
}

impl Component for Alert {
    fn type_name(&self) -> &'static str {
        "Alert"
    }

    fn id(&self) -> &str {
        self.id.as_deref().unwrap_or("")
    }

    fn get_state(&self) -> HashMap<String, Value> {
        let mut state = HashMap::default();
        state.insert("message".to_string(), Value::String(self.message.clone()));
        state
    }

    fn set_state(&mut self, mut state: HashMap<String, Value>) {
        if let Some(Value::String(message)) = state.remove("message") {
            self.message = message;
        }
    }

    fn handle_event(
        &mut self,
        event: &str,
        _params: HashMap<String, Value>,
    ) -> Result<(), ComponentError> {
        Err(ComponentError::EventError(format!(
            "Alert component does not handle events: {event}"
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
    fn test_alert_basic() {
        let alert = Alert::new("Test message");
        let html = alert.render(Framework::Bootstrap5).unwrap();
        assert!(html.contains("class=\"alert alert-info\""));
        assert!(html.contains("Test message"));
    }

    #[test]
    fn test_alert_success() {
        let alert = Alert::success("Success!");
        let html = alert.render(Framework::Bootstrap5).unwrap();
        assert!(html.contains("alert-success"));
    }

    #[test]
    fn test_alert_dismissible() {
        let alert = Alert::new("Dismissible").dismissible(true);
        let html = alert.render(Framework::Bootstrap5).unwrap();
        assert!(html.contains("alert-dismissible"));
        assert!(html.contains("btn-close"));
    }

    #[test]
    fn test_alert_with_icon() {
        let alert = Alert::success("Done!").icon("✓");
        let html = alert.render(Framework::Bootstrap5).unwrap();
        assert!(html.contains("✓"));
    }
}
