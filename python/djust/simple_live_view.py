"""
Simplified LiveView for initial testing
"""

from django.views import View
from django.http import HttpResponse

try:
    from ._rust import RustLiveView, render_template_with_dirs
except ImportError:
    RustLiveView = None
    render_template_with_dirs = None

from .utils import get_template_dirs


class LiveView(View):
    """Simple LiveView using Rust backend"""

    template = None

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self._rust_view = None

    def mount(self, request, **kwargs):
        """Override to set initial state"""
        pass

    def get_context_data(self):
        """Get context for rendering"""
        context = {}
        for key in dir(self):
            if not key.startswith("_") and not callable(getattr(self, key)):
                if key not in ["template"]:
                    context[key] = getattr(self, key)
        return context

    def render_template(self):
        """Render using Rust backend"""
        if RustLiveView and render_template_with_dirs and self.template:
            try:
                context = self.get_context_data()
                return render_template_with_dirs(self.template, context, get_template_dirs())
            except Exception as e:
                return f"<div>Error: {e}</div>"
        return "<div>Rust backend not available</div>"

    def get(self, request, *args, **kwargs):
        """Handle GET requests"""
        self.mount(request, **kwargs)
        html = self.render_template()
        return HttpResponse(html)
