//! Pure Rust Toast component for maximum performance.
//!
//! This is a simple PyO3 class that renders toasts in ~1μs.
//! Unlike the complex Component trait system, this is stateless and optimized for speed.

use pyo3::prelude::*;

/// Pure Rust Toast component (stateless, high-performance)
#[pyclass(name = "RustToast")]
pub struct RustToast {
    title: String,
    message: String,
    variant: String,
    dismissable: bool,
    show_icon: bool,
    auto_hide: bool,
}

#[pymethods]
impl RustToast {
    #[new]
    #[pyo3(signature = (title="", message="", variant="info", dismissable=true, show_icon=true, auto_hide=false))]
    pub fn new(
        title: &str,
        message: &str,
        variant: &str,
        dismissable: bool,
        show_icon: bool,
        auto_hide: bool,
    ) -> Self {
        Self {
            title: title.to_string(),
            message: message.to_string(),
            variant: variant.to_string(),
            dismissable,
            show_icon,
            auto_hide,
        }
    }

    /// Render toast to HTML string (Bootstrap 5)
    pub fn render(&self) -> String {
        let mut html = String::with_capacity(512);

        // Opening div with variant class
        html.push_str(r#"<div class="toast align-items-center text-bg-"#);
        html.push_str(&self.variant);
        html.push_str(r#" border-0" role="alert" aria-live="assertive" aria-atomic="true""#);

        if self.auto_hide {
            html.push_str(r#" data-bs-autohide="true""#);
        }

        html.push_str(">\n    <div class=\"d-flex\">\n        <div class=\"toast-body\">\n");

        // Icon (if enabled)
        if self.show_icon {
            let icon = match self.variant.as_str() {
                "success" => "✓",
                "warning" => "⚠",
                "danger" => "✗",
                _ => "ℹ", // info or default
            };
            html.push_str("            <span class=\"me-2\">");
            html.push_str(icon);
            html.push_str("</span>");
        }

        // Title
        if !self.title.is_empty() {
            html.push_str("            <strong>");
            html.push_str(&html_escape(&self.title));
            html.push_str("</strong>");
        }

        // Line break if both title and message
        if !self.title.is_empty() && !self.message.is_empty() {
            html.push_str("<br>");
        }

        // Message
        if !self.message.is_empty() {
            html.push_str("            ");
            html.push_str(&html_escape(&self.message));
            html.push('\n');
        }

        html.push_str("        </div>\n");

        // Close button (if dismissable)
        if self.dismissable {
            html.push_str(r#"        <button type="button" class="btn-close btn-close-white me-2 m-auto" data-bs-dismiss="toast" aria-label="Close"></button>"#);
            html.push('\n');
        }

        html.push_str("    </div>\n</div>");

        html
    }

    pub fn __str__(&self) -> String {
        self.render()
    }

    pub fn __repr__(&self) -> String {
        format!(
            "RustToast(title='{}', message='{}', variant='{}', dismissable={}, show_icon={}, auto_hide={})",
            self.title, self.message, self.variant, self.dismissable, self.show_icon, self.auto_hide
        )
    }
}

/// HTML escape for XSS protection
#[inline]
fn html_escape(s: &str) -> String {
    s.replace('&', "&amp;")
        .replace('<', "&lt;")
        .replace('>', "&gt;")
        .replace('"', "&quot;")
        .replace('\'', "&#x27;")
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_toast_basic() {
        let toast = RustToast::new(
            "Success",
            "Operation completed",
            "success",
            true,
            true,
            false,
        );
        let html = toast.render();
        assert!(html.contains("toast align-items-center text-bg-success"));
        assert!(html.contains("<strong>Success</strong>"));
        assert!(html.contains("Operation completed"));
        assert!(html.contains("btn-close"));
    }

    #[test]
    fn test_toast_variants() {
        let success = RustToast::new("", "Success message", "success", false, true, false);
        let info = RustToast::new("", "Info message", "info", false, true, false);
        let warning = RustToast::new("", "Warning message", "warning", false, true, false);
        let danger = RustToast::new("", "Error message", "danger", false, true, false);

        assert!(success.render().contains("text-bg-success"));
        assert!(info.render().contains("text-bg-info"));
        assert!(warning.render().contains("text-bg-warning"));
        assert!(danger.render().contains("text-bg-danger"));
    }

    #[test]
    fn test_toast_dismissable() {
        let dismissable = RustToast::new("Notice", "Can close", "info", true, false, false);
        let not_dismissable = RustToast::new("Notice", "Cannot close", "info", false, false, false);

        assert!(dismissable.render().contains("btn-close"));
        assert!(!not_dismissable.render().contains("btn-close"));
    }

    #[test]
    fn test_toast_auto_hide() {
        let auto = RustToast::new("", "Will hide", "info", false, false, true);
        let manual = RustToast::new("", "Manual", "info", false, false, false);

        assert!(auto.render().contains("data-bs-autohide=\"true\""));
        assert!(!manual.render().contains("data-bs-autohide"));
    }

    #[test]
    fn test_toast_icons() {
        let with_icon = RustToast::new("", "Message", "success", false, true, false);
        let without_icon = RustToast::new("", "Message", "success", false, false, false);

        assert!(with_icon.render().contains("✓"));
        assert!(!without_icon.render().contains("✓"));
    }

    #[test]
    fn test_html_escape() {
        let toast = RustToast::new(
            "<script>xss</script>",
            "Alert: <b>bold</b>",
            "danger",
            false,
            false,
            false,
        );
        let html = toast.render();
        assert!(html.contains("&lt;script&gt;"));
        assert!(html.contains("&lt;b&gt;bold&lt;/b&gt;"));
        assert!(!html.contains("<script>"));
    }

    #[test]
    fn test_toast_title_and_message() {
        let both = RustToast::new("Title", "Message", "info", false, false, false);
        let title_only = RustToast::new("Title", "", "info", false, false, false);
        let message_only = RustToast::new("", "Message", "info", false, false, false);

        assert!(both.render().contains("<strong>Title</strong><br>"));
        assert!(title_only.render().contains("<strong>Title</strong>"));
        assert!(!title_only.render().contains("<br>"));
        assert!(message_only.render().contains("Message"));
        assert!(!message_only.render().contains("<strong>"));
    }
}
