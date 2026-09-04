"""
Type stubs for djust._rust module.

This file provides type information for the Rust extension module,
enabling proper type checking and IDE autocomplete for Rust-injected
functions and classes.

Generated for djust framework - see crates/djust_live/src/lib.rs
"""

from typing import Any, Awaitable, Callable, Dict, List, Optional, Tuple

# ============================================================================
# Core Template Rendering Functions
# ============================================================================

def render_template(
    template_source: str,
    context: Dict[str, Any],
    auto_call: Optional[bool] = None,
    string_if_invalid: Optional[str] = None,
) -> str:
    """
    Render a template string with the given context.

    Fast Rust-based template rendering using the djust template engine.

    Any context value that is not ``None`` and not a scalar also enters the
    raw-Python sidecar (#2501), so a dotted lookup that the serialized value
    cannot answer falls back to Django's ``getattr`` + auto-call against the
    live object. Built from ``context`` itself — there is nothing to pass.

    Args:
        template_source: The template source string to render
        context: Template context variables as a dictionary
        auto_call: Django-parity auto-call of callables reached through the
            sidecar (ADR-024). ``None`` means ON, which is Django's behaviour;
            ``DjustTemplate.render`` passes the project's
            ``LIVEVIEW_CONFIG['template_auto_call']``.
        string_if_invalid: Django's ``Engine.string_if_invalid`` — what a
            MISSING variable renders. ``None``/absent means the empty string
            (render nothing). A non-empty value also SKIPS the filter chain,
            which is Django's own control flow.

    Returns:
        The rendered HTML string

    Example::

        html = render_template(
            "<h1>{{ title }}</h1>",
            {"title": "Hello, World!"}
        )
    """
    ...

def compile_template(template_source: str) -> None:
    """
    Parse a template without rendering it (#2549).

    The construction-time check behind ``DjustTemplate.__init__``: raises
    ``RuntimeError`` with the engine's message when the source does not
    parse — an unregistered tag, an unknown filter, a bad arity, an unclosed
    block — and returns ``None`` otherwise. A successful parse is stored in
    the engine's template cache, so the render that follows does not parse
    again; a failed parse is never cached.
    """
    ...

def template_cache_contains(template_source: str) -> bool:
    """Whether the engine's template cache holds a parse of this exact source.

    Read-only test-support probe for the #2549 construction-time parse.
    """
    ...

def render_template_with_dirs(
    template_source: str,
    context: Dict[str, Any],
    template_dirs: List[str],
    safe_keys: Optional[List[str]] = None,
    auto_call: Optional[bool] = None,
    string_if_invalid: Optional[str] = None,
    template_name: Optional[str] = None,
) -> str:
    """
    Render a template with support for {% include %} tags.

    Extends render_template to support template inheritance and includes
    by providing template directories for the Rust renderer to search.

    Args:
        template_source: The template source string to render
        context: Template context variables as a dictionary
        template_dirs: List of directories to search for included templates
        safe_keys: Optional list of context keys to mark as safe (skip auto-escaping)
        auto_call: see ``render_template``. ``None`` means ON.
        string_if_invalid: see ``render_template``.
        template_name: this template's own name, which a relative
            ``{% extends "./parent.html" %}`` resolves against (Django's
            ``construct_relative_path``). ``None`` leaves such a target
            unchanged.

    Returns:
        The rendered HTML string

    Example::

        html = render_template_with_dirs(
            "{% include 'header.html' %}",
            {"title": "Home"},
            ["/app/templates"],
            safe_keys=["safe_html"]
        )
    """
    ...

def render_markdown(
    src: str,
    *,
    provisional: bool = True,
    tables: bool = True,
    strikethrough: bool = True,
    task_lists: bool = False,
) -> str:
    """
    Render Markdown source to sanitised HTML.

    Safe by construction:

    - Raw HTML tags in ``src`` are HTML-escaped (``Options::ENABLE_HTML`` is
      never set on the underlying pulldown-cmark parser).
    - ``javascript:``, ``vbscript:``, and ``data:`` URL schemes in links/images
      are replaced with ``#``.
    - Inputs larger than 10 MiB are returned wrapped in an escaped
      ``<pre class="djust-md-toobig">`` block without invoking the parser.

    Args:
        src: Markdown source string.
        provisional: If True (default), split the trailing unfinished line off
            as escaped plain text — avoids mid-syntax flicker during streaming
            LLM output.
        tables: Enable GFM tables.
        strikethrough: Enable ``~~strikethrough~~``.
        task_lists: Enable ``- [ ]`` / ``- [x]`` checkboxes.

    Returns:
        Sanitised HTML string.

    Example::

        html = render_markdown("**bold** and *italic*")
        # '<p><strong>bold</strong> and <em>italic</em></p>\\n'
    """
    ...

def diff_html(old_html: str, new_html: str) -> str:
    """
    Compute diff between two HTML strings.

    Parses both HTML strings into virtual DOM and computes minimal
    patches needed to transform old_html into new_html.

    Args:
        old_html: The old HTML string
        new_html: The new HTML string

    Returns:
        JSON string containing the patches

    Example::

        patches_json = diff_html("<div>Old</div>", "<div>New</div>")
    """
    ...

def resolve_template_inheritance(
    template_path: str,
    template_dirs: List[str],
) -> str:
    """
    Resolve template inheritance ({% extends %} and {% block %}).

    Given a template path and list of template directories, resolves
    {% extends %} and {% block %} tags to produce a final merged template.

    Args:
        template_path: Path to the child template (e.g., "products.html")
        template_dirs: List of directories to search for templates

    Returns:
        The merged template string with all inheritance resolved

    Example::

        template = resolve_template_inheritance(
            "pages/home.html",
            ["/app/templates"]
        )
    """
    ...

# ============================================================================
# Serialization Functions
# ============================================================================

def fast_json_dumps(obj: Any) -> str:
    """
    Fast JSON serialization for Python objects using Rust's serde_json.

    Benefits:
    - Releases Python GIL during serialization (better for concurrent workloads)
    - More memory efficient for large datasets
    - Similar performance to Python json.dumps for small datasets

    Args:
        obj: Python object to serialize (list, dict, primitives)

    Returns:
        JSON string

    Example::

        json_str = fast_json_dumps({"key": "value", "count": 42})
    """
    ...

def extract_template_variables(template: str) -> Dict[str, List[str]]:
    """
    Extract all variable references from a template.

    Parses the template and returns a mapping of variable names to
    their attribute access paths (for JIT serialization).

    Args:
        template: Template source string

    Returns:
        Dictionary mapping variable names to list of attribute paths

    Example::

        vars = extract_template_variables("{{ user.name }}")
        # Returns: {"user": ["name"]}
    """
    ...

def compute_template_hash(source: str) -> str:
    """
    Compute the canonical 8-hex template-source hash.

    The same hash drives both ``<!--dj-if id="if-<prefix>-N"-->``
    boundary marker IDs (Foundation 1 of #1358) and the per-template
    slot of the Redis state-backend cache key (#1362 section 1). Both
    consumers flow through the SAME ``template_hash_hex`` Rust helper,
    so they cannot drift.

    Args:
        source: Template source string (any size).

    Returns:
        8-character lowercase hex string. Same source ⇒ same hash;
        different sources ⇒ different hashes (collision rate ~1/4B).

    Example::

        compute_template_hash("<div>{{ x }}</div>")
        # Returns e.g. "42f47713"
    """
    ...

def crosses_as_encoded(obj: object) -> bool:
    """Does *obj* cross into the renderer as a ``Value::Encoded``? (#2477/#2489)

    ``Value::Encoded`` is the carrier that holds a Python object by facts
    MEASURED from it — ``str(o)``, ``repr(o)``, ``bool(o)``, ``len(o)``, its
    attributes and its items — rather than by its ``str()``. A ``set``, a
    ``dict_keys``, a ``complex`` and a ``__bool__``-False class all cross that
    way; a ``bytes``, a ``deque`` and anything with an integer ``__getitem__``
    are claimed by PyO3's sequence extraction first and cross as a
    ``Value::List``.

    Consulted by ``djust.serialization.normalize_django_value`` at its final
    fallback, so the LiveView path stops flattening what the conversion carries
    exactly. It RUNS the conversion rather than re-stating its gate, which is
    what keeps the two from drifting (#1646).

    Args:
        obj: Any Python object.

    Returns:
        True only if the conversion produces a ``Value::Encoded``.
    """
    ...

def crosses_as_encoded_by_conversion(obj: object) -> bool:
    """The same bit, decided by RUNNING the conversion (#2477/#2489).

    The reference :func:`crosses_as_encoded` is checked against, and NOT what
    production calls: converting an object eagerly walks its whole graph, which
    is work the render path never does and which overflowed the stack when the
    production predicate was written this way. Exposed so the differential in
    ``python/tests/test_opaque_collections_2477_2489.py`` is a real comparison
    rather than an assertion that the probe agrees with itself.
    """
    ...

def set_virtual_keyed_ops(enabled: bool) -> None:
    """Enable/disable `[dj-virtual]` keyed splice ops in the differ (ADR-026).

    Process-global, unlike the per-view `set_loop_render_cache_enabled`.
    Django applies it once at startup from
    `LIVEVIEW_CONFIG['virtual_keyed_ops']`; see `DjustConfig.ready`.
    """

def virtual_keyed_ops_enabled() -> bool:
    """Current `[dj-virtual]` keyed-splice-ops setting."""

def set_django_value_repr(enabled: bool) -> None:
    """Enable/disable Django-parity value rendering (#2203).

    When True (the default), `{{ }}` renders values as Python's `str()` does:
    `True`, `None`, `1.0`, `[1, 2]`, `{'a': 1}`, `(1, 2)`. Process-global, for
    the same reason as `set_virtual_keyed_ops` — `impl Display for Value` has
    nowhere to thread per-render config. Django applies it once at startup from
    `LIVEVIEW_CONFIG['django_value_repr']`; see `DjustConfig.ready`.
    """

def django_value_repr_enabled() -> bool:
    """Current Django-parity value-rendering setting."""

def set_resolve_lazy(enabled: bool) -> None:
    """Set the CALLING THREAD's ADR-027 lazy-resolution flag (#2539).

    `LIVEVIEW_CONFIG["template_resolve_lazy"]`, default **True** since #2539
    movement 3 (`False` is the escape hatch). When on, a dotted template lookup
    resolves against the LIVE Python object one segment at a time — Django's
    `Variable._resolve_lookup` — instead of against an eager conversion of it.

    Thread-local for the reason `set_active_timezone` below is, and NOT a
    per-`Context` field: half the behaviour it gates lives inside the
    PyO3 conversion (`impl FromPyObject for Value`), which has no `Context` to
    thread config through. Applied per render by
    `djust.render_env.apply_render_env`, beside the timezone and the number
    format, so a render path cannot acquire one ambient setting and miss
    another.

    The thread-local is SET, not scoped: a thread keeps the last pushed value.
    Every framework render entry pushes on each render, so this only matters
    for a caller reaching `render_template` / `render_template_with_dirs`
    directly — it inherits whatever the thread last rendered with, and on a
    FRESH thread that is the Rust-side default, which tracks the Python one.
    """

def resolve_lazy_enabled() -> bool:
    """The calling thread's ADR-027 lazy-resolution flag (#2539).

    Exposed so the Python side can ASSERT the wiring took effect rather than
    assume it — a setter with no getter cannot be tested end to end (#2017).
    """

def set_active_timezone(name: Optional[str] = None) -> bool:
    """Set the active render timezone for the CALLING THREAD (#2209).

    `name` is an IANA zone (`"America/New_York"`); `None` disables conversion,
    which is what `USE_TZ = False` wants. Returns False if the name is not a
    zone the bundled tz database knows, leaving the previous value in place.

    Thread-local rather than process-global — unlike `set_django_value_repr`
    above — because djust renders run in `sync_to_async` worker threads and two
    connections can have activated different zones. Mirrors Django, whose own
    `timezone._active` is a `Local()`. Applied per render by
    `RustBridgeMixin._apply_active_timezone`, not once at startup: a zone
    captured at `ready()` would miss every `timezone.activate()`.
    """

def active_timezone_name() -> Optional[str]:
    """The calling thread's active render timezone, or None."""

def set_number_format(
    decimal_sep: Optional[str] = None,
    thousand_sep: Optional[str] = None,
    grouping: Optional[List[int]] = None,
    use_grouping: bool = False,
    raw_decimal_sep: Optional[str] = None,
    raw_thousand_sep: Optional[str] = None,
    raw_grouping: Optional[List[int]] = None,
) -> None:
    """Set the CALLING THREAD's number format (#2221).

    `None` for `decimal_sep` disables localization. `grouping` is Django's
    `NUMBER_GROUPING` as a list — a scalar `3` arrives as `[3, 0]`, and Indian
    grouping is `[3, 2, 0]`; a `0` entry keeps the previous width.

    The `raw_*` triple is Django's `use_l10n=False` format (#2266) —
    `settings.DECIMAL_SEPARATOR` / `THOUSAND_SEPARATOR` / `NUMBER_GROUPING`
    read directly rather than through the active locale, which is what
    `floatformat`'s `u` suffix formats through. `None` clears it. There is no
    `raw_use_grouping`: that half never groups on its own, because Django's
    `use_grouping` is False whenever `use_l10n` is False; `floatformat`'s `g`
    supplies `force_grouping` at the call site.

    The parameters come from Python rather than being derived in Rust — the
    inverse of `set_active_timezone` above, and deliberate: locale formatting is
    defined by `django/conf/locale/*/formats.py`, so deriving it in Rust would
    fork Django's data instead of using it. Applied per render by
    `djust.render_env.apply_render_env`.
    """

def active_number_format() -> Optional[Tuple[str, str, List[int], bool]]:
    """`(decimal_sep, thousand_sep, grouping, use_grouping)`, or None."""

def active_unlocalized_number_format() -> Optional[Tuple[str, str, List[int], bool]]:
    """The `use_l10n=False` format (#2266), same shape as above, or None.

    Exposed so the Python side can ASSERT the second format reached Rust
    rather than assume it (#2017). Its `use_grouping` is always False.
    """

def dj_model_fields_from_template(
    template_source: str,
    template_dirs: Optional[List[str]] = None,
) -> List[str]:
    """
    Collect fields bound via static ``dj-model="<field>"`` from a raw template
    source string (and any ``{% include %}``d templates resolvable in
    ``template_dirs``).

    Module-level companion to :meth:`RustLiveView.dj_model_fields` for callers
    that have a template source but no live view — notably embedded
    ``{% live_render %}`` children. The immune source for the dj-model
    mass-assignment allowlist (CWE-915): values come from the parsed template
    AST's ``Node::Text`` literals (developer-authored template text), not the
    rendered output. A dynamic ``dj-model="{{ var }}"`` binding is NOT captured
    (fail-closed); a parse error or unresolvable include yields no fields for
    that branch (fail-closed).

    Args:
        template_source: Raw template source string.
        template_dirs: Search dirs for ``{% include %}`` resolution.

    Returns:
        Sorted, deduplicated list of bindable field names.
    """
    ...

def serialize_queryset(
    objects: List[Any],
    field_paths: List[str],
) -> List[Dict[str, Any]]:
    """
    Serialize Django QuerySet objects efficiently.

    Fast Rust-based serialization that prevents N+1 queries by
    pre-fetching related fields.

    Args:
        objects: List of Django model instances
        field_paths: List of field paths to serialize (e.g., ["id", "user.name"])

    Returns:
        List of dictionaries containing serialized objects

    Example::

        data = serialize_queryset(
            list(Article.objects.all()),
            ["id", "title", "author.name"]
        )
    """
    ...

def serialize_context(
    context: Dict[str, Any],
    field_paths: Dict[str, List[str]],
) -> Dict[str, Any]:
    """
    Serialize template context with field paths.

    Efficiently serializes Django models and QuerySets in template context
    using the provided field paths (from template variable extraction).

    Args:
        context: Template context dictionary
        field_paths: Mapping of variable names to field paths

    Returns:
        Serialized context dictionary

    Example::

        serialized = serialize_context(
            {"user": user_obj, "articles": articles_qs},
            {"user": ["name", "email"], "articles": ["id", "title"]}
        )
    """
    ...

# ============================================================================
# Model Serialization (N+1 Prevention)
# ============================================================================

def serialize_models_fast(
    models: List[Any],
    fields: List[str],
) -> List[Dict[str, Any]]:
    """
    Fast serialization of Django model instances.

    Optimized Rust-based serialization for lists of Django model instances.

    Args:
        models: List of Django model instances
        fields: List of field names to serialize

    Returns:
        List of dictionaries containing serialized models
    """
    ...

def serialize_models_to_list(
    models: List[Any],
    fields: List[str],
) -> List[List[Any]]:
    """
    Serialize Django models to list of lists (table format).

    Similar to serialize_models_fast but returns data in tabular format
    instead of list of dicts.

    Args:
        models: List of Django model instances
        fields: List of field names to serialize

    Returns:
        List of lists containing serialized field values
    """
    ...

# ============================================================================
# `{% load %}` library loader (#2547)
# ============================================================================

def register_library_loader(callable: Callable[[List[str]], None]) -> None:
    """
    Install the parser's ``{% load %}`` hook.

    ``callable(args)`` receives the tag's arguments exactly as written
    (``["static"]``, ``["a", "b"]``, ``["echo", "from", "testtags"]``) at
    PARSE time — for every parse, including ``{% include %}``d and
    ``{% extends %}``ed files — and registers the named library's tags and
    filters before the parser reaches them. An exception it raises (Django's
    ``TemplateSyntaxError`` for an unknown library) crosses the parse whole.
    ``djust.template_libraries.install_loader`` is the framework's caller.
    """
    ...

def register_raw_block_tag_handler(tag_name: str, end_tag: str, handler: Any) -> None: ...
def unregister_raw_block_tag_handler(tag_name: str) -> bool: ...
def has_raw_block_tag_handler(tag_name: str) -> bool: ...
def clear_raw_block_tag_handlers() -> None: ...
def register_translator(callable: Callable[[str], str]) -> None: ...
def clear_translator() -> None: ...
def arm_scope_tags(names: List[str]) -> None: ...
def clear_scope_tags() -> None: ...
def register_language_scope_hooks(
    enter: Callable[[str], Any], exit: Callable[[Any], None]
) -> None: ...
def register_timezone_scope_hooks(
    enter: Callable[[str], Any], exit: Callable[[Any], None]
) -> None: ...
def clear_library_loader() -> None:
    """Remove the ``{% load %}`` hook; ``{% load %}`` is a no-op again."""
    ...

def has_library_loader() -> bool:
    """Is a ``{% load %}`` hook installed?"""
    ...

# ============================================================================
# Template Tag Handler Registry
# ============================================================================

def register_tag_handler(tag_name: str, handler: Any) -> None:
    """
    Register a custom template tag handler.

    Allows registering Python handlers for custom inline template tags
    like {% url %}, {% static %}, etc.

    Args:
        tag_name: Name of the tag (e.g., "url", "static")
        handler: Python object with a ``render(args, context)`` method.
            A bare function is rejected with
            ``TypeError: Handler must have a 'render' method``.
            ``args`` holds the tag's arguments ALREADY RESOLVED against the
            context -- ``{% custom p %}`` with ``p="<b>hi</b>"`` arrives as
            ``args == ['<b>hi</b>']``, not as ``['p']``.

    The return value is ESCAPED unless it is already HTML (#2379), mirroring
    Django's ``SimpleNode.render``, which runs ``conditional_escape`` over a
    ``simple_tag``'s return unless it carries ``__html__``. Return a plain
    ``str`` for text; wrap markup you have made safe yourself in
    ``mark_safe`` (or return any object exposing ``__html__``).

    Example::

        from django.utils.html import escape
        from django.utils.safestring import mark_safe

        class CustomTagHandler:
            def render(self, args, context):
                name = args[0] if args else ""
                return mark_safe(f"<custom>{escape(name)}</custom>")

        register_tag_handler("custom", CustomTagHandler())
    """
    ...

def has_tag_handler(tag_name: str) -> bool:
    """
    Check if a tag handler is registered.

    Args:
        tag_name: Name of the tag to check

    Returns:
        True if a handler is registered for this tag
    """
    ...

def get_registered_tags() -> List[str]:
    """
    Get list of all registered tag names.

    Returns:
        List of registered tag names
    """
    ...

def unregister_tag_handler(tag_name: str) -> None:
    """
    Unregister a template tag handler.

    Args:
        tag_name: Name of the tag to unregister
    """
    ...

def clear_tag_handlers() -> None:
    """
    Clear all registered tag handlers.
    """
    ...

def register_block_tag_handler(tag_name: str, end_tag: str, handler: Any) -> None:
    """
    Register a Python block tag handler for a custom template block tag.

    Block tags wrap content like ``{% modal %}...{% endmodal %}``.
    The handler receives the pre-rendered HTML of the block body.

    Args:
        tag_name: Opening tag name (e.g., "modal", "card")
        end_tag: Closing tag name (e.g., "endmodal", "endcard")
        handler: Python object with ``render(args, content, context)`` method

    Known constraints:

    * **No parent-tag propagation** (issue #804). A block tag handler
      whose children include another block tag handler receives the
      inner tag's output as a pre-rendered HTML string embedded in
      ``content``; the inner handler is NOT informed that it is nested
      inside a parent handler. If your outer tag needs to know about
      nesting (e.g. to emit different markup when inside a ``<table>``
      wrapper tag), stash the hint on ``context`` in the outer handler
      and read it back in the inner handler rather than relying on
      automatic propagation. Future enhancement tracked in issue #804.

    * **No loader access in handlers** (issue #803). Block handlers
      currently cannot call ``{% render_template name=... %}``-style
      template loads. The ``FilesystemTemplateLoader`` is not exposed
      through the Rust-to-Python bridge. Workaround: pre-render the
      child template in your view and pass the result via context.
    """
    ...

def has_block_tag_handler(tag_name: str) -> bool:
    """
    Check if a block tag handler is registered for the given tag name.

    Args:
        tag_name: Tag name to check

    Returns:
        True if a block handler is registered
    """
    ...

def unregister_block_tag_handler(tag_name: str) -> bool:
    """
    Unregister a block tag handler.

    Args:
        tag_name: Name of the tag to unregister

    Returns:
        True if a handler was removed
    """
    ...

def clear_block_tag_handlers() -> None:
    """
    Clear all registered block tag handlers (primarily for testing).
    """
    ...

def register_assign_tag_handler(tag_name: str, handler: Any) -> None:
    """
    Register a Python assign-tag handler for a context-mutating template tag.

    Unlike ``register_tag_handler`` (emits HTML) and
    ``register_block_tag_handler`` (wraps content), an assign tag
    returns a ``dict[str, Any]`` whose keys are merged into the
    template context for subsequent sibling nodes. No HTML is emitted.

    Args:
        tag_name: Tag name (e.g., "assign_slot")
        handler: Python object with ``render(args, context)`` method
            returning a ``dict[str, Any]``
    """
    ...

def has_assign_tag_handler(tag_name: str) -> bool:
    """Check if an assign tag handler is registered for the given name."""
    ...

def unregister_assign_tag_handler(tag_name: str) -> bool:
    """Unregister an assign tag handler. Returns True if one was removed."""
    ...

def clear_assign_tag_handlers() -> None:
    """Clear all registered assign tag handlers (primarily for testing)."""
    ...

# ============================================================================
# Custom Filter Registry (project-defined ``@register.filter``)
# ============================================================================

def register_custom_filter(
    name: str,
    callable: Any,
    is_safe: bool = False,
    needs_autoescape: bool = False,
) -> None:
    """Register a project-defined custom template filter (#1121).

    Bridges Django's ``@register.filter`` callables into the Rust
    template engine. The Rust renderer's filter dispatch consults this
    registry when its built-in match falls through.

    Most callers use the higher-level
    :func:`djust.template_filters.register_django_filter` (single
    filter) or :func:`djust.template_filters.bootstrap_django_filters`
    (walk every registered Django Library).

    Args:
        name: Filter name as used in templates (``{{ x|name }}``).
        callable: Django filter callable (``(value, arg=None) -> str``).
        is_safe: Django ``filter.is_safe`` attribute — when True, a
            SafeData INPUT stays safe through the filter (Django's
            ``is_safe and isinstance(obj, SafeData)`` rule, #2548). Unsafe
            input is still escaped; a filter that produces markup must
            return ``mark_safe(...)``/``format_html(...)`` itself.
        needs_autoescape: Django ``filter.needs_autoescape`` attribute —
            when True, ``autoescape=True`` is passed as a kwarg.
    """
    ...

def unregister_custom_filter(name: str) -> bool:
    """Unregister a custom filter. Returns True if a filter was removed."""
    ...

def has_custom_filter(name: str) -> bool:
    """Check if a custom filter is registered."""
    ...

def clear_custom_filters() -> None:
    """Clear all registered custom filters (primarily for tests)."""
    ...

def get_registered_custom_filters() -> List[str]:
    """Return the names of all registered custom filters."""
    ...

# ============================================================================
# Actor System
# ============================================================================

class SessionActorHandle:
    """
    Handle to a session actor for async state management.

    Provides async methods for interacting with the actor-based
    session state system.
    """

    def mount(
        self,
        view_path: str,
        params: Dict[str, Any],
        request_meta: Dict[str, Any],
    ) -> Awaitable[Tuple[str, str]]:
        """
        Mount a LiveView and render initial HTML.

        Args:
            view_path: Python path to the LiveView class (e.g., "app.views.Counter")
            params: Initial state parameters
            request_meta: Request metadata (user, session, etc.)

        Returns:
            Awaitable that resolves to (view_id, html)
        """
        ...

    def handle_event(
        self,
        view_id: str,
        event: str,
        params: Dict[str, Any],
    ) -> Awaitable[str]:
        """
        Handle an event on a mounted view.

        Args:
            view_id: ID of the view (from mount)
            event: Event name (e.g., "increment", "submit")
            params: Event parameters

        Returns:
            Awaitable that resolves to HTML patches JSON
        """
        ...

    def shutdown(self) -> Awaitable[None]:
        """
        Shutdown the session actor.

        Returns:
            Awaitable that resolves when shutdown is complete
        """
        ...

class SupervisorStatsPy:
    """
    Statistics for the actor supervisor.

    Provides metrics about active sessions, memory usage, etc.
    """

    active_sessions: int
    total_created: int
    total_dropped: int
    ttl_secs: int

def create_session_actor(session_id: str) -> Awaitable[SessionActorHandle]:
    """
    Create or retrieve a session actor.

    Creates a new session actor or returns existing one for the given
    session ID. Uses the global supervisor for lifecycle management.

    Args:
        session_id: Unique session identifier

    Returns:
        Awaitable that resolves to SessionActorHandle

    Example::

        handle = await create_session_actor("session-123")
        view_id, html = await handle.mount("app.views.Counter", {}, {})
    """
    ...

def get_actor_stats() -> SupervisorStatsPy:
    """
    Get statistics from the actor supervisor.

    Returns:
        SupervisorStatsPy with metrics about active sessions
    """
    ...

# ============================================================================
# RustLiveView Backend
# ============================================================================

class RustLiveView:
    """
    Rust-backed LiveView component for high-performance rendering.

    Manages state and rendering using Rust's template engine and
    virtual DOM diffing.
    """

    def __init__(
        self,
        template_source: str,
        template_dirs: Optional[List[str]] = None,
    ) -> None:
        """
        Create a new RustLiveView backend.

        Args:
            template_source: The template source string
            template_dirs: Optional list of template directories for {% include %}
        """
        ...

    def set_template_dirs(self, dirs: List[str]) -> None:
        """
        Set template directories for {% include %} tag support.

        Args:
            dirs: List of template directory paths
        """
        ...

    def set_state(self, key: str, value: Any) -> None:
        """
        Set a single state variable.

        Args:
            key: State variable name
            value: State variable value
        """
        ...

    def update_state(self, updates: Dict[str, Any]) -> None:
        """
        Update state with multiple variables.

        Args:
            updates: Dictionary of state updates
        """
        ...

    def retain_state_keys(self, keys: List[str]) -> List[str]:
        """
        Drop every state key absent from ``keys`` and return the removed keys.

        ``update_state`` merges and never removes (#2564); the bridge calls
        this with the FULL context's keys before every ``update_state`` so a
        key the context stopped carrying stops rendering. Removal revokes the
        removed keys' safe grants (#2300). The caller adds the returned keys
        to ``set_changed_keys`` so a partial render re-renders their regions.

        Args:
            keys: The keys to KEEP (the full context, plus static assigns)

        Returns:
            The keys that were removed
        """
        ...

    def mark_safe_keys(self, keys: List[str]) -> None:
        """
        Mark context keys as safe (skip auto-escaping).

        Called from Python when SafeString values are detected.

        Args:
            keys: List of context keys to mark as safe
        """
        ...

    def set_raw_py_values(self, values: Dict[str, Any]) -> None:
        """
        Attach raw Python objects for ``getattr``-fallback lookups.

        Called from ``_sync_state_to_rust`` to pass through Django
        model instances (and other non-JSON-serializable context
        values) so the Rust template engine can resolve expressions
        like ``{{ user.username }}`` via ``getattr`` when the value
        is not present in the JSON-serialized state.

        An empty dict clears any previously-attached sidecar.

        Args:
            values: Mapping of top-level context name -> Python object
        """
        ...

    def update_template(self, new_template_source: str) -> None:
        """
        Update the template source while preserving VDOM state.

        Allows dynamic templates to change without losing diffing capability.

        Args:
            new_template_source: New template source string
        """
        ...

    def template_hash(self) -> str:
        """
        Return the canonical 8-hex template-source hash for this view.

        Same hash powers the ``<!--dj-if id="if-<prefix>-N"-->`` boundary
        marker IDs and the per-template slot of the state-backend cache
        key (#1362 section 1). Cf. :func:`compute_template_hash` for the
        module-level entry point used by callers that don't have a view
        instance yet.

        Returns:
            8 lowercase hex chars; stable across re-renders of the same
            ``template_source``.
        """
        ...

    def dj_model_fields(self) -> List[str]:
        """
        Return the fields bound via static ``dj-model="<field>"`` in this
        view's CURRENT template source (and any ``{% include %}``d templates).

        The immune source for the dj-model mass-assignment allowlist
        (CWE-915): values come from the parsed template AST's ``Node::Text``
        literals — developer-authored template text that attacker data can
        never reach (it flows only through ``{{ }}`` ``Node::Variable``
        substitution). A dynamic ``dj-model="{{ var }}"`` binding is NOT
        captured (fail-closed). Cf. :func:`dj_model_fields_from_template` for
        the module-level entry point used by callers (embedded children) that
        have a template source but no view instance.

        Returns:
            Sorted, deduplicated list of bindable field names.
        """
        ...

    def clear_fragment_cache(self) -> None:
        """
        Clear the partial-render fragment cache, forcing the next render to
        do a full collecting render.

        Keeps ``last_vdom`` intact so the diff baseline is preserved. Used by
        the partial-render correctness harness in tests to produce a control
        output for byte-equality comparison.
        """
        ...

    def get_state(self) -> Dict[str, Any]:
        """
        Get current state.

        Returns:
            Dictionary containing current state
        """
        ...

    def render(self) -> str:
        """
        Render the template and return HTML.

        Returns:
            Rendered HTML string
        """
        ...

    def render_with_diff(self) -> Tuple[str, Optional[str], int]:
        """
        Render and compute diff from last render.

        Returns:
            Tuple of (html, patches_json, version)
        """
        ...

    def serialize_msgpack(self) -> bytes:
        """
        Serialize the view state to MessagePack bytes (with embedded timestamp).

        The compact binary form (~30-40% smaller than JSON) used by the state
        backends (``djust.state_backends.memory`` / ``redis``) to persist a view
        across requests. The current timestamp is embedded for session-age
        tracking (see :meth:`get_timestamp`).

        Returns:
            ``bytes`` containing the serialized state plus timestamp.
        """
        ...

    @staticmethod
    def deserialize_msgpack(data: bytes) -> "RustLiveView":
        """
        Reconstruct a ``RustLiveView`` from bytes produced by
        :meth:`serialize_msgpack`.

        Args:
            data: ``bytes`` containing MessagePack data.

        Returns:
            A ``RustLiveView`` instance with restored state.
        """
        ...

    def get_timestamp(self) -> float:
        """
        Return the Unix timestamp (seconds since epoch) embedded when this view
        was last serialized via :meth:`serialize_msgpack`.

        Returns:
            ``float`` Unix timestamp; ``0`` for a never-serialized view.
        """
        ...

# ============================================================================
# Rust UI Components
# ============================================================================

class RustButton:
    """
    Rust-backed Button component.

    High-performance button with support for Bootstrap 5, Tailwind, and plain HTML.
    """

    def __init__(
        self,
        id: str,
        label: str,
        *,
        variant: Optional[str] = None,
        size: Optional[str] = None,
        outline: Optional[bool] = None,
        disabled: Optional[bool] = None,
        full_width: Optional[bool] = None,
        icon: Optional[str] = None,
        on_click: Optional[str] = None,
    ) -> None: ...
    @property
    def id(self) -> str: ...
    @property
    def label(self) -> str: ...
    @label.setter
    def label(self, value: str) -> None: ...
    @property
    def disabled(self) -> bool: ...
    @disabled.setter
    def disabled(self, value: bool) -> None: ...
    def variant(self, variant: str) -> None: ...
    def render(self) -> str: ...
    def render_with_framework(self, framework: str) -> str: ...
    def with_variant(self, variant: str) -> "RustButton": ...
    def with_size(self, size: str) -> "RustButton": ...
    def with_outline(self, outline: bool) -> "RustButton": ...
    def with_disabled(self, disabled: bool) -> "RustButton": ...
    def with_icon(self, icon: str) -> "RustButton": ...
    def with_on_click(self, handler: str) -> "RustButton": ...

class RustAlert:
    """Rust-backed Alert component."""
    def __init__(self, id: str, message: str, **kwargs: Any) -> None: ...
    def render(self) -> str: ...

class RustAvatar:
    """Rust-backed Avatar component."""
    def __init__(self, id: str, **kwargs: Any) -> None: ...
    def render(self) -> str: ...

class RustBadge:
    """Rust-backed Badge component."""
    def __init__(self, id: str, text: str, **kwargs: Any) -> None: ...
    def render(self) -> str: ...

class RustCard:
    """Rust-backed Card component."""
    def __init__(self, id: str, **kwargs: Any) -> None: ...
    def render(self) -> str: ...

class RustDivider:
    """Rust-backed Divider component."""
    def __init__(self, id: str, **kwargs: Any) -> None: ...
    def render(self) -> str: ...

class RustIcon:
    """Rust-backed Icon component."""
    def __init__(self, id: str, name: str, **kwargs: Any) -> None: ...
    def render(self) -> str: ...

class RustModal:
    """Rust-backed Modal component."""
    def __init__(self, id: str, **kwargs: Any) -> None: ...
    def render(self) -> str: ...

class RustProgress:
    """Rust-backed Progress bar component."""
    def __init__(self, id: str, value: float, **kwargs: Any) -> None: ...
    def render(self) -> str: ...

class RustRange:
    """Rust-backed Range slider component."""
    def __init__(self, id: str, **kwargs: Any) -> None: ...
    def render(self) -> str: ...

class RustSpinner:
    """Rust-backed Spinner component."""
    def __init__(self, id: str, **kwargs: Any) -> None: ...
    def render(self) -> str: ...

class RustSwitch:
    """Rust-backed Switch/Toggle component."""
    def __init__(self, id: str, **kwargs: Any) -> None: ...
    def render(self) -> str: ...

class RustTextArea:
    """Rust-backed TextArea component."""
    def __init__(self, id: str, **kwargs: Any) -> None: ...
    def render(self) -> str: ...

class RustToast:
    """Rust-backed Toast notification component."""
    def __init__(self, id: str, message: str, **kwargs: Any) -> None: ...
    def render(self) -> str: ...

class RustTooltip:
    """Rust-backed Tooltip component."""
    def __init__(self, id: str, **kwargs: Any) -> None: ...
    def render(self) -> str: ...

# ============================================================================
# Module Exports
# ============================================================================

__all__ = [
    # Core rendering
    "render_template",
    "render_template_with_dirs",
    "compile_template",
    "template_cache_contains",
    "render_markdown",
    "diff_html",
    "resolve_template_inheritance",
    # Serialization
    "fast_json_dumps",
    "extract_template_variables",
    "compute_template_hash",
    "crosses_as_encoded",
    "crosses_as_encoded_by_conversion",
    "dj_model_fields_from_template",
    "serialize_queryset",
    "serialize_context",
    "serialize_models_fast",
    "serialize_models_to_list",
    # Tag handlers (inline)
    "register_library_loader",
    "clear_library_loader",
    "has_library_loader",
    # Raw-block handlers, the `_("…")` translator, and the scope hooks (#2558)
    "register_raw_block_tag_handler",
    "unregister_raw_block_tag_handler",
    "has_raw_block_tag_handler",
    "clear_raw_block_tag_handlers",
    "register_translator",
    "clear_translator",
    "arm_scope_tags",
    "clear_scope_tags",
    "register_language_scope_hooks",
    "register_timezone_scope_hooks",
    "register_tag_handler",
    "has_tag_handler",
    "get_registered_tags",
    "unregister_tag_handler",
    "clear_tag_handlers",
    # Block tag handlers
    "register_block_tag_handler",
    "has_block_tag_handler",
    "unregister_block_tag_handler",
    "clear_block_tag_handlers",
    # Assign tag handlers (context-mutating)
    "register_assign_tag_handler",
    "has_assign_tag_handler",
    "unregister_assign_tag_handler",
    "clear_assign_tag_handlers",
    # Custom filter registry (project-defined ``@register.filter``)
    "register_custom_filter",
    "unregister_custom_filter",
    "has_custom_filter",
    "clear_custom_filters",
    "get_registered_custom_filters",
    # Actor system
    "SessionActorHandle",
    "SupervisorStatsPy",
    "create_session_actor",
    "get_actor_stats",
    # LiveView backend
    "RustLiveView",
    # UI Components
    "RustAlert",
    "RustAvatar",
    "RustBadge",
    "RustButton",
    "RustCard",
    "RustDivider",
    "RustIcon",
    "RustModal",
    "RustProgress",
    "RustRange",
    "RustSpinner",
    "RustSwitch",
    "RustTextArea",
    "RustToast",
    "RustTooltip",
    "set_virtual_keyed_ops",
    "set_django_value_repr",
    "django_value_repr_enabled",
    "set_resolve_lazy",
    "resolve_lazy_enabled",
    "set_active_timezone",
    "active_timezone_name",
    "set_number_format",
    "active_number_format",
    "active_unlocalized_number_format",
    "virtual_keyed_ops_enabled",
]
