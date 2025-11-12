use pyo3::prelude::*;

#[pyclass(name = "RustModal")]
pub struct RustModal {
    body: String,
    title: Option<String>,
    footer: Option<String>,
    size: String,
    centered: bool,
    dismissable: bool,
    id: String,
    show: bool,
}

#[pymethods]
impl RustModal {
    #[new]
    #[pyo3(signature = (body, id, title=None, footer=None, size="md", centered=false, dismissable=true, show=false))]
    #[allow(clippy::too_many_arguments)] // Constructor with defaults - will refactor to builder pattern
    pub fn new(
        body: String,
        id: String,
        title: Option<String>,
        footer: Option<String>,
        size: &str,
        centered: bool,
        dismissable: bool,
        show: bool,
    ) -> Self {
        Self {
            body,
            title,
            footer,
            size: size.to_string(),
            centered,
            dismissable,
            id,
            show,
        }
    }

    /// Render modal to HTML string (Bootstrap 5)
    pub fn render(&self) -> String {
        // Build modal classes
        let mut modal_classes = vec!["modal", "fade"];
        if self.show {
            modal_classes.push("show");
        }

        // Build dialog classes
        let mut dialog_classes = vec!["modal-dialog"];
        if self.size != "md" {
            dialog_classes.push(match self.size.as_str() {
                "sm" => "modal-sm",
                "lg" => "modal-lg",
                "xl" => "modal-xl",
                _ => "modal-md",
            });
        }
        if self.centered {
            dialog_classes.push("modal-dialog-centered");
        }

        let modal_class_str = modal_classes.join(" ");
        let dialog_class_str = dialog_classes.join(" ");
        let label_id = format!("{}Label", self.id);

        let mut html = format!(
            r#"<div class="{}" id="{}" tabindex="-1" aria-labelledby="{}" aria-hidden="true">"#,
            modal_class_str, self.id, label_id
        );

        html.push_str(&format!(
            r#"<div class="{dialog_class_str}"><div class="modal-content">"#
        ));

        // Header
        if let Some(ref title) = self.title {
            html.push_str(r#"<div class="modal-header">"#);
            html.push_str(&format!(
                r#"<h5 class="modal-title" id="{}">{}</h5>"#,
                label_id,
                html_escape(title)
            ));
            if self.dismissable {
                html.push_str(r#"<button type="button" class="btn-close" data-bs-dismiss="modal" aria-label="Close"></button>"#);
            }
            html.push_str("</div>");
        }

        // Body
        html.push_str(r#"<div class="modal-body">"#);
        html.push_str(&self.body); // Allow raw HTML in body
        html.push_str("</div>");

        // Footer
        if let Some(ref footer) = self.footer {
            html.push_str(r#"<div class="modal-footer">"#);
            html.push_str(footer); // Allow raw HTML in footer
            html.push_str("</div>");
        }

        html.push_str("</div></div></div>");

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
