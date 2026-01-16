//! Pure Rust Divider component for maximum performance.
//!
//! This is a simple PyO3 class that renders dividers in ~1μs.
//! Unlike the complex Component trait system, this is stateless and optimized for speed.

use pyo3::prelude::*;

/// Pure Rust Divider component (stateless, high-performance)
#[pyclass(name = "RustDivider")]
pub struct RustDivider {
    text: Option<String>,
    style: String,
    margin: String,
}

#[pymethods]
impl RustDivider {
    #[new]
    #[pyo3(signature = (text=None, style="solid", margin="md"))]
    pub fn new(text: Option<String>, style: &str, margin: &str) -> Self {
        Self {
            text,
            style: style.to_string(),
            margin: margin.to_string(),
        }
    }

    /// Render divider to HTML string (Bootstrap 5)
    pub fn render(&self) -> String {
        // Map margin to Bootstrap spacing classes
        let margin_class = match self.margin.as_str() {
            "sm" => "my-2",
            "md" => "my-3",
            "lg" => "my-4",
            _ => "my-3",
        };

        // Map style to border classes
        let style_class = match self.style.as_str() {
            "dashed" => " border-dashed",
            "dotted" => " border-dotted",
            _ => "", // solid is default
        };

        if let Some(ref text) = self.text {
            // Divider with text in center
            // Use Bootstrap flexbox utilities
            format!(
                r#"<div class="d-flex align-items-center {}">
    <hr class="flex-grow-1{}">
    <span class="px-3 text-muted">{}</span>
    <hr class="flex-grow-1{}">
</div>"#,
                margin_class,
                style_class,
                html_escape(text),
                style_class
            )
        } else {
            // Simple horizontal rule
            format!(r#"<hr class="{margin_class}{style_class}">"#)
        }
    }

    pub fn __str__(&self) -> String {
        self.render()
    }

    pub fn __repr__(&self) -> String {
        if let Some(ref text) = self.text {
            format!(
                "RustDivider(text='{}', style='{}', margin='{}')",
                text, self.style, self.margin
            )
        } else {
            format!(
                "RustDivider(style='{}', margin='{}')",
                self.style, self.margin
            )
        }
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
    fn test_divider_simple() {
        let divider = RustDivider::new(None, "solid", "md");
        let html = divider.render();
        assert!(html.contains("<hr"));
        assert!(html.contains("my-3"));
    }

    #[test]
    fn test_divider_with_text() {
        let divider = RustDivider::new(Some("OR".to_string()), "solid", "md");
        let html = divider.render();
        assert!(html.contains("d-flex"));
        assert!(html.contains("OR"));
        assert!(html.contains("text-muted"));
    }

    #[test]
    fn test_divider_dashed() {
        let divider = RustDivider::new(None, "dashed", "sm");
        let html = divider.render();
        assert!(html.contains("my-2"));
        assert!(html.contains("border-dashed"));
    }

    #[test]
    fn test_divider_dotted_large() {
        let divider = RustDivider::new(None, "dotted", "lg");
        let html = divider.render();
        assert!(html.contains("my-4"));
        assert!(html.contains("border-dotted"));
    }

    #[test]
    fn test_divider_with_text_dashed() {
        let divider = RustDivider::new(Some("AND".to_string()), "dashed", "lg");
        let html = divider.render();
        assert!(html.contains("AND"));
        assert!(html.contains("border-dashed"));
        assert!(html.contains("my-4"));
    }

    #[test]
    fn test_html_escape() {
        let divider = RustDivider::new(
            Some("<script>alert('xss')</script>".to_string()),
            "solid",
            "md",
        );
        let html = divider.render();
        assert!(html.contains("&lt;script&gt;"));
        assert!(!html.contains("<script>"));
    }
}
