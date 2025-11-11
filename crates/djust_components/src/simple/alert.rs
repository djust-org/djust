use pyo3::prelude::*;

#[pyclass(name = "RustAlert")]
pub struct RustAlert {
    text: String,
    variant: String,
    dismissable: bool,
}

#[pymethods]
impl RustAlert {
    #[new]
    #[pyo3(signature = (text, variant="info", dismissable=false))]
    pub fn new(text: String, variant: &str, dismissable: bool) -> Self {
        Self {
            text,
            variant: variant.to_string(),
            dismissable,
        }
    }

    /// Render alert to HTML string (Bootstrap 5)
    pub fn render(&self) -> String {
        let mut classes = format!("alert alert-{}", self.variant);

        if self.dismissable {
            classes.push_str(" alert-dismissible fade show");
        }

        let mut html = format!(r#"<div class="{classes}" role="alert">"#);
        html.push_str("\n    ");
        html.push_str(&html_escape(&self.text));

        if self.dismissable {
            html.push_str("\n    ");
            html.push_str(r#"<button type="button" class="btn-close" data-bs-dismiss="alert" aria-label="Close"></button>"#);
        }

        html.push_str("\n</div>");
        html
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
