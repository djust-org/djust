"""A raw ``@register.tag`` that CONSUMES A BODY — the shape #2547 refuses
loudly, per tag, when a template uses it (the raw-body registration kind is
#2558) — next to a simple tag and a filter that the same ``{% load %}`` must
still bridge."""

from django import template

register = template.Library()


@register.simple_tag
def sibling2547():
    return "sibling - Expected result"


@register.filter
def sibling_filter2547(value):
    return "[%s]" % value


class WrapNode(template.Node):
    def __init__(self, nodelist):
        self.nodelist = nodelist

    def render(self, context):
        return "[" + self.nodelist.render(context) + "]"


@register.tag
def wrapblock2547(parser, token):
    nodelist = parser.parse(("endwrapblock2547",))
    parser.delete_first_token()
    return WrapNode(nodelist)
