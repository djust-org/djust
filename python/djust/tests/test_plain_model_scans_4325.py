"""A list the snapshot proved plain data is not scanned for a model again (#4325).

The sync used to walk every context list per turn for a ``Model`` or
``QuerySet`` (``_normalize_db_values``, ``_holds_db_value``, the explicit JIT
checks). A post-event snapshot entry that holds this very object and calls it
plain proves there is none, as ``plain_sub_ids`` already relies on. These tests
change the list on each side of the snapshot (CLAUDE.md, "Mutation-after-capture
test discipline"): a handler inserts a Model before the snapshot (seen), a
``get_context_data`` override inserts one after it (the reuse is refused and the
list is scanned). Each states the frames, with the switch on and off, and the
scan counts.
"""

from __future__ import annotations

from typing import Any, Dict, List

import pytest
from django.contrib.auth.models import Group, User

from djust import LiveView, event_handler
from djust.decorators import state
from djust.mixins import rust_bridge
from djust.tests import _per_turn_harness as harness

MODULE = __name__
ws = [pytest.mark.asyncio, pytest.mark.django_db(transaction=True)]

TEMPLATE = (
    "<div dj-root><ul>{% for r in rows %}<li>{{ r.name }}|{{ r.u.username }}|"
    "{% for g in r.groups.all %}{{ g.name }}{% endfor %}</li>{% endfor %}</ul>"
    "<i>{{ n }}</i></div>"
)


def _row(i: int) -> Dict[str, Any]:
    return {"name": "row%d" % i}


class Scanned(LiveView):
    exposure_policy = "explicit"
    template = TEMPLATE
    n = state(0)
    rows = state(default_factory=list)

    def _get_context_processors(self):
        return []

    def mount(self, request, **kwargs):
        self.rows = [_row(i) for i in range(20)]

    @event_handler()
    def bump(self, **kwargs):
        self.n += 1

    @event_handler()
    def edit(self, **kwargs):
        self.rows[3]["name"] += "!"
        self.n += 1

    @event_handler()
    def add_model(self, **kwargs):
        user = User.objects.get(username="scan-a")
        user.groups.add(Group.objects.get_or_create(name="scan-g")[0])
        self.rows.append(user)

    @event_handler()
    def add_nested(self, **kwargs):
        self.rows.append({"name": "nested", "u": User.objects.get(username="scan-b")})


class LateModel(Scanned):
    """The Model enters in ``get_context_data``, after the snapshot's walk."""

    def get_context_data(self, **kwargs):
        if self.n and not any(isinstance(r, User) for r in self.rows):
            user = User.objects.get(username="scan-a")
            user.groups.add(Group.objects.get_or_create(name="scan-g")[0])
            self.rows.append(user)
        return super().get_context_data(**kwargs)


def _users() -> None:
    for name in ("scan-a", "scan-b"):
        User.objects.get_or_create(username=name)


class Scans:
    """How many times a whole-list scan ran over the ``rows`` list."""

    def __init__(self) -> None:
        self.normalize = 0
        self.holds = 0


def _spy(monkeypatch) -> Scans:
    scans = Scans()
    real_normalize, real_holds = rust_bridge._normalize_db_values, rust_bridge._holds_db_value

    def is_rows(value: Any) -> bool:
        return type(value) is list and len(value) >= 20

    def normalize(value, *args, **kwargs):
        scans.normalize += is_rows(value)
        return real_normalize(value, *args, **kwargs)

    def holds(value, *args, **kwargs):
        scans.holds += is_rows(value)
        return real_holds(value, *args, **kwargs)

    monkeypatch.setattr(rust_bridge, "_normalize_db_values", normalize)
    monkeypatch.setattr(rust_bridge, "_holds_db_value", holds)
    return scans


async def _run(monkeypatch, view_class: type, events: List[str], *off: str):
    """``(frames, scans per event)`` of *view_class* over a real socket."""
    from asgiref.sync import sync_to_async

    await sync_to_async(_users)()
    key = await harness.new_session_key_async()
    with harness.shortcuts(monkeypatch, *off), harness.ws_settings(MODULE):
        scans = _spy(monkeypatch)
        comm, _mount = await harness.mount_view(view_class, key)
        frames, per_event = [], []
        try:
            for name in events:
                before = (scans.normalize, scans.holds)
                frames.append(await harness.event(comm, name))
                per_event.append((scans.normalize - before[0], scans.holds - before[1]))
        finally:
            await comm.disconnect()
    return frames, per_event


def _texts(frame: Dict[str, Any]) -> List[str]:
    out: List[str] = []

    def walk(node: Any) -> None:
        if isinstance(node, dict):
            if node.get("text") is not None:
                out.append(node["text"])
            for v in node.values():
                walk(v)
        elif isinstance(node, list):
            for v in node:
                walk(v)

    walk(frame.get("patches", []))
    return out


class TestPlainListIsNotScanned:
    pytestmark = ws

    async def test_a_cell_edit_scans_nothing(self, monkeypatch):
        frames, per_event = await _run(monkeypatch, Scanned, ["edit", "bump", "edit"])
        assert any("row3!" in t for t in _texts(frames[0])), frames[0]
        assert per_event == [(0, 0)] * 3, per_event

    async def test_gate_off_scans_again(self, monkeypatch):
        frames_on, _ = await _run(monkeypatch, Scanned, ["edit"])
        frames_off, per_event = await _run(monkeypatch, Scanned, ["edit"], "plain_model_scans")
        assert per_event[0][0] >= 1, per_event
        assert frames_on == frames_off


class TestModelInsertedBeforeTheSnapshot:
    pytestmark = ws

    async def test_a_model_the_handler_appends_is_seen(self, monkeypatch):
        events = ["bump", "add_model", "bump"]
        now, scans = await _run(monkeypatch, Scanned, events)
        off, _ = await _run(monkeypatch, Scanned, events, "plain_model_scans")
        assert now == off
        assert any("scan-g" in t for t in _texts(now[1])), now[1]
        assert scans[0] == (0, 0)
        assert scans[1][0] >= 1, "the list holding a Model was scanned"

    async def test_a_model_inside_a_nested_dict_row_is_seen(self, monkeypatch):
        events = ["bump", "add_nested", "bump"]
        now, scans = await _run(monkeypatch, Scanned, events)
        off, _ = await _run(monkeypatch, Scanned, events, "plain_model_scans")
        assert now == off
        assert any("scan-b" in t for t in _texts(now[1])), now[1]
        assert scans[1][1] >= 1, "the nested Model made the list be searched"


class TestModelInsertedAfterTheSnapshot:
    pytestmark = ws

    async def test_a_model_added_by_get_context_data_is_seen(self, monkeypatch):
        """The override is application code in the window: the snapshot's proof
        is not used, and the list is scanned with the Model in it."""
        events = ["bump", "bump"]
        now, scans = await _run(monkeypatch, LateModel, events)
        off, _ = await _run(monkeypatch, LateModel, events, "plain_model_scans")
        assert now == off
        assert any("scan-g" in t for t in _texts(now[0])), now[0]
        assert scans[0][0] >= 1

    async def test_the_proof_would_miss_it_if_the_window_check_were_skipped(self, monkeypatch):
        """Pins that the test above reaches the case: trusting the walk (as if
        the window check were missing) drops the relation the Model carries."""
        from djust.change_detection import ChangedKeys

        monkeypatch.setattr(
            rust_bridge,
            "_snapshot_walks_for_sync",
            lambda view, keys, pre: keys.fingerprints if type(keys) is ChangedKeys else None,
        )
        # The stale walk calls the list plain, so no scan sees the late Model.
        now, scans = await _run(monkeypatch, LateModel, ["bump", "bump"])
        assert scans[0][0] == 0


class TestTheProof:
    def test_it_needs_the_same_object_and_a_plain_walk(self):
        rows = [{"a": 1}]
        other = [{"a": 1}]
        walks = {id(rows): (rows, ("fp",), True)}
        assert rust_bridge._plain_by_snapshot(walks, "rows", rows)
        assert not rust_bridge._plain_by_snapshot(walks, "rows", other)
        assert not rust_bridge._plain_by_snapshot(None, "rows", rows)
        assert not rust_bridge._plain_by_snapshot({id(rows): (rows, ("fp",), False)}, "rows", rows)
        # An entry whose object is not this one (a reused id) proves nothing.
        assert not rust_bridge._plain_by_snapshot({id(rows): (other, ("fp",), True)}, "rows", rows)

    def test_the_switch_turns_it_off(self, monkeypatch):
        rows = [{"a": 1}]
        walks = {id(rows): (rows, ("fp",), True)}
        with harness.shortcuts(monkeypatch, "plain_model_scans"):
            assert not rust_bridge._plain_by_snapshot(walks, "rows", rows)
