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
from typing import Any, Dict, List

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
                        pass
                elif key == "position":
                    position = str(value).strip("'\"")

        css_class = "dj-flash-container"
        if position:
            css_class = f"{css_class} dj-flash-{position}"

        return (
            f'<div id="dj-flash-container" class="{css_class}" dj-update="ignore"'
            f' data-dj-auto-dismiss="{auto_dismiss}" aria-live="polite" role="status"></div>'
        )
