"""DEBUG-only notice for a template name an explicit view holds but does not supply.

Under explicit exposure (ADR-038, the default) a template gets the view's
declared ``state()`` fields (#4050), what ``get_context_data()`` returns and
what the framework's providers hand it. A view written for the attribute walk
sets ``self.count`` in ``mount()`` without declaring it and reads
``{{ count }}`` in its template: that name renders empty, and Django's
template semantics make an unknown name silent. This module makes it audible
while ``DEBUG`` is on.

A declared field is supplied by the base ``get_context_data()``, so it is
reported only when it still did not arrive: it was declared
``state(render=False)``, or the view's ``get_context_data()`` returned a new
dict without calling ``super().get_context_data()``.

The check runs where the render path settles its context
(``ContextMixin._context_for_render``). With ``DEBUG`` off it is one settings
read and returns. It names attributes and never reads their values.
"""

from __future__ import annotations

import logging
import weakref
from types import FunctionType
from typing import Any, Mapping

logger = logging.getLogger("djust.exposure")

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


def warn_unsupplied_template_names(view: Any, context: Any) -> None:
    """Log, once per view class and name, each name that will render empty.

    Only under ``DEBUG`` and only for a view whose policy is ``"explicit"``.
    Never raises: a diagnostic must not break the render it describes.
    """
    try:
        from django.conf import settings

        if getattr(settings, "DEBUG", False) is not True:
            return
        policy = getattr(view, "exposure_policy", None)
        if type(policy) is not str or policy != "explicit":
            return
        if not isinstance(context, Mapping):
            return
        cls = type(view)
        reported = _REPORTED.get(cls)
        if reported is None:
            reported = _REPORTED[cls] = set()
        for name in unsupplied_template_names(view, context):
            if name in reported:
                continue
            reported.add(name)
            logger.warning(
                "%s.%s: the template reads {{ %s }} and the view sets %s, but nothing "
                "supplies it to the template, so it renders empty. %s "
                "Persisting it or sending it to the browser takes its own grant "
                '(persist=, client=True). To keep the 1.x behaviour for now, set exposure_policy = "legacy" on '
                "the view. See %s",
                cls.__module__,
                cls.__qualname__,
                name,
                name,
                _advice(view, name),
                MIGRATION_GUIDE_URL,
            )
    except Exception:  # noqa: BLE001 — never let the diagnostic break a render
        # No exception text: it can carry view values (ADR-038 D-a).
        logger.debug("Unsupplied-template-name check could not run")
