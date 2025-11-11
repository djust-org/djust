//! Pure Rust Icon component for maximum performance.
//!
//! This is a simple PyO3 class that renders icons in ~1μs.
//! Supports Bootstrap Icons, Font Awesome, and custom icon classes.

use pyo3::prelude::*;

/// Pure Rust Icon component (stateless, high-performance)
#[pyclass(name = "RustIcon")]
pub struct RustIcon {
    name: String,
    library: String,
    size: String,
    color: Option<String>,
    label: Option<String>,
}

#[pymethods]
impl RustIcon {
    #[new]
    #[pyo3(signature = (name, library="bootstrap", size="md", color=None, label=None))]
    pub fn new(
        name: String,
        library: &str,
        size: &str,
        color: Option<String>,
        label: Option<String>,
    ) -> Self {
        Self {
            name,
            library: library.to_string(),
            size: size.to_string(),
            color,
            label,
        }
    }

    /// Render icon to HTML string (Bootstrap 5)
    pub fn render(&self) -> String {
        // Build base icon class based on library
        let icon_class = match self.library.as_str() {
            "bootstrap" => format!("bi bi-{}", self.name),
            "fontawesome" => self.name.clone(),
            _ => self.name.clone(), // custom
        };

        let mut classes = icon_class;

        // Add size class using Bootstrap font-size utilities
        // xs=fs-6 (1rem), sm=fs-5 (1.25rem), md=fs-4 (1.5rem), lg=fs-3 (1.75rem), xl=fs-2 (2rem)
        match self.size.as_str() {
            "xs" => classes.push_str(" fs-6"),
            "sm" => classes.push_str(" fs-5"),
            "md" => classes.push_str(" fs-4"),
            "lg" => classes.push_str(" fs-3"),
            "xl" => classes.push_str(" fs-2"),
            _ => classes.push_str(" fs-4"), // default to md
        }

        // Add color class
        if let Some(ref color) = self.color {
            classes.push_str(&format!(" text-{color}"));
        }

        // Build accessibility attributes
        let aria_attrs = if let Some(ref label) = self.label {
            format!(r#" aria-label="{}" role="img""#, html_escape(label))
        } else {
            String::new()
        };

        format!(r#"<i class="{classes}"{aria_attrs}></i>"#)
    }

    pub fn __str__(&self) -> String {
        self.render()
    }

    pub fn __repr__(&self) -> String {
        format!(
            "RustIcon(name='{}', library='{}', size='{}', color={:?}, label={:?})",
            self.name, self.library, self.size, self.color, self.label
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
    fn test_icon_bootstrap() {
        let icon = RustIcon::new("star-fill".to_string(), "bootstrap", "md", None, None);
        let html = icon.render();
        assert!(html.contains("bi bi-star-fill"));
        assert!(html.contains("fs-4")); // md size
    }

    #[test]
    fn test_icon_fontawesome() {
        let icon = RustIcon::new("fa-heart".to_string(), "fontawesome", "lg", None, None);
        let html = icon.render();
        assert!(html.contains("fa-heart"));
        assert!(html.contains("fs-3")); // lg size
        assert!(!html.contains("bi bi-"));
    }

    #[test]
    fn test_icon_custom() {
        let icon = RustIcon::new("custom-logo".to_string(), "custom", "sm", None, None);
        let html = icon.render();
        assert!(html.contains("custom-logo"));
        assert!(html.contains("fs-5")); // sm size
    }

    #[test]
    fn test_icon_sizes() {
        let xs = RustIcon::new("star".to_string(), "bootstrap", "xs", None, None);
        let sm = RustIcon::new("star".to_string(), "bootstrap", "sm", None, None);
        let md = RustIcon::new("star".to_string(), "bootstrap", "md", None, None);
        let lg = RustIcon::new("star".to_string(), "bootstrap", "lg", None, None);
        let xl = RustIcon::new("star".to_string(), "bootstrap", "xl", None, None);

        assert!(xs.render().contains("fs-6"));
        assert!(sm.render().contains("fs-5"));
        assert!(md.render().contains("fs-4"));
        assert!(lg.render().contains("fs-3"));
        assert!(xl.render().contains("fs-2"));
    }

    #[test]
    fn test_icon_color() {
        let icon = RustIcon::new(
            "check-circle".to_string(),
            "bootstrap",
            "md",
            Some("success".to_string()),
            None,
        );
        let html = icon.render();
        assert!(html.contains("text-success"));
    }

    #[test]
    fn test_icon_accessibility() {
        let icon = RustIcon::new(
            "info-circle".to_string(),
            "bootstrap",
            "md",
            None,
            Some("Information".to_string()),
        );
        let html = icon.render();
        assert!(html.contains(r#"aria-label="Information""#));
        assert!(html.contains(r#"role="img""#));
    }

    #[test]
    fn test_html_escape_label() {
        let icon = RustIcon::new(
            "alert".to_string(),
            "bootstrap",
            "md",
            None,
            Some("<script>alert('xss')</script>".to_string()),
        );
        let html = icon.render();
        assert!(html.contains("&lt;script&gt;"));
        assert!(!html.contains("<script>"));
    }

    #[test]
    fn test_valid_html_structure() {
        // Regression test for malformed HTML bug
        // The bug was: <i class="bi bi-star-fill"</i> (missing '>')
        // Should be: <i class="bi bi-star-fill"></i>

        let icon = RustIcon::new("star-fill".to_string(), "bootstrap", "md", None, None);
        let html = icon.render();

        // Check 1: Should have proper opening and closing tags
        assert!(html.starts_with("<i "), "Icon should start with '<i '");
        assert!(html.ends_with("</i>"), "Icon should end with '</i>'");

        // Check 2: Should have '>' before '</i>' (not '""</i>')
        assert!(
            html.contains("></i>"),
            "Icon should have ></i> at the end, not \"\"</i>"
        );

        // Check 3: Should NOT contain the malformed pattern '""<'
        assert!(
            !html.contains("\"\"<"),
            "Icon should not have malformed closing '\"\"<'"
        );

        // Check 4: With aria-label, should still be valid
        let icon_with_label = RustIcon::new(
            "info".to_string(),
            "bootstrap",
            "md",
            Some("primary".to_string()),
            Some("Info".to_string()),
        );
        let html_with_label = icon_with_label.render();
        assert!(
            html_with_label.contains("></i>"),
            "Icon with aria-label should have ></i>"
        );
        assert!(
            !html_with_label.contains("\"\"<"),
            "Icon with aria-label should not have '\"\"<'"
        );
    }
}
