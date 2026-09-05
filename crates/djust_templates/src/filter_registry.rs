//! Custom filter registry for project-defined Django ``@register.filter`` callables.
//!
//! Mirrors the design of [`crate::registry`] (which dispatches custom *tags*),
//! but for filters. Built-in Django filters live as native-Rust matches in
//! [`crate::filters`]; project-level custom filters that come from
//! ``@register.filter`` in a Django app's ``templatetags/`` package are
//! registered here at engine bootstrap time.
//!
//! # Lazy vs eager
//!
//! This implementation is **eager** — Python registers each filter callable
//! exactly once via [`register_custom_filter`], typically by walking
//! ``template.engines['django'].engine.template_libraries`` at import time.
//! At render time, [`apply_custom_filter`] performs a HashMap lookup
//! followed by a GIL acquire + Python call. The eager design matches the
//! existing tag-handler pattern in [`crate::registry`] and avoids a
//! per-render GIL acquisition for "is this a known filter name?" probes.
//!
//! Memory cost: one entry per project filter. ~50 bytes of `String` +
//! `Py<PyAny>` + `FilterMeta` per registration. Even projects with hundreds
//! of custom filters fit comfortably.
//!
//! # Filter signature
//!
//! Django filter callables accept ``(value, arg=None)`` and return a
//! string (or a SafeString when they produce markup of their own).
//! ``needs_autoescape=True`` filters additionally accept ``autoescape`` as a
//! kwarg.
//!
//! - ``value`` — the filtered expression's current `Value`, converted to
//!   the appropriate Python type (str/int/float/bool/None/list/dict).
//! - ``arg`` — for one-argument filters, the resolved argument:
//!     - quoted literals (``"foo"``) are passed as ``str``,
//!     - bare identifiers are resolved against the template context. If
//!       the context resolves to a primitive, it's passed as that type;
//!       otherwise as the value's natural Python representation.
//! - return — the result. The renderer escapes it unless it is a runtime
//!   ``SafeString``, or the filter is ``is_safe=True`` AND its input was
//!   already safe — Django's ``is_safe and isinstance(obj, SafeData)`` rule,
//!   applied in `renderer::filter_output_is_safe` via
//!   [`is_custom_filter_safe`] (#2548). The flag alone never grants safety.

use crate::filters::InputSafety;
use crate::Value;
use djust_core::Context;
use once_cell::sync::Lazy;
use pyo3::prelude::*;
use pyo3::types::{PyDict, PyList, PyString, PyTuple};
use std::collections::HashMap;
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::RwLock;

/// Django's own reading of "this value is already HTML" (#2290, #2379).
///
/// `render_value_in_context` stringifies any NON-`str` value —
/// `if not issubclass(type(value), str): value = str(value)` — BEFORE it looks
/// for `__html__` at all, so only a genuine `str` subclass carrying the marker
/// is trusted.
///
/// **Requiring `str` subclass-ness is the security half, not tidiness.** A
/// non-`str` object that advertises `__html__` and renders attacker-controlled
/// HTML through its `__str__` would otherwise reach output unescaped, because
/// `Value`'s `FromPyObject` stringifies an arbitrary object via `__str__`.
/// `is_instance_of::<PyString>()` is an `isinstance(_, str)` check — true for
/// a `SafeString`, false for the impostor.
///
/// One function, three callers: the custom-FILTER return (#2290), and the
/// custom-TAG and BLOCK-tag returns (#2379). The tag path had no such test at
/// all and inserted a handler's return verbatim; giving it a second copy of
/// the rule is the drift this repo keeps retiring (#1646).
pub fn py_value_is_safe_string(obj: &Bound<'_, PyAny>) -> bool {
    obj.is_instance_of::<PyString>() && obj.hasattr("__html__").unwrap_or(false)
}

/// Per-filter metadata mirroring Django's filter object attributes.
#[derive(Debug, Clone, Default)]
pub struct FilterMeta {
    /// ``filter.is_safe`` — when true, the renderer must NOT HTML-escape
    /// the filter's output. The Python callable is expected to return
    /// already-escaped content (e.g. via ``mark_safe``).
    pub is_safe: bool,
    /// ``filter.needs_autoescape`` — when true, the dispatcher passes
    /// ``autoescape=True`` as a kwarg so the filter can branch on the
    /// surrounding autoescape policy.
    pub needs_autoescape: bool,
}

struct FilterEntry {
    callable: Py<PyAny>,
    meta: FilterMeta,
}

/// Global registry mapping filter names to Python callables + metadata.
///
/// `RwLock` (not `Mutex`): registration is one-time bootstrap; lookup is
/// read-only and on the hot render path, so concurrent renders share the
/// read lock.
static FILTER_REGISTRY: Lazy<RwLock<HashMap<String, FilterEntry>>> =
    Lazy::new(|| RwLock::new(HashMap::new()));

/// Hot-path short-circuit guard: ``true`` once any custom filter has been
/// registered in this process (ever).
///
/// The renderer consults [`is_custom_filter_safe`] inside the
/// ``filter_specs.iter().any(|name| ...)`` loop on every variable
/// expansion. For the common case — no project-level custom filters —
/// the read-lock acquire on every name was wasted work. This `AtomicBool`
/// is checked first; the lock is only touched when at least one filter
/// has actually been registered.
///
/// Once flipped to ``true`` it stays that way for the process lifetime
/// even if ``clear_custom_filters`` empties the registry. That's
/// intentional: clearing is rare (test teardown) and the read-lock path
/// then handles the "name not in map" case correctly anyway. The
/// alternative — toggling the flag on clear — would race with another
/// thread that's mid-render.
static ANY_CUSTOM_FILTERS_REGISTERED: AtomicBool = AtomicBool::new(false);

/// Register a project-defined custom filter from Python.
///
/// # Arguments
///
/// * ``name`` — filter name as used in templates (``{{ x|name }}``).
/// * ``callable`` — Django filter callable (``(value, arg=None) -> str``).
/// * ``is_safe`` — Django filter's ``is_safe`` attribute (skip auto-escape).
/// * ``needs_autoescape`` — Django filter's ``needs_autoescape`` attribute
///   (pass ``autoescape=True`` as kwarg).
///
/// Re-registering an existing name overwrites — matching Django's behaviour
/// when a Library is re-imported.
#[pyfunction]
#[pyo3(signature = (name, callable, is_safe=false, needs_autoescape=false))]
pub fn register_custom_filter(
    name: String,
    callable: Py<PyAny>,
    is_safe: bool,
    needs_autoescape: bool,
) -> PyResult<()> {
    // Parse validation consults this registry (`is_known_filter`), so it is
    // part of the generation the template cache is keyed on (#2668 review).
    let _bump = crate::registry::BumpOnReturn;
    let mut registry = FILTER_REGISTRY.write().map_err(|e| {
        PyErr::new::<pyo3::exceptions::PyRuntimeError, _>(format!("Filter registry lock: {e}"))
    })?;
    registry.insert(
        name,
        FilterEntry {
            callable,
            meta: FilterMeta {
                is_safe,
                needs_autoescape,
            },
        },
    );
    // Flip the hot-path guard so renderer's ``is_custom_filter_safe`` stops
    // short-circuiting and starts consulting the registry. ``Release``
    // pairs with the renderer's ``Acquire`` load to ensure registry
    // visibility, though in practice the write-lock acquisition that
    // precedes this already provides that ordering. Belt + suspenders.
    ANY_CUSTOM_FILTERS_REGISTERED.store(true, Ordering::Release);
    Ok(())
}

/// Unregister a custom filter (returns ``true`` if a filter was removed).
#[pyfunction]
pub fn unregister_custom_filter(name: &str) -> PyResult<bool> {
    // Parse validation consults this registry (`is_known_filter`), so it is
    // part of the generation the template cache is keyed on (#2668 review).
    let _bump = crate::registry::BumpOnReturn;
    let mut registry = FILTER_REGISTRY.write().map_err(|e| {
        PyErr::new::<pyo3::exceptions::PyRuntimeError, _>(format!("Filter registry lock: {e}"))
    })?;
    Ok(registry.remove(name).is_some())
}

/// Check if a custom filter is registered (intended for tests + diagnostics).
#[pyfunction]
pub fn has_custom_filter(name: &str) -> PyResult<bool> {
    let registry = FILTER_REGISTRY.read().map_err(|e| {
        PyErr::new::<pyo3::exceptions::PyRuntimeError, _>(format!("Filter registry lock: {e}"))
    })?;
    Ok(registry.contains_key(name))
}

/// Clear all registered custom filters (primarily for tests).
#[pyfunction]
pub fn clear_custom_filters() -> PyResult<()> {
    // Parse validation consults this registry (`is_known_filter`), so it is
    // part of the generation the template cache is keyed on (#2668 review).
    let _bump = crate::registry::BumpOnReturn;
    let mut registry = FILTER_REGISTRY.write().map_err(|e| {
        PyErr::new::<pyo3::exceptions::PyRuntimeError, _>(format!("Filter registry lock: {e}"))
    })?;
    registry.clear();
    Ok(())
}

/// List all registered custom filter names (for diagnostics).
#[pyfunction]
pub fn get_registered_custom_filters() -> PyResult<Vec<String>> {
    let registry = FILTER_REGISTRY.read().map_err(|e| {
        PyErr::new::<pyo3::exceptions::PyRuntimeError, _>(format!("Filter registry lock: {e}"))
    })?;
    Ok(registry.keys().cloned().collect())
}

// ============================================================================
// Internal Rust API (called from filters.rs / renderer.rs)
// ============================================================================

/// Returns ``true`` if `name` is a project-registered custom filter.
///
/// The non-`PyResult` counterpart of the `#[pyfunction]`
/// [`has_custom_filter`] above, for Rust callers that have no `Python<'_>`
/// to raise into — [`crate::filters::is_known_filter`], which the PARSER
/// consults while compiling a filter chain (#2419).
///
/// Lock poisoning answers ``false``, which is the same fail-soft
/// [`is_custom_filter_safe`] takes. It is the conservative direction here
/// too: a poisoned lock makes the parser fall through to the render-time
/// ``Unknown filter`` path — djust's pre-#2419 behaviour — rather than
/// refusing a template whose filter may well be registered.
///
/// Short-circuits on the [`ANY_CUSTOM_FILTERS_REGISTERED`] `AtomicBool` for
/// the same reason [`is_custom_filter_safe`] does: a project with no custom
/// filters pays one atomic load per filter spec rather than a lock acquire.
pub fn is_registered_custom_filter(name: &str) -> bool {
    if !ANY_CUSTOM_FILTERS_REGISTERED.load(Ordering::Acquire) {
        return false;
    }
    FILTER_REGISTRY
        .read()
        .map(|reg| reg.contains_key(name))
        .unwrap_or(false)
}

/// Returns ``true`` if a registered custom filter has ``is_safe=True``.
///
/// The renderer consults this alongside the built-in ``IS_SAFE_FILTERS`` list,
/// and ONLY when the filter's input was already safe (#2548): Django's
/// ``is_safe`` means "a safe input stays safe through this filter", not "this
/// filter's output is safe". The unconditional grants — a runtime
/// ``SafeString`` return, or a built-in that escapes internally — live in
/// ``renderer::filter_output_is_safe``, not here.
///
/// Hot path: this is called once per filter in the
/// ``filter_specs.iter().any(...)`` loop on every variable expansion.
/// We short-circuit on the [`ANY_CUSTOM_FILTERS_REGISTERED`]
/// `AtomicBool` so projects that never register custom filters pay
/// only an atomic load, not a lock acquisition. ``Acquire`` ordering
/// pairs with the ``Release`` store in [`register_custom_filter`].
pub fn is_custom_filter_safe(name: &str) -> bool {
    if !ANY_CUSTOM_FILTERS_REGISTERED.load(Ordering::Acquire) {
        return false;
    }
    FILTER_REGISTRY
        .read()
        .map(|reg| reg.get(name).map(|e| e.meta.is_safe).unwrap_or(false))
        .unwrap_or(false)
}

/// Restore Django's `SafeData` markers on the value crossing INTO Python
/// (#2290).
///
/// A `Value` carries no safety of its own — it is a plain data enum, so
/// `Value::String("<b>x</b>")` is the same object whether the chain reached it
/// through `|safe` or straight off an unescaped context variable. The renderer
/// is the only layer that knows which, and after #2284/#2283 it reports the
/// answer as [`InputSafety`]. Without this function that answer stopped at the
/// PyO3 boundary: `into_pyobject` hands Python a bare `str`, so every custom
/// filter saw `isinstance(value, SafeData) == False` and Django's canonical
/// `needs_autoescape` opening line
///
/// ```text
/// autoescape = autoescape and not isinstance(value, SafeData)
/// ```
///
/// could never take its second branch.
///
/// # Which field drives which wrap
///
/// Measured against Django 5.2 with a live `@register.filter` probe on one side
/// and this registry on the other (`p = "<b>x</b>"`, `L = [p, "<i>y</i>"]`):
///
/// | template | what Django hands the filter |
/// |---|---|
/// | `{{ p\|safe\|probe }}` | `SafeString`, `isinstance(_, SafeData)` **true** |
/// | `{{ p\|escape\|probe }}` | `SafeString` — `escape` returns one (#2281) |
/// | `{{ L\|safeseq\|probe }}` | a PLAIN `list` of `SafeString` ITEMS |
/// | `{{ L\|escapeseq\|probe }}` | ditto |
/// | `{{ L\|safeseq\|slice:':1'\|probe }}` | ditto — `slice` returns the same objects |
///
/// So **both** fields drive a wrap, each at its own granularity, because they
/// are two different Django states and one cannot stand in for the other:
///
/// * `container` — `mark_safe` the VALUE. `safeseq`'s output is not `SafeData`,
///   so answering this field for it would be wrong in the permissive direction.
/// * `items` — `mark_safe` each ELEMENT of a list/tuple and leave the sequence
///   itself plain, which is exactly what
///   `[mark_safe(o) for o in value]` produces. Answering `container` here would
///   hand a custom filter a safety Django never granted.
///
/// # Only `str` is wrapped, and that is the conservative half
///
/// Django's `mark_safe` STRINGIFIES a non-`str` (`mark_safe(42)` is
/// `SafeString("42")`), and reproducing that would change the TYPE an existing
/// filter receives — `{{ n|safe|my_filter }}` would start handing `my_filter` a
/// string where djust hands it an `int` today, and `{{ absent|safe|f }}` would
/// hand it the text `"None"` (djust passes `None`; Django's
/// `string_if_invalid` had already made it `""`). Those are pre-existing
/// SHAPE divergences with their own blast radius, not the safety gap #2290 is
/// about, so a non-`str` is passed through untouched: the filter keeps seeing
/// `isinstance(_, SafeData) == False`, which is the ESCAPING direction and is
/// exactly what it saw before this function existed.
///
/// # It can only ever narrow the gap, never open one
///
/// Every wrap is gated on a field the renderer set to `true`, and the renderer
/// sets those only when the context `mark_safe`d the value or an earlier
/// `|safe` / safe-output / item-safe filter marked it (see
/// `renderer::filter_output_is_safe` / `filter_output_items_are_safe`). A value
/// nothing ever marked reaches Python exactly as it did before. That is the
/// property #2290 requires: djust may stop over-escaping, and must not become
/// more permissive than Django anywhere.
fn mark_input_safety<'py>(
    py: Python<'py>,
    obj: Bound<'py, PyAny>,
    input_safety: InputSafety,
) -> PyResult<Bound<'py, PyAny>> {
    // The overwhelmingly common case — nothing upstream marked anything — pays
    // one branch and never touches `sys.modules`.
    if !input_safety.container && !input_safety.items {
        return Ok(obj);
    }
    // `py.import` resolves out of `sys.modules` after the first call, so this
    // is a dict lookup on the warm path. Django is a hard dependency of the
    // engine's only caller; if it somehow cannot be imported the value is
    // handed over UNMARKED, which is the escaping direction and identical to
    // pre-#2290 behaviour.
    let Ok(safestring) = py.import("django.utils.safestring") else {
        return Ok(obj);
    };
    let Ok(mark_safe) = safestring.getattr("mark_safe") else {
        return Ok(obj);
    };

    if input_safety.container {
        // `is_instance_of::<PyString>()` — Django's own reading. A `SafeString`
        // IS a `str` subclass, and `render_value_in_context` stringifies any
        // non-`str` before it looks for `__html__` at all.
        if obj.is_instance_of::<PyString>() {
            return mark_safe.call1((obj,));
        }
        return Ok(obj);
    }

    // `items` — Django marks the ELEMENTS and rebuilds nothing else, so the
    // sequence stays a `list` and a filter branching on its type keeps its
    // answer.
    //
    // BOTH sequence shapes, and the tuple arm's history is worth keeping
    // because it is a worked example of a reachability claim expiring (#2305).
    //
    // When #2290 shipped, `items` could only be `true` via
    // `renderer::filter_output_items_are_safe`, whose every path then
    // originated at `safeseq`/`escapeseq` — Django's `[… for o in value]`, a
    // list comprehension — so a tuple INPUT had already become a list by the
    // time the grant existed. A first draft carried a parallel `PyTuple` arm,
    // the gate-off reported it SURVIVED, and it was deleted as decorative
    // rather than defensive (#1859). Correct on the evidence available.
    //
    // #2287 then added `Context::items_are_safe`, a SECOND producer that reads
    // the grant off `mark_safe_keys` and accepts `Value::Tuple` — so the claim
    // expired the moment the two changes met, and the arm came back with the
    // empirical proof #2290 correctly demanded and could not get at the time.
    // Two entry points reach it, both public in `_rust.pyi`:
    //
    //   render_template_with_dirs(tpl, {"p": ("<b>", "<i>")}, [], ["p.0","p.1"])
    //   RustLiveView(tpl).update_state({"p": (...)}) + mark_safe_keys([...])
    //
    // Neither normalizes, so the tuple survives as `Value::Tuple` and
    // `IntoPyObject` (`djust_core/src/lib.rs`) rebuilds a real `PyTuple` on the
    // way out. What still keeps every FRAMEWORK path off this arm is
    // `normalize_django_value`, which collapses a Python tuple to a list before
    // it ever crosses into Rust — `LiveView` via `rust_bridge`,
    // `TemplateMixin`'s page-shell render, `SimpleLiveView` and the template
    // backend all normalize first, and the latter two pass no `safe_keys` at
    // all. That is why this was a parity gap and never a regression.
    //
    // The output SHAPE is the input shape, because Django's own marking is
    // `[mark_safe(o) for o in value]`-style per element and leaves the sequence
    // object alone — a filter branching on `isinstance(value, tuple)` must keep
    // Django's answer, which building a list here would silently change.
    if let Ok(seq) = obj.cast::<PyList>() {
        let out = PyList::empty(py);
        for item in seq.iter() {
            out.append(mark_item(&mark_safe, item)?)?;
        }
        return Ok(out.into_any());
    }
    if let Ok(seq) = obj.cast::<PyTuple>() {
        let mut out: Vec<Bound<'py, PyAny>> = Vec::with_capacity(seq.len());
        for item in seq.iter() {
            out.push(mark_item(&mark_safe, item)?);
        }
        return Ok(PyTuple::new(py, out)?.into_any());
    }
    Ok(obj)
}

/// One ELEMENT of a sequence carrying an item grant.
///
/// Extracted so the `PyList` and `PyTuple` arms cannot drift apart on the
/// element policy — the failure mode that made the tuple arm's first life
/// unprovable was precisely that the two were separate copies of one rule
/// (#1646).
///
/// **An element under an item grant is always a `str`** (#2337). This carried
/// an `is_instance_of::<PyString>()` guard with a non-`str` pass-through, and
/// the pass-through had no producer: EVERY writer of `InputSafety.items = true`
/// can only ever grant on a sequence whose elements are `Value::String`, which
/// `IntoPyObject` (`djust_core/src/lib.rs`) turns into a `PyString`. The three
/// of them, which
/// `python/tests/test_mark_item_dead_branch_2337.py::TestTheProducerEnumerationIsComplete`
/// pins mechanically rather than in this prose:
///
/// 1. `Context::items_are_safe` (#2287) — requires
///    `matches!(item, Value::String(_))` for EVERY element, and refuses
///    otherwise. That narrowing is load-bearing for a different reason
///    (`join` stringifies a sublist and Django escapes the repr), so it is not
///    going anywhere.
/// 2. `safeseq` / `escapeseq` — `ITEM_SAFE_OUTPUT_FILTERS`. Both CONSTRUCT
///    `Value::List(… Value::String(…) …)` unconditionally; `safeseq` has since
///    #2324, `escapeseq` always did.
/// 3. `slice` — `ITEM_SAFETY_PRESERVING_FILTERS`, and only when the grant was
///    already held. It selects elements and rebuilds the same shape, so it
///    cannot introduce a type its input did not have.
///
/// The renderer's three seed sites all read `context.items_are_safe(k)` for the
/// same `k` they resolve the value from, and `Context::resolve` returns
/// `Context::get`'s value verbatim on a hit — so the grant and the value can
/// never describe different objects.
///
/// Deleting the guard rather than keeping it is #1859: an unreachable branch is
/// decorative, not defensive, and while both mechanisms exist no test can tell
/// them apart (v1.1.1-2 retro). What replaces it is a test of the REACHABLE
/// paths — `TestANonConvertingProducerRefusesANonStrSequence` sweeps every
/// producer that does NOT itself convert its elements (arm 1 above, and arm 3
/// over it) × every non-`str` element shape, and asserts each element arrives
/// as its own type and NOT `SafeData`. If a future producer starts granting on
/// a sequence holding a non-`str`, `mark_safe` STRINGIFIES it (`mark_safe(42)`
/// is `SafeString("42")`) and every one of those goes red — which is the
/// property the guard was silently providing and the one worth keeping.
///
/// Arm 2 needs the other shape, because `safeseq`/`escapeseq` stringify FIRST
/// and there is then nothing left for a probe to observe: that axis is covered
/// by the structural pin on their constructors plus Django parity on the
/// downstream sinks. `TestAConvertingProducerLeavesNoNonStrElement` carries it,
/// and its own comment records the gate-off that proved the obvious assertion
/// there could not go red.
fn mark_item<'py>(
    mark_safe: &Bound<'py, PyAny>,
    item: Bound<'py, PyAny>,
) -> PyResult<Bound<'py, PyAny>> {
    mark_safe.call1((item,))
}

/// Apply a custom filter callable to a value with an optional argument.
///
/// Called from [`crate::filters::apply_filter_with_context`] when the
/// built-in filter match falls through. Returns ``None`` if no custom
/// filter is registered for the name (so the caller can fall through to
/// the standard ``Unknown filter`` error).
///
/// Argument resolution: when ``arg`` is provided as a non-empty string
/// after ``strip_filter_arg_quotes``, this function inspects the original
/// arg string for surrounding quotes:
/// - quoted (``"foo"`` or ``'foo'``) — passed to Python as a literal string
///   (with quotes already stripped by the caller).
/// - bare identifier — resolved against ``context`` first; if a binding
///   exists, the resolved `Value` is passed. Otherwise the bare identifier
///   string itself is passed (mirroring Django's tolerant behaviour where
///   filters accept literal arg text when no binding matches).
///
/// This split is the same convention `crate::filters::apply_filter_with_context`
/// already uses for built-ins like ``date`` (literal format string) vs
/// callers passing context-resolved values.
///
/// ``input_safety`` carries the renderer's reading of the INPUT's Django
/// `SafeData`-ness through to Python — see [`mark_input_safety`] (#2290).
pub fn apply_custom_filter(
    name: &str,
    value: &Value,
    arg: Option<&str>,
    context: Option<&Context>,
    arg_was_quoted: bool,
    autoescape: bool,
    input_safety: InputSafety,
) -> Option<Result<(Value, bool), String>> {
    // Hot-path short-circuit: skip the lock when no filter has ever
    // been registered. Mirrors the guard in ``is_custom_filter_safe``.
    if !ANY_CUSTOM_FILTERS_REGISTERED.load(Ordering::Acquire) {
        return None;
    }
    let (callable, meta) = {
        let registry = FILTER_REGISTRY.read().ok()?;
        let entry = registry.get(name)?;
        // clone_ref under the GIL; meta is plain Copy-ish.
        let callable = Python::attach(|py| entry.callable.clone_ref(py));
        (callable, entry.meta.clone())
    };

    let result = Python::attach(|py| -> Result<(Value, bool), String> {
        use pyo3::IntoPyObject;

        let py_value = value
            .clone()
            .into_pyobject(py)
            .map_err(|e| format!("Failed to convert filter input value: {e}"))?;
        // #2290: `Value` is safety-blind, so restore the `SafeData` markers the
        // renderer tracked before the filter ever sees the value.
        let py_value = mark_input_safety(py, py_value, input_safety)
            .map_err(|e| format!("Failed to mark filter input value safe: {e}"))?;

        // Resolve the arg into a Python object. Quoted literals → string;
        // bare identifiers → context resolve, fall back to the raw string
        // when not found.
        let py_arg: Option<pyo3::Bound<'_, PyAny>> = match arg {
            None => None,
            Some(s) if arg_was_quoted => {
                // Quoted literal — pass as plain string.
                Some(
                    s.into_pyobject(py)
                        .map_err(|e| format!("Failed to convert filter arg: {e}"))?
                        .into_any(),
                )
            }
            Some(s) => {
                // A NUMERIC literal first (#2547): Django's `Variable("5")`
                // is `int 5` and `Variable("1.5")` is `float 1.5` before any
                // context lookup happens, so `{{ s|trim:5 }}` hands the
                // filter an `int` and `value[:num]` slices. Before this arm
                // the token fell through to context resolution, found no
                // binding, and arrived as the `str` `"5"` — `slice indices
                // must be integers`. The ONE literal recogniser
                // (`renderer::django_literal`, #2376) decides; the `false`
                // in the pattern is "not a quoted string", which cannot
                // reach here anyway (`arg_was_quoted` took it above).
                if let Some((literal, false)) = crate::renderer::django_literal(s) {
                    Some(
                        literal
                            .into_pyobject(py)
                            .map_err(|e| format!("Failed to convert literal filter arg: {e}"))?
                            .into_any(),
                    )
                }
                // Bare identifier — try context resolution first. An
                // exception raised inside an auto-called method (ADR-024)
                // surfaces as a filter error rather than being swallowed.
                else if let Some(ctx) = context {
                    if let Some(resolved) = ctx
                        .resolve(s)
                        .map_err(|e| format!("Failed to resolve filter arg '{s}': {e}"))?
                    {
                        Some(
                            resolved
                                .into_pyobject(py)
                                .map_err(|e| format!("Failed to convert resolved filter arg: {e}"))?
                                .into_any(),
                        )
                    } else {
                        // No binding — pass the raw identifier as a string,
                        // matching Django's tolerant default.
                        Some(
                            s.into_pyobject(py)
                                .map_err(|e| format!("Failed to convert filter arg: {e}"))?
                                .into_any(),
                        )
                    }
                } else {
                    Some(
                        s.into_pyobject(py)
                            .map_err(|e| format!("Failed to convert filter arg: {e}"))?
                            .into_any(),
                    )
                }
            }
        };

        let callable_ref = callable.bind(py);

        // Build kwargs: ``needs_autoescape`` filters get the renderer's
        // current autoescape policy as a kwarg. Caller (renderer) supplies
        // the bool — today always ``true``, but threaded through the call
        // chain so when the Rust engine learns ``{% autoescape %}`` block
        // tracking, only the renderer call site needs to change.
        let kwargs = if meta.needs_autoescape {
            let kw = PyDict::new(py);
            kw.set_item("autoescape", autoescape)
                .map_err(|e| format!("Failed to set autoescape kwarg: {e}"))?;
            Some(kw)
        } else {
            None
        };

        let py_result = match (py_arg, kwargs) {
            (Some(arg_obj), Some(kw)) => callable_ref
                .call((py_value, arg_obj), Some(&kw))
                .map_err(|e| format_py_err(py, name, &e))?,
            (Some(arg_obj), None) => callable_ref
                .call1((py_value, arg_obj))
                .map_err(|e| format_py_err(py, name, &e))?,
            (None, Some(kw)) => callable_ref
                .call((py_value,), Some(&kw))
                .map_err(|e| format_py_err(py, name, &e))?,
            (None, None) => callable_ref
                .call1((py_value,))
                .map_err(|e| format_py_err(py, name, &e))?,
        };

        // Detect ``async def filter_x(...)`` — the user defined a
        // coroutine function. Without this check the unawaited coroutine
        // object stringifies to ``"<coroutine object ...>"`` and ends up
        // in the rendered HTML, with a "coroutine was never awaited"
        // RuntimeWarning at GC time. Raise a clear error instead so the
        // author fixes the filter signature.
        let inspect = py
            .import("inspect")
            .map_err(|e| format!("Failed to import inspect: {e}"))?;
        let is_coro: bool = inspect
            .call_method1("iscoroutine", (&py_result,))
            .and_then(|r| r.extract::<bool>())
            .unwrap_or(false);
        if is_coro {
            // Close the coroutine so Python doesn't emit a
            // "coroutine was never awaited" RuntimeWarning at GC.
            // ``coro.close()`` is the canonical cleanup for an
            // unawaited coroutine; ignore any error from close itself
            // since we're already raising a structured error.
            let _ = py_result.call_method0("close");
            return Err(format!(
                "Custom filter '{name}' is an async function (coroutine); \
                 the Rust template engine does not support async filters. \
                 Define '{name}' as a regular ``def`` (sync) filter or \
                 render this template via the Python path."
            ));
        }

        // Capture runtime safeness BEFORE collapsing to ``Value`` (#1660).
        // Django's ``SafeString`` / ``mark_safe()`` sets ``__html__``; the
        // ``extract::<Value>()`` below discards that marker (a SafeString and a
        // plain str both become ``Value::String``). The renderer uses this flag
        // to skip auto-escaping a value the filter explicitly marked safe at
        // runtime — independent of the static ``is_safe=True`` decoration —
        // matching Django's ``render_value_in_context``.
        //
        // CRITICAL — require ``str`` subclass-ness, not just ``__html__``.
        // Django's ``render_value_in_context`` stringifies any NON-``str`` value
        // (``if not issubclass(type(value), str): value = str(value)``) BEFORE
        // the ``__html__`` check, so only a genuine ``str``-subclass SafeString
        // is trusted. Trusting ``__html__`` alone is an XSS hole here: ``Value``'s
        // ``FromPyObject`` stringifies an arbitrary object via ``__str__``
        // (``djust_core`` lib.rs), so a non-``str`` object that advertises
        // ``__html__`` but renders attacker-controlled HTML through ``__str__``
        // would reach output UNESCAPED. ``is_instance_of::<PyString>()`` is an
        // ``isinstance(_, str)`` check — true for ``SafeString`` subclasses,
        // false for the impostor.
        let is_runtime_safe = py_value_is_safe_string(&py_result);

        // Convert back to Value. Filters typically return strings or
        // SafeStrings; via ``FromPyObject for Value`` either becomes
        // ``Value::String``. Rare numeric/bool returns also extract.
        let value = py_result
            .extract::<Value>()
            .map_err(|_| format!("Custom filter '{name}' returned a non-convertible value"))?;
        Ok((value, is_runtime_safe))
    });

    Some(result)
}

fn format_py_err(py: Python<'_>, name: &str, err: &PyErr) -> String {
    let traceback = err
        .traceback(py)
        .map(|tb| tb.format().unwrap_or_default())
        .unwrap_or_default();
    format!(
        "Custom filter '{}' raised exception: {}\n{}",
        name,
        err.value(py),
        traceback
    )
}

// Tests for the hot-path short-circuit guard (#1162) live in an isolated
// integration test at `tests/test_filter_registry_isolated.rs`. Cargo runs
// each integration-test file in its own process binary, which gives the
// `ANY_CUSTOM_FILTERS_REGISTERED` AtomicBool a guaranteed-clean starting
// state — unlike in-module unit tests where the static persists across
// every test in the same `cargo test` binary. See #1235 / v0.9.1 retro
// Action Tracker #201 for the rationale.
//
// Functional cross-checks (registration → render → custom filter produces
// output) live in the Python regression suite at
// `tests/unit/test_rust_custom_filters_1121.py`.
