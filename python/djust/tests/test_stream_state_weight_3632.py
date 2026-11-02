"""Why stream rows stay in the stored state (#3632, part 2): the cheap fix is unsafe.

``LiveView._reset_temporary_assigns`` returns at ``if not self.temporary_assigns``
before it reaches ``_reset_streams()``, so a view that uses streams without
``temporary_assigns`` keeps every stream row in the stored Rust state (about 80 KB
for 30 rows of 2.7 KB). The obvious fix is to clear the stream items after each
render, as the temporary-assigns path does.

These tests run both through the real consumer and show what the candidate does.
The server-held list is what the next render lists, and the differ turns that list
into DOM changes against the stored tree (``docs/website/guides/large-lists.md``:
stream ``limit`` and ``stream_delete`` work by dropping items from that list). With
the items cleared after the render, the next render lists no rows while the stored
tree still has them, so the first unrelated event removes every row from the page.

If someone later finds a safe design for #3632 (see the issue), these tests are
the contract to rewrite: the baseline tests describe what the page must keep doing.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from unittest.mock import patch

import pytest
from asgiref.sync import sync_to_async
from channels.testing import WebsocketCommunicator
from django.contrib.sessions.backends.db import SessionStore
from django.test import override_settings

from djust import LiveView
from djust.decorators import event_handler

pytestmark = [pytest.mark.django_db]

MODULE = __name__


@dataclass
class Row:
    """A stream item with an ``id``: the default dom id is ``<stream>-<item.id>``."""

    id: int
    body: str


ROWS = [Row(i, f"BODY{i}") for i in range(5)]


class Feed(LiveView):
    template = (
        f'<div dj-view="{MODULE}.Feed" dj-id="0"><div id="list" dj-stream="rows">'
        '{% for r in streams.rows %}<div id="rows-{{ r.id }}">{{ r.body }}</div>{% endfor %}'
        "</div>|{{ n }}</div>"
    )

    def mount(self, request, **kwargs):
        self.stream("rows", ROWS)
        self.n = 0

    @event_handler()
    def bump(self, **kwargs):
        """An event that touches no stream."""
        self.n += 1

    @event_handler()
    def add(self, **kwargs):
        self.stream("rows", [Row(100 + self.n, f"NEW{self.n}")])
        self.n += 1

    # ``drop`` and ``cap`` also move ``n``: a handler whose ONLY effect is a stream
    # mutation is skipped as a no-op over the WebSocket (filed separately), and the
    # point here is what the render does with the held list once it runs.
    @event_handler()
    def drop(self, **kwargs):
        self.stream_delete("rows", ROWS[2])
        self.n += 1

    @event_handler()
    def cap(self, **kwargs):
        self.stream_prune("rows", 3, edge="top")
        self.n += 1


def _clear_streams_after_every_render(self):
    """The candidate fix for #3632 part 2, applied to every view."""
    self._reset_streams()


async def _drive(events, *, candidate=False):
    """Mount ``Feed`` through the real consumer and send ``events``; return each
    event's patch list."""
    from djust.websocket import LiveViewConsumer

    from .test_ws_send_version_1788 import _receive_until

    def make_key():
        session = SessionStore()
        session.create()
        return session.session_key

    key = await sync_to_async(make_key)()

    class Session:
        session_key = key

    communicator = WebsocketCommunicator(
        LiveViewConsumer.as_asgi(),
        "/ws/",
        headers=[(b"host", b"testserver"), (b"origin", b"http://testserver")],
    )
    communicator.scope["session"] = Session()
    patched = (
        patch.object(LiveView, "_reset_temporary_assigns", _clear_streams_after_every_render)
        if candidate
        else patch.object(LiveView, "_reset_temporary_assigns", LiveView._reset_temporary_assigns)
    )
    results = []
    with patched, override_settings(LIVEVIEW_ALLOWED_MODULES=[MODULE]):
        connected, _ = await communicator.connect()
        assert connected
        await communicator.receive_json_from()
        await communicator.send_json_to({"type": "mount", "view": f"{MODULE}.Feed", "url": "/"})
        mounted = await _receive_until(communicator, "mount")
        assert all(f"BODY{i}" in mounted["html"] for i in range(5))
        for ref, event in enumerate(events, start=1):
            await communicator.send_json_to(
                {"type": "event", "event": event, "params": {}, "ref": ref}
            )
            frame = await _receive_until(communicator, "patch")
            results.append(frame.get("patches") or [])
        await communicator.disconnect()
    return results


def _kinds(patches):
    return [p["type"] for p in patches]


@pytest.mark.asyncio
class TestWhatTheCurrentBehaviourGuarantees:
    async def test_an_event_that_touches_no_stream_leaves_the_rows_alone(self):
        (patches,) = await _drive(["bump"])
        assert _kinds(patches) == ["SetText"], patches

    async def test_a_stream_insert_adds_exactly_one_row_after_the_existing_ones(self):
        (patches,) = await _drive(["add"])
        assert _kinds(patches) == ["InsertChild", "SetText"], patches
        assert patches[0]["index"] == len(ROWS)

    async def test_stream_delete_and_prune_remove_rows_by_diffing_the_held_list(self):
        deleted, pruned = await _drive(["drop", "cap"])
        # One row left the list, so exactly one row leaves the page (the diff is by
        # position, so the rows after it are rewritten: SetAttr/SetText).
        assert _kinds(deleted).count("RemoveChild") == 1, deleted
        # 4 rows remained after the delete; capping to 3 from the top removes one more.
        assert _kinds(pruned).count("RemoveChild") == 1, pruned


@pytest.mark.asyncio
class TestTheCandidateFixIsUnsafe:
    async def test_clearing_stream_items_after_each_render_removes_every_row_on_an_unrelated_event(
        self,
    ):
        """The falsification of 'just call _reset_streams() for every view'."""
        (patches,) = await _drive(["bump"], candidate=True)
        removed = [p for p in patches if p["type"] == "RemoveChild"]
        assert len(removed) == len(ROWS), json.dumps(patches)[:400]

    async def test_and_the_next_insert_then_replaces_the_page_instead_of_extending_it(self):
        _bump, added = await _drive(["bump", "add"], candidate=True)
        # The row is inserted at index 0 of a container the client has already
        # emptied: the page now shows one row where it showed six.
        assert added[0]["type"] == "InsertChild" and added[0]["index"] == 0, added
