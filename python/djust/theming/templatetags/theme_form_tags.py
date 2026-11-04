"""
Template tags for Django form integration.

Provides:

- ``{% theme_form form %}`` — render a full form with themed layout
- ``{% theme_form_errors form %}`` — render form-level non-field errors
- ``{% get_css_prefix as var %}`` — expose css_prefix for templates
"""

from typing import Any

from django import template
from django.forms import BaseForm
from django.template import Context
from django.utils.html import format_html
from django.utils.safestring import SafeString, mark_safe

from ..manager import get_theme_config

register = template.Library()


def _css_prefix() -> str:
    """Return the current css_prefix from theme config."""
    return str(get_theme_config().get("css_prefix", ""))


@register.simple_tag
def get_css_prefix() -> str:
    """
    Return the configured CSS prefix.

    Usage::

        {% load theme_form_tags %}
        {% get_css_prefix as prefix %}
        <div class="{{ prefix }}theme-input">...</div>
    """
    return _css_prefix()


@register.simple_tag(takes_context=True)
def theme_form(
    context: Context, form: BaseForm, layout: str = "stacked", **kwargs: Any
) -> SafeString:
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
    # Every line is a constant or the result of ``format_html`` (it escapes each value it
    # is given), so what ``mark_safe`` joins at the end holds no raw value. ``prefix`` is
    # config, ``field_id`` is built from the FIELD NAME, the form prefix and ``auto_id``
    # (a form builder can put a quote there): neither is trusted markup (#3766).
    lines = []

    # Form-level non-field errors
    if form.non_field_errors():
        lines.append(
            format_html(
                '<div class="{0}theme-form-errors {0}alert {0}alert-destructive" role="alert">',
                prefix,
            )
        )
        lines.append(format_html('  <ul class="{}theme-error-list">', prefix))
        for error in form.non_field_errors():
            lines.append(format_html("    <li>{}</li>", error))
        lines.append("  </ul>")
        lines.append("</div>")

    # Form container (``layout`` is one of ``allowed_layouts``; ``format_html`` anyway)
    lines.append(format_html('<div class="{}theme-form-{}">', prefix, layout))

    # Visible fields
    for bound_field in form.visible_fields():
        lines.append(format_html('  <div class="{}theme-form-field">', prefix))

        # Label
        field = bound_field.field
        label_text = bound_field.label
        field_id = bound_field.id_for_label
        # Skip the label block when the field has no label (`label_text` empty)
        # OR when there's explicitly no widget attached. `type(None)` handles the
        # no-widget case; the earlier expression included a dead `if False` branch
        # referencing `template.library.InvalidTemplateLibrary` that was never
        # reached — dropped for clarity.
        if label_text and not isinstance(field.widget, type(None)):
            use_fieldset = (
                bound_field.field.widget.use_fieldset
                if hasattr(bound_field.field.widget, "use_fieldset")
                else False
            )
            if use_fieldset:
                lines.append(
                    format_html('    <legend class="{}theme-label">{}</legend>', prefix, label_text)
                )
            else:
                lines.append(
                    format_html(
                        '    <label for="{}" class="{}theme-label">{}</label>',
                        field_id,
                        prefix,
                        label_text,
                    )
                )

        # Help text (before widget for stacked/horizontal, after for inline)
        if bound_field.help_text:
            lines.append(
                format_html(
                    '    <div class="{}theme-help-text" id="{}_helptext">{}</div>',
                    prefix,
                    field_id,
                    bound_field.help_text,
                )
            )

        # Widget (a ``BoundField`` renders itself as marked-safe HTML)
        lines.append(format_html("    {}", bound_field))

        # Field errors
        if bound_field.errors:
            for error in bound_field.errors:
                lines.append(
                    format_html(
                        '    <span class="{}theme-field-error" role="alert">{}</span>',
                        prefix,
                        error,
                    )
                )

        lines.append("  </div>")

    # Hidden fields
    for hidden in form.hidden_fields():
        lines.append(format_html("  {}", hidden))

    lines.append("</div>")

    return mark_safe("\n".join(lines))


@register.simple_tag(takes_context=True)
def theme_form_errors(context: Context, form: BaseForm) -> SafeString:
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
        format_html(
            '<div class="{0}theme-form-errors {0}alert {0}alert-destructive" role="alert">', prefix
        ),
        format_html('  <ul class="{}theme-error-list">', prefix),
    ]
    for error in form.non_field_errors():
        lines.append(format_html("    <li>{}</li>", error))
    lines.append("  </ul>")
    lines.append("</div>")

    return mark_safe("\n".join(lines))
