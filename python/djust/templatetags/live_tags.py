"""
Django template tags for LiveView forms.

These tags provide a cleaner syntax for rendering LiveView forms with
automatic validation, error display, and framework-specific styling.

Usage:
    {% load live_tags %}

    <!-- Render entire form -->
    {% live_form view %}

    <!-- Render single field (view-based, with FormMixin) -->
    {% live_field view "field_name" %}

    <!-- Render with options -->
    {% live_field view "email" label="Email Address" wrapper_class="custom-class" %}

    <!-- Standalone field (no view required) -->
    {% live_field "text" handler="set_note_subject" value=note_form_subject placeholder="Brief subject line..." %}
    {% live_field "textarea" handler="set_note_body" value=note_form_body rows="4" %}
    {% live_field "select" handler="set_note_type" value=note_form_type choices=note_type_choices %}
"""

from django import template
from django.utils.html import escape
from django.utils.safestring import mark_safe
from typing import Any

register = template.Library()

# Field types that use <input> with dj-input
_INPUT_TYPES = frozenset({"text", "password", "email", "number", "tel", "url"})

# All recognised standalone field type names (used to disambiguate the
# one-positional-arg form from the two-positional-arg view-based form).
_STANDALONE_FIELD_TYPES = _INPUT_TYPES | frozenset({"textarea", "select"})


def _get_field_css_class() -> str:
    """Return the CSS class for form fields from djust config, falling back to 'form-input'."""
    try:
        from djust.config import config

        css_class = config.get_framework_class("field_class")
        if css_class:
            return css_class
    except Exception:
        pass
    return "form-input"


def _build_attr_string(attrs: dict) -> str:
    """Build a safely-escaped HTML attribute string from a dict.

    Values are escaped; keys are assumed to be safe (developer-provided
    attribute names like ``placeholder``, ``rows``, ``id``).
    """
    parts: list[str] = []
    for key, value in attrs.items():
        if value is None:
            continue
        # Boolean / valueless attributes (e.g. required="")
        escaped = escape(str(value))
        parts.append(f'{key}="{escaped}"')
    return " ".join(parts)


def _render_standalone_field(field_type: str, **kwargs) -> str:
    """Render a standalone ``<input>``, ``<textarea>``, or ``<select>`` element."""
    handler = kwargs.pop("handler", "")
    value = kwargs.pop("value", "")
    choices = kwargs.pop("choices", None)
    css_class = _get_field_css_class()

    if value is None:
        value = ""

    if field_type == "textarea":
        attrs: dict[str, Any] = {"class": css_class}
        if handler:
            attrs["dj-input"] = handler
        # Merge extra kwargs (rows, id, placeholder, etc.)
        attrs.update(kwargs)
        attr_str = _build_attr_string(attrs)
        escaped_value = escape(str(value))
        return mark_safe(f"<textarea {attr_str}>{escaped_value}</textarea>")

    if field_type == "select":
        attrs = {"class": css_class}
        if handler:
            attrs["dj-change"] = handler
        attrs.update(kwargs)
        attr_str = _build_attr_string(attrs)

        options_html = ""
        if choices:
            for choice_value, choice_label in choices:
                escaped_cv = escape(str(choice_value))
                escaped_cl = escape(str(choice_label))
                selected = " selected" if str(choice_value) == str(value) else ""
                options_html += f'<option value="{escaped_cv}"{selected}>{escaped_cl}</option>'

        return mark_safe(f"<select {attr_str}>{options_html}</select>")

    # Default: <input> types (text, password, email, number, tel, url)
    attrs = {"class": css_class, "type": field_type}
    if handler:
        attrs["dj-input"] = handler
    if value:
        attrs["value"] = str(value)
    attrs.update(kwargs)
    attr_str = _build_attr_string(attrs)
    return mark_safe(f"<input {attr_str}>")


@register.simple_tag
def live_form(view, **kwargs):
    """
    Render an entire form automatically using the configured CSS framework.

    Args:
        view: The LiveView instance (must use FormMixin)
        **kwargs: Rendering options passed to as_live()
            - framework: Override the configured CSS framework
            - render_labels: Whether to render field labels (default: True)
            - render_help_text: Whether to render help text (default: True)
            - render_errors: Whether to render errors (default: True)
            - auto_validate: Whether to add validation on change (default: True)
            - wrapper_class: Custom wrapper class for each field

    Returns:
        HTML string for the entire form

    Example:
        {% load live_tags %}
        <form @submit="submit_form">
            {% live_form view %}
            <button type="submit">Submit</button>
        </form>
    """
    if not hasattr(view, "as_live"):
        return "<!-- ERROR: View does not have as_live() method. Did you use FormMixin? -->"

    return view.as_live(**kwargs)


@register.simple_tag
def live_field(*args, **kwargs):
    """
    Render a form field — either view-based or standalone.

    **View-based** (two positional args, requires FormMixin):
        {% live_field view "email" %}
        {% live_field view "password" label="Custom Password Label" %}

    **Standalone** (one positional arg, field type string):
        {% live_field "text" handler="set_name" value=name_val placeholder="Name..." %}
        {% live_field "textarea" handler="set_body" value=body_val rows="4" %}
        {% live_field "select" handler="set_type" value=type_val choices=type_choices %}
        {% live_field "password" handler="set_pw" placeholder="Enter password..." %}

    Standalone mode renders ``<input>``, ``<textarea>``, or ``<select>`` elements
    with the appropriate ``dj-input`` / ``dj-change`` binding and CSS class from
    the djust config (falling back to ``"form-input"``).

    Supported input types: text, password, email, number, tel, url.
    Extra kwargs are passed through as HTML attributes.
    """
    # --- Dispatch: standalone vs view-based ---
    if len(args) == 1 and isinstance(args[0], str) and args[0] in _STANDALONE_FIELD_TYPES:
        return _render_standalone_field(args[0], **kwargs)

    # View-based: (view, field_name)
    if len(args) >= 2:
        view, field_name = args[0], args[1]
        if not hasattr(view, "as_live_field"):
            return (
                "<!-- ERROR: View does not have as_live_field() method. Did you use FormMixin? -->"
            )
        return view.as_live_field(field_name, **kwargs)

    # Single arg that isn't a recognised field type — assume it's a view missing field_name
    if len(args) == 1:
        return (
            "<!-- ERROR: live_field requires either a field type string or (view, field_name) -->"
        )

    return "<!-- ERROR: live_field requires at least one argument -->"


@register.simple_tag
def live_errors(view, field_name: str = None):
    """
    Render form errors for a specific field or all non-field errors.

    Args:
        view: The LiveView instance (must use FormMixin)
        field_name: Optional field name. If None, renders non-field errors.

    Returns:
        HTML string for the errors

    Example:
        {% load live_tags %}
        {% live_errors view "email" %}
        {% live_errors view %}  <!-- non-field errors -->
    """
    if field_name:
        if hasattr(view, "get_field_errors"):
            errors = view.get_field_errors(field_name)
            if errors:
                html = '<div class="invalid-feedback d-block">'
                for error in errors:
                    html += f"<div>{error}</div>"
                html += "</div>"
                return html
    else:
        if hasattr(view, "form_errors") and view.form_errors:
            html = '<div class="alert alert-danger">'
            for error in view.form_errors:
                html += f"<div>{error}</div>"
            html += "</div>"
            return html

    return ""


@register.filter
def field_value(view, field_name: str) -> Any:
    """
    Get the current value of a form field.

    Args:
        view: The LiveView instance (must use FormMixin)
        field_name: Name of the field

    Returns:
        Current field value

    Example:
        {% load live_tags %}
        <input type="text" value="{{ view|field_value:'email' }}">
    """
    if hasattr(view, "get_field_value"):
        return view.get_field_value(field_name)
    return ""


@register.filter
def has_errors(view, field_name: str) -> bool:
    """
    Check if a field has validation errors.

    Args:
        view: The LiveView instance (must use FormMixin)
        field_name: Name of the field

    Returns:
        True if field has errors, False otherwise

    Example:
        {% load live_tags %}
        <input class="{% if view|has_errors:'email' %}is-invalid{% endif %}">
    """
    if hasattr(view, "has_field_errors"):
        return view.has_field_errors(field_name)
    return False
