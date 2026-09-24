"""A third-party tag that keeps per-render state in ``render_context``
(#3053 review of #3044)."""

from django import template

register = template.Library()


class CountNode(template.Node):
    def render(self, context):
        n = context.render_context.get(self, 0) + 1
        context.render_context[self] = n
        return str(n)


@register.tag
def counter3053(parser, token):
    return CountNode()
