"""
Flash message container tag handler for djust's Rust template engine.

Handles ``{% dj_flash %}`` so the flash container renders correctly
when templates are processed by the Rust renderer.

Usage in templates::

    {% dj_flash %}
    {% dj_flash auto_dismiss=8000 %}
    {% dj_flash position="top-right" %}
"""

import logging
import re
from typing import Any, Dict, List

from django.utils.html import format_html

from . import TagHandler, register

logger = logging.getLogger(__name__)


@register("dj_flash")
class DjFlashTagHandler(TagHandler):
    """
    Render the ``#dj-flash-container`` element for flash messages.

    Mirrors the Django template tag in ``djust.templatetags.djust_flash``
    but runs inside the Rust template engine.
    """

    def render(self, args: List[str], context: Dict[str, Any]) -> str:
        auto_dismiss = 5000
        position = ""

        for arg in args:
            resolved = self._resolve_arg(arg, context)
            if isinstance(resolved, tuple):
                key, value = resolved
                if key == "auto_dismiss":
                    try:
                        auto_dismiss = int(value)
                    except (ValueError, TypeError):
                        pass  # Keep default auto_dismiss if value isn't numeric
                elif key == "position":
                    position = str(value).strip("'\"")

        css_class = "dj-flash-container"
        if position:
            # Sanitize position: only allow alphanumeric and hyphens
            safe_position = re.sub(r"[^a-zA-Z0-9-]", "", position)
            css_class = f"{css_class} dj-flash-{safe_position}"

        return format_html(
            '<div id="dj-flash-container" class="{}" dj-update="ignore"'
            ' data-dj-auto-dismiss="{}" aria-live="polite" role="status"></div>',
            css_class,
            auto_dismiss,
        )
