"""A scratch library for the `{% load %}`-bridged rows of #2556: the tag and
filter shapes whose escaping Django decides from ``context.autoescape``."""

from django import template
from django.utils.safestring import mark_safe

register = template.Library()


@register.simple_tag(name="lib2556_tag")
def lib_tag(value):
    # A plain ``str`` carrying the value: ``SimpleNode.render`` applies
    # ``conditional_escape`` only when ``context.autoescape`` is true.
    return "[" + str(value) + "]"


@register.simple_tag(name="lib2556_safe_tag")
def lib_safe_tag(value):
    # Marked safe by the tag itself: raw under both policies.
    return mark_safe("<i>" + str(value) + "</i>")


@register.simple_block_tag(name="lib2556_block")
def lib_block(content, value):
    # ``SimpleBlockNode``: the body arrives rendered (safe), the tag's own
    # return is a plain ``str`` — escaped only under ``on``.
    return "{" + str(value) + ":" + str(content) + "}"


@register.filter(name="lib2556_needs", needs_autoescape=True)
def lib_needs(value, autoescape=True):
    # ``linebreaksbr``'s shape: the flag is the filter's to read.
    return "%s|ae=%s" % (value, autoescape)
