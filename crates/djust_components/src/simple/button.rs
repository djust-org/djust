//! Pure Rust Button component for maximum performance.
//!
//! This is a simple PyO3 class that renders buttons in ~1μs.
//! Unlike the complex Component trait system, this is stateless and optimized for speed.

use pyo3::prelude::*;

/// Pure Rust Button component (stateless, high-performance)
#[pyclass(name = "RustButton")]
pub struct RustButton {
    text: String,
    variant: String,
    size: String,
    disabled: bool,
    outline: bool,
}

#[pymethods]
impl RustButton {
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

    /// Render button to HTML string (Bootstrap 5)
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
            "md" => {} // Default size, no class needed
            _ => {}
        }

        // Disabled attribute
        let disabled_attr = if self.disabled { " disabled" } else { "" };

        format!(
            r#"<button type="button" class="{}"{}>{}</button>"#,
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
            "RustButton(text='{}', variant='{}', size='{}', disabled={}, outline={})",
            self.text, self.variant, self.size, self.disabled, self.outline
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
    fn test_button_basic() {
        let button = RustButton::new("Click me".to_string(), "primary", "md", false, false);
        let html = button.render();
        assert!(html.contains("btn btn-primary"));
        assert!(html.contains("Click me"));
        assert!(!html.contains("disabled"));
    }

    #[test]
    fn test_button_outline() {
        let button = RustButton::new("Outline".to_string(), "success", "md", false, true);
        let html = button.render();
        assert!(html.contains("btn-outline-success"));
    }

    #[test]
    fn test_button_sizes() {
        let small = RustButton::new("Small".to_string(), "primary", "sm", false, false);
        let large = RustButton::new("Large".to_string(), "primary", "lg", false, false);

        assert!(small.render().contains("btn-sm"));
        assert!(large.render().contains("btn-lg"));
    }

    #[test]
    fn test_button_disabled() {
        let button = RustButton::new("Disabled".to_string(), "secondary", "md", true, false);
        let html = button.render();
        assert!(html.contains("disabled"));
    }

    #[test]
    fn test_html_escape() {
        let button = RustButton::new(
            "<script>xss</script>".to_string(),
            "danger",
            "md",
            false,
            false,
        );
        let html = button.render();
        assert!(html.contains("&lt;script&gt;"));
        assert!(!html.contains("<script>"));
    }
}
