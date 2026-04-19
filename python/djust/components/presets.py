"""
Component preset registry for djust-components.

Presets map a short name to a dict of default parameters for a component
template tag.  When a ``preset`` kwarg is passed to a tag that supports it,
the registry params are merged (tag-level kwargs win).

Usage in templates::

    {% load djust_components %}
    {% dj_button preset="danger-confirm" label="Delete" event="delete" %}

Registering custom presets at startup::

    from djust.components.presets import register_preset

    register_preset("dj_button", "my-cta", {
        "variant": "success",
        "size": "lg",
        "icon": "🚀",
    })
"""

from copy import deepcopy
from typing import Any, Dict, Optional


# ---------------------------------------------------------------------------
# Registry — maps (tag_name, preset_name) → param dict
# ---------------------------------------------------------------------------

_PRESET_REGISTRY: Dict[str, Dict[str, Dict[str, Any]]] = {}


def register_preset(
    tag_name: str,
    preset_name: str,
    params: Dict[str, Any],
) -> None:
    """Register a preset for a given tag.

    Args:
        tag_name: Template tag name, e.g. ``"dj_button"``.
        preset_name: Short identifier, e.g. ``"danger-confirm"``.
        params: Dict of parameter names → values that will be applied when the
            preset is used.

    Raises:
        ValueError: If *tag_name* or *preset_name* is empty.
    """
    if not tag_name:
        raise ValueError("tag_name must not be empty")
    if not preset_name:
        raise ValueError("preset_name must not be empty")

    _PRESET_REGISTRY.setdefault(tag_name, {})[preset_name] = params


def get_preset(
    tag_name: str,
    preset_name: str,
) -> Optional[Dict[str, Any]]:
    """Look up a preset and return a *copy* of its params (or ``None``).

    Returns a deep copy so callers can mutate the dict without affecting the
    registry.
    """
    tag_presets = _PRESET_REGISTRY.get(tag_name)
    if tag_presets is None:
        return None
    preset = tag_presets.get(preset_name)
    if preset is None:
        return None
    return deepcopy(preset)


def list_presets(tag_name: Optional[str] = None) -> Dict[str, Dict[str, Dict[str, Any]]]:
    """Return registered presets.

    If *tag_name* is given, return only that tag's presets (shallow copy of the
    inner dict).  Otherwise return the whole registry (shallow copy).
    """
    if tag_name is not None:
        return dict(_PRESET_REGISTRY.get(tag_name, {}))
    return {k: dict(v) for k, v in _PRESET_REGISTRY.items()}


def clear_presets(tag_name: Optional[str] = None) -> None:
    """Remove presets. Useful for testing.

    If *tag_name* is given, remove only that tag's presets. Otherwise remove
    all presets.
    """
    if tag_name is not None:
        _PRESET_REGISTRY.pop(tag_name, None)
    else:
        _PRESET_REGISTRY.clear()


# ---------------------------------------------------------------------------
# Built-in presets for dj_button
# ---------------------------------------------------------------------------

_BUTTON_PRESETS = {
    "danger-confirm": {
        "variant": "danger",
        "icon": "⚠",
    },
    "danger-sm": {
        "variant": "danger",
        "size": "sm",
    },
    "primary-lg": {
        "variant": "primary",
        "size": "lg",
    },
    "ghost-sm": {
        "variant": "ghost",
        "size": "sm",
    },
    "success": {
        "variant": "success",
    },
    "warning": {
        "variant": "warning",
    },
    "link": {
        "variant": "link",
    },
    "loading": {
        "loading": True,
    },
}

for _name, _params in _BUTTON_PRESETS.items():
    register_preset("dj_button", _name, _params)
