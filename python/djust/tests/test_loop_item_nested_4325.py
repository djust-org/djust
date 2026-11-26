"""Mutate-before-and-after tests for nested loops in the per-item store (#4325).

``RustLiveView`` keeps each row of a ``{% for %}`` whose body holds a nested
loop (``djust._per_turn`` ``loop_item_nested``) and serves it again when the
row hashes the same. A row's inner list is part of its value, so a change to
it must re-render the row whether it is made in place by the handler (before
the post-event snapshot) or by ``get_context_data`` (after it). Each test checks that the frame carries the new
text, and that the same turn with a store that ignores inner lists
(sabotage 3) does not, so the test can see the failure it guards against.
"""

from __future__ import annotations

import json
from typing import Any, Dict, List

import pytest

from djust import LiveView, event_handler
from djust.decorators import state
from djust.tests import _per_turn_harness as harness

pytestmark = [pytest.mark.asyncio, pytest.mark.django_db(transaction=True)]

MODULE = __name__


def _rows(n: int) -> List[Dict[str, Any]]:
    return [{"id": i, "name": f"row {i}", "tags": ["alpha", f"t{i}"]} for i in range(n)]


class Nested(LiveView):
    exposure_policy = "explicit"
    template = (
        "<div dj-root><h1>{{ title }}</h1><ul>{% for row in items %}"
        '<li id="r{{ row.id }}">{{ row.name }}:{% for t in row.tags %}'
        "{% if forloop.first %}<b>{{ t }}</b>{% else %}<i>{{ t }}</i>{% endif %}"
        "{% endfor %}</li>{% endfor %}</ul></div>"
    )
    title = state("a")
    items = state(default_factory=list)
    late = state(False, render=False)

    def mount(self, request, **kwargs):
        self.items = _rows(20)

    def get_context_data(self, **kwargs):
        if self.late:
            # After the post-event snapshot: application code in the window.
            self.items[4]["tags"][1] = "late-" + self.title
        return super().get_context_data(**kwargs)

    @event_handler()
    def retag(self, **kwargs):
        # In place, same length: only the row's value tells.
        self.items[7]["tags"][1] = "handler"

    @event_handler()
    def arm_late(self, **kwargs):
        self.late = True
        self.title = "b"

    @event_handler()
    def nothing(self, **kwargs):
        pass


def _sabotage(patch: Any) -> None:
    from djust.mixins import rust_bridge

    real = rust_bridge.RustBridgeMixin._sync_state_to_rust

    def sync(self: Any, *args: Any, **kwargs: Any) -> Any:
        rust_view = getattr(self, "_rust_view", None)
        if rust_view is not None and hasattr(rust_view, "_set_loop_item_store_sabotage"):
            rust_view._set_loop_item_store_sabotage(3)
        return real(self, *args, **kwargs)

    patch.setattr(rust_bridge.RustBridgeMixin, "_sync_state_to_rust", sync)


async def _frames(monkeypatch: Any, steps: List[str], sabotage: bool) -> List[str]:
    """Run the events *steps* and return each answer frame as text, with the
    store's hit count for that turn appended."""
    with monkeypatch.context() as patch:
        counts = harness.counting(patch)
        if sabotage:
            _sabotage(patch)
        key = await harness.new_session_key_async()
        out: List[str] = []
        with harness.ws_settings(MODULE):
            comm, _mount = await harness.mount_view(Nested, key)
            try:
                await harness.event(comm, "nothing")  # the first event after mount
                for step in steps:
                    counts.reset()
                    out.append(json.dumps(await harness.event(comm, step)))
                    out[-1] += " hits=%d" % counts.snapshot().get("loop_item_store.hits", 0)
            finally:
                await comm.disconnect()
    return out


async def test_an_inner_list_changed_by_the_handler_reaches_the_frame(monkeypatch):
    honest = await _frames(monkeypatch, ["retag"], sabotage=False)
    assert "handler" in honest[0]
    # Every other row came from the store.
    assert honest[0].endswith(" hits=19"), honest[0][-20:]
    broken = await _frames(monkeypatch, ["retag"], sabotage=True)
    assert "handler" not in broken[0]


async def test_an_inner_list_changed_after_the_snapshot_reaches_the_frame(monkeypatch):
    honest = await _frames(monkeypatch, ["arm_late", "retag"], sabotage=False)
    assert "late-b" in honest[0]
    assert "handler" in honest[1]
    broken = await _frames(monkeypatch, ["arm_late"], sabotage=True)
    assert "late-b" not in broken[0]
