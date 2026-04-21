"""
Decorators for LiveView event handlers and reactive properties

These decorators make LiveView code more elegant and explicit by marking
event handlers, reactive state, and computed properties.
"""

import asyncio
import functools
import warnings
from typing import Callable, Any, TypeVar, Union, cast, List, Optional


F = TypeVar("F", bound=Callable[..., Any])


def _add_decorator_metadata(func: Callable, key: str, value: Any) -> None:
    """
    Add decorator metadata to function.

    Internal helper for @debounce, @throttle, @optimistic, @cache, @client_state.
    Ensures consistent metadata structure across all decorators.

    Args:
        func: Function to add metadata to
        key: Decorator name (e.g., 'debounce', 'cache')
        value: Decorator configuration (dict, bool, etc.)
    """
    if not hasattr(func, "_djust_decorators"):
        func._djust_decorators = {}  # type: ignore
    func._djust_decorators[key] = value  # type: ignore


def _make_metadata_decorator(key: str, value: Any) -> Callable[[F], F]:
    """
    Create a decorator that adds metadata without modifying execution.

    Factory for @debounce, @throttle, @cache, @client_state which only add
    metadata for client-side processing, not runtime behavior.

    Args:
        key: Metadata key to add to _djust_decorators
        value: Metadata value (typically a dict with config)

    Returns:
        Decorator function that adds metadata to the wrapped function
    """

    def decorator(func: F) -> F:
        @functools.wraps(func)
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            return func(*args, **kwargs)

        _add_decorator_metadata(wrapper, key, value)
        return cast(F, wrapper)

    return decorator


def event_handler(
    params: Optional[List[str]] = None,
    description: str = "",
    coerce_types: bool = True,
    expose_api: bool = False,
) -> Callable[[F], F]:
    """
    Mark method as event handler with automatic signature introspection.

    Auto-extracts parameter names, types, and descriptions from function signature.
    Stores metadata in _djust_decorators for validation and debug panel.

    By default, string parameters from template data-* attributes are automatically
    coerced to the expected types based on type hints (e.g., "123" -> 123 for int).
    Set coerce_types=False to receive raw string values.

    Args:
        params: Optional explicit parameter list (overrides auto-extraction)
        description: Human-readable description (overrides docstring)
        coerce_types: Whether to coerce string params to expected types (default: True)
        expose_api: Expose this handler as an HTTP API endpoint at
            ``POST /djust/api/<view_slug>/<handler_name>/`` with OpenAPI 3.1 schema.
            Default is False (WebSocket-only). When True, the same handler runs with
            identical validation, permissions, and rate limiting regardless of
            transport. See docs/adr/008-auto-generated-http-api-from-event-handlers.md.

    Usage:
        @event_handler
        def search(self, value: str = "", **kwargs):
            '''Search leases with debouncing'''
            self.search_query = value
            self._refresh_leases()

        @event_handler(description="Update item quantity")
        def update_item(self, item_id: int, quantity: int, **kwargs):
            # item_id and quantity are automatically coerced from strings
            self.items[item_id].quantity = quantity

        @event_handler(coerce_types=False)
        def raw_handler(self, value: str = "", **kwargs):
            # Receives raw string values from template
            pass

    Metadata Structure:
        The decorator stores comprehensive metadata in func._djust_decorators["event_handler"]:
        {
            "params": [{"name": "value", "type": "str", "required": False, "default": ""}],
            "param_names": ["value"],
            "description": "Search items",
            "accepts_kwargs": True,
            "required": [],
            "optional": ["value"],
            "coerce_types": True
        }

    Note: The @event alias is deprecated. Use @event_handler directly.
    """

    def decorator(func: F) -> F:
        # Import here to avoid circular dependency
        from djust.validation import get_handler_signature_info

        # Extract comprehensive signature information
        sig_info = get_handler_signature_info(func)

        # Use explicit params if provided, otherwise use extracted
        if params is not None:
            param_names = params
        else:
            param_names = [p["name"] for p in sig_info["params"]]

        # Use explicit description if provided, otherwise use docstring
        final_description = description or sig_info["description"]

        # Store comprehensive metadata
        _add_decorator_metadata(
            func,
            "event_handler",
            {
                "params": sig_info["params"],  # Full param info with types
                "param_names": param_names,  # Just names for quick lookup
                "description": final_description,
                "accepts_kwargs": sig_info["accepts_kwargs"],
                "required": [p["name"] for p in sig_info["params"] if p["required"]],
                "optional": [p["name"] for p in sig_info["params"] if not p["required"]],
                "coerce_types": coerce_types,  # Whether to coerce string params
                "expose_api": expose_api,  # ADR-008: expose as HTTP API endpoint
            },
        )

        return cast(F, func)

    # Support both @event_handler and @event_handler() syntaxes
    # This enables flexible usage: @event_handler vs @event_handler(description="...")
    if callable(params):
        # Called as @event_handler (no parentheses)
        # In this case, 'params' is actually the function being decorated
        func = params
        params = None
        return decorator(func)

    # Called as @event_handler() or @event_handler(params=..., description=...)
    return decorator


# Shorter alias for event_handler (deprecated)
def event(func: F) -> F:
    """
    Deprecated alias for @event_handler. Use @event_handler instead.

    .. deprecated::
        ``@event`` is deprecated and will be removed in a future release.
        Use ``@event_handler`` instead.
    """
    warnings.warn(
        "@event is deprecated. Use @event_handler instead.",
        DeprecationWarning,
        stacklevel=2,
    )
    return event_handler(func)


def is_event_handler(func: Any) -> bool:
    """
    Check if a function has been decorated with @event_handler.

    Args:
        func: The function to check.

    Returns:
        True if the function has event_handler metadata.
    """
    return bool(getattr(func, "_djust_decorators", {}).get("event_handler"))


def reactive(func: Callable) -> property:
    """
    Create a reactive property that triggers re-render on change.

    Usage:
        class MyView(LiveView):
            @reactive
            def count(self):
                return self._count

            @count.setter
            def count(self, value):
                self._count = value
    """
    # Create internal property name
    internal_name = f"_{func.__name__}_reactive"

    def _getter(self):
        return getattr(self, internal_name, None)

    def _setter(self, value):
        old_value = getattr(self, internal_name, None)
        setattr(self, internal_name, value)

        # Trigger update if value changed
        if old_value != value and hasattr(self, "update"):
            self.update()

    return property(_getter, _setter)


def state(default: Any = None):
    """
    Decorator to mark a property as reactive state.

    This provides a cleaner syntax than manually setting attributes in mount().
    The state is automatically included in the view's context and triggers
    re-renders when changed.

    Usage:
        class MyView(LiveView):
            count = state(default=0)
            message = state(default="Hello")

            @event_handler
            def increment(self):
                self.count += 1

    Args:
        default: Default value for the state property

    Returns:
        Property descriptor for the state attribute
    """

    class StateProperty:
        def __init__(self):
            self.default = default
            self.attr_name = None
            self.public_name = None

        def __set_name__(self, owner, name):
            self.attr_name = f"_state_{name}"
            self.public_name = name

        def __get__(self, obj, objtype=None):
            if obj is None:
                return self
            return getattr(obj, self.attr_name, self.default)

        def __set__(self, obj, value):
            setattr(obj, self.attr_name, value)
            # Mark this as reactive state
            if not hasattr(obj, "_reactive_state"):
                obj._reactive_state = set()
            obj._reactive_state.add(self.public_name)

    return StateProperty()


def computed(*deps):
    """
    Decorator to mark a method as a computed property.

    Two forms:

    1. **Plain** — ``@computed`` with no args. Recomputes on every access.
       Good for cheap derivations::

           @computed
           def count_doubled(self):
               return self.count * 2

    2. **Memoized** — ``@computed("items", "tax_rate")`` with explicit dependency
       attribute names. The value is cached on the instance and only recomputed
       when any of the listed dependencies' identity or shallow content
       fingerprint changes. Use for expensive derivations (large sums, DB
       aggregates, etc.)::

           @computed("items", "tax_rate")
           def total_price(self):
               subtotal = sum(i["price"] * i["qty"] for i in self.items)
               return subtotal * (1 + self.tax_rate)

    In both forms the result is a property — available in templates as a plain
    attribute::

        <div>Count: {{ count }}</div>
        <div>Total: {{ total_price }}</div>

    The memoized form stores its cache under ``self._djust_computed_cache``
    (a dict keyed by attribute name) and its last-seen dependency fingerprints
    under ``self._djust_computed_deps`` (a dict keyed by attribute name). Both
    attributes are lazily created on first access — no ``__init__`` change
    needed.
    """
    # Polymorphic call: ``@computed`` (no parens, ``deps == (func,)``) vs.
    # ``@computed("dep1", "dep2")``.
    if len(deps) == 1 and callable(deps[0]) and not isinstance(deps[0], str):
        func = deps[0]

        @functools.wraps(func)
        def _inner(self):
            return func(self)

        prop = _ComputedProperty(_inner)
        prop._is_computed = True
        prop._computed_name = func.__name__
        return cast(F, prop)

    # Memoized form: keep a per-instance cache keyed on a fingerprint of the
    # dependency values. The fingerprint uses identity + shallow content info,
    # matching what ``_snapshot_assigns`` uses elsewhere in djust.
    dep_names = tuple(deps)
    for name in dep_names:
        if not isinstance(name, str):
            raise TypeError(
                f"@computed() dependency names must be strings, got {type(name).__name__}"
            )

    def make_decorator(func):
        attr_name = func.__name__

        def _fingerprint(instance):
            parts = []
            for name in dep_names:
                v = getattr(instance, name, _MISSING)
                if v is _MISSING:
                    parts.append((name, _MISSING_TAG))
                elif isinstance(v, (int, float, bool, str, bytes)) or v is None:
                    parts.append((name, "v", v))
                elif isinstance(v, (list, tuple)):
                    parts.append((name, "seq", id(v), len(v)))
                elif isinstance(v, dict):
                    parts.append((name, "dict", id(v), len(v), tuple(v.keys())[:16]))
                elif isinstance(v, set):
                    parts.append((name, "set", id(v), len(v)))
                else:
                    parts.append((name, "id", id(v)))
            return tuple(parts)

        @functools.wraps(func)
        def _inner(self):
            cache = self.__dict__.setdefault("_djust_computed_cache", {})
            deps_seen = self.__dict__.setdefault("_djust_computed_deps", {})
            current = _fingerprint(self)
            if deps_seen.get(attr_name) != current or attr_name not in cache:
                cache[attr_name] = func(self)
                deps_seen[attr_name] = current
            return cache[attr_name]

        prop = _ComputedProperty(_inner)
        prop._is_computed = True
        prop._computed_name = attr_name
        prop._computed_deps = dep_names
        return cast(F, prop)

    return make_decorator


_MISSING = object()
_MISSING_TAG = "__djust_missing__"


class _ComputedProperty(property):
    """A ``property`` subclass that allows custom attributes for djust metadata.

    Plain ``property`` instances reject ``__setattr__`` on arbitrary names,
    which breaks ``@functools.wraps`` and our own ``_is_computed`` / ``_computed_name``
    / ``_computed_deps`` metadata. Subclassing lets the attributes live on the
    descriptor without runtime errors.
    """

    # Explicit __slots__-free class so arbitrary attributes are permitted via
    # the usual ``__dict__``; ``property`` defines ``__dict__`` on the
    # descriptor, so assignment works here.
    pass


def debounce(wait: float = 0.3, max_wait: Optional[float] = None) -> Callable[[F], F]:
    """
    Debounce event handler calls on the client side.

    This decorator adds metadata to the event handler that the JavaScript
    client uses to debounce events. Useful for input events where you want
    to wait until the user stops typing.

    Usage:
        class MyView(LiveView):
            @debounce(wait=0.5)
            def search(self, query: str = "", **kwargs):
                self.results = Product.objects.filter(name__icontains=query)

            @debounce(wait=0.5, max_wait=2.0)
            def auto_save(self, **kwargs):
                # Debounced but forced after 2 seconds
                self.save_draft()

    Args:
        wait: Seconds to wait after last event before triggering (default: 0.3)
        max_wait: Maximum seconds to wait before forcing execution (default: None)

    Returns:
        Decorator function
    """
    return _make_metadata_decorator("debounce", {"wait": wait, "max_wait": max_wait})


def throttle(
    interval: float = 0.1, leading: bool = True, trailing: bool = True
) -> Callable[[F], F]:
    """
    Throttle event handler calls on the client side.

    This decorator adds metadata to the event handler that the JavaScript
    client uses to throttle events. Useful for scroll, resize, or mouse
    move events where you want to limit how often the handler runs.

    Usage:
        class MyView(LiveView):
            @throttle(interval=0.1)
            def on_scroll(self, scroll_y: int = 0, **kwargs):
                self.scroll_position = scroll_y

            @throttle(interval=1.0, leading=True, trailing=False)
            def on_resize(self, width: int = 0, **kwargs):
                # Fire immediately on first event, ignore trailing events
                self.viewport_width = width

    Args:
        interval: Minimum interval between calls in seconds (default: 0.1)
        leading: Execute on leading edge of interval (default: True)
        trailing: Execute on trailing edge of interval (default: True)

    Returns:
        Decorator function
    """
    return _make_metadata_decorator(
        "throttle", {"interval": interval, "leading": leading, "trailing": trailing}
    )


def optimistic(func: F) -> F:
    """
    Apply optimistic updates before server validation.

    The client will update the UI instantly based on the event data,
    then apply server corrections if needed. This provides instant
    feedback for user interactions.

    Usage:
        class MyView(LiveView):
            @optimistic
            def increment(self, **kwargs):
                self.count += 1

            @optimistic
            def toggle_todo(self, todo_id: int = 0, **kwargs):
                todo = Todo.objects.get(id=todo_id)
                todo.completed = not todo.completed
                todo.save()

    The client will optimistically update the DOM based on the event data,
    then apply any corrections from the server response.

    Returns:
        Decorated function with optimistic metadata
    """

    @functools.wraps(func)
    def wrapper(*args, **kwargs):
        return func(*args, **kwargs)

    # Add standardized metadata using helper
    _add_decorator_metadata(wrapper, "optimistic", True)

    return cast(F, wrapper)


def cache(ttl: int = 60, key_params: Optional[List[str]] = None) -> Callable[[F], F]:
    """
    Cache handler responses client-side.

    Responses are cached in the browser with a TTL (time-to-live).
    Cache keys are built from the handler name plus specified parameters.

    Usage:
        class MyView(LiveView):
            @cache(ttl=60, key_params=["query"])
            def search(self, query: str = "", **kwargs):
                self.results = Product.objects.filter(name__icontains=query)

            @cache(ttl=300)  # 5 minutes, cache key is just handler name
            def get_stats(self, **kwargs):
                self.stats = expensive_calculation()

    Args:
        ttl: Cache time-to-live in seconds (default: 60)
        key_params: Parameters to include in cache key (default: [])
                   Example: ["query", "page"] creates key "search:laptop:1"

    Returns:
        Decorator function
    """
    return _make_metadata_decorator("cache", {"ttl": ttl, "key_params": key_params or []})


def client_state(keys: List[str]) -> Callable[[F], F]:
    """
    Share state via client-side StateBus (pub/sub pattern).

    When this handler executes, the specified keys are published to
    the StateBus. Other handlers decorated with @client_state and
    subscribed to the same keys will be notified of changes.

    Usage:
        class DashboardView(LiveView):
            @client_state(keys=["filter"])
            def update_filter(self, filter: str = "", **kwargs):
                # Publishes "filter" to StateBus
                self.filter = filter

            @client_state(keys=["filter"])
            def on_filter_change(self, filter: str = "", **kwargs):
                # Automatically called when "filter" changes
                self.apply_filter()

            @client_state(keys=["filter", "sort"])
            def apply_filters(self, filter: str = "", sort: str = "", **kwargs):
                # Publishes both "filter" and "sort"
                self.filter = filter
                self.sort = sort
                self.update_results()

    Args:
        keys: List of state keys to publish/subscribe
              Example: ["filter", "sort", "page"]

    Returns:
        Decorator function

    Raises:
        ValueError: If keys list is empty
    """
    if not keys:
        raise ValueError("At least one key must be specified for @client_state decorator")
    return _make_metadata_decorator("client_state", {"keys": keys})


def rate_limit(rate: float = 10, burst: int = 5) -> Callable[[F], F]:
    """
    Rate-limit a WebSocket event handler (server-side).

    Uses a per-handler token bucket. When the limit is exceeded, the event
    is dropped and the client is warned.

    Args:
        rate: Tokens per second (sustained rate).
        burst: Maximum burst capacity.

    Usage:
        class MyView(LiveView):
            @rate_limit(rate=5, burst=3)
            @event_handler
            def expensive_operation(self, **kwargs):
                ...
    """
    return _make_metadata_decorator("rate_limit", {"rate": rate, "burst": burst})


def permission_required(perm: Union[str, List[str]]) -> Callable[[F], F]:
    """
    Require Django permission(s) to call this event handler.

    Checked server-side before the handler executes. If the user lacks
    the permission, the event is rejected with "Permission denied".

    Args:
        perm: Django permission string or list of strings.

    Usage:
        class MyView(LiveView):
            @permission_required("myapp.delete_item")
            @event_handler()
            def delete_item(self, item_id: int, **kwargs):
                ...
    """
    return _make_metadata_decorator("permission_required", perm)


def background(func: F) -> F:
    """
    Run event handler in background after flushing current state.

    The decorator wraps the entire handler to run via start_async(),
    allowing immediate UI feedback (loading states) while the handler
    executes in the background.

    The current view state is flushed to the client before the handler runs,
    so any changes made before calling the handler (e.g., self.loading = True)
    are visible immediately. When the handler completes, the view re-renders
    and patches are sent.

    Both sync and async def handlers are supported.  For async handlers,
    the decorator creates a native async closure so ``_run_async_work``
    can ``await`` it directly on the event loop instead of routing through
    ``sync_to_async`` (#697).

    Usage:
        class MyView(LiveView):
            @event_handler
            @background
            def generate_content(self, prompt: str = "", **kwargs):
                '''Entire method runs in background thread.'''
                self.generating = True
                self.content = call_llm(prompt)  # slow operation
                self.generating = False

            @event_handler
            @background
            async def generate_async(self, prompt: str = "", **kwargs):
                '''Async handlers are also supported.'''
                self.generating = True
                self.content = await call_llm_async(prompt)
                self.generating = False

            def handle_async_result(self, name: str, result=None, error=None):
                '''Optional: handle completion or errors.'''
                if error:
                    self.error = f"Generation failed: {error}"

    The @background decorator can be combined with other decorators:
        @event_handler
        @debounce(wait=0.5)
        @background
        def auto_save(self, **kwargs):
            # Debounced and runs in background
            self.save_draft()
    """

    if asyncio.iscoroutinefunction(func):
        # Async handler: closure is itself async so _run_async_work can
        # detect it via iscoroutinefunction and await it directly.
        @functools.wraps(func)
        def wrapper(self, *args, **kwargs):
            async def _async_callback():
                return await func(self, *args, **kwargs)

            task_name = func.__name__
            self.start_async(_async_callback, name=task_name)

    else:
        # Sync handler: plain closure, run in thread via sync_to_async.
        @functools.wraps(func)
        def wrapper(self, *args, **kwargs):
            def _async_callback():
                return func(self, *args, **kwargs)

            task_name = func.__name__
            self.start_async(_async_callback, name=task_name)

    # Add metadata for introspection
    _add_decorator_metadata(wrapper, "background", True)

    return cast(F, wrapper)


from .hooks import on_mount  # noqa: E402 — re-export for public API


__all__ = [
    "event_handler",
    "event",
    "is_event_handler",
    "permission_required",
    "rate_limit",
    "reactive",
    "state",
    "computed",
    "debounce",
    "throttle",
    "optimistic",
    "cache",
    "client_state",
    "background",
    "on_mount",
]
