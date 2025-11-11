use pyo3::prelude::*;

#[pyclass(name = "RustCard")]
pub struct RustCard {
    body: String,
    header: Option<String>,
    footer: Option<String>,
    variant: String,
}

#[pymethods]
impl RustCard {
    #[new]
    #[pyo3(signature = (body, header=None, footer=None, variant="default"))]
    pub fn new(
        body: String,
        header: Option<String>,
        footer: Option<String>,
        variant: &str,
    ) -> Self {
        Self {
            body,
            header,
            footer,
            variant: variant.to_string(),
        }
    }

    /// Render card to HTML string (Bootstrap 5)
    pub fn render(&self) -> String {
        let mut html = String::from("<div class=\"card");

        // Add variant class
        match self.variant.as_str() {
            "outlined" => html.push_str(" border"),
            "elevated" => html.push_str(" shadow"),
            _ => {}
        }

        html.push_str("\">");

        // Header
        if let Some(ref header) = self.header {
            html.push_str("\n    <div class=\"card-header\">");
            html.push_str(&html_escape(header));
            html.push_str("</div>");
        }

        // Body
        html.push_str("\n    <div class=\"card-body\">");
        html.push_str(&html_escape(&self.body));
        html.push_str("</div>");

        // Footer
        if let Some(ref footer) = self.footer {
            html.push_str("\n    <div class=\"card-footer\">");
            html.push_str(&html_escape(footer));
            html.push_str("</div>");
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
