"""A library only ever reached through ``OPTIONS['builtins']`` (#2547) — no
test ``{% load %}``s it, so its tag being usable proves the builtins path."""

from django import template

register = template.Library()


@register.simple_tag
def builtin_only2547():
    return "builtin_only - Expected result"
