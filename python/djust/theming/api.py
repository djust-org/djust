"""Request-scoped theming helpers.

The documented way to work with theming from a view or function. Each is a
thin wrapper over :class:`~djust.theming.manager.ThemeManager` — the manager
is the mechanism, this is the surface.

Every function takes ``request``, because theme state is per-request: the
active pack and mode live in the session (or a cookie for anonymous users),
so "the active pack" is only meaningful relative to a request. A module-level
``get_active_pack()`` with no request could not answer the question.

    from djust.theming import get_active_pack, set_active_mode

    pack = get_active_pack(request)          # ThemePack | None
    set_active_mode(request, "dark")         # True if applied
"""

from __future__ import annotations

import logging
from typing import Any

from ._registry_accessor import get_registry
from .manager import get_theme_manager
from .theme_packs import get_theme_pack

logger = logging.getLogger(__name__)


def _manager(request: Any) -> Any:
    return get_theme_manager(request)


def get_active_pack(request: Any) -> Any:
    """Return the active :class:`ThemePack`, or ``None``.

    ``None`` is the normal answer when the project has not selected a pack —
    a design system and colour preset are in effect instead. That is not an
    error, so it is not signalled as one.
    """
    state = _manager(request).get_state()
    if not state.pack:
        return None
    return get_theme_pack(state.pack)


def set_active_pack(request: Any, name: str | None) -> bool:
    """Make ``name`` the active pack.

    Returns ``False`` for an unknown pack, leaving the current state alone
    rather than half-applying a change. Pass ``None`` to clear the pack and
    fall back to the design system + preset.
    """
    if name is not None and not (get_registry().has_pack(name) or get_theme_pack(name)):
        return False
    return bool(_manager(request).set_pack(name))


def get_active_mode(request: Any) -> str:
    """Return ``'light'`` or ``'dark'`` — the *resolved* mode.

    Resolved, not the raw setting: a user on ``'system'`` is actually seeing
    light or dark, and callers overriding a mode want to know which.
    """
    return _manager(request).get_state().resolved_mode


def set_active_mode(request: Any, mode: str) -> bool:
    """Set the mode. ``mode`` is ``'light'``, ``'dark'`` or ``'system'``."""
    return bool(_manager(request).set_mode(mode))


def reset_to_defaults(request: Any) -> None:
    """Clear the session's stored theme choice.

    Used on logout, or wherever a user's earlier pick should stop following
    them. Returns to whatever the project configures as default.
    """
    _manager(request).reset()


def get_theme_css_url(request: Any) -> str:
    """URL of the stylesheet for the currently active theme.

    For injecting into a custom asset pipeline. Most projects should use the
    ``{{ theme_head }}`` context variable instead, which emits the ``<link>``
    plus the inline critical CSS.

    The query string is a cache key derived from the active state, so a theme
    switch produces a new URL and cannot be served from a stale cache.
    """
    from django.urls import NoReverseMatch, reverse

    state = _manager(request).get_state()

    try:
        url = reverse("djust_theming:theme_css")
    except NoReverseMatch:
        # The theming URLs are not mounted. Returning the conventional path is
        # more useful to a caller than raising from a helper whose whole job is
        # to hand back a string.
        logger.debug("djust theming URLs are not mounted; returning the default path")
        url = "/djust/theme/theme.css"

    key = f"{state.pack or state.theme}-{state.preset}-{state.resolved_mode}"
    return f"{url}?v={abs(hash(key)) & 0xFFFFFFFF:08x}"


__all__ = [
    "get_active_pack",
    "set_active_pack",
    "get_active_mode",
    "set_active_mode",
    "reset_to_defaults",
    "get_theme_css_url",
]
