"""Time-travel debugging: record state snapshots around event handlers.

Dev-only. Gated on ``DEBUG=True`` and per-view opt-in via the
``LiveView.time_travel_enabled`` class attribute. Every
``@event_handler`` dispatch records a ``state_before`` / ``state_after``
snapshot, and the developer can scrub back through history from the
debug panel to restore any past state.

The buffer is per-view-instance, bounded, and thread-safe. All
exceptions are logged and degrade to a no-op so time-travel can never
break the production event path (it's DEBUG-gated at the WebSocket
consumer anyway).

Async / background caveats
--------------------------

``state_after`` captures public view state *synchronously* at the
moment the handler returns control to the dispatcher. That means:

* Work scheduled via
  :meth:`~djust.live_view.LiveView.start_async` or wrapped in
  ``@background`` runs in a thread AFTER the handler returns — any
  state it mutates will appear in the NEXT event's snapshot (or not at
  all, if no further event fires).
* ``async def`` handlers are fully awaited before ``state_after`` is
  captured, so awaited coroutines are reflected correctly. Only
  *fire-and-forget* background work is deferred out of the snapshot.

Developers who need to time-travel past the result of background work
should mutate a public flag in the background callback and observe the
next event snapshot for the final state.
"""

from __future__ import annotations

import logging
import threading
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Any, Deque, Dict, List, Optional

logger = logging.getLogger("djust.time_travel")


@dataclass
class EventSnapshot:
    """Captured state around a single event dispatch.

    ``state_before`` is captured *before* the handler runs, ``state_after``
    after it returns (or raises). ``error`` is a short human-readable
    error string when the handler raised, truncated to 200 chars to keep
    the buffer small.
    """

    event_name: str
    params: Dict[str, Any]
    ref: Optional[int]
    ts: float
    state_before: Dict[str, Any]
    state_after: Dict[str, Any] = field(default_factory=dict)
    error: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        """JSON-safe dict view of the snapshot for wire transport."""
        return {
            "event_name": self.event_name,
            "params": self.params,
            "ref": self.ref,
            "ts": self.ts,
            "state_before": self.state_before,
            "state_after": self.state_after,
            "error": self.error,
        }


class TimeTravelBuffer:
    """Bounded ring buffer of :class:`EventSnapshot` entries.

    Thread-safe — the same LiveView instance can be touched from async
    event handlers (event loop thread) and from ``start_async()``
    background threads. Uses a module-level ``collections.deque`` with
    ``maxlen``; oldest entries evict silently once the cap is reached.
    """

    def __init__(self, max_events: int = 100):
        if not isinstance(max_events, int) or max_events <= 0:
            raise ValueError("max_events must be a positive int, got %r" % (max_events,))
        self._max = max_events
        self._buf: Deque[EventSnapshot] = deque(maxlen=max_events)
        self._lock = threading.Lock()

    @property
    def max_events(self) -> int:
        return self._max

    def append(self, snapshot: EventSnapshot) -> None:
        """Append a snapshot. Oldest entry is dropped when at capacity."""
        with self._lock:
            self._buf.append(snapshot)

    def jump(self, index: int) -> Optional[EventSnapshot]:
        """Return the snapshot at ``index`` or ``None`` if out of range.

        Index is positional within the current buffer (0 = oldest still
        retained), matching what ``history()`` returns.
        """
        with self._lock:
            if 0 <= index < len(self._buf):
                return self._buf[index]
            return None

    def history(self) -> List[Dict[str, Any]]:
        """Return a list of JSON-safe dicts, oldest first."""
        with self._lock:
            return [s.to_dict() for s in self._buf]

    def clear(self) -> None:
        with self._lock:
            self._buf.clear()

    def __len__(self) -> int:
        with self._lock:
            return len(self._buf)


def record_event_start(
    view: Any,
    event_name: str,
    params: Optional[Dict[str, Any]],
    ref: Optional[int],
) -> Optional[EventSnapshot]:
    """Capture ``state_before`` for an event dispatch.

    Returns an :class:`EventSnapshot` ready to be finalized via
    :func:`record_event_end`, or ``None`` when time-travel is disabled
    or the view is missing the expected surface. Never raises.
    """
    if not getattr(view, "time_travel_enabled", False):
        return None
    if not hasattr(view, "_capture_snapshot_state"):
        return None
    buffer = getattr(view, "_time_travel_buffer", None)
    if buffer is None:
        return None
    try:
        state_before = view._capture_snapshot_state()
    except Exception:  # noqa: BLE001 — dev-only, log + degrade
        logger.exception("time_travel: _capture_snapshot_state failed (before)")
        return None
    return EventSnapshot(
        event_name=event_name,
        params=dict(params) if params else {},
        ref=ref,
        ts=time.time(),
        state_before=state_before,
    )


#: Maximum chars retained on ``EventSnapshot.error``. Defense-in-depth:
#: callers already truncate, but record_event_end enforces the cap so a
#: misbehaving caller can't blow out the buffer footprint.
ERROR_MESSAGE_MAX_CHARS = 200


def record_event_end(
    view: Any,
    snapshot: Optional[EventSnapshot],
    error: Optional[str] = None,
) -> None:
    """Finalize a snapshot with ``state_after`` and append it to the buffer.

    No-op when ``snapshot`` is ``None`` (time-travel disabled) or the
    buffer is unavailable. Never raises.

    ``error`` is coerced to ``str`` and truncated to
    :data:`ERROR_MESSAGE_MAX_CHARS` characters — defense-in-depth, even
    though well-behaved callers are expected to truncate themselves.
    """
    if snapshot is None:
        return
    buffer = getattr(view, "_time_travel_buffer", None)
    if buffer is None:
        return
    try:
        state_after = view._capture_snapshot_state()
    except Exception:  # noqa: BLE001 — dev-only, log + degrade
        logger.exception("time_travel: _capture_snapshot_state failed (after)")
        state_after = {}
    snapshot.state_after = state_after
    if error is not None:
        try:
            error = str(error)[:ERROR_MESSAGE_MAX_CHARS]
        except Exception:  # noqa: BLE001 — dev-only, log + degrade
            logger.exception("time_travel: error coercion failed")
            error = "<unrepresentable error>"
    snapshot.error = error
    buffer.append(snapshot)


#: Reserved snapshot key for component-level state (#1041).
#: Lives at the top level of the state dict to keep components out of
#: the parent's flat-attr namespace.
_COMPONENTS_SNAPSHOT_KEY = "__components__"


def restore_snapshot(view: Any, snapshot: EventSnapshot, which: str = "before") -> bool:
    """Restore view public state from a snapshot.

    ``which`` must be ``"before"`` or ``"after"``. Uses
    :func:`djust.security.safe_setattr` so dunder / private keys are
    blocked even if someone has tampered with the buffer. Returns
    ``True`` on success, ``False`` when any key failed to restore.

    Restoration first removes any CURRENT public attributes that are
    absent from the snapshot ("ghost attrs"), then sets snapshot
    values. This ensures that restoring ``{a:1}`` over a live state
    of ``{a:5, b:10}`` leaves the view as ``{a:1}`` — not
    ``{a:1, b:10}``. Framework-internal names (``_FRAMEWORK_INTERNAL_ATTRS``)
    and any key starting with ``_`` are never deleted.

    Component-level restoration (#1041, v0.9.0): when the snapshot
    contains a top-level ``"__components__"`` key, each
    ``{component_id: state}`` entry is dispatched to the matching
    component in ``view._components``. Components missing from the
    snapshot keep their current state (no ghost-attr cleanup across
    component boundaries — components are first-class instances, not
    parent-scoped attrs).
    """
    if which not in ("before", "after"):
        raise ValueError("which must be 'before' or 'after', got %r" % (which,))
    from djust.security import safe_setattr

    state = snapshot.state_before if which == "before" else snapshot.state_after
    # Pull components out before computing parent-state keys so they
    # don't get mistaken for top-level ghost-attr candidates.
    components_state = state.get(_COMPONENTS_SNAPSHOT_KEY, {})
    state = {k: v for k, v in state.items() if k != _COMPONENTS_SNAPSHOT_KEY}
    state_keys = set(state.keys())

    # Framework-internal attrs must never be removed even if they're
    # public. Import lazily to avoid a circular import at module load.
    framework_internal: frozenset = frozenset()
    try:
        from djust.live_view import _FRAMEWORK_INTERNAL_ATTRS as _fia

        framework_internal = _fia
    except Exception:  # noqa: BLE001 — dev-only, degrade silently
        pass

    # Phase 1: delete ghost attrs (public, not framework-internal,
    # not in the target snapshot). Tolerates AttributeError from
    # class-level descriptors that don't have an instance-level slot.
    try:
        current_keys = [
            key
            for key in list(vars(view).keys())
            if not key.startswith("_") and key not in framework_internal and key not in state_keys
        ]
    except TypeError:
        # vars() fails on objects without __dict__; degrade silently.
        current_keys = []
    for stale_key in current_keys:
        try:
            delattr(view, stale_key)
        except AttributeError:
            # Already gone or class-level — nothing to do.
            pass
        except Exception:  # noqa: BLE001 — dev-only, log + continue
            logger.exception("time_travel: ghost-attr cleanup failed for key=%s", stale_key)

    # Phase 2: apply snapshot values through safe_setattr.
    ok = True
    for key, value in state.items():
        try:
            applied = safe_setattr(view, key, value, allow_private=False)
        except Exception:  # noqa: BLE001 — dev-only, log + degrade
            logger.exception("time_travel: restore failed for key=%s", key)
            ok = False
            continue
        if not applied:
            # Blocked by safe_setattr (dunder, read-only, etc.).
            logger.warning("time_travel: restore blocked for key=%s", key)
            ok = False

    # Phase 3 (#1041): restore per-component state. Each component
    # entry in ``__components__`` is dispatched to the matching
    # ``view._components[component_id]`` instance via safe_setattr.
    # Components absent from the snapshot keep current state — they
    # are first-class instances, not parent-scoped attrs, so the
    # ghost-attr cleanup model doesn't apply.
    #
    # NOTE: parent-from-component derivations (e.g. ``parent.total =
    # sum(comp.value for comp in components)``) are NOT recomputed
    # here. Time-travel restores literal captured state at each layer;
    # if the user wants live-recomputed invariants they should derive
    # them at render time, not at restore time.
    if components_state:
        registry = getattr(view, "_components", None) or {}
        for component_id, component_snap in components_state.items():
            component = registry.get(component_id)
            if component is None:
                logger.warning(
                    "time_travel: component %r in snapshot but not in registry",
                    component_id,
                )
                ok = False
                continue
            for key, value in component_snap.items():
                try:
                    applied = safe_setattr(component, key, value, allow_private=False)
                except Exception:  # noqa: BLE001 — dev-only, log + degrade
                    logger.exception(
                        "time_travel: component restore failed for id=%s key=%s",
                        component_id,
                        key,
                    )
                    ok = False
                    continue
                if not applied:
                    logger.warning(
                        "time_travel: component restore blocked for id=%s key=%s",
                        component_id,
                        key,
                    )
                    ok = False
    return ok


def replay_event(
    view: Any,
    snapshot: EventSnapshot,
    override_params: Optional[Dict[str, Any]] = None,
    record_replay: bool = True,
) -> Optional[EventSnapshot]:
    """Replay a recorded event from a snapshot's ``state_before``.

    Forward-replay (#1042, v0.9.0) — Redux DevTools parity. Restores
    the view to the snapshot's ``state_before``, then re-invokes the
    recorded ``event_name`` handler with either the original ``params``
    OR a caller-supplied ``override_params`` (branched timeline).
    Returns the new :class:`EventSnapshot` for the replayed event so
    callers can inspect the resulting state.

    The replay path produces a fresh snapshot in the time-travel
    buffer (when ``record_replay=True``, the default) so the branched
    timeline is itself scrubbable. Setting ``record_replay=False``
    runs a "dry" replay that mutates the view but doesn't append to
    the buffer — useful for the debug panel's "preview this branch"
    UI.

    The handler lookup uses ``getattr(view, snapshot.event_name)``;
    callers are responsible for ensuring the handler hasn't been
    renamed since the snapshot was captured. If the handler is
    missing, returns ``None`` and logs a warning.

    Restoration uses :func:`restore_snapshot` (with ``which="before"``)
    so component state from #1041 captures replays correctly.

    :param view: The LiveView instance to replay against.
    :param snapshot: The original :class:`EventSnapshot` providing the
        ``state_before`` baseline AND the ``event_name`` / ``params``
        to re-execute.
    :param override_params: When non-``None``, replaces
        ``snapshot.params`` for the replay invocation. Use to fork
        the timeline with different inputs.
    :param record_replay: When ``True`` (default), the replayed event
        is appended to the view's time-travel buffer as a NEW
        snapshot. When ``False``, the replay still mutates the view
        but the buffer is unchanged.
    :returns: The new :class:`EventSnapshot` capturing the replay's
        before/after state when ``record_replay=True``, or ``None``
        when the handler is missing, time-travel is disabled, OR
        ``record_replay=False`` (dry-replay path always returns None).
    """
    # Defense-in-depth: reject dunder / private event names. The
    # snapshot is normally produced by the framework's own dispatcher
    # which only records ``@event_handler``-decorated public methods,
    # but a hand-edited or malicious snapshot could specify
    # ``__init__`` and replay would re-invoke the constructor. The
    # bare-``getattr`` resolution would happily return it. Belt +
    # suspenders for the future ws-replay wiring.
    if snapshot.event_name.startswith("_"):
        logger.warning(
            "time_travel: replay_event refused dunder/private event_name %r",
            snapshot.event_name,
        )
        return None
    handler = getattr(view, snapshot.event_name, None)
    if handler is None or not callable(handler):
        logger.warning(
            "time_travel: replay_event handler %r not found on view %s",
            snapshot.event_name,
            type(view).__name__,
        )
        return None

    # Capture the handler reference and the live ``time_travel_enabled``
    # flag BEFORE ``restore_snapshot`` because the restore's
    # ghost-attr cleanup phase (Phase 1) deletes any public attrs
    # that aren't in the snapshot — including handler functions a
    # test may have monkey-patched onto the instance after the
    # snapshot was captured, AND including ``time_travel_enabled``
    # if the caller disabled it after the snapshot was taken (the
    # CURRENT user intent matters more than the snapshot's
    # historical state).
    live_tt_enabled = bool(getattr(view, "time_travel_enabled", False))

    # Restore the view to state_before so the handler runs from the
    # captured baseline. Component state restores via the #1041 path.
    restore_snapshot(view, snapshot, which="before")

    # Build the params to invoke the handler with — original by
    # default, override for branched timelines.
    params = override_params if override_params is not None else dict(snapshot.params)

    if record_replay and live_tt_enabled:
        # Capture a fresh snapshot pair around the replay so the
        # branched timeline is scrubbable itself.
        replay_snap = record_event_start(view, snapshot.event_name, params, ref=None)
        try:
            handler(**params) if params else handler()
        except Exception as exc:  # noqa: BLE001 — replay shouldn't break caller
            logger.exception("time_travel: replay handler %s raised", snapshot.event_name)
            record_event_end(view, replay_snap, error=str(exc))
            return replay_snap
        record_event_end(view, replay_snap)
        return replay_snap

    # Dry-replay path — mutate view but don't record. Caller wants
    # to preview a branch without polluting the buffer.
    try:
        handler(**params) if params else handler()
    except Exception:  # noqa: BLE001 — dry replay swallows for preview
        logger.exception("time_travel: dry replay handler %s raised", snapshot.event_name)
    return None


__all__ = [
    "EventSnapshot",
    "TimeTravelBuffer",
    "record_event_start",
    "record_event_end",
    "restore_snapshot",
    "replay_event",
]
