"""
Django Rust Live - Blazing fast reactive server-side rendering for Django

This package provides a Phoenix LiveView-style reactive framework for Django,
powered by Rust for maximum performance.
"""

from .live_view import LiveView, live_view
from .component import Component
from .decorators import reactive, event_handler
from ._rust import render_template, diff_html

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
