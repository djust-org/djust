/*!
Column Component

A flex column component for grid layouts with:
- Responsive column sizing (1-12 grid)
- Auto-sizing
- Offset and order control
*/

use crate::html::element;
use crate::{Component, ComponentError, Framework};
use ahash::AHashMap as HashMap;
use djust_core::Value;

/// Column size (1-12 for grid, or Auto)
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum ColSize {
    Auto,
    Size(u8), // 1-12
}

/// Column component
pub struct Column {
    pub id: Option<String>,
    pub size: ColSize,
    pub sm: Option<ColSize>,
    pub md: Option<ColSize>,
    pub lg: Option<ColSize>,
    pub xl: Option<ColSize>,
    pub offset: Option<u8>,
    pub order: Option<u8>,
    pub children: Vec<String>,
}

impl Column {
    /// Create a new column with auto sizing
    pub fn new() -> Self {
        Self {
            id: None,
            size: ColSize::Auto,
            sm: None,
            md: None,
            lg: None,
            xl: None,
            offset: None,
            order: None,
            children: Vec::new(),
        }
    }

    /// Create a column with specific size (1-12)
    pub fn sized(size: u8) -> Self {
        assert!(
            (1..=12).contains(&size),
            "Column size must be between 1 and 12"
        );
        Self {
            id: None,
            size: ColSize::Size(size),
            sm: None,
            md: None,
            lg: None,
            xl: None,
            offset: None,
            order: None,
            children: Vec::new(),
        }
    }

    /// Set small breakpoint size
    pub fn sm(mut self, size: u8) -> Self {
        assert!(
            (1..=12).contains(&size),
            "Column size must be between 1 and 12"
        );
        self.sm = Some(ColSize::Size(size));
        self
    }

    /// Set medium breakpoint size
    pub fn md(mut self, size: u8) -> Self {
        assert!(
            (1..=12).contains(&size),
            "Column size must be between 1 and 12"
        );
        self.md = Some(ColSize::Size(size));
        self
    }

    /// Set large breakpoint size
    pub fn lg(mut self, size: u8) -> Self {
        assert!(
            (1..=12).contains(&size),
            "Column size must be between 1 and 12"
        );
        self.lg = Some(ColSize::Size(size));
        self
    }

    /// Set extra large breakpoint size
    pub fn xl(mut self, size: u8) -> Self {
        assert!(
            (1..=12).contains(&size),
            "Column size must be between 1 and 12"
        );
        self.xl = Some(ColSize::Size(size));
        self
    }

    /// Set offset
    pub fn offset(mut self, offset: u8) -> Self {
        assert!(
            (1..=11).contains(&offset),
            "Column offset must be between 1 and 11"
        );
        self.offset = Some(offset);
        self
    }

    /// Set order
    pub fn order(mut self, order: u8) -> Self {
        self.order = Some(order);
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
        let mut classes = Vec::new();

        // Base column class
        match self.size {
            ColSize::Auto => classes.push("col-auto".to_string()),
            ColSize::Size(n) => classes.push(format!("col-{n}")),
        }

        // Responsive sizes
        if let Some(ColSize::Size(n)) = self.sm {
            classes.push(format!("col-sm-{n}"));
        }
        if let Some(ColSize::Size(n)) = self.md {
            classes.push(format!("col-md-{n}"));
        }
        if let Some(ColSize::Size(n)) = self.lg {
            classes.push(format!("col-lg-{n}"));
        }
        if let Some(ColSize::Size(n)) = self.xl {
            classes.push(format!("col-xl-{n}"));
        }

        // Offset
        if let Some(offset) = self.offset {
            classes.push(format!("offset-{offset}"));
        }

        // Order
        if let Some(order) = self.order {
            classes.push(format!("order-{order}"));
        }

        let mut elem = element("div").classes(classes);

        if let Some(ref id) = self.id {
            elem = elem.attr("id", id);
        }

        let children_html = self.children.join("\n");
        elem.child(&children_html).build()
    }

    fn render_tailwind(&self) -> String {
        let mut classes = Vec::new();

        // Base flex class
        match self.size {
            ColSize::Auto => classes.push("flex-auto".to_string()),
            ColSize::Size(n) => {
                // Tailwind uses fractional widths
                let width = format!("w-{n}/12");
                classes.push(width);
            }
        }

        // Responsive sizes using Tailwind breakpoints
        if let Some(ColSize::Size(n)) = self.sm {
            classes.push(format!("sm:w-{n}/12"));
        }
        if let Some(ColSize::Size(n)) = self.md {
            classes.push(format!("md:w-{n}/12"));
        }
        if let Some(ColSize::Size(n)) = self.lg {
            classes.push(format!("lg:w-{n}/12"));
        }
        if let Some(ColSize::Size(n)) = self.xl {
            classes.push(format!("xl:w-{n}/12"));
        }

        // Order
        if let Some(order) = self.order {
            classes.push(format!("order-{order}"));
        }

        let mut elem = element("div").classes(classes);

        if let Some(ref id) = self.id {
            elem = elem.attr("id", id);
        }

        let children_html = self.children.join("\n");
        elem.child(&children_html).build()
    }

    fn render_plain(&self) -> String {
        let mut classes = vec!["col".to_string()];

        match self.size {
            ColSize::Auto => classes.push("col-auto".to_string()),
            ColSize::Size(n) => classes.push(format!("col-{n}")),
        }

        let mut elem = element("div").classes(classes);

        if let Some(ref id) = self.id {
            elem = elem.attr("id", id);
        }

        let children_html = self.children.join("\n");
        elem.child(&children_html).build()
    }
}

impl Default for Column {
    fn default() -> Self {
        Self::new()
    }
}

impl Component for Column {
    fn type_name(&self) -> &'static str {
        "Column"
    }

    fn id(&self) -> &str {
        self.id.as_deref().unwrap_or("")
    }

    fn get_state(&self) -> HashMap<String, Value> {
        HashMap::default()
    }

    fn set_state(&mut self, _state: HashMap<String, Value>) {
        // Column doesn't have mutable state
    }

    fn handle_event(
        &mut self,
        event: &str,
        _params: HashMap<String, Value>,
    ) -> Result<(), ComponentError> {
        Err(ComponentError::EventError(format!(
            "Column component does not handle events: {event}"
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
    fn test_column_auto() {
        let col = Column::new();
        let html = col.render(Framework::Bootstrap5).unwrap();
        assert!(html.contains("col-auto"));
    }

    #[test]
    fn test_column_sized() {
        let col = Column::sized(6);
        let html = col.render(Framework::Bootstrap5).unwrap();
        assert!(html.contains("col-6"));
    }

    #[test]
    fn test_column_responsive() {
        let col = Column::sized(12).md(6).lg(4);

        let html = col.render(Framework::Bootstrap5).unwrap();
        assert!(html.contains("col-12"));
        assert!(html.contains("col-md-6"));
        assert!(html.contains("col-lg-4"));
    }

    #[test]
    fn test_column_offset() {
        let col = Column::sized(6).offset(3);
        let html = col.render(Framework::Bootstrap5).unwrap();
        assert!(html.contains("offset-3"));
    }
}
