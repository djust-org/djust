"""
Django static file template tag handler for djust.

This module provides the {% static %} template tag handler that integrates
Django's static file handling with djust's Rust template engine.

Usage in templates:
    {% static 'css/style.css' %}
    {% static image_path %}
"""

import logging
from typing import List, Dict, Any

from . import TagHandler, register

logger = logging.getLogger(__name__)


@register("static")
class StaticTagHandler(TagHandler):
    """
    Handler for the {% static %} template tag.

    Resolves static file URLs using Django's static file handling.

    Note: The Rust template engine already has built-in support for static tags
    via Node::Static. This handler serves as a fallback and demonstration of
    the registry pattern, and may be used if the built-in handler is disabled
    or for custom static file handling.

    Examples
    --------
    ```django
    {# CSS file #}
    <link href="{% static 'css/style.css' %}" rel="stylesheet">

    {# JavaScript file #}
    <script src="{% static 'js/app.js' %}"></script>

    {# Image with dynamic path #}
    <img src="{% static image_path %}" alt="Image">
    ```
    """

    def render(self, args: List[str], context: Dict[str, Any]) -> str:
        """
        Render the static file URL.

        Parameters
        ----------
        args : list
            Single argument: the static file path (quoted string or variable).

        context : dict
            Template context (for variable resolution).

        Returns
        -------
        str
            The full static file URL.
        """
        if not args:
            logger.warning("{% static %} tag requires a file path")
            return ""

        # Resolve the path argument
        path = self._resolve_arg(args[0], context)
        if isinstance(path, str):
            path = path.strip("'\"")

        try:
            from django.templatetags.static import static

            return static(path)
        except ImportError:
            # Fallback: try to use STATIC_URL from settings
            try:
                from django.conf import settings

                static_url = getattr(settings, "STATIC_URL", "/static/")
                # Ensure proper joining of URL parts
                if static_url.endswith("/"):
                    return f"{static_url}{path}"
                else:
                    return f"{static_url}/{path}"
            except ImportError:
                logger.error("Django not installed - cannot resolve static URLs")
                return f"/static/{path}"
        except Exception as e:
            logger.error(f"Error resolving static path '{path}': {e}")
            return f"/static/{path}"
