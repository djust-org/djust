/*!
Container Component

A responsive container component that wraps content with:
- Fluid or fixed width
- Responsive breakpoints
- Padding and margin control
*/

use crate::html::element;
use crate::{Component, ComponentError, Framework};
use ahash::AHashMap as HashMap;
use djust_core::Value;

/// Container type
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum ContainerType {
    /// Fixed width container with responsive breakpoints
    Fixed,
    /// Full width container
    Fluid,
    /// Small container (max-width: 540px)
    Small,
    /// Medium container (max-width: 720px)
    Medium,
    /// Large container (max-width: 960px)
    Large,
    /// Extra large container (max-width: 1140px)
    ExtraLarge,
}

/// Container component
pub struct Container {
    pub id: Option<String>,
    pub container_type: ContainerType,
    pub children: Vec<String>,
}

impl Container {
    /// Create a new container
    pub fn new() -> Self {
        Self {
            id: None,
            container_type: ContainerType::Fixed,
            children: Vec::new(),
        }
    }

    /// Create a fluid container
    pub fn fluid() -> Self {
        Self {
            id: None,
            container_type: ContainerType::Fluid,
            children: Vec::new(),
        }
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
        let class = match self.container_type {
            ContainerType::Fixed => "container",
            ContainerType::Fluid => "container-fluid",
            ContainerType::Small => "container-sm",
            ContainerType::Medium => "container-md",
            ContainerType::Large => "container-lg",
            ContainerType::ExtraLarge => "container-xl",
        };

        let mut elem = element("div").class(class);

        if let Some(ref id) = self.id {
            elem = elem.attr("id", id);
        }

        // Join children HTML
        let children_html = self.children.join("\n");

        elem.child(&children_html).build()
    }

    fn render_tailwind(&self) -> String {
        let mut classes = vec!["container", "mx-auto"];

        match self.container_type {
            ContainerType::Fluid => classes.push("w-full"),
            ContainerType::Small => classes.push("max-w-screen-sm"),
            ContainerType::Medium => classes.push("max-w-screen-md"),
            ContainerType::Large => classes.push("max-w-screen-lg"),
            ContainerType::ExtraLarge => classes.push("max-w-screen-xl"),
            ContainerType::Fixed => {
                classes.push("max-w-7xl");
            }
        }

        let mut elem = element("div").classes(classes);

        if let Some(ref id) = self.id {
            elem = elem.attr("id", id);
        }

        let children_html = self.children.join("\n");
        elem.child(&children_html).build()
    }

    fn render_plain(&self) -> String {
        let mut classes = vec!["container"];

        match self.container_type {
            ContainerType::Fluid => classes.push("container-fluid"),
            ContainerType::Small => classes.push("container-sm"),
            ContainerType::Medium => classes.push("container-md"),
            ContainerType::Large => classes.push("container-lg"),
            ContainerType::ExtraLarge => classes.push("container-xl"),
            ContainerType::Fixed => {}
        }

        let mut elem = element("div").classes(classes);

        if let Some(ref id) = self.id {
            elem = elem.attr("id", id);
        }

        let children_html = self.children.join("\n");
        elem.child(&children_html).build()
    }
}

impl Default for Container {
    fn default() -> Self {
        Self::new()
    }
}

impl Component for Container {
    fn type_name(&self) -> &'static str {
        "Container"
    }

    fn id(&self) -> &str {
        self.id.as_deref().unwrap_or("")
    }

    fn get_state(&self) -> HashMap<String, Value> {
        HashMap::default()
    }

    fn set_state(&mut self, _state: HashMap<String, Value>) {
        // Container doesn't have mutable state
    }

    fn handle_event(
        &mut self,
        event: &str,
        _params: HashMap<String, Value>,
    ) -> Result<(), ComponentError> {
        Err(ComponentError::EventError(format!(
            "Container component does not handle events: {event}"
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
    fn test_container_default() {
        let container = Container::new();
        let html = container.render(Framework::Bootstrap5).unwrap();
        assert!(html.contains("class=\"container\""));
    }

    #[test]
    fn test_container_fluid() {
        let container = Container::fluid();
        let html = container.render(Framework::Bootstrap5).unwrap();
        assert!(html.contains("container-fluid"));
    }

    #[test]
    fn test_container_with_children() {
        let container = Container::new().child("<p>Hello</p>").child("<p>World</p>");

        let html = container.render(Framework::Bootstrap5).unwrap();
        assert!(html.contains("<p>Hello</p>"));
        assert!(html.contains("<p>World</p>"));
    }
}
