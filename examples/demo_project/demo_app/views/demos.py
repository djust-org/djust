"""
Live Demos - interactive examples showcasing Django Rust Live features
"""

from .base import BaseTemplateView


class DemosIndexView(BaseTemplateView):
    """
    Live demos index page showing all available interactive examples.

    Note: This inherits from BaseTemplateView (not LiveView) because it's
    a static page, but it gets the navbar component automatically.
    """
    template_name = 'demos/index.html'
