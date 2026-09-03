"""A scratch Django template library exercising every registration shape (#2547).

Loaded by ``test_load_imports_django_libraries_2547.py`` on both the plain
backend and the LiveView entry and compared byte-for-byte against Django.
"""

from django import template
from django.template.defaultfilters import stringfilter
from django.utils.html import escape, format_html
from django.utils.safestring import mark_safe

register = template.Library()


# --- filters ---------------------------------------------------------------


@register.filter
@stringfilter
def trim2547(value, num):
    return value[:num]


@register.filter(is_safe=True)
def shout2547(value):
    """`is_safe=True` on a plain-`str` return: keeps a SAFE input safe, never
    makes a hostile one safe (#2548)."""
    return "<b>%s</b>" % value


@register.filter
@mark_safe
def data_div2547(value):
    return '<div data-name="%s"></div>' % value


@register.filter(needs_autoescape=True)
def initial2547(text, autoescape=True):
    first, other = text[0], text[1:]
    esc = escape if autoescape else (lambda x: x)
    return mark_safe("<strong>%s</strong>%s" % (esc(first), esc(other)))


# --- simple_tag ------------------------------------------------------------


@register.simple_tag
def no_params2547():
    return "no_params - Expected result"


@register.simple_tag
def one_param2547(arg):
    return "one_param - Expected result: %s" % arg


@register.simple_tag(takes_context=True)
def with_context2547(context, arg):
    return "with_context (%s): %s" % (context["value"], arg)


@register.simple_tag
def one_default2547(one, two="hi"):
    return "one_default: %s, %s" % (one, two)


@register.simple_tag
def unlimited2547(one, two="hi", *args, **kwargs):
    return "%s / %s" % (
        ", ".join(str(a) for a in [one, two, *args]),
        ", ".join("%s=%s" % kv for kv in kwargs.items()),
    )


@register.simple_tag
def kwonly2547(*, kwarg=42):
    return "kwonly: %s" % kwarg


@register.simple_tag(takes_context=True)
def escape_naive2547(context):
    return "Hello {}!".format(context["name"])


@register.simple_tag(takes_context=True)
def escape_explicit2547(context):
    return escape("Hello {}!".format(context["name"]))


@register.simple_tag(takes_context=True)
def escape_format_html2547(context):
    return format_html("Hello {0}!", context["name"])


@register.simple_tag
def echo_arg2547(arg):
    """Returns its (resolved) argument unchanged — the escaping probe."""
    return arg


@register.simple_tag
def types2547(*args):
    return " ".join("%s:%r" % (type(a).__name__, a) for a in args)


@register.simple_tag(name="minustwo2547")
def minustwo_overridden(value):
    return value - 2


register.simple_tag(lambda x: x - 1, name="minusone2547")


# --- simple_block_tag ------------------------------------------------------


@register.simple_block_tag
def div2547(content, id="test"):
    return format_html("<div id='{}'>{}</div>", id, content)


@register.simple_block_tag(end_name="divend2547")
def div_custom_end2547(content):
    return format_html("<div>{}</div>", content)


@register.simple_block_tag(takes_context=True)
def escape_naive_block2547(context, content):
    return "Hello {}: {}!".format(context["name"], content)


@register.simple_block_tag
def kwonly_block2547(content, *, kwarg=42):
    return "kwonly_block (%s): %s" % (content, kwarg)


# --- inclusion_tag ---------------------------------------------------------


@register.inclusion_tag("lib2547_incl.html")
def incl_one2547(arg):
    return {"result": "incl_one: %s" % arg}


@register.inclusion_tag("lib2547_incl.html", takes_context=True)
def incl_ctx2547(context):
    return {"result": "incl_ctx: %s" % context["value"]}


# --- raw @register.tag that build their node from their own token ---------


class EchoNode(template.Node):
    def __init__(self, contents):
        self.contents = contents

    def render(self, context):
        return " ".join(self.contents)


@register.tag
def echo2547(parser, token):
    return EchoNode(token.contents.split()[1:])


register.tag("other_echo2547", echo2547)


class CounterNode(template.Node):
    def __init__(self):
        self.count = 0

    def render(self, context):
        count = self.count
        self.count = count + 1
        return str(count)


@register.tag("counter2547")
def counter(parser, token):
    return CounterNode()


class TwoKeysNode(template.Node):
    """A `get_*`-style node that writes TWO context keys and emits nothing."""

    def render(self, context):
        context["k1_2547"] = "one"
        context["k2_2547"] = mark_safe("<i>two</i>")
        return ""


@register.tag("set_two2547")
def set_two(parser, token):
    return TwoKeysNode()


class RawMarkupNode(template.Node):
    """A node that returns MARKUP as a plain `str` — Django emits it raw
    (a node's output is never re-escaped); the bridge must too. This is the
    `BlockTranslateNode` shape the #2558 amendment measured, and the row
    that goes red when the `mark_safe` on node output is gated off."""

    def render(self, context):
        return "<em>raw</em> " + context.get("name", "")


@register.tag("raw_markup2547")
def raw_markup(parser, token):
    return RawMarkupNode()


class BadNode(template.Node):
    def render(self, context):
        raise RuntimeError("I am a bad tag (2547)")


@register.tag
def badtag2547(parser, token):
    return BadNode()
