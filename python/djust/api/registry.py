"""Registry of LiveView classes that expose handlers via HTTP API.

Slug derivation:
  1. Explicit: ``view_cls.api_name`` (class attribute) — preferred for stable URLs.
  2. Fallback: ``<app_label>.<ClassNameLower>`` based on the module path.

The registry is populated lazily on first access by walking all subclasses of
``LiveView`` that have at least one ``@event_handler(expose_api=True)`` method.
Duplicate slugs raise ``ImproperlyConfigured``.
"""

from __future__ import annotations

import threading
from typing import Dict, Iterator, List, Optional, Tuple, Type

from django.core.exceptions import ImproperlyConfigured

_registry: Dict[str, Type] = {}
_explicit: Dict[str, Type] = {}
_duplicates: Dict[str, List[Type]] = {}
_registry_lock = threading.Lock()
_registry_built = False


def _iter_live_view_subclasses(cls) -> Iterator[Type]:
    for sub in cls.__subclasses__():
        yield sub
        yield from _iter_live_view_subclasses(sub)


def _has_exposed_handler(view_cls) -> bool:
    for name in dir(view_cls):
        if name.startswith("_"):
            continue
        attr = getattr(view_cls, name, None)
        if not callable(attr):
            continue
        meta = getattr(attr, "_djust_decorators", None)
        if meta and meta.get("event_handler", {}).get("expose_api"):
            return True
    return False


def _has_server_function(view_cls) -> bool:
    """Return True if ``view_cls`` has at least one ``@server_function`` method.

    Mirrors :func:`_has_exposed_handler` so the registry can include views
    that only expose RPC endpoints (``@server_function``), not
    ``@event_handler(expose_api=True)`` HTTP handlers.
    """
    for name in dir(view_cls):
        if name.startswith("_"):
            continue
        attr = getattr(view_cls, name, None)
        if not callable(attr):
            continue
        meta = getattr(attr, "_djust_decorators", None)
        if meta and meta.get("server_function"):
            return True
    return False


def _derive_slug(view_cls) -> str:
    explicit = getattr(view_cls, "api_name", None)
    if explicit:
        return str(explicit)
    module = view_cls.__module__ or ""
    # Best-effort app label from module path (e.g., "myapp.views" -> "myapp")
    app_label = module.split(".")[0] if module else "djust"
    return f"{app_label}.{view_cls.__name__.lower()}"


def _build_registry() -> None:
    """Walk all LiveView subclasses and register those with exposed handlers.

    Duplicate slugs are recorded but do not raise until a caller actually
    resolves the conflicting slug — this keeps the registry resilient when
    unrelated parts of a process (or test suite) happen to share a slug.
    """
    global _registry_built
    # Import here to avoid a circular import at module load.
    from djust.live_view import LiveView

    new: Dict[str, Type] = {}
    dupes: Dict[str, List[Type]] = {}
    for view_cls in _iter_live_view_subclasses(LiveView):
        # Register the view if it has EITHER an exposed @event_handler OR a
        # @server_function method. A single registry covers both endpoint
        # kinds; ``dispatch_api`` / ``dispatch_server_function`` independently
        # enforce which decorator they accept.
        if not (_has_exposed_handler(view_cls) or _has_server_function(view_cls)):
            continue
        slug = _derive_slug(view_cls)
        existing = new.get(slug)
        if existing is None:
            new[slug] = view_cls
        elif existing is view_cls:
            continue
        else:
            dupes.setdefault(slug, [existing]).append(view_cls)
    # Layer explicit registrations on top — they take precedence over auto-walk.
    new.update(_explicit)
    _registry.clear()
    _registry.update(new)
    _duplicates.clear()
    _duplicates.update(dupes)
    _registry_built = True


def register_api_view(slug: str, view_cls: Type) -> None:
    """Explicitly register a view class under a slug (test/custom cases).

    Explicit registrations survive registry rebuilds and take precedence over
    the auto-walk derivation.
    """
    with _registry_lock:
        existing = _explicit.get(slug)
        if existing is not None and existing is not view_cls:
            raise ImproperlyConfigured(
                f"Duplicate djust API slug {slug!r}: {existing!r} vs {view_cls!r}"
            )
        _explicit[slug] = view_cls
        _registry[slug] = view_cls
        # Also clear from the duplicates map — an explicit registration is an
        # intentional override.
        _duplicates.pop(slug, None)


def resolve_api_view(slug: str) -> Optional[Type]:
    """Look up a view class by slug, building the registry on first access.

    Raises :class:`ImproperlyConfigured` if the slug has multiple registered
    view classes (duplicate ``api_name``) — the developer must resolve the
    ambiguity explicitly.
    """
    with _registry_lock:
        if not _registry_built:
            _build_registry()
        dupes = _duplicates.get(slug)
        if dupes:
            names = ", ".join(f"{c.__module__}.{c.__name__}" for c in dupes)
            raise ImproperlyConfigured(
                f"Duplicate djust API slug {slug!r}: {names}. "
                f"Set a distinct ``api_name`` on at least one of the views."
            )
        return _registry.get(slug)


def get_api_view_registry() -> Dict[str, Type]:
    """Return a copy of the registry (building it if needed)."""
    with _registry_lock:
        if not _registry_built:
            _build_registry()
        return dict(_registry)


def reset_registry() -> None:
    """Reset the registry — used by tests to force a rebuild."""
    global _registry_built
    with _registry_lock:
        _registry.clear()
        _explicit.clear()
        _duplicates.clear()
        _registry_built = False


def iter_exposed_handlers() -> Iterator[Tuple[str, Type, str, callable]]:
    """Yield ``(slug, view_cls, handler_name, handler)`` for every exposed handler."""
    for slug, view_cls in get_api_view_registry().items():
        for name in dir(view_cls):
            if name.startswith("_"):
                continue
            attr = getattr(view_cls, name, None)
            if not callable(attr):
                continue
            meta = getattr(attr, "_djust_decorators", None)
            if meta and meta.get("event_handler", {}).get("expose_api"):
                yield slug, view_cls, name, attr


def iter_server_functions() -> Iterator[Tuple[str, Type, str, callable]]:
    """Yield ``(slug, view_cls, function_name, function)`` for every @server_function."""
    for slug, view_cls in get_api_view_registry().items():
        for name in dir(view_cls):
            if name.startswith("_"):
                continue
            attr = getattr(view_cls, name, None)
            if not callable(attr):
                continue
            meta = getattr(attr, "_djust_decorators", None)
            if meta and meta.get("server_function"):
                yield slug, view_cls, name, attr
