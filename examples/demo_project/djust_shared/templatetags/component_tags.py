"""
Custom template tags for rendering components without |safe filter.

Usage:
    {% load component_tags %}
    {% component hero %}
    {% component code_block %}
"""

from django import template
from django.utils.safestring import mark_safe

register = template.Library()


@register.simple_tag
def component(html_string):
    """
    Render a component HTML string as safe HTML.

    Usage:
        {% load component_tags %}
        {% component hero %}

    This is equivalent to {{ hero|safe }} but clearer and more semantic.
    """
    return mark_safe(html_string)


@register.simple_tag(takes_context=True)
def components(context, *names):
    """
    Render multiple components at once.

    Usage:
        {% load component_tags %}
        {% components 'hero' 'subtitle' 'code' %}

    This will render context['hero'], context['subtitle'], context['code']
    in order, all marked as safe HTML.
    """
    html_parts = []
    for name in names:
        if name in context:
            html_parts.append(str(context[name]))
    return mark_safe('\n'.join(html_parts))


@register.inclusion_tag('components/wrapper.html')
def component_section(component_html, classes=""):
    """
    Wrap a component in a section with optional classes.

    Usage:
        {% load component_tags %}
        {% component_section hero classes="mt-4" %}
    """
    return {
        'component_html': mark_safe(component_html),
        'classes': classes
    }
