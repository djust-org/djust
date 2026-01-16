//! Pure Rust Badge component for maximum performance.
//!
//! This is a simple PyO3 class that renders badges in ~1μs.
//! Unlike the complex Component trait system, this is stateless and optimized for speed.

use pyo3::prelude::*;

/// Pure Rust Badge component (stateless, high-performance)
#[pyclass(name = "RustBadge")]
pub struct RustBadge {
    text: String,
    variant: String,
    size: String,
    pill: bool,
}

#[pymethods]
impl RustBadge {
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

    /// Render badge to HTML string (Bootstrap 5)
    pub fn render(&self) -> String {
        let mut classes = format!("badge bg-{}", self.variant);

        // Add size class using Bootstrap font-size utilities
        // sm = default (0.75em), md = fs-6 (1rem), lg = fs-5 (1.25rem)
        match self.size.as_str() {
            "sm" => {} // Default badge size
            "md" => classes.push_str(" fs-6"),
            "lg" => classes.push_str(" fs-5"),
            _ => {}
        }

        // Add pill class
        if self.pill {
            classes.push_str(" rounded-pill");
        }

        format!(
            r#"<span class="{}">{}</span>"#,
            classes,
            html_escape(&self.text)
        )
    }

    pub fn __str__(&self) -> String {
        self.render()
    }

    pub fn __repr__(&self) -> String {
        format!(
            "RustBadge(text='{}', variant='{}', size='{}', pill={})",
            self.text, self.variant, self.size, self.pill
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
    fn test_badge_basic() {
        let badge = RustBadge::new("Test".to_string(), "primary", "md", false);
        let html = badge.render();
        assert!(html.contains("badge bg-primary"));
        assert!(html.contains("fs-6"));
        assert!(html.contains("Test"));
    }

    #[test]
    fn test_badge_small() {
        let badge = RustBadge::new("Small".to_string(), "secondary", "sm", false);
        let html = badge.render();
        assert!(html.contains("badge bg-secondary"));
        assert!(!html.contains("fs-")); // Small uses default size
    }

    #[test]
    fn test_badge_pill() {
        let badge = RustBadge::new("Pill".to_string(), "success", "md", true);
        let html = badge.render();
        assert!(html.contains("rounded-pill"));
    }

    #[test]
    fn test_html_escape() {
        let badge = RustBadge::new(
            "<script>alert('xss')</script>".to_string(),
            "danger",
            "md",
            false,
        );
        let html = badge.render();
        assert!(html.contains("&lt;script&gt;"));
        assert!(!html.contains("<script>"));
    }
}
