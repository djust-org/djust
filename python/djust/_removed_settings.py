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

#: ``TEMPLATES[...]['BACKEND']`` module paths 2.0 removed. Without this, the
#: first template lookup raises a bare ``ModuleNotFoundError``.
REMOVED_TEMPLATE_BACKENDS: dict[str, str] = {
    "djust.template_backend.DjustTemplateBackend": "djust.template.backend.DjustTemplateBackend",
}

#: ``TEMPLATES[...]['OPTIONS']['context_processors']`` paths 2.0 removed
#: (#4025). Without this, the first render raises a bare ``ImportError``.
REMOVED_CONTEXT_PROCESSORS: dict[str, str] = {
    "djust.auth.social.social_auth_providers": (
        "{% auth_providers auth %} from {% load djust_auth %}"
    ),
}

#: ``LIVEVIEW_CONFIG`` / ``DJUST_CONFIG`` keys 2.0 removed: kill switches it
#: hard-wired on, and switches for behaviour it removed.
REMOVED_CONFIG_KEYS: dict[str, str] = {
    "template_auto_call": "Django's nullary auto-call in templates is always on",
    "virtual_keyed_ops": "keyed [dj-virtual] VDOM ops are always on",
    "django_value_repr": "{{ value }} always renders as Django's str() does",
    "view_state_in_session": (
        "no view state is kept in the Django session any more: a legacy-exposure "
        "view is mounted again on every page-POST fallback request, and an explicit "
        'view keeps exactly its state(..., persist="server") fields'
    ),
}

#: View attributes 2.0 removed. A truthy value is refused when the class is
#: created, so the module fails to import instead of the view quietly running
#: without the behaviour it asked for. The mount path checks the instance again
#: (``_params_form.run_handle_params``), which also sees a value set on the
#: instance, assigned to the class later, or hidden from ``__init_subclass__``.
REMOVED_VIEW_ATTRIBUTES: dict[str, str] = {
    "use_actors": (
        "the Tokio actor system was removed; delete the attribute. The view runs on "
        "the standard runtime, which already runs one turn at a time per connection. "
        "For work that should not block a turn, use start_async() or @background"
    ),
    # A method, not a flag: the base class no longer defines it, so any value
    # found on a view is an override the application wrote and djust would
    # never call.
    "_restore_snapshot": (
        "the legacy signed back-navigation snapshot that called this override was "
        "removed, so it would never run; delete it, declare the fields the browser "
        'may hold with state(..., persist="client", client=True), and move what '
        "the override did after restoring into handle_restore()"
    ),
    "enable_state_snapshot": (
        "saving a view's whole public state and restoring it on a reconnect was "
        "removed; delete the attribute and declare the fields to keep with "
        'state(..., persist="server") (and state(..., persist="client", client=True) '
        "for what the browser may hold across Back)"
    ),
}


#: Where each refusal sends the reader for the replacement.
MIGRATION_GUIDE_URL = "https://docs.djust.org/guides/migrating-to-2.0/"

_SIGNED_SNAPSHOT = (
    "the legacy signed back-navigation snapshot was removed. Declare the fields "
    'the browser may hold with state(..., persist="client", client=True); djust '
    "signs and verifies their token itself"
)

_PERSISTENT_LIVE_VIEW = (
    "it set enable_state_snapshot, which saved a view's whole public state and "
    "restored it on a reconnect. Subclass LiveView and declare the fields to keep "
    'with state(..., persist="server")'
)

#: Importable names 2.0 removed, by the module they were imported from. A module
#: ``__getattr__`` passes its misses to :func:`refuse_removed_name`, so
#: ``from djust.security import sign_snapshot`` names the replacement instead of
#: failing with a bare "cannot import name".
_INERT_OPTIMISTIC = (
    "it recorded metadata that nothing read, so a decorated handler behaved like an "
    "undecorated one (#4006). Delete the decorator; for feedback before the server "
    "answers, use dj-loading.* attributes or JS Commands"
)
_INERT_CLIENT_STATE = (
    "it recorded metadata that nothing read, so a decorated handler behaved like an "
    "undecorated one (#4006). Delete the decorator; to keep two values in step, set "
    "both in one handler"
)

REMOVED_NAMES: dict[tuple[str, str], str] = {
    ("djust", "PersistentLiveView"): _PERSISTENT_LIVE_VIEW,
    ("djust.live_view", "PersistentLiveView"): _PERSISTENT_LIVE_VIEW,
    ("djust.security", "sign_snapshot"): _SIGNED_SNAPSHOT,
    ("djust.security", "unsign_snapshot"): _SIGNED_SNAPSHOT,
    ("djust.security", "SNAPSHOT_SALT"): _SIGNED_SNAPSHOT,
    ("djust", "optimistic"): _INERT_OPTIMISTIC,
    ("djust.decorators", "optimistic"): _INERT_OPTIMISTIC,
    ("djust", "client_state"): _INERT_CLIENT_STATE,
    ("djust.decorators", "client_state"): _INERT_CLIENT_STATE,
}


def refuse_removed_name(module: str, name: str) -> None:
    """Raise ``ImportError`` if ``module.name`` is a name djust 2.0 removed.

    ``ImportError`` and not ``AttributeError``: the import system replaces an
    ``AttributeError`` from a module ``__getattr__`` with its own "cannot
    import name", which would drop the replacement this message names.
    """
    replacement = REMOVED_NAMES.get((module, name))
    if replacement is not None:
        raise ImportError(
            "%s.%s was removed in djust 2.0: %s. See %s"
            % (module, name, replacement, MIGRATION_GUIDE_URL),
            name=module,
        )


#: Instance attributes 2.0 stopped reading, which a handler assigns rather than a
#: class declares. Each is a property on the view whose setter refuses a truthy
#: value at the assignment (:func:`removed_instance_attribute`), so every
#: transport refuses it the same way; without it, a WebSocket turn dropped the
#: work silently while the HTTP fallback still ran it (#4031).
REMOVED_INSTANCE_ATTRIBUTES: dict[str, str] = {
    "_async_pending": (
        "the single-task queue's readers were removed in #3515 (#3500); "
        "queue the work with self.start_async(fn, *args, **kwargs) or decorate "
        "the handler with @background"
    ),
}


def removed_instance_attribute(name: str) -> property:
    """A property that refuses a truthy assignment to the removed ``name``.

    Reading it gives ``None``. Assigning ``None`` or another falsy value is
    allowed, since it queues nothing (the same rule as
    :data:`REMOVED_VIEW_ATTRIBUTES`).
    """
    replacement = REMOVED_INSTANCE_ATTRIBUTES[name]

    def read(self: Any) -> None:
        return None

    def refuse(self: Any, value: Any) -> None:
        if value:
            import logging

            from django.core.exceptions import ImproperlyConfigured

            cls = type(self)
            message = "%s.%s assigns self.%s, which was removed in djust 2.0: %s." % (
                cls.__module__,
                cls.__qualname__,
                name,
                replacement,
            )
            # An explicit-exposure view's handler failure is logged without its
            # message (ADR-038), so the refusal is logged here too. It names
            # only the class and the attribute, never the assigned value.
            logging.getLogger("djust").error("%s", message)
            raise ImproperlyConfigured(message)

    return property(read, refuse, doc="Removed in djust 2.0; see REMOVED_INSTANCE_ATTRIBUTES.")


def reject_removed_view_attributes(view: Any) -> None:
    """Raise ``ImproperlyConfigured`` if a view turns on a removed attribute.

    ``view`` is a view class (at class creation) or a view instance (on the
    mount path, after ``mount()``).
    """
    cls = view if isinstance(view, type) else type(view)
    for name, replacement in REMOVED_VIEW_ATTRIBUTES.items():
        if getattr(view, name, False):
            from django.core.exceptions import ImproperlyConfigured

            raise ImproperlyConfigured(
                "%s.%s sets %s, which was removed in djust 2.0: %s. See %s"
                % (cls.__module__, cls.__qualname__, name, replacement, MIGRATION_GUIDE_URL)
            )


def removed_settings_in_use(settings: Any) -> list[str]:
    """One line per removed setting or key that ``settings`` still sets."""
    problems = [
        "settings.%s was removed in djust 2.0; use %s." % (name, replacement)
        for name, replacement in REMOVED_FLAT_SETTINGS.items()
        if hasattr(settings, name)
    ]
    templates = getattr(settings, "TEMPLATES", None)
    if isinstance(templates, (list, tuple)):
        for i, entry in enumerate(templates):
            backend = entry.get("BACKEND") if isinstance(entry, dict) else None
            if isinstance(backend, str) and backend in REMOVED_TEMPLATE_BACKENDS:
                problems.append(
                    "TEMPLATES[%d]['BACKEND'] is %r, a module path removed in djust 2.0; "
                    "use %r." % (i, backend, REMOVED_TEMPLATE_BACKENDS[backend])
                )
            options = entry.get("OPTIONS") if isinstance(entry, dict) else None
            processors = options.get("context_processors") if isinstance(options, dict) else None
            if isinstance(processors, (list, tuple)):
                problems.extend(
                    "TEMPLATES[%d]['OPTIONS']['context_processors'] lists %r, a context "
                    "processor removed in djust 2.0; delete the entry and use %s."
                    % (i, path, REMOVED_CONTEXT_PROCESSORS[path])
                    for path in processors
                    if isinstance(path, str) and path in REMOVED_CONTEXT_PROCESSORS
                )
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
