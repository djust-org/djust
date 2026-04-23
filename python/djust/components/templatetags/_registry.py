"""
Shared template-tag registry and helpers.

All templatetag sub-modules import ``register``, ``_resolve``, and
``_parse_kv_args`` from here so that every tag registers on the same
``template.Library`` instance.
"""

import uuid  # noqa: F401 — re-exported for sub-modules

from django import template
from django.utils.html import conditional_escape  # noqa: F401
from django.utils.safestring import mark_safe  # noqa: F401

from djust.components.utils import CURRENCY_SYMBOLS  # noqa: F401
from djust.components.utils import interpolate_color  # noqa: F401

register = template.Library()

# Re-exports for sub-modules (see _charts.py, _forms.py, etc.)
__all__ = [
    "register",
    "uuid",
    "conditional_escape",
    "mark_safe",
    "CURRENCY_SYMBOLS",
    "interpolate_color",
    "_resolve",
    "_parse_kv_args",
]


def _resolve(value, context):
    """Resolve a template variable or return the literal value."""
    if isinstance(value, template.Variable):
        try:
            return value.resolve(context)
        except template.VariableDoesNotExist:
            return ""
    return value


def _parse_kv_args(bits, parser):
    """Parse key=value arguments from template tag tokens."""
    kwargs = {}
    for bit in bits:
        if "=" in bit:
            key, val = bit.split("=", 1)
            # Strip quotes for literal strings
            if (val.startswith('"') and val.endswith('"')) or (
                val.startswith("'") and val.endswith("'")
            ):
                kwargs[key] = val[1:-1]
            else:
                kwargs[key] = template.Variable(val)
        else:
            raise template.TemplateSyntaxError(
                f"Unexpected argument '{bit}'. Use key=value format."
            )
    return kwargs
