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
    """Keeps its nodelist (as Django's own parents do) but renders only the
    typed children it finds in it — a rendered text body has none."""

    def __init__(self, nodelist):
        self.nodelist = nodelist

    def render(self, context):
        children = [n for n in self.nodelist if isinstance(n, WrapNode)]
        return "children=%d:%s" % (len(children), "".join(c.render(context) for c in children))


@register.tag
def introspect2547(parser, token):
    nodelist = parser.parse(("endintrospect2547",))
    parser.delete_first_token()
    return IntrospectNode(nodelist)


class PushNode(template.Node):
    """Renders its body under a pushed variable — the shape the rendered-body
    bridge cannot honour (the body is rendered before the node runs)."""

    def __init__(self, nodelist):
        self.nodelist = nodelist

    def render(self, context):
        with context.push(who="inner"):
            return self.nodelist.render(context)


@register.tag
def pushvar2547(parser, token):
    nodelist = parser.parse(("endpushvar2547",))
    parser.delete_first_token()
    return PushNode(nodelist)


class NoEscNode(template.Node):
    def __init__(self, nodelist):
        self.nodelist = nodelist

    def render(self, context):
        previous, context.autoescape = context.autoescape, False
        try:
            return self.nodelist.render(context)
        finally:
            context.autoescape = previous


@register.tag
def noesc2547(parser, token):
    nodelist = parser.parse(("endnoesc2547",))
    parser.delete_first_token()
    return NoEscNode(nodelist)


class UpperNode(template.Node):
    """Transforms its body's OUTPUT — still a wrapper: Django, too, renders the
    nodelist to a string first."""

    def __init__(self, nodelist):
        self.nodelist = nodelist

    def render(self, context):
        return self.nodelist.render(context).upper()


@register.tag
def upper2547(parser, token):
    nodelist = parser.parse(("endupper2547",))
    parser.delete_first_token()
    return UpperNode(nodelist)


class ReadsContextNode(template.Node):
    def __init__(self, nodelist):
        self.nodelist = nodelist

    def render(self, context):
        return "%s:%s" % (context["name"], self.nodelist.render(context))


@register.tag
def ctxread2547(parser, token):
    nodelist = parser.parse(("endctxread2547",))
    parser.delete_first_token()
    return ReadsContextNode(nodelist)


@register.tag
def reqarg2547(parser, token):
    bits = token.split_contents()
    if len(bits) != 2:
        raise template.TemplateSyntaxError("reqarg2547 takes exactly one argument")
    nodelist = parser.parse(("endreqarg2547",))
    parser.delete_first_token()
    return WrapNode(nodelist)


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
