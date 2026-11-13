"""An app library that defines its own tag under a name 2.0 removed from djust (#4002)."""

from django import template

register = template.Library()


@register.simple_tag
def calendar() -> str:
    return "APP-CALENDAR"
