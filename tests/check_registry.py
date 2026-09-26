"""Keep an app's Django system checks from outliving the test that installed it (#3177).

Django's check registry is process-global and nothing ever unregisters from
it. A test that sets ``INSTALLED_APPS = ["daphne", ...]`` imports
``daphne.checks``, whose ``@register()`` adds ``daphne.E001`` for the rest of
the worker. Once the settings are restored, daphne is gone from the app
registry but its check is still registered. It then fires on any later
``call_command(..., skip_checks=False)`` or ``run_checks()``. That is how
``test_b008_*`` failed whenever an INSTALLED_APPS test ran first in the same
xdist worker (#3169)::

    pytest python/tests/test_checks_c003_asgi_servers_1630.py \\
           python/djust/tests/test_checks_assets_followups.py -p no:randomly

The root ``conftest.py`` wraps every test in :func:`isolated_check_registry`.
"""

from __future__ import annotations

import functools
from contextlib import contextmanager
from typing import Iterator


def _defining_module(check: object) -> str:
    """The module a check was written in. A ``functools.partial`` reports
    ``functools``, so unwrap it (and any ``__wrapped__`` chain) first."""
    while isinstance(check, functools.partial):
        check = check.func
    check = getattr(check, "__wrapped__", check)
    return getattr(check, "__module__", None) or ""


def _belongs_to_installed_app(check: object) -> bool:
    from django.apps import apps

    return apps.get_containing_app_config(_defining_module(check)) is not None


@contextmanager
def isolated_check_registry() -> Iterator[None]:
    """On exit, drop the checks added inside the block whose defining module
    is not part of an installed app, and restore any check the block removed.

    Django registers a check when its module is *imported*, not when its app
    is installed; ``@register()`` runs at import time. In a normal process a
    module is imported once, for an installed app, so the two coincide. A test
    that overrides ``INSTALLED_APPS`` breaks that: it imports the app's check
    module, and the registration outlives the override. This drops exactly
    those registrations.

    A check added for an app that is still installed is kept, because a check
    module imported for the first time inside a test (djust's own, for
    example) is never imported again, and dropping its checks would lose them
    for the rest of the process. An app installed again later re-registers
    its checks from ``AppConfig.ready()``.

    Limits:
    - A check registered by a module outside every installed app (say, a
      djust module that is not itself an app) is dropped too, if it was first
      imported inside a test. Only that test sees it; later tests do not.
    - An ``INSTALLED_APPS`` override with class or module scope registers
      before the per-test snapshot and so is not undone here. No test does
      that today.
    """
    from django.apps import apps
    from django.core.checks.registry import registry

    before = (set(registry.registered_checks), set(registry.deployment_checks))
    try:
        yield
    finally:
        if apps.ready:
            for current, snapshot in zip(
                (registry.registered_checks, registry.deployment_checks), before
            ):
                for check in current - snapshot:
                    if not _belongs_to_installed_app(check):
                        current.discard(check)
                current.update(snapshot - current)
