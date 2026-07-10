//! Template context management

use crate::Value;
use ahash::{AHashMap, AHashSet};
use pyo3::prelude::*;
use std::collections::{HashMap, HashSet};
use std::sync::{Mutex, OnceLock};

/// A context for template rendering, similar to Django's Context
///
/// In addition to JSON-friendly `Value` entries, `Context` can hold a
/// sidecar map of raw Python objects (e.g. Django model instances) for
/// `getattr`-style fallback lookups when a nested key like
/// `user.username` cannot be resolved through the normal value stack.
#[derive(Debug)]
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
    /// Django-parity auto-call of callables during sidecar `getattr`
    /// resolution (ADR-024). Default `true`; the Python side wires
    /// `LIVEVIEW_CONFIG["template_auto_call"]` through
    /// `RustLiveView::set_template_auto_call` (mirroring the #1967
    /// `loop_render_cache_enabled` flag plumbing). `false` restores the
    /// pre-ADR plain-getattr walk (kill-switch).
    auto_call: bool,
}

impl Default for Context {
    fn default() -> Self {
        Self::new()
    }
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
            auto_call: self.auto_call,
        }
    }
}

/// Outcome of the Django-parity callable handling for one resolved
/// attribute (ADR-024, mirrors `Variable._resolve_lookup`).
enum CallOutcome<'py> {
    /// Not callable / `do_not_call_in_templates` / auto-call disabled —
    /// keep the object as-is and continue the walk.
    AsIs(pyo3::Bound<'py, pyo3::PyAny>),
    /// The callable was invoked; continue the walk with its result.
    Called(pyo3::Bound<'py, pyo3::PyAny>),
    /// `alters_data` refusal or an args-required callable — the whole
    /// expression resolves to empty (Django's `string_if_invalid`).
    Empty,
}

impl Context {
    pub fn new() -> Self {
        Self {
            stack: vec![AHashMap::new()],
            safe_keys: AHashSet::new(),
            loop_mappings: AHashMap::new(),
            raw_py_objects: None,
            auto_call: true,
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
            auto_call: true,
        }
    }

    /// Enable/disable Django-parity auto-call in the sidecar walk
    /// (ADR-024 kill-switch; wired from
    /// `LIVEVIEW_CONFIG["template_auto_call"]`).
    pub fn set_auto_call(&mut self, enabled: bool) {
        self.auto_call = enabled;
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
    ///   raise, etc.) is caught and resolved as `None`. This mirrors
    ///   Django's documented "template string if invalid" behaviour
    ///   (defaults to "") — a malformed template never crashes the
    ///   render.
    /// - **Auto-call (ADR-024, Django parity)**: after the root bind
    ///   and after every `getattr` segment, a callable is invoked with
    ///   no arguments — exactly `django.template.base.Variable._resolve_lookup`:
    ///   `do_not_call_in_templates` → use the object as-is;
    ///   `alters_data` → the expression resolves empty (never called);
    ///   a `TypeError` from the call runs the `inspect.signature(...).bind()`
    ///   probe — args-required (or unsignaturable) → empty, otherwise the
    ///   original `TypeError` propagates; any other exception raised by the
    ///   called method propagates as a render error.
    ///
    /// Errors: `Err` is returned only for exceptions raised *inside an
    /// auto-called method* (Django propagates those); lookup failures
    /// stay `Ok(None)` as before.
    pub fn resolve(&self, key: &str) -> crate::Result<Option<Value>> {
        if let Some(v) = self.get(key) {
            return Ok(Some(v.clone()));
        }
        let Some(raw) = self.raw_py_objects.as_deref() else {
            return Ok(None);
        };
        let parts: Vec<&str> = key.split('.').collect();
        let Some(first) = parts.first().copied() else {
            return Ok(None);
        };
        let Some(obj) = raw.get(first) else {
            return Ok(None);
        };

        Python::attach(|py| -> crate::Result<Option<Value>> {
            let mut current: pyo3::Bound<'_, pyo3::PyAny> = obj.bind(py).clone();
            // Django auto-calls the value at EVERY lookup step, including
            // the root bit ({{ some_callable }}) and mid-path
            // ({{ obj.get_settings.theme }}).
            current = match self.maybe_call(py, current, key)? {
                CallOutcome::AsIs(v) | CallOutcome::Called(v) => v,
                CallOutcome::Empty => return Ok(Some(Value::Null)),
            };
            current = self.protect_sidecar(py, current);
            for part in &parts[1..] {
                // Django `Variable._resolve_lookup` order at EVERY segment:
                // (1) mapping/dict item access, (2) attribute, (3) integer
                // list-index. The pre-#1997 walk did (2) only, so a dict/list
                // intermediate — e.g. a model's `JSONField` value reached mid-
                // path (`{{ block.content.text }}`) — resolved to empty because
                // `getattr(dict, "text")` raises `AttributeError`. Mirroring
                // Django's order fixes nested JSONField/dict/list access.
                // The #1986 proxies (`_SidecarModelProxy`/`_SidecarQuerySetProxy`)
                // implement no `__getitem__`, so item access on them falls
                // through to `getattr` and the serialization floor still governs
                // — this does not open a floor bypass.
                let next = current
                    .get_item(*part)
                    .or_else(|_| current.getattr(*part))
                    .or_else(|e| match part.parse::<usize>() {
                        Ok(idx) => current.get_item(idx),
                        Err(_) => Err(e),
                    });
                match next {
                    Ok(n) => {
                        current = n;
                    }
                    Err(_) => {
                        // Swallow the lookup failure — invalid template paths
                        // render as empty, matching Django's default
                        // (`string_if_invalid` = "").
                        return Ok(None);
                    }
                }
                current = match self.maybe_call(py, current, key)? {
                    CallOutcome::AsIs(v) | CallOutcome::Called(v) => v,
                    CallOutcome::Empty => return Ok(Some(Value::Null)),
                };
                current = self.protect_sidecar(py, current);
            }
            // Convert the resolved attribute to Value; failure → None
            Ok(current.extract::<Value>().ok())
        })
    }

    /// Route a just-materialized attribute/call result through the Python
    /// sidecar serialization floor (SECURE_DEFAULTS Pattern 1 / #1986).
    ///
    /// `djust.serialization._protect_sidecar_value` wraps a Django `Model`
    /// in `_SidecarModelProxy` and a `Manager`/`QuerySet` in
    /// `_SidecarQuerySetProxy` (both floor-enforcing); anything else is
    /// returned unchanged. Applying it at THIS point — the single spot where
    /// the walk holds a freshly-resolved value, after both `getattr` and the
    /// auto-call — is what makes the floor hold *however* a model was reached:
    /// a related-field getattr, an auto-called method that returns a model
    /// (`{{ obj.get_related.password }}`), or an attribute of a non-model
    /// intermediary object placed in the context (`{{ presenter.user.password }}`,
    /// #1986 vector 6). Python-side proxies alone can't cover those — a raw
    /// intermediary has no proxy `__getattr__`, and a Rust auto-call result
    /// never re-enters Python. One chokepoint here retires the class (#1646).
    ///
    /// Floor enforcement is INDEPENDENT of the `auto_call` kill-switch
    /// (`{{ p.user.password }}` leaks via pure getattr, no call), so this runs
    /// regardless of `self.auto_call`. It is idempotent (wrapping a proxy
    /// returns it unchanged) and fail-safe: any error returns the value
    /// unwrapped rather than crashing the render.
    fn protect_sidecar<'py>(
        &self,
        py: Python<'py>,
        obj: pyo3::Bound<'py, pyo3::PyAny>,
    ) -> pyo3::Bound<'py, pyo3::PyAny> {
        match py
            .import("djust.serialization")
            .and_then(|m| m.getattr("_protect_sidecar_value"))
            .and_then(|f| f.call1((obj.clone(),)))
        {
            Ok(wrapped) => wrapped,
            Err(_) => obj,
        }
    }

    /// Django-parity callable handling for one resolved attribute
    /// (ADR-024; mirrors `Variable._resolve_lookup`'s callable block).
    /// `path` is the full dotted expression, used only for the
    /// debug-mode ORM-call warning.
    fn maybe_call<'py>(
        &self,
        py: Python<'py>,
        obj: pyo3::Bound<'py, pyo3::PyAny>,
        path: &str,
    ) -> crate::Result<CallOutcome<'py>> {
        // Kill-switch OFF restores the pre-ADR plain-getattr walk: no
        // guard checks, no calls.
        if !self.auto_call || !obj.is_callable() {
            return Ok(CallOutcome::AsIs(obj));
        }
        // `do_not_call_in_templates` → use as-is (Model classes,
        // enums.Choices set this).
        if attr_is_truthy(&obj, "do_not_call_in_templates") {
            return Ok(CallOutcome::AsIs(obj));
        }
        // `alters_data` → refuse: never call, expression renders empty
        // (Django stamps Model.save/delete, QuerySet.delete/update, …).
        if attr_is_truthy(&obj, "alters_data") {
            return Ok(CallOutcome::Empty);
        }
        warn_once_on_orm_autocall(py, &obj, path);
        match obj.call0() {
            Ok(result) => Ok(CallOutcome::Called(result)),
            Err(err) if err.is_instance_of::<pyo3::exceptions::PyTypeError>(py) => {
                // Django's probe: TypeError from the call is "invalid"
                // (empty) when the callable actually REQUIRES arguments
                // (or has no introspectable signature); a TypeError
                // raised INSIDE a zero-arg method is a real bug and
                // propagates.
                if callable_requires_arguments(py, &obj) {
                    Ok(CallOutcome::Empty)
                } else {
                    Err(err.into())
                }
            }
            // Any other exception raised by the method propagates as a
            // render error, matching Django.
            Err(err) => Err(err.into()),
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

/// Truthiness of an optional attribute (`getattr(obj, name, False)` +
/// `bool(...)`). Missing attribute or a raising descriptor counts as
/// falsy — matching Django's `getattr(current, "...", False)` reads.
fn attr_is_truthy(obj: &pyo3::Bound<'_, pyo3::PyAny>, name: &str) -> bool {
    obj.getattr(name)
        .ok()
        .and_then(|v| v.is_truthy().ok())
        .unwrap_or(false)
}

/// Django's args-required probe, run only on the cold `TypeError` path:
/// `inspect.signature(obj).bind()` — bind raising `TypeError` means the
/// callable genuinely requires arguments (→ expression is "invalid",
/// renders empty); `inspect.signature` itself failing (unsignaturable
/// builtin) is treated the same. A successful zero-arg bind means the
/// `TypeError` came from INSIDE the method and must propagate.
fn callable_requires_arguments(py: Python<'_>, obj: &pyo3::Bound<'_, pyo3::PyAny>) -> bool {
    let probe = || -> PyResult<bool> {
        let inspect = py.import("inspect")?;
        let signature = inspect.call_method1("signature", (obj,))?;
        Ok(signature.call_method0("bind").is_err())
    };
    // No signature found (ValueError on some builtins) → Django's
    // `string_if_invalid` branch → treat as args-required (empty).
    probe().unwrap_or(true)
}

/// Debug-only, one-shot-per-dotted-path warning when an auto-called
/// callable is bound to a Django `Manager`/`QuerySet` (ADR-024
/// observability rider): in a LiveView the template re-renders on every
/// WebSocket event, so `{{ workspace.memberships.count }}` is a DB
/// query per event. Best-effort — never fails or blocks the render.
fn warn_once_on_orm_autocall(py: Python<'_>, obj: &pyo3::Bound<'_, pyo3::PyAny>, path: &str) {
    // One-shot per dotted path per process: the set-membership check runs
    // FIRST so already-warned paths cost a single HashSet lookup.
    static WARNED_PATHS: OnceLock<Mutex<HashSet<String>>> = OnceLock::new();
    let warned = WARNED_PATHS.get_or_init(|| Mutex::new(HashSet::new()));
    {
        let Ok(guard) = warned.lock() else { return };
        if guard.contains(path) {
            return; // already warned for this path
        }
    }
    // Only bound methods whose __self__ is a Manager/QuerySet.
    let Ok(receiver) = obj.getattr("__self__") else {
        return;
    };
    let is_orm = py
        .import("django.db.models")
        .and_then(|m| {
            let manager = m.getattr("Manager")?;
            let queryset = m.getattr("QuerySet")?;
            Ok(receiver.is_instance(&manager)? || receiver.is_instance(&queryset)?)
        })
        .unwrap_or(false);
    if !is_orm {
        return;
    }
    // Read settings.DEBUG live (deliberately not cached, so
    // `override_settings(DEBUG=...)` stays honest). Under DEBUG=False this
    // re-runs on every render of a not-yet-warned ORM path — one settings
    // getattr chain, trivial next to the ORM query the auto-call performs.
    let debug = py
        .import("django.conf")
        .and_then(|m| m.getattr("settings"))
        .and_then(|s| s.getattr("DEBUG"))
        .ok()
        .and_then(|d| d.is_truthy().ok())
        .unwrap_or(false);
    if !debug {
        return;
    }
    {
        let Ok(mut guard) = warned.lock() else { return };
        if !guard.insert(path.to_string()) {
            return; // raced with another render thread — already warned
        }
    }
    let _ = py.import("logging").and_then(|logging| {
        let logger = logging.call_method1("getLogger", ("djust.templates",))?;
        logger.call_method1(
            "warning",
            (
                "[djust] Template path '%s' auto-calls an ORM method — this runs on \
                 EVERY re-render (each WebSocket event). Consider precomputing it in \
                 get_context_data() if this view re-renders frequently. (ADR-024)",
                path,
            ),
        )?;
        Ok(())
    });
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
