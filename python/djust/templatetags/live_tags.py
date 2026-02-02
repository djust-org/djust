"""
Django template tags for LiveView forms and embedded views.

These tags provide a cleaner syntax for rendering LiveView forms with
automatic validation, error display, and framework-specific styling,
as well as embedding child LiveViews within parent templates.

Usage:
    {% load live_tags %}

    <!-- Render entire form -->
    {% live_form view %}

    <!-- Render single field -->
    {% live_field view "field_name" %}

    <!-- Render with options -->
    {% live_field view "email" label="Email Address" wrapper_class="custom-class" %}

    <!-- Embed a child LiveView -->
    {% live_render "myapp.views.SearchBar" query="" %}
    {% live_render "myapp.views.NotificationBell" %}
"""

from django import template
from typing import Any

register = template.Library()


@register.simple_tag(takes_context=True)
def live_render(context, view_path, **kwargs):
    """
    Embed a child LiveView inside a parent LiveView's template.

    Each embedded view has its own state, mount(), and event handlers.
    It re-renders independently and shares the parent's WebSocket connection.

    Args:
        view_path: Dotted path to the LiveView class (e.g., "myapp.views.SearchBar")
        **kwargs: Initial keyword arguments passed to the child's mount()

    Returns:
        HTML string with the embedded view wrapped in a scoped container

    Example:
        {% load live_tags %}
        {% live_render "myapp.views.SearchBar" query="" placeholder="Search..." %}
        {% live_render "myapp.views.NotificationBell" %}
    """
    from ..embedded import render_embedded_view

    # Get parent view from template context
    parent_view = context.get("view")
    if parent_view is None:
        # Try common context variable names
        for key in ("self", "liveview", "live_view"):
            parent_view = context.get(key)
            if parent_view is not None:
                break

    if parent_view is None:
        return "<!-- ERROR: live_render requires a parent LiveView in context as 'view' -->"

    # Get request from context
    request = context.get("request")

    return render_embedded_view(
        parent_view=parent_view,
        view_path=view_path,
        request=request,
        **kwargs,
    )


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
def live_field(view, field_name: str, **kwargs):
    """
    Render a single form field automatically using the configured CSS framework.

    Args:
        view: The LiveView instance (must use FormMixin)
        field_name: Name of the field to render
        **kwargs: Rendering options passed to as_live_field()
            - framework: Override the configured CSS framework
            - render_labels: Whether to render field labels (default: True)
            - render_help_text: Whether to render help text (default: True)
            - render_errors: Whether to render errors (default: True)
            - auto_validate: Whether to add validation on change (default: True)
            - wrapper_class: Custom wrapper class for the field
            - label: Custom label text

    Returns:
        HTML string for the field

    Example:
        {% load live_tags %}
        {% live_field view "email" %}
        {% live_field view "password" label="Custom Password Label" %}
    """
    if not hasattr(view, "as_live_field"):
        return "<!-- ERROR: View does not have as_live_field() method. Did you use FormMixin? -->"

    return view.as_live_field(field_name, **kwargs)


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


@register.simple_tag
def djust_route_map():
    """
    Output a <script> tag that populates the client-side route map for
    live_redirect navigation.

    Include once in your base template:
        {% load live_tags %}
        {% djust_route_map %}
    """
    from djust.routing import get_route_map_script
    return get_route_map_script()
