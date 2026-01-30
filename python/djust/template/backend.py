"""
Django template backend engine for djust.

Provides the DjustTemplateBackend class that integrates with Django's
template engine framework.
"""

from pathlib import Path
from typing import Any, Dict, List

from django.template import TemplateDoesNotExist, Origin
from django.template.backends.base import BaseEngine

from .rendering import DjustTemplate


class DjustTemplateBackend(BaseEngine):
    """
    Django template backend using djust's Rust rendering engine.

    Benefits:
    - 10-100x faster rendering than Django templates
    - Sub-millisecond template compilation
    - Automatic template caching
    - Compatible with Django template syntax

    Limitations:
    - Not all Django template tags/filters supported yet
    - Custom template tags not supported
    - See djust documentation for supported features
    """

    app_dirname = "templates"

    def __init__(self, params: Dict[str, Any]):
        """Initialize the Djust template backend."""
        params = params.copy()
        options = params.pop("OPTIONS").copy()
        super().__init__(params)

        self.context_processors = options.pop("context_processors", [])

        # Build list of template directories
        self.template_dirs = self._get_template_dirs(
            params.get("DIRS", []), params.get("APP_DIRS", False)
        )

        # Check if Rust rendering is available
        try:
            from djust._rust import render_template, render_template_with_dirs

            self._render_fn = render_template
            self._render_fn_with_dirs = render_template_with_dirs
        except ImportError as e:
            raise ImportError(
                "djust Rust extension not available. "
                "Make sure djust is properly installed with: pip install -e ."
            ) from e

    def _get_template_dirs(self, configured_dirs: List, app_dirs: bool) -> List[Path]:
        """Get list of directories to search for templates."""
        template_dirs = [Path(d) for d in configured_dirs]

        if app_dirs:
            from django.apps import apps

            for app_config in apps.get_app_configs():
                template_dir = Path(app_config.path) / self.app_dirname
                if template_dir.is_dir():
                    template_dirs.append(template_dir)

        return template_dirs

    def from_string(self, template_code: str):
        """
        Create a template from a string.

        Args:
            template_code: Template source code

        Returns:
            DjustTemplate instance
        """
        return DjustTemplate(template_code, backend=self)

    def get_template(self, template_name: str):
        """
        Load a template by name.

        Searches through template directories in order until the template
        is found.

        Args:
            template_name: Name of template to load (e.g., 'home.html')

        Returns:
            DjustTemplate instance

        Raises:
            TemplateDoesNotExist: If template not found
        """
        for template_dir in self.template_dirs:
            template_path = template_dir / template_name
            if template_path.is_file():
                try:
                    with open(template_path, "r", encoding="utf-8") as f:
                        template_code = f.read()
                    origin = Origin(
                        name=str(template_path),
                        template_name=template_name,
                        loader=self,
                    )
                    return DjustTemplate(template_code, backend=self, origin=origin)
                except OSError as e:
                    raise TemplateDoesNotExist(template_name) from e

        # Template not found in any directory
        tried = [str(d / template_name) for d in self.template_dirs]
        raise TemplateDoesNotExist(
            template_name,
            tried=tried,
            backend=self,
        )
