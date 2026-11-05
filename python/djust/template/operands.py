"""Python template objects crossing the native include/inheritance boundary."""

import logging
from typing import Any

from django.template import Context, Template
from django.template.backends.django import Template as BackendTemplate

from djust._template_guards import refused_marker

logger = logging.getLogger(__name__)


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
    """Include accepts any render-capable object, with an isolated Context.

    ``render`` is called under the template-call guard (#3815): a ``render`` that
    carries ``alters_data`` or ``do_not_call_in_templates`` is NOT called and the
    include renders the empty string, with a WARNING. Stock Django calls it; djust
    is stricter on purpose, on the rule the serializers apply (#3772) -- a template
    names the object, so a ``render`` that writes must not run because it was named.
    Fail closed: a marker read that raises refuses too.
    """
    if isinstance(value, BackendTemplate):
        value = value.template
    marker = refused_marker(value.render)
    if marker is not None:
        logger.warning(
            "{%% include %%}: %s.render() is marked %s and was not called (#3815)",
            type(value).__name__,
            marker,
        )
        return ""
    return value.render(Context(data, autoescape=autoescape))
