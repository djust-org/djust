"""Documentation view for Django Rust Live"""

from django.views.generic import TemplateView


class DocsView(TemplateView):
    """Static documentation page"""
    template_name = "docs.html"
