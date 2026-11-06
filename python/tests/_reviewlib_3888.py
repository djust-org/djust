"""A tiny template library for the #3888 review-round tests: the generic helpers an application
installs (a filter that walks a dotted attribute path, a tag that reads one attribute), used to
prove that a Python reader handed a carried model gets the floored object and nothing behind it."""

from django import template

register = template.Library()


@register.filter
def dotted(value, path):
    """Walk ``path`` with ``getattr``; answers ``ERR:<type>`` instead of raising."""
    try:
        for part in path.split("."):
            value = getattr(value, part)
        return value
    except Exception as exc:  # noqa: BLE001 - the helper reports, the test asserts
        return "ERR:" + type(exc).__name__


@register.simple_tag
def attr_of(obj, name):
    try:
        return getattr(obj, name)
    except Exception as exc:  # noqa: BLE001
        return "ERR:" + type(exc).__name__


@register.filter
def kind(value):
    return type(value).__name__
