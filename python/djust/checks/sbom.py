"""App SBOM checks, ``djust.B011``-``B014`` (ADR-040)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from django.conf import settings
from django.core.checks import CheckMessage, Error, Tags, Warning, register


def _sbom_path() -> Path | None:
    value = getattr(settings, "DJUST_SBOM_PATH", None)
    return Path(value) if value else None


@register("djust", deploy=True)
def check_sbom_configured(app_configs: Any, **kwargs: Any) -> list[CheckMessage]:
    if _sbom_path() is not None:
        return []
    return [
        Warning(
            "DJUST_SBOM_PATH is not set, so collectstatic writes no SBOM of the "
            "third-party browser code this app serves.",
            hint="Set it to a path outside every directory your web server serves.",
            id="djust.B011",
        )
    ]


@register("djust")
def check_sbom_path(app_configs: Any, **kwargs: Any) -> list[CheckMessage]:
    from djust.assets.sbom import served_directory_containing

    path = _sbom_path()
    if path is None:
        return []
    served = served_directory_containing(path)
    if served is not None:
        return [
            Error(
                f"DJUST_SBOM_PATH ({path}) is inside {served}, which is served to browsers.",
                hint="Move it outside STATIC_ROOT, MEDIA_ROOT and STATICFILES_DIRS.",
                id="djust.B012",
            )
        ]
    return []


@register("djust", Tags.staticfiles, deploy=True)
def check_sbom_collectstatic_order(app_configs: Any, **kwargs: Any) -> list[CheckMessage]:
    if _sbom_path() is None:
        return []
    apps = list(settings.INSTALLED_APPS)
    if "djust" in apps and "django.contrib.staticfiles" in apps:
        if apps.index("djust") > apps.index("django.contrib.staticfiles"):
            return [
                Error(
                    "DJUST_SBOM_PATH is set, but 'djust' comes after 'django.contrib.staticfiles' "
                    "in INSTALLED_APPS, so collectstatic will not write the SBOM.",
                    hint="Move 'djust' above 'django.contrib.staticfiles'.",
                    id="djust.B013",
                )
            ]
    return []


@register("djust", deploy=True)
def check_sbom_current(app_configs: Any, **kwargs: Any) -> list[CheckMessage]:
    from djust.assets.registry import get_registry
    from djust.assets.sbom import digest_of

    path = _sbom_path()
    if path is None:
        return []
    try:
        recorded = digest_of(json.loads(path.read_text(encoding="utf-8")))
    except (OSError, ValueError):
        recorded = None
    if recorded != get_registry().digest():
        return [
            Error(
                f"The SBOM at {path} is missing or does not match the declared assets.",
                hint="Run collectstatic (or manage.py djust_sbom -o PATH) as part of the build.",
                id="djust.B014",
            )
        ]
    return []
