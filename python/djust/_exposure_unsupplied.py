"""Notice (or error) for a template name an explicit view holds but does not supply.

Under explicit exposure (ADR-038, the default) a template gets the view's
declared ``state()`` fields (#4050), what ``get_context_data()`` returns and
what the framework's providers hand it. A view written for the attribute walk
sets ``self.count`` in ``mount()`` without declaring it and reads
``{{ count }}`` in its template: that name renders empty, and Django's
template semantics make an unknown name silent. This module makes it audible
while ``DEBUG`` is on, and, with ``DJUST_UNSUPPLIED_NAMES`` (#4279), always
(``"warn"``) or as an error a test run cannot miss (``"error"``).

A declared field is supplied by the base ``get_context_data()``, so it is
reported only when it still did not arrive: it was declared
``state(render=False)``, or the view's ``get_context_data()`` returned a new
dict without calling ``super().get_context_data()``.

A name ``X_count`` gets its own notice when ``X`` is in the context as a
sized value (a QuerySet, list, tuple or set) and nothing supplies
``X_count`` (#4054). The legacy policy generated ``items_count`` from
``items``; explicit exposure does not, so a 1.x template reading it renders
empty. The notice points at ``{{ X|length }}``.

The check runs where the render path settles its context
(``ContextMixin._context_for_render``). In the default mode with ``DEBUG`` off it
is one settings read and returns. It names attributes and never reads their values (the
``_count`` check looks at the stem's type, never its contents: a QuerySet is
not evaluated).
"""

from __future__ import annotations

import logging
import weakref
from types import FunctionType, MappingProxyType
from typing import Any, Mapping

from ._exposure import ExposureConfigurationError

logger = logging.getLogger("djust.exposure")

#: The setting that chooses how an unsupplied name is reported (#4279).
SETTING = "DJUST_UNSUPPLIED_NAMES"
#: ``"debug"`` logs under ``DEBUG`` (the default); ``"warn"`` logs whatever ``DEBUG`` is;
#: ``"error"`` raises on every render that leaves a name unsupplied.
_MODES = ("debug", "warn", "error")


class UnsuppliedTemplateNameError(ExposureConfigurationError):
    """A render left a template name the view holds unsupplied (``DJUST_UNSUPPLIED_NAMES = "error"``).

    Names only, never values: the message is safe to show the developer.
    """


_INVALID_MODE_LOGGED = False


def _normalised_mode(value: Any) -> "str | None":
    """The mode ``value`` names (case-insensitive), or None when it is not one."""
    if type(value) is not str:
        return None
    mode = value.strip().lower()
    return mode if mode in _MODES else None


def invalid_mode_message(value: Any) -> str:
    return "%s must be one of %s; got %s." % (
        SETTING,
        ", ".join('"%s"' % m for m in _MODES),
        repr(value)[:80],
    )


def unsupplied_names_mode() -> str:
    """The ``DJUST_UNSUPPLIED_NAMES`` mode.

    An invalid value is reported once at ERROR level and treated as ``"debug"``: a
    dev-only diagnostic setting must not take a production render down. ``manage.py
    check`` fails on it at startup (djust.C043).
    """
    global _INVALID_MODE_LOGGED
    from django.conf import settings

    value = getattr(settings, SETTING, "debug")
    mode = _normalised_mode(value)
    if mode is None:
        if not _INVALID_MODE_LOGGED:
            _INVALID_MODE_LOGGED = True
            logger.error('%s Using "debug" until it is fixed.', invalid_mode_message(value))
        return "debug"
    return mode


#: The names Django's own context processors supply, so no processor runs to learn them.
_KNOWN_PROCESSOR_KEYS: Mapping[str, frozenset] = MappingProxyType(
    {
        "django.contrib.auth.context_processors.auth": frozenset({"user", "perms"}),
        "django.contrib.messages.context_processors.messages": frozenset(
            {"messages", "DEFAULT_MESSAGE_LEVELS"}
        ),
        "django.template.context_processors.request": frozenset({"request"}),
        "django.template.context_processors.debug": frozenset({"debug", "sql_queries"}),
        "django.template.context_processors.i18n": frozenset(
            {"LANGUAGES", "LANGUAGE_CODE", "LANGUAGE_BIDI"}
        ),
        "django.template.context_processors.tz": frozenset({"TIME_ZONE"}),
        "django.template.context_processors.static": frozenset({"STATIC_URL"}),
        "django.template.context_processors.media": frozenset({"MEDIA_URL"}),
        "django.template.context_processors.csrf": frozenset({"csrf_token"}),
    }
)


def context_processor_names(request: Any = None) -> frozenset:
    """Every name the configured ``TEMPLATES`` context processors can supply (B1).

    A name a context processor supplies is not "unsupplied": the render path adds it
    after the notice runs, so the notice and T027 must count it. Django's own
    processors come from a known map; any other is called with ``request`` (or a fake
    ``RequestFactory`` request) inside a ``try``. A processor that cannot be resolved
    or raises supplies nothing here, which is what the render does with it too.
    Never raises.
    """
    names: set[str] = set()
    try:
        from django.conf import settings

        from .utils import is_app_dirs_template_backend

        paths: list[Any] = []
        for config in getattr(settings, "TEMPLATES", []) or []:
            if is_app_dirs_template_backend(config.get("BACKEND")):
                paths.extend((config.get("OPTIONS") or {}).get("context_processors") or [])
    except Exception:  # noqa: BLE001
        return frozenset()
    fake = None
    for path in paths:
        try:
            if isinstance(path, str) and path in _KNOWN_PROCESSOR_KEYS:
                names.update(_KNOWN_PROCESSOR_KEYS[path])
                continue
            if isinstance(path, str):
                from django.utils.module_loading import import_string

                processor = import_string(path)
            else:
                processor = path
            if request is None and fake is None:
                from django.contrib.auth.models import AnonymousUser
                from django.test import RequestFactory

                fake = RequestFactory().get("/")
                fake.user = AnonymousUser()
                fake.session = {}
            supplied = processor(request if request is not None else fake)
            if supplied:
                names.update(k for k in supplied if type(k) is str)
        except Exception:  # noqa: BLE001 — a processor that fails supplies nothing
            continue
    return frozenset(names)


#: Where the warning sends a reader.
MIGRATION_GUIDE_URL = "https://docs.djust.org/guides/migrating-to-2.0/"

#: Names already reported, by view class. Weak: view classes can be collected.
_REPORTED: "weakref.WeakKeyDictionary[type, set[str]]" = weakref.WeakKeyDictionary()

#: The log modes' template paths, by view class: ``(source key, paths)`` (#4294).
#: With ``DEBUG`` on the JIT source is not cached, so ``template_paths()`` resolves
#: the inheritance and inlines every include on each call; the notice would pay
#: that on every render. Kept while the view's template source key is unchanged
#: and something clears it when a template changes: render caches are on, or hot
#: reload is running (its template refresh clears the render caches, and this).
_PATHS_BY_CLASS: "weakref.WeakKeyDictionary[type, tuple[Any, Any]]" = weakref.WeakKeyDictionary()


def clear_unsupplied_paths_cache() -> None:
    """Forget the log modes' template paths (a template may have changed)."""
    _PATHS_BY_CLASS.clear()


def _templates_stay_fresh() -> bool:
    """Whether a template edit clears the render caches before the next render."""
    from ._render_caches import render_caches_enabled

    if render_caches_enabled():
        return True
    try:
        from .dev_server import hot_reload_server
    except ImportError:  # watchdog is optional
        return False
    return bool(hot_reload_server.is_running())


def _log_mode_paths(view: Any, cls: type) -> Any:
    """``view.template_paths()``, kept per view class and template source key.

    A view that overrides ``template_paths`` or ``_get_template_content`` is
    asked every time: its answer may depend on more than the template.
    """
    from .mixins.jit import JITMixin, _template_source_key

    if (
        getattr(cls, "template_paths", None) is not JITMixin.template_paths
        or getattr(cls, "_get_template_content", None) is not JITMixin._get_template_content
        or "template_paths" in getattr(view, "__dict__", {})
        or "_get_template_content" in getattr(view, "__dict__", {})
    ):
        return view.template_paths()
    if not _templates_stay_fresh():
        return view.template_paths()
    key = _template_source_key(view)
    if key is None:
        return view.template_paths()
    held = _PATHS_BY_CLASS.get(cls)
    if held is not None and held[0] == key:
        return held[1]
    paths = view.template_paths()
    _PATHS_BY_CLASS[cls] = (key, paths)
    return paths


_NOT_VALUES = (FunctionType, staticmethod, classmethod, type)


def _is_framework_class(cls: type) -> bool:
    module = getattr(cls, "__module__", "") or ""
    return (
        module == "djust"
        or module == "builtins"
        or module.startswith("django.")
        or (module.startswith("djust.") and not module.startswith("djust.tests."))
    )


def _held_by_view(view: Any, name: str) -> bool:
    """Whether ``name`` is a public attribute the application set on the view.

    Looks at the instance's own attributes (minus the ones ``LiveView.__init__``
    created) and at the class bodies of the application's classes. Types only:
    no descriptor, property or value is evaluated.
    """
    if name.startswith("_"):
        return False
    if name in vars(view) and name not in getattr(view, "_framework_attrs", ()):
        return True
    for owner in type(view).__mro__:
        if _is_framework_class(owner):
            continue
        if name in vars(owner) and not isinstance(vars(owner)[name], _NOT_VALUES):
            return True
    return False


def _declared_state(view: Any, name: str) -> Any:
    """The ``state()`` descriptor the view's class declares for ``name``, or None."""
    from inspect import getattr_static

    from ._state import StateProperty

    declaration = getattr_static(type(view), name, None)
    return declaration if issubclass(type(declaration), StateProperty) else None


def _advice(view: Any, name: str) -> str:
    declaration = _declared_state(view, name)
    if declaration is None:
        return (
            "Under explicit exposure (the default) declare it with state(), which "
            "renders it, or return it from get_context_data()."
        )
    if declaration.render is False:
        return (
            "It is declared with state(render=False), which keeps it out of the "
            "template; drop render=False or return it from get_context_data()."
        )
    return (
        "It is declared with state(), which renders it, but get_context_data() "
        "returned a dict without calling super().get_context_data(); build on "
        "the super() result instead."
    )


def unsupplied_template_names(
    view: Any, context: Mapping[str, Any], paths: Any = None
) -> list[str]:
    """Names the view's template reads, the view holds, and ``context`` lacks."""
    if paths is None:
        paths = view.template_paths()
    candidates = [
        name
        for name in paths
        if type(name) is str and name not in context and _held_by_view(view, name)
    ]
    if not candidates:
        return []
    supplied = _processor_names(view)
    return sorted(name for name in candidates if name not in supplied)


def _processor_names(view: Any) -> frozenset:
    """The names the view's context processors add after the notice runs (B1)."""
    return context_processor_names(getattr(view, "request", None))


#: The suffix the legacy policy derived a count from (``items`` -> ``items_count``).
COUNT_SUFFIX = "_count"


def _is_sized_collection(value: Any) -> bool:
    """A value the legacy policy counted: a QuerySet, list, tuple or set.

    A type test only. A QuerySet is not evaluated, and a string or a mapping
    (sized, but never counted by the legacy policy) does not qualify.
    """
    if isinstance(value, (list, tuple, set, frozenset)):
        return True
    try:
        from django.db.models import QuerySet
    except ImportError:  # pragma: no cover - Django is a hard dependency
        return False
    return isinstance(value, QuerySet)


def unsupplied_count_names(
    view: Any, context: Mapping[str, Any], paths: Any = None
) -> list[tuple[str, str]]:
    """``(name, stem)`` for each ``stem_count`` the template reads that nothing supplies.

    Only when ``stem`` is in ``context`` as a sized collection and the view
    does not hold ``stem_count`` itself (that case is the ordinary
    unsupplied-name notice).
    """
    if paths is None:
        paths = view.template_paths()
    found = []
    for name in paths:
        if type(name) is not str or not name.endswith(COUNT_SUFFIX) or name in context:
            continue
        stem = name[: -len(COUNT_SUFFIX)]
        if not stem or stem not in context or _held_by_view(view, name):
            continue
        if _is_sized_collection(context[stem]) and name not in _processor_names(view):
            found.append((name, stem))
    return sorted(found)


def warn_unsupplied_template_names(view: Any, context: Any) -> None:
    """Report each name that will render empty, as ``DJUST_UNSUPPLIED_NAMES`` says (#4279).

    ``"debug"`` (the default): log once per view class and name, under ``DEBUG`` only.
    ``"warn"``: the same log whatever ``DEBUG`` is. ``"error"``: raise
    :class:`UnsuppliedTemplateNameError` on every render that leaves a name unsupplied
    (no once-per-name memory: each test must fail on its own). Only for a view whose
    policy is ``"explicit"``. In the log modes it never raises: a diagnostic must not
    break the render it describes. An invalid setting value does raise.
    """
    from django.conf import settings

    mode = unsupplied_names_mode()
    if mode == "debug" and getattr(settings, "DEBUG", False) is not True:
        return
    strict: list[tuple[list[tuple[str, str]], list[str]]] = []
    try:
        policy = getattr(view, "exposure_policy", None)
        if type(policy) is not str or policy != "explicit":
            return
        if not isinstance(context, Mapping):
            return
        cls = type(view)
        reported = _REPORTED.get(cls)
        if reported is None:
            reported = _REPORTED[cls] = set()
        # One template read per render, not one per helper (#4294); in the log
        # modes, one per view class while its template is unchanged.
        paths = view.template_paths() if mode == "error" else _log_mode_paths(view, cls)
        counts = unsupplied_count_names(view, context, paths)
        names = unsupplied_template_names(view, context, paths)
        if mode == "error":
            if counts or names:
                strict.append((counts, names))
        else:
            _log_unsupplied(view, cls, reported, counts, names)
    except Exception:  # noqa: BLE001 — never let the diagnostic break a render
        # No exception text: it can carry view values (ADR-038 D-a).
        logger.debug("Unsupplied-template-name check could not run")
    if strict:
        counts, names = strict[0]
        raise UnsuppliedTemplateNameError(_strict_message(view, counts, names))


def _strict_message(view: Any, counts: list[tuple[str, str]], names: list[str]) -> str:
    """The ``"error"`` mode message: the view, the names, the advice. Never a value."""
    cls = type(view)
    parts = []
    for name in names:
        parts.append("{{ %s }} (%s)" % (name, _advice(view, name)))
    for name, stem in counts:
        parts.append(
            "{{ %s }} (djust 1.x generated it from %s; use {{ %s|length }} or return %s "
            "from get_context_data())" % (name, stem, stem, name)
        )
    return (
        "%s.%s: the template reads %d name(s) that nothing supplies, so they render "
        'empty: %s. %s = "error" makes this an error; set it to "warn" to log '
        "instead. See %s"
        % (
            cls.__module__,
            cls.__qualname__,
            len(parts),
            "; ".join(parts),
            SETTING,
            MIGRATION_GUIDE_URL,
        )
    )


def _log_unsupplied(
    view: Any,
    cls: type,
    reported: "set[str]",
    counts: "list[tuple[str, str]]",
    names: "list[str]",
) -> None:
    for name, stem in counts:
        if name in reported:
            continue
        reported.add(name)
        logger.warning(
            "%s.%s: the template reads {{ %s }} and nothing supplies it, so it renders "
            "empty. djust 1.x generated it from %s; explicit exposure does not. Use "
            "{{ %s|length }} in the template, or return %s from get_context_data(). See %s",
            cls.__module__,
            cls.__qualname__,
            name,
            stem,
            stem,
            name,
            MIGRATION_GUIDE_URL,
        )
    for name in names:
        if name in reported:
            continue
        reported.add(name)
        logger.warning(
            "%s.%s: the template reads {{ %s }} and the view sets %s, but nothing "
            "supplies it to the template, so it renders empty. %s "
            "Persisting it or sending it to the browser takes its own grant "
            "(persist=, client=True). See %s",
            cls.__module__,
            cls.__qualname__,
            name,
            name,
            _advice(view, name),
            MIGRATION_GUIDE_URL,
        )


#: Context-processor names templates use for authorization decisions
#: (``{% if user.is_staff %}``, ``{% if perms.app.change_x %}``). A declared
#: ``state()`` field shadows a same-named context-processor value, as a
#: ``get_context_data()`` key does; for most names (``messages``, ``debug``,
#: ``LANGUAGE_CODE``) that is ordinary and allowed. For these two the shadow
#: silently changes what every template the view renders, including base
#: templates, believes about the current user, so djust.V027 and the DEBUG
#: warning below flag it. Extend this set to flag another name.
AUTHORIZATION_CONTEXT_NAMES = frozenset({"user", "perms"})

#: Shadowed authorization names already reported, by view class.
_SHADOW_REPORTED: "weakref.WeakKeyDictionary[type, set[str]]" = weakref.WeakKeyDictionary()


def warn_shadowed_authorization_names(view: Any, names: Any) -> None:
    """Log, once per view class and name, a declared field shadowing ``user``/``perms``.

    ``names`` are the context-processor keys the view's own context already
    held. Only names in :data:`AUTHORIZATION_CONTEXT_NAMES` that are declared
    ``state()`` fields are reported. ``DEBUG`` only; names, never values;
    never raises.
    """
    try:
        from django.conf import settings

        if getattr(settings, "DEBUG", False) is not True:
            return
        from .mixins.context import rendered_state_names

        cls = type(view)
        declared = set(rendered_state_names(cls))
        reported = _SHADOW_REPORTED.get(cls)
        if reported is None:
            reported = _SHADOW_REPORTED[cls] = set()
        for name in sorted(AUTHORIZATION_CONTEXT_NAMES.intersection(names)):
            if name not in declared or name in reported:
                continue
            reported.add(name)
            logger.warning(
                "%s.%s: the declared state() field %s replaces the %s a context "
                "processor supplies, in every template this view renders, including "
                "base templates that test it for authorization. Rename the field "
                "(for example selected_%s) or declare it with render=False. See "
                "djust.V027.",
                cls.__module__,
                cls.__qualname__,
                name,
                name,
                name,
            )
    except Exception:  # noqa: BLE001 — never let the diagnostic break a render
        logger.debug("Shadowed-authorization-name check could not run")
