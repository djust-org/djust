"""
Django template backend engine for djust.

Provides the DjustTemplateBackend class that integrates with Django's
template engine framework.
"""

import logging
from pathlib import Path
from os.path import abspath
from typing import Any, Dict, List

from django.template import TemplateDoesNotExist, Origin
from django.conf import settings
from django.template.backends.base import BaseEngine

from .rendering import DjustTemplate

logger = logging.getLogger(__name__)


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
    - See djust documentation for supported features

    ``{% load app_tags %}`` imports the project's Django template library and
    bridges its tags and filters into the Rust engine (#2547). ``OPTIONS``
    accepts Django's ``libraries`` (label → dotted path, added to the
    installed-app discovery) and ``builtins`` (dotted paths bridged at
    construction, no ``{% load %}`` needed), with the same meaning they have
    on ``DjangoTemplates``.
    """

    app_dirname = "templates"

    def __init__(self, params: Dict[str, Any]):
        """Initialize the Djust template backend."""
        params = params.copy()
        options = params.pop("OPTIONS").copy()
        super().__init__(params)

        self.context_processors = options.pop("context_processors", [])

        # Django's ``OPTIONS['string_if_invalid']`` (#2517): what ``{{ missing }}``
        # renders. Default ``""`` — render nothing — exactly as ``Engine`` has it.
        # A non-empty value RETURNS from the variable node without running the
        # filter chain, which is Django's own control flow; see
        # ``Context::string_if_invalid_for``.
        self.string_if_invalid: str = str(options.pop("string_if_invalid", "") or "")

        # `debug` (#2518): Django defaults it to `settings.DEBUG`.
        self.debug: bool = bool(options.pop("debug", getattr(settings, "DEBUG", False)))

        # Match Django's engine default; render-time Context settings can
        # override it. Context dictionary keys never configure escaping.
        self.autoescape = bool(options.pop("autoescape", True))

        # Anything still in OPTIONS is a key djust does not implement. Django
        # raises `ImproperlyConfigured` for an unknown option; staying silent is
        # what let the four keys above go unnoticed.
        if options:
            logger.warning(
                "DjustTemplateBackend: unsupported TEMPLATES OPTIONS key(s) %s — ignored",
                ", ".join(sorted(options)),
            )

        # Django's `OPTIONS['libraries']` / `OPTIONS['builtins']`, with the
        # meaning `DjangoTemplates` gives them (#2547). `libraries` extends
        # the `{% load %}` name map; `builtins` are bridged now.
        self.template_libraries: Dict[str, str] = dict(options.pop("libraries", {}) or {})
        self.template_builtins: List[str] = list(options.pop("builtins", []) or [])
        from ..template_libraries import register_backend_libraries

        register_backend_libraries(self.template_libraries, self.template_builtins)

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

    def from_string(self, template_code: str) -> DjustTemplate:
        """
        Create a template from a string.

        Args:
            template_code: Template source code

        Returns:
            DjustTemplate instance
        """
        return DjustTemplate(template_code, backend=self)

    def get_template(self, template_name: str) -> DjustTemplate:
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
                        name=abspath(template_path),
                        template_name=template_name,
                        loader=self,
                    )
                    return DjustTemplate(template_code, backend=self, origin=origin)
                except OSError as e:
                    raise TemplateDoesNotExist(template_name) from e

        # Template not found in any directory
        tried = [
            (
                Origin(name=abspath(d / template_name), template_name=template_name, loader=self),
                "Source does not exist",
            )
            for d in self.template_dirs
        ]
        raise TemplateDoesNotExist(
            template_name,
            tried=tried,
            backend=self,
        )
