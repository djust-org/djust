//! Template context management

use crate::Value;
use ahash::AHashMap;
use std::collections::HashMap;

/// A context for template rendering, similar to Django's Context
#[derive(Debug, Clone, Default)]
pub struct Context {
    stack: Vec<AHashMap<String, Value>>,
}

impl Context {
    pub fn new() -> Self {
        Self {
            stack: vec![AHashMap::new()],
        }
    }

    pub fn from_dict(dict: HashMap<String, Value>) -> Self {
        let mut map = AHashMap::new();
        for (k, v) in dict {
            map.insert(k, v);
        }
        Self { stack: vec![map] }
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

    pub fn update(&mut self, dict: HashMap<String, Value>) {
        if let Some(frame) = self.stack.last_mut() {
            for (k, v) in dict {
                frame.insert(k, v);
            }
        }
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
