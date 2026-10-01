"""Theme configuration reader — leaf module (CodeQL py/cyclic-import #2351, #2357–#2362).

Holds ``DEFAULT_CONFIG`` + ``get_theme_config()`` + cookie-namespace validation,
extracted from ``manager.py``. This module imports ONLY Django + stdlib — never
``.presets`` / ``.registry`` / ``.manager`` / ``.css_generator`` — so that
``registry.py`` and ``css_generator.py`` can read the theme config WITHOUT
importing ``manager`` (the edge that closed the ``presets ↔ registry ↔ manager``
and ``manager ↔ theme_css_generator ↔ css_generator`` import cycles).

``manager`` re-exports ``get_theme_config`` / ``DEFAULT_CONFIG`` for back-compat,
so ``from djust.theming.manager import get_theme_config`` keeps working.
"""

from __future__ import annotations

import re

from django.conf import settings
from django.core.exceptions import ImproperlyConfigured

# Cookie names may only contain ASCII letters, digits, underscores, hyphens.
COOKIE_NAMESPACE_RE = re.compile(r"^[A-Za-z0-9_-]+$")

# Default configuration
DEFAULT_CONFIG = {
    "theme": "material",  # Design system theme
    "preset": "default",  # Color preset
    "default_mode": "system",
    "persist_in_session": True,
    "session_key": "djust_theme",
    "enable_dark_mode": True,
    "css_prefix": "",  # Namespace prefix for component CSS classes (e.g. "dj-")
    "use_css_layers": True,  # Wrap generated CSS in @layer declarations
    "css_layer_order": "base, tokens, components, djust-components, theme",  # Layer priority order
    "critical_css": True,  # Split CSS into critical (inlined) and deferred (async-loaded)
    "themes_dir": "themes/",  # User theme directory, relative to BASE_DIR
    "direction": "auto",  # Text direction: "ltr", "rtl", or "auto" (detect from LANGUAGE_CODE)
}


def _validate_cookie_namespace(value: str | None) -> str | None:
    """Validate ``LIVEVIEW_CONFIG['theme']['cookie_namespace']`` (#1169(b)).

    The value is interpolated directly into cookie names; characters
    illegal in cookie names (whitespace, ``=``, ``;``, non-ASCII)
    silently produce malformed Set-Cookie headers. Validate at
    config-load so the failure mode is a loud
    :class:`~django.core.exceptions.ImproperlyConfigured` at startup
    instead of broken cookies in production.

    ``None`` and empty string are accepted as "no namespace configured".
    """
    if value is None or value == "":
        return value
    if not isinstance(value, str) or not COOKIE_NAMESPACE_RE.match(value):
        raise ImproperlyConfigured(
            f"LIVEVIEW_CONFIG['theme']['cookie_namespace']={value!r} contains "
            f"characters illegal in cookie names. Use only ASCII letters, "
            f"digits, underscores, and hyphens."
        )
    return value


def get_theme_config() -> dict:
    """Get theme configuration from Django settings.

    Raises:
        ImproperlyConfigured: if ``cookie_namespace`` contains characters
            illegal in cookie names (#1169(b)).
    """
    liveview_config = getattr(settings, "LIVEVIEW_CONFIG", {})
    theme_config = liveview_config.get("theme", {})
    merged = {**DEFAULT_CONFIG, **theme_config}
    # Validate cookie_namespace at config-load (#1169(b)).
    _validate_cookie_namespace(merged.get("cookie_namespace"))
    return merged


def parse_preset_names(value: object) -> list[str] | None:
    """Normalise a preset allow-list: ``"legal, medical"`` or ``["legal", "medical"]``
    become ``["legal", "medical"]``. ``None`` stays ``None`` (not given); an empty
    string or list gives ``[]`` (given, and meaning "no limit").

    Raises:
        ValueError: ``value`` is neither a string nor an iterable of names.
    """
    if value is None:
        return None
    if isinstance(value, str):
        items: list[object] = list(value.split(","))
    else:
        try:
            items = list(value)  # type: ignore[call-overload]
        except TypeError:
            raise ValueError(
                f"preset names must be a comma-separated string or a list of names, got {value!r}"
            ) from None
    return [str(item).strip() for item in items if str(item).strip()]
