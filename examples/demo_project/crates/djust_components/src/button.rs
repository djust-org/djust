//! Button component - pure Rust implementation for maximum performance.
//!
//! Renders Bootstrap 5 buttons in ~1μs.

use pyo3::prelude::*;
use serde::{Deserialize, Serialize};

#[pyclass(name = "RustButton")]
#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct Button {
    text: String,
    variant: String,
    size: String,
    disabled: bool,
    outline: bool,
}

#[pymethods]
impl Button {
    #[new]
    #[pyo3(signature = (text, variant="primary", size="md", disabled=false, outline=false))]
    pub fn new(text: String, variant: &str, size: &str, disabled: bool, outline: bool) -> Self {
        Self {
            text,
            variant: variant.to_string(),
            size: size.to_string(),
            disabled,
            outline,
        }
    }

    /// Render button to HTML string
    pub fn render(&self) -> String {
        let mut classes = String::from("btn");

        // Add variant class (solid or outline)
        if self.outline {
            classes.push_str(&format!(" btn-outline-{}", self.variant));
        } else {
            classes.push_str(&format!(" btn-{}", self.variant));
        }

        // Add size class
        match self.size.as_str() {
            "sm" => classes.push_str(" btn-sm"),
            "lg" => classes.push_str(" btn-lg"),
            "md" => {}, // Default size, no class needed
            _ => {},
        }

        // Disabled attribute
        let disabled_attr = if self.disabled { " disabled" } else { "" };

        format!(
            r#"<button type="button" class="{}"{}>{}button>"#,
            classes,
            disabled_attr,
            html_escape(&self.text)
        )
    }

    pub fn __str__(&self) -> String {
        self.render()
    }

    pub fn __repr__(&self) -> String {
        format!(
            "RustButton(text={:?}, variant={:?}, size={:?}, disabled={}, outline={})",
            self.text, self.variant, self.size, self.disabled, self.outline
        )
    }
}

/// HTML escape function for safety
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
    fn test_button_basic() {
        let button = Button::new("Click me".to_string(), "primary", "md", false, false);
        let html = button.render();
        assert!(html.contains("btn btn-primary"));
        assert!(html.contains("Click me"));
        assert!(!html.contains("disabled"));
    }

    #[test]
    fn test_button_outline() {
        let button = Button::new("Outline".to_string(), "success", "md", false, true);
        let html = button.render();
        assert!(html.contains("btn-outline-success"));
    }

    #[test]
    fn test_button_sizes() {
        let small = Button::new("Small".to_string(), "primary", "sm", false, false);
        let medium = Button::new("Medium".to_string(), "primary", "md", false, false);
        let large = Button::new("Large".to_string(), "primary", "lg", false, false);

        let small_html = small.render();
        let medium_html = medium.render();
        let large_html = large.render();

        assert!(small_html.contains("btn-sm"));
        assert!(!medium_html.contains("btn-sm") && !medium_html.contains("btn-lg"));
        assert!(large_html.contains("btn-lg"));
    }

    #[test]
    fn test_button_disabled() {
        let button = Button::new("Disabled".to_string(), "secondary", "md", true, false);
        let html = button.render();
        assert!(html.contains("disabled"));
    }

    #[test]
    fn test_html_escape() {
        let button = Button::new("<script>xss</script>".to_string(), "danger", "md", false, false);
        let html = button.render();
        assert!(html.contains("&lt;script&gt;"));
        assert!(!html.contains("<script>"));
    }
}
