//! Template context management

use crate::Value;
use ahash::{AHashMap, AHashSet};
use pyo3::prelude::*;
use std::collections::{HashMap, HashSet};
use std::sync::{Mutex, OnceLock};

/// Django's three template builtins, or `None` for any other name (#2347).
///
/// `django.template.context.builtins` is
/// `[{"True": True, "False": False, "None": None}]`, added to EVERY Django
/// `Context` at `dicts[0]`. So these three names RESOLVE — they are not
/// literals, and `Variable.__init__` does not special-case them. `{{ True }}`
/// renders `True`, `{{ p|add:True }}` hands the filter the real `True`, and a
/// custom filter receives a Python `bool`.
///
/// **The single statement of the rule.** Two resolvers can reach a bare name
/// (#1646): [`Context::resolve`], which serves `{{ }}` output, built-in filter
/// arguments and the custom-filter argument channel; and
/// `renderer::get_value_safe`, which serves `{% if %}` / `{% for %}` /
/// `{% with %}` / `{% firstof %}` / `{% cycle %}` and the filtered tag-operand
/// channel. The second already answered these three from inline arms while the
/// first did not, which is the drift #2347 is. Both call this now.
///
/// Case-sensitive, because Django is: `true` / `none` are ordinary undefined
/// variables to Django and render empty. `get_value_safe` additionally accepts
/// the lowercase spellings as a djust extension; that behaviour is unchanged
/// and deliberately does NOT live here, so this function stays exactly the
/// Django set.
///
/// `None` maps to [`Value::None`] and not [`Value::Missing`]: the two are
/// distinct (#2203) — `Missing` denotes an ABSENT variable, `None` the Python
/// singleton — and this name denotes the singleton.
pub fn template_builtin(name: &str) -> Option<Value> {
    match name {
        "True" => Some(Value::Bool(true)),
        "False" => Some(Value::Bool(false)),
        "None" => Some(Value::None),
        _ => None,
    }
}

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

    /// Accepts any map of pairs — `HashMap` or the `IndexMap` that now backs
    /// `Value::Object` (#2203). Frames are `AHashMap`, so ordering is not
    /// meaningful here; this is generic only to avoid forcing callers to
    /// convert.
    pub fn from_dict<M: IntoIterator<Item = (String, Value)>>(dict: M) -> Self {
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

    /// Is the value at `key` a SEQUENCE whose every ELEMENT was marked safe,
    /// while the sequence itself was not? (#2287)
    ///
    /// Django's second safety granularity, arriving from the CONTEXT rather
    /// than from a filter. A view that returns
    /// `{"p": [mark_safe("<b>x</b>"), mark_safe("<i>y</i>")]}` has marked the
    /// ITEMS — `mark_safe` is never called on the list, so the list is not
    /// `SafeData` and [`Context::is_safe`] correctly answers `false` for `p`.
    /// `join` and `unordered_list` `conditional_escape` per element, so in
    /// Django those items come through LIVE; without this method djust
    /// escaped them, which is the whole of #2287.
    ///
    /// `_collect_safe_keys` (`python/djust/mixins/rust_bridge.py`) already
    /// walks containers and emits one dotted path per `SafeString` it finds,
    /// so `p.0` / `p.1` are ALREADY in `safe_keys` — the channel existed and
    /// nothing read it at this granularity.
    ///
    /// Four deliberate narrowings, each of which is the ESCAPING direction and
    /// three of which would be an under-escape if dropped:
    ///
    /// * **`List` / `Tuple` only.** A `Value::Object` (a Python `dict`) records
    ///   its safe paths by NAME (`p.<k>`) while `filters::iter_values` yields
    ///   its KEYS — so a by-index check can never confuse the two, and a dict
    ///   whose VALUES are safe never grants safety to its (unmarked) keys.
    ///   A `String` is excluded for the same reason: `iter_values` yields its
    ///   CHARACTERS, and no `mark_safe` can mark a character.
    /// * **EVERY index present.** Django escapes per element, so a list whose
    ///   items are only PARTIALLY marked has a per-item answer that one bool
    ///   cannot express. Requiring all of them means a mixed list is escaped
    ///   whole — over-escaping, never under.
    /// * **Each element is a `String`.** `_collect_safe_keys` only ever emits a
    ///   path for a `SafeString`, so this is implied for a FRESH sync — but
    ///   `mark_safe_keys` only ever EXTENDS the set (there is no clear), so a
    ///   later render that puts a different shape at the same index must not
    ///   inherit the old grant. See the note on staleness below.
    /// * **Non-empty.** A zero-element grant is unobservable (there is no item
    ///   to escape) and asserting it would be a claim no test can falsify.
    ///
    /// Nested containers are refused by the `String` narrowing, and that is
    /// load-bearing rather than incidental: `join` stringifies a sublist and
    /// Django escapes that `repr`, so granting the whole sequence on
    /// `[mark_safe("a"), [mark_safe("b")]]` would emit raw `<` where Django
    /// emits `&lt;` — MORE permissive than Django, which this fix must never
    /// be.
    ///
    /// Staleness: `RustLiveView::mark_safe_keys` accumulates and is never
    /// cleared, so a key marked safe in one render stays marked in the next.
    /// That is a PRE-EXISTING defect at the container granularity — today
    /// `{{ p }}` already emits a later hostile value raw once `p` has been
    /// `mark_safe`d once — and this method rides the same set rather than
    /// creating a second one. Tracked at #2300; a fix there fixes both
    /// granularities, because both read this one set.
    pub fn items_are_safe(&self, key: &str) -> bool {
        let items = match self.get(key) {
            Some(Value::List(items)) | Some(Value::Tuple(items)) => items,
            _ => return false,
        };
        if items.is_empty() {
            return false;
        }

        // The prefixes this value's items could have been recorded under: the
        // key itself, plus the loop-variable alias `is_safe` resolves — inside
        // `{% for row in rows %}`, `row`'s items live at `rows.<i>.<j>`.
        let mut prefixes: Vec<String> = vec![key.to_string()];
        let parts: Vec<&str> = key.split('.').collect();
        if let Some((iterable_name, index)) = self.loop_mappings.get(parts[0]) {
            let index_str = index.to_string();
            let mut resolved_parts = vec![iterable_name.as_str(), index_str.as_str()];
            resolved_parts.extend_from_slice(&parts[1..]);
            prefixes.push(resolved_parts.join("."));
        }

        prefixes.iter().any(|prefix| {
            items.iter().enumerate().all(|(i, item)| {
                matches!(item, Value::String(_))
                    && self.safe_keys.contains(&format!("{prefix}.{i}"))
            })
        })
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
                        Value::List(list) | Value::Tuple(list) => {
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

    /// Python's `dict.items()` / `.keys()` / `.values()`, reached as the LAST
    /// segment of a dotted path over a [`Value::Object`] (#2334).
    ///
    /// These are METHODS, not keys, so [`Context::get`]'s nested walk — which
    /// only ever does `obj.get(part)` — misses them and `{% for k, v in
    /// d.items %}` renders nothing. Django reaches them through
    /// `Variable._resolve_lookup`'s attribute step plus its auto-call, which
    /// is why they work there.
    ///
    /// Returns an owned `Value`, which is why this cannot live inside
    /// `Context::get` (that returns a borrow into the value stack, and these
    /// sequences are constructed on demand).
    ///
    /// **The container is a `Value::List`, not a live dict view.** Python's
    /// `dict_items` differs from `list(d.items())` in exactly two observable
    /// ways: `str()` reads `dict_items([…])` rather than `[…]`, and it is not
    /// subscriptable or JSON-serializable (so Django RAISES on
    /// `{{ d.items|first }}` / `|last` / `|json_script`). Both residues are
    /// measured and pinned by
    /// `python/tests/test_dict_iteration_and_sequence_equality_2334_2335.py`
    /// and tracked at #2340; modelling them faithfully would mean a new
    /// `Value` variant threaded
    /// through every `Value::List | Value::Tuple` or-pattern in the workspace,
    /// which buys a debug-only repr at the cost of a wide edit in escaping
    /// machinery. Neither residue is more permissive than Django.
    ///
    /// Order is the `IndexMap`'s insertion order — Python's dict order. A
    /// `HashMap` here would make `{% for k in d %}` nondeterministic across
    /// renders and thrash the VDOM.
    fn dict_view(&self, key: &str) -> Option<Value> {
        let (prefix, last) = key.rsplit_once('.')?;
        if !matches!(last, "items" | "keys" | "values") {
            return None;
        }
        let Value::Object(map) = self.get(prefix)? else {
            return None;
        };
        Some(Value::List(match last {
            "keys" => map.keys().cloned().map(Value::from).collect(),
            "values" => map.values().cloned().collect(),
            // `items` — each entry a 2-`Tuple`, which is what makes
            // `{% for k, v in d.items %}` unpack through the renderer's
            // existing tuple-unpacking branch, and what makes `{{ x }}` over
            // one render `('a', 1)` as Python does.
            _ => map
                .iter()
                .map(|(k, v)| Value::Tuple(vec![Value::from(k.clone()), v.clone()]))
                .collect(),
        }))
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
        // Django's THREE template builtins, tried LAST (#2347).
        //
        // `django.template.context.builtins` is `[{"True": True, "False":
        // False, "None": None}]` and lives at `Context.dicts[0]`, so every
        // Django template carries them and `{{ True }}` renders `True` rather
        // than the empty string an unresolvable name renders. They are not
        // literals — `Variable.__init__` does not special-case them — they
        // RESOLVE, through the ordinary context lookup.
        //
        // Position is the whole of the semantics. `Context.__getitem__` walks
        // `reversed(self.dicts)`, so `dicts[0]` is the LAST place looked and a
        // user variable named `True` SHADOWS the builtin. Measured against
        // Django 5.2.16, not assumed. Hence: after `get`, after the dict-view
        // methods, and after the raw-Python sidecar walk — the fallback runs
        // only where this function used to answer `None`, which is what bounds
        // the change to exactly the cells that rendered empty.
        //
        // This is the deeper of the TWO resolvers a bare name can reach
        // (#1646): `renderer::get_value_safe` has its own literal arms for
        // `{% if %}` / `{% with %}` / `{% firstof %}` / `{% cycle %}`, and
        // those already answered these three. Both now go through
        // `template_builtin` so there is one statement of the rule; the
        // renderer's arms additionally accept the LOWERCASE spellings, which
        // are a djust extension Django does not have and are deliberately kept
        // separate from this function (see the note there).
        match self.resolve_without_builtins(key)? {
            Some(value) => Ok(Some(value)),
            None => Ok(template_builtin(key)),
        }
    }

    /// [`Context::resolve`] without the Django template-builtin fallback.
    ///
    /// Split out so the fallback is applied at ONE place rather than at each
    /// of the several `Ok(None)` returns below — a per-branch fallback is the
    /// shape that leaves one branch behind.
    fn resolve_without_builtins(&self, key: &str) -> crate::Result<Option<Value>> {
        if let Some(v) = self.get(key) {
            return Ok(Some(v.clone()));
        }
        // `d.items` / `d.keys` / `d.values` on a plain dict (#2334). Placed
        // AFTER `get` because Django's `Variable._resolve_lookup` tries
        // mapping-item access FIRST and attribute access second, so a dict
        // that HAS a key named `items` resolves to that key's value and never
        // reaches the method — which is what the `get` above already does.
        //
        // Placed BEFORE the `raw_py_objects` guard because the common case has
        // no sidecar at all: a dict in the JSON state is a `Value::Object` in
        // the value stack, and the sidecar walk (which reaches these methods
        // for a raw Python dict through `getattr` + `maybe_call`) never runs
        // for it. One chokepoint here serves every operand site — `{{ }}`
        // resolves through this function directly, and `{% for %}` /
        // `{% if %}` / `{% with %}` / `{% include … with %}` reach it as
        // `get_value_safe`'s last arm (#1646).
        if let Some(view) = self.dict_view(key) {
            return Ok(Some(view));
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
                CallOutcome::Empty => return Ok(Some(Value::Missing)),
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
                    CallOutcome::Empty => return Ok(Some(Value::Missing)),
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
    use indexmap::IndexMap;

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
        let mut user = IndexMap::new();
        user.insert("name".into(), Value::String("John".to_string()));
        user.insert("age".into(), Value::Integer(30));

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

    // -- `items_are_safe` (#2287) ------------------------------------------
    //
    // Unit-level pins for each narrowing in the doc comment. The Django-parity
    // half lives in `python/tests/test_context_item_safety_2287.py`; these
    // exist because three of the narrowings are only OBSERVABLE as a bool here
    // — from the Python side, "grant refused" and "grant given but the filter
    // escaped anyway" produce the same bytes for some shapes.

    fn strs(values: &[&str]) -> Vec<Value> {
        values
            .iter()
            .map(|s| Value::String(s.to_string()))
            .collect()
    }

    /// A list with `p.0`/`p.1` marked — the shape `_collect_safe_keys` emits
    /// for `[mark_safe(a), mark_safe(b)]`.
    fn ctx_with_marked_list() -> Context {
        let mut ctx = Context::new();
        ctx.set(
            "p".to_string(),
            Value::List(strs(&["<b>x</b>", "<i>y</i>"])),
        );
        ctx.mark_safe("p.0".to_string());
        ctx.mark_safe("p.1".to_string());
        ctx
    }

    #[test]
    fn items_are_safe_when_every_index_is_marked() {
        assert!(ctx_with_marked_list().items_are_safe("p"));
        // …and the CONTAINER is not, which is the whole distinction: Django's
        // `mark_safe` was never called on the list.
        assert!(!ctx_with_marked_list().is_safe("p"));
    }

    #[test]
    fn a_tuple_is_the_same_shape_as_a_list() {
        let mut ctx = Context::new();
        ctx.set("p".to_string(), Value::Tuple(strs(&["a", "b"])));
        ctx.mark_safe("p.0".to_string());
        ctx.mark_safe("p.1".to_string());
        assert!(ctx.items_are_safe("p"));
    }

    #[test]
    fn a_partially_marked_list_is_refused() {
        let mut ctx = ctx_with_marked_list();
        ctx.set("p".to_string(), Value::List(strs(&["a", "b", "c"])));
        // `p.2` is unmarked — Django would escape only that element, and one
        // bool cannot say so, so the whole grant is withheld.
        assert!(!ctx.items_are_safe("p"));
    }

    #[test]
    fn a_dict_is_refused_even_when_its_values_are_marked() {
        // `_collect_safe_keys` writes a dict's paths by NAME while the filters
        // iterate its KEYS. Marking `p.0` here is the adversarial case: a dict
        // that happens to have a key spelled "0".
        let mut ctx = Context::new();
        let mut map = IndexMap::new();
        map.insert("0".into(), Value::String("<b>v</b>".to_string()));
        ctx.set("p".to_string(), Value::Object(map));
        ctx.mark_safe("p.0".to_string());
        assert!(!ctx.items_are_safe("p"));
    }

    #[test]
    fn a_marked_string_grants_nothing_to_its_characters() {
        let mut ctx = Context::new();
        ctx.set("p".to_string(), Value::String("<b>x</b>".to_string()));
        ctx.mark_safe("p".to_string());
        ctx.mark_safe("p.0".to_string());
        assert!(ctx.is_safe("p"));
        assert!(!ctx.items_are_safe("p"));
    }

    #[test]
    fn an_empty_list_is_refused() {
        let mut ctx = Context::new();
        ctx.set("p".to_string(), Value::List(vec![]));
        assert!(!ctx.items_are_safe("p"));
    }

    #[test]
    fn a_nested_container_is_refused_even_when_every_leaf_is_marked() {
        // Granting this would out-permit Django: `join` stringifies the
        // sublist and Django escapes that repr.
        let mut ctx = Context::new();
        ctx.set(
            "p".to_string(),
            Value::List(vec![
                Value::String("<b>a</b>".to_string()),
                Value::List(strs(&["<i>b</i>"])),
            ]),
        );
        ctx.mark_safe("p.0".to_string());
        ctx.mark_safe("p.1".to_string());
        ctx.mark_safe("p.1.0".to_string());
        assert!(!ctx.items_are_safe("p"));
    }

    #[test]
    fn a_stale_mark_cannot_reach_a_non_string_item() {
        // `mark_safe_keys` only extends, so a path marked for a previous
        // render survives into this one. The element-is-a-String narrowing is
        // what stops it granting safety to a shape never marked.
        let mut ctx = Context::new();
        ctx.set(
            "p".to_string(),
            Value::List(vec![Value::List(strs(&["<script>x</script>"]))]),
        );
        ctx.mark_safe("p.0".to_string());
        assert!(!ctx.items_are_safe("p"));
    }

    #[test]
    fn a_missing_key_is_refused() {
        assert!(!Context::new().items_are_safe("nope"));
    }

    #[test]
    fn a_loop_variable_resolves_through_its_iterables_path() {
        // Inside `{% for row in rows %}` the variable is `row`, but
        // `_collect_safe_keys` recorded the marks at `rows.0.<i>`.
        let mut ctx = Context::new();
        let row = Value::List(strs(&["<b>x</b>", "<i>y</i>"]));
        ctx.set("rows".to_string(), Value::List(vec![row.clone()]));
        ctx.mark_safe("rows.0.0".to_string());
        ctx.mark_safe("rows.0.1".to_string());

        ctx.push();
        ctx.set("row".to_string(), row);
        ctx.set_loop_mapping("row".to_string(), "rows".to_string(), 0);
        assert!(ctx.items_are_safe("row"));
    }

    #[test]
    fn a_loop_variable_pointing_at_an_unmarked_row_is_refused() {
        let mut ctx = Context::new();
        let marked = Value::List(strs(&["<b>x</b>"]));
        let unmarked = Value::List(strs(&["<img src=x onerror=alert(1)>"]));
        ctx.set(
            "rows".to_string(),
            Value::List(vec![marked, unmarked.clone()]),
        );
        ctx.mark_safe("rows.0.0".to_string());

        ctx.push();
        ctx.set("row".to_string(), unmarked);
        ctx.set_loop_mapping("row".to_string(), "rows".to_string(), 1);
        assert!(!ctx.items_are_safe("row"));
    }

    // -- #2334: dict views --------------------------------------------

    fn dict_ctx() -> Context {
        let mut map = indexmap::IndexMap::new();
        map.insert("a".into(), Value::Integer(1));
        map.insert("b".into(), Value::Integer(2));
        let mut ctx = Context::new();
        ctx.set("d".to_string(), Value::Object(map));
        ctx
    }

    #[test]
    fn dict_items_keys_and_values_resolve_in_insertion_order() {
        let ctx = dict_ctx();
        match ctx.resolve("d.keys").unwrap().unwrap() {
            Value::List(items) => assert_eq!(
                items.iter().map(|v| v.to_string()).collect::<Vec<_>>(),
                vec!["a", "b"],
                "Python's dict order, not a hash order — a HashMap here would \
                 make `{{% for k in d %}}` nondeterministic across renders"
            ),
            other => panic!("expected a list, got {other:?}"),
        }
        match ctx.resolve("d.values").unwrap().unwrap() {
            Value::List(items) => {
                assert_eq!(
                    items.iter().map(|v| v.to_string()).collect::<Vec<_>>(),
                    vec!["1", "2"]
                )
            }
            other => panic!("expected a list, got {other:?}"),
        }
        match ctx.resolve("d.items").unwrap().unwrap() {
            Value::List(items) => {
                assert_eq!(items.len(), 2);
                // Each entry is a 2-TUPLE, which is what makes
                // `{% for k, v in d.items %}` unpack through the renderer's
                // existing tuple-unpacking branch.
                match &items[0] {
                    Value::Tuple(pair) => {
                        assert_eq!(pair.len(), 2);
                        assert_eq!(pair[0].to_string(), "a");
                        assert_eq!(pair[1].to_string(), "1");
                    }
                    other => panic!("expected a 2-tuple, got {other:?}"),
                }
            }
            other => panic!("expected a list, got {other:?}"),
        }
    }

    #[test]
    fn a_real_key_named_items_shadows_the_method() {
        // Django's `Variable._resolve_lookup` tries mapping-item access FIRST
        // and attribute access second, which is why the view resolution is
        // placed AFTER `Context::get` rather than inside its walk.
        let mut map = indexmap::IndexMap::new();
        map.insert("items".into(), Value::Integer(5));
        let mut ctx = Context::new();
        ctx.set("d".to_string(), Value::Object(map));
        assert_eq!(ctx.resolve("d.items").unwrap().unwrap().to_string(), "5");
    }

    #[test]
    fn a_dict_view_is_not_offered_for_a_non_object_or_a_deeper_path() {
        let mut ctx = dict_ctx();
        ctx.set("s".to_string(), Value::String("x".to_string()));
        ctx.set("l".to_string(), Value::List(vec![Value::Integer(1)]));
        // Only a mapping has these methods.
        assert!(ctx.resolve("s.items").unwrap().is_none());
        assert!(ctx.resolve("l.keys").unwrap().is_none());
        // A single-segment name is never a view.
        assert!(ctx.resolve("items").unwrap().is_none());
        // Python's `dict_items` has no `.0` and no `.keys`, and neither does
        // this: the walk resolves the PREFIX through `get`, which misses.
        assert!(ctx.resolve("d.items.0").unwrap().is_none());
        assert!(ctx.resolve("d.items.keys").unwrap().is_none());
    }

    #[test]
    fn a_nested_dict_view_resolves_through_the_prefix_walk() {
        let mut inner = indexmap::IndexMap::new();
        inner.insert("x".into(), Value::Integer(9));
        let mut outer = indexmap::IndexMap::new();
        outer.insert("inner".into(), Value::Object(inner));
        let mut ctx = Context::new();
        ctx.set("d".to_string(), Value::Object(outer));
        match ctx.resolve("d.inner.keys").unwrap().unwrap() {
            Value::List(items) => {
                assert_eq!(
                    items.iter().map(|v| v.to_string()).collect::<Vec<_>>(),
                    vec!["x"]
                )
            }
            other => panic!("expected a list, got {other:?}"),
        }
    }
}
