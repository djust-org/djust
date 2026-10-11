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
    raw_py_objects: Option<std::sync::Arc<HashMap<String, Py<PyAny>>>>,
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
    pub fn set_raw_py_objects(&mut self, objects: HashMap<String, Py<PyAny>>) {
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
    pub fn raw_py_objects(&self) -> Option<&HashMap<String, Py<PyAny>>> {
        self.raw_py_objects.as_deref()
    }

    /// Mark a variable name as safe (skip auto-escaping on render).
    pub fn mark_safe(&mut self, key: String) {
        self.safe_keys.insert(key);
    }

    /// Bind `name` to `value`, **REPLACING** whatever safety grant `name`
    /// carried.
    ///
    /// This is the one door for every template construct that binds a
    /// resolved value to a NEW NAME — `{% with %}`, `{% include … with %}`
    /// and the `{% … as x %}` assign-tag merge. [`Context::set`] moves the
    /// VALUE; this moves the value AND revokes the grant that described the
    /// value being SHADOWED.
    ///
    /// # Why the grant must be REPLACED, not merely carried
    ///
    /// djust's safety channel is keyed BY NAME: `safe_keys` holds dotted
    /// paths written by `rust_bridge._collect_safe_keys`, and
    /// [`Context::is_safe`] answers by looking a name up in it. A bind copies
    /// the value, so without this the grant stays attached to a name that now
    /// holds a DIFFERENT — possibly attacker-controlled — value:
    ///
    /// ```text
    /// safe_keys = ["p"],  p = mark_safe("<b>trusted</b>")
    /// {% with p=hostile %}{{ p }}{% endwith %}   ->  hostile emitted RAW
    /// ```
    ///
    /// That is an UNDER-escape — djust MORE permissive than Django, the one
    /// direction this machinery must never move in.
    ///
    /// # The paths BENEATH the name go too
    ///
    /// `safe_keys` holds `p.a` as readily as `p`, and those descendants
    /// described the value being SHADOWED. Leaving them makes
    /// `{% with p=hostile_dict %}{{ p.a }}{% endwith %}` emit raw. So a bind
    /// revokes `name` and every `name.…` beneath it.
    ///
    /// # Why no `safe` argument on this branch
    ///
    /// None of the 1.1.x bind sinks has a runtime-safe channel to carry: the
    /// `{% with %}` / `{% include … with %}` arms resolve their operand with a
    /// bare `Context::get` (no filter pipeline, so no `filter_output_is_safe`
    /// bool exists to thread), and an assign tag's handler returns plain
    /// `Value`s across the PyO3 boundary with no safety information at all.
    /// The honest replacement grant is therefore always "none", and taking a
    /// `bool` that every call site passes `false` would be dead config. When a
    /// sink gains a genuine runtime-safe bool, widen this signature then — the
    /// revoke half is what closes the leak and is unaffected either way.
    ///
    /// The `{% for %}` loop variable is the fourth bind sink and hoists the
    /// [`Context::revoke_safe_subtree`] half OUT of its iteration rather than
    /// calling this per item — see that method's docs.
    pub fn bind(&mut self, name: String, value: Value) {
        self.revoke_safe_subtree(&name);
        self.set(name, value);
    }

    /// The SUBTREE half of a [`Context::bind`]: drop the grant on `key` and on
    /// every dotted path beneath it. `O(len(safe_keys))`.
    ///
    /// The descendants go because they described the value being SHADOWED.
    /// With `p.a` marked, leaving them makes
    /// `{% with p=hostile_dict %}{{ p.a }}{% endwith %}` emit raw.
    ///
    /// A `{% for %}` binds the same names once per iteration, so it calls this
    /// ONCE before the loop instead of `bind` per item: the shadowed outer
    /// grants every iteration would clear are the same ones, so clearing them
    /// once is identical in effect and turns an `O(N·len(safe_keys))` scan
    /// into one. `context::tests::the_loop_decomposition_of_bind_agrees_with_bind`
    /// pins that the two spellings agree so the split cannot drift.
    ///
    /// The scan is skipped entirely when the set is empty — the common case
    /// for a render with no context marks at all.
    ///
    /// This deliberately does NOT touch [`Context::set_loop_mapping`]'s
    /// aliases: that mapping is how a real list's per-item marks resolve
    /// (`{% for x in p %}` → `p.<index>`), which is a grant the bound value
    /// genuinely carries rather than a stale one it inherited.
    pub fn revoke_safe_subtree(&mut self, key: &str) {
        if self.safe_keys.is_empty() {
            return;
        }
        self.safe_keys.remove(key);
        let prefix = format!("{key}.");
        self.safe_keys.retain(|k| !k.starts_with(&prefix));
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

        Python::attach(|py| -> Option<Value> {
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

    // ---- `Context::bind` — a binding REPLACES the grant ----

    /// The three-key fixture every bind test below shadows one name of.
    fn ctx_with_a_marked_name() -> Context {
        let mut ctx = Context::new();
        ctx.set("p".to_string(), Value::String("<b>x</b>".into()));
        ctx.mark_safe("p".to_string());
        ctx.mark_safe("p.a".to_string());
        ctx.mark_safe("q".to_string());
        ctx
    }

    #[test]
    fn bind_revokes_a_stale_grant_on_the_shadowed_name() {
        let mut ctx = ctx_with_a_marked_name();
        ctx.bind("p".to_string(), Value::String("<img>".into()));
        assert!(!ctx.is_safe("p"), "the shadowed name kept its grant");
    }

    #[test]
    fn bind_revokes_the_grants_beneath_the_shadowed_name() {
        let mut ctx = ctx_with_a_marked_name();
        ctx.bind("p".to_string(), Value::String("<img>".into()));
        assert!(
            !ctx.is_safe("p.a"),
            "a descendant of the shadowed name survived"
        );
    }

    #[test]
    fn bind_leaves_every_other_name_alone() {
        let mut ctx = ctx_with_a_marked_name();
        ctx.bind("p".to_string(), Value::String("<img>".into()));
        assert!(ctx.is_safe("q"), "bind revoked an unrelated name");
    }

    #[test]
    fn bind_still_moves_the_value() {
        let mut ctx = ctx_with_a_marked_name();
        ctx.bind("p".to_string(), Value::String("<img>".into()));
        assert!(matches!(ctx.get("p"), Some(Value::String(s)) if s == "<img>"));
    }

    /// The `{% for %}` arm hoists `revoke_safe_subtree` out of its iteration
    /// and calls plain `set` per item. That decomposition is a COST decision,
    /// so it must be observationally identical to calling `bind` each time, or
    /// the split has drifted.
    #[test]
    fn the_loop_decomposition_of_bind_agrees_with_bind() {
        let items = [
            Value::String("<b>0</b>".into()),
            Value::String("<i>1</i>".into()),
            Value::String("<u>2</u>".into()),
        ];

        // Spelling A — `bind` per iteration.
        let mut a = ctx_with_a_marked_name();
        // Spelling B — one subtree revoke, then `set` per item.
        let mut b = ctx_with_a_marked_name();
        b.revoke_safe_subtree("p");

        for value in items.iter() {
            a.bind("p".to_string(), value.clone());
            b.set("p".to_string(), value.clone());

            assert_eq!(
                a.is_safe("p"),
                b.is_safe("p"),
                "bind and its loop decomposition disagree on `p` at {value:?}"
            );
            assert_eq!(a.is_safe("p.a"), b.is_safe("p.a"), "…and on `p.a`");
            assert_eq!(a.is_safe("q"), b.is_safe("q"), "…and on the untouched `q`");
        }
    }

    #[test]
    fn revoke_safe_subtree_does_not_touch_a_sibling_sharing_a_prefix() {
        // `pp` starts with `p` but is not beneath it — only `p.` is.
        let mut ctx = Context::new();
        ctx.mark_safe("p".to_string());
        ctx.mark_safe("pp".to_string());
        ctx.mark_safe("p.a".to_string());
        ctx.revoke_safe_subtree("p");
        assert!(!ctx.is_safe("p"));
        assert!(!ctx.is_safe("p.a"));
        assert!(ctx.is_safe("pp"), "a prefix-sharing SIBLING was revoked");
    }

    /// The revoke must not disturb the loop-mapping alias, which is how a real
    /// list's per-item marks reach the loop variable. That grant is one the
    /// bound value genuinely carries, not a stale one it inherited.
    #[test]
    fn revoke_safe_subtree_leaves_the_loop_mapping_channel_intact() {
        let mut ctx = Context::new();
        ctx.mark_safe("items.0".to_string());
        ctx.set_loop_mapping("x".to_string(), "items".to_string(), 0);
        ctx.revoke_safe_subtree("x");
        assert!(
            ctx.is_safe("x"),
            "revoking the loop variable's name dropped its per-item mark"
        );
    }
}
