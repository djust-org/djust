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
from types import FunctionType
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


def unsupplied_names_mode() -> str:
    """The ``DJUST_UNSUPPLIED_NAMES`` mode; any other value is a configuration error.

    A typo must not silently turn strictness off, so it raises instead of falling back.
    """
    from django.conf import settings
    from django.core.exceptions import ImproperlyConfigured

    mode = getattr(settings, SETTING, "debug")
    if type(mode) is not str or mode not in _MODES:
        raise ImproperlyConfigured(
            "%s must be one of %s; got %s."
            % (SETTING, ", ".join('"%s"' % m for m in _MODES), repr(mode))
        )
    return mode


#: Where the warning sends a reader.
MIGRATION_GUIDE_URL = "https://docs.djust.org/guides/migrating-to-2.0/"

#: Names already reported, by view class. Weak: view classes can be collected.
_REPORTED: "weakref.WeakKeyDictionary[type, set[str]]" = weakref.WeakKeyDictionary()

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


def unsupplied_template_names(view: Any, context: Mapping[str, Any]) -> list[str]:
    """Names the view's template reads, the view holds, and ``context`` lacks."""
    paths = view.template_paths()
    return sorted(
        name
        for name in paths
        if type(name) is str and name not in context and _held_by_view(view, name)
    )


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
        if _is_sized_collection(context[stem]):
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
        paths = view.template_paths()
        counts = unsupplied_count_names(view, context, paths)
        names = unsupplied_template_names(view, context)
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
