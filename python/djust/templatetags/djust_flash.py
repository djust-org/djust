"""
Django template tags for flash message support.

Provides a container element that receives server-pushed flash messages
via WebSocket.

Usage:
    {% load djust_flash %}

    <!-- Basic flash container -->
    {% dj_flash %}

    <!-- Custom auto-dismiss timeout (ms, 0 = no auto-dismiss) -->
    {% dj_flash auto_dismiss=8000 %}

    <!-- With a position hint CSS class -->
    {% dj_flash position="top-right" %}
"""

from django import template
from django.utils.html import format_html

register = template.Library()


@register.simple_tag
def dj_flash(auto_dismiss: int = 5000, position: str = "") -> str:
    """
    Render the flash message container element.

    Flash messages pushed by ``put_flash()`` on the server are rendered
    inside this container by the client JS.  The container is marked with
    ``dj-update="ignore"`` so morphdom does not clobber active flash
    messages during DOM patches.

    Args:
        auto_dismiss: Milliseconds before a flash message auto-dismisses.
            Set to ``0`` to disable auto-dismiss.  Default ``5000``.
        position: Optional CSS class hint appended to the container
            (e.g. ``"top-right"``).

    Returns:
        Safe HTML string for the container ``<div>``.
    """
    css_class = "dj-flash-container"
    if position:
        css_class = "{} dj-flash-{}".format(css_class, position)

    return format_html(
        '<div id="dj-flash-container" class="{}" dj-update="ignore"'
        ' data-dj-auto-dismiss="{}" aria-live="polite" role="status"></div>',
        css_class,
        auto_dismiss,
    )
