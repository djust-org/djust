/*!
Row Component

A flex row component for grid layouts with:
- Responsive gutters
- Alignment options
- Column wrapping
*/

use crate::html::element;
use crate::{Component, ComponentError, Framework};
use ahash::AHashMap as HashMap;
use djust_core::Value;

/// Horizontal alignment
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum HorizontalAlign {
    Start,
    Center,
    End,
    Between,
    Around,
    Evenly,
}

/// Vertical alignment
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum VerticalAlign {
    Start,
    Center,
    End,
    Baseline,
    Stretch,
}

/// Gutter size
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum GutterSize {
    None,
    Small,
    Medium,
    Large,
    ExtraLarge,
}

/// Row component
pub struct Row {
    pub id: Option<String>,
    pub h_align: Option<HorizontalAlign>,
    pub v_align: Option<VerticalAlign>,
    pub gutter: GutterSize,
    pub no_wrap: bool,
    pub children: Vec<String>,
}

impl Row {
    /// Create a new row
    pub fn new() -> Self {
        Self {
            id: None,
            h_align: None,
            v_align: None,
            gutter: GutterSize::Medium,
            no_wrap: false,
            children: Vec::new(),
        }
    }

    /// Set horizontal alignment
    pub fn h_align(mut self, align: HorizontalAlign) -> Self {
        self.h_align = Some(align);
        self
    }

    /// Set vertical alignment
    pub fn v_align(mut self, align: VerticalAlign) -> Self {
        self.v_align = Some(align);
        self
    }

    /// Set gutter size
    pub fn gutter(mut self, size: GutterSize) -> Self {
        self.gutter = size;
        self
    }

    /// Disable column wrapping
    pub fn no_wrap(mut self, no_wrap: bool) -> Self {
        self.no_wrap = no_wrap;
        self
    }

    /// Add HTML content as child
    pub fn child(mut self, html: impl Into<String>) -> Self {
        self.children.push(html.into());
        self
    }

    /// Add multiple children
    pub fn children(mut self, children: Vec<String>) -> Self {
        self.children.extend(children);
        self
    }

    /// Set ID
    pub fn with_id(mut self, id: impl Into<String>) -> Self {
        self.id = Some(id.into());
        self
    }

    // Render methods for different frameworks
    fn render_bootstrap(&self) -> String {
        let mut classes = vec!["row"];

        // Gutter
        match self.gutter {
            GutterSize::None => classes.push("g-0"),
            GutterSize::Small => classes.push("g-2"),
            GutterSize::Medium => {} // Default Bootstrap gutter
            GutterSize::Large => classes.push("g-4"),
            GutterSize::ExtraLarge => classes.push("g-5"),
        }

        // Horizontal alignment
        if let Some(align) = self.h_align {
            let class = match align {
                HorizontalAlign::Start => "justify-content-start",
                HorizontalAlign::Center => "justify-content-center",
                HorizontalAlign::End => "justify-content-end",
                HorizontalAlign::Between => "justify-content-between",
                HorizontalAlign::Around => "justify-content-around",
                HorizontalAlign::Evenly => "justify-content-evenly",
            };
            classes.push(class);
        }

        // Vertical alignment
        if let Some(align) = self.v_align {
            let class = match align {
                VerticalAlign::Start => "align-items-start",
                VerticalAlign::Center => "align-items-center",
                VerticalAlign::End => "align-items-end",
                VerticalAlign::Baseline => "align-items-baseline",
                VerticalAlign::Stretch => "align-items-stretch",
            };
            classes.push(class);
        }

        let mut elem = element("div").classes(classes);

        if let Some(ref id) = self.id {
            elem = elem.attr("id", id);
        }

        let children_html = self.children.join("\n");
        elem.child(&children_html).build()
    }

    fn render_tailwind(&self) -> String {
        let mut classes = vec!["flex"];

        if !self.no_wrap {
            classes.push("flex-wrap");
        }

        // Gutter using gap
        match self.gutter {
            GutterSize::None => {}
            GutterSize::Small => classes.push("gap-2"),
            GutterSize::Medium => classes.push("gap-4"),
            GutterSize::Large => classes.push("gap-6"),
            GutterSize::ExtraLarge => classes.push("gap-8"),
        }

        // Horizontal alignment
        if let Some(align) = self.h_align {
            let class = match align {
                HorizontalAlign::Start => "justify-start",
                HorizontalAlign::Center => "justify-center",
                HorizontalAlign::End => "justify-end",
                HorizontalAlign::Between => "justify-between",
                HorizontalAlign::Around => "justify-around",
                HorizontalAlign::Evenly => "justify-evenly",
            };
            classes.push(class);
        }

        // Vertical alignment
        if let Some(align) = self.v_align {
            let class = match align {
                VerticalAlign::Start => "items-start",
                VerticalAlign::Center => "items-center",
                VerticalAlign::End => "items-end",
                VerticalAlign::Baseline => "items-baseline",
                VerticalAlign::Stretch => "items-stretch",
            };
            classes.push(class);
        }

        let mut elem = element("div").classes(classes);

        if let Some(ref id) = self.id {
            elem = elem.attr("id", id);
        }

        let children_html = self.children.join("\n");
        elem.child(&children_html).build()
    }

    fn render_plain(&self) -> String {
        let mut classes = vec!["row"];

        // Add alignment classes
        if let Some(align) = self.h_align {
            classes.push(match align {
                HorizontalAlign::Start => "row-justify-start",
                HorizontalAlign::Center => "row-justify-center",
                HorizontalAlign::End => "row-justify-end",
                HorizontalAlign::Between => "row-justify-between",
                HorizontalAlign::Around => "row-justify-around",
                HorizontalAlign::Evenly => "row-justify-evenly",
            });
        }

        if let Some(align) = self.v_align {
            classes.push(match align {
                VerticalAlign::Start => "row-align-start",
                VerticalAlign::Center => "row-align-center",
                VerticalAlign::End => "row-align-end",
                VerticalAlign::Baseline => "row-align-baseline",
                VerticalAlign::Stretch => "row-align-stretch",
            });
        }

        let mut elem = element("div").classes(classes);

        if let Some(ref id) = self.id {
            elem = elem.attr("id", id);
        }

        let children_html = self.children.join("\n");
        elem.child(&children_html).build()
    }
}

impl Default for Row {
    fn default() -> Self {
        Self::new()
    }
}

impl Component for Row {
    fn type_name(&self) -> &'static str {
        "Row"
    }

    fn id(&self) -> &str {
        self.id.as_deref().unwrap_or("")
    }

    fn get_state(&self) -> HashMap<String, Value> {
        HashMap::default()
    }

    fn set_state(&mut self, _state: HashMap<String, Value>) {
        // Row doesn't have mutable state
    }

    fn handle_event(
        &mut self,
        event: &str,
        _params: HashMap<String, Value>,
    ) -> Result<(), ComponentError> {
        Err(ComponentError::EventError(format!(
            "Row component does not handle events: {event}"
        )))
    }

    fn render(&self, framework: Framework) -> Result<String, ComponentError> {
        Ok(match framework {
            Framework::Bootstrap5 => self.render_bootstrap(),
            Framework::Tailwind => self.render_tailwind(),
            Framework::Plain => self.render_plain(),
        })
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_row_default() {
        let row = Row::new();
        let html = row.render(Framework::Bootstrap5).unwrap();
        assert!(html.contains("class=\"row\""));
    }

    #[test]
    fn test_row_with_alignment() {
        let row = Row::new()
            .h_align(HorizontalAlign::Center)
            .v_align(VerticalAlign::Center);

        let html = row.render(Framework::Bootstrap5).unwrap();
        assert!(html.contains("justify-content-center"));
        assert!(html.contains("align-items-center"));
    }

    #[test]
    fn test_row_with_gutter() {
        let row = Row::new().gutter(GutterSize::None);
        let html = row.render(Framework::Bootstrap5).unwrap();
        assert!(html.contains("g-0"));
    }
}
