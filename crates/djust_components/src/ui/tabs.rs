/*!
Tabs Component

A tabbed interface component with:
- Multiple tabs with labels and content
- Active tab selection
- Variants for styling
- Vertical/horizontal orientation
*/

use crate::html::element;
use crate::{Component, ComponentError, Framework};
use ahash::AHashMap as HashMap;
use djust_core::Value;

/// Tab variant
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum TabVariant {
    Default,
    Pills,
    Underline,
}

/// Tab item
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct TabItem {
    pub id: String,
    pub label: String,
    pub content: String,
}

/// Tabs component
pub struct Tabs {
    pub id: String,
    pub tabs: Vec<TabItem>,
    pub active: String,
    pub variant: TabVariant,
    pub vertical: bool,
}

impl Tabs {
    /// Create a new tabs component
    pub fn new(id: impl Into<String>) -> Self {
        Self {
            id: id.into(),
            tabs: Vec::new(),
            active: String::new(),
            variant: TabVariant::Default,
            vertical: false,
        }
    }

    /// Add a tab
    pub fn tab(
        mut self,
        id: impl Into<String>,
        label: impl Into<String>,
        content: impl Into<String>,
    ) -> Self {
        let tab_id = id.into();
        if self.tabs.is_empty() {
            self.active = tab_id.clone();
        }
        self.tabs.push(TabItem {
            id: tab_id,
            label: label.into(),
            content: content.into(),
        });
        self
    }

    /// Set tabs from vector
    pub fn tabs(mut self, tabs: Vec<TabItem>) -> Self {
        if !tabs.is_empty() && self.active.is_empty() {
            self.active = tabs[0].id.clone();
        }
        self.tabs = tabs;
        self
    }

    /// Set active tab
    pub fn active(mut self, active: impl Into<String>) -> Self {
        self.active = active.into();
        self
    }

    /// Set variant
    pub fn variant(mut self, variant: TabVariant) -> Self {
        self.variant = variant;
        self
    }

    /// Set vertical orientation
    pub fn vertical(mut self, vertical: bool) -> Self {
        self.vertical = vertical;
        self
    }

    // Render methods for different frameworks
    fn render_bootstrap(&self) -> String {
        let mut nav_classes = vec!["nav".to_string()];

        match self.variant {
            TabVariant::Default => nav_classes.push("nav-tabs".to_string()),
            TabVariant::Pills => nav_classes.push("nav-pills".to_string()),
            TabVariant::Underline => nav_classes.push("nav-tabs".to_string()),
        }

        if self.vertical {
            nav_classes.push("flex-column".to_string());
        }

        // Nav items
        let mut nav_items = Vec::new();
        for tab in &self.tabs {
            let is_active = tab.id == self.active;
            let mut link_classes = vec!["nav-link".to_string()];
            if is_active {
                link_classes.push("active".to_string());
            }

            let item_html = element("li")
                .class("nav-item")
                .child(
                    element("a")
                        .classes(link_classes)
                        .attr("id", format!("{}-tab", tab.id))
                        .attr("data-bs-toggle", "tab")
                        .attr("data-bs-target", format!("#{}-pane", tab.id))
                        .attr("type", "button")
                        .attr("role", "tab")
                        .attr("aria-controls", format!("{}-pane", tab.id))
                        .attr("aria-selected", if is_active { "true" } else { "false" })
                        .child(&tab.label)
                        .build(),
                )
                .build();
            nav_items.push(item_html);
        }

        let nav_html = element("ul")
            .classes(nav_classes)
            .attr("id", format!("{}-nav", self.id))
            .attr("role", "tablist")
            .child(nav_items.join("\n"))
            .build();

        // Tab content
        let mut content_panes = Vec::new();
        for tab in &self.tabs {
            let is_active = tab.id == self.active;
            let mut pane_classes = vec!["tab-pane".to_string(), "fade".to_string()];
            if is_active {
                pane_classes.push("show".to_string());
                pane_classes.push("active".to_string());
            }

            let pane_html = element("div")
                .classes(pane_classes)
                .attr("id", format!("{}-pane", tab.id))
                .attr("role", "tabpanel")
                .attr("aria-labelledby", format!("{}-tab", tab.id))
                .child(&tab.content)
                .build();
            content_panes.push(pane_html);
        }

        let content_html = element("div")
            .class("tab-content")
            .attr("id", format!("{}-content", self.id))
            .child(content_panes.join("\n"))
            .build();

        if self.vertical {
            element("div")
                .classes(vec!["d-flex", "align-items-start"])
                .child(format!("{nav_html}\n{content_html}"))
                .build()
        } else {
            format!("{nav_html}\n{content_html}")
        }
    }

    fn render_tailwind(&self) -> String {
        // Nav tabs
        let mut nav_classes = vec!["flex".to_string()];

        if self.vertical {
            nav_classes.push("flex-col".to_string());
            nav_classes.push("space-y-2".to_string());
        } else {
            nav_classes.push("border-b".to_string());
            nav_classes.push("border-gray-200".to_string());
            nav_classes.push("space-x-2".to_string());
        }

        let mut nav_items = Vec::new();
        for tab in &self.tabs {
            let is_active = tab.id == self.active;

            let mut button_classes = vec![
                "px-4".to_string(),
                "py-2".to_string(),
                "font-medium".to_string(),
                "transition-colors".to_string(),
                "duration-200".to_string(),
            ];

            match self.variant {
                TabVariant::Default | TabVariant::Underline => {
                    if is_active {
                        button_classes.push("text-blue-600".to_string());
                        button_classes.push("border-b-2".to_string());
                        button_classes.push("border-blue-600".to_string());
                    } else {
                        button_classes.push("text-gray-600".to_string());
                        button_classes.push("hover:text-blue-600".to_string());
                        button_classes.push("border-b-2".to_string());
                        button_classes.push("border-transparent".to_string());
                    }
                }
                TabVariant::Pills => {
                    button_classes.push("rounded".to_string());
                    if is_active {
                        button_classes.push("bg-blue-600".to_string());
                        button_classes.push("text-white".to_string());
                    } else {
                        button_classes.push("text-gray-600".to_string());
                        button_classes.push("hover:bg-gray-100".to_string());
                    }
                }
            }

            let item_html = element("button")
                .classes(button_classes)
                .attr("type", "button")
                .attr("id", format!("{}-tab", tab.id))
                .attr("data-tab", &tab.id)
                .child(&tab.label)
                .build();
            nav_items.push(item_html);
        }

        let nav_html = element("div")
            .classes(nav_classes)
            .attr("id", format!("{}-nav", self.id))
            .child(nav_items.join("\n"))
            .build();

        // Tab content
        let mut content_panes = Vec::new();
        for tab in &self.tabs {
            let is_active = tab.id == self.active;
            let mut pane_classes = vec!["p-4".to_string()];
            if !is_active {
                pane_classes.push("hidden".to_string());
            }

            let pane_html = element("div")
                .classes(pane_classes)
                .attr("id", format!("{}-pane", tab.id))
                .attr("data-tab-content", &tab.id)
                .child(&tab.content)
                .build();
            content_panes.push(pane_html);
        }

        let content_html = element("div")
            .attr("id", format!("{}-content", self.id))
            .child(content_panes.join("\n"))
            .build();

        if self.vertical {
            element("div")
                .class("flex")
                .child(format!("{nav_html}\n{content_html}"))
                .build()
        } else {
            format!("{nav_html}\n{content_html}")
        }
    }

    fn render_plain(&self) -> String {
        let mut nav_classes = vec!["tabs-nav".to_string()];

        match self.variant {
            TabVariant::Default => nav_classes.push("tabs-default".to_string()),
            TabVariant::Pills => nav_classes.push("tabs-pills".to_string()),
            TabVariant::Underline => nav_classes.push("tabs-underline".to_string()),
        }

        if self.vertical {
            nav_classes.push("tabs-vertical".to_string());
        }

        // Nav items
        let mut nav_items = Vec::new();
        for tab in &self.tabs {
            let is_active = tab.id == self.active;
            let mut item_classes = vec!["tab".to_string()];
            if is_active {
                item_classes.push("active".to_string());
            }

            let item_html = element("button")
                .classes(item_classes)
                .attr("type", "button")
                .attr("id", format!("{}-tab", tab.id))
                .attr("data-tab", &tab.id)
                .child(&tab.label)
                .build();
            nav_items.push(item_html);
        }

        let nav_html = element("div")
            .classes(nav_classes)
            .attr("id", format!("{}-nav", self.id))
            .child(nav_items.join("\n"))
            .build();

        // Tab content
        let mut content_panes = Vec::new();
        for tab in &self.tabs {
            let is_active = tab.id == self.active;
            let mut pane_classes = vec!["tab-pane".to_string()];
            if !is_active {
                pane_classes.push("hidden".to_string());
            }

            let pane_html = element("div")
                .classes(pane_classes)
                .attr("id", format!("{}-pane", tab.id))
                .child(&tab.content)
                .build();
            content_panes.push(pane_html);
        }

        let content_html = element("div")
            .class("tabs-content")
            .attr("id", format!("{}-content", self.id))
            .child(content_panes.join("\n"))
            .build();

        element("div")
            .class("tabs")
            .child(format!("{nav_html}\n{content_html}"))
            .build()
    }
}

impl Component for Tabs {
    fn type_name(&self) -> &'static str {
        "Tabs"
    }

    fn id(&self) -> &str {
        &self.id
    }

    fn get_state(&self) -> HashMap<String, Value> {
        let mut state = HashMap::default();
        state.insert("active".to_string(), Value::String(self.active.clone()));
        state
    }

    fn set_state(&mut self, mut state: HashMap<String, Value>) {
        if let Some(Value::String(active)) = state.remove("active") {
            self.active = active;
        }
    }

    fn handle_event(
        &mut self,
        event: &str,
        mut params: HashMap<String, Value>,
    ) -> Result<(), ComponentError> {
        match event {
            "activate" => {
                if let Some(Value::String(tab_id)) = params.remove("tab") {
                    self.active = tab_id;
                    Ok(())
                } else {
                    Err(ComponentError::EventError(
                        "Missing 'tab' parameter".to_string(),
                    ))
                }
            }
            _ => Err(ComponentError::EventError(format!(
                "Unknown event: {event}"
            ))),
        }
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
    fn test_tabs_basic() {
        let tabs = Tabs::new("testTabs").tab("tab1", "Tab 1", "Content 1").tab(
            "tab2",
            "Tab 2",
            "Content 2",
        );
        let html = tabs.render(Framework::Bootstrap5).unwrap();
        assert!(html.contains("Tab 1"));
        assert!(html.contains("Tab 2"));
        assert!(html.contains("Content 1"));
    }

    #[test]
    fn test_tabs_active() {
        let tabs = Tabs::new("testTabs")
            .tab("tab1", "Tab 1", "Content 1")
            .tab("tab2", "Tab 2", "Content 2")
            .active("tab2");
        let html = tabs.render(Framework::Bootstrap5).unwrap();
        assert!(html.contains("tab2"));
    }

    #[test]
    fn test_tabs_pills() {
        let tabs = Tabs::new("testTabs")
            .tab("tab1", "Tab 1", "Content 1")
            .variant(TabVariant::Pills);
        let html = tabs.render(Framework::Bootstrap5).unwrap();
        assert!(html.contains("nav-pills"));
    }

    #[test]
    fn test_tabs_vertical() {
        let tabs = Tabs::new("testTabs")
            .tab("tab1", "Tab 1", "Content 1")
            .vertical(true);
        let html = tabs.render(Framework::Bootstrap5).unwrap();
        assert!(html.contains("flex-column"));
    }
}
