"""Settings, attributes and arguments djust 2.0 removed, and the refusals that name their replacements.

A removed setting that is still set would otherwise be ignored without a word:
a flat ``DJUST_STATE_BACKEND = "redis://..."`` would quietly fall back to the
in-memory backend, and ``LIVEVIEW_CONFIG['template_auto_call'] = False`` would
quietly stop opting out. ``DjustConfig.ready()`` calls
:func:`reject_removed_settings`, which raises ``ImproperlyConfigured`` instead.
It runs at startup rather than as a system check because ASGI servers do not
run system checks.

Removed settings are refused at startup; removed view attributes and attribute
values (``exposure_policy = "legacy"``) when the class is created and again on
construction and the mount path; removed handler argument values
(``parameter_policy="legacy"``) when the decorator applies (#4199). Only
``djust_exposure_inventory`` may create 1.3 classes, under
:func:`upgrade_inspection`, so it can read and rewrite them.
"""

from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from typing import Any, Iterator, Optional

#: What a developer uses instead of time-travel debugging (#4184).
TIME_TRAVEL_REPLACEMENT = (
    "to follow state changes event by event, open the debug panel's State tab "
    "(DEBUG = True, with LIVEVIEW_CONFIG['debug_panel'] left on), which lists each "
    "event with the state after it and what changed from the event before"
)

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
        "the key is gone; a view's state is saved in the server-side session by "
        "default. To save nothing, as view_state_in_session = False did, set "
        "DJUST_AUTOMATIC_STATE = False in settings (the whole project) or "
        "automatic_state = False on a view; each page-POST fallback request then "
        'runs mount() again. Fields declared state(..., persist="server") are '
        "still kept"
    ),
    # #4184: time-travel debugging was removed, with its buffer size.
    "time_travel_enabled": "time-travel debugging was removed; " + TIME_TRAVEL_REPLACEMENT,
    "time_travel_max_events": "time-travel debugging was removed; " + TIME_TRAVEL_REPLACEMENT,
    # #4199: the legacy parameter policy and the legacy-only re-authorization switches.
    "event_parameter_policy": (
        "strict event parameters are the only policy; once the key is gone, manage.py check "
        "reports each handler parameter strict refuses as djust.V016"
    ),
    "reauth_on_event": (
        "every event and server turn is re-authorized against a freshly loaded session, so "
        "the switch has nothing to turn on"
    ),
    "reauth_server_turn_interval": "every server turn is re-authorized, with no interval",
}

#: Keys that are top-level Django settings, never keys of ``LIVEVIEW_CONFIG`` /
#: ``DJUST_CONFIG``. Inside either dict they would be ignored without a word,
#: which for a switch that turns saving OFF means state is saved against the
#: project's wish (#4128). Compared lower-case: key -> the setting to use.
MISPLACED_CONFIG_KEYS: dict[str, str] = {
    "automatic_state": "DJUST_AUTOMATIC_STATE",
    "djust_automatic_state": "DJUST_AUTOMATIC_STATE",
}

#: ``LIVEVIEW_CONFIG`` / ``DJUST_CONFIG`` keys whose values 2.0 removed: key ->
#: (the value still accepted, the removed values, the replacement). Values are
#: compared as ``str(v).strip().lower()``. The accepted value does nothing and is
#: silent; a removed value names the replacement; anything else is an unknown
#: value and the message says to delete the key.
REMOVED_CONFIG_VALUES: dict[str, tuple[str, frozenset[str], str]] = {
    "event_security": (
        "strict",
        # #4101: the "warn" and "open" modes let an undecorated public method
        # run as a client event; 2.0 refuses it on every transport.
        frozenset({"warn", "open"}),
        "only methods decorated with @event_handler are client events, on every "
        "transport. Decorate each method a template binds (dj-click, dj-submit, ...) "
        "with @event_handler, then delete the key",
    ),
}


def _removed_value_problem(setting: str, key: str, value: Any) -> Optional[str]:
    """The problem with ``setting[key] = value``, or None when it is accepted."""
    accepted, removed, replacement = REMOVED_CONFIG_VALUES[key]
    normalized = str(value).strip().lower()
    if normalized == accepted:
        return None
    if normalized in removed:
        return "%s[%r] = %r was removed in djust 2.0: %s. See %s" % (
            setting,
            key,
            value,
            replacement,
            MIGRATION_GUIDE_URL,
        )
    return (
        "%s[%r] = %r is an unknown value, and the key does nothing in djust 2.0. "
        "Delete the key." % (setting, key, value)
    )


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
    # #4184: time-travel debugging (record, jump, component jump, forward
    # replay) was removed. The excluded-fields list only fed the bug-capture
    # Share button, which read the time-travel buffer and went with it.
    "time_travel_enabled": "time-travel debugging was removed; delete the attribute and, "
    + TIME_TRAVEL_REPLACEMENT,
    "time_travel_excluded_fields": (
        "it named the fields the time-travel Share button left out, and that button "
        "read the time-travel buffer, which was removed; delete the attribute. The "
        "debug panel's State tab now has the Share button: list the fields it must "
        "leave out in LIVEVIEW_CONFIG['bug_capture_default_scrub']"
    ),
    # #4199: an explicit view's background work is always cancelled on disconnect.
    "cancel_async_on_disconnect": (
        "a view's background work is always cancelled when its connection closes; delete the "
        "attribute. Work that must finish says so: start_async(fn, survive_disconnect=True) "
        "or @background(survive_disconnect=True)"
    ),
}

#: View attributes whose values 2.0 removed (#4199). The accepted value does
#: nothing and is silent (``exposure_policy = "explicit"`` through 2.x); a
#: removed value names the replacement; any other value is refused as unknown.
#: Refused when the class is created and again on the mount path.
REMOVED_VIEW_ATTRIBUTE_VALUES: dict[str, tuple[str, frozenset[str], str]] = {
    "exposure_policy": (
        "explicit",
        frozenset({"legacy"}),
        "explicit exposure is the only policy. Supply the names the template reads from "
        "get_context_data(), declare the state a later event or a reconnect needs with "
        'state() (persist="server" to keep it across requests), then delete the line. '
        "python manage.py djust_exposure_inventory --fix makes the mechanical part of "
        "that change and lists what needs a person",
    ),
}

#: Handler decorator arguments whose values 2.0 removed (#4199), shaped like
#: :data:`REMOVED_VIEW_ATTRIBUTE_VALUES`. Refused when the decorator applies.
REMOVED_HANDLER_ARGUMENT_VALUES: dict[str, tuple[str, frozenset[str], str]] = {
    "parameter_policy": (
        "strict",
        frozenset({"legacy"}),
        "strict event parameters are the only policy. Annotate the handler's parameters "
        "(Any accepts a value unchecked; manage.py check reports what strict refuses as "
        "djust.V016), send dj-value-* from the template instead of data-*, and delete the argument",
    ),
}

#: The :data:`REMOVED_VIEW_ATTRIBUTES` that :func:`upgrade_inspection` lets
#: through at class creation. Only #4199's refusal: the others were refused
#: before ``djust_exposure_inventory --fix`` (#4209) existed.
UPGRADE_PERMITTED_VIEW_ATTRIBUTES = frozenset({"cancel_async_on_disconnect"})

_UPGRADE_INSPECTION: ContextVar[bool] = ContextVar("djust_upgrade_inspection", default=False)


@contextmanager
def upgrade_inspection() -> Iterator[None]:
    """Let ``djust_exposure_inventory`` import 1.3 code that 2.0 refuses (#4199, #4209).

    Inside the block, a class or handler that states one of the removed 1.3 spellings
    (``exposure_policy = "legacy"``, ``@event_handler(parameter_policy="legacy")``,
    ``cancel_async_on_disconnect = True``) is created instead of refused, so the command
    can inventory and rewrite it. The permit only creates classes: constructing or
    mounting a view that states a removed spelling is refused everywhere, inside the
    block too. A ``parameter_policy="legacy"`` handler created inside the block still
    carries that policy until the legacy parameter runtime is deleted (#4205). Only that
    command enters it; a test pins that.
    """
    token = _UPGRADE_INSPECTION.set(True)
    try:
        yield
    finally:
        _UPGRADE_INSPECTION.reset(token)


def upgrade_inspection_active() -> bool:
    """True inside :func:`upgrade_inspection`."""
    return _UPGRADE_INSPECTION.get()


def _value_label(value: Any) -> str:
    """``repr`` of a plain ``str``; only the type name of anything else, a ``str``
    subclass included (its ``__repr__`` is never run)."""
    if type(value) is str:
        return repr(value)
    if isinstance(value, str):
        return "a value of type %s (a str subclass, which is not accepted)" % type(value).__name__
    return "a value of type %s" % type(value).__name__


def _value_problem(
    table: dict[str, tuple[str, frozenset[str], str]], label: str, name: str, value: Any
) -> Optional[str]:
    accepted, removed, replacement = table[name]
    if type(value) is str and value == accepted:
        return None
    if type(value) is str and value.strip().lower() in removed:
        return "%s sets %s = %r, which was removed in djust 2.0: %s. See %s" % (
            label,
            name,
            value,
            replacement,
            MIGRATION_GUIDE_URL,
        )
    return "%s sets %s = %s, an unknown value: %r is the only %s. Delete the line. See %s" % (
        label,
        name,
        _value_label(value),
        accepted,
        name,
        MIGRATION_GUIDE_URL,
    )


def removed_attribute_value_problem(label: str, name: str, value: Any) -> Optional[str]:
    """The problem with a view stating ``name = value``, or None when it is accepted.

    ``name`` is a key of :data:`REMOVED_VIEW_ATTRIBUTE_VALUES`. Only exactly the
    accepted string passes; a removed value names the replacement; anything else
    (another string, ``None``, a descriptor, a non-string) is an unknown value.
    """
    return _value_problem(REMOVED_VIEW_ATTRIBUTE_VALUES, label, name, value)


_ABSENT = object()


def _stated_value(view: Any, name: str) -> Any:
    """The value ``view`` states for ``name`` without evaluating a descriptor."""
    if not isinstance(view, type):
        try:
            own = vars(view)
        except TypeError:
            own = {}
        if name in own:
            return own[name]
        view = type(view)
    for klass in view.__mro__:
        found = vars(klass).get(name, _ABSENT)
        if found is not _ABSENT:
            return found
    return _ABSENT


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

_TIME_TRAVEL = "time-travel debugging was removed (#4184); " + TIME_TRAVEL_REPLACEMENT
_ENCODE_VIEW_STATE = (
    "it read the time-travel buffer, which was removed with time-travel debugging "
    "(#4184). Use the Share button on the debug panel's State tab, or build the "
    "capture from the states you choose: BugCapture(state_before=..., "
    "state_after=..., vdom_patches=...).encode(scrub=scrub_fields(...))"
)

_REACT = (
    "the React integration was removed (#4006): it rendered a placeholder <div> that only a "
    "demo Counter ever hydrated. Mount a client-side widget with a JavaScript hook instead: put "
    'dj-hook="Name" on an ordinary element and register the hook in window.djust.hooks '
    "(https://docs.djust.org/guides/hooks/)"
)

REMOVED_NAMES: dict[tuple[str, str], str] = {
    ("djust", "ReactMixin"): _REACT,
    ("djust", "react_components"): _REACT,
    ("djust", "register_react_component"): _REACT,
    ("djust", "time_travel"): _TIME_TRAVEL,
    ("djust.bug_capture", "encode_view_state"): _ENCODE_VIEW_STATE,
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

    ``view`` is a view class (at class creation) or a view instance (on
    construction and on the mount path, after ``mount()``). It also refuses a
    value of :data:`REMOVED_VIEW_ATTRIBUTE_VALUES` other than the accepted one
    (``exposure_policy = "legacy"``, #4199). Inside :func:`upgrade_inspection`
    a class is let through for the 1.3 spellings; an instance never is.
    """
    from django.core.exceptions import ImproperlyConfigured

    is_class = isinstance(view, type)
    permitted = is_class and upgrade_inspection_active()
    cls = view if is_class else type(view)
    label = "%s.%s" % (cls.__module__, cls.__qualname__)
    for name, replacement in REMOVED_VIEW_ATTRIBUTES.items():
        if permitted and name in UPGRADE_PERMITTED_VIEW_ATTRIBUTES:
            continue
        if getattr(view, name, False):
            raise ImproperlyConfigured(
                "%s sets %s, which was removed in djust 2.0: %s. See %s"
                % (label, name, replacement, MIGRATION_GUIDE_URL)
            )
    if permitted:
        return
    for name in REMOVED_VIEW_ATTRIBUTE_VALUES:
        value = _stated_value(view, name)
        if value is _ABSENT:
            continue
        problem = removed_attribute_value_problem(label, name, value)
        if problem is not None:
            raise ImproperlyConfigured(problem)


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
        problems.extend(
            "%s[%r] is not read: it is the top-level setting %s (True or False), or the "
            "class attribute automatic_state on a view. Move it out of %s."
            % (setting, key, MISPLACED_CONFIG_KEYS[key.lower()], setting)
            for key in cfg
            if isinstance(key, str) and key.lower() in MISPLACED_CONFIG_KEYS
        )
        for key in REMOVED_CONFIG_VALUES:
            if key in cfg:
                problem = _removed_value_problem(setting, key, cfg[key])
                if problem is not None:
                    problems.append(problem)
    return problems


def reject_removed_settings() -> None:
    """Raise ``ImproperlyConfigured`` listing every removed setting still set."""
    from django.conf import settings
    from django.core.exceptions import ImproperlyConfigured

    problems = removed_settings_in_use(settings)
    if problems:
        raise ImproperlyConfigured(" ".join(problems))
