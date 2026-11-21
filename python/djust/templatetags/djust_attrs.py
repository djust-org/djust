"""Attribute filters for djust's own page templates (#4157).

Usage::

    {% load djust_attrs %}
    <a href="{{ next_url|djust_url_attr }}">

Django's autoescape (``conditional_escape``) writes a marked-safe value as it is.
That is right in a text slot and wrong in an attribute, where a value marked safe
for text closes the attribute. A non-URL attribute uses ``|force_escape``; a URL
attribute (``href``, ``action``, ``src``, ``formaction``) uses ``djust_url_attr``,
which also neutralises a ``javascript:`` / ``vbscript:`` / ``data:`` URL.

The filter name is prefixed: ``template_filters.bootstrap_django_filters`` bridges
custom filters to the Rust engine by bare name, so a project's own ``url_attr``
filter would collide with an unprefixed one.
"""

from typing import Any

from django import template
from django.utils.safestring import SafeString, mark_safe

from djust.components.utils import data_url_attr

register = template.Library()


@register.filter
def djust_url_attr(value: Any) -> SafeString:
    """``value`` ready for a quoted ``href`` / ``action`` / ``src``: escaped once, and a URL
    whose scheme is not a navigation scheme becomes ``#``.

    A marked-safe value is judged and escaped like any other (``data_url_attr``); only the
    literal no-op hrefs ``javascript:void(0)`` / ``javascript:;`` pass. The result is
    escaped, so it is marked safe for the template (neither engine escapes it again).
    """
    return mark_safe(data_url_attr(value))  # noqa: S308 - data_url_attr escapes
