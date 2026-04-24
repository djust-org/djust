"""ActivityMixin — per-LiveView registry of ``{% dj_activity %}`` regions (v0.7.0).

React 19.2 ``<Activity>`` parity. Pre-renders hidden regions of a LiveView and
preserves their local state across show/hide cycles. The server is the
canonical source of truth for visibility; every render emits ``hidden``
(or not) via the wrapper element, and the client mirrors that state.

Responsibilities:

* Maintain ``self._djust_activities`` — a ``{name: {"visible": bool, "eager": bool}}``
  map populated on every render via :meth:`_register_activity` (invoked by the
  ``{% dj_activity %}`` template tag).
* Expose :meth:`set_activity_visible` / :meth:`is_activity_visible` as the
  server-side API for flipping activities from event handlers.
* Queue client-triggered events that fire inside a hidden (non-eager)
  activity via :meth:`_queue_deferred_activity_event`. The WebSocket
  consumer drains the queue via :meth:`_flush_deferred_activity_events`
  after any handler that flips an activity to ``visible=True``. The
  flush is an ``async`` method; it ``await``s each deferred event via
  ``consumer._dispatch_single_event`` so every drained event completes
  inside the same WebSocket round-trip as the handler that flipped the
  panel visible — not after the response has already been sent.
* Declarative opt-in for always-active activities via the class attribute
  ``eager_activities: frozenset[str]``.

All internal storage uses underscore-prefixed attribute names so
:func:`~djust.live_view.LiveView.get_state` auto-filters them from the
client-visible reactive-state payload without any bespoke rules.

Per-instance allocation (NOT class-level) in :meth:`_init_activity` prevents
cross-instance leaks: every LiveView gets its own fresh dict/queue pair.
"""

from __future__ import annotations

import collections
import logging
from typing import Any, Deque, Dict, FrozenSet, Tuple

logger = logging.getLogger(__name__)

# Per-activity deferred-event queue cap. Prevents a malicious or misbehaving
# client from spamming a hidden activity into unbounded memory growth; when
# exceeded, the oldest event is evicted (FIFO). Overridable per-subclass via
# the ``activity_event_queue_cap`` class attribute.
_DEFAULT_ACTIVITY_EVENT_QUEUE_CAP = 100


class ActivityMixin:
    """Per-LiveView registry of ``{% dj_activity %}`` regions.

    Composed into :class:`~djust.live_view.LiveView` via the MRO list in
    ``live_view.py`` (AFTER :class:`~djust.mixins.sticky.StickyChildRegistry`,
    BEFORE Django's ``View``). Consumers MUST call :meth:`_init_activity`
    from ``__init__`` so the per-instance dicts exist before any
    ``{% dj_activity %}`` tag tries to register into them.
    """

    # Declarative eager-activity names. Subclasses override with a
    # ``frozenset`` so list/set mutation can't accidentally leak across
    # instances. Members are activities that keep dispatching events
    # even while their wrapper carries ``hidden``.
    eager_activities: FrozenSet[str] = frozenset()

    # Per-class override for the FIFO queue cap (see module constant).
    activity_event_queue_cap: int = _DEFAULT_ACTIVITY_EVENT_QUEUE_CAP

    # Declared on the class for type checkers; populated by _init_activity.
    _djust_activities: Dict[str, Dict[str, bool]]
    _deferred_activity_events: Dict[str, Deque[Tuple[str, Dict[str, Any]]]]

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def _init_activity(self) -> None:
        """Allocate per-instance storage. Called from ``LiveView.__init__``.

        Per-instance allocation is load-bearing: if the attributes lived on
        the class they would be shared between every LiveView instance in
        the process and a handler on view A would see queued events for
        view B. Test ``test_activities_internal_state_excluded_from_get_state``
        locks in that these attrs never surface in client-visible state.
        """
        self._djust_activities = {}
        self._deferred_activity_events = {}

    # ------------------------------------------------------------------
    # Template-tag registration path
    # ------------------------------------------------------------------

    def _register_activity(self, name: str, visible: bool, eager: bool) -> None:
        """Record the declared state of an activity during template render.

        Invoked by ``DjActivityNode.render`` on every pass. ``name`` is the
        activity id; duplicate names inside one render pass overwrite
        earlier entries (the ``A071`` check flags this at dev time).
        """
        if not name:
            return
        # Declarative eager-activities win over the per-tag flag so a
        # subclass can mark an activity eager once and template authors
        # don't have to repeat ``eager=True`` at every render site.
        effective_eager = eager or (name in self.eager_activities)
        # Allocate on first touch — defensive against subclasses that
        # override __init__ without calling super()._init_activity().
        if not hasattr(self, "_djust_activities") or self._djust_activities is None:
            self._init_activity()
        self._djust_activities[name] = {
            "visible": bool(visible),
            "eager": bool(effective_eager),
        }

    # ------------------------------------------------------------------
    # Public API — called from user code in event handlers
    # ------------------------------------------------------------------

    def set_activity_visible(self, name: str, visible: bool) -> None:
        """Flip the server-side visibility state of an activity.

        The next render will emit (or drop) the ``hidden`` attribute on
        the wrapper ``<div>``; the client-side MutationObserver fires
        ``djust:activity-shown`` on un-hide. Any queued deferred events
        for this activity will be drained by the WS consumer after the
        current handler returns.
        """
        if not name:
            return
        if not hasattr(self, "_djust_activities") or self._djust_activities is None:
            self._init_activity()
        entry = self._djust_activities.setdefault(
            name,
            {"visible": True, "eager": name in self.eager_activities},
        )
        entry["visible"] = bool(visible)

    def is_activity_visible(self, name: str) -> bool:
        """Return the DECLARED visibility of an activity.

        Note: does NOT account for an outer activity being hidden. When
        activities are nested, an outer ``hidden`` wrapper visually hides
        every descendant regardless of their own declared state. The
        event-dispatch gate on the client uses ``closest([hidden])`` and
        correctly drops events from any hidden ancestor — but
        ``is_activity_visible`` is a cheap direct-state read and does not
        walk declarations.
        """
        activities = getattr(self, "_djust_activities", None) or {}
        entry = activities.get(name)
        if entry is None:
            # Unknown activity defaults to visible — safer than returning
            # False (which would suppress legitimate events against a
            # typo'd name instead of surfacing the bug at dev time).
            return True
        return bool(entry.get("visible", True))

    # ------------------------------------------------------------------
    # Deferred-event queue (called by WebSocket consumer)
    # ------------------------------------------------------------------

    def _is_activity_eager(self, name: str) -> bool:
        """Return True if ``name`` is marked eager (tag arg or class attr)."""
        if name in self.eager_activities:
            return True
        activities = getattr(self, "_djust_activities", None) or {}
        entry = activities.get(name)
        return bool(entry and entry.get("eager", False))

    def _queue_deferred_activity_event(
        self, activity_name: str, event_name: str, params: Dict[str, Any]
    ) -> None:
        """Push ``(event_name, params)`` onto the deferred queue for ``activity_name``.

        FIFO eviction once the queue reaches
        :attr:`activity_event_queue_cap`. The cap guards against a
        misbehaving client flooding a hidden panel with synthetic events
        and ballooning consumer memory.

        Security contract: events are queued WITHOUT permission/rate-limit
        validation — validation runs when the event is dispatched
        (``_dispatch_single_event``), so a denied event in the queue never
        reaches its handler. The ``activity_event_queue_cap`` FIFO bound
        (default 100) limits queue memory; each dispatched event is still
        subject to the full auth stack (``_validate_event_security``,
        ``@permission_required``, rate limiter, CSRF). Queue insertion
        itself requires that the triggering WebSocket frame already passed
        CSRF + connection-level authentication in the consumer.
        """
        if not activity_name or not event_name:
            return
        if not hasattr(self, "_deferred_activity_events") or self._deferred_activity_events is None:
            self._init_activity()
        cap = int(getattr(self, "activity_event_queue_cap", _DEFAULT_ACTIVITY_EVENT_QUEUE_CAP))
        if cap <= 0:
            return  # Opt-out: zero-cap disables deferral entirely.
        queue = self._deferred_activity_events.get(activity_name)
        if queue is None:
            queue = collections.deque(maxlen=cap)
            self._deferred_activity_events[activity_name] = queue
        elif queue.maxlen != cap:
            # Subclass may have re-bound activity_event_queue_cap at
            # runtime; rebuild the deque with the new cap while
            # preserving existing entries (trimmed to the new maxlen).
            new_queue: Deque[Tuple[str, Dict[str, Any]]] = collections.deque(queue, maxlen=cap)
            self._deferred_activity_events[activity_name] = new_queue
            queue = new_queue
        # Copy params so later mutation in the caller's scope doesn't
        # corrupt the queued payload. Shallow copy is sufficient — values
        # are already JSON-coerced from the WebSocket frame.
        queue.append((event_name, dict(params) if params else {}))

    async def _flush_deferred_activity_events(self, consumer: Any) -> None:
        """Drain all queues whose activity is now both visible and non-eager-skipped.

        Called by :class:`~djust.websocket.LiveViewConsumer` after every
        successful ``handle_event`` / ``handle_info`` / ``db_notify``
        dispatch. The flush is ``async`` and ``await``s each dispatched
        event inline so every drained event finishes within the SAME
        WebSocket round-trip as the handler that flipped the panel
        visible — no fire-and-forget ``create_task`` anywhere.

        Implementation notes:

        * MUST NOT call back into :py:meth:`LiveViewConsumer.handle_event`.
          That method acquires ``self._render_lock``, which is already
          held by the caller. ``asyncio.Lock`` is non-reentrant, so a
          callback would deadlock. Instead the flush uses the consumer's
          lighter-weight ``_dispatch_single_event`` helper which assumes
          the caller already owns the lock.
        * The drain pass takes a snapshot of pending activity names up
          front so that a handler firing during the drain doesn't mutate
          the dict we're iterating over (RuntimeError guard).
        * Each drained event strips its ``_activity`` param before
          dispatch so the server-side activity gate does not re-queue it
          (we already decided to deliver these).
        * Exceptions raised by a single drained handler are logged and
          swallowed so one bad event can't break the remaining flush.
        """
        queues = getattr(self, "_deferred_activity_events", None)
        if not queues:
            return
        dispatch = getattr(consumer, "_dispatch_single_event", None)
        if dispatch is None:
            # Consumer did not expose the minimal-dispatch helper —
            # bail rather than silently losing events.
            logger.debug(
                "dj_activity: consumer has no _dispatch_single_event; leaving queue intact"
            )
            return
        # Snapshot names up front so handler-induced mutations are safe.
        for activity_name in list(queues.keys()):
            # Skip still-hidden, non-eager activities: their events stay
            # queued until the next flip.
            if not self.is_activity_visible(activity_name) and not self._is_activity_eager(
                activity_name
            ):
                continue
            queue = queues.get(activity_name)
            if not queue:
                continue
            # Pop-all + dispatch. We pop (rather than iterate) so a
            # handler that flips the activity back to hidden mid-drain
            # doesn't re-deliver events we've already dispatched.
            while queue:
                event_name, params = queue.popleft()
                # Strip the _activity marker so the server-side gate
                # does not re-queue this event. (If we left it in and
                # the handler itself flipped the activity back to
                # hidden, the gate would re-intercept on some future
                # replay — which we're not in here, but defence in
                # depth keeps the contract explicit.)
                if params and "_activity" in params:
                    params = {k: v for k, v in params.items() if k != "_activity"}
                try:
                    await dispatch(self, event_name, params or {})
                except Exception:  # noqa: BLE001 — never let a drained event kill the consumer
                    logger.exception(
                        "dj_activity: deferred event %r on %s raised during flush",
                        event_name,
                        activity_name,
                    )
            # Drop the now-empty queue so it doesn't linger.
            if not queue:
                queues.pop(activity_name, None)
