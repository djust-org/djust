"""Regression tests for #2664 — in-place mutation of LiveView state must re-render.

The djust.org kanban demo mutated ``self.kanban_columns[col].append(card)``
in place. Two seams hid the change:

1. ``_snapshot_assigns`` fingerprinted a dict by ``(id, len, keys)`` only, so
   an append into one of its VALUE lists left ``pre == post`` and the event
   auto-skipped (``noop`` frame).
2. When something else DID change (the demo bumped a ``_kanban_version``
   counter), the render ran but ``_sync_state_to_rust`` compared the
   container by VALUE against ``_prev_context_containers[key]`` — which was
   the SAME object (mutation-after-capture aliasing, #1039) — so the mutated
   dict was never re-sent, Rust diffed the stale snapshot against itself and
   the frame carried ``patches: []``.

Both are driven here through a real ``WebsocketCommunicator`` → ``LiveViewConsumer``
→ ``ViewRuntime.dispatch_event`` round-trip (#1466: no HTTP proxy, no mocked
session). The fix is one shared structural fingerprint
(``djust.change_detection.deep_fingerprint``) used by every change-detection
path.
"""

from __future__ import annotations

import pytest
from asgiref.sync import sync_to_async

from djust import LiveView
from djust.decorators import event_handler


class _KanbanView(LiveView):
    """The djust.org kanban shape: a dict of lists mutated in place."""

    template = (
        '<div dj-view="djust.tests.test_in_place_mutation_2664._KanbanView" dj-id="0">'
        "{% for col, cards in kanban_columns.items %}"
        '<ul id="col-{{ col }}">{% for c in cards %}<li>{{ c.title }}</li>{% endfor %}</ul>'
        "{% endfor %}"
        "</div>"
    )

    def mount(self, request, **kwargs):
        self.kanban_columns = {
            "todo": [{"id": 1, "title": "Write tests"}, {"id": 2, "title": "Fix bug"}],
            "done": [],
        }
        self._kanban_version = 0

    def get_context_data(self, **kwargs):
        return {"kanban_columns": self.kanban_columns}

    @event_handler()
    def move_card(self, card_id: int = 0, target: str = "", **kwargs):
        # The issue's handler, verbatim in shape: in-place remove + append.
        for _col, cards in self.kanban_columns.items():
            card = next((c for c in cards if c["id"] == card_id), None)
            if card:
                cards.remove(card)
                self.kanban_columns[target].append(card)
                break

    @event_handler()
    def move_card_versioned(self, card_id: int = 0, target: str = "", **kwargs):
        # Same mutation plus the demo's version counter: the render RUNS but
        # the stale-alias comparison in _sync_state_to_rust dropped the dict.
        self.move_card(card_id=card_id, target=target)
        self._kanban_version += 1


async def _receive_until(communicator, wanted_types, *, tries=6, timeout=3):
    last = None
    for _ in range(tries):
        last = await communicator.receive_json_from(timeout=timeout)
        if last.get("type") in wanted_types:
            return last
    return last


async def _mounted_communicator():
    pytest.importorskip("channels")
    from channels.testing import WebsocketCommunicator
    from django.contrib.sessions.backends.db import SessionStore

    from djust.websocket import LiveViewConsumer

    def _create_session():
        s = SessionStore()
        s.create()
        return s.session_key

    session_key = await sync_to_async(_create_session)()

    class _ScopeSession:
        def __init__(self, key):
            self.session_key = key

    communicator = WebsocketCommunicator(LiveViewConsumer.as_asgi(), "/ws/")
    communicator.scope["session"] = _ScopeSession(session_key)
    connected, _ = await communicator.connect()
    assert connected
    await communicator.receive_json_from(timeout=2)  # connect frame
    await communicator.send_json_to(
        {"type": "mount", "view": f"{__name__}._KanbanView", "url": "/kanban/"}
    )
    mount_resp = await _receive_until(communicator, {"mount"})
    assert mount_resp.get("type") == "mount", mount_resp
    return communicator


def _frame_updates_dom(frame) -> bool:
    if frame.get("type") == "patch":
        return bool(frame.get("patches"))
    if frame.get("type") == "html_update":
        return "Write tests" in (frame.get("html") or "")
    return False


@pytest.mark.django_db
@pytest.mark.asyncio
@pytest.mark.parametrize("event", ["move_card", "move_card_versioned"])
async def test_in_place_mutation_reaches_the_browser(event):
    """An in-place ``dict[list].append`` must produce a DOM update frame with
    content — not ``noop`` (seam 1) and not ``patches: []`` (seam 2)."""
    from django.test import override_settings

    with override_settings(LIVEVIEW_ALLOWED_MODULES=[__name__]):
        communicator = await _mounted_communicator()
        await communicator.send_json_to(
            {
                "type": "event",
                "event": event,
                "params": {"card_id": 1, "target": "done"},
                "ref": 1,
            }
        )
        frame = await _receive_until(communicator, {"patch", "html_update", "noop"})
        assert _frame_updates_dom(frame), (
            f"{event}: in-place mutation of kanban_columns must reach the client as a "
            f"non-empty patch/html_update; got {frame!r} (#2664)"
        )
        await communicator.disconnect()


def test_all_change_detection_paths_share_one_fingerprint():
    """#1646 pin: every path that decides "did this value change?" must call the
    ONE structural fingerprint, so no path can drift back to an aliasing compare."""
    import importlib
    import inspect

    modules = [
        importlib.import_module(name)
        for name in (
            "djust.live_view",
            "djust.decorators",
            "djust.websocket",
            "djust.mixins.rust_bridge",
        )
    ]
    rust_bridge = modules[-1]
    for module in modules:
        src = inspect.getsource(module)
        assert "deep_fingerprint(" in src, f"{module.__name__} must use deep_fingerprint"
    assert "_prev_context_containers" not in inspect.getsource(rust_bridge), (
        "rust_bridge must not keep an aliased container for value comparison (#1039)"
    )
