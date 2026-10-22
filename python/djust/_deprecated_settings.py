"""Settings djust 2.0 removes: a startup ``DeprecationWarning`` for each one in use.

djust 2.0 fails startup with ``ImproperlyConfigured`` when one of these is
still set, because a silently ignored ``DJUST_STATE_BACKEND`` would fall back
to the in-memory backend. 1.x keeps honouring them and warns once, from
``DjustConfig.ready()``, so a project sees the replacement before upgrading.
A system check would not do: ASGI servers do not run checks.
"""

from __future__ import annotations

from typing import Any, Optional

from ._deprecation import warn_deprecated

#: Top-level settings that alias a nested key, and the key that replaces each.
DEPRECATED_FLAT_SETTINGS: dict[str, str] = {
    "DJUST_WS_COMPRESSION": "LIVEVIEW_CONFIG['websocket_compression']",
    "DJUST_VDOM_CACHE_ENABLED": "LIVEVIEW_CONFIG['service_worker']['vdom_cache_enabled']",
    "DJUST_VDOM_CACHE_TTL_SECONDS": "LIVEVIEW_CONFIG['service_worker']['vdom_cache_ttl_seconds']",
    "DJUST_VDOM_CACHE_MAX_ENTRIES": "LIVEVIEW_CONFIG['service_worker']['vdom_cache_max_entries']",
    "DJUST_STATE_BACKEND": (
        "DJUST_CONFIG['STATE_BACKEND'] ('memory' or 'redis') with the URL in "
        "DJUST_CONFIG['REDIS_URL']"
    ),
    "DJUST_REDIS_URL": "DJUST_CONFIG['REDIS_URL']",
}

#: Kill switches 2.0 hard-wires on: the key goes, and so does the opt-out.
DEPRECATED_CONFIG_KEYS: dict[str, str] = {
    "template_auto_call": "Django's nullary auto-call in templates",
    "virtual_keyed_ops": "keyed [dj-virtual] VDOM ops",
    "django_value_repr": "Django-parity {{ value }} rendering",
}

#: ``TEMPLATES[...]['BACKEND']`` paths 2.0 removes.
DEPRECATED_TEMPLATE_BACKENDS: dict[str, str] = {
    "djust.template_backend.DjustTemplateBackend": "djust.template.backend.DjustTemplateBackend",
}


def deprecated_settings_in_use(settings: Any) -> list[tuple[str, Optional[str]]]:
    """``(what, instead)`` for each deprecated setting ``settings`` sets."""
    found: list[tuple[str, Optional[str]]] = [
        ("settings.%s" % name, replacement)
        for name, replacement in DEPRECATED_FLAT_SETTINGS.items()
        if hasattr(settings, name)
    ]
    for setting in ("LIVEVIEW_CONFIG", "DJUST_CONFIG"):
        cfg = getattr(settings, setting, None)
        if not isinstance(cfg, dict):
            continue
        found.extend(
            ("%s[%r] (2.0 always enables %s; delete the key)" % (setting, key, behaviour), None)
            for key, behaviour in DEPRECATED_CONFIG_KEYS.items()
            if key in cfg
        )
    templates = getattr(settings, "TEMPLATES", None)
    if isinstance(templates, (list, tuple)):
        for i, entry in enumerate(templates):
            backend = entry.get("BACKEND") if isinstance(entry, dict) else None
            if isinstance(backend, str) and backend in DEPRECATED_TEMPLATE_BACKENDS:
                found.append(
                    (
                        "TEMPLATES[%d]['BACKEND'] = %r" % (i, backend),
                        repr(DEPRECATED_TEMPLATE_BACKENDS[backend]),
                    )
                )
    return found


def warn_deprecated_settings() -> None:
    """Emit one ``DeprecationWarning`` per deprecated setting still in use."""
    from django.conf import settings

    for what, instead in deprecated_settings_in_use(settings):
        warn_deprecated(what, since="1.4", removed_in="2.0.0", instead=instead, stacklevel=2)
