//! Pure Rust Range (Slider) component for maximum performance.
//!
//! This is a simple PyO3 class that renders range inputs in ~1μs.
//! Unlike the complex Component trait system, this is stateless and optimized for speed.

use pyo3::prelude::*;

/// Pure Rust Range component (stateless, high-performance)
#[pyclass(name = "RustRange")]
pub struct RustRange {
    name: String,
    id: String,
    label: Option<String>,
    value: f64,
    min_value: f64,
    max_value: f64,
    step: f64,
    show_value: bool,
    help_text: Option<String>,
    disabled: bool,
}

#[pymethods]
impl RustRange {
    #[new]
    #[pyo3(signature = (
        name,
        id=None,
        label=None,
        value=50.0,
        min_value=0.0,
        max_value=100.0,
        step=1.0,
        show_value=false,
        help_text=None,
        disabled=false
    ))]
    #[allow(clippy::too_many_arguments)] // Constructor with defaults - will refactor to builder pattern
    pub fn new(
        name: String,
        id: Option<String>,
        label: Option<String>,
        value: f64,
        min_value: f64,
        max_value: f64,
        step: f64,
        show_value: bool,
        help_text: Option<String>,
        disabled: bool,
    ) -> Self {
        let range_id = id.unwrap_or_else(|| name.clone());
        Self {
            name,
            id: range_id,
            label,
            value,
            min_value,
            max_value,
            step,
            show_value,
            help_text,
            disabled,
        }
    }

    /// Render range input to HTML string (Bootstrap 5)
    pub fn render(&self) -> String {
        let mut html = String::with_capacity(512);

        html.push_str("<div class=\"mb-3\">");

        // Label
        if let Some(ref label) = self.label {
            html.push_str("\n    <label for=\"");
            html.push_str(&self.id);
            html.push_str("\" class=\"form-label\">");
            html.push_str(&html_escape(label));

            // Show current value as badge
            if self.show_value {
                html.push_str(" <span class=\"badge bg-secondary\">");
                html.push_str(&format_number(self.value));
                html.push_str("</span>");
            }

            html.push_str("</label>");
        }

        // Range input
        html.push_str("\n    <input type=\"range\"");
        html.push_str("\n           class=\"form-range\"");
        html.push_str("\n           id=\"");
        html.push_str(&self.id);
        html.push('"');
        html.push_str("\n           name=\"");
        html.push_str(&self.name);
        html.push('"');
        html.push_str("\n           value=\"");
        html.push_str(&format_number(self.value));
        html.push('"');
        html.push_str("\n           min=\"");
        html.push_str(&format_number(self.min_value));
        html.push('"');
        html.push_str("\n           max=\"");
        html.push_str(&format_number(self.max_value));
        html.push('"');
        html.push_str("\n           step=\"");
        html.push_str(&format_number(self.step));
        html.push('"');

        if self.disabled {
            html.push_str(" disabled");
        }

        html.push('>');

        // Help text
        if let Some(ref help) = self.help_text {
            html.push_str("\n    <div class=\"form-text\">");
            html.push_str(&html_escape(help));
            html.push_str("</div>");
        }

        html.push_str("\n</div>");
        html
    }

    pub fn __str__(&self) -> String {
        self.render()
    }

    pub fn __repr__(&self) -> String {
        format!(
            "RustRange(name='{}', id='{}', value={}, min={}, max={}, step={})",
            self.name, self.id, self.value, self.min_value, self.max_value, self.step
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

/// Format number for HTML attributes (remove unnecessary decimals)
#[inline]
fn format_number(n: f64) -> String {
    if n.fract() == 0.0 {
        format!("{n:.0}")
    } else {
        n.to_string()
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_range_basic() {
        let range = RustRange::new(
            "volume".to_string(),
            None,
            Some("Volume".to_string()),
            50.0,
            0.0,
            100.0,
            1.0,
            false,
            None,
            false,
        );
        let html = range.render();
        assert!(html.contains("form-range"));
        assert!(html.contains("Volume"));
        assert!(html.contains("value=\"50\""));
        assert!(html.contains("min=\"0\""));
        assert!(html.contains("max=\"100\""));
        assert!(html.contains("step=\"1\""));
    }

    #[test]
    fn test_range_with_value_display() {
        let range = RustRange::new(
            "brightness".to_string(),
            None,
            Some("Brightness".to_string()),
            75.0,
            0.0,
            100.0,
            5.0,
            true,
            None,
            false,
        );
        let html = range.render();
        assert!(html.contains("badge bg-secondary"));
        assert!(html.contains(">75<"));
    }

    #[test]
    fn test_range_decimal_step() {
        let range = RustRange::new(
            "opacity".to_string(),
            None,
            Some("Opacity".to_string()),
            0.5,
            0.0,
            1.0,
            0.1,
            false,
            None,
            false,
        );
        let html = range.render();
        assert!(html.contains("step=\"0.1\""));
        assert!(html.contains("max=\"1\""));
    }

    #[test]
    fn test_range_disabled() {
        let range = RustRange::new(
            "locked".to_string(),
            None,
            None,
            50.0,
            0.0,
            100.0,
            1.0,
            false,
            None,
            true,
        );
        let html = range.render();
        assert!(html.contains("disabled"));
    }

    #[test]
    fn test_range_help_text() {
        let range = RustRange::new(
            "temperature".to_string(),
            None,
            Some("Temperature".to_string()),
            22.0,
            0.0,
            40.0,
            0.5,
            false,
            Some("Set room temperature in Celsius".to_string()),
            false,
        );
        let html = range.render();
        assert!(html.contains("form-text"));
        assert!(html.contains("Set room temperature in Celsius"));
    }

    #[test]
    fn test_range_custom_id() {
        let range = RustRange::new(
            "slider".to_string(),
            Some("custom-slider-id".to_string()),
            Some("Custom Slider".to_string()),
            50.0,
            0.0,
            100.0,
            1.0,
            false,
            None,
            false,
        );
        let html = range.render();
        assert!(html.contains("id=\"custom-slider-id\""));
        assert!(html.contains("for=\"custom-slider-id\""));
        assert!(html.contains("Custom Slider"));
    }

    #[test]
    fn test_range_negative_values() {
        let range = RustRange::new(
            "balance".to_string(),
            None,
            Some("Balance".to_string()),
            0.0,
            -10.0,
            10.0,
            1.0,
            false,
            None,
            false,
        );
        let html = range.render();
        assert!(html.contains("min=\"-10\""));
        assert!(html.contains("max=\"10\""));
        assert!(html.contains("value=\"0\""));
    }

    #[test]
    fn test_html_escape() {
        let range = RustRange::new(
            "test".to_string(),
            None,
            Some("<script>alert('xss')</script>".to_string()),
            50.0,
            0.0,
            100.0,
            1.0,
            false,
            Some("<b>Help</b>".to_string()),
            false,
        );
        let html = range.render();
        assert!(html.contains("&lt;script&gt;"));
        assert!(!html.contains("<script>"));
        assert!(html.contains("&lt;b&gt;Help&lt;/b&gt;"));
    }

    #[test]
    fn test_format_number() {
        assert_eq!(format_number(50.0), "50");
        assert_eq!(format_number(0.5), "0.5");
        assert_eq!(format_number(100.0), "100");
        assert_eq!(format_number(0.0), "0");
    }
}
