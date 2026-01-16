//! Pure Rust Tooltip component for maximum performance.
//!
//! This is a simple PyO3 class that renders tooltips in ~1μs.
//! Unlike the complex Component trait system, this is stateless and optimized for speed.

use pyo3::prelude::*;

/// Pure Rust Tooltip component (stateless, high-performance)
#[pyclass(name = "RustTooltip")]
pub struct RustTooltip {
    content: String,
    text: String,
    placement: String,
    trigger: String,
    arrow: bool,
}

#[pymethods]
impl RustTooltip {
    #[new]
    #[pyo3(signature = (content, text, placement="top", trigger="hover", arrow=true))]
    pub fn new(content: String, text: String, placement: &str, trigger: &str, arrow: bool) -> Self {
        Self {
            content,
            text,
            placement: placement.to_string(),
            trigger: trigger.to_string(),
            arrow,
        }
    }

    /// Render tooltip to HTML string (Bootstrap 5)
    pub fn render(&self) -> String {
        // Validate placement
        let valid_placements = ["top", "bottom", "left", "right"];
        let placement = if valid_placements.contains(&self.placement.as_str()) {
            &self.placement
        } else {
            "top"
        };

        // Validate trigger
        let valid_triggers = ["hover", "click", "focus"];
        let trigger = if valid_triggers.contains(&self.trigger.as_str()) {
            &self.trigger
        } else {
            "hover"
        };

        // Build arrow attribute
        let arrow_attr = if self.arrow {
            r#" data-bs-arrow="true""#
        } else {
            ""
        };

        // Escape text for HTML attribute
        let escaped_text = html_escape_attr(&self.text);

        format!(
            r#"<span class="d-inline-block" tabindex="0" data-bs-toggle="tooltip" data-bs-placement="{}" data-bs-trigger="{}"{}title="{}">{}</span>"#,
            placement, trigger, arrow_attr, escaped_text, &self.content
        )
    }

    pub fn __str__(&self) -> String {
        self.render()
    }

    pub fn __repr__(&self) -> String {
        format!(
            "RustTooltip(content='{}', text='{}', placement='{}', trigger='{}', arrow={})",
            truncate(&self.content, 30),
            truncate(&self.text, 30),
            self.placement,
            self.trigger,
            self.arrow
        )
    }
}

/// HTML escape for attributes (quotes, ampersands, etc.)
#[inline]
fn html_escape_attr(s: &str) -> String {
    s.replace('&', "&amp;")
        .replace('"', "&quot;")
        .replace('\'', "&#x27;")
        .replace('<', "&lt;")
        .replace('>', "&gt;")
}

/// Truncate string for display (used in __repr__)
#[inline]
fn truncate(s: &str, max_len: usize) -> String {
    if s.len() > max_len {
        format!("{}...", &s[..max_len])
    } else {
        s.to_string()
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_tooltip_basic() {
        let tooltip = RustTooltip::new(
            "Hover me".to_string(),
            "Tooltip text".to_string(),
            "top",
            "hover",
            true,
        );
        let html = tooltip.render();
        assert!(html.contains("data-bs-toggle=\"tooltip\""));
        assert!(html.contains("data-bs-placement=\"top\""));
        assert!(html.contains("data-bs-trigger=\"hover\""));
        assert!(html.contains("title=\"Tooltip text\""));
        assert!(html.contains("Hover me"));
    }

    #[test]
    fn test_tooltip_placements() {
        let placements = ["top", "bottom", "left", "right"];
        for placement in placements.iter() {
            let tooltip = RustTooltip::new(
                "Test".to_string(),
                "Text".to_string(),
                placement,
                "hover",
                true,
            );
            let html = tooltip.render();
            assert!(html.contains(&format!("data-bs-placement=\"{placement}\"")));
        }
    }

    #[test]
    fn test_tooltip_triggers() {
        let triggers = ["hover", "click", "focus"];
        for trigger in triggers.iter() {
            let tooltip =
                RustTooltip::new("Test".to_string(), "Text".to_string(), "top", trigger, true);
            let html = tooltip.render();
            assert!(html.contains(&format!("data-bs-trigger=\"{trigger}\"")));
        }
    }

    #[test]
    fn test_tooltip_arrow() {
        let with_arrow =
            RustTooltip::new("Test".to_string(), "Text".to_string(), "top", "hover", true);
        let without_arrow = RustTooltip::new(
            "Test".to_string(),
            "Text".to_string(),
            "top",
            "hover",
            false,
        );

        assert!(with_arrow.render().contains("data-bs-arrow=\"true\""));
        assert!(!without_arrow.render().contains("data-bs-arrow"));
    }

    #[test]
    fn test_html_escape() {
        let tooltip = RustTooltip::new(
            "<script>alert('xss')</script>".to_string(),
            "Text with \"quotes\" & <tags>".to_string(),
            "top",
            "hover",
            true,
        );
        let html = tooltip.render();

        // Content should not be escaped (allows HTML content like icons)
        assert!(html.contains("<script>"));

        // But title attribute should be escaped
        assert!(html.contains("&quot;"));
        assert!(html.contains("&amp;"));
        assert!(html.contains("&lt;"));
        assert!(html.contains("&gt;"));
    }

    #[test]
    fn test_invalid_placement() {
        let tooltip = RustTooltip::new(
            "Test".to_string(),
            "Text".to_string(),
            "invalid",
            "hover",
            true,
        );
        let html = tooltip.render();
        // Should default to "top"
        assert!(html.contains("data-bs-placement=\"top\""));
    }

    #[test]
    fn test_invalid_trigger() {
        let tooltip = RustTooltip::new(
            "Test".to_string(),
            "Text".to_string(),
            "top",
            "invalid",
            true,
        );
        let html = tooltip.render();
        // Should default to "hover"
        assert!(html.contains("data-bs-trigger=\"hover\""));
    }
}
