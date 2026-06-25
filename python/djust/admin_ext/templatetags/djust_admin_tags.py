"""Template tags and filters for djust admin_ext."""

from typing import Any, Mapping, Optional

from django import forms, template
from django.forms.boundfield import BoundField

register = template.Library()


@register.filter
def get_item(dictionary: Optional[Mapping[Any, Any]], key: Any) -> Any:
    """Get an item from a dictionary by key."""
    if dictionary is None:
        return None
    return dictionary.get(key)


@register.filter
def get_field(form: Optional[forms.BaseForm], field_name: str) -> Optional[BoundField]:
    """Get a field from a form by name."""
    if form is None:
        return None
    return form[field_name] if field_name in form.fields else None


@register.filter
def concat(value: Any, arg: Any) -> str:
    """Concatenate two values as strings."""
    return str(value) + str(arg)


@register.simple_tag
def admin_url(admin_site_name: str, view_name: str, *args: Any, **kwargs: Any) -> str:
    """Generate admin URL."""
    from django.urls import reverse

    url: str = reverse(f"{admin_site_name}:{view_name}", args=args, kwargs=kwargs)
    return url
