use pyo3::prelude::*;

#[pyclass(name = "RustProgress")]
pub struct RustProgress {
    value: f64,
    variant: String,
    striped: bool,
    animated: bool,
    show_label: bool,
    label: Option<String>,
    height: Option<String>,
    min_value: f64,
    max_value: f64,
}

#[pymethods]
impl RustProgress {
    #[new]
    #[pyo3(signature = (value, variant="primary", striped=false, animated=false, show_label=false, label=None, height=None, min_value=0.0, max_value=100.0))]
    #[allow(clippy::too_many_arguments)] // Constructor with defaults - will refactor to builder pattern
    pub fn new(
        value: f64,
        variant: &str,
        striped: bool,
        animated: bool,
        show_label: bool,
        label: Option<String>,
        height: Option<String>,
        min_value: f64,
        max_value: f64,
    ) -> Self {
        Self {
            value: value.max(min_value).min(max_value), // Clamp value
            variant: variant.to_string(),
            striped,
            animated,
            show_label,
            label,
            height,
            min_value,
            max_value,
        }
    }

    /// Render progress bar to HTML string (Bootstrap 5)
    pub fn render(&self) -> String {
        // Calculate percentage
        let range = self.max_value - self.min_value;
        let percentage = if range > 0.0 {
            (self.value - self.min_value) / range * 100.0
        } else {
            0.0
        };

        // Build progress bar classes
        let mut bar_classes = vec!["progress-bar"];
        if self.striped {
            bar_classes.push("progress-bar-striped");
        }
        if self.animated {
            bar_classes.push("progress-bar-animated");
        }
        let bar_class_str = bar_classes.join(" ");

        // Determine label text
        let label_html = if let Some(ref custom_label) = self.label {
            html_escape(custom_label)
        } else if self.show_label {
            format!("{}%", percentage as i32)
        } else {
            String::new()
        };

        // Build outer style
        let outer_style = if let Some(ref h) = self.height {
            format!(" style=\"height: {}\"", html_escape(h))
        } else {
            String::new()
        };

        format!(
            r#"<div class="progress"{}><div class="{} bg-{}" role="progressbar" style="width: {:.1}%" aria-valuenow="{}" aria-valuemin="{}" aria-valuemax="{}">{}</div></div>"#,
            outer_style,
            bar_class_str,
            self.variant,
            percentage,
            self.value,
            self.min_value,
            self.max_value,
            label_html
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
