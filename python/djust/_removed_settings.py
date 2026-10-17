"""Settings djust 2.0 removed, and the startup check that names their replacements.

A removed setting that is still set would otherwise be ignored without a word:
a flat ``DJUST_STATE_BACKEND = "redis://..."`` would quietly fall back to the
in-memory backend, and ``LIVEVIEW_CONFIG['template_auto_call'] = False`` would
quietly stop opting out. ``DjustConfig.ready()`` calls
:func:`reject_removed_settings`, which raises ``ImproperlyConfigured`` instead.
It runs at startup rather than as a system check because ASGI servers do not
run system checks.
"""

from __future__ import annotations

from typing import Any

#: Top-level Django settings that were aliases of a nested key (#3500).
REMOVED_FLAT_SETTINGS: dict[str, str] = {
    "DJUST_WS_COMPRESSION": "LIVEVIEW_CONFIG['websocket_compression']",
    "DJUST_VDOM_CACHE_ENABLED": "LIVEVIEW_CONFIG['service_worker']['vdom_cache_enabled']",
    "DJUST_VDOM_CACHE_TTL_SECONDS": "LIVEVIEW_CONFIG['service_worker']['vdom_cache_ttl_seconds']",
    "DJUST_VDOM_CACHE_MAX_ENTRIES": "LIVEVIEW_CONFIG['service_worker']['vdom_cache_max_entries']",
    "DJUST_STATE_BACKEND": (
        "DJUST_CONFIG['STATE_BACKEND'] ('memory' or 'redis'; put a redis:// URL "
        "in DJUST_CONFIG['REDIS_URL'])"
    ),
    "DJUST_REDIS_URL": "DJUST_CONFIG['REDIS_URL']",
}

#: ``LIVEVIEW_CONFIG`` / ``DJUST_CONFIG`` keys for kill switches 2.0 hard-wired on.
REMOVED_CONFIG_KEYS: dict[str, str] = {
    "template_auto_call": "Django's nullary auto-call in templates is always on",
    "virtual_keyed_ops": "keyed [dj-virtual] VDOM ops are always on",
    "django_value_repr": "{{ value }} always renders as Django's str() does",
}


def removed_settings_in_use(settings: Any) -> list[str]:
    """One line per removed setting or key that ``settings`` still sets."""
    problems = [
        "settings.%s was removed in djust 2.0; use %s." % (name, replacement)
        for name, replacement in REMOVED_FLAT_SETTINGS.items()
        if hasattr(settings, name)
    ]
    for setting in ("LIVEVIEW_CONFIG", "DJUST_CONFIG"):
        cfg = getattr(settings, setting, None)
        if not isinstance(cfg, dict):
            continue
        problems.extend(
            "%s[%r] was removed in djust 2.0 (%s); delete the key." % (setting, key, behaviour)
            for key, behaviour in REMOVED_CONFIG_KEYS.items()
            if key in cfg
        )
    return problems


def reject_removed_settings() -> None:
    """Raise ``ImproperlyConfigured`` listing every removed setting still set."""
    from django.conf import settings
    from django.core.exceptions import ImproperlyConfigured

    problems = removed_settings_in_use(settings)
    if problems:
        raise ImproperlyConfigured(" ".join(problems))
