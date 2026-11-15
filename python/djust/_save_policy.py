"""When an explicit turn's state is saved: before its reply, or later.

Every explicit turn over a WebSocket or SSE connection reaches storage
through one place, :meth:`djust.runtime.ViewRuntime.commit_explicit_turn`.
Before writing, that method asks :func:`save_mode` how this turn is saved:

``IMMEDIATE``
    Today's behaviour (ADR-038 E3): the turn's state is in storage before its
    success frame is sent.
``DEFERRED``
    The turn marks the view as changed and sends its frame; a later flush
    writes the view's current state once (design: save-frequency).

In this release the answer is always ``IMMEDIATE``: the seam exists so the
deferred path can be added behind a setting without touching the call sites.
The HTTP fallback never asks: it rebuilds the view from storage on every
request, so it always saves before it answers.
"""

from __future__ import annotations

from typing import Any

IMMEDIATE = "immediate"
DEFERRED = "deferred"

#: Every answer :func:`save_mode` can give.
MODES = frozenset({IMMEDIATE, DEFERRED})


def transport_holds_view(transport: Any) -> bool:
    """Whether ``transport`` keeps the view in this process between turns.

    True for the WebSocket and SSE transports. A transport that does not say
    (a partial test transport) is taken not to: its turns save immediately.
    """
    return getattr(transport, "holds_view", False) is True


def save_mode(view: Any, transport: Any, *, source: str) -> str:
    """How the turn ``source`` of ``view`` over ``transport`` is saved.

    ``source`` is the commit's own label (``"event"``, ``"url_change"``,
    ``"async"``). Always :data:`IMMEDIATE` in this release.
    """
    return IMMEDIATE


def holder_claims(view: Any) -> bool:
    """Whether restoring ``view``'s saved state claims it (the holder fence).

    A claim is one session write at a socket mount or fallback request that
    restores the tab's state; after it, an older holder of the same tab can
    no longer write (``djust._automatic_state._hold``). It is needed only
    where a holder can write after its last turn, which only a deferred save
    does. False in this release: no turn is deferred, and no restore pays
    the write.
    """
    return False


__all__ = [
    "DEFERRED",
    "IMMEDIATE",
    "MODES",
    "holder_claims",
    "save_mode",
    "transport_holds_view",
]
