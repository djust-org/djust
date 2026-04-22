"""Shared class-introspection helpers used by multiple management commands.

Three management commands — ``djust_audit``, ``djust_doctor``, and
``djust_typecheck`` — independently grew near-identical implementations of
"walk every LiveView subclass defined in user code." The duplication has been
flagged during code review on PR #849; this module centralizes the shared
helpers.

Anything more than class-walking / user-vs-framework filtering belongs elsewhere
— this file stays deliberately small.
"""

from __future__ import annotations

from typing import Iterable


def walk_subclasses(cls: type) -> Iterable[type]:
    """Recursively yield every subclass of ``cls`` at any depth.

    Duplicates are skipped — diamond inheritance would otherwise repeat a class.
    """
    seen: set = set()
    stack: list = list(cls.__subclasses__())
    while stack:
        sub = stack.pop()
        if sub in seen:
            continue
        seen.add(sub)
        yield sub
        stack.extend(sub.__subclasses__())


def is_user_class(cls: type) -> bool:
    """Return True if ``cls`` is user-defined (not internal djust framework code).

    Classes defined in ``djust.*`` or ``djust_*`` modules are framework classes
    and should be skipped by commands that audit "user views." The exception is
    test modules and example projects, which are user-shaped and should be
    included when reachable.
    """
    mod = getattr(cls, "__module__", "") or ""
    if mod.startswith("djust.") or mod.startswith("djust_"):
        if "test" not in mod and "example" not in mod:
            return False
    return True


def app_label_for_class(cls: type) -> str:
    """Return the Django-app-label-shaped prefix of a class's module path.

    For ``myapp.views.FooView`` this returns ``"myapp"``. Unlike Django's
    ``apps.get_containing_app_config``, this never needs the app registry to be
    ready — it works at import time and is safe to call during management
    command startup.
    """
    mod = getattr(cls, "__module__", "") or ""
    return mod.split(".", 1)[0] if mod else ""
