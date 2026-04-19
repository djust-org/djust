"""
Template tags for Django form integration.

Provides:

- ``{% theme_form form %}`` — render a full form with themed layout
- ``{% theme_form_errors form %}`` — render form-level non-field errors
- ``{% get_css_prefix as var %}`` — expose css_prefix for templates
"""

from django import template
from django.utils.html import conditional_escape
from django.utils.safestring import mark_safe

from ..manager import get_theme_config

register = template.Library()


def _css_prefix() -> str:
    """Return the current css_prefix from theme config."""
    return get_theme_config().get("css_prefix", "")


@register.simple_tag
def get_css_prefix():
    """
    Return the configured CSS prefix.

    Usage::

        {% load theme_form_tags %}
        {% get_css_prefix as prefix %}
        <div class="{{ prefix }}theme-input">...</div>
    """
    return _css_prefix()


@register.simple_tag(takes_context=True)
def theme_form(context, form, layout="stacked", **kwargs):
    """
    Render a complete themed form.

    Iterates over all visible fields, rendering each with its label,
    widget, help text, and field-level errors. Hidden fields are
    appended at the end. Form-level non-field errors are displayed
    at the top as a themed alert.

    Args:
        form: A Django form instance.
        layout: ``"stacked"`` (default), ``"horizontal"``, or ``"inline"``.

    Usage::

        {% load theme_form_tags %}
        {% theme_form form %}
        {% theme_form form layout="horizontal" %}
        {% theme_form form layout="inline" %}
    """
    prefix = _css_prefix()
    allowed_layouts = ("stacked", "horizontal", "inline")
    if layout not in allowed_layouts:
        layout = "stacked"
    lines = []

    # Form-level non-field errors
    if form.non_field_errors():
        lines.append(
            f'<div class="{prefix}theme-form-errors {prefix}alert {prefix}alert-destructive" role="alert">'
        )
        lines.append(f'  <ul class="{prefix}theme-error-list">')
        for error in form.non_field_errors():
            lines.append(f"    <li>{conditional_escape(error)}</li>")
        lines.append("  </ul>")
        lines.append("</div>")

    # Form container
    lines.append(f'<div class="{prefix}theme-form-{layout}">')

    # Visible fields
    for bound_field in form.visible_fields():
        lines.append(f'  <div class="{prefix}theme-form-field">')

        # Label
        field = bound_field.field
        label_text = bound_field.label
        field_id = bound_field.id_for_label
        if label_text and not isinstance(
            field.widget, template.library.InvalidTemplateLibrary if False else type(None)
        ):
            escaped_label = conditional_escape(label_text)
            use_fieldset = (
                bound_field.field.widget.use_fieldset
                if hasattr(bound_field.field.widget, "use_fieldset")
                else False
            )
            if use_fieldset:
                lines.append(f'    <legend class="{prefix}theme-label">{escaped_label}</legend>')
            else:
                lines.append(
                    f'    <label for="{field_id}" class="{prefix}theme-label">{escaped_label}</label>'
                )

        # Help text (before widget for stacked/horizontal, after for inline)
        if bound_field.help_text:
            lines.append(
                f'    <div class="{prefix}theme-help-text" id="{field_id}_helptext">'
                f"{conditional_escape(bound_field.help_text)}</div>"
            )

        # Widget
        lines.append(f"    {bound_field}")

        # Field errors
        if bound_field.errors:
            for error in bound_field.errors:
                lines.append(
                    f'    <span class="{prefix}theme-field-error" role="alert">{conditional_escape(error)}</span>'
                )

        lines.append("  </div>")

    # Hidden fields
    for hidden in form.hidden_fields():
        lines.append(f"  {hidden}")

    lines.append("</div>")

    return mark_safe("\n".join(lines))


@register.simple_tag(takes_context=True)
def theme_form_errors(context, form):
    """
    Render form-level non-field errors as a themed alert.

    Args:
        form: A Django form instance.

    Usage::

        {% load theme_form_tags %}
        {% theme_form_errors form %}
    """
    if not form.non_field_errors():
        return mark_safe("")

    prefix = _css_prefix()
    lines = [
        f'<div class="{prefix}theme-form-errors {prefix}alert {prefix}alert-destructive" role="alert">',
        f'  <ul class="{prefix}theme-error-list">',
    ]
    for error in form.non_field_errors():
        lines.append(f"    <li>{conditional_escape(error)}</li>")
    lines.append("  </ul>")
    lines.append("</div>")

    return mark_safe("\n".join(lines))
