"""
Django template backend for djust's Rust rendering engine.

This package provides the template backend, rendering, and serialization
utilities for djust's high-performance Rust template engine.
"""

from .backend import DjustTemplateBackend
from .rendering import DjustTemplate
from .serialization import serialize_value, serialize_context

__all__ = [
    "DjustTemplateBackend",
    "DjustTemplate",
    "serialize_value",
    "serialize_context",
]
