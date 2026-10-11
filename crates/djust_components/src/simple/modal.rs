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
        let e_id = html_escape(&self.id);
        let e_label_id = html_escape(&label_id);

        let mut html = format!(
            r#"<div class="{}" id="{}" tabindex="-1" aria-labelledby="{}" aria-hidden="true">"#,
            modal_class_str, e_id, e_label_id
        );

        html.push_str(&format!(
            r#"<div class="{dialog_class_str}"><div class="modal-content">"#
        ));

        // Header
        if let Some(ref title) = self.title {
            html.push_str(r#"<div class="modal-header">"#);
            html.push_str(&format!(
                r#"<h5 class="modal-title" id="{}">{}</h5>"#,
                e_label_id,
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

#[cfg(test)]
mod escaping_tests {
    use super::*;

    #[test]
    fn id_and_title_are_html_escaped() {
        let modal = RustModal::new(
            "Body".to_string(),
            "m\" onmouseover=\"y".to_string(),
            Some("<img src=x onerror=alert(1)>".to_string()),
            None,
            "md",
            false,
            true,
            false,
        );
        let html = modal.render();
        assert!(!html.contains("m\" onmouseover"));
        assert!(html.contains("id=\"m&quot; onmouseover=&quot;y\""));
        assert!(html.contains("aria-labelledby=\"m&quot; onmouseover=&quot;yLabel\""));
        assert!(!html.contains("<img"));
        assert!(html.contains("&lt;img src=x onerror=alert(1)&gt;"));
    }

    #[test]
    fn body_and_footer_keep_markup() {
        let modal = RustModal::new(
            "<b>ok</b>".to_string(),
            "m1".to_string(),
            Some("Title".to_string()),
            Some("<button>Close</button>".to_string()),
            "md",
            false,
            false,
            false,
        );
        let html = modal.render();
        assert_eq!(
            html,
            r#"<div class="modal fade" id="m1" tabindex="-1" aria-labelledby="m1Label" aria-hidden="true"><div class="modal-dialog"><div class="modal-content"><div class="modal-header"><h5 class="modal-title" id="m1Label">Title</h5></div><div class="modal-body"><b>ok</b></div><div class="modal-footer"><button>Close</button></div></div></div></div>"#
        );
    }
}
