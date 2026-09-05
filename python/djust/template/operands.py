"""Python template objects crossing the native include/inheritance boundary."""

from typing import Any

from django.template import Context, Template
from django.template.backends.django import Template as BackendTemplate


def template_source(value: Any) -> tuple[str, str | None, str, Any] | None:
    """Return source and origin only for supported compiled template types."""
    from .rendering import DjustTemplate

    if isinstance(value, BackendTemplate):
        value = value.template
    if not isinstance(value, (Template, DjustTemplate)):
        return None
    origin = value.origin
    return (
        value.source,
        getattr(origin, "template_name", None),
        getattr(origin, "name", "<unknown source>"),
        value._compiled_template if isinstance(value, DjustTemplate) else None,
    )


def render_template_object(value: Any, data: dict[str, Any], autoescape: bool) -> str:
    """Include accepts any render-capable object, with an isolated Context."""
    if isinstance(value, BackendTemplate):
        value = value.template
    return value.render(Context(data, autoescape=autoescape))
