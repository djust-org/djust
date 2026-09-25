"""Render declared assets as tags carrying Subresource Integrity (ADR-040)."""

from __future__ import annotations

import logging

from django.core.exceptions import ImproperlyConfigured
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


def _read_static(path: str) -> bytes:
    """The bytes the app serves for ``path``: the stored (post-processed)
    file when static storage has it, else the source a finder locates
    (runserver before collectstatic)."""
    from django.contrib.staticfiles import finders
    from django.contrib.staticfiles.storage import staticfiles_storage

    stored = path
    stored_name = getattr(staticfiles_storage, "stored_name", None)
    if stored_name is not None:
        try:
            stored = stored_name(path)
        except ValueError:  # not in the manifest yet (not collected)
            stored = path
    try:
        with staticfiles_storage.open(stored) as handle:
            return handle.read()
    except Exception as exc:  # noqa: BLE001 - any backend's "not stored here"; fall back to the source
        # FileNotFoundError is the normal dev path (not collected yet); anything
        # else (permissions, network, credentials — the remote-storage case
        # R8 calls out) is unexpected and worth a WARNING, not silence.
        level = logging.DEBUG if isinstance(exc, FileNotFoundError) else logging.WARNING
        logger.log(
            level,
            "static storage could not open %r (%s: %s); falling back to staticfiles finders",
            stored,
            type(exc).__name__,
            exc,
        )
        found = finders.find(path)
        if not found:
            raise ImproperlyConfigured(
                f"djust asset file {path!r} is in neither static storage nor any "
                f"staticfiles finder (see check djust.B003); static storage raised "
                f"{type(exc).__name__}: {exc}."
            ) from exc
        with open(found, "rb") as handle:
            return handle.read()


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


def asset_tags(name: str, variant: str | None = None) -> SafeString:
    # Every element is format_html output, so joining them is safe.
    return mark_safe("\n".join(_tag(f) for f in _files(name, variant)))


def asset_url(name: str, variant: str | None = None, file_type: str = "module") -> str:
    for file in _files(name, variant):
        if file.type == file_type:
            return _source(file)[0]
    raise ImproperlyConfigured(f"djust asset {name!r} has no {file_type} file.")
