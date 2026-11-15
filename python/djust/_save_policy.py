"""When an explicit turn's state is saved: before its reply, or later.

Every explicit turn over a WebSocket or SSE connection reaches storage
through one place, :meth:`djust.runtime.ViewRuntime.commit_explicit_turn`.
Before writing, that method asks :func:`save_mode` how this turn is saved:

``IMMEDIATE``
    Today's behaviour (ADR-038 E3): the turn's state is in storage before its
    success frame is sent.
``DEFERRED``
    The turn marks the view as changed and sends its frame; a later flush
    (:meth:`~djust.runtime.ViewRuntime.flush_state`) writes the view's current
    state once: no later than ``DJUST_STATE_SAVE_INTERVAL`` seconds after the
    first unsaved change, when the connection closes, and before the view is
    replaced by a navigation.

A turn is deferred only when ALL of these hold; otherwise it is immediate:

1. the transport keeps the view between turns (WebSocket, SSE);
2. the view declares no ``persist="server"`` / ``persist="client"`` field;
3. ``DJUST_STATE_STORE_STRICT`` is off;
4. the interval is not 0;
5. the turn is not a URL change, queued no navigation, its handler wrote
   nothing to ``request.session`` itself, and nothing asked for the save
   (``@event_handler(save=True)``, ``self.save_state()``).

``DJUST_STATE_SAVE_INTERVAL`` defaults to ``0``: every turn is immediate, as
before. A view's ``state_save_interval`` (seconds, ``0``, or ``None`` for the
project's value) overrides it for that view. The HTTP fallback never asks: it rebuilds the view from storage on
every request, so it always saves before it answers.
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)

IMMEDIATE = "immediate"
DEFERRED = "deferred"

#: Every answer :func:`save_mode` can give.
MODES = frozenset({IMMEDIATE, DEFERRED})

#: The project setting: seconds; 0 saves before every reply.
SETTING = "DJUST_STATE_SAVE_INTERVAL"
#: The view class attribute: the same, for one view (None: the project's).
VIEW_ATTRIBUTE = "state_save_interval"
#: Where a turn's request for an immediate save waits for the commit.
SAVE_FLAG = "_djust_save_requested"
#: What the setting may be, besides 0.
MIN_INTERVAL_S = 0.5
MAX_INTERVAL_S = 60.0

_WARNED: set = set()


def _valid(value: Any) -> bool:
    if type(value) not in (int, float):
        return False
    return value == 0 or MIN_INTERVAL_S <= value <= MAX_INTERVAL_S


def within_lifetime(value: float) -> bool:
    """Whether an interval leaves the stored state's half-life refresh room:
    at most half of ``DJUST_SERVER_STATE_MAX_AGE`` (an unreadable lifetime
    leaves no room)."""
    from ._exposure import ExposureError
    from ._exposure_sessions import server_state_max_age

    try:
        return value <= server_state_max_age() / 2
    except ExposureError:
        return False


def _usable(value: Any, where: str) -> float:
    """``value`` as an interval, or 0 with one WARNING per ``where``."""
    if _valid(value) and (value == 0 or within_lifetime(float(value))):
        return float(value)
    if where not in _WARNED:
        _WARNED.add(where)
        logger.warning(
            "djust: %s must be 0 or a number of seconds from %s to %s and at most half of "
            "DJUST_SERVER_STATE_MAX_AGE; it is read as 0, and every turn's state is saved "
            "before its reply (system check djust.C040).",
            where,
            MIN_INTERVAL_S,
            MAX_INTERVAL_S,
        )
    return 0.0


def state_save_interval() -> float:
    """The project's interval in seconds; 0 (the default) saves every turn.

    A value that is not 0 or a number from 0.5 to 60 is read as 0, with one
    WARNING per process (system check ``djust.C040`` reports it at startup).
    """
    try:
        from django.conf import settings

        value = getattr(settings, SETTING, 0)
    except Exception:  # noqa: BLE001 — settings unavailable: the default
        return 0.0
    if type(value) is int and value == 0:
        return 0.0
    return _usable(value, SETTING)


def interval_for(view: Any) -> float:
    """The interval that applies to ``view``: its class's
    ``state_save_interval`` when that is not None, else the project's."""
    from inspect import getattr_static

    value = getattr_static(type(view), VIEW_ATTRIBUTE, None)
    if value is None:
        return state_save_interval()
    owner = type(view)
    return _usable(value, "%s.%s.%s" % (owner.__module__, owner.__qualname__, VIEW_ATTRIBUTE))


def request_save(owner: Any) -> None:
    """Have the current turn of ``owner``'s view saved before its reply.

    ``owner`` is the view, or a component (its view is found through the
    component's parents). Read once, by the turn's commit
    (:func:`take_save_request`)."""
    target = owner
    for _ in range(32):
        parent = vars(target).get("_parent") if hasattr(target, "__dict__") else None
        if parent is None:
            break
        target = parent
    vars(target)[SAVE_FLAG] = True


def take_save_request(view: Any) -> bool:
    """Whether something asked for this turn to be saved now; forgets it."""
    storage = getattr(view, "__dict__", None)
    return bool(storage.pop(SAVE_FLAG, False)) if storage is not None else False


def transport_holds_view(transport: Any) -> bool:
    """Whether ``transport`` keeps the view in this process between turns.

    True for the WebSocket and SSE transports. A transport that does not say
    (a partial test transport) is taken not to: its turns save immediately.
    """
    return getattr(transport, "holds_view", False) is True


def declares_persistence(view: Any) -> bool:
    """Whether ``view`` declares a ``persist="server"`` or ``"client"`` field:
    the developer said those values must be kept, and ADR-038 E3 (saved
    before the success frame) was written for them. A contract that cannot be
    read counts as declaring: the immediate path reports it."""
    from ._exposure import ExposureContract

    try:
        fields = ExposureContract.from_view_class(type(view)).fields
    except Exception:  # noqa: BLE001 — the immediate path raises and reports it
        return True
    return any(field.persist in ("server", "client") for field in fields.values())


def deferrable(view: Any) -> bool:
    """Whether ``view``'s turns may be saved after their reply over a
    transport that holds it (conditions 2-4 of the module docstring)."""
    from . import _per_turn
    from .state_store import strict

    if not _per_turn.on("deferred_save") or interval_for(view) <= 0:
        return False
    if strict():
        return False
    return not declares_persistence(view)


def _wrote_the_session(view: Any) -> bool:
    """Whether the turn's handler changed ``request.session`` itself."""
    from ._session_merge import TrackedSessionData

    request = getattr(view, "_djust_event_request", None)
    data = getattr(getattr(request, "session", None), "_session_cache", None)
    if type(data) is not TrackedSessionData:
        # Not tracked: what changed cannot be told, so it is saved now.
        return request is not None
    return data.has_foreign_changes()


def _navigates(view: Any) -> bool:
    pending = getattr(view, "_pending_navigation", None)
    return bool(pending) if isinstance(pending, list) else False


def save_mode(view: Any, transport: Any, *, source: str) -> str:
    """How the turn ``source`` of ``view`` over ``transport`` is saved.

    ``source`` is the commit's own label (``"event"``, ``"url_change"``,
    ``"async"``). :data:`IMMEDIATE` unless every condition of the module
    docstring holds.
    """
    asked = take_save_request(view)  # one turn's request, read once whatever the answer
    if asked or not transport_holds_view(transport) or not deferrable(view):
        return IMMEDIATE
    if source == "url_change" or _navigates(view) or _wrote_the_session(view):
        return IMMEDIATE
    return DEFERRED


def holder_claims(view: Any) -> bool:
    """Whether restoring ``view``'s saved state claims it (the holder fence).

    A claim is one session write at a socket mount or fallback request that
    restores the tab's state; after it, an older holder of the same tab can
    no longer write (``djust._automatic_state._hold``). Needed only where a
    holder can write after its last turn, which only a deferred save does, so
    only a view whose turns may be deferred claims: with the interval at 0
    (the default) no restore pays the write.
    """
    return deferrable(view)


__all__ = [
    "DEFERRED",
    "IMMEDIATE",
    "MAX_INTERVAL_S",
    "MIN_INTERVAL_S",
    "MODES",
    "SAVE_FLAG",
    "SETTING",
    "VIEW_ATTRIBUTE",
    "deferrable",
    "declares_persistence",
    "holder_claims",
    "interval_for",
    "request_save",
    "save_mode",
    "state_save_interval",
    "take_save_request",
    "transport_holds_view",
    "within_lifetime",
]
