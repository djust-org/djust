"""The one ``render`` frame of a turn and its ``effects`` (#3658, TC9).

Every turn result goes to the client as one frame, on the WebSocket, over SSE
and as the HTTP answer body::

    {"type": "render", "source": "event", "ref": 7, "version": 12,
     "patches": [...] | "html": "..." | "view_id": ..., "html": "...",
     "effects": [{"kind": "push_event", "event": ..., "payload": {...}},
                 {"kind": "flash", "action": "put", "level": ..., "message": ...},
                 {"kind": "page_metadata", "action": "title", "value": ...},
                 {"kind": "layout", "path": ..., "html": ...},
                 {"kind": "navigation", "action": "live_patch", ...},
                 {"kind": "navigate", "to": ...}]}

The DOM part is at most one of ``patches`` (with ``html`` alongside for the
compression fallback), ``html``, or ``view_id`` + ``html`` (an embedded child).
A frame with none of them acknowledges a turn that changed nothing.

:func:`drain_effects` / :func:`adrain_effects` take the queued effects of one
or more views (root first) off their one outbound queue
(:class:`~djust.mixins.commands.CommandsMixin`), in the canonical rank order,
and render a queued layout. :func:`render_frame` builds the frame.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, Iterable, List, Optional, Tuple

logger = logging.getLogger(__name__)

#: The frame type of every turn result.
RENDER = "render"

Effect = Dict[str, Any]


def _queued(view: Any, kinds: Optional[Iterable[str]] = None) -> List[Tuple[str, Dict[str, Any]]]:
    """``view``'s queued effects, drained; ``[]`` for anything that is not a view."""
    if view is None:
        return []
    drain = getattr(view, "_drain_outbound", None)
    if not callable(drain):
        return []
    drained = drain(kinds)
    return drained if isinstance(drained, list) else []


def _layout_failed(view: Any, path: str, exc: BaseException) -> bool:
    """Log a layout render failure; ``True`` when it must be re-raised (DEBUG)."""
    from django.conf import settings
    from django.template.exceptions import TemplateDoesNotExist

    if isinstance(exc, TemplateDoesNotExist):
        logger.warning("set_layout(%r) — template not found; ignoring swap request", path)
        return False
    from ._exposure_diagnostics import log_failure_for

    # Restricted to the view the layout rendered for (ADR-038): a drain can run
    # outside a runtime turn (a push, a tick), where no diagnostic scope is open.
    log_failure_for(
        logger,
        (view,),
        exc,
        "set_layout(%r) — template rendering raised; ignoring swap request",
        path,
        traceback=True,
    )
    return bool(getattr(settings, "DEBUG", False))


def _effect(kind: str, payload: Dict[str, Any]) -> Effect:
    return {"kind": kind, **payload}


def drain_effects(*views: Any, kinds: Optional[Iterable[str]] = None) -> List[Effect]:
    """The queued effects of ``views`` (root first), drained, as wire objects.

    Sync. A queued layout is rendered here with the view's current context;
    ``TemplateDoesNotExist`` warns and drops it, any other failure is logged
    and dropped, and re-raised under ``DEBUG``. ``kinds`` limits the drain to
    those kinds (``flush_push_events()`` takes only ``push_event``).
    """
    kinds = tuple(kinds) if kinds is not None else None
    effects: List[Effect] = []
    for view in views:
        for kind, payload in _queued(view, kinds):
            if kind == "layout":
                from .mixins.layout import render_pending_layout

                path = payload["path"]
                try:
                    html = render_pending_layout(view, path)
                except Exception as exc:  # noqa: BLE001 — a broken layout must not kill the turn
                    if _layout_failed(view, path, exc):
                        raise
                    continue
                effects.append({"kind": "layout", "path": path, "html": html})
            else:
                effects.append(_effect(kind, payload))
    return effects


async def adrain_effects(*views: Any, kinds: Optional[Iterable[str]] = None) -> List[Effect]:
    """:func:`drain_effects` for async callers.

    Inline when no layout is queued; otherwise the layout's context and
    template render take one ``sync_to_async`` hop (#3178), on the session's
    pinned worker when ``worker_threads`` is set.
    """
    kinds = tuple(kinds) if kinds is not None else None
    drained = [(view, _queued(view, kinds)) for view in views]
    effects: List[Effect] = []
    for view, queued in drained:
        for kind, payload in queued:
            if kind != "layout":
                effects.append(_effect(kind, payload))
                continue
            from asgiref.sync import sync_to_async

            from .mixins.layout import render_pending_layout

            path = payload["path"]
            try:
                html = await sync_to_async(render_pending_layout)(view, path)
            except Exception as exc:  # noqa: BLE001 — a broken layout must not kill the wire
                if _layout_failed(view, path, exc):
                    raise
                continue
            effects.append({"kind": "layout", "path": path, "html": html})
    return effects


def _check_dom_part(fields: Dict[str, Any]) -> None:
    if "view_id" in fields and fields.get("view_id") is not None:
        if "patches" in fields:
            raise ValueError("a render frame's embedded child carries html, not patches")
        if "html" not in fields:
            raise ValueError("a render frame's embedded child needs its html")


def render_frame(effects: Optional[List[Effect]] = None, **fields: Any) -> Dict[str, Any]:
    """A ``render`` frame: ``type`` first, the given fields, ``effects`` last.

    ``fields`` must not carry ``type``. The DOM part is checked: an embedded
    child (``view_id``) carries ``html`` and no ``patches``.
    """
    if "type" in fields:
        raise ValueError("render_frame() sets the type itself")
    _check_dom_part(fields)
    frame: Dict[str, Any] = {"type": RENDER}
    frame.update(fields)
    frame["effects"] = list(effects) if effects else []
    return frame


def dom_part(frame: Dict[str, Any]) -> Optional[str]:
    """``"embedded"``, ``"patches"``, ``"html"`` or ``None`` (an acknowledgement)."""
    if frame.get("view_id") is not None and "html" in frame:
        return "embedded"
    if "patches" in frame:
        return "patches"
    if "html" in frame:
        return "html"
    return None


__all__ = [
    "RENDER",
    "adrain_effects",
    "dom_part",
    "drain_effects",
    "render_frame",
]
