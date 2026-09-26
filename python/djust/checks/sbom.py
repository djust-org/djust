"""App SBOM checks, ``djust.B011``-``B014`` (ADR-040)."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from django.conf import settings
from django.core.checks import CheckMessage, Error, Tags, Warning, register


def _sbom_setting() -> Any:
    return getattr(settings, "DJUST_SBOM_PATH", None)


def _sbom_path() -> Path | None:
    """The configured SBOM path; None when unset or not a path (B012 reports
    a non-path value, so the other checks skip it instead of raising)."""
    value = _sbom_setting()
    if not value or not isinstance(value, (str, os.PathLike)):
        return None
    return Path(value)


_SUFFIX_NOTE = (
    " Name the file *.cdx.json as well: scanners such as osv-scanner only "
    "recognise a CycloneDX SBOM by that suffix."
)


@register("djust", deploy=True)
def check_sbom_configured(app_configs: Any, **kwargs: Any) -> list[CheckMessage]:
    if _sbom_setting():
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

    value = _sbom_setting()
    if value and not isinstance(value, (str, os.PathLike)):
        return [
            Error(
                f"DJUST_SBOM_PATH must be a str or os.PathLike naming a file, "
                f"not {type(value).__name__} ({value!r}).",
                hint="Set it to a path outside STATIC_ROOT, MEDIA_ROOT and STATICFILES_DIRS.",
                id="djust.B012",
            )
        ]
    path = _sbom_path()
    if path is None:
        return []
    served = served_directory_containing(path)
    if served is not None:
        hint = "Move it outside STATIC_ROOT, MEDIA_ROOT and STATICFILES_DIRS."
        if not path.name.endswith(".cdx.json"):
            hint += _SUFFIX_NOTE
        return [
            Error(
                f"DJUST_SBOM_PATH ({path}) is inside {served}, which is served to browsers.",
                hint=hint,
                id="djust.B012",
            )
        ]
    return []


@register("djust", Tags.staticfiles, deploy=True)
def check_sbom_collectstatic_order(app_configs: Any, **kwargs: Any) -> list[CheckMessage]:
    from django.core.management import get_commands

    if _sbom_path() is None:
        return []
    # Ask Django which app's collectstatic wins, rather than comparing the
    # positions of the literal "djust" and staticfiles: a third app that
    # overrides collectstatic skips djust's SBOM step just the same.
    owner = get_commands().get("collectstatic")
    if owner == "djust":
        return []
    return [
        Error(
            f"DJUST_SBOM_PATH is set, but collectstatic resolves to {owner}, so the "
            "SBOM won't be written.",
            hint=(
                "Put 'djust' above 'django.contrib.staticfiles' (and any other app that "
                "overrides collectstatic) in INSTALLED_APPS, or run "
                "`manage.py djust_sbom -o PATH` as part of the build."
            ),
            id="djust.B013",
        )
    ]


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
