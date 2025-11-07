"""
Django Rust Live - Blazing fast reactive server-side rendering for Django

This package provides a Phoenix LiveView-style reactive framework for Django,
powered by Rust for maximum performance.
"""

from .live_view import LiveView, live_view
from .component import Component
from .decorators import reactive, event_handler

# Import Rust functions
try:
    from .django_rust_live import render_template, diff_html, RustLiveView
except ImportError as e:
    # Fallback for when Rust extension isn't built
    import warnings
    warnings.warn(f"Could not import Rust extension: {e}. Performance will be degraded.")
    render_template = None
    diff_html = None
    RustLiveView = None

__version__ = "0.1.0"

__all__ = [
    "LiveView",
    "live_view",
    "Component",
    "reactive",
    "event_handler",
    "render_template",
    "diff_html",
]
