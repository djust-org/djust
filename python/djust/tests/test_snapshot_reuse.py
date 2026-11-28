"""An event turn takes the previous event turn's post snapshot as its pre snapshot.

``_snapshot_reuse``: over a real socket, the second of two events with nothing
between them walks the state once, not twice; a push, a render outside a turn,
an attribute reassigned by the render, or a ``get_context_data`` override makes
the next event walk again; and the frames are the same either way (the
``snapshot_reuse`` case of ``test_per_turn_oracle.py`` compares every frame).
"""

from __future__ import annotations

from typing import Any, List

import pytest
from asgiref.sync import sync_to_async

from djust import LiveView, state
from djust import _snapshot_reuse
from djust.decorators import event_handler

from . import _per_turn_harness as harness

MODULE = __name__

pytestmark = [pytest.mark.asyncio, pytest.mark.django_db(transaction=True)]


class _Base(LiveView):
    exposure_policy = "explicit"

    def _get_context_processors(self):
        return []


class Rows(_Base):
    exposure_policy = "explicit"
    template = (
        "<div dj-root><h1>{{ title }}</h1>{% for r in rows %}<p>{{ r.n }}</p>{% endfor %}</div>"
    )
    title = state("a")
    rows = state(default_factory=list)

    def mount(self, request, **kwargs):
        self.rows = [{"n": i} for i in range(50)]

    @event_handler()
    def touch(self, row: int = 0, **kwargs):
        self.rows[row]["n"] += 1

    @event_handler()
    def retitle(self, value: str = "", **kwargs):
        self.title = value

    @event_handler()
    def nothing(self, **kwargs):
        pass


class Overriding(Rows):
    exposure_policy = "explicit"

    def get_context_data(self, **kwargs):
        return super().get_context_data(**kwargs)


@pytest.fixture
def walks(monkeypatch) -> List[Any]:
    """Every ``_snapshot_assigns`` call: ``None`` for a full walk, else the names walked."""
    from djust import websocket

    calls: List[Any] = []
    real = websocket._snapshot_assigns

    def counted(view: Any, only: Any = None) -> Any:
        calls.append(None if only is None else set(only))
        return real(view, only=only)

    monkeypatch.setattr(websocket, "_snapshot_assigns", counted)
    return calls


async def _open(view_class: type):
    key = await harness.new_session_key_async()
    recorded: List[Any] = []
    from djust.websocket import LiveViewConsumer

    real_init = LiveViewConsumer.__init__

    def init(self: Any, *args: Any, **kwargs: Any) -> None:
        real_init(self, *args, **kwargs)
        recorded.append(self)

    LiveViewConsumer.__init__ = init  # type: ignore[method-assign]
    try:
        comm, _frame = await harness.mount_view(view_class, key)
    finally:
        LiveViewConsumer.__init__ = real_init  # type: ignore[method-assign]
    return comm, recorded[-1].view_instance


async def _walks_for(walks: List[Any], comm: Any, name: str, params: Any = None):
    """Full walks during one event, and the frame that answered it."""
    before = len(walks)
    frame = await harness.event(comm, name, params)
    return sum(1 for call in walks[before:] if call is None), frame


async def test_back_to_back_events_walk_once(walks):
    with harness.ws_settings(MODULE):
        comm, view = await _open(Rows)
        try:
            first, _ = await _walks_for(walks, comm, "touch", {"row": 1})
            assert first == 2  # nothing kept yet: pre and post
            second, frame = await _walks_for(walks, comm, "touch", {"row": 2})
            assert second == 1, "the kept post snapshot is the next pre snapshot"
            assert frame.get("patches")
            # A handler that changes nothing is still a noop with the kept snapshot.
            third, frame = await _walks_for(walks, comm, "nothing")
            assert third == 1 and frame.get("type") in ("noop", "render")
            assert not frame.get("patches")
            assert _snapshot_reuse.KEPT_ATTR in view.__dict__
        finally:
            await comm.disconnect()


async def test_a_render_between_events_walks_again_and_renders(walks):
    """A render outside the event turns moves the baseline: the kept snapshot
    no longer describes what the client shows."""
    with harness.ws_settings(MODULE):
        comm, view = await _open(Rows)
        try:
            await harness.event(comm, "retitle", {"value": "x"})

            def elsewhere() -> None:
                view.title = "y"
                view._sync_state_to_rust()
                view.render_with_diff()

            await sync_to_async(elsewhere)()
            count, frame = await _walks_for(walks, comm, "retitle", {"value": "x"})
            assert count == 2
            assert frame.get("patches"), "x after y must render"
        finally:
            await comm.disconnect()


async def test_another_turn_on_the_view_drops_the_kept_snapshot(walks):
    with harness.ws_settings(MODULE):
        comm, view = await _open(Rows)
        try:
            await harness.event(comm, "touch", {"row": 1})
            _snapshot_reuse.bump_turn(view)  # as a push / tick / url turn does
            count, _ = await _walks_for(walks, comm, "touch", {"row": 1})
            assert count == 2
        finally:
            await comm.disconnect()


async def test_an_attribute_reassigned_after_the_snapshot_is_walked_again_alone(walks):
    """Only the replaced attribute is walked again: its kept entry named the
    old object, and the rest of the kept snapshot still holds."""
    with harness.ws_settings(MODULE):
        comm, view = await _open(Rows)
        try:
            await harness.event(comm, "touch", {"row": 1})
            view.rows = list(view.rows)  # same rows, another object
            before = len(walks)
            count, frame = await _walks_for(walks, comm, "touch", {"row": 1})
            assert count == 1
            partial = [call for call in walks[before:] if call is not None]
            assert partial and "_state_rows" in partial[0]  # the storage name
            assert frame.get("patches")
        finally:
            await comm.disconnect()


async def test_a_view_whose_render_runs_application_code_keeps_nothing(walks):
    with harness.ws_settings(MODULE):
        comm, view = await _open(Overriding)
        try:
            await harness.event(comm, "touch", {"row": 1})
            count, _ = await _walks_for(walks, comm, "touch", {"row": 2})
            assert count == 2
            assert _snapshot_reuse.KEPT_ATTR not in view.__dict__
        finally:
            await comm.disconnect()
