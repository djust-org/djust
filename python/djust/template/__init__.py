"""
Django template backend for djust's Rust rendering engine.

This package provides the template backend, rendering, and serialization
utilities for djust's high-performance Rust template engine.
"""

from .backend import DjustTemplateBackend
from .exceptions import DjustTemplateSyntaxError
from .rendering import DjustTemplate
from .serialization import serialize_context, serialize_value

__all__ = [
    "DjustTemplate",
    "DjustTemplateBackend",
    "DjustTemplateSyntaxError",
    "serialize_context",
    "serialize_value",
]
