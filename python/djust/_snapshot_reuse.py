"""The last event turn's post-handler snapshot is the next one's pre-handler snapshot.

An event turn walks the view's state twice for change detection: once before
the handler (``pre``) and once after (``post``, ``websocket._snapshot_assigns``).
When nothing has run on the view since the previous event turn took its
``post``, the state the next handler starts from is the state that ``post``
describes, so it is taken as the next ``pre`` instead of walking again (one
digest walk of the rows, about 0.18 ms for 1,000).

"Nothing has run" is proven, not assumed. The kept snapshot is used only when
all of these still hold at the next event:

- no other turn ran on the view (every turn bumps :data:`TURN_SEQ_ATTR` in
  ``turns.run_turn``; the event turn itself is the one bump allowed);
- the Rust diff baseline is the one the previous turn left
  (``RustLiveView.baseline_epoch``, a new process-unique value on every render,
  commit or reset): a render outside a turn also invalidates it;
- an attribute whose object was replaced since (framework bookkeeping a
  render reassigns, such as the last page size) or that was added since is
  walked again, alone; the entry of an attribute that is still the very
  object it was is kept. The kept entry holds those objects, so an ``id()``
  in the snapshot cannot be reused by a new object;
- the view has no owned child views and no materialized bound components,
  whose state can move in turns of their own;
- no application code runs between the snapshot and the end of the turn: the
  render's context hooks are the framework's own (the same test the Rust sync
  applies before it reuses the snapshot's walks, #3999), and no turn
  middleware is configured.

Anything else, and the pre snapshot is walked as before. A wrong reuse could
only err towards rendering: the kept snapshot is the state as the previous turn
ended, and the checks above rule out every way it could differ from the state
the next handler starts from.
"""

from __future__ import annotations

import copy
from typing import Any, Callable, Dict, NamedTuple, Optional

from . import _per_turn

#: The view attribute holding the kept snapshot (:class:`_Kept`).
KEPT_ATTR = "_djust_kept_snapshot"
#: The view attribute ``turns.run_turn`` bumps for every turn on the view.
TURN_SEQ_ATTR = "_djust_turn_seq"

_SWITCH = "snapshot_reuse"


class _Kept(NamedTuple):
    snapshot: Any
    #: The objects the snapshot covered, by attribute: pinned, and compared by identity.
    objects: Dict[str, Any]
    turn_seq: int
    epoch: Optional[int]


def bump_turn(view: Any) -> None:
    """A turn is running on ``view`` (``turns.run_turn``)."""
    state = getattr(view, "__dict__", None)
    if state is not None:
        state[TURN_SEQ_ATTR] = state.get(TURN_SEQ_ATTR, 0) + 1


def _epoch(view: Any) -> Optional[int]:
    rust = view.__dict__.get("_rust_view")
    read = getattr(rust, "baseline_epoch", None)
    return read() if read is not None else None


def _eligible(view: Any) -> bool:
    if not _per_turn.on(_SWITCH):
        return False
    if getattr(view, "_child_views", None) or getattr(view, "_component_bindings", None):
        return False
    from .mixins.rust_bridge import render_runs_framework_code_only
    from .turns import app_chain_configured

    # The kept snapshot is taken before the render: application code in the
    # render (a ``get_context_data`` override, a ``@computed``) or in turn
    # middleware after it could change the state in place, unseen.
    return render_runs_framework_code_only(type(view)) and not app_chain_configured()


def covered_objects(view: Any, snapshot: Any) -> Dict[str, Any]:
    """The objects ``snapshot`` was taken from, by attribute (taken with it)."""
    state = view.__dict__
    return {name: state[name] for name in snapshot if name in state}


def keep(view: Any, snapshot: Any, objects: Dict[str, Any]) -> None:
    """Keep this turn's post snapshot for the next event turn. Called once the
    turn's render (or its no-render answer) is done."""
    state = view.__dict__
    if not _eligible(view):
        state.pop(KEPT_ATTR, None)
        return
    state[KEPT_ATTR] = _Kept(snapshot, objects, state.get(TURN_SEQ_ATTR, 0), _epoch(view))


def drop(view: Any) -> None:
    """Forget the kept snapshot (the turn ended in a way that does not keep one)."""
    getattr(view, "__dict__", {}).pop(KEPT_ATTR, None)


def pre_snapshot(view: Any, take: Callable[..., Any]) -> Any:
    """The pre-handler snapshot: the kept one when it still holds, else ``take(view)``.

    ``take`` is ``websocket._snapshot_assigns``. Attributes whose object was
    replaced since the kept snapshot (framework bookkeeping a render or a turn
    reassigns, such as the last page size), and attributes added since, are
    walked again with ``take(view, only=...)``; the rest is the kept snapshot."""
    state = view.__dict__
    kept: Optional[_Kept] = state.pop(KEPT_ATTR, None)
    if kept is None or not _eligible(view) or not _still_holds(view, kept):
        return take(view)
    objects = kept.objects
    missing = object()
    stale = {name for name, obj in objects.items() if state.get(name, missing) is not obj}
    stale.update(name for name in state if name not in objects)
    if not stale:
        return kept.snapshot
    fresh = take(view, only=stale)
    merged = copy.copy(kept.snapshot)
    merged.fingerprints = dict(kept.snapshot.fingerprints)
    for name in stale:
        merged.pop(name, None)
    merged.pop("_streams", None)  # always taken by ``take``, from the view
    merged.update(fresh)
    merged.fingerprints.update(fresh.fingerprints)
    return merged


def _still_holds(view: Any, kept: _Kept) -> bool:
    """No other turn and no render since the snapshot was kept."""
    state = view.__dict__
    if state.get(TURN_SEQ_ATTR, 0) != kept.turn_seq + 1:
        return False
    return kept.epoch is not None and _epoch(view) == kept.epoch
