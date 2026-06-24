"""
Simplified LiveView for initial testing
"""

from typing import Any, Dict, Optional

from django.conf import settings
from django.http import HttpRequest, HttpResponse
from django.views import View

try:
    from ._rust import render_template_with_dirs

    _RUST_AVAILABLE = True
except ImportError:
    _RUST_AVAILABLE = False

from .utils import get_template_dirs


class LiveView(View):
    """Simple LiveView using Rust backend"""

    template: Optional[str] = None

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self._rust_view: Any = None

    def mount(self, request: HttpRequest, **kwargs: Any) -> None:
        """Override to set initial state"""
        pass

    def get_context_data(self) -> Dict[str, Any]:
        """Get context for rendering"""
        context: Dict[str, Any] = {}
        for key in dir(self):
            if not key.startswith("_") and not callable(getattr(self, key)):
                if key not in ["template"]:
                    context[key] = getattr(self, key)
        return context

    def render_template(self) -> str:
        """Render using Rust backend"""
        if _RUST_AVAILABLE and self.template:
            try:
                context = self.get_context_data()
                return str(render_template_with_dirs(self.template, context, get_template_dirs()))
            except Exception as e:
                if settings.DEBUG:
                    return f"<div>Template error: {e}</div>"
                return "<div>An error occurred rendering this view.</div>"
        return "<div>Rust backend not available</div>"

    def get(self, request: HttpRequest, *args: Any, **kwargs: Any) -> HttpResponse:
        """Handle GET requests"""
        self.mount(request, **kwargs)
        html = self.render_template()
        return HttpResponse(html)
