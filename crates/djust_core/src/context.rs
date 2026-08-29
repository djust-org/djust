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

/// ONE path segment, resolved by Django's three steps in Django's order
/// (#2371).
///
/// `django.template.base.Variable._resolve_lookup` tries three things per
/// segment and takes the first that does not raise:
///
/// 1. **mapping item access, the segment as a STRING** — `current[bit]`;
/// 2. **attribute access** — `getattr(current, bit)`;
/// 3. **integer index** — `current[int(bit)]`.
///
/// The walk this replaced branched on the SPELLING of the segment instead: a
/// numeric segment got step 3 and only step 3, a non-numeric segment got step
/// 1 and only step 1. So each spelling was missing the other's half, and
/// `{{ d.0 }}` resolved nothing on a dict whatever its key's type —
/// `{'0': 4}` needs step 1, `{0: 4}` needs step 3, and neither ran. Silently:
/// no exception, no warning, an empty render. `{{ d.0|divisibleby:"2" }}` is
/// the sharpest of them, answering a definite **False** rather than nothing.
///
/// **The order is the semantics, and it is measured.** A dict carrying both
/// spellings — `{'0': 's', 0: 'i'}` — renders `'s'` in Django, because step 1
/// runs first. Reversing these two arms passes every single-key test and
/// fails that one.
///
/// **Step 2 has no counterpart here and needs none.** A [`Value`] is inert
/// data with no attributes; the attribute step belongs to the raw-Python
/// sidecar walk in [`Context::resolve`], which has done all three in this
/// order since #1997. That walk is this one's parallel path (CLAUDE.md
/// #1646) — one had Django's rule and its twin did not, which is the drift
/// this helper exists to end. State it once, call it from both spellings.
///
/// A [`Value::DictView`] is deliberately absent from step 3: Python's
/// `dict_items` is not subscriptable, Django's `current[int(bit)]` raises on
/// it, and `{{ d.items.0 }}` must stay empty on both engines.
///
/// **Not covered, and named rather than silent (#2373).** Django's step 3
/// subscripts a `str` too — `{{ s.0 }}` on `"abc"` is `'a'` there and empty
/// here. Closing it needs an OWNED return (the character is constructed, not
/// borrowed), which is a return-type change across every
/// [`Context::get`] caller rather than an arm in this `match`. Scoped out of
/// #2371 deliberately; pinned in
/// `python/tests/test_numeric_path_segment_2371.py::
/// TestTheStringIndexStepIsNamedNotFixed` so it cannot be mistaken for
/// something this change was supposed to have covered.
fn lookup_segment<'a>(current: &'a Value, part: &str) -> Option<&'a Value> {
    // (1) mapping item access, with the segment as a STRING. `ObjectKey`
    //     hashes its `Str` variant exactly as the `str` does, so this is the
    //     same `get("k")` every other caller makes.
    if let Value::Object(obj) = current {
        if let Some(found) = obj.get(part) {
            return Some(found);
        }
    }

    // (2) attribute access — see the note above; a `Value` has none.

    // (3) integer index. `int(bit)`, so `"007"` is the index 7, and a segment
    //     that is not an integer at all stops the walk here as Django's
    //     `ValueError` does.
    let index = part.parse::<usize>().ok()?;
    match current {
        Value::List(items) | Value::Tuple(items) => items.get(index),
        // A dict subscripted by an int. `ObjectKey` compares numerics BY
        // VALUE across `Int`/`Float`/`Bool`/`Decimal`/`BigInt` (#2339), which
        // is what makes `{{ d.1 }}` resolve against `{1.0: …}` and
        // `{True: …}` exactly as CPython's `{1.0: "a"}[1]` does.
        Value::Object(obj) => obj.get(&crate::ObjectKey::Int(index as i64)),
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
    /// Name ALIASES: `name -> <dotted path prefix>` (#2375).
    ///
    /// djust's safety channel is keyed BY NAME — `safe_keys` holds dotted
    /// paths written by `rust_bridge._collect_safe_keys` — so a construct that
    /// binds a value to a NEW name has two ways to keep its grants:
    ///
    /// * **copy** them, which is what [`Context::bind`] does at the NAME
    ///   granularity, and which cannot reach the paths BENEATH the name
    ///   without an `O(len(safe_keys))` scan per bind;
    /// * **alias** the name, which rewrites the whole dotted path on the way
    ///   IN to [`Context::is_safe`] and costs `O(1)`.
    ///
    /// This was `loop_var -> (iterable_name, index)`, an alias in everything
    /// but generality: it could express `item -> items.<i>` and nothing else,
    /// so `{% for x in rows %}{{ x.a }}` resolved its grant and
    /// `{% with q=p %}{{ q.a }}` did not. Widening it to a plain path prefix
    /// retires that split rather than adding a second copy — the #1646 cure
    /// of stating one mechanism rather than two.
    ///
    /// The invariant an alias asserts is that `name` **IS** the value at
    /// `<prefix>`, so it may only be registered where that correspondence is
    /// REAL. A filtered expression breaks it (`slice` shifts indices,
    /// `dictsort` reorders), and for a dict the loop's positional form is a
    /// live XSS rather than a theoretical one (#2334) — see the guards at
    /// each registration site. Registering nothing costs only over-escaping,
    /// which is the direction to fail in.
    ///
    /// A [`Context::bind`] REMOVES the alias for the name it binds, through
    /// [`Context::revoke_safe_subtree`]. That is not an optimisation: without
    /// it, `{% with q=p %}{% with q=hostile %}{{ q.a }}` would resolve `q.a`
    /// through the STALE alias to `p.a` and emit the hostile value raw —
    /// exactly the under-escape #2361/#2363 closed for the name itself,
    /// reopened one path segment down. "A bind REPLACES the grant" has to
    /// mean the alias too, or it means nothing.
    aliases: AHashMap<String, String>,
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
            aliases: self.aliases.clone(),
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
            aliases: AHashMap::new(),
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
            aliases: AHashMap::new(),
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

    /// Bind `name` to `value`, **REPLACING** whatever safety grant `name`
    /// carried (#2361, #2363).
    ///
    /// This is the one door for every template construct that binds a
    /// resolved value to a NEW NAME — `{% with %}`, `{% include … with %}`,
    /// the `{% for %}` loop variable and its tuple unpacking, and the
    /// `{% … as x %}` assign-tag merge. [`Context::set`] moves the VALUE;
    /// this moves the value AND the grant, which is the whole of the defect
    /// those two issues describe from opposite sides.
    ///
    /// # Why a REPLACEMENT and not an addition
    ///
    /// djust's safety channel is keyed BY NAME: `safe_keys` holds dotted
    /// paths written by `rust_bridge._collect_safe_keys`, and
    /// [`Context::is_safe`] answers by looking a name up in it. A binding
    /// therefore has to move the grant in BOTH directions, and only one of
    /// them was reported:
    ///
    /// * **grant absent, value safe** — `{% with q=p|linebreaks %}` bound the
    ///   `Value` and dropped the `bool` beside it, so `{{ q }}` escaped what
    ///   `{{ p|linebreaks }}` emits live. Over-escaping (#2363).
    /// * **grant present, value NOT safe** — a bind that SHADOWS a marked
    ///   name inherited the stale grant, so with `p` marked
    ///   `{% with p=hostile %}{{ p }}{% endwith %}` emitted the hostile value
    ///   RAW where Django escapes it. That is an UNDER-escape, the one
    ///   direction this machinery must never move in, and it was found only
    ///   by measuring the over-escape above. Writing `bind` as
    ///   "also carry a grant" would have left it open; writing it as
    ///   "a bind replaces the grant" retires both at once — the #2129 lesson
    ///   that a rule about the OPERATION beats a rule about the values.
    ///
    /// # The paths BENEATH the name go too
    ///
    /// `safe_keys` holds `p.a` as readily as `p`, and those descendants
    /// described the value being SHADOWED, not the new one. Leaving them
    /// makes `{% with p=hostile_dict %}{{ p.a }}{% endwith %}` emit raw.
    /// So a bind revokes `name` and every `name.…` beneath it.
    ///
    /// The scan is skipped entirely when the set is empty — the common case
    /// for a render with no context marks at all — so a loop over a
    /// grant-free context pays one `is_empty` check per iteration.
    ///
    /// The revoke is not the whole story for a loop variable: `is_safe` also
    /// resolves through [`Context::set_loop_mapping`], which is registered
    /// only where the positional correspondence is genuine. That alias is
    /// left alone deliberately — it is how a real list's per-item marks
    /// (#2287) still resolve.
    pub fn bind(&mut self, name: String, value: Value, safe: bool) {
        self.revoke_safe_subtree(&name);
        self.set_safety(&name, safe);
        self.set(name, value);
    }

    /// The EXACT-NAME half of a [`Context::bind`]. `O(1)`.
    ///
    /// A `{% for %}` binds the same names once per iteration, so it hoists the
    /// `O(len(safe_keys))` [`Context::revoke_safe_subtree`] half OUT of the
    /// iteration — the shadowed outer grants it clears are the same ones every
    /// iteration would clear — and calls this per item. Splitting the door is
    /// a cost decision, not a semantic one: `revoke_safe_subtree` once, then
    /// `set` + `set_safety` per iteration, is `bind` per iteration, and
    /// `context::tests::the_loop_decomposition_of_bind_agrees_with_bind`
    /// pins that the two spellings agree so the split cannot drift.
    ///
    /// Without the hoist, a loop over an N-element list whose items are all
    /// marked pays `O(N²)` prefix comparisons — `_collect_safe_keys` emits one
    /// path per marked item, so both factors are the list's own length.
    pub fn set_safety(&mut self, name: &str, safe: bool) {
        if safe {
            self.safe_keys.insert(name.to_string());
        } else {
            self.safe_keys.remove(name);
        }
    }

    /// The SUBTREE half of a [`Context::bind`]: drop the grant on `key` and on
    /// every dotted path beneath it. `O(len(safe_keys))`.
    ///
    /// The descendants go because they described the value being SHADOWED.
    /// With `p.a` marked, leaving them makes
    /// `{% with p=hostile_dict %}{{ p.a }}{% endwith %}` emit raw.
    pub fn revoke_safe_subtree(&mut self, key: &str) {
        // The ALIASES go first, and deliberately BEFORE the `safe_keys`
        // early-return below (#2375). An alias is a claim that one name IS the
        // value at some path; a bind makes two such claims false, and BOTH are
        // UNDER-escapes rather than over-escapes.
        //
        // 1. The alias ON this name. `{% with q=p %}{% with q=hostile %}`
        //    would otherwise resolve `q.a` through the stale `p.a` and emit
        //    raw. That is #2378's "a bind REPLACES the grant", one path
        //    segment down.
        //
        // 2. Every alias whose TARGET is this name, or lives beneath it. This
        //    one was found by probing rather than by reading, and it was a
        //    LIVE XSS in the first version of this change:
        //
        //        {% with q=p %}{% with p=r|safe %}{{ q }}{% endwith %}{% endwith %}
        //
        //    binds `q` to the ORIGINAL `p` and then re-binds `p` to something
        //    SAFE. `set_safety("p", true)` marks the NAME `p`; the surviving
        //    alias `q -> p` then answered `is_safe("q")` from it, and `q`'s
        //    value — the original, hostile one — went to the page UNESCAPED.
        //    An alias asserts an identity, so rebinding either END of it has
        //    to retire it.
        //
        // Both sit above the `safe_keys.is_empty()` guard, and that placement
        // is load-bearing rather than defensive: in the leak above `safe_keys`
        // IS empty at this point — `set_safety` fills it one line later — so
        // an alias removal under the guard would not run at all.
        self.aliases.remove(key);
        let beneath = format!("{key}.");
        self.aliases
            .retain(|_, target| target != key && !target.starts_with(&beneath));
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

        // If not found, rewrite the path through the name ALIASES (#2375).
        // `item.content` becomes `items.0.content` inside a loop, and `q.a`
        // becomes `p.a` inside `{% with q=p %}` — one mechanism for what used
        // to be a loop-only one.
        if let Some(resolved_key) = self.resolve_alias(key) {
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
        if let Some(resolved) = self.resolve_alias(key) {
            prefixes.push(resolved);
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
                current = lookup_segment(current, part)?;
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
    /// **The container is a [`Value::DictView`], a live view** (#2340). It was
    /// a plain `Value::List` until then, which differed from Python's in two
    /// observable ways: `str()` read `[…]` rather than `dict_items([…])`, and
    /// it was subscriptable and JSON-serializable where Python's is not.
    /// See that variant's docs for the three-way split Django's own filters
    /// make, which was measured across all of them rather than reasoned about.
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
        let kind = match last {
            "keys" => crate::DictViewKind::Keys,
            "values" => crate::DictViewKind::Values,
            _ => crate::DictViewKind::Items,
        };
        Some(Value::DictView {
            kind,
            items: match kind {
                crate::DictViewKind::Keys => crate::object_key::dict_iteration_values(map),
                crate::DictViewKind::Values => map.values().cloned().collect(),
                // `items` — each entry a 2-`Tuple`, which is what makes
                // `{% for k, v in d.items %}` unpack through the renderer's
                // existing tuple-unpacking branch, and what makes `{{ x }}`
                // over one render `('a', 1)` as Python does.
                crate::DictViewKind::Items => map
                    .iter()
                    .map(|(k, v)| Value::Tuple(vec![Value::from(k.clone()), v.clone()]))
                    .collect(),
            },
        })
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

    /// Register a loop variable's alias: `loop_var` IS `<iterable>.<index>`.
    ///
    /// A thin spelling of [`Context::set_alias`] kept because the loop's
    /// index is a `usize` and every caller would otherwise format it.
    pub fn set_loop_mapping(&mut self, loop_var: String, iterable_name: String, index: usize) {
        // Expanded against SELF, and that is the right context here: the loop
        // renders its body against this very `ctx`, so an outer alias on the
        // iterable's own name (a nested loop's `row`) is exactly the one that
        // applies.
        let base = self.alias_path(&iterable_name);
        self.set_alias(loop_var, format!("{base}.{index}"));
    }

    /// Register `name` as an alias for the value at the dotted path `target`.
    ///
    /// The caller is responsible for two things, and both are security
    /// boundaries rather than hygiene:
    ///
    /// 1. **The correspondence is REAL** — see the `aliases` field docs and
    ///    the `bare_dotted_path` guard the renderer applies at every call site.
    /// 2. **`target` is already expanded**, through
    ///    [`Context::alias_path`] on the context the EXPRESSION was resolved
    ///    against.
    ///
    /// (2) is not a convention, it is the fix for a second live XSS this
    /// mechanism had. Django's `{% with %}` resolves EVERY assignment against
    /// the OUTER context (`WithNode.render` builds the whole `values` dict
    /// before `context.update`), so in `{% with a=p b=a %}` the name `b` binds
    /// the OUTER `a`. Expanding `b`'s path inside the NEW context would walk
    /// the `a -> p` alias registered one line earlier and point `b` at `p`
    /// instead — and with `p` marked and the outer `a` hostile, `{{ b }}`
    /// emitted the hostile value RAW. Making the expansion the caller's
    /// explicit step is what forces each site to name which context it means.
    ///
    /// A self-alias is refused: `{% with p=p %}` would otherwise make
    /// `is_safe` consult a name that no longer describes the bound value, and
    /// the `bind` that precedes it has already said what `p`'s grant is.
    pub fn set_alias(&mut self, name: String, target: String) {
        if target == name || target.starts_with(&format!("{name}.")) {
            return;
        }
        self.aliases.insert(name, target);
    }

    /// `path` with its first segment expanded through THIS context's aliases.
    ///
    /// The registration-time half of the alias mechanism: collapsing the chain
    /// once, here, is what keeps [`Context::is_safe`] to a single hop on the
    /// hot path. Inside `{% for row in rows %}`, `{% with q=row.sub %}`
    /// expands to `rows.<i>.sub` — `row.sub` would resolve against a
    /// `safe_keys` set that never spells `row` at all.
    pub fn alias_path(&self, path: &str) -> String {
        self.expand_alias(path)
    }

    /// Clear a loop variable's alias (when exiting the loop scope).
    pub fn clear_loop_mapping(&mut self, loop_var: &str) {
        self.aliases.remove(loop_var);
    }

    /// `key` rewritten through the alias on its FIRST segment, or `None` when
    /// that segment is not aliased.
    ///
    /// Returns `None` rather than `key` so a caller can tell "no alias" from
    /// "an alias that resolves to itself" — `is_safe` has already checked the
    /// un-rewritten spelling by the time it calls this.
    fn resolve_alias(&self, key: &str) -> Option<String> {
        let (first, rest) = match key.split_once('.') {
            Some((first, rest)) => (first, Some(rest)),
            None => (key, None),
        };
        let prefix = self.aliases.get(first)?;
        Some(match rest {
            Some(rest) => format!("{prefix}.{rest}"),
            None => prefix.clone(),
        })
    }

    /// `path` with its first segment expanded through the aliases, or `path`
    /// unchanged. The registration-time half of [`Context::resolve_alias`].
    fn expand_alias(&self, path: &str) -> String {
        self.resolve_alias(path).unwrap_or_else(|| path.to_string())
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
        // A `DictView` carrying its KIND, not a bare list (#2340): the kind is
        // what names the container in `str()`, and asserting it here is what
        // stops `d.keys` from silently resolving to a `dict_values(...)`.
        match ctx.resolve("d.keys").unwrap().unwrap() {
            Value::DictView { kind, items } => {
                assert_eq!(kind, crate::DictViewKind::Keys);
                assert_eq!(
                    items.iter().map(|v| v.to_string()).collect::<Vec<_>>(),
                    vec!["a", "b"],
                    "Python's dict order, not a hash order — a HashMap here would \
                     make `{{% for k in d %}}` nondeterministic across renders"
                );
            }
            other => panic!("expected a dict view, got {other:?}"),
        }
        match ctx.resolve("d.values").unwrap().unwrap() {
            Value::DictView { kind, items } => {
                assert_eq!(kind, crate::DictViewKind::Values);
                assert_eq!(
                    items.iter().map(|v| v.to_string()).collect::<Vec<_>>(),
                    vec!["1", "2"]
                )
            }
            other => panic!("expected a dict view, got {other:?}"),
        }
        match ctx.resolve("d.items").unwrap().unwrap() {
            Value::DictView { kind, items } => {
                assert_eq!(kind, crate::DictViewKind::Items);
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
            other => panic!("expected a dict view, got {other:?}"),
        }
    }

    #[test]
    fn each_kind_names_its_own_container_in_str() {
        // The whole of #2340's visible half, at the type level.
        let ctx = dict_ctx();
        for (path, want) in [
            ("d.keys", "dict_keys(['a', 'b'])"),
            ("d.values", "dict_values([1, 2])"),
            ("d.items", "dict_items([('a', 1), ('b', 2)])"),
        ] {
            assert_eq!(
                ctx.resolve(path).unwrap().unwrap().to_string(),
                want,
                "{path}"
            );
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
            Value::DictView { kind, items } => {
                assert_eq!(kind, crate::DictViewKind::Keys);
                assert_eq!(
                    items.iter().map(|v| v.to_string()).collect::<Vec<_>>(),
                    vec!["x"]
                )
            }
            other => panic!("expected a dict view, got {other:?}"),
        }
    }
    // ---- `Context::bind` — a binding REPLACES the grant (#2361, #2363) ----

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
    fn bind_grants_when_the_value_is_safe() {
        let mut ctx = Context::new();
        ctx.bind("x".to_string(), Value::String("<b>".into()), true);
        assert!(ctx.is_safe("x"));
    }

    #[test]
    fn bind_revokes_a_stale_grant_on_the_shadowed_name() {
        let mut ctx = ctx_with_a_marked_name();
        ctx.bind("p".to_string(), Value::String("<img>".into()), false);
        assert!(!ctx.is_safe("p"), "the shadowed name kept its grant");
    }

    #[test]
    fn bind_revokes_the_grants_beneath_the_shadowed_name() {
        let mut ctx = ctx_with_a_marked_name();
        ctx.bind("p".to_string(), Value::String("<img>".into()), false);
        assert!(
            !ctx.is_safe("p.a"),
            "a descendant of the shadowed name survived"
        );
    }

    #[test]
    fn bind_leaves_every_other_name_alone() {
        let mut ctx = ctx_with_a_marked_name();
        ctx.bind("p".to_string(), Value::String("<img>".into()), false);
        assert!(ctx.is_safe("q"), "bind revoked an unrelated name");
    }

    #[test]
    fn a_safe_bind_still_clears_the_shadowed_descendants() {
        // The new value is safe AS A WHOLE; nothing is known about its
        // sub-paths, and the old ones described a different value.
        let mut ctx = ctx_with_a_marked_name();
        ctx.bind("p".to_string(), Value::String("<b>ok</b>".into()), true);
        assert!(ctx.is_safe("p"));
        assert!(!ctx.is_safe("p.a"));
    }

    /// The `{% for %}` arm hoists `revoke_safe_subtree` out of its iteration
    /// and calls `set_safety` per item — see [`Context::set_safety`]. That
    /// decomposition is a COST decision, so it must be observationally
    /// identical to calling `bind` each time, or the split has drifted.
    #[test]
    fn the_loop_decomposition_of_bind_agrees_with_bind() {
        let items = [
            (Value::String("<b>0</b>".into()), true),
            (Value::String("<i>1</i>".into()), false),
            (Value::String("<u>2</u>".into()), true),
        ];

        // Spelling A — `bind` per iteration.
        let mut a = ctx_with_a_marked_name();
        // Spelling B — one subtree revoke, then `set` + `set_safety` per item.
        let mut b = ctx_with_a_marked_name();
        b.revoke_safe_subtree("p");

        for (value, safe) in items.iter() {
            a.bind("p".to_string(), value.clone(), *safe);

            b.set("p".to_string(), value.clone());
            b.set_safety("p", *safe);

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
}
