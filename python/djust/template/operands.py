"""Python template objects crossing the native include/inheritance boundary."""

from typing import Any

from django.template import Context, Template
from django.template.backends.django import Template as BackendTemplate


def template_source(value: Any) -> tuple[str, str | None, str, Any] | None:
    """Return source and origin only for supported compiled template types."""
    from .rendering import DjustTemplate

    if isinstance(value, BackendTemplate):
        value = value.template
    if not isinstance(value, (Template, DjustTemplate)):
        return None
    origin = value.origin
    return (
        value.source,
        getattr(origin, "template_name", None),
        getattr(origin, "name", "<unknown source>"),
        value._compiled_template if isinstance(value, DjustTemplate) else None,
    )


def render_template_object(value: Any, data: dict[str, Any], autoescape: bool) -> str:
    """Include accepts any render-capable object, with an isolated Context."""
    if isinstance(value, BackendTemplate):
        value = value.template
    return value.render(Context(data, autoescape=autoescape))


def is_safe_dynamic_template_object(value: Any) -> bool:
    """Prove a dynamic Python template has only safe built-in output nodes.

    Application values can still execute their normal Django callable or
    attribute resolution; template node subclasses, filters and control tags
    cannot be verified without running their behavior and are rejected.
    """
    from django.template.base import (
        FilterExpression,
        Node,
        NodeList,
        TextNode,
        Variable,
        VariableNode,
    )

    def bound_method(obj: Any, name: str, expected: Any) -> bool:
        return getattr(getattr(obj, name, None), "__func__", None) is expected

    if type(value) is BackendTemplate:
        value = value.template
    if type(value) is not Template:
        return False
    if not bound_method(value, "render", Template.render) or not bound_method(
        value, "_render", Template._render
    ):
        return False
    nodes = value.nodelist
    if type(nodes) is not NodeList or not bound_method(nodes, "render", NodeList.render):
        return False
    for node in nodes:
        if type(node) is TextNode:
            if (
                type(node.s) is not str
                or not bound_method(node, "render", TextNode.render)
                or not bound_method(node, "render_annotated", TextNode.render_annotated)
            ):
                return False
        elif type(node) is VariableNode:
            expression = node.filter_expression
            if (
                not bound_method(node, "render", VariableNode.render)
                or not bound_method(node, "render_annotated", Node.render_annotated)
                or type(expression) is not FilterExpression
                or not bound_method(expression, "resolve", FilterExpression.resolve)
                or type(expression.filters) is not list
                or expression.filters
                or expression.is_var is not True
                or type(expression.var) is not Variable
                or not bound_method(expression.var, "resolve", Variable.resolve)
            ):
                return False
        else:
            return False
    return True
