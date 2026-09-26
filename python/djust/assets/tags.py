"""Render declared assets as tags carrying Subresource Integrity (ADR-040)."""

from __future__ import annotations

import logging

from django.core.exceptions import ImproperlyConfigured, SuspiciousFileOperation
from django.utils.html import format_html
from django.utils.safestring import SafeString, mark_safe

from .manifest import Asset, AssetFile, sri
from .registry import get_registry

logger = logging.getLogger("djust.assets")

_integrity_cache: dict[tuple[str, str], str] = {}


def clear_integrity_cache() -> None:
    _integrity_cache.clear()


def get_asset(name: str) -> Asset:
    assets = get_registry().assets
    try:
        return assets[name]
    except KeyError:
        declared = ", ".join(sorted(assets)) or "none"
        raise ImproperlyConfigured(
            f"No djust asset named {name!r} is declared (declared: {declared}). "
            "Declare it in a djust_assets.json manifest."
        ) from None


def _outside_static(path: str, exc: Exception) -> ImproperlyConfigured:
    return ImproperlyConfigured(
        f"djust asset file {path!r} resolves outside the static directories "
        f"({type(exc).__name__}: {exc}); asset paths must be relative static paths "
        "(see check djust.B001)."
    )


def _read_finder(path: str) -> bytes | None:
    """The source file a staticfiles finder locates, or None."""
    from django.contrib.staticfiles import finders

    try:
        found = finders.find(path)
    except SuspiciousFileOperation as exc:
        raise _outside_static(path, exc) from exc
    if not found:
        return None
    with open(found, "rb") as handle:
        return handle.read()


def _read_storage(path: str) -> bytes:
    """The stored (post-processed) file static storage serves for ``path``."""
    from django.contrib.staticfiles.storage import staticfiles_storage

    stored = path
    stored_name = getattr(staticfiles_storage, "stored_name", None)
    if stored_name is not None:
        try:
            stored = stored_name(path)
        except ValueError:  # not in the manifest yet (not collected)
            stored = path
    with staticfiles_storage.open(stored) as handle:
        return handle.read()


def _read_static(path: str) -> bytes:
    """The bytes the app serves for ``path``.

    With ``DEBUG`` on, runserver (and WhiteNoise's ``USE_FINDERS``) serve the
    source a finder locates, so that comes first: a previously collected
    STATIC_ROOT may hold a stale copy whose hash the browser would reject.
    With ``DEBUG`` off the stored (post-processed) file is what is served, so
    static storage comes first and the finders are the fallback.
    """
    from django.conf import settings

    if settings.DEBUG:
        found = _read_finder(path)
        if found is not None:
            return found
        try:
            return _read_storage(path)
        except SuspiciousFileOperation as exc:
            raise _outside_static(path, exc) from exc
        except Exception as exc:  # noqa: BLE001 - any backend's "not stored here"
            raise ImproperlyConfigured(
                f"djust asset file {path!r} is in neither any staticfiles finder nor "
                f"static storage (see check djust.B003); static storage raised "
                f"{type(exc).__name__}: {exc}."
            ) from exc

    try:
        return _read_storage(path)
    except SuspiciousFileOperation as exc:
        raise _outside_static(path, exc) from exc
    except Exception as exc:  # noqa: BLE001 - any backend's "not stored here"; fall back to the source
        # FileNotFoundError (not collected yet) and ImproperlyConfigured
        # (STATIC_ROOT unset) are the normal dev paths; anything else
        # (permissions, network, credentials — the remote-storage case R8
        # calls out) is unexpected and worth a WARNING, not silence.
        expected = isinstance(exc, (FileNotFoundError, ImproperlyConfigured))
        logger.log(
            logging.DEBUG if expected else logging.WARNING,
            "static storage could not open %r (%s: %s); falling back to staticfiles finders",
            path,
            type(exc).__name__,
            exc,
        )
        found = _read_finder(path)
        if found is None:
            raise ImproperlyConfigured(
                f"djust asset file {path!r} is in neither static storage nor any "
                f"staticfiles finder (see check djust.B003); static storage raised "
                f"{type(exc).__name__}: {exc}."
            ) from exc
        return found


def stored_integrity(path: str, alg: str) -> str:
    key = (path, alg)
    if key not in _integrity_cache:
        _integrity_cache[key] = sri(_read_static(path), alg)
    return _integrity_cache[key]


def _static_is_cross_origin() -> bool:
    from django.conf import settings

    return str(settings.STATIC_URL or "").startswith(("http://", "https://", "//"))


def _source(file: AssetFile) -> tuple[str, str, bool]:
    if file.url is not None:
        return file.url, file.integrity, True
    from django.templatetags.static import static

    alg = file.integrity.split("-", 1)[0]
    return static(file.path), stored_integrity(str(file.path), alg), _static_is_cross_origin()


def _tag(file: AssetFile) -> SafeString:
    src, integrity, cross_origin = _source(file)
    cors = mark_safe(' crossorigin="anonymous"') if cross_origin else ""  # constant markup
    if file.type == "style":
        return format_html(
            '<link rel="stylesheet" href="{}" integrity="{}"{}>', src, integrity, cors
        )
    if file.type == "module":
        return format_html(
            '<link rel="modulepreload" href="{}" integrity="{}"{}>', src, integrity, cors
        )
    return format_html('<script src="{}" integrity="{}"{}></script>', src, integrity, cors)


def _files(name: str, variant: str | None) -> tuple[AssetFile, ...]:
    asset = get_asset(name)
    if variant is not None and variant not in asset.variants:
        available = ", ".join(asset.variants) or "none"
        raise ImproperlyConfigured(
            f"djust asset {name!r} has no variant {variant!r} (available: {available})."
        )
    return asset.files_for(variant)


def asset_tags(name: str, variant: str | None = None, file_type: str | None = None) -> SafeString:
    """Tags for every file of ``name`` (and ``variant``); only the files of
    ``file_type`` ("script", "module", "style") when it is given."""
    files = [f for f in _files(name, variant) if file_type is None or f.type == file_type]
    # Every element is format_html output, so joining them is safe.
    return mark_safe("\n".join(_tag(f) for f in files))


def asset_source(
    name: str, variant: str | None = None, file_type: str = "module"
) -> tuple[str, str, bool]:
    """``(url, integrity, cross_origin)`` of the first ``file_type`` file of
    ``name``: what a script building the element in the browser needs to
    apply the same Subresource Integrity and ``crossorigin`` rule as
    :func:`asset_tags`."""
    for file in _files(name, variant):
        if file.type == file_type:
            return _source(file)
    raise ImproperlyConfigured(f"djust asset {name!r} has no {file_type} file.")


def asset_url(name: str, variant: str | None = None, file_type: str = "module") -> str:
    return asset_source(name, variant, file_type)[0]
