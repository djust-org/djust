"""
Type stubs for djust._rust module.

This file provides type information for the Rust extension module,
enabling proper type checking and IDE autocomplete for Rust-injected
functions and classes.

Generated for djust framework - see crates/djust_live/src/lib.rs
"""

from typing import Any, Awaitable, Dict, List, Optional, Tuple

# ============================================================================
# Core Template Rendering Functions
# ============================================================================

def render_template(template_source: str, context: Dict[str, Any]) -> str:
    """
    Render a template string with the given context.

    Fast Rust-based template rendering using the djust template engine.

    Args:
        template_source: The template source string to render
        context: Template context variables as a dictionary

    Returns:
        The rendered HTML string

    Example::

        html = render_template(
            "<h1>{{ title }}</h1>",
            {"title": "Hello, World!"}
        )
    """
    ...

def render_template_with_dirs(
    template_source: str,
    context: Dict[str, Any],
    template_dirs: List[str],
    safe_keys: Optional[List[str]] = None,
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
# Template Tag Handler Registry
# ============================================================================

def register_tag_handler(tag_name: str, handler: Any) -> None:
    """
    Register a custom template tag handler.

    Allows registering Python callbacks for custom template tags
    like {% url %}, {% static %}, etc.

    Args:
        tag_name: Name of the tag (e.g., "url", "static")
        handler: Python callable to handle the tag

    Example::

        def handle_custom_tag(args, kwargs):
            return f"<custom>{args}</custom>"

        register_tag_handler("custom", handle_custom_tag)
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

    def mark_safe_keys(self, keys: List[str]) -> None:
        """
        Mark context keys as safe (skip auto-escaping).

        Called from Python when SafeString values are detected.

        Args:
            keys: List of context keys to mark as safe
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
    "diff_html",
    "resolve_template_inheritance",
    # Serialization
    "fast_json_dumps",
    "extract_template_variables",
    "serialize_queryset",
    "serialize_context",
    "serialize_models_fast",
    "serialize_models_to_list",
    # Tag handlers
    "register_tag_handler",
    "has_tag_handler",
    "get_registered_tags",
    "unregister_tag_handler",
    "clear_tag_handlers",
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
]
