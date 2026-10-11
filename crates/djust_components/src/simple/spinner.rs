use pyo3::prelude::*;

#[pyclass(name = "RustSpinner")]
pub struct RustSpinner {
    variant: String,
    size: String,
    animation: String,
    sr_text: String,
}

#[pymethods]
impl RustSpinner {
    #[new]
    #[pyo3(signature = (variant="primary", size="md", animation="border", sr_text="Loading..."))]
    pub fn new(variant: &str, size: &str, animation: &str, sr_text: &str) -> Self {
        Self {
            variant: variant.to_string(),
            size: size.to_string(),
            animation: animation.to_string(),
            sr_text: sr_text.to_string(),
        }
    }

    /// Render spinner to HTML string (Bootstrap 5)
    pub fn render(&self) -> String {
        let size_class = if self.size == "sm" {
            format!(" spinner-{}-sm", html_escape(&self.animation))
        } else {
            String::new()
        };

        format!(
            r#"<div class="spinner-{} text-{}{}" role="status">
    <span class="visually-hidden">{}</span>
</div>"#,
            html_escape(&self.animation),
            html_escape(&self.variant),
            size_class,
            html_escape(&self.sr_text)
        )
    }

    pub fn __str__(&self) -> String {
        self.render()
    }
}

#[inline]
fn html_escape(s: &str) -> String {
    s.replace('&', "&amp;")
        .replace('<', "&lt;")
        .replace('>', "&gt;")
        .replace('"', "&quot;")
        .replace('\'', "&#x27;")
}

#[cfg(test)]
mod escaping_tests {
    use super::*;

    #[test]
    fn variant_and_animation_are_html_escaped() {
        let html = RustSpinner::new("x\" onmouseover=\"y", "sm", "b\"><img src=x>", "<b>").render();
        assert!(!html.contains("x\" onmouseover"));
        assert!(!html.contains("<img"));
        assert!(!html.contains("<b>"));
        assert!(html.contains("text-x&quot; onmouseover=&quot;y"));
        assert!(html.contains("spinner-b&quot;&gt;&lt;img src=x&gt;"));
    }

    #[test]
    fn plain_output_unchanged() {
        let html = RustSpinner::new("primary", "sm", "border", "Loading...").render();
        assert_eq!(
            html,
            "<div class=\"spinner-border text-primary spinner-border-sm\" role=\"status\">\n    <span class=\"visually-hidden\">Loading...</span>\n</div>"
        );
    }
}
