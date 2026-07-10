"""
Template Tag Handler Registry for djust.

This module provides a Python API for registering custom template tag handlers
that are called from the Rust template engine. This enables Django-specific tags
like {% url %} and {% static %} to work seamlessly with djust's fast rendering.

Usage
-----
```python
from djust.template_tags import TagHandler, register

@register("url")
class UrlTagHandler(TagHandler):
    def render(self, args: list, context: dict) -> str:
        from django.urls import reverse
        url_name = args[0].strip("'\"")
        return reverse(url_name)
```

Architecture
------------
The registry uses a Rust-Python callback pattern:

1. Parser encounters unknown tag (e.g., {% url 'name' %})
2. Checks if Python handler is registered for "url"
3. If yes, creates CustomTag node
4. At render time, Rust calls Python handler with:
   - args: List of arguments from the tag
   - context: Dictionary of template context
5. Handler returns string to insert in output

Performance
-----------
- Built-in tags (if, for, block): Zero overhead (native Rust)
- Custom tags (url, static): ~15-50µs per call (GIL + callback)

This overhead is acceptable for typical templates and provides full
compatibility with Django's URL resolution and static file handling.
"""

import logging
import re
from typing import Any, Callable, ClassVar, Dict, List, Optional, Set, Type

logger = logging.getLogger(__name__)

# A template-tag argument, as produced by the template tokenizer, is a
# whitespace-delimited token: a bare dotted-identifier path (``block.text``),
# a ``key=value`` kwarg (``tables=False``), a quoted literal, or an int. The
# Rust custom-tag dispatch, however, *pre-resolves* bare-name variable args to
# their VALUE before handing them to the Python handler (renderer.rs
# ``Node::CustomTag``). A resolved value (e.g. Markdown source text) is NOT a
# token, and re-running the kwarg-split / dotted-lookup heuristics on it
# corrupts any value containing ``=`` (tuple-split → ``str((k, v))`` repr) or a
# leading dotted segment that happens to match a context key (#2037). These
# patterns gate the heuristics so a non-token value is returned verbatim.
_KWARG_TOKEN_RE = re.compile(r"^[A-Za-z_]\w*=")  # ``key=`` — identifier then '='
_VAR_TOKEN_RE = re.compile(r"^[A-Za-z_]\w*(\.\w+)*$")  # dotted-identifier path

# Track registered handlers for debugging
_registered_handlers: Dict[str, "TagHandler"] = {}


class TagHandler:
    """
    Base class for custom template tag handlers.

    Subclass this and implement the `render` method to create a handler
    for a custom template tag.

    Example
    -------
    ```python
    class MyTagHandler(TagHandler):
        def render(self, args: list, context: dict) -> str:
            return f"Hello, {args[0]}!"
    ```
    """

    def render(self, args: List[str], context: Dict[str, Any]) -> str:
        """
        Render the template tag and return the output string.

        Parameters
        ----------
        args : list
            Arguments from the template tag. String literals include their quotes.
            For {% url 'post' post.slug %}, args would be ["'post'", "my-slug"]
            (second arg already resolved by Rust if it was a variable).

        context : dict
            The full template context as a dictionary. Can be used for additional
            variable resolution if needed.

        Returns
        -------
        str
            The rendered output to insert in the template.
        """
        raise NotImplementedError(f"{self.__class__.__name__} must implement render(args, context)")

    def _resolve_arg(self, arg: str, context: Dict[str, Any]) -> Any:
        """
        Resolve an argument value from the context.

        Handles:
        - String literals ('value' or "value") -> returns stripped string
        - Integer literals (123) -> returns int
        - Context variables (post.slug) -> returns resolved value
        - Named parameters (key=value) -> returns (key, resolved_value)

        Parameters
        ----------
        arg : str
            The argument string from the template tag

        context : dict
            The template context

        Returns
        -------
        Any
            The resolved value
        """
        arg = arg.strip()

        # String literals
        if (arg.startswith("'") and arg.endswith("'")) or (
            arg.startswith('"') and arg.endswith('"')
        ):
            return arg[1:-1]

        # Integer literals
        if arg.lstrip("-").isdigit():
            return int(arg)

        # Named parameters: key=value — only when the arg is syntactically a
        # kwarg TOKEN (bare identifier immediately followed by '='). A value
        # that merely CONTAINS '=' (e.g. Rust-resolved Markdown source text
        # "x = y") must not be tuple-split (#2037 double-resolution).
        if _KWARG_TOKEN_RE.match(arg):
            key, value = arg.split("=", 1)
            return (key.strip(), self._resolve_arg(value, context))

        # Context variable (dot-separated like post.slug) — only when the arg
        # is a bare dotted-identifier TOKEN. A non-token string (whitespace,
        # markup, newlines) is a value the Rust dispatch already resolved; it
        # is returned verbatim rather than re-resolved against the context
        # (which would corrupt values whose first segment matches a key).
        if not _VAR_TOKEN_RE.match(arg):
            return arg

        parts = arg.split(".")
        result = context.get(parts[0])

        for part in parts[1:]:
            if result is None:
                return arg  # Return original if lookup fails
            if isinstance(result, dict):
                result = result.get(part)
            elif hasattr(result, part):
                result = getattr(result, part)
            else:
                return arg  # Return original if lookup fails

        return result if result is not None else arg


class AssignTagHandler(TagHandler):
    """
    Base class for *assign* (context-mutating) template tag handlers.

    Unlike :class:`TagHandler` (which emits an HTML string), an assign
    tag handler mutates the template context: its ``render`` returns a
    ``dict`` whose keys become context variables visible to the sibling
    nodes that follow the tag. This mirrors Django tags like
    ``{% regroup ... as var %}`` and ``{% with ... %}``.

    Operand-resolution contract (``RESOLVE_ARG_POSITIONS``)
    ------------------------------------------------------
    Arg resolution happens **in the Rust engine before ``render`` is
    called**: the assign-tag dispatch (``renderer.rs`` —
    ``resolve_assign_tag_args``, the single entry point shared by all four
    dispatch sites) resolves selected args via ``resolve_tag_arg``,
    JSON-encoding structured (list/object) values so a source list arrives
    as a JSON string.

    Which positions get resolved is governed by the class attribute
    :attr:`RESOLVE_ARG_POSITIONS`:

    * ``None`` (the default) — resolve **every** arg against the context,
      the historical behavior, kept for any handler that doesn't opt in.
    * a ``set[int]`` of 0-based positions — resolve **only** those
      positions; every other arg is passed through as a **literal token**.

    Passing keyword/name operands unresolved matches Django, which never
    resolves an assign tag's ``by`` / ``<attr>`` / ``as`` / ``<var>``
    operands against the outer context — only the source expression. This
    closes the operand-shadowing footgun (#2041): before it, a context key
    named like the ``<attr>`` token (djust auto-exposes public view attrs)
    shadowed the per-item lookup, silently corrupting the grouping. See
    :class:`~djust.template_tags.regroup.RegroupTagHandler`, which declares
    ``RESOLVE_ARG_POSITIONS = {0}`` (resolve only the source expression).
    """

    #: 0-based arg positions the Rust engine should resolve against the
    #: render context before calling ``render``; the rest arrive as literal
    #: tokens. ``None`` = resolve every arg (default). Read once at
    #: registration time by ``register_assign_tag_handler`` (#2041).
    RESOLVE_ARG_POSITIONS: ClassVar[Optional[Set[int]]] = None

    def render(self, args: List[str], context: Dict[str, Any]) -> Dict[str, Any]:  # type: ignore[override]
        """Return a mapping of context updates to merge for later siblings."""
        raise NotImplementedError(
            f"{self.__class__.__name__} must implement render(args, context) -> dict"
        )


def register_assign(name: str) -> Callable[[Type[AssignTagHandler]], Type[AssignTagHandler]]:
    """
    Decorator to register an *assign* (context-mutating) tag handler.

    The handler is instantiated and registered with the Rust template
    engine via ``register_assign_tag_handler`` when the decorated class
    is defined. Its ``render(args, context)`` must return a
    ``dict[str, Any]`` of context updates (or ``None`` for a no-op).

    Parameters
    ----------
    name : str
        The tag name (e.g., "regroup").
    """

    def decorator(handler_class: Type[AssignTagHandler]) -> Type[AssignTagHandler]:
        try:
            from djust._rust import register_assign_tag_handler

            handler = handler_class()
            register_assign_tag_handler(name, handler)
            _registered_handlers[name] = handler
            logger.debug("Registered assign tag handler: %s", name)
        except ImportError as e:
            logger.warning(
                "Could not register assign tag handler '%s': Rust extension not available (%s)",
                name,
                e,
            )
        except Exception as e:
            logger.error("Failed to register assign tag handler '%s': %s", name, e)

        return handler_class

    return decorator


def register(name: str) -> Callable[[Type[TagHandler]], Type[TagHandler]]:
    """
    Decorator to register a tag handler class.

    The handler is instantiated and registered with the Rust template engine
    when the decorated class is defined.

    Parameters
    ----------
    name : str
        The tag name (e.g., "url", "static")

    Example
    -------
    ```python
    @register("url")
    class UrlTagHandler(TagHandler):
        def render(self, args, context):
            return reverse(args[0])
    ```
    """

    def decorator(handler_class: Type[TagHandler]) -> Type[TagHandler]:
        try:
            from djust._rust import register_tag_handler

            handler = handler_class()
            register_tag_handler(name, handler)
            _registered_handlers[name] = handler
            logger.debug("Registered template tag handler: %s", name)
        except ImportError as e:
            logger.warning(
                "Could not register tag handler '%s': Rust extension not available (%s)", name, e
            )
        except Exception as e:
            logger.error("Failed to register tag handler '%s': %s", name, e)

        return handler_class

    return decorator


def get_registered_handlers() -> Dict[str, "TagHandler"]:
    """
    Get a dictionary of all registered handlers.

    Returns
    -------
    dict
        Mapping of tag names to handler instances
    """
    return _registered_handlers.copy()


def is_registered(name: str) -> bool:
    """
    Check if a handler is registered for a tag name.

    Parameters
    ----------
    name : str
        The tag name to check

    Returns
    -------
    bool
        True if a handler is registered
    """
    try:
        from djust._rust import has_tag_handler

        return has_tag_handler(name)
    except ImportError:
        return name in _registered_handlers


# Auto-register built-in handlers on import
def _register_builtins() -> None:
    """Register built-in tag handlers."""
    # Import handlers to trigger their @register decorators
    try:
        from . import url  # noqa: F401
        from . import static  # noqa: F401
        from . import pwa  # noqa: F401
        from . import templatetag  # noqa: F401
        from . import flash  # noqa: F401
        from . import markdown  # noqa: F401
        from . import client_config  # noqa: F401
        from . import live_render  # noqa: F401  # #1145
        from . import regroup  # noqa: F401  # Django {% regroup %} parity
    except ImportError as e:
        logger.debug("Could not import built-in handlers: %s", e)


def reregister_builtins() -> None:
    """Re-assert the built-in ``djust.template_tags`` handlers with the Rust
    registry (idempotent).

    ``@register`` / ``@register_assign`` run once, on first import. The
    Rust tag/assign registries are process-global and shared across an
    xdist worker, so a test that calls ``clear_tag_handlers()`` /
    ``clear_assign_tag_handlers()`` leaves the built-ins (``url``,
    ``static``, ``regroup`` …) gone for the rest of the worker. This
    re-registers every already-instantiated built-in handler from
    ``_registered_handlers`` to its correct registry, mirroring the
    theme/component ``register_with_rust_engine`` restore path (#1928).
    No-op without the Rust extension.
    """
    try:
        from djust._rust import register_assign_tag_handler, register_tag_handler
    except ImportError:
        return
    for name, handler in list(_registered_handlers.items()):
        try:
            if isinstance(handler, AssignTagHandler):
                register_assign_tag_handler(name, handler)
            else:
                register_tag_handler(name, handler)
        except Exception as e:  # noqa: BLE001 — restore must never break a test
            logger.debug("Could not re-register built-in tag handler '%s': %s", name, e)


# Register on module load
_register_builtins()
