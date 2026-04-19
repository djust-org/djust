"""Template tags and filters for djust admin_ext."""

from django import template

register = template.Library()


@register.filter
def get_item(dictionary, key):
    """Get an item from a dictionary by key."""
    if dictionary is None:
        return None
    return dictionary.get(key)


@register.filter
def get_field(form, field_name):
    """Get a field from a form by name."""
    if form is None:
        return None
    return form[field_name] if field_name in form.fields else None


@register.filter
def concat(value, arg):
    """Concatenate two values as strings."""
    return str(value) + str(arg)


@register.simple_tag
def admin_url(admin_site_name, view_name, *args, **kwargs):
    """Generate admin URL."""
    from django.urls import reverse

    return reverse(f"{admin_site_name}:{view_name}", args=args, kwargs=kwargs)
