"""
Live Demos - interactive examples showcasing Django Rust Live features
"""

from django.views.generic import TemplateView


class DemosIndexView(TemplateView):
    """
    Live demos index page showing all available interactive examples.

    Note: This is a regular Django TemplateView (not LiveView) because it's
    a static page using Django template inheritance.
    """
    template_name = 'demos/index.html'
