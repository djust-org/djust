"""
Decorators for LiveView event handlers and reactive properties

These decorators make LiveView code more elegant and explicit by marking
event handlers, reactive state, and computed properties.
"""

import asyncio
import functools
import logging
import threading
import warnings
from typing import Callable, Any, TypeVar, Union, cast, List, Optional


logger = logging.getLogger(__name__)

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
    serialize: Optional[Union[Callable[..., Any], str]] = None,
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
        serialize: Optional per-handler override for the HTTP API response shape.
            Only applies when ``expose_api=True``. Either a callable or the name of
            a method on the view. When set, replaces the handler's return value with
            ``serializer(...)`` on the HTTP transport only; the WebSocket path is
            unaffected (zero serialization overhead).

            Callable arity is auto-detected: 0-arg is called as ``fn()``, 1-arg as
            ``fn(view)``, 2-or-more-arg as ``fn(view, handler_return_value)``.

            When the view defines an ``api_response(self)`` method *and* no
            per-handler ``serialize=`` is set, the convention method is called
            instead — giving DRY zero-wiring for views whose API handlers all
            return the same shape. Resolution order on HTTP:
            ``serialize=`` > ``api_response()`` > handler return value.

            Raises ``TypeError`` at decoration time if ``serialize`` is set but
            ``expose_api`` is False.

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

        if serialize is not None and not expose_api:
            raise TypeError(
                "@event_handler(serialize=...) requires expose_api=True. "
                "The serializer only runs on the HTTP transport; setting it "
                "without exposing the handler over HTTP is almost certainly a bug."
            )

        # Mutual-exclusion guard with @server_function — a single handler
        # cannot be both a WebSocket/re-render event and an RPC/no-re-render
        # call. Catching the misuse at decoration time beats a silent 404
        # at runtime (dispatch_server_function would reject the function).
        if getattr(func, "_djust_decorators", {}).get("server_function"):
            raise TypeError(
                f"@event_handler cannot be combined with @server_function on "
                f"{getattr(func, '__name__', repr(func))!r}. A function cannot "
                f"be both an @event_handler (WebSocket/re-render) and an "
                f"@server_function (RPC/no-re-render). Pick one."
            )

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
                "serialize": serialize,  # ADR-008 follow-up: per-handler HTTP response override
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


def action(
    func: Optional[F] = None,
    *,
    description: str = "",
    coerce_types: bool = True,
) -> Any:
    """
    Mark a method as a Server Action with auto-tracked pending/error/result state.

    React 19's ``useActionState`` and form actions provide a pattern where form
    submissions automatically handle pending states, error states, and results
    without per-handler boilerplate. ``@action`` is the djust equivalent: any
    method decorated with ``@action`` automatically populates
    ``self._action_state[<method_name>]`` with::

        {
            "pending": True,    # set BEFORE the handler body runs
            "error":   None,
            "result":  None,
        }

    On successful return::

        {
            "pending": False,
            "error":   None,
            "result":  <return_value>,
        }

    On exception (caught and recorded; **not** re-raised)::

        {
            "pending": False,
            "error":   "<str(exc)>",
            "result":  None,
        }

    The exception is **logged** at ERROR level (via ``logger.exception``)
    so diagnostics aren't lost, but is **not** re-raised. Re-raising
    would route the dispatcher to its exception path, which sends an
    ``{"type": "error"}`` frame to the client — and that frame
    short-circuits the re-render. The template would never see the
    ``error`` field. See #1276. ``BaseException`` subclasses
    (``KeyboardInterrupt``, ``SystemExit``, ``GeneratorExit``) still
    propagate by Python convention.

    Templates access this via context injection — each action's name becomes a
    context variable: ``{{ create_todo.pending }}``, ``{{ create_todo.result }}``,
    ``{{ create_todo.error }}``. Works in both the Django template engine and
    the Rust template engine (dict attribute access resolves identically).

    ``@action`` builds on ``@event_handler`` — every action is also an event
    handler, with the same parameter coercion, permissions, and rate-limit
    machinery. The difference is the action-state tracking layer; you don't
    write try/except + manual state-setting in every mutation handler.

    Pairs with the v0.8.0 client-side ``dj-form-pending`` attribute (from PR
    #1023): ``dj-form-pending`` covers the in-flight client UX (during the
    network round-trip), ``@action`` covers the post-completion server state
    (after the handler returns). Together they give React 19-level form
    ergonomics with zero per-handler wiring.

    Args:
        func: When ``@action`` is used bare (without parentheses), the
            decorated function is passed directly.
        description: Human-readable description (passes through to the
            underlying ``@event_handler``; overrides docstring).
        coerce_types: Whether to coerce string params to expected types
            (passes through to ``@event_handler``).

    Usage::

        from djust.decorators import action

        class TodoView(LiveView):
            @action
            def create_todo(self, title: str = "", **kwargs):
                # While this body runs, self._action_state["create_todo"] is
                # {"pending": True, "error": None, "result": None}.
                # In the template (re-rendered after this returns), it will
                # be {"pending": False, "error": None, "result": <return_val>}.
                if not title:
                    raise ValueError("Title is required")
                todo = Todo.objects.create(title=title, user=self.request.user)
                self.todos.append(todo)
                return {"created": todo.id}

    Template usage::

        {% if create_todo.error %}
            <div class="error">{{ create_todo.error }}</div>
        {% elif create_todo.result %}
            <div class="success">
                Todo {{ create_todo.result.created }} created!
            </div>
        {% endif %}

    Metadata Structure:
        Stores ``_djust_decorators["action"] = {"name": <method_name>}`` AND
        ``_djust_decorators["event_handler"]`` (since every action is also an
        event handler). The ``"action"`` metadata key is the marker the
        dispatch pipeline reads to populate ``_action_state``.
    """

    def _build(func_inner: F) -> F:
        action_name = func_inner.__name__

        # Bare-call: @action decorator (no parens) → use the underlying
        # @event_handler with default args + tag with action metadata. We
        # call event_handler() to get the wrapper, then add the "action"
        # key to its metadata.
        eh_decorator = event_handler(
            description=description,
            coerce_types=coerce_types,
        )

        @functools.wraps(func_inner)
        def action_wrapper(self: Any, *args: Any, **kwargs: Any) -> Any:
            # Initialize/reset the action state at entry. Visible to the
            # template if any re-render fires DURING the handler body —
            # which only happens for handlers that yield to the event loop
            # (async handlers, handlers using `self.start_async()`, etc).
            # Synchronous handlers that don't yield never expose pending=True
            # to a renderer, but the field is still useful for the
            # post-completion state shape.
            if not hasattr(self, "_action_state") or self._action_state is None:
                # Defensive: LiveView.__init__ initializes this, but a
                # subclass __init__ that forgets super().__init__() would
                # break us; create on demand.
                self._action_state = {}

            self._action_state[action_name] = {
                "pending": True,
                "error": None,
                "result": None,
            }

            try:
                result = func_inner(self, *args, **kwargs)
                self._action_state[action_name] = {
                    "pending": False,
                    "error": None,
                    "result": result,
                }
                return result
            except Exception as exc:
                # Record the error in the action state and let the dispatcher
                # re-render normally so the template can show
                # ``{{ <name>.error }}``. Closes #1276 — the previous
                # behavior was ``raise`` here, which routed the dispatcher
                # to its exception-frame path and bypassed re-render.
                #
                # The exception is still logged so diagnostics aren't lost.
                # ``BaseException`` subclasses (KeyboardInterrupt, SystemExit,
                # GeneratorExit) propagate via the bare ``except Exception``
                # — by Python convention those should never be caught.
                logger.exception(
                    "@action %s raised %s; recorded in _action_state[%r]",
                    action_name,
                    type(exc).__name__,
                    action_name,
                )
                self._action_state[action_name] = {
                    "pending": False,
                    "error": str(exc) or exc.__class__.__name__,
                    "result": None,
                }
                return None

        # Apply @event_handler to our wrapper, then tag with the "action"
        # metadata key. event_handler stores its own metadata under
        # _djust_decorators["event_handler"]; we add ours alongside.
        wrapped = eh_decorator(action_wrapper)
        _add_decorator_metadata(wrapped, "action", {"name": action_name})
        return cast(F, wrapped)

    # Support both `@action` (bare) and `@action(description="…")` (called).
    if func is None:
        # Called form: @action(...)
        return _build
    # Bare form: @action
    return _build(func)


def is_action(func: Any) -> bool:
    """Check if a function has been decorated with ``@action``."""
    return bool(getattr(func, "_djust_decorators", {}).get("action"))


def server_function(
    description: Any = "",
    coerce_types: bool = True,
) -> Any:
    """Mark a method as a same-origin browser RPC target (v0.7.0).

    The method becomes callable from the client as
    ``await djust.call('<view_slug>', '<method_name>', {params})`` and its
    return value is JSON-serialized straight back to the caller — no VDOM
    re-render, no assigns diff.

    Session-cookie auth + CSRF are both required unconditionally. The
    dispatch pipeline reuses the ADR-008 validators: parameter coercion via
    the signature, ``@permission_required`` gating, and ``@rate_limit``
    token-bucket limits.

    ``@server_function`` differs from ``@event_handler(expose_api=True)`` in
    intent: no OpenAPI export, no external-caller contract, no
    ``api_response`` / ``serialize=`` hooks. It is designed exclusively for
    in-browser RPC. A function cannot be both an event handler and a server
    function — decoration fails with ``TypeError`` at import time.

    Args:
        description: Optional human-readable description (overrides docstring).
        coerce_types: Coerce string params to the method's typed signature.
            Default True.

    Usage::

        from djust.decorators import server_function

        class ProductView(LiveView):
            @server_function
            def search(self, q: str = "", **kwargs) -> list[dict]:
                return [{"id": p.id, "name": p.name}
                        for p in Product.objects.filter(name__icontains=q)[:10]]

    On the client::

        const hits = await djust.call('myapp.productview', 'search', {q: 'chair'});

    When stacking with ``@permission_required``, put ``@server_function``
    OUTERMOST (topmost in source), otherwise the metadata is attached to the
    inner wrapper and the dispatcher cannot see it.
    """

    def decorator(func):
        from djust.validation import get_handler_signature_info

        if getattr(func, "_djust_decorators", {}).get("event_handler"):
            raise TypeError(
                f"@server_function cannot be combined with @event_handler on "
                f"{getattr(func, '__name__', repr(func))!r}. A function cannot "
                f"be both an @event_handler (WebSocket/re-render) and an "
                f"@server_function (RPC/no-re-render). Pick one."
            )

        sig_info = get_handler_signature_info(func)
        _desc = description if isinstance(description, str) else ""
        _add_decorator_metadata(
            func,
            "server_function",
            {
                "params": sig_info["params"],
                "param_names": [p["name"] for p in sig_info["params"]],
                "description": _desc or sig_info["description"],
                "accepts_kwargs": sig_info["accepts_kwargs"],
                "required": [p["name"] for p in sig_info["params"] if p["required"]],
                "optional": [p["name"] for p in sig_info["params"] if not p["required"]],
                "coerce_types": coerce_types,
            },
        )
        return func

    # Support both @server_function (no parens) and @server_function(...) syntaxes.
    if callable(description):
        func = description
        return decorator(func)
    return decorator


def is_server_function(func: Any) -> bool:
    """
    Check if a function has been decorated with @server_function.

    Args:
        func: The function to check.

    Returns:
        True if the function has server_function metadata.
    """
    return bool(getattr(func, "_djust_decorators", {}).get("server_function"))


class _ReactiveProperty:
    """Descriptor for @reactive properties with __set_name__ validation (#1287).

    Validates at class-definition time that the host class has an ``update()``
    method, rather than silently no-opping at runtime when it doesn't.
    """

    def __init__(self, fget, fset=None):
        self.fget = fget
        self.fset = fset

    def __set_name__(self, owner, name):
        if not hasattr(owner, "update"):
            raise TypeError(
                f"@reactive property '{name}' on {owner.__name__} requires "
                f"the host class to have an 'update()' method (typically "
                f"inherited from LiveView)."
            )

    def __get__(self, obj, objtype=None):
        if obj is None:
            return self
        return self.fget(obj)

    def __set__(self, obj, value):
        if self.fset is None:
            raise AttributeError("can't set attribute")
        old_value = self.fget(obj)
        self.fset(obj, value)
        if old_value != value:
            obj.update()

    def setter(self, fset):
        return type(self)(self.fget, fset)


def reactive(func: Callable):
    """
    Create a reactive property that triggers re-render on change.

    Usage::

        class MyView(LiveView):
            @reactive
            def count(self):
                return self._count

            @count.setter
            def count(self, value):
                self._count = value

    The host class must have an ``update()`` method (inherited from
    ``LiveView``).  A ``TypeError`` is raised at class-definition time if
    it doesn't, rather than silently no-opping at runtime.
    """
    internal_name = f"_{func.__name__}_reactive"

    def _getter(self):
        return getattr(self, internal_name, None)

    def _setter(self, value):
        setattr(self, internal_name, value)

    prop = _ReactiveProperty(_getter, _setter)
    prop.__doc__ = func.__doc__
    return prop


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
            lock = self.__dict__.get("_djust_computed_lock")
            if lock is None:
                lock = self.__dict__.setdefault("_djust_computed_lock", threading.Lock())
            with lock:
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

    **Return values are discarded.**  The handler's return value is not
    captured — ``start_async()`` discards it.  Mutate ``self.<attr>`` to
    surface results to templates, or combine with ``@action`` for
    ``_action_state`` tracking (the ``@action`` decorator populates
    ``_action_state[name]`` with ``{pending, error, result}``).

    The @background decorator can be combined with other decorators:
        @event_handler
        @debounce(wait=0.5)
        @background
        def auto_save(self, **kwargs):
            # Debounced and runs in background
            self.save_draft()

    Combining ``@background`` and ``@action``:
        @event_handler
        @background
        @action
        def slow_op(self, **kwargs):
            ...

    When combined, ``@action`` catches ``Exception`` and records it in
    ``self._action_state[name]["error"]`` (does **not** re-raise — see
    @action docs).  Because ``@action`` never re-raises, ``@background``'s
    ``start_async`` never sees the exception — ``handle_async_result`` is
    called with ``error=None`` even when the handler body raised.  The
    error signal is ``_action_state[name]["error"]``, NOT
    ``handle_async_result``'s ``error`` parameter.  Checks that only
    inspect ``handle_async_result`` for errors will miss ``@action``
    failures.
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
    "server_function",
    "is_server_function",
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
