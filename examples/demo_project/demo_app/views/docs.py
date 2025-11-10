"""Documentation view for Django Rust Live"""

from .base import BaseTemplateView


class DocsView(BaseTemplateView):
    """Static documentation page"""
    template_name = "docs.html"
