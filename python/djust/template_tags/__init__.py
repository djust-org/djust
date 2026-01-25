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
from typing import Any, Dict, List, Type

logger = logging.getLogger(__name__)

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

        # Named parameters: key=value
        if "=" in arg:
            key, value = arg.split("=", 1)
            return (key.strip(), self._resolve_arg(value, context))

        # Context variable (may be dot-separated like post.slug)
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


def register(name: str):
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
            logger.debug(f"Registered template tag handler: {name}")
        except ImportError as e:
            logger.warning(
                f"Could not register tag handler '{name}': Rust extension not available ({e})"
            )
        except Exception as e:
            logger.error(f"Failed to register tag handler '{name}': {e}")

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
def _register_builtins():
    """Register built-in tag handlers."""
    # Import handlers to trigger their @register decorators
    try:
        from . import url  # noqa: F401
        from . import static  # noqa: F401
    except ImportError as e:
        logger.debug(f"Could not import built-in handlers: {e}")


# Register on module load
_register_builtins()
