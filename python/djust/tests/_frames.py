"""Reading ``render`` frames in tests (#3658, TC9).

Every turn result is one ``{"type": "render", ..., "effects": [...]}`` frame:
the DOM part is at most one of ``patches`` (``html`` alongside for the
compression fallback), ``html``, or ``view_id`` + ``html`` (an embedded child),
and the side effects ride in ``effects``. See ``djust._render_frame``.
"""

from __future__ import annotations

from typing import Any, Dict, Iterable, List, Optional, Union

from djust._render_frame import dom_part

Frame = Dict[str, Any]

__all__ = [
    "dom_part",
    "effects_of",
    "frame_matches",
    "is_ack",
    "legacy_type",
    "payloads_of",
    "renders",
]


def _frames(frame_or_frames: Union[Frame, Iterable[Frame]]) -> List[Frame]:
    if isinstance(frame_or_frames, dict):
        return [frame_or_frames]
    return list(frame_or_frames)


def effects_of(
    frame_or_frames: Union[Frame, Iterable[Frame]], kind: Optional[str] = None
) -> List[Dict[str, Any]]:
    """The effects of one frame or of several (in order), optionally of one ``kind``."""
    found: List[Dict[str, Any]] = []
    for frame in _frames(frame_or_frames):
        if not isinstance(frame, dict) or frame.get("type") != "render":
            continue
        for effect in frame.get("effects") or ():
            if kind is None or effect.get("kind") == kind:
                found.append(effect)
    return found


def payloads_of(frame_or_frames: Union[Frame, Iterable[Frame]], kind: str) -> List[Dict[str, Any]]:
    """The ``kind`` effects without their ``kind`` key: the 1.x side-channel entries.

    ``payloads_of(answer, "navigation")`` is what the HTTP answer's
    ``_navigation`` list held before #3658.
    """
    return [{k: v for k, v in e.items() if k != "kind"} for e in effects_of(frame_or_frames, kind)]


def renders(frames: Iterable[Frame], part: Any = ...) -> List[Frame]:
    """The ``render`` frames of ``frames``; with ``part``, those whose :func:`dom_part` is it."""
    out = [f for f in frames if isinstance(f, dict) and f.get("type") == "render"]
    if part is not ...:
        out = [f for f in out if dom_part(f) == part]
    return out


def is_ack(frame: Frame) -> bool:
    """A ``render`` frame with no DOM part (today's ``noop``)."""
    return frame.get("type") == "render" and dom_part(frame) is None


def legacy_type(frame: Frame) -> Any:
    """The 1.x frame type a ``render`` frame's DOM part stands for.

    ``"patch"``, ``"html_update"``, ``"embedded_update"`` or ``"noop"`` (no DOM
    part); any other frame's own ``type``.
    """
    if frame.get("type") != "render":
        return frame.get("type")
    return {
        "patches": "patch",
        "html": "html_update",
        "embedded": "embedded_update",
        None: "noop",
    }[dom_part(frame)]


def frame_matches(frame: Frame, types: Iterable[str]) -> bool:
    """Whether ``frame`` is one of ``types``, reading a ``render`` frame by its old names.

    A ``render`` frame matches the 1.x name of its DOM part (``"patch"``,
    ``"html_update"``, ``"embedded_update"``, ``"noop"``) and the ``kind`` of any
    effect it carries (``"navigate"``, ``"push_event"``...), as
    ``LivePage.receive`` does.
    """
    from djust._testing_live import _frame_is

    wanted = (types,) if isinstance(types, str) else tuple(types)
    return isinstance(frame, dict) and _frame_is(frame, wanted)
