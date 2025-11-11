"""
djust - Blazing fast reactive server-side rendering for Django

This package provides a Phoenix LiveView-style reactive framework for Django,
powered by Rust for maximum performance.
"""

from .live_view import LiveView, live_view
from .component import Component
from .decorators import (
    reactive,
    event_handler,
    event,
    state,
    computed,
    debounce,
    throttle,
)
from .react import react_components, register_react_component, ReactMixin
from .forms import FormMixin, LiveViewForm, form_field

# Import Rust functions
try:
    from ._rust import render_template, diff_html, RustLiveView
except ImportError as e:
    # Fallback for when Rust extension isn't built
    import warnings

    warnings.warn(f"Could not import Rust extension: {e}. Performance will be degraded.")
    render_template = None
    diff_html = None
    RustLiveView = None

# Import Rust components (optional, requires separate build)
try:
    from . import rust_components
except ImportError:
    # Rust components not yet built - this is optional
    rust_components = None

__version__ = "0.1.0"

__all__ = [
    "LiveView",
    "live_view",
    "Component",
    "reactive",
    "event_handler",
    "event",
    "state",
    "computed",
    "debounce",
    "throttle",
    "render_template",
    "diff_html",
    "react_components",
    "register_react_component",
    "ReactMixin",
    "FormMixin",
    "LiveViewForm",
    "form_field",
]
