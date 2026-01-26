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

    def _get_template_dirs(self) -> list:
        """Get template directories from Django settings for {% include %} support."""
        from django.conf import settings
        from pathlib import Path

        template_dirs = []

        # Add DIRS from all TEMPLATES configs
        for template_config in settings.TEMPLATES:
            if "DIRS" in template_config:
                template_dirs.extend(template_config["DIRS"])

        # Add app template directories (only for DjangoTemplates with APP_DIRS=True)
        for template_config in settings.TEMPLATES:
            if template_config["BACKEND"] == "django.template.backends.django.DjangoTemplates":
                if template_config.get("APP_DIRS", False):
                    from django.apps import apps

                    for app_config in apps.get_app_configs():
                        templates_dir = Path(app_config.path) / "templates"
                        if templates_dir.exists():
                            template_dirs.append(str(templates_dir))

        return [str(d) for d in template_dirs]

    def render_template(self):
        """Render using Rust backend"""
        if RustLiveView and render_template_with_dirs and self.template:
            try:
                context = self.get_context_data()
                template_dirs = self._get_template_dirs()
                return render_template_with_dirs(self.template, context, template_dirs)
            except Exception as e:
                return f"<div>Error: {e}</div>"
        return "<div>Rust backend not available</div>"

    def get(self, request, *args, **kwargs):
        """Handle GET requests"""
        self.mount(request, **kwargs)
        html = self.render_template()
        return HttpResponse(html)
