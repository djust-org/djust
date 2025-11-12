use pyo3::prelude::*;

#[pyclass(name = "RustAvatar")]
pub struct RustAvatar {
    src: Option<String>,
    alt: String,
    initials: Option<String>,
    size: String,
    shape: String,
    status: Option<String>,
}

#[pymethods]
impl RustAvatar {
    #[new]
    #[pyo3(signature = (src=None, alt="", initials=None, size="md", shape="circle", status=None))]
    pub fn new(
        src: Option<String>,
        alt: &str,
        initials: Option<String>,
        size: &str,
        shape: &str,
        status: Option<String>,
    ) -> Self {
        // Limit initials to 2 characters and uppercase
        let initials = initials.map(|s| s.chars().take(2).collect::<String>().to_uppercase());

        Self {
            src,
            alt: alt.to_string(),
            initials,
            size: size.to_string(),
            shape: shape.to_string(),
            status,
        }
    }

    /// Render avatar to HTML string (Bootstrap 5)
    pub fn render(&self) -> String {
        // Size mapping
        let size_style = match self.size.as_str() {
            "xs" => "width: 1.5rem; height: 1.5rem;",
            "sm" => "width: 2rem; height: 2rem;",
            "md" => "width: 3rem; height: 3rem;",
            "lg" => "width: 4rem; height: 4rem;",
            "xl" => "width: 6rem; height: 6rem;",
            _ => "width: 3rem; height: 3rem;",
        };

        // Shape class
        let shape_class = if self.shape == "circle" {
            "rounded-circle"
        } else {
            "rounded"
        };

        let mut html =
            format!(r#"<div class="position-relative d-inline-block" style="{size_style}">"#);

        // Image or initials
        if let Some(ref src) = self.src {
            html.push_str(&format!(
                r#"
    <img src="{}" alt="{}" class="w-100 h-100 object-fit-cover {}">"#,
                html_escape(src),
                html_escape(&self.alt),
                shape_class
            ));
        } else if let Some(ref initials) = self.initials {
            html.push_str(&format!(
                r#"
    <div class="w-100 h-100 bg-primary text-white d-flex align-items-center justify-content-center {shape_class}">
        <span class="fw-bold">{initials}</span>
    </div>"#
            ));
        }

        // Status indicator
        if let Some(ref status) = self.status {
            let status_class = match status.as_str() {
                "online" => "bg-success",
                "offline" => "bg-secondary",
                "busy" => "bg-danger",
                "away" => "bg-warning",
                _ => "bg-secondary",
            };

            html.push_str(&format!(
                r#"
    <span class="position-absolute bottom-0 end-0 p-1 {status_class} border border-white rounded-circle"></span>"#
            ));
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
