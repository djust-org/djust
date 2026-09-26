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

from contextlib import contextmanager
from typing import Iterator


def _belongs_to_installed_app(check: object) -> bool:
    from django.apps import apps

    module = getattr(check, "__module__", None) or ""
    return apps.get_containing_app_config(module) is not None


@contextmanager
def isolated_check_registry() -> Iterator[None]:
    """On exit, drop the checks added inside the block that belong to no
    installed app, and restore any check the block removed.

    In a real project, an uninstalled app's checks are never registered. A
    check added for an app that is still installed is kept, because a check
    module imported for the first time inside a test (djust's own, for
    example) is never imported again, and dropping its checks would lose them
    for the rest of the process. An app installed again later re-registers
    its checks from ``AppConfig.ready()``.
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
