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


__all__ = ["DEFERRED", "IMMEDIATE", "MODES", "save_mode", "transport_holds_view"]
