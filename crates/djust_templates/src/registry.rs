//! Tag Handler Registry for Django-compatible template tags
//!
//! This module provides a registry for custom template tag handlers that can be
//! implemented in Python. This enables Django-specific tags like `{% url %}` and
//! `{% static %}` to be handled by Python callbacks while keeping built-in tags
//! (if, for, block) as fast native Rust implementations.
//!
//! # Architecture
//!
//! ```text
//! Template: {% url 'post' post.slug %}
//!     |-> Rust parser encounters "url" tag
//!     |-> Not in built-in match -> check Python registry
//!     |-> Found UrlTagHandler -> create Node::CustomTag
//!     |-> Rust renderer hits Node::CustomTag
//!     |-> Acquires GIL, calls Python handler with args + context
//!     |-> Handler calls Django's reverse()
//!     |-> Returns "/posts/my-slug/"
//! Final HTML with correct URL
//! ```
//!
//! # Performance
//!
//! - Built-in tags: Zero overhead (native Rust match)
//! - Custom tags: ~15-50µs per call (GIL acquisition + Python callback)

use once_cell::sync::Lazy;
use pyo3::prelude::*;
use std::collections::{HashMap, HashSet};
use std::sync::RwLock;

use djust_core::DjangoRustError;

/// A tag handler's return, escaped unless it is already HTML (#2379).
///
/// # The defect this closes
///
/// Django's `SimpleNode.render` runs `conditional_escape` over a
/// `simple_tag`'s return unless it carries `__html__`:
///
/// ```python
/// def render(self, context):
///     output = self.func(*resolved_args, **resolved_kwargs)
///     if context.autoescape:
///         output = conditional_escape(output)
///     return render_value_in_context(output, context)
/// ```
///
/// djust inserted the return into the page VERBATIM, so a handler as ordinary
/// as `return f"Hello {name}"` emitted `Hello <img src=x onerror=alert(1)>`
/// live where Django renders `Hello &lt;img …&gt;`. That is the fail-OPEN half
/// of the asymmetry #2290 found on the way IN, and it reached every
/// `register_tag_handler` / `register_block_tag_handler` user — djust's own and
/// any project's.
///
/// # Why `escape` and not `conditional_escape`
///
/// The `__html__` test is made HERE, on the Python object, and the escape is
/// applied only when it fails — which is what `conditional_escape` does, split
/// across the language boundary because `Value`'s `FromPyObject` discards the
/// marker. Doing it after the extract would be too late by construction: a
/// `SafeString` and a plain `str` are the same `String` by then, which is
/// exactly the information loss #2290 documents one registry over.
///
/// # The audit that goes with it
///
/// Escaping a return that legitimately IS markup is a rendering regression,
/// not a fix, so every handler djust registers was enumerated by intercepting
/// the three `register_*_tag_handler` functions and CALLED. Of 221, **195**
/// already carry `__html__`, 13 return the empty string and 5 return plain
/// text — and **6** returned markup as a plain `str` and now `mark_safe` it.
/// See `python/tests/test_custom_tag_return_escape_2379.py`, which re-runs
/// that enumeration so a handler added later without the marker fails a test
/// rather than rendering as escaped text.
fn escape_handler_return(
    result: &Bound<'_, PyAny>,
    what: &str,
    name: &str,
) -> Result<String, String> {
    let already_html = crate::filter_registry::py_value_is_safe_string(result);
    let text = result
        .extract::<String>()
        .map_err(|_| format!("{what} '{name}' render() must return a string"))?;
    if already_html {
        Ok(text)
    } else {
        Ok(crate::filters::html_escape(&text))
    }
}

/// `django.utils.safestring.mark_safe(text)`, or the bare string if Django is
/// not importable (#2379).
///
/// Fails SOFT on purpose: a pure-Rust embedding without Django installed
/// should keep rendering, and a handler that sees a bare `str` there sees
/// exactly what it saw before this change.
fn mark_safe_str<'py>(py: Python<'py>, text: &str) -> PyResult<Bound<'py, PyAny>> {
    let plain = pyo3::types::PyString::new(py, text);
    let Ok(module) = py.import("django.utils.safestring") else {
        return Ok(plain.into_any());
    };
    let Ok(mark_safe) = module.getattr("mark_safe") else {
        return Ok(plain.into_any());
    };
    mark_safe.call1((plain,))
}

/// One resolved tag argument, as the Python handler receives it (#2416).
///
/// # Why the bool travels beside the text
///
/// Django's `SimpleNode.render` resolves each operand with
/// `FilterExpression.resolve(context)` and hands the handler the resolved
/// **object** — so a `mark_safe`d context value arrives as a `SafeString` and
/// a handler's defensive `conditional_escape(value)` is a no-op. djust's tag
/// channel transports every argument as a `String`, which is the information
/// loss #2290 documents one registry over: the marker cannot survive
/// `Value` → `value_to_arg_string`, so the handler re-escapes markup Django
/// emits live.
///
/// The renderer knows the answer at resolution time (`get_value_safe` already
/// returns it, and it is the same bool that decides whether `{{ p }}` escapes).
/// Carrying it here lets [`build_py_args`] re-mint the `SafeString` on the
/// Python side, where `SafeData` actually means something.
///
/// This is deliberately NOT a general "make the argument a real Python object"
/// channel: an `int` still arrives as `"5"`, a list still arrives as JSON. Only
/// the `SafeData` bit crosses, because only that bit changes an escaping
/// decision.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct TagArg {
    /// The text the handler receives.
    pub text: String,
    /// Django would have handed this argument `SafeData`.
    pub safe: bool,
}

impl TagArg {
    /// An argument with no safety grant — the shape every argument had before
    /// #2416, and the one every unresolved / composite / non-string operand
    /// keeps.
    pub fn plain(text: String) -> Self {
        Self { text, safe: false }
    }

    /// An argument Django would have handed `SafeData`.
    pub fn marked(text: String) -> Self {
        Self { text, safe: true }
    }
}

/// The `list[str]` a handler's `render` receives, with the marked positions
/// re-minted as `SafeString` (#2416).
///
/// One builder for all three registries, so a future registry cannot acquire a
/// hand-copied twin that forgets the marker (#1646). `mark_safe_str` fails
/// SOFT — without Django importable the position degrades to the plain `str` it
/// was before this change rather than failing the render.
fn build_py_args<'py>(
    py: Python<'py>,
    args: &[TagArg],
) -> Result<Bound<'py, pyo3::types::PyList>, String> {
    let list = pyo3::types::PyList::empty(py);
    for arg in args {
        let item = if arg.safe {
            mark_safe_str(py, &arg.text)
                .map_err(|e| format!("Failed to mark an argument safe: {e}"))?
        } else {
            pyo3::types::PyString::new(py, &arg.text).into_any()
        };
        list.append(item)
            .map_err(|e| format!("Failed to create args list: {e}"))?;
    }
    Ok(list)
}

/// Global registry mapping tag names to Python handler objects.
///
/// Thread-safe via `RwLock`. Registration is one-time bootstrap; lookup is
/// read-only and happens on every render, so concurrent renders share the
/// read lock. Handlers must implement a `render(args, context)` method
/// that returns a string.
static TAG_HANDLERS: Lazy<RwLock<HashMap<String, TagHandlerEntry>>> =
    Lazy::new(|| RwLock::new(HashMap::new()));

/// A registered block-tag handler: its end tag, the handler, and the two
/// opt-in policies the inline registry already carried (#2547).
///
/// Was a bare `(end_tag, handler)` tuple until #2547, which is why the block
/// registry alone ignored `RESOLVE_ARG_POSITIONS` — the #1646 drift the
/// #2547 plan measured (`{% div id=name %}` handed Django's parser the
/// resolved VALUE of `name`). Same readers, same fields, same semantics as
/// [`TagHandlerEntry`] now.
struct BlockHandlerEntry {
    end_tag: String,
    handler: Py<PyAny>,
    resolve_positions: Option<HashSet<usize>>,
    returns_bindings: bool,
}

/// Global registry for block tag handlers (tags with children).
///
/// Maps opening tag name -> (end_tag_name, handler).
/// Handlers must implement a `render(args, content, context)` method:
/// - `args`: list of strings from the opening tag
/// - `content`: pre-rendered HTML string of the block body
/// - `context`: dict of template context variables
static BLOCK_TAG_HANDLERS: Lazy<RwLock<HashMap<String, BlockHandlerEntry>>> =
    Lazy::new(|| RwLock::new(HashMap::new()));

/// The handler's opt-in arg-resolution policy, read at registration time.
///
/// A missing attribute OR an explicit `None` means "resolve every arg" — the
/// historical default. A `set[int]` restricts resolution to those 0-based
/// positions and the renderer passes the rest as literal TOKENS (#2041).
///
/// ONE reader for both registries (#1646). It was spelled inline in
/// `register_assign_tag_handler`; extending the policy to the inline-tag
/// registry (#2423) would otherwise have made that two hand-copies of a rule
/// whose two halves — "absent" and "explicitly `None`" — are exactly the pair
/// a copy gets wrong.
fn read_resolve_positions(handler: &Bound<'_, PyAny>) -> PyResult<Option<HashSet<usize>>> {
    if !handler.hasattr("RESOLVE_ARG_POSITIONS")? {
        return Ok(None);
    }
    let attr = handler.getattr("RESOLVE_ARG_POSITIONS")?;
    if attr.is_none() {
        return Ok(None);
    }
    Ok(Some(attr.extract::<HashSet<usize>>()?))
}

/// The handler's opt-in "I return `(output, bindings)`" declaration (#2547).
///
/// A bridged Django library tag (`{% load app_tags %}`) is rendered by
/// Django's OWN node, and a Django node may do two things a djust handler's
/// bare-string contract cannot express: write the context (`{% one_param 37
/// as out %}`, every `get_* … as x` tag) and raise a Python exception that
/// Django's callers dispatch on by TYPE (`TemplateSyntaxError` from
/// `parse_bits`, a library's own `RuntimeError`). A handler that sets
/// `RETURNS_BINDINGS = True` gets both: its `render` returns a 2-tuple
/// `(output, {name: value})` and its exceptions cross as
/// `DjangoRustError::PythonException` instead of being flattened to a string.
///
/// Absent or falsy = the historical contract, untouched for every existing
/// handler. ONE reader for both registries, like [`read_resolve_positions`].
fn read_returns_bindings(handler: &Bound<'_, PyAny>) -> PyResult<bool> {
    if !handler.hasattr("RETURNS_BINDINGS")? {
        return Ok(false);
    }
    handler.getattr("RETURNS_BINDINGS")?.is_truthy()
}

/// A registered inline-tag handler plus its arg-resolution policy (#2423).
///
/// Same shape as [`AssignHandlerEntry`], and for the same reason one registry
/// over: a handler that must resolve its own operand needs the LITERAL token,
/// not the engine's pre-resolved flattening. `render_slot` is the first
/// inline tag to want it — `{% render_slot slots.col.0.content %}` and a
/// hostile `{% render_slot p %}` are the same opaque string once the engine
/// has resolved them, and only the un-resolved path can tell them apart.
struct TagHandlerEntry {
    handler: Py<PyAny>,
    resolve_positions: Option<HashSet<usize>>,
    /// See [`read_returns_bindings`] (#2547).
    returns_bindings: bool,
    /// `Some(message)` when the parser must REFUSE this tag with Django's
    /// `TemplateSyntaxError` the moment a template uses it (#2547): a raw
    /// `@register.tag` that consumes a body cannot be bridged, and the
    /// refusal is per TAG so the rest of its library still works. Read off
    /// the handler's `REFUSE_AT_PARSE` attribute at registration.
    parse_refusal: Option<String>,
}

/// The handler's opt-in parse-time refusal message (#2547); `None` when the
/// attribute is absent or `None`.
fn read_parse_refusal(handler: &Bound<'_, PyAny>) -> PyResult<Option<String>> {
    if !handler.hasattr("REFUSE_AT_PARSE")? {
        return Ok(None);
    }
    let attr = handler.getattr("REFUSE_AT_PARSE")?;
    if attr.is_none() {
        return Ok(None);
    }
    Ok(Some(attr.extract::<String>()?))
}

/// A registered assign-tag handler plus its arg-resolution policy.
///
/// `resolve_positions` records which arg positions the renderer should
/// resolve against the render context before invoking the handler
/// (#2041). `None` = resolve *every* arg (the historical default, kept
/// for any handler that does not opt in). `Some(set)` = resolve only the
/// listed 0-based positions and pass the rest as literal tokens — this is
/// how `{% regroup %}` keeps its `by`/`<attr>`/`as`/`<var>` keyword and
/// name operands UNRESOLVED (Django parity), so a context key named like
/// the `<attr>` token can no longer shadow the per-item lookup. Sourced
/// from the handler's optional `RESOLVE_ARG_POSITIONS` Python attribute at
/// registration time.
struct AssignHandlerEntry {
    handler: Py<PyAny>,
    resolve_positions: Option<HashSet<usize>>,
}

/// Global registry for assign tag handlers (context-mutating tags).
///
/// Handlers implement `render(args, context) -> dict[str, Any]`. The
/// returned dict is merged into the template context for siblings
/// following the tag in the same render iteration.
static ASSIGN_TAG_HANDLERS: Lazy<RwLock<HashMap<String, AssignHandlerEntry>>> =
    Lazy::new(|| RwLock::new(HashMap::new()));

/// Register a Python tag handler for a custom template tag.
///
/// The handler must be a Python object with a `render(self, args, context)` method:
/// - `args`: List of string arguments from the template tag
/// - `context`: Dictionary of template context variables
/// - Returns: String to insert in the rendered output
///
/// # Arguments
///
/// * `name` - Tag name (e.g., "url", "static")
/// * `handler` - Python handler object with `render` method
///
/// # Example
///
/// ```python
/// from djust._rust import register_tag_handler
///
/// class UrlTagHandler:
///     def render(self, args, context):
///         url_name = args[0].strip("'\"")
///         return reverse(url_name)
///
/// register_tag_handler("url", UrlTagHandler())
/// ```
#[pyfunction]
pub fn register_tag_handler(py: Python<'_>, name: String, handler: Py<PyAny>) -> PyResult<()> {
    // Verify handler has render method
    let handler_ref = handler.bind(py);
    if !handler_ref.hasattr("render")? {
        return Err(PyErr::new::<pyo3::exceptions::PyTypeError, _>(
            "Handler must have a 'render' method",
        ));
    }

    // The opt-in arg-resolution policy, same reader as the assign registry
    // (#2041, extended to inline tags in #2423).
    let resolve_positions = read_resolve_positions(handler_ref)?;
    let returns_bindings = read_returns_bindings(handler_ref)?;
    let parse_refusal = read_parse_refusal(handler_ref)?;

    let mut registry = TAG_HANDLERS.write().map_err(|e| {
        PyErr::new::<pyo3::exceptions::PyRuntimeError, _>(format!("Registry lock error: {e}"))
    })?;

    registry.insert(
        name,
        TagHandlerEntry {
            handler,
            resolve_positions,
            returns_bindings,
            parse_refusal,
        },
    );
    Ok(())
}

/// Unregister a tag handler.
///
/// Returns true if a handler was removed, false if no handler existed for the name.
#[pyfunction]
pub fn unregister_tag_handler(name: &str) -> PyResult<bool> {
    let mut registry = TAG_HANDLERS.write().map_err(|e| {
        PyErr::new::<pyo3::exceptions::PyRuntimeError, _>(format!("Registry lock error: {e}"))
    })?;

    Ok(registry.remove(name).is_some())
}

/// Check if a handler is registered for a tag name.
#[pyfunction]
pub fn has_tag_handler(name: &str) -> PyResult<bool> {
    let registry = TAG_HANDLERS.read().map_err(|e| {
        PyErr::new::<pyo3::exceptions::PyRuntimeError, _>(format!("Registry lock error: {e}"))
    })?;

    Ok(registry.contains_key(name))
}

/// Get a list of all registered tag names.
#[pyfunction]
pub fn get_registered_tags() -> PyResult<Vec<String>> {
    let registry = TAG_HANDLERS.read().map_err(|e| {
        PyErr::new::<pyo3::exceptions::PyRuntimeError, _>(format!("Registry lock error: {e}"))
    })?;

    Ok(registry.keys().cloned().collect())
}

/// Clear all registered handlers (primarily for testing).
#[pyfunction]
pub fn clear_tag_handlers() -> PyResult<()> {
    let mut registry = TAG_HANDLERS.write().map_err(|e| {
        PyErr::new::<pyo3::exceptions::PyRuntimeError, _>(format!("Registry lock error: {e}"))
    })?;

    registry.clear();
    Ok(())
}

// ============================================================================
// Block Tag Handler API (Python-callable)
// ============================================================================

/// Register a block tag handler for a custom template block tag.
///
/// Block tags wrap content like `{% modal %}...{% endmodal %}`.
/// The handler receives the pre-rendered HTML of the block body as `content`.
///
/// The handler must be a Python object with a `render(self, args, content, context)` method:
/// - `args`: List of string arguments from the opening tag
/// - `content`: Pre-rendered HTML string of the block body
/// - `context`: Dictionary of template context variables
/// - Returns: String to insert in the rendered output
///
/// # Arguments
///
/// * `name` - Opening tag name (e.g., "modal", "card")
/// * `end_tag` - Closing tag name (e.g., "endmodal", "endcard")
/// * `handler` - Python handler object with `render` method
///
/// # Known constraints
///
/// * **No parent-tag propagation** (issue #804). When a block tag's
///   children include another block tag, the inner tag's output is
///   rendered to a string and embedded in `content`. The inner
///   handler is not informed that it is nested inside a parent
///   handler. Handlers that need nesting awareness should stash a
///   hint on the template context in the outer handler and read it
///   back in the inner handler — automatic propagation is not yet
///   implemented.
///
/// * **No loader access from handlers** (issue #803). The
///   `FilesystemTemplateLoader` is not currently exposed through the
///   Rust-to-Python bridge, so block handlers cannot call
///   `{% render_template name=... %}`-style template loads. Workaround:
///   pre-render child templates in the view and pass the result via
///   context.
#[pyfunction]
pub fn register_block_tag_handler(
    py: Python<'_>,
    name: String,
    end_tag: String,
    handler: Py<PyAny>,
) -> PyResult<()> {
    let handler_ref = handler.bind(py);
    if !handler_ref.hasattr("render")? {
        return Err(PyErr::new::<pyo3::exceptions::PyTypeError, _>(
            "Block tag handler must have a 'render' method",
        ));
    }

    // The SAME two readers the inline registry uses (#2547): the block
    // registry ignoring `RESOLVE_ARG_POSITIONS` was the #1646 drift the
    // #2547 plan measured.
    let resolve_positions = read_resolve_positions(handler_ref)?;
    let returns_bindings = read_returns_bindings(handler_ref)?;

    let mut registry = BLOCK_TAG_HANDLERS.write().map_err(|e| {
        PyErr::new::<pyo3::exceptions::PyRuntimeError, _>(format!("Registry lock error: {e}"))
    })?;

    registry.insert(
        name,
        BlockHandlerEntry {
            end_tag,
            handler,
            resolve_positions,
            returns_bindings,
        },
    );
    Ok(())
}

/// Unregister a block tag handler.
#[pyfunction]
pub fn unregister_block_tag_handler(name: &str) -> PyResult<bool> {
    let mut registry = BLOCK_TAG_HANDLERS.write().map_err(|e| {
        PyErr::new::<pyo3::exceptions::PyRuntimeError, _>(format!("Registry lock error: {e}"))
    })?;

    Ok(registry.remove(name).is_some())
}

/// Check if a block tag handler is registered.
#[pyfunction]
pub fn has_block_tag_handler(name: &str) -> PyResult<bool> {
    let registry = BLOCK_TAG_HANDLERS.read().map_err(|e| {
        PyErr::new::<pyo3::exceptions::PyRuntimeError, _>(format!("Registry lock error: {e}"))
    })?;

    Ok(registry.contains_key(name))
}

/// Clear all block tag handlers (primarily for testing).
#[pyfunction]
pub fn clear_block_tag_handlers() -> PyResult<()> {
    let mut registry = BLOCK_TAG_HANDLERS.write().map_err(|e| {
        PyErr::new::<pyo3::exceptions::PyRuntimeError, _>(format!("Registry lock error: {e}"))
    })?;

    registry.clear();
    Ok(())
}

// ============================================================================
// Internal Rust API (for use by parser and renderer)
// ============================================================================

/// Check if a block tag handler exists and return the end tag name (internal Rust API).
///
/// Returns `Some(end_tag_name)` if a block handler is registered, `None` otherwise.
pub fn block_handler_exists(name: &str) -> Option<String> {
    BLOCK_TAG_HANDLERS
        .read()
        .map(|registry| registry.get(name).map(|entry| entry.end_tag.clone()))
        .unwrap_or(None)
}

/// Call a registered Python block handler with args, content, and context.
///
/// The handler's `render(args, content, context)` method is called and the
/// returned string is inserted into the rendered output.
///
/// Back-compat shim around [`call_block_handler_with_py_sidecar`] —
/// equivalent to passing `None` for the raw Python sidecar.
pub fn call_block_handler(
    name: &str,
    args: &[TagArg],
    content: &str,
    context: &HashMap<String, djust_core::Value>,
) -> Result<String, String> {
    call_block_handler_with_py_sidecar(name, args, content, context, None)
}

/// Variant of [`call_block_handler`] that additionally injects raw
/// Python objects from the [`Context::raw_py_objects`] sidecar into
/// the handler's ``context`` dict.
///
/// Mirrors [`call_handler_with_py_sidecar`] (extended in PR #1166)
/// for `Node::CustomTag`. Block handlers (``modal``, ``card`` …)
/// that need access to Python-only objects in the parent's render
/// context (notably ``request`` / ``view``) can read those keys from
/// the dict directly. Sidecar values overwrite same-named JSON keys
/// so a Python model instance wins over a normalized dict snapshot.
///
/// Existing block handlers that ignore the extra keys are unaffected.
pub fn call_block_handler_with_py_sidecar(
    name: &str,
    args: &[TagArg],
    content: &str,
    context: &HashMap<String, djust_core::Value>,
    raw_py_objects: Option<&HashMap<String, pyo3::Py<PyAny>>>,
) -> Result<String, String> {
    let handler = {
        let registry = BLOCK_TAG_HANDLERS
            .read()
            .map_err(|e| format!("Registry lock error: {e}"))?;

        let entry = registry
            .get(name)
            .ok_or_else(|| format!("No block handler registered for tag: {name}"))?;

        Python::attach(|py| entry.handler.clone_ref(py))
    };

    Python::attach(|py| {
        let py_args = build_py_args(py, args)?;

        // The block body reaches Python as a `SafeString`, not a bare `str`
        // (#2379). Django's `simple_block_tag` hands the handler
        // `nodelist.render(context)` — already-rendered, already-escaped
        // markup, and therefore `SafeData` — so a handler that returns its
        // content unchanged keeps the marker and the body is emitted once.
        //
        // Load-bearing since the return started being escaped: without it,
        // `{% cb_ident %}{{ p }}{% endcb_ident %}` over a hostile value
        // emitted `&amp;lt;img …&amp;gt;` where Django emits `&lt;img …&gt;`
        // — the body escaped TWICE, once by the `{{ p }}` inside it and again
        // by the bridge. Over-escaping rather than a leak, but a visibly wrong
        // page, and it is the block-path twin of the marker loss #2290 found
        // on the filter-argument side.
        //
        // Fails SOFT: a missing `django.utils.safestring` (Django absent, as
        // in a pure-Rust embedding) falls back to the bare string rather than
        // failing the render — the handler then sees what it saw before.
        let py_content =
            mark_safe_str(py, content).map_err(|e| format!("Failed to convert content: {e}"))?;

        // Context dict with the raw-Python sidecar (``request``, ``view``)
        // on top, through the one builder every registry shares.
        let py_context = build_py_context(py, context, raw_py_objects)?;

        let handler_ref = handler.bind(py);
        let result = handler_ref
            .call_method1("render", (py_args, py_content, py_context))
            .map_err(|e| {
                let traceback = e
                    .traceback(py)
                    .map(|tb| tb.format().unwrap_or_default())
                    .unwrap_or_default();
                format!(
                    "Block handler '{}' raised exception: {}\n{}",
                    name,
                    e.value(py),
                    traceback
                )
            })?;

        escape_handler_return(&result, "Block handler", name)
    })
}

/// Check if a handler exists for the given tag name (internal Rust API).
///
/// This is used by the parser to decide whether to create a CustomTag node.
pub fn handler_exists(name: &str) -> bool {
    TAG_HANDLERS
        .read()
        .map(|registry| registry.contains_key(name))
        .unwrap_or(false)
}

/// Call a registered Python handler with args and context (internal Rust API).
///
/// This is used by the renderer to execute custom tag handlers.
///
/// # Arguments
///
/// * `name` - Tag name
/// * `args` - Arguments from the template tag as strings
/// * `context` - Template context as a HashMap (will be converted to Python dict)
///
/// # Returns
///
/// The rendered string from the handler, or an error if:
/// - No handler is registered for the tag
/// - Handler doesn't have a `render` method
/// - Handler's `render` method raises an exception
/// - Handler's `render` method doesn't return a string
pub fn call_handler(
    name: &str,
    args: &[TagArg],
    context: &HashMap<String, djust_core::Value>,
) -> Result<String, String> {
    call_handler_with_py_sidecar(name, args, context, None)
}

/// Variant of [`call_handler`] that additionally injects raw Python
/// objects from the [`Context::raw_py_objects`] sidecar into the
/// handler's ``context`` dict.
///
/// Existing tag handlers (``url``, ``static``, ``dj_flash`` …) only
/// look at JSON-friendly context keys, so the additional Python
/// objects are inert noise to them. Handlers that *do* need access to
/// Python-only context (e.g. ``live_render``, which needs the parent
/// ``view`` and the ``request`` object to delegate to the Django
/// template tag) read those keys from the dict directly.
///
/// Sidecar values overwrite same-named JSON keys so that a Python
/// model instance wins over a normalized dict snapshot — the Python
/// handler nearly always wants the live object, not the projection.
pub fn call_handler_with_py_sidecar(
    name: &str,
    args: &[TagArg],
    context: &HashMap<String, djust_core::Value>,
    raw_py_objects: Option<&HashMap<String, pyo3::Py<PyAny>>>,
) -> Result<String, String> {
    // Get handler from registry
    let handler = {
        let registry = TAG_HANDLERS
            .read()
            .map_err(|e| format!("Registry lock error: {e}"))?;

        // Clone the Py<PyAny> using Python::attach
        let entry = registry
            .get(name)
            .ok_or_else(|| format!("No handler registered for tag: {name}"))?;

        Python::attach(|py| entry.handler.clone_ref(py))
    };

    // Acquire GIL and call Python handler
    Python::attach(|py| {
        // Convert args to Python list
        let py_args = build_py_args(py, args)?;

        // Convert context to Python dict, raw-Python sidecar (``request``,
        // ``view`` — notably the ``live_render`` lazy=True path) on top.
        let py_context = build_py_context(py, context, raw_py_objects)?;

        // Call handler.render(args, context)
        let handler_ref = handler.bind(py);
        let result = handler_ref
            .call_method1("render", (py_args, py_context))
            .map_err(|e| {
                // Extract Python exception details
                let traceback = e
                    .traceback(py)
                    .map(|tb| tb.format().unwrap_or_default())
                    .unwrap_or_default();
                format!(
                    "Handler '{}' raised exception: {}\n{}",
                    name,
                    e.value(py),
                    traceback
                )
            })?;

        escape_handler_return(&result, "Handler", name)
    })
}

// ============================================================================
// Assign Tag Handler API (context-mutating tags)
// ============================================================================

/// Register a Python assign-tag handler for a custom template tag.
///
/// Assign tags mutate the template context rather than emitting HTML.
/// Example: `{% assign_slot user_card %}` — the handler returns a
/// dict whose keys become context variables visible to subsequent
/// sibling nodes in the template.
///
/// The handler must be a Python object with a `render(args, context)`
/// method that returns a `dict[str, Any]`. Non-dict return values
/// are treated as an empty dict (no-op) and logged by the caller.
///
/// An optional `RESOLVE_ARG_POSITIONS` attribute on the handler (a
/// `set[int]`, or `None`) declares which arg positions the renderer
/// should resolve against the context; the rest are passed as literal
/// tokens (#2041). Absent / `None` = resolve every arg (historical
/// default).
///
/// # Arguments
///
/// * `name` - Tag name (e.g., "assign_slot")
/// * `handler` - Python handler object with `render` method
#[pyfunction]
pub fn register_assign_tag_handler(
    py: Python<'_>,
    name: String,
    handler: Py<PyAny>,
) -> PyResult<()> {
    let handler_ref = handler.bind(py);
    if !handler_ref.hasattr("render")? {
        return Err(PyErr::new::<pyo3::exceptions::PyTypeError, _>(
            "Assign tag handler must have a 'render' method",
        ));
    }

    // The opt-in arg-resolution policy (#2041), through the ONE reader both
    // registries share (#2423).
    let resolve_positions = read_resolve_positions(handler_ref)?;

    let mut registry = ASSIGN_TAG_HANDLERS.write().map_err(|e| {
        PyErr::new::<pyo3::exceptions::PyRuntimeError, _>(format!("Registry lock error: {e}"))
    })?;

    registry.insert(
        name,
        AssignHandlerEntry {
            handler,
            resolve_positions,
        },
    );
    Ok(())
}

/// Unregister an assign tag handler.
#[pyfunction]
pub fn unregister_assign_tag_handler(name: &str) -> PyResult<bool> {
    let mut registry = ASSIGN_TAG_HANDLERS.write().map_err(|e| {
        PyErr::new::<pyo3::exceptions::PyRuntimeError, _>(format!("Registry lock error: {e}"))
    })?;
    Ok(registry.remove(name).is_some())
}

/// Check if an assign tag handler is registered.
#[pyfunction]
pub fn has_assign_tag_handler(name: &str) -> PyResult<bool> {
    let registry = ASSIGN_TAG_HANDLERS.read().map_err(|e| {
        PyErr::new::<pyo3::exceptions::PyRuntimeError, _>(format!("Registry lock error: {e}"))
    })?;
    Ok(registry.contains_key(name))
}

/// Clear all registered assign tag handlers (primarily for testing).
#[pyfunction]
pub fn clear_assign_tag_handlers() -> PyResult<()> {
    let mut registry = ASSIGN_TAG_HANDLERS.write().map_err(|e| {
        PyErr::new::<pyo3::exceptions::PyRuntimeError, _>(format!("Registry lock error: {e}"))
    })?;
    registry.clear();
    Ok(())
}

/// Internal Rust API — does an assign tag handler exist for this name?
pub fn assign_handler_exists(name: &str) -> bool {
    ASSIGN_TAG_HANDLERS
        .read()
        .map(|registry| registry.contains_key(name))
        .unwrap_or(false)
}

/// Internal Rust API — the arg positions the renderer should resolve for this
/// INLINE tag (#2423).
///
/// The inline-tag twin of [`assign_handler_resolve_positions`], with the same
/// contract: `Some(set)` only when the handler declared a policy, `None`
/// otherwise (and for an unregistered name), in which case the renderer
/// resolves every arg — the historical default that every handler but
/// `render_slot` still takes.
pub fn tag_handler_resolve_positions(name: &str) -> Option<HashSet<usize>> {
    TAG_HANDLERS.read().ok().and_then(|registry| {
        registry
            .get(name)
            .and_then(|entry| entry.resolve_positions.clone())
    })
}

/// Internal Rust API — the arg positions the renderer should resolve for this
/// BLOCK tag (#2547).
///
/// The block-registry twin of [`tag_handler_resolve_positions`], same
/// contract. Every block handler djust ships (`call`, `component`, `slot`, …)
/// declares nothing → `None` → resolve every arg, bytes unchanged.
pub fn block_handler_resolve_positions(name: &str) -> Option<HashSet<usize>> {
    BLOCK_TAG_HANDLERS.read().ok().and_then(|registry| {
        registry
            .get(name)
            .and_then(|entry| entry.resolve_positions.clone())
    })
}

/// Internal Rust API — the parse-time refusal message the inline handler for
/// `name` declared (#2547), `None` for a bridgeable or unregistered tag.
pub fn tag_handler_parse_refusal(name: &str) -> Option<String> {
    TAG_HANDLERS.read().ok().and_then(|registry| {
        registry
            .get(name)
            .and_then(|entry| entry.parse_refusal.clone())
    })
}

/// Django's own `TemplateSyntaxError(message)`, stamped as library-raised so
/// `DjustTemplate.render` passes it through WHOLE (#2547). Falls back to a
/// `TemplateError` string when Django is not importable (pure-Rust use).
pub fn library_syntax_error(message: &str) -> DjangoRustError {
    Python::attach(|py| {
        let Ok(module) = py.import("django.template") else {
            return DjangoRustError::TemplateError(message.to_string());
        };
        let Ok(cls) = module.getattr("TemplateSyntaxError") else {
            return DjangoRustError::TemplateError(message.to_string());
        };
        let Ok(exc) = cls.call1((message,)) else {
            return DjangoRustError::TemplateError(message.to_string());
        };
        let _ = exc.setattr("_djust_raised_by_library", true);
        DjangoRustError::PythonException(PyErr::from_value(exc))
    })
}

/// Internal Rust API — did the inline handler for `name` declare
/// `RETURNS_BINDINGS` (#2547)? `false` for an unregistered name.
pub fn tag_handler_returns_bindings(name: &str) -> bool {
    TAG_HANDLERS
        .read()
        .ok()
        .and_then(|registry| registry.get(name).map(|entry| entry.returns_bindings))
        .unwrap_or(false)
}

/// Internal Rust API — did the block handler for `name` declare
/// `RETURNS_BINDINGS` (#2547)? `false` for an unregistered name.
pub fn block_handler_returns_bindings(name: &str) -> bool {
    BLOCK_TAG_HANDLERS
        .read()
        .ok()
        .and_then(|registry| registry.get(name).map(|entry| entry.returns_bindings))
        .unwrap_or(false)
}

// ============================================================================
// Bindings-returning handlers and the `{% load %}` library loader (#2547)
// ============================================================================

/// One context write a bindings-returning handler made (#2547).
///
/// `safe` is the `SafeData` bit of the Python object the handler bound —
/// Django stores a `simple_tag`'s `as var` result RAW (`context[target_var] =
/// output`, no `conditional_escape`), so a `format_html` return is a
/// `SafeString` and `{{ var }}` must not re-escape it, while a plain-`str`
/// return is escaped by `{{ var }}` exactly as Django does.
#[derive(Debug)]
pub struct HandlerBinding {
    pub name: String,
    pub value: djust_core::Value,
    pub safe: bool,
}

/// The `context` dict a handler receives: every JSON-friendly value, then the
/// raw-Python sidecar on top (#1167) so a live object wins over its snapshot.
///
/// ONE builder for every registry (#1646) — this was spelled inline, three
/// times, before #2547 added a fourth and fifth caller.
fn build_py_context<'py>(
    py: Python<'py>,
    context: &HashMap<String, djust_core::Value>,
    raw_py_objects: Option<&HashMap<String, pyo3::Py<PyAny>>>,
) -> Result<Bound<'py, pyo3::types::PyDict>, String> {
    use pyo3::IntoPyObject;

    let py_context = pyo3::types::PyDict::new(py);
    for (key, value) in context {
        let py_value = value
            .clone()
            .into_pyobject(py)
            .map_err(|e| format!("Failed to convert value for key '{key}': {e}"))?;
        py_context
            .set_item(key, py_value)
            .map_err(|e| format!("Failed to set context key '{key}': {e}"))?;
    }
    if let Some(raw) = raw_py_objects {
        for (key, obj) in raw {
            py_context
                .set_item(key, obj.bind(py))
                .map_err(|e| format!("Failed to set raw context key '{key}': {e}"))?;
        }
    }
    Ok(py_context)
}

/// Re-mint the `SafeData` bit on the context values the renderer had marked
/// safe (#2547).
///
/// A bridged library tag lets Django's OWN node resolve its operands against
/// the context dict, so the safety `{{ p }}` would have honoured has to
/// travel on the dict's values: `{% echo_arg p %}` over a `mark_safe`d `p`
/// renders raw on Django (`conditional_escape` sees a `SafeString`) and must
/// here. Each marked path is walked through dicts and lists; a `str` at the
/// end is replaced by `mark_safe(str)`. Only the STRING at the end of a
/// marked path is re-minted — the grant is the renderer's own, keyed by the
/// same name it would use for `{{ p }}`, so nothing data-derived acquires a
/// grant it did not already have. Fails SOFT without Django importable.
fn remint_safe_context(
    py: Python<'_>,
    dict: &Bound<'_, pyo3::types::PyDict>,
    safe_paths: &[String],
) -> Result<(), String> {
    for path in safe_paths {
        let mut segments = path.split('.').peekable();
        let Some(first) = segments.next() else {
            continue;
        };
        let Ok(Some(mut current)) = dict.get_item(first) else {
            continue;
        };
        let mut parent: Bound<'_, PyAny> = dict.clone().into_any();
        let mut key: String = first.to_string();
        let mut reachable = true;
        for segment in segments {
            let next = if let Ok(d) = current.cast::<pyo3::types::PyDict>() {
                d.get_item(segment).ok().flatten()
            } else if let Ok(l) = current.cast::<pyo3::types::PyList>() {
                segment
                    .parse::<usize>()
                    .ok()
                    .and_then(|i| l.get_item(i).ok())
            } else {
                None
            };
            match next {
                Some(value) => {
                    parent = current;
                    key = segment.to_string();
                    current = value;
                }
                None => {
                    reachable = false;
                    break;
                }
            }
        }
        if !reachable {
            continue;
        }
        let Ok(text) = current.cast::<pyo3::types::PyString>() else {
            continue;
        };
        let marked = mark_safe_str(py, &text.to_string_lossy())
            .map_err(|e| format!("Failed to mark context value '{path}' safe: {e}"))?;
        if let Ok(d) = parent.cast::<pyo3::types::PyDict>() {
            d.set_item(&key, marked)
                .map_err(|e| format!("Failed to set context key '{path}': {e}"))?;
        } else if let Ok(l) = parent.cast::<pyo3::types::PyList>() {
            if let Ok(i) = key.parse::<usize>() {
                l.set_item(i, marked)
                    .map_err(|e| format!("Failed to set context item '{path}': {e}"))?;
            }
        }
    }
    Ok(())
}

/// Split a bindings-returning handler's `(output, {name: value})` result.
///
/// The output goes through the SAME `escape_handler_return` as every other
/// handler (#2379) — a bridged Django node hands back `mark_safe`d output
/// because Django never re-escapes a node's return, and the escape is a
/// no-op on it; a handler that returns a plain `str` is escaped like any
/// other. The bindings are snapshotted into an owned `Vec` before conversion
/// (#2510) and carry their `SafeData` bit.
fn split_bindings_result(
    result: &Bound<'_, PyAny>,
    what: &str,
    name: &str,
) -> Result<(String, Vec<HandlerBinding>), String> {
    let tuple = result.cast::<pyo3::types::PyTuple>().map_err(|_| {
        format!("{what} '{name}' declared RETURNS_BINDINGS but did not return a (str, dict) tuple")
    })?;
    if tuple.len() != 2 {
        return Err(format!(
            "{what} '{name}' declared RETURNS_BINDINGS but returned a {}-tuple, not (str, dict)",
            tuple.len()
        ));
    }
    let output = tuple
        .get_item(0)
        .map_err(|e| format!("{what} '{name}': {e}"))?;
    let html = escape_handler_return(&output, what, name)?;
    let dict_obj = tuple
        .get_item(1)
        .map_err(|e| format!("{what} '{name}': {e}"))?;
    let mut bindings = Vec::new();
    if !dict_obj.is_none() {
        let dict = dict_obj
            .cast::<pyo3::types::PyDict>()
            .map_err(|_| format!("{what} '{name}' bindings must be a dict"))?;
        let pairs: Vec<(Bound<'_, PyAny>, Bound<'_, PyAny>)> = dict.iter().collect();
        for (key, value) in pairs {
            let key_str: String = key
                .extract()
                .map_err(|e| format!("{what} '{name}' bound a non-string name: {e}"))?;
            let safe = crate::filter_registry::py_value_is_safe_string(&value);
            let converted = value.extract::<djust_core::Value>().map_err(|e| {
                format!("{what} '{name}' bound '{key_str}' to an unconvertible value: {e}")
            })?;
            bindings.push(HandlerBinding {
                name: key_str,
                value: converted,
                safe,
            });
        }
    }
    Ok((html, bindings))
}

/// Call an inline handler that declared `RETURNS_BINDINGS` (#2547).
///
/// Differs from [`call_handler_with_py_sidecar`] in exactly the two ways
/// [`read_returns_bindings`] documents: the return is `(output, bindings)`,
/// and a Python exception crosses WHOLE as
/// `DjangoRustError::PythonException` — Django's `TemplateSyntaxError` from
/// `parse_bits`, a library's own `RuntimeError` — so the caller can dispatch
/// on its type as Django's callers do.
pub fn call_handler_with_bindings(
    name: &str,
    args: &[TagArg],
    context: &HashMap<String, djust_core::Value>,
    raw_py_objects: Option<&HashMap<String, pyo3::Py<PyAny>>>,
    safe_paths: &[String],
) -> Result<(String, Vec<HandlerBinding>), DjangoRustError> {
    let handler = {
        let registry = TAG_HANDLERS
            .read()
            .map_err(|e| DjangoRustError::TemplateError(format!("Registry lock error: {e}")))?;
        let entry = registry.get(name).ok_or_else(|| {
            DjangoRustError::TemplateError(format!("No handler registered for tag: {name}"))
        })?;
        Python::attach(|py| entry.handler.clone_ref(py))
    };
    Python::attach(|py| {
        let py_args = build_py_args(py, args).map_err(DjangoRustError::TemplateError)?;
        let py_context = build_py_context(py, context, raw_py_objects)
            .map_err(DjangoRustError::TemplateError)?;
        remint_safe_context(py, &py_context, safe_paths).map_err(DjangoRustError::TemplateError)?;
        let result = handler
            .bind(py)
            .call_method1("render", (py_args, py_context))
            .map_err(DjangoRustError::PythonException)?;
        split_bindings_result(&result, "Handler", name).map_err(DjangoRustError::TemplateError)
    })
}

/// Call a block handler that declared `RETURNS_BINDINGS` (#2547).
///
/// The block twin of [`call_handler_with_bindings`]; the body crosses as a
/// `SafeString` exactly as in [`call_block_handler_with_py_sidecar`] (#2379).
pub fn call_block_handler_with_bindings(
    name: &str,
    args: &[TagArg],
    content: &str,
    context: &HashMap<String, djust_core::Value>,
    raw_py_objects: Option<&HashMap<String, pyo3::Py<PyAny>>>,
    safe_paths: &[String],
) -> Result<(String, Vec<HandlerBinding>), DjangoRustError> {
    let handler = {
        let registry = BLOCK_TAG_HANDLERS
            .read()
            .map_err(|e| DjangoRustError::TemplateError(format!("Registry lock error: {e}")))?;
        let entry = registry.get(name).ok_or_else(|| {
            DjangoRustError::TemplateError(format!("No block handler registered for tag: {name}"))
        })?;
        Python::attach(|py| entry.handler.clone_ref(py))
    };
    Python::attach(|py| {
        let py_args = build_py_args(py, args).map_err(DjangoRustError::TemplateError)?;
        let py_content = mark_safe_str(py, content).map_err(|e| {
            DjangoRustError::TemplateError(format!("Failed to convert content: {e}"))
        })?;
        let py_context = build_py_context(py, context, raw_py_objects)
            .map_err(DjangoRustError::TemplateError)?;
        remint_safe_context(py, &py_context, safe_paths).map_err(DjangoRustError::TemplateError)?;
        let result = handler
            .bind(py)
            .call_method1("render", (py_args, py_content, py_context))
            .map_err(DjangoRustError::PythonException)?;
        split_bindings_result(&result, "Block handler", name)
            .map_err(DjangoRustError::TemplateError)
    })
}

/// The `{% load %}` hook: a Python callable the parser invokes with the tag's
/// arguments (#2547).
///
/// `None` (the pure-Rust default, and what every parser test sees) keeps
/// `{% load %}` a no-op that records its names for inheritance re-emit. When
/// djust's Python side installs a loader, the parser calls it at THE sink —
/// every `{% load %}` in every parse, primary or `{% include %}`d or inside a
/// `{% block %}` — and the loader imports the Django library and registers
/// its tags and filters before the parser reaches them.
static LIBRARY_LOADER: Lazy<RwLock<Option<Py<PyAny>>>> = Lazy::new(|| RwLock::new(None));

/// Install the `{% load %}` library loader (#2547).
///
/// `callable(args: list[str]) -> None` receives the tag's arguments exactly as
/// written — `["static"]`, `["a", "b"]`, `["echo", "from", "testtags"]` — and
/// raises Django's own `TemplateSyntaxError` for an unknown library, which
/// crosses the parse WHOLE.
#[pyfunction]
pub fn register_library_loader(callable: Py<PyAny>) -> PyResult<()> {
    let mut slot = LIBRARY_LOADER.write().map_err(|e| {
        PyErr::new::<pyo3::exceptions::PyRuntimeError, _>(format!("Registry lock error: {e}"))
    })?;
    *slot = Some(callable);
    Ok(())
}

/// Remove the `{% load %}` library loader; `{% load %}` is a no-op again.
#[pyfunction]
pub fn clear_library_loader() -> PyResult<()> {
    let mut slot = LIBRARY_LOADER.write().map_err(|e| {
        PyErr::new::<pyo3::exceptions::PyRuntimeError, _>(format!("Registry lock error: {e}"))
    })?;
    *slot = None;
    Ok(())
}

/// Is a `{% load %}` library loader installed?
#[pyfunction]
pub fn has_library_loader() -> PyResult<bool> {
    let slot = LIBRARY_LOADER.read().map_err(|e| {
        PyErr::new::<pyo3::exceptions::PyRuntimeError, _>(format!("Registry lock error: {e}"))
    })?;
    Ok(slot.is_some())
}

/// Internal Rust API — the parser's `{% load %}` arm calls this with the tag's
/// arguments (#2547).
///
/// `Ok(())` when no loader is installed. A Python exception from the loader
/// (Django's `TemplateSyntaxError` for an unknown library, the loud refusal
/// of a raw block-consuming tag) crosses WHOLE as
/// `DjangoRustError::PythonException`. No registry lock is held while the
/// loader runs, so its own `register_*` write-locks cannot deadlock against
/// the parser's read-locks.
pub fn call_library_loader(args: &[String]) -> Result<(), DjangoRustError> {
    let loader = {
        let slot = LIBRARY_LOADER
            .read()
            .map_err(|e| DjangoRustError::TemplateError(format!("Registry lock error: {e}")))?;
        match slot.as_ref() {
            None => return Ok(()),
            Some(callable) => Python::attach(|py| callable.clone_ref(py)),
        }
    };
    Python::attach(|py| {
        let py_args = pyo3::types::PyList::new(py, args.iter().map(|s| s.as_str()))
            .map_err(DjangoRustError::PythonException)?;
        loader
            .bind(py)
            .call1((py_args,))
            .map_err(DjangoRustError::PythonException)?;
        Ok(())
    })
}

/// Internal Rust API — the arg positions the renderer should resolve for
/// this assign tag (#2041).
///
/// Returns `Some(set)` only when the registered handler declared a
/// `RESOLVE_ARG_POSITIONS` set; the renderer then resolves ONLY those
/// 0-based positions and passes the rest as literal tokens. Returns
/// `None` when the handler declared no policy (or is not registered), in
/// which case the renderer resolves every arg — the historical default.
pub fn assign_handler_resolve_positions(name: &str) -> Option<HashSet<usize>> {
    ASSIGN_TAG_HANDLERS.read().ok().and_then(|registry| {
        registry
            .get(name)
            .and_then(|entry| entry.resolve_positions.clone())
    })
}

/// Call a registered Python assign-tag handler with args and context.
///
/// Returns a map of context updates to merge into the surrounding
/// render context. Error strings bubble up through
/// [`crate::renderer`] as `DjangoRustError::TemplateError`.
///
/// Back-compat shim around [`call_assign_handler_with_py_sidecar`] —
/// equivalent to passing `None` for the raw Python sidecar.
pub fn call_assign_handler(
    name: &str,
    args: &[TagArg],
    context: &HashMap<String, djust_core::Value>,
) -> Result<HashMap<String, djust_core::Value>, String> {
    call_assign_handler_with_py_sidecar(name, args, context, None)
}

/// Variant of [`call_assign_handler`] that additionally injects raw
/// Python objects from the [`Context::raw_py_objects`] sidecar into
/// the handler's ``context`` dict.
///
/// Mirrors [`call_handler_with_py_sidecar`] (extended in PR #1166)
/// for `Node::CustomTag`. Assign handlers needing access to
/// Python-only context (e.g. ``request`` / ``view``) can read those
/// keys from the dict directly. Sidecar values overwrite same-named
/// JSON keys.
///
/// Existing assign handlers that ignore the extra keys are
/// unaffected.
pub fn call_assign_handler_with_py_sidecar(
    name: &str,
    args: &[TagArg],
    context: &HashMap<String, djust_core::Value>,
    raw_py_objects: Option<&HashMap<String, pyo3::Py<PyAny>>>,
) -> Result<HashMap<String, djust_core::Value>, String> {
    let handler = {
        let registry = ASSIGN_TAG_HANDLERS
            .read()
            .map_err(|e| format!("Registry lock error: {e}"))?;
        let entry = registry
            .get(name)
            .ok_or_else(|| format!("No assign handler registered for tag: {name}"))?;
        Python::attach(|py| entry.handler.clone_ref(py))
    };

    Python::attach(|py| {
        let py_args = build_py_args(py, args)?;

        // Context dict with the raw-Python sidecar (``request``, ``view``)
        // on top, through the one builder every registry shares.
        let py_context = build_py_context(py, context, raw_py_objects)?;

        let handler_ref = handler.bind(py);
        let result = handler_ref
            .call_method1("render", (py_args, py_context))
            .map_err(|e| {
                let traceback = e
                    .traceback(py)
                    .map(|tb| tb.format().unwrap_or_default())
                    .unwrap_or_default();
                format!(
                    "Assign handler '{}' raised exception: {}\n{}",
                    name,
                    e.value(py),
                    traceback
                )
            })?;

        // Handlers may legitimately return None or something dict-like
        // but not a dict. Treat any extraction failure as an empty
        // merge (no-op) so a misbehaving handler can't crash the
        // whole render. Warn once per handler when the coercion fails
        // so the developer sees the silent-empty pattern rather than
        // hunting for why their assign tag didn't set anything (#805).
        //
        // NOT `result.extract::<HashMap<String, Value>>()` (#2510, round 4):
        // that blanket impl holds a live PyDict iterator over the handler's
        // OWN returned dict while recursively converting each value, and a
        // handler-authored value whose conversion mutates that SAME dict
        // (e.g. an unresolved lazy object) panics exactly like every other
        // site this bug class was found in. This is a PUBLIC extension
        // point (`register_assign_tag_handler`) — a handler author's own
        // return value is exactly the kind of object we can't assume is
        // well-behaved. Snapshot into an owned `Vec` first, matching every
        // other fix for this bug class.
        let snapshotted: Result<HashMap<String, djust_core::Value>, PyErr> = (|| {
            let dict = result.cast::<pyo3::types::PyDict>()?;
            let pairs: Vec<(Bound<'_, pyo3::PyAny>, Bound<'_, pyo3::PyAny>)> =
                dict.iter().collect();
            let mut map = HashMap::with_capacity(pairs.len());
            for (key, value) in pairs {
                let key_str: String = key.extract()?;
                map.insert(key_str, value.extract::<djust_core::Value>()?);
            }
            Ok(map)
        })();
        match snapshotted {
            Ok(map) => Ok(map),
            Err(err) => {
                // None is the documented "no context updates" sentinel;
                // don't warn on it — that's the deliberate "I did work
                // but have nothing to merge" path.
                let is_none = result.is_none();
                if !is_none {
                    let type_name = result
                        .get_type()
                        .qualname()
                        .map(|s| s.to_string())
                        .unwrap_or_else(|_| "<unknown>".to_string());
                    eprintln!(
                        "[djust] assign tag handler '{}' returned a non-dict value \
                         (type = {}); treating as empty merge. \
                         Handlers must return a dict[str, Any] mapping of context updates, \
                         or None. Coercion error: {}",
                        name, type_name, err
                    );
                }
                Ok(HashMap::new())
            }
        }
    })
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_handler_exists_empty() {
        // Clear any existing handlers
        clear_tag_handlers().unwrap();

        assert!(!handler_exists("url"));
        assert!(!handler_exists("static"));
    }

    #[test]
    fn test_get_registered_tags_empty() {
        clear_tag_handlers().unwrap();

        let tags = get_registered_tags().unwrap();
        assert!(tags.is_empty());
    }

    #[test]
    fn every_registry_builds_its_args_through_the_one_builder() {
        // The SINK pin (#2416). Three registries hand a handler a `list[str]`,
        // and the `SafeData` re-minting happens in exactly one place. A fourth
        // registry — or a hand-rolled `PyList::new(py, args)` creeping back
        // into one of the three — silently drops the marker for that path,
        // which is the #1646 shape this whole change exists to retire.
        let whole = include_str!("registry.rs");
        let (src, tests) = whole
            .split_once("\n#[cfg(test)]\n")
            .expect("the test-module boundary moved; this pin scans the wrong half");
        assert!(
            tests.contains("every_registry_builds_its_args_through_the_one_builder"),
            "the split landed in the wrong place"
        );
        // Five call sites since #2547: tag, block, assign, and the two
        // bindings-returning variants (`call_handler_with_bindings`,
        // `call_block_handler_with_bindings`).
        assert_eq!(
            src.matches("build_py_args(py, args)").count(),
            5,
            "the tag, block, assign and both bindings registries must all build args here"
        );
        assert!(
            !src.contains("PyList::new(py, args)"),
            "a registry is building its args list without the marker again"
        );
        // And the builder is the only place `mark_safe_str` reaches an
        // ARGUMENT: the block body's own call (#2379), its bindings twin and
        // the marked-context re-mint (#2547) are the other three, and there
        // must be exactly those four.
        assert_eq!(src.matches("mark_safe_str(py, ").count(), 4);
        // The context dict is built in ONE place for every registry (#2547):
        // the five call paths above plus no inline copy.
        assert_eq!(
            src.matches("build_py_context(py, context, raw_py_objects)")
                .count(),
            5,
            "every registry call path builds its context dict through the one builder"
        );
        assert_eq!(
            src.matches("for (key, value) in context {").count(),
            1,
            "the context-dict loop lives only inside `build_py_context`"
        );
    }

    #[test]
    fn a_tag_arg_is_plain_unless_explicitly_marked() {
        assert!(!TagArg::plain("x".to_string()).safe);
        assert!(TagArg::marked("x".to_string()).safe);
        assert_eq!(TagArg::plain("x".to_string()).text, "x");
    }
}
