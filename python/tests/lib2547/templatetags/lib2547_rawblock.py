"""Raw ``@register.tag``s that CONSUME A BODY (#2547, ADR-030), next to a
simple tag and a filter the same ``{% load %}`` must still bridge.

``wrapblock2547`` is a plain WRAPPER — one ``parser.parse(("end…",))`` and a
node that renders its body as-is — and is bridged through the rendered-body
path. ``twoseg2547`` (an intermediate ``{% sep2547 %}``) and
``introspect2547`` (a node that keeps only typed children) are the two shapes
the registration probe must refuse."""

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


class TwoSegNode(template.Node):
    def __init__(self, first, second):
        self.first = first
        self.second = second

    def render(self, context):
        return "<%s|%s>" % (self.first.render(context), self.second.render(context))


@register.tag
def twoseg2547(parser, token):
    first = parser.parse(("sep2547",))
    parser.delete_first_token()
    second = parser.parse(("endtwoseg2547",))
    parser.delete_first_token()
    return TwoSegNode(first, second)


class IntrospectNode(template.Node):
    def __init__(self, nodelist):
        self.children = [n for n in nodelist if isinstance(n, WrapNode)]

    def render(self, context):
        return "children=%d" % len(self.children)


@register.tag
def introspect2547(parser, token):
    nodelist = parser.parse(("endintrospect2547",))
    parser.delete_first_token()
    return IntrospectNode(nodelist)


@register.tag
def optelse2547(parser, token):
    """Stops at either an optional ``{% else2547 %}`` or the end tag. The probe
    never sees the ``else`` branch, so the wrapper check must refuse this on
    the stop set alone — a real template with the branch would otherwise
    hand the Rust engine a stray tag inside a rendered body."""
    nodelist = parser.parse(("else2547", "endoptelse2547"))
    token = parser.next_token()
    if token.contents == "else2547":
        parser.parse(("endoptelse2547",))
        parser.delete_first_token()
    return WrapNode(nodelist)
