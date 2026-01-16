//! Badge component - pure Rust implementation for maximum performance.
//!
//! Renders Bootstrap 5 badges in ~1μs.

use pyo3::prelude::*;
use serde::{Deserialize, Serialize};

#[pyclass(name = "RustBadge")]
#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct Badge {
    text: String,
    variant: String,
    size: String,
    pill: bool,
}

#[pymethods]
impl Badge {
    #[new]
    #[pyo3(signature = (text, variant="primary", size="md", pill=false))]
    pub fn new(text: String, variant: &str, size: &str, pill: bool) -> Self {
        Self {
            text,
            variant: variant.to_string(),
            size: size.to_string(),
            pill,
        }
    }

    /// Render badge to HTML string
    pub fn render(&self) -> String {
        let mut classes = format!("badge bg-{}", self.variant);

        // Add size class using Bootstrap font-size utilities
        match self.size.as_str() {
            "sm" => {}, // Default badge size (0.75em)
            "md" => classes.push_str(" fs-6"),  // 1rem
            "lg" => classes.push_str(" fs-5"),  // 1.25rem
            _ => {},
        }

        // Add pill class
        if self.pill {
            classes.push_str(" rounded-pill");
        }

        format!(r#"<span class="{}">{}</span>"#, classes, html_escape(&self.text))
    }

    pub fn __str__(&self) -> String {
        self.render()
    }

    pub fn __repr__(&self) -> String {
        format!(
            "RustBadge(text={:?}, variant={:?}, size={:?}, pill={})",
            self.text, self.variant, self.size, self.pill
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
    fn test_badge_basic() {
        let badge = Badge::new("Test".to_string(), "primary", "md", false);
        let html = badge.render();
        assert!(html.contains("badge bg-primary"));
        assert!(html.contains("Test"));
    }

    #[test]
    fn test_badge_pill() {
        let badge = Badge::new("Pill".to_string(), "success", "md", true);
        let html = badge.render();
        assert!(html.contains("rounded-pill"));
    }

    #[test]
    fn test_badge_sizes() {
        let small = Badge::new("Small".to_string(), "secondary", "sm", false);
        let medium = Badge::new("Medium".to_string(), "secondary", "md", false);
        let large = Badge::new("Large".to_string(), "secondary", "lg", false);

        let small_html = small.render();
        let medium_html = medium.render();
        let large_html = large.render();

        // Small should not have fs- class (uses default)
        assert!(!small_html.contains("fs-"));

        // Medium should have fs-6
        assert!(medium_html.contains("fs-6"));

        // Large should have fs-5
        assert!(large_html.contains("fs-5"));
    }

    #[test]
    fn test_html_escape() {
        let badge = Badge::new("<script>alert('xss')</script>".to_string(), "danger", "md", false);
        let html = badge.render();
        assert!(html.contains("&lt;script&gt;"));
        assert!(!html.contains("<script>"));
    }
}
