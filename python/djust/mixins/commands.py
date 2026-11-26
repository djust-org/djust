"""CommandsMixin — the one outbound queue of a LiveView (#3658, TC9).

Everything a handler asks the client to do besides re-rendering is queued
here, in one list, as an *effect*: a ``push_event()``, a ``put_flash()`` /
``clear_flash()``, a ``page_title`` / ``page_meta`` change, a ``set_layout()``
and a ``live_patch()`` / ``live_redirect()``. The public methods stay on their
own mixins (``PushEventMixin``, ``FlashMixin``, ``PageMetadataMixin``,
``LayoutMixin``, ``NavigationMixin``); they all write through
:meth:`CommandsMixin._enqueue`.

A turn drains the queue once (:meth:`CommandsMixin._drain_outbound`). The drain
is sorted stably by :data:`EFFECT_RANKS`, so the client always applies the
effects in the canonical order (push events, flash, page metadata, layout,
navigation) and, within one kind, in the order they were queued.

Each queued payload is the effect as the client receives it, without its
``kind``:

=================  ===========================================================
kind               payload
=================  ===========================================================
``push_event``     ``{"event": name, "payload": {...}}``
``flash``          ``{"action": "put", "level": ..., "message": ...}`` or
                   ``{"action": "clear"[, "level": ...]}``
``page_metadata``  ``{"action": "title", "value": ...}`` or
                   ``{"action": "meta", "name": ..., "content": ...}``
``layout``         ``{"path": template_path}`` (the HTML is rendered when the
                   frame is built)
``navigation``     ``{"action": "live_patch" | "live_redirect", "replace": ...,
                   ["params": ...], ["path": ...]}``
=================  ===========================================================
"""

from __future__ import annotations

from typing import Any, Dict, Iterable, List, Optional, Tuple

#: The order a drain sorts the effects in; within one kind the queue order is kept.
#: ``navigate`` (a full-page navigation, the auth/refusal redirect) is never queued
#: on a view, but shares the rank of ``navigation`` where a frame carries both.
EFFECT_RANKS: Dict[str, int] = {
    "push_event": 0,
    "flash": 1,
    "page_metadata": 2,
    "layout": 3,
    "navigation": 4,
    "navigate": 4,
}

#: The kinds a view may queue.
QUEUED_KINDS = frozenset({"push_event", "flash", "page_metadata", "layout", "navigation"})

#: Kinds of which only the last queued one is kept.
_LAST_WINS = frozenset({"layout"})

Entry = Tuple[int, str, Dict[str, Any]]


class CommandsMixin:
    """The one outbound effect queue of a view.

    ``self._outbound`` holds ``(seq, kind, payload)`` entries in the order they
    were queued. Nothing outside the framework reads it; views use the public
    methods of the effect mixins.
    """

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self._outbound: List[Entry] = []

    def _outbound_queue(self) -> List[Entry]:
        # A view whose __init__ did not run (a bare ``object.__new__`` in a
        # test, or a subclass that skipped ``super().__init__``) still queues.
        try:
            return self.__dict__["_outbound"]
        except KeyError:
            queue: List[Entry] = []
            self.__dict__["_outbound"] = queue
            return queue

    def _enqueue(self, kind: str, payload: Dict[str, Any]) -> None:
        """Queue one effect of ``kind`` (see :data:`QUEUED_KINDS`)."""
        if kind not in QUEUED_KINDS:
            raise ValueError("unknown effect kind %r" % (kind,))
        queue = self._outbound_queue()
        if kind in _LAST_WINS:
            queue[:] = [entry for entry in queue if entry[1] != kind]
        # The sequence only orders entries of one queue: one more than the
        # newest entry (a drain that empties the queue may restart it).
        seq = queue[-1][0] + 1 if queue else 0
        queue.append((seq, kind, payload))

    def _pending(self, kind: str) -> List[Dict[str, Any]]:
        """The queued payloads of ``kind``, oldest first (the queue is unchanged)."""
        return [payload for _seq, k, payload in self._outbound_queue() if k == kind]

    def _has_pending(self, kind: Optional[str] = None) -> bool:
        """Whether an effect of ``kind`` (any kind when ``None``) is queued."""
        queue = self._outbound_queue()
        if kind is None:
            return bool(queue)
        return any(k == kind for _seq, k, _payload in queue)

    def _replace_pending(self, kind: str, payloads: Iterable[Dict[str, Any]]) -> None:
        """Drop the queued effects of ``kind`` and queue ``payloads`` in their place."""
        self._drain_outbound((kind,))
        for payload in payloads:
            self._enqueue(kind, payload)

    def _drain_outbound(
        self, kinds: Optional[Iterable[str]] = None
    ) -> List[Tuple[str, Dict[str, Any]]]:
        """Remove and return the queued effects as ``(kind, payload)`` pairs.

        Sorted stably by :data:`EFFECT_RANKS`. ``kinds`` limits the drain to
        those kinds; the others stay queued in their order.
        """
        queue = self._outbound_queue()
        if not queue:
            return []
        if kinds is None:
            taken = list(queue)
            queue.clear()
        else:
            wanted = frozenset(kinds)
            taken = [entry for entry in queue if entry[1] in wanted]
            if not taken:
                return []
            queue[:] = [entry for entry in queue if entry[1] not in wanted]
        taken.sort(key=lambda entry: (EFFECT_RANKS.get(entry[1], len(EFFECT_RANKS)), entry[0]))
        return [(kind, payload) for _seq, kind, payload in taken]
