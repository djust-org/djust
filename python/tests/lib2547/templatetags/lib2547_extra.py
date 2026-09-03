"""A second scratch library, for ``{% load a b %}`` and cross-library filters."""

from django import template

register = template.Library()


@register.filter
def twice2547(value):
    return "%s%s" % (value, value)


@register.simple_tag
def extra_tag2547(arg):
    return "extra: %s" % arg
