//! Pure Rust TextArea component for maximum performance.
//!
//! This is a simple PyO3 class that renders textareas in ~1μs.
//! Unlike the complex Component trait system, this is stateless and optimized for speed.

use pyo3::prelude::*;

/// Pure Rust TextArea component (stateless, high-performance)
#[pyclass(name = "RustTextArea")]
pub struct RustTextArea {
    name: String,
    id: String,
    label: Option<String>,
    value: String,
    placeholder: Option<String>,
    help_text: Option<String>,
    rows: usize,
    required: bool,
    disabled: bool,
    readonly: bool,
    validation_state: Option<String>,
    validation_message: Option<String>,
}

#[pymethods]
impl RustTextArea {
    #[new]
    #[pyo3(signature = (
        name,
        id=None,
        label=None,
        value="",
        placeholder=None,
        help_text=None,
        rows=3,
        required=false,
        disabled=false,
        readonly=false,
        validation_state=None,
        validation_message=None
    ))]
    #[allow(clippy::too_many_arguments)] // Constructor with defaults - will refactor to builder pattern
    pub fn new(
        name: String,
        id: Option<String>,
        label: Option<String>,
        value: &str,
        placeholder: Option<String>,
        help_text: Option<String>,
        rows: usize,
        required: bool,
        disabled: bool,
        readonly: bool,
        validation_state: Option<String>,
        validation_message: Option<String>,
    ) -> Self {
        let textarea_id = id.unwrap_or_else(|| name.clone());
        Self {
            name,
            id: textarea_id,
            label,
            value: value.to_string(),
            placeholder,
            help_text,
            rows,
            required,
            disabled,
            readonly,
            validation_state,
            validation_message,
        }
    }

    /// Render textarea to HTML string (Bootstrap 5)
    pub fn render(&self) -> String {
        let mut html = String::with_capacity(512);

        html.push_str("<div class=\"mb-3\">");

        // Label
        if let Some(ref label) = self.label {
            html.push_str("\n    <label for=\"");
            html.push_str(&self.id);
            html.push_str("\" class=\"form-label\">");
            html.push_str(&html_escape(label));
            if self.required {
                html.push_str(" <span class=\"text-danger\">*</span>");
            }
            html.push_str("</label>");
        }

        // Textarea opening tag
        html.push_str("\n    <textarea class=\"form-control");

        // Validation classes
        if let Some(ref state) = self.validation_state {
            match state.as_str() {
                "valid" => html.push_str(" is-valid"),
                "invalid" => html.push_str(" is-invalid"),
                _ => {}
            }
        }

        html.push_str("\"\n              id=\"");
        html.push_str(&self.id);
        html.push_str("\"\n              name=\"");
        html.push_str(&self.name);
        html.push_str("\"\n              rows=\"");
        html.push_str(&self.rows.to_string());
        html.push('"');

        // Optional attributes
        if let Some(ref placeholder) = self.placeholder {
            html.push_str(" placeholder=\"");
            html.push_str(&html_escape(placeholder));
            html.push('"');
        }

        if self.required {
            html.push_str(" required");
        }

        if self.disabled {
            html.push_str(" disabled");
        }

        if self.readonly {
            html.push_str(" readonly");
        }

        html.push('>');
        html.push_str(&html_escape(&self.value));
        html.push_str("</textarea>");

        // Help text
        if let Some(ref help) = self.help_text {
            html.push_str("\n    <div class=\"form-text\">");
            html.push_str(&html_escape(help));
            html.push_str("</div>");
        }

        // Validation message
        if let Some(ref message) = self.validation_message {
            let feedback_class = match self.validation_state.as_deref() {
                Some("valid") => "valid-feedback",
                _ => "invalid-feedback",
            };
            html.push_str("\n    <div class=\"");
            html.push_str(feedback_class);
            html.push_str("\">");
            html.push_str(&html_escape(message));
            html.push_str("</div>");
        }

        html.push_str("\n</div>");
        html
    }

    pub fn __str__(&self) -> String {
        self.render()
    }

    pub fn __repr__(&self) -> String {
        format!(
            "RustTextArea(name='{}', id='{}', rows={}, required={})",
            self.name, self.id, self.rows, self.required
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
    fn test_textarea_basic() {
        let textarea = RustTextArea::new(
            "description".to_string(),
            None,
            Some("Description".to_string()),
            "Initial value",
            None,
            None,
            3,
            false,
            false,
            false,
            None,
            None,
        );
        let html = textarea.render();
        assert!(html.contains("form-control"));
        assert!(html.contains("rows=\"3\""));
        assert!(html.contains("Description"));
        assert!(html.contains("Initial value"));
    }

    #[test]
    fn test_textarea_with_placeholder() {
        let textarea = RustTextArea::new(
            "bio".to_string(),
            None,
            None,
            "",
            Some("Tell us about yourself...".to_string()),
            None,
            5,
            false,
            false,
            false,
            None,
            None,
        );
        let html = textarea.render();
        assert!(html.contains("placeholder=\"Tell us about yourself...\""));
        assert!(html.contains("rows=\"5\""));
    }

    #[test]
    fn test_textarea_required() {
        let textarea = RustTextArea::new(
            "comment".to_string(),
            None,
            Some("Comment".to_string()),
            "",
            None,
            None,
            4,
            true,
            false,
            false,
            None,
            None,
        );
        let html = textarea.render();
        assert!(html.contains("required"));
        assert!(html.contains("text-danger"));
    }

    #[test]
    fn test_textarea_validation_invalid() {
        let textarea = RustTextArea::new(
            "description".to_string(),
            None,
            None,
            "Short",
            None,
            None,
            3,
            false,
            false,
            false,
            Some("invalid".to_string()),
            Some("Description is too short".to_string()),
        );
        let html = textarea.render();
        assert!(html.contains("is-invalid"));
        assert!(html.contains("invalid-feedback"));
        assert!(html.contains("Description is too short"));
    }

    #[test]
    fn test_textarea_validation_valid() {
        let textarea = RustTextArea::new(
            "description".to_string(),
            None,
            None,
            "Long enough description",
            None,
            None,
            3,
            false,
            false,
            false,
            Some("valid".to_string()),
            Some("Looks good!".to_string()),
        );
        let html = textarea.render();
        assert!(html.contains("is-valid"));
        assert!(html.contains("valid-feedback"));
        assert!(html.contains("Looks good!"));
    }

    #[test]
    fn test_textarea_disabled() {
        let textarea = RustTextArea::new(
            "readonly_text".to_string(),
            None,
            None,
            "Can't edit this",
            None,
            None,
            3,
            false,
            true,
            false,
            None,
            None,
        );
        let html = textarea.render();
        assert!(html.contains("disabled"));
    }

    #[test]
    fn test_textarea_readonly() {
        let textarea = RustTextArea::new(
            "readonly_text".to_string(),
            None,
            None,
            "Can't edit this",
            None,
            None,
            3,
            false,
            false,
            true,
            None,
            None,
        );
        let html = textarea.render();
        assert!(html.contains("readonly"));
    }

    #[test]
    fn test_textarea_help_text() {
        let textarea = RustTextArea::new(
            "notes".to_string(),
            None,
            None,
            "",
            None,
            Some("Maximum 500 characters".to_string()),
            3,
            false,
            false,
            false,
            None,
            None,
        );
        let html = textarea.render();
        assert!(html.contains("form-text"));
        assert!(html.contains("Maximum 500 characters"));
    }

    #[test]
    fn test_html_escape() {
        let textarea = RustTextArea::new(
            "xss_test".to_string(),
            None,
            None,
            "<script>alert('xss')</script>",
            None,
            None,
            3,
            false,
            false,
            false,
            None,
            None,
        );
        let html = textarea.render();
        assert!(html.contains("&lt;script&gt;"));
        assert!(!html.contains("<script>"));
    }
}
