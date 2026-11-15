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
    strict_styles: bool,
}

#[pymethods]
impl RustProgress {
    #[new]
    #[pyo3(signature = (value, variant="primary", striped=false, animated=false, show_label=false, label=None, height=None, min_value=0.0, max_value=100.0, strict_styles=false))]
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
        strict_styles: bool,
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
            strict_styles,
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

        // The fill is a `dj-progress-w-N` class (no JavaScript, no first-paint jump). By
        // default the height and the width are also the inline style they have always been;
        // with `strict_styles` (LIVEVIEW_CONFIG["csp_strict_styles"], for a `style-src`
        // without 'unsafe-inline') the height is a `data-dj-style-height` the client applies
        // and the width is the class alone (#3979). The Python wrapper checks `height` first.
        let whole = (percentage + 0.5).floor().clamp(0.0, 100.0) as i64;
        let (outer_style, bar_style) = if self.strict_styles {
            let outer = match self.height {
                Some(ref h) => format!(
                    " data-dj-style data-dj-style-height=\"{}\"",
                    html_escape(h.trim())
                ),
                None => String::new(),
            };
            (outer, String::new())
        } else {
            let outer = match self.height {
                Some(ref h) => format!(" style=\"height: {}\"", html_escape(h)),
                None => String::new(),
            };
            (outer, format!(" style=\"width: {:.1}%\"", percentage))
        };

        format!(
            r#"<div class="progress"{}><div class="{} bg-{} dj-progress-w-{}" role="progressbar"{} aria-valuenow="{}" aria-valuemin="{}" aria-valuemax="{}">{}</div></div>"#,
            outer_style,
            bar_class_str,
            html_escape(&self.variant),
            whole,
            bar_style,
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

#[cfg(test)]
mod escaping_tests {
    use super::*;

    #[test]
    fn variant_and_label_are_html_escaped() {
        let html = RustProgress::new(
            40.0,
            "x\" onmouseover=\"y",
            false,
            false,
            false,
            Some("<img src=x onerror=alert(1)>".to_string()),
            None,
            0.0,
            100.0,
            false,
        )
        .render();
        assert!(!html.contains("x\" onmouseover"));
        assert!(html.contains("bg-x&quot; onmouseover=&quot;y"));
        assert!(!html.contains("<img"));
        assert!(html.contains("&lt;img"));
    }

    #[test]
    fn plain_output_unchanged() {
        let html = RustProgress::new(
            50.0, "success", false, false, true, None, None, 0.0, 100.0, false,
        )
        .render();
        assert_eq!(
            html,
            r#"<div class="progress"><div class="progress-bar bg-success dj-progress-w-50" role="progressbar" style="width: 50.0%" aria-valuenow="50" aria-valuemin="0" aria-valuemax="100">50%</div></div>"#
        );
    }

    #[test]
    fn strict_styles_write_no_inline_style() {
        let html = RustProgress::new(
            25.0,
            "info",
            false,
            false,
            false,
            None,
            Some("12px".to_string()),
            0.0,
            100.0,
            true,
        )
        .render();
        assert!(!html.contains("style=\""));
        assert!(html.contains(r#"data-dj-style data-dj-style-height="12px""#));
        assert!(html.contains("dj-progress-w-25"));
    }
}
