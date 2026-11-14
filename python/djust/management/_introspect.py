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

from djust._framework_code import is_djust_module
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

    Classes defined in djust's own modules are framework classes and should be
    skipped by commands that audit "user views." The exception is test modules
    and example projects, which are user-shaped and should be included when
    reachable: a module inside a ``tests`` / ``examples`` package, or a
    ``test_*`` module.

    Decided by the module's dotted segments, not by a substring: a framework
    module whose NAME merely contains "example" (``djust.components.
    interactive_examples``, the documented menu demo) is framework code, so its
    views stay out however they got imported (#4129).
    """
    mod = getattr(cls, "__module__", "") or ""
    if is_djust_module(mod):
        return _is_djust_test_or_example_module(mod)
    return True


#: Packages inside djust whose classes are user-shaped (tests, example projects).
_USER_SHAPED_PACKAGES = frozenset({"tests", "examples"})


def _is_djust_test_or_example_module(module: str) -> bool:
    """``djust.tests.x`` / ``djust.examples.x`` / ``djust.x.test_y``: yes; anything else: no."""
    parts = module.split(".")[1:]
    if not parts:
        return False
    if any(part in _USER_SHAPED_PACKAGES for part in parts[:-1]):
        return True
    last = parts[-1]
    return last == "tests" or last.startswith("test_")


def app_label_for_class(cls: type) -> str:
    """Return the Django-app-label-shaped prefix of a class's module path.

    For ``myapp.views.FooView`` this returns ``"myapp"``. Unlike Django's
    ``apps.get_containing_app_config``, this never needs the app registry to be
    ready — it works at import time and is safe to call during management
    command startup.
    """
    mod = getattr(cls, "__module__", "") or ""
    return mod.split(".", 1)[0] if mod else ""
