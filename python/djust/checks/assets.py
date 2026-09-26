"""Vendored third-party asset checks, ``djust.B001``-``B010`` (ADR-040).

B011-B014 (the app SBOM) live in ``checks/sbom.py``.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from django.conf import settings
from django.core.checks import CheckMessage, Error, Warning, register
from django.core.exceptions import SuspiciousFileOperation

from .utils import _get_template_dirs, _is_check_suppressed, _iter_template_files, _walk_subclasses

_SBOM_SUFFIXES = (".cdx.json", ".spdx.json", ".bom.json")
_TAG = re.compile(r"<(script|link)\b([^>]*)>", re.I)
# \s before the name keeps data-src / data-href out.
_ATTR = re.compile(r"\s(src|href|rel)\s*=\s*(?:\"([^\"]*)\"|'([^']*)'|([^\s>]+))", re.I)
_EXTERNAL = re.compile(r"^((?:https?:)?//[^/?#]+)", re.I)
# <link> rels that make the browser fetch the href as a resource; canonical,
# alternate, preconnect, dns-prefetch, icon, manifest and the like do not
# load code, so they are not B010's concern.
_LOADING_RELS = frozenset({"stylesheet", "modulepreload", "preload", "prefetch"})
_REBUILD = "Rebuild with `make vendor` (djust) or regenerate your manifest's integrity."
# Checks djust's collectstatic runs, deploy checks included, before it
# collects anything (see management/commands/collectstatic.py).
COLLECTSTATIC_TAG = "djust_collectstatic"


@register("djust")
def check_asset_manifests(app_configs: Any, **kwargs: Any) -> list[CheckMessage]:
    from djust.assets.registry import get_registry

    registry = get_registry()
    messages: list[CheckMessage] = []
    for problem in registry.problems:
        text = f"{problem.source}: {problem.message}"
        if problem.check_id == "djust.B002":
            messages.append(
                Error(text, hint="Pin an exact version and an SPDX license.", id="djust.B002")
            )
        elif problem.check_id == "djust.B006":
            messages.append(
                Error(text, hint='Add the file\'s SRI hash as "integrity".', id="djust.B006")
            )
        else:
            messages.append(
                Error(text, hint="See the Vendoring third-party JS guide.", id="djust.B001")
            )
    if not _is_check_suppressed("B009"):
        for shadow in registry.shadows:
            won = ", ".join(p.purl for p in shadow.winner.packages)
            lost = ", ".join(p.purl for p in shadow.loser.packages)
            messages.append(
                Warning(
                    f"Asset {shadow.name!r} from {shadow.winner.source} ({won}) overrides "
                    f"{shadow.loser.source} ({lost}).",
                    hint="Intended overrides can be silenced with suppress_checks: ['B009'].",
                    id="djust.B009",
                )
            )
    return messages


@register("djust")
def check_asset_files(app_configs: Any, **kwargs: Any) -> list[CheckMessage]:
    from django.contrib.staticfiles import finders

    from djust.assets.manifest import sri
    from djust.assets.registry import get_registry

    allow_external = getattr(settings, "DJUST_ALLOW_EXTERNAL_ASSETS", False)
    messages: list[CheckMessage] = []
    for asset in get_registry().assets.values():
        if asset.external and not allow_external:
            messages.append(
                Error(
                    f"Asset {asset.name!r} ({asset.source}) loads from an external origin.",
                    hint="Vendor the file, or set DJUST_ALLOW_EXTERNAL_ASSETS = True.",
                    id="djust.B005",
                )
            )
        for file in asset.files:
            if file.path is None:
                continue
            try:
                found = finders.find(file.path)
            except SuspiciousFileOperation as exc:
                messages.append(
                    Error(
                        f"Asset {asset.name!r}: {file.path!r} resolves outside the static "
                        f"directories ({asset.source}): {exc}.",
                        hint="Use a relative static path with no '..' segment.",
                        id="djust.B003",
                    )
                )
                continue
            if not found:
                messages.append(
                    Error(
                        f"Asset {asset.name!r}: {file.path!r} is not found by any staticfiles finder.",
                        hint="Check the path and that the owning app or STATICFILES_DIRS is configured.",
                        id="djust.B003",
                    )
                )
                continue
            alg = file.integrity.split("-", 1)[0]
            try:
                with open(found, "rb") as handle:
                    actual = sri(handle.read(), alg)
            except OSError as exc:
                messages.append(
                    Error(
                        f"Asset {asset.name!r}: {file.path!r} could not be read to verify its "
                        f"integrity ({asset.source}): {exc}.",
                        hint="Check the file's permissions.",
                        id="djust.B004",
                    )
                )
                continue
            if actual != file.integrity:
                messages.append(
                    Error(
                        f"Asset {asset.name!r}: {file.path!r} does not match its manifest "
                        f"integrity ({asset.source}). The file was edited or replaced "
                        "without updating the declared versions.",
                        hint=_REBUILD,
                        id="djust.B004",
                    )
                )
    return messages


@register("djust")
def check_required_assets(app_configs: Any, **kwargs: Any) -> list[CheckMessage]:
    from djust.assets.registry import get_registry
    from djust.components.base import Component

    declared = get_registry().assets
    messages: list[CheckMessage] = []
    for cls in _walk_subclasses(Component):
        for name in getattr(cls, "requires_assets", ()):
            if name not in declared:
                messages.append(
                    Error(
                        f"{cls.__module__}.{cls.__qualname__}.requires_assets names {name!r}, "
                        "which no djust_assets.json declares.",
                        hint="Declare it, or install the package that ships it.",
                        id="djust.B007",
                    )
                )
    return messages


# A deploy check (#3144): it lists every static file, which runserver,
# autoreload and migrate should not pay for on every start. It still runs
# under `check --deploy` and, through COLLECTSTATIC_TAG, before djust's
# collectstatic publishes anything.
@register("djust", COLLECTSTATIC_TAG, deploy=True)
def check_sbom_not_served(app_configs: Any, **kwargs: Any) -> list[CheckMessage]:
    from django.contrib.staticfiles import finders

    messages: list[CheckMessage] = []
    for finder in finders.get_finders():
        for path, _storage in finder.list([]):
            if path.endswith(_SBOM_SUFFIXES):
                messages.append(
                    Error(
                        f"{path!r} is a static file: collectstatic would publish this SBOM.",
                        hint="Move it outside every static directory; ship it in the package or dist-info.",
                        id="djust.B008",
                    )
                )
    return messages


def _external_loads(line: str) -> list[str]:
    """External origins that ``<script src>`` and resource-loading ``<link>``
    tags on ``line`` fetch from."""
    refs = []
    for tag, attrs in _TAG.findall(line):
        values = {m[0].lower(): m[1] or m[2] or m[3] for m in _ATTR.findall(attrs)}
        if tag.lower() == "script":
            url = values.get("src", "")
        elif _LOADING_RELS & set(values.get("rel", "").lower().split()):
            url = values.get("href", "")
        else:
            continue
        match = _EXTERNAL.match(url.strip())
        if match:
            refs.append(match.group(1))
    return refs


def _host(entry: str) -> str:
    """``js.stripe.com`` from ``js.stripe.com``, ``https://js.stripe.com`` or
    ``//js.stripe.com/v3/``, lower-cased."""
    entry = entry.strip()
    return urlsplit(entry if "//" in entry else "//" + entry).netloc.lower()


def _allowed_origins() -> tuple[frozenset[str], list[CheckMessage]]:
    """``DJUST_ALLOWED_EXTERNAL_ORIGINS`` as hosts, plus a B010 warning when
    the setting isn't a list of strings (then nothing is allowed)."""
    value = getattr(settings, "DJUST_ALLOWED_EXTERNAL_ORIGINS", None)
    if value is None:
        return frozenset(), []
    if isinstance(value, (list, tuple, set, frozenset)) and all(
        isinstance(entry, str) for entry in value
    ):
        return frozenset(_host(entry) for entry in value), []
    return frozenset(), [
        Warning(
            "DJUST_ALLOWED_EXTERNAL_ORIGINS must be a list of hostnames such as "
            f'["js.stripe.com"], not {type(value).__name__} ({value!r}); it is ignored.',
            hint="List each origin that can't be vendored or pinned, one string per host.",
            id="djust.B010",
        )
    ]


@register("djust")
def check_undeclared_origins(app_configs: Any, **kwargs: Any) -> list[CheckMessage]:
    """B010: templates that load code from an origin no manifest declares
    and ``DJUST_ALLOWED_EXTERNAL_ORIGINS`` does not list.

    Scans line by line, so a ``<script>`` or ``<link>`` tag split across
    lines is not seen.
    """
    if _is_check_suppressed("B010"):
        return []
    from djust.assets.registry import get_registry

    declared = {
        urlsplit(f.url).netloc
        for asset in get_registry().assets.values()
        for f in asset.files
        if f.url
    }
    allowed, messages = _allowed_origins()
    for template_path in _iter_template_files(_get_template_dirs()):
        try:
            content = Path(template_path).read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        lines = content.splitlines()
        for lineno, line in enumerate(lines, start=1):
            if "noqa: B010" in line:
                continue
            for ref in _external_loads(line):
                origin = urlsplit(ref if ref.startswith("http") else "https:" + ref).netloc
                if origin not in declared and origin.lower() not in allowed:
                    messages.append(
                        Warning(
                            f"{template_path}:{lineno} loads from {origin}, which no manifest declares; "
                            "scanners will not see what it serves.",
                            hint="Vendor it and declare it, declare it as external with integrity, "
                            "list an origin that can't be pinned in DJUST_ALLOWED_EXTERNAL_ORIGINS, "
                            "or add {# noqa: B010 #} to the line.",
                            id="djust.B010",
                        )
                    )
    return messages
