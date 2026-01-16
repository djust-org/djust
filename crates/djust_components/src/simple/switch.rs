//! Pure Rust Switch component for maximum performance.
//!
//! This is a simple PyO3 class that renders toggle switches in ~1μs.
//! Unlike the complex Component trait system, this is stateless and optimized for speed.

use pyo3::prelude::*;

/// Pure Rust Switch component (stateless, high-performance)
#[pyclass(name = "RustSwitch")]
pub struct RustSwitch {
    name: String,
    label: String,
    id: String,
    checked: bool,
    disabled: bool,
    help_text: Option<String>,
    value: String,
    inline: bool,
}

#[pymethods]
impl RustSwitch {
    #[new]
    #[pyo3(signature = (name, label, id=None, checked=false, disabled=false, help_text=None, value="on".to_string(), inline=false))]
    #[allow(clippy::too_many_arguments)] // Constructor with defaults - will refactor to builder pattern
    pub fn new(
        name: String,
        label: String,
        id: Option<String>,
        checked: bool,
        disabled: bool,
        help_text: Option<String>,
        value: String,
        inline: bool,
    ) -> Self {
        let switch_id = id.unwrap_or_else(|| name.clone());
        Self {
            name,
            label,
            id: switch_id,
            checked,
            disabled,
            help_text,
            value,
            inline,
        }
    }

    /// Render switch to HTML string (Bootstrap 5)
    pub fn render(&self) -> String {
        let mut html = String::with_capacity(256);

        // Outer wrapper
        html.push_str(r#"<div class="mb-3">"#);
        html.push('\n');

        // Form check wrapper with form-switch
        html.push_str(r#"    <div class="form-check form-switch"#);
        if self.inline {
            html.push_str(" form-check-inline");
        }
        html.push_str(r#"">"#);
        html.push('\n');

        // Input element
        html.push_str(
            r#"        <input class="form-check-input" type="checkbox" role="switch" id=""#,
        );
        html.push_str(&html_escape(&self.id));
        html.push_str(r#"" name=""#);
        html.push_str(&html_escape(&self.name));
        html.push_str(r#"" value=""#);
        html.push_str(&html_escape(&self.value));
        html.push('"');

        if self.checked {
            html.push_str(" checked");
        }
        if self.disabled {
            html.push_str(" disabled");
        }
        html.push('>');
        html.push('\n');

        // Label
        html.push_str(r#"        <label class="form-check-label" for=""#);
        html.push_str(&html_escape(&self.id));
        html.push_str(r#"">"#);
        html.push('\n');
        html.push_str("            ");
        html.push_str(&html_escape(&self.label));
        html.push('\n');
        html.push_str("        </label>");
        html.push('\n');

        // Close form-check
        html.push_str("    </div>");

        // Help text if present
        if let Some(ref help) = self.help_text {
            html.push('\n');
            html.push_str(r#"    <div class="form-text">"#);
            html.push_str(&html_escape(help));
            html.push_str("</div>");
        }

        html.push('\n');
        html.push_str("</div>");

        html
    }

    pub fn __str__(&self) -> String {
        self.render()
    }

    pub fn __repr__(&self) -> String {
        format!(
            "RustSwitch(name='{}', label='{}', checked={})",
            self.name, self.label, self.checked
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
    fn test_switch_basic() {
        let switch = RustSwitch::new(
            "notifications".to_string(),
            "Enable notifications".to_string(),
            None,
            false,
            false,
            None,
            "on".to_string(),
            false,
        );
        let html = switch.render();
        assert!(html.contains(r#"class="form-check form-switch""#));
        assert!(html.contains(r#"type="checkbox""#));
        assert!(html.contains(r#"role="switch""#));
        assert!(html.contains(r#"name="notifications""#));
        assert!(html.contains("Enable notifications"));
    }

    #[test]
    fn test_switch_checked() {
        let switch = RustSwitch::new(
            "dark_mode".to_string(),
            "Dark Mode".to_string(),
            None,
            true, // checked
            false,
            None,
            "on".to_string(),
            false,
        );
        let html = switch.render();
        assert!(html.contains(" checked"));
        assert!(html.contains("Dark Mode"));
    }

    #[test]
    fn test_switch_disabled() {
        let switch = RustSwitch::new(
            "premium".to_string(),
            "Premium Features".to_string(),
            None,
            false,
            true, // disabled
            None,
            "on".to_string(),
            false,
        );
        let html = switch.render();
        assert!(html.contains(" disabled"));
    }

    #[test]
    fn test_switch_with_help_text() {
        let switch = RustSwitch::new(
            "email_notify".to_string(),
            "Email Notifications".to_string(),
            None,
            false,
            false,
            Some("Receive updates via email".to_string()),
            "on".to_string(),
            false,
        );
        let html = switch.render();
        assert!(html.contains(r#"class="form-text""#));
        assert!(html.contains("Receive updates via email"));
    }

    #[test]
    fn test_switch_inline() {
        let switch = RustSwitch::new(
            "toggle".to_string(),
            "Toggle".to_string(),
            None,
            false,
            false,
            None,
            "on".to_string(),
            true, // inline
        );
        let html = switch.render();
        assert!(html.contains("form-check-inline"));
    }

    #[test]
    fn test_switch_custom_id() {
        let switch = RustSwitch::new(
            "switch_name".to_string(),
            "Label".to_string(),
            Some("custom-id".to_string()),
            false,
            false,
            None,
            "on".to_string(),
            false,
        );
        let html = switch.render();
        assert!(html.contains(r#"id="custom-id""#));
        assert!(html.contains(r#"for="custom-id""#));
    }

    #[test]
    fn test_html_escape() {
        let switch = RustSwitch::new(
            "test".to_string(),
            "<script>alert('xss')</script>".to_string(),
            None,
            false,
            false,
            Some("<b>bold</b>".to_string()),
            "on".to_string(),
            false,
        );
        let html = switch.render();
        assert!(html.contains("&lt;script&gt;"));
        assert!(!html.contains("<script>"));
        assert!(html.contains("&lt;b&gt;bold&lt;/b&gt;"));
    }
}
