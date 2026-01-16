/*!
Dropdown Component

A dropdown/select component with:
- Multiple variants
- Multiple sizes
- Item selection
- Disabled state
*/

use crate::html::element;
use crate::{Component, ComponentError, Framework};
use ahash::AHashMap as HashMap;
use djust_core::Value;

/// Dropdown variant
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum DropdownVariant {
    Primary,
    Secondary,
    Success,
    Danger,
    Warning,
    Info,
    Light,
    Dark,
}

/// Dropdown size
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum DropdownSize {
    Small,
    Medium,
    Large,
}

/// Dropdown item
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct DropdownItem {
    pub label: String,
    pub value: String,
}

/// Dropdown component
pub struct Dropdown {
    pub id: String,
    pub items: Vec<DropdownItem>,
    pub selected: Option<String>,
    pub variant: DropdownVariant,
    pub size: DropdownSize,
    pub disabled: bool,
    pub placeholder: Option<String>,
}

impl Dropdown {
    /// Create a new dropdown
    pub fn new(id: impl Into<String>) -> Self {
        Self {
            id: id.into(),
            items: Vec::new(),
            selected: None,
            variant: DropdownVariant::Primary,
            size: DropdownSize::Medium,
            disabled: false,
            placeholder: None,
        }
    }

    /// Add an item
    pub fn item(mut self, label: impl Into<String>, value: impl Into<String>) -> Self {
        self.items.push(DropdownItem {
            label: label.into(),
            value: value.into(),
        });
        self
    }

    /// Set items from vector
    pub fn items(mut self, items: Vec<DropdownItem>) -> Self {
        self.items = items;
        self
    }

    /// Set selected value
    pub fn selected(mut self, value: impl Into<String>) -> Self {
        self.selected = Some(value.into());
        self
    }

    /// Set variant
    pub fn variant(mut self, variant: DropdownVariant) -> Self {
        self.variant = variant;
        self
    }

    /// Set size
    pub fn size(mut self, size: DropdownSize) -> Self {
        self.size = size;
        self
    }

    /// Set disabled
    pub fn disabled(mut self, disabled: bool) -> Self {
        self.disabled = disabled;
        self
    }

    /// Set placeholder
    pub fn placeholder(mut self, placeholder: impl Into<String>) -> Self {
        self.placeholder = Some(placeholder.into());
        self
    }

    // Render methods for different frameworks
    fn render_bootstrap(&self) -> String {
        let mut classes = vec!["btn".to_string(), "dropdown-toggle".to_string()];

        // Variant
        let variant_class = match self.variant {
            DropdownVariant::Primary => "btn-primary",
            DropdownVariant::Secondary => "btn-secondary",
            DropdownVariant::Success => "btn-success",
            DropdownVariant::Danger => "btn-danger",
            DropdownVariant::Warning => "btn-warning",
            DropdownVariant::Info => "btn-info",
            DropdownVariant::Light => "btn-light",
            DropdownVariant::Dark => "btn-dark",
        };
        classes.push(variant_class.to_string());

        // Size
        match self.size {
            DropdownSize::Small => classes.push("btn-sm".to_string()),
            DropdownSize::Medium => {}
            DropdownSize::Large => classes.push("btn-lg".to_string()),
        }

        let dropdown_btn = element("button")
            .classes(classes)
            .attr("type", "button")
            .attr("id", &self.id)
            .attr("data-bs-toggle", "dropdown")
            .attr("aria-expanded", "false");

        let dropdown_btn = if self.disabled {
            dropdown_btn.attr("disabled", "disabled")
        } else {
            dropdown_btn
        };

        // Button label
        let label = if let Some(ref sel) = self.selected {
            self.items
                .iter()
                .find(|item| &item.value == sel)
                .map(|item| item.label.clone())
                .unwrap_or_else(|| sel.clone())
        } else {
            self.placeholder
                .clone()
                .unwrap_or_else(|| "Select...".to_string())
        };

        let button_html = dropdown_btn.child(&label).build();

        // Dropdown menu
        let mut menu_items = Vec::new();
        for item in &self.items {
            let is_selected = self.selected.as_ref() == Some(&item.value);
            let mut item_classes = vec!["dropdown-item".to_string()];
            if is_selected {
                item_classes.push("active".to_string());
            }

            let item_html = element("a")
                .classes(item_classes)
                .attr("href", "#")
                .attr("data-value", &item.value)
                .child(&item.label)
                .build();
            menu_items.push(item_html);
        }

        let menu_html = element("ul")
            .class("dropdown-menu")
            .attr("aria-labelledby", &self.id)
            .child(menu_items.join("\n"))
            .build();

        // Wrap in dropdown div
        element("div")
            .class("dropdown")
            .child(format!("{button_html}\n{menu_html}"))
            .build()
    }

    fn render_tailwind(&self) -> String {
        let mut button_classes = vec![
            "relative".to_string(),
            "inline-flex".to_string(),
            "items-center".to_string(),
            "justify-between".to_string(),
            "rounded".to_string(),
            "font-medium".to_string(),
            "transition-colors".to_string(),
            "duration-200".to_string(),
        ];

        // Variant colors
        let (bg_class, hover_class, text_class) = match self.variant {
            DropdownVariant::Primary => ("bg-blue-600", "hover:bg-blue-700", "text-white"),
            DropdownVariant::Secondary => ("bg-gray-600", "hover:bg-gray-700", "text-white"),
            DropdownVariant::Success => ("bg-green-600", "hover:bg-green-700", "text-white"),
            DropdownVariant::Danger => ("bg-red-600", "hover:bg-red-700", "text-white"),
            DropdownVariant::Warning => ("bg-yellow-600", "hover:bg-yellow-700", "text-white"),
            DropdownVariant::Info => ("bg-cyan-600", "hover:bg-cyan-700", "text-white"),
            DropdownVariant::Light => ("bg-gray-100", "hover:bg-gray-200", "text-gray-800"),
            DropdownVariant::Dark => ("bg-gray-800", "hover:bg-gray-900", "text-white"),
        };

        button_classes.push(bg_class.to_string());
        button_classes.push(hover_class.to_string());
        button_classes.push(text_class.to_string());

        // Size
        let (padding_class, text_size_class) = match self.size {
            DropdownSize::Small => ("px-2 py-1", "text-sm"),
            DropdownSize::Medium => ("px-4 py-2", "text-base"),
            DropdownSize::Large => ("px-6 py-3", "text-lg"),
        };
        button_classes.push(padding_class.to_string());
        button_classes.push(text_size_class.to_string());

        if self.disabled {
            button_classes.push("opacity-50".to_string());
            button_classes.push("cursor-not-allowed".to_string());
        } else {
            button_classes.push("cursor-pointer".to_string());
        }

        // Button label
        let label = if let Some(ref sel) = self.selected {
            self.items
                .iter()
                .find(|item| &item.value == sel)
                .map(|item| item.label.clone())
                .unwrap_or_else(|| sel.clone())
        } else {
            self.placeholder
                .clone()
                .unwrap_or_else(|| "Select...".to_string())
        };

        let button = element("button")
            .classes(button_classes)
            .attr("type", "button")
            .attr("id", &self.id);

        let button = if self.disabled {
            button.attr("disabled", "disabled")
        } else {
            button
        };

        let button_html = button
            .child(format!(
                r#"{label}<svg class="w-4 h-4 ml-2" fill="currentColor" viewBox="0 0 20 20"><path fill-rule="evenodd" d="M5.293 7.293a1 1 0 011.414 0L10 10.586l3.293-3.293a1 1 0 111.414 1.414l-4 4a1 1 0 01-1.414 0l-4-4a1 1 0 010-1.414z" clip-rule="evenodd"></path></svg>"#
            ))
            .build();

        // Dropdown menu
        let mut menu_items = Vec::new();
        for item in &self.items {
            let is_selected = self.selected.as_ref() == Some(&item.value);
            let mut item_classes = vec![
                "block".to_string(),
                "px-4".to_string(),
                "py-2".to_string(),
                "hover:bg-gray-100".to_string(),
                "cursor-pointer".to_string(),
            ];
            if is_selected {
                item_classes.push("bg-gray-200".to_string());
            }

            let item_html = element("a")
                .classes(item_classes)
                .attr("href", "#")
                .attr("data-value", &item.value)
                .child(&item.label)
                .build();
            menu_items.push(item_html);
        }

        let menu_html = element("div")
            .classes(vec![
                "absolute",
                "mt-2",
                "w-full",
                "bg-white",
                "border",
                "border-gray-200",
                "rounded",
                "shadow-lg",
                "z-10",
                "hidden",
            ])
            .child(menu_items.join("\n"))
            .build();

        // Wrap in relative div
        element("div")
            .class("relative")
            .child(format!("{button_html}\n{menu_html}"))
            .build()
    }

    fn render_plain(&self) -> String {
        let mut classes = vec!["dropdown-toggle".to_string()];

        let variant_class = match self.variant {
            DropdownVariant::Primary => "dropdown-primary",
            DropdownVariant::Secondary => "dropdown-secondary",
            DropdownVariant::Success => "dropdown-success",
            DropdownVariant::Danger => "dropdown-danger",
            DropdownVariant::Warning => "dropdown-warning",
            DropdownVariant::Info => "dropdown-info",
            DropdownVariant::Light => "dropdown-light",
            DropdownVariant::Dark => "dropdown-dark",
        };
        classes.push(variant_class.to_string());

        match self.size {
            DropdownSize::Small => classes.push("dropdown-sm".to_string()),
            DropdownSize::Medium => {}
            DropdownSize::Large => classes.push("dropdown-lg".to_string()),
        }

        let button = element("button")
            .classes(classes)
            .attr("type", "button")
            .attr("id", &self.id);

        let button = if self.disabled {
            button.attr("disabled", "disabled")
        } else {
            button
        };

        let label = if let Some(ref sel) = self.selected {
            self.items
                .iter()
                .find(|item| &item.value == sel)
                .map(|item| item.label.clone())
                .unwrap_or_else(|| sel.clone())
        } else {
            self.placeholder
                .clone()
                .unwrap_or_else(|| "Select...".to_string())
        };

        let button_html = button.child(&label).build();

        let mut menu_items = Vec::new();
        for item in &self.items {
            let is_selected = self.selected.as_ref() == Some(&item.value);
            let mut item_classes = vec!["dropdown-item".to_string()];
            if is_selected {
                item_classes.push("active".to_string());
            }

            let item_html = element("a")
                .classes(item_classes)
                .attr("href", "#")
                .attr("data-value", &item.value)
                .child(&item.label)
                .build();
            menu_items.push(item_html);
        }

        let menu_html = element("ul")
            .class("dropdown-menu")
            .child(menu_items.join("\n"))
            .build();

        element("div")
            .class("dropdown")
            .child(format!("{button_html}\n{menu_html}"))
            .build()
    }
}

impl Component for Dropdown {
    fn type_name(&self) -> &'static str {
        "Dropdown"
    }

    fn id(&self) -> &str {
        &self.id
    }

    fn get_state(&self) -> HashMap<String, Value> {
        let mut state = HashMap::default();
        if let Some(ref sel) = self.selected {
            state.insert("selected".to_string(), Value::String(sel.clone()));
        }
        state
    }

    fn set_state(&mut self, mut state: HashMap<String, Value>) {
        if let Some(Value::String(sel)) = state.remove("selected") {
            self.selected = Some(sel);
        }
    }

    fn handle_event(
        &mut self,
        event: &str,
        mut params: HashMap<String, Value>,
    ) -> Result<(), ComponentError> {
        match event {
            "select" => {
                if let Some(Value::String(value)) = params.remove("value") {
                    self.selected = Some(value);
                    Ok(())
                } else {
                    Err(ComponentError::EventError(
                        "Missing 'value' parameter".to_string(),
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
    fn test_dropdown_basic() {
        let dropdown = Dropdown::new("testDropdown")
            .item("Option 1", "opt1")
            .item("Option 2", "opt2");
        let html = dropdown.render(Framework::Bootstrap5).unwrap();
        assert!(html.contains("dropdown"));
        assert!(html.contains("Option 1"));
        assert!(html.contains("Option 2"));
    }

    #[test]
    fn test_dropdown_with_selection() {
        let dropdown = Dropdown::new("testDropdown")
            .item("Option 1", "opt1")
            .item("Option 2", "opt2")
            .selected("opt1");
        let html = dropdown.render(Framework::Bootstrap5).unwrap();
        assert!(html.contains("Option 1"));
    }

    #[test]
    fn test_dropdown_variants() {
        let dropdown = Dropdown::new("testDropdown")
            .item("Test", "test")
            .variant(DropdownVariant::Success);
        let html = dropdown.render(Framework::Bootstrap5).unwrap();
        assert!(html.contains("btn-success"));
    }

    #[test]
    fn test_dropdown_disabled() {
        let dropdown = Dropdown::new("testDropdown")
            .item("Test", "test")
            .disabled(true);
        let html = dropdown.render(Framework::Bootstrap5).unwrap();
        assert!(html.contains("disabled"));
    }
}
