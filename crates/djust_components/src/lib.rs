/*!
# djust Components

Rust-backed UI components for djust with type-safe HTML generation.

Provides a component system similar to JustPy/NiceGUI but with:
- Compile-time type safety
- Framework-agnostic rendering (Bootstrap 5, Tailwind, Plain HTML)
- Zero-copy rendering directly in Rust
- Integration with djust's VDOM system
*/

use ahash::AHashMap as HashMap;
use djust_core::Value;
use once_cell::sync::Lazy;
use std::sync::RwLock;

pub mod complex;
pub mod html;
pub mod layout;
pub mod simple;
pub mod ui; // Simple stateless components (pure Rust PyO3)

// Python bindings module
pub mod python;

pub use html::HtmlBuilder;
pub use simple::{
    RustAlert, RustAvatar, RustBadge, RustButton, RustCard, RustDivider, RustIcon, RustModal,
    RustProgress, RustRange, RustSpinner, RustSwitch, RustTextArea, RustToast, RustTooltip,
};

/// CSS framework for rendering components
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Framework {
    Bootstrap5,
    Tailwind,
    Plain,
}

impl std::str::FromStr for Framework {
    type Err = std::convert::Infallible;

    fn from_str(s: &str) -> Result<Self, Self::Err> {
        Ok(match s.to_lowercase().as_str() {
            "bootstrap5" | "bootstrap" => Self::Bootstrap5,
            "tailwind" => Self::Tailwind,
            _ => Self::Plain,
        })
    }
}

/// Core trait for all components
pub trait Component: Send + Sync {
    /// Unique component type name
    fn type_name(&self) -> &'static str;

    /// Unique component instance ID
    fn id(&self) -> &str;

    /// Get component state as a Value
    fn get_state(&self) -> HashMap<String, Value>;

    /// Update component state from a Value map
    fn set_state(&mut self, state: HashMap<String, Value>);

    /// Handle an event (click, input, change, etc.)
    fn handle_event(
        &mut self,
        event: &str,
        params: HashMap<String, Value>,
    ) -> Result<(), ComponentError>;

    /// Render component to HTML string
    fn render(&self, framework: Framework) -> Result<String, ComponentError>;
}

/// Component errors
#[derive(Debug, thiserror::Error)]
pub enum ComponentError {
    #[error("Invalid state: {0}")]
    InvalidState(String),

    #[error("Render error: {0}")]
    RenderError(String),

    #[error("Event error: {0}")]
    EventError(String),

    #[error("Unknown framework")]
    UnknownFramework,
}

/// Global component registry
static COMPONENT_REGISTRY: Lazy<RwLock<HashMap<String, Box<dyn Component>>>> =
    Lazy::new(|| RwLock::new(HashMap::default()));

/// Register a component instance
pub fn register_component(id: String, component: Box<dyn Component>) {
    COMPONENT_REGISTRY.write().unwrap().insert(id, component);
}

/// Get a component by ID
pub fn get_component(id: &str) -> Option<Box<dyn Component>> {
    COMPONENT_REGISTRY.read().unwrap().get(id).map(|_c| {
        // This is a workaround since we can't clone trait objects directly
        // In practice, we'll need to implement a better cloning mechanism
        unimplemented!("Component cloning not yet implemented")
    })
}

/// Remove a component from the registry
pub fn unregister_component(id: &str) -> Option<Box<dyn Component>> {
    COMPONENT_REGISTRY.write().unwrap().remove(id)
}

/// Builder pattern helper trait
pub trait ComponentBuilder: Sized {
    type Component: Component;

    /// Build the final component
    fn build(self) -> Self::Component;
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_framework_from_str() {
        assert_eq!(
            "bootstrap5".parse::<Framework>().unwrap(),
            Framework::Bootstrap5
        );
        assert_eq!(
            "Bootstrap".parse::<Framework>().unwrap(),
            Framework::Bootstrap5
        );
        assert_eq!(
            "tailwind".parse::<Framework>().unwrap(),
            Framework::Tailwind
        );
        assert_eq!("plain".parse::<Framework>().unwrap(), Framework::Plain);
        assert_eq!("unknown".parse::<Framework>().unwrap(), Framework::Plain);
    }
}
