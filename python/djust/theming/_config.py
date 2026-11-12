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
from typing import TypeVar

from django.conf import settings
from django.core.exceptions import ImproperlyConfigured

_T = TypeVar("_T")

# Cookie names may only contain ASCII letters, digits, underscores, hyphens.
COOKIE_NAMESPACE_RE = re.compile(r"^[A-Za-z0-9_-]+$")

# A class-name prefix is printed into ``<style>`` blocks that ``theme_head.html`` renders under
# ``{% autoescape off %}`` (and into class attributes), so it must not be able to carry markup.
# ``fullmatch``, not ``match`` + ``$``: ``$`` also matches before a trailing newline.
CSS_PREFIX_RE = re.compile(r"[A-Za-z0-9_-]*")

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
    # Whether ``{% theme_head %}`` links the component catalog's stylesheet,
    # ``djust_components/components.css`` (about 330 KB), when ``djust.components``
    # is installed. Off by default since 2.0 (#4003): the theme's own component CSS
    # (.card, .btn, .input, ...) is always linked, and only pages that render
    # catalog tags ({% modal %}, {% data_table %}, {% app_shell %}, ...) need this
    # file. Only ``True`` restores the 1.x behaviour (a truthy string does not);
    # the ``{{ theme_head }}`` context variable follows it. djust_theming.W004 reports a
    # project that installs djust.components without stating either value.
    "component_catalog_css": False,
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


def _validate_css_prefix(value: _T) -> _T:
    """Validate a CSS class-name prefix: ``LIVEVIEW_CONFIG['theme']['css_prefix']`` (#3779).

    The prefix is interpolated into the generated ``<style>`` blocks (``.{prefix}btn { ... }``),
    which ``theme_head.html`` prints with autoescape OFF. ``x</style><img src=x onerror=...>``
    closed the block and ran. Validating once, at config load, covers every consumer
    (``theme_head``, ``theme_css``, the context processor, the CSS view, the form/component tags)
    instead of escaping at each of them, and a hard error at load also holds under servers that
    never run ``manage.py check``.

    ``""`` (the default, no prefix) and ``None`` (unset) are accepted. Anything else must be a
    string of ASCII letters, digits, underscores and hyphens.

    Raises:
        ImproperlyConfigured: the value is not a string of those characters.
    """
    if value is None:
        return value
    if not isinstance(value, str) or CSS_PREFIX_RE.fullmatch(value) is None:
        raise ImproperlyConfigured(
            f"LIVEVIEW_CONFIG['theme']['css_prefix']={value!r} contains characters that are not "
            f"allowed in a CSS class prefix. Use only ASCII letters, digits, underscores, and "
            f"hyphens (for example 'dj-')."
        )
    return value


def _merged_theme_config() -> dict:
    """The defaults overlaid with ``LIVEVIEW_CONFIG['theme']``, NOT validated.

    For a system check that has to report a bad value; everything else wants
    :func:`get_theme_config`.
    """
    liveview_config = getattr(settings, "LIVEVIEW_CONFIG", {})
    theme_config = liveview_config.get("theme", {})
    return {**DEFAULT_CONFIG, **theme_config}


def get_theme_config() -> dict:
    """Get theme configuration from Django settings.

    Raises:
        ImproperlyConfigured: if ``cookie_namespace`` contains characters
            illegal in cookie names (#1169(b)), or ``css_prefix`` is not
            letters/digits/underscore/hyphen (#3779).
    """
    merged = _merged_theme_config()
    # Validate cookie_namespace at config-load (#1169(b)).
    _validate_cookie_namespace(merged.get("cookie_namespace"))
    # Validate css_prefix at config-load (#3779): it is printed into <style> blocks.
    _validate_css_prefix(merged.get("css_prefix"))
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
