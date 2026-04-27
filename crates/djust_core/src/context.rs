//! Template context management

use crate::Value;
use ahash::{AHashMap, AHashSet};
use pyo3::prelude::*;
use std::collections::HashMap;

/// A context for template rendering, similar to Django's Context
///
/// In addition to JSON-friendly `Value` entries, `Context` can hold a
/// sidecar map of raw Python objects (e.g. Django model instances) for
/// `getattr`-style fallback lookups when a nested key like
/// `user.username` cannot be resolved through the normal value stack.
#[derive(Debug, Default)]
pub struct Context {
    stack: Vec<AHashMap<String, Value>>,
    /// Keys marked as safe (skip auto-escaping), like Django's SafeData
    safe_keys: AHashSet<String>,
    /// Track loop variable mappings: loop_var -> (iterable_name, index)
    /// e.g., "item" -> ("items", 0) means `item` refers to `items[0]`
    loop_mappings: AHashMap<String, (String, usize)>,
    /// Optional sidecar of raw Python objects keyed by top-level
    /// context name. Used only as a fallback when `get()` misses —
    /// the value-stack path remains the fast path for JSON-friendly
    /// context entries.
    ///
    /// Shared via `Arc` across clones because `Py<PyAny>` does not
    /// implement `Clone` directly (it requires a GIL-held `clone_ref`).
    /// Wrapping in `Arc` lets `Context::clone` stay GIL-free — the
    /// sidecar is logically immutable after construction.
    raw_py_objects: Option<std::sync::Arc<HashMap<String, PyObject>>>,
}

impl Clone for Context {
    fn clone(&self) -> Self {
        Self {
            stack: self.stack.clone(),
            safe_keys: self.safe_keys.clone(),
            loop_mappings: self.loop_mappings.clone(),
            // Arc::clone is cheap and does not require the GIL —
            // the contained `Py<PyAny>` refcount is not touched.
            raw_py_objects: self.raw_py_objects.clone(),
        }
    }
}

impl Context {
    pub fn new() -> Self {
        Self {
            stack: vec![AHashMap::new()],
            safe_keys: AHashSet::new(),
            loop_mappings: AHashMap::new(),
            raw_py_objects: None,
        }
    }

    pub fn from_dict(dict: HashMap<String, Value>) -> Self {
        let mut map = AHashMap::new();
        for (k, v) in dict {
            map.insert(k, v);
        }
        Self {
            stack: vec![map],
            safe_keys: AHashSet::new(),
            loop_mappings: AHashMap::new(),
            raw_py_objects: None,
        }
    }

    /// Attach a map of raw Python objects for `getattr`-fallback
    /// lookups. Typically called by the live-view layer after
    /// building the context from JSON-compatible state. Safe to
    /// call with an empty map (no-op on lookup).
    pub fn set_raw_py_objects(&mut self, objects: HashMap<String, PyObject>) {
        if objects.is_empty() {
            self.raw_py_objects = None;
        } else {
            self.raw_py_objects = Some(std::sync::Arc::new(objects));
        }
    }

    /// Does this context have any raw Python objects attached?
    pub fn has_raw_py_objects(&self) -> bool {
        self.raw_py_objects.is_some()
    }

    /// Borrow the raw Python objects sidecar, if attached.
    ///
    /// Used by the custom-tag bridge to pass Python-only context
    /// (e.g. ``request``, ``view``) to handlers that need them — like
    /// the Rust-path ``{% live_render %}`` handler which delegates to
    /// the Django template tag. Returns ``None`` when no sidecar is
    /// attached (the common case for templates rendered outside a
    /// ``RustLiveView``).
    pub fn raw_py_objects(&self) -> Option<&HashMap<String, PyObject>> {
        self.raw_py_objects.as_deref()
    }

    /// Mark a variable name as safe (skip auto-escaping on render).
    pub fn mark_safe(&mut self, key: String) {
        self.safe_keys.insert(key);
    }

    /// Check if a variable name is marked safe.
    pub fn is_safe(&self, key: &str) -> bool {
        // First check directly
        if self.safe_keys.contains(key) {
            return true;
        }

        // If not found, try resolving loop variables
        // e.g., "item.content" might map to "items.0.content" via loop_mappings
        let parts: Vec<&str> = key.split('.').collect();
        if let Some((iterable_name, index)) = self.loop_mappings.get(parts[0]) {
            // Build the resolved path: "items.0.content" from "item.content"
            let index_str = index.to_string();
            let mut resolved_parts = vec![iterable_name.as_str(), index_str.as_str()];
            resolved_parts.extend_from_slice(&parts[1..]);
            let resolved_key = resolved_parts.join(".");
            if self.safe_keys.contains(&resolved_key) {
                return true;
            }
        }

        false
    }

    pub fn get(&self, key: &str) -> Option<&Value> {
        // Handle nested lookups like "user.name"
        let parts: Vec<&str> = key.split('.').collect();

        if parts.len() == 1 {
            // Simple lookup
            for frame in self.stack.iter().rev() {
                if let Some(value) = frame.get(key) {
                    return Some(value);
                }
            }
            None
        } else {
            // Nested lookup
            let first = parts[0];
            let mut current = None;

            for frame in self.stack.iter().rev() {
                if let Some(value) = frame.get(first) {
                    current = Some(value);
                    break;
                }
            }

            let mut current = current?;

            for part in &parts[1..] {
                // Check if this part is a numeric index (for list access)
                if let Ok(index) = part.parse::<usize>() {
                    // Try to access as list index
                    match current {
                        Value::List(list) => {
                            current = list.get(index)?;
                        }
                        _ => return None,
                    }
                } else {
                    // Regular object field access
                    match current {
                        Value::Object(obj) => {
                            current = obj.get(*part)?;
                        }
                        _ => return None,
                    }
                }
            }

            Some(current)
        }
    }

    pub fn set(&mut self, key: String, value: Value) {
        if let Some(frame) = self.stack.last_mut() {
            frame.insert(key, value);
        }
    }

    pub fn push(&mut self) {
        self.stack.push(AHashMap::new());
    }

    pub fn pop(&mut self) {
        if self.stack.len() > 1 {
            self.stack.pop();
        }
    }

    /// Register a loop variable mapping.
    /// e.g., set_loop_mapping("item", "items", 0) means `item` refers to `items[0]`
    pub fn set_loop_mapping(&mut self, loop_var: String, iterable_name: String, index: usize) {
        self.loop_mappings.insert(loop_var, (iterable_name, index));
    }

    /// Clear a loop variable mapping (when exiting the loop scope)
    pub fn clear_loop_mapping(&mut self, loop_var: &str) {
        self.loop_mappings.remove(loop_var);
    }

    pub fn update(&mut self, dict: HashMap<String, Value>) {
        if let Some(frame) = self.stack.last_mut() {
            for (k, v) in dict {
                frame.insert(k, v);
            }
        }
    }

    /// Resolve a dotted lookup, falling back to `getattr` on raw
    /// Python objects when the normal value-stack path misses.
    ///
    /// This is the public user-facing lookup used by the template
    /// renderer for `{{ variable.path }}` expressions. Unlike
    /// [`Context::get`], the return type is owned `Value` (not
    /// `&Value`) because the `getattr` fallback constructs fresh
    /// values from Python attributes.
    ///
    /// Fallback semantics:
    /// - Single-segment keys with a hit in `raw_py_objects` convert
    ///   the object to `Value` (via `Value::extract`).
    /// - Nested keys walk `getattr` one segment at a time.
    ///   Intermediate attributes that themselves are Python objects
    ///   continue the walk; intermediate `dict`/`list` return values
    ///   are honoured as if they were regular `Value`s.
    /// - Any exception raised by `getattr` (AttributeError, property
    ///   raise, etc.) is caught and returned as `None`. This mirrors
    ///   Django's documented "template string if invalid" behaviour
    ///   (defaults to "") — a malformed template never crashes the
    ///   render.
    pub fn resolve(&self, key: &str) -> Option<Value> {
        if let Some(v) = self.get(key) {
            return Some(v.clone());
        }
        let raw = self.raw_py_objects.as_deref()?;
        let parts: Vec<&str> = key.split('.').collect();
        let first = *parts.first()?;
        let obj = raw.get(first)?;

        Python::with_gil(|py| -> Option<Value> {
            let mut current: pyo3::Bound<'_, pyo3::PyAny> = obj.bind(py).clone();
            for part in &parts[1..] {
                match current.getattr(*part) {
                    Ok(next) => {
                        current = next;
                    }
                    Err(_) => {
                        // Swallow AttributeError (and anything else
                        // raised by custom descriptors) — invalid
                        // template paths render as empty, matching
                        // Django's default.
                        return None;
                    }
                }
            }
            // Convert the resolved attribute to Value; failure → None
            current.extract::<Value>().ok()
        })
    }

    /// Convert the entire context to a flattened HashMap.
    ///
    /// This merges all stack frames (with later frames taking precedence)
    /// into a single HashMap. Used for passing context to Python callbacks.
    pub fn to_hashmap(&self) -> HashMap<String, Value> {
        let mut result = HashMap::new();
        // Iterate from bottom to top so later frames override earlier ones
        for frame in &self.stack {
            for (key, value) in frame {
                result.insert(key.clone(), value.clone());
            }
        }
        result
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_context_simple_get() {
        let mut ctx = Context::new();
        ctx.set("name".to_string(), Value::String("Django".to_string()));

        assert!(matches!(ctx.get("name"), Some(Value::String(s)) if s == "Django"));
        assert!(ctx.get("missing").is_none());
    }

    #[test]
    fn test_context_nested_get() {
        let mut ctx = Context::new();
        let mut user = HashMap::new();
        user.insert("name".to_string(), Value::String("John".to_string()));
        user.insert("age".to_string(), Value::Integer(30));

        ctx.set("user".to_string(), Value::Object(user));

        assert!(matches!(ctx.get("user.name"), Some(Value::String(s)) if s == "John"));
        assert!(matches!(ctx.get("user.age"), Some(Value::Integer(30))));
        assert!(ctx.get("user.missing").is_none());
    }

    #[test]
    fn test_context_stack() {
        let mut ctx = Context::new();
        ctx.set("a".to_string(), Value::Integer(1));

        ctx.push();
        ctx.set("a".to_string(), Value::Integer(2));
        assert!(matches!(ctx.get("a"), Some(Value::Integer(2))));

        ctx.pop();
        assert!(matches!(ctx.get("a"), Some(Value::Integer(1))));
    }
}
