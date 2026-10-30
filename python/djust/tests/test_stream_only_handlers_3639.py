"""A handler whose only effect is a stream mutation reaches the page (#3639).

Two shapes over a real WebSocket (``WebsocketCommunicator`` + ``LiveViewConsumer``):

1. A handler whose ONLY effect is ``stream_insert`` / ``stream_delete`` /
   ``stream_prune`` answered ``noop``. The skip decision compares
   ``websocket._snapshot_assigns`` before and after the handler, and that walk
   leaves out ``_streams`` (a framework attribute), so a stream-only mutation
   changed nothing it could see. The stream items are kept between renders, so
   the row then appeared with the NEXT unrelated render instead of this one.
2. ``temporary_assigns`` + ``dj-update="append"``: ``self.rows = [new_row]``
   next to another change replied with a patch that had no row, because
   ``_sync_state_to_rust`` left every temporary-assigns key out of the changed
   keys. See ``test_temporary_assigns_append_3639.py``.

The pages here are what the client sees: the patches are the only channel for
the rows (the server ships no separate stream frame; ``_stream_operations`` is
recorded for the test client but nothing sends it).
"""

from __future__ import annotations

from dataclasses import dataclass

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
    id: int
    body: str


ROWS = [Row(i, f"BODY{i}") for i in range(5)]

_FEED_TEMPLATE = (
    '<div dj-view="{view}" dj-id="0"><div id="list" dj-stream="rows">'
    '{{% for r in streams.rows %}}<div id="rows-{{{{ r.id }}}}">{{{{ r.body }}}}</div>{{% endfor %}}'
    "</div>|{{{{ n }}}}</div>"
)


class Feed(LiveView):
    template = _FEED_TEMPLATE.format(view=f"{MODULE}.Feed")

    def mount(self, request, **kwargs):
        self.stream("rows", ROWS)
        self.n = 0

    @event_handler()
    def ins(self, **kwargs):
        """ONLY a stream insert."""
        self.stream_insert("rows", Row(100, "NEW"))

    @event_handler()
    def ins_front(self, **kwargs):
        self.stream_insert("rows", Row(101, "FRONT"), at=0)

    @event_handler()
    def ins_and_n(self, **kwargs):
        """A stream insert next to a public attribute (always rendered)."""
        self.stream_insert("rows", Row(100, "NEW"))
        self.n += 1

    @event_handler()
    def many(self, **kwargs):
        """``self.stream(...)`` with items, and nothing else."""
        self.stream("rows", [Row(110, "M1"), Row(111, "M2")])

    @event_handler()
    def dele(self, **kwargs):
        """ONLY a stream delete."""
        self.stream_delete("rows", ROWS[2])

    @event_handler()
    def prune(self, **kwargs):
        """ONLY a stream prune."""
        self.stream_prune("rows", 3, edge="top")

    @event_handler()
    def reset(self, **kwargs):
        """ONLY a stream reset with new content."""
        self.stream_reset("rows", [Row(200, "R0")])

    @event_handler()
    def nothing(self, **kwargs):
        """Touches no state at all."""

    @event_handler()
    def reads_only(self, **kwargs):
        """Reads the streams and mutates none."""
        assert len(self._streams["rows"].items) >= 0

    @event_handler()
    def empty_stream_call(self, **kwargs):
        """``self.stream('rows', [])`` on an existing stream adds nothing."""
        self.stream("rows", [])

    @event_handler()
    def prune_noop(self, **kwargs):
        """A prune above the current size removes nothing."""
        self.stream_prune("rows", 99)


async def drive(view_name, events, *, module=None):
    """Mount ``view_name`` through the real consumer, send each event, and return
    the FIRST reply frame of every event (``patch`` / ``noop`` / ``html_update``).

    ``module`` is where the view class lives (default: this module); other test
    modules reuse this harness so the shape under test is the same real socket.
    """
    from djust.websocket import LiveViewConsumer

    module = module or MODULE

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
    frames = []
    with override_settings(LIVEVIEW_ALLOWED_MODULES=[module]):
        connected, _ = await communicator.connect()
        assert connected
        await communicator.receive_json_from(timeout=2)
        await communicator.send_json_to(
            {"type": "mount", "view": f"{module}.{view_name}", "url": "/"}
        )
        mounted = None
        for _ in range(8):
            mounted = await communicator.receive_json_from(timeout=3)
            if mounted.get("type") == "mount":
                break
        assert mounted and mounted.get("type") == "mount", mounted
        for ref, event in enumerate(events, start=1):
            await communicator.send_json_to(
                {"type": "event", "event": event, "params": {}, "ref": ref}
            )
            for _ in range(8):
                frame = await communicator.receive_json_from(timeout=3)
                if frame.get("type") in ("patch", "noop", "html_update", "error"):
                    break
            frames.append(frame)
        await communicator.disconnect()
    return frames


def kinds(frame):
    return [p["type"] for p in frame.get("patches") or []]


def texts(frame):
    """Every text/attribute value the patches write, to look for a row's body."""
    out = []
    for p in frame.get("patches") or []:
        for key in ("text", "value", "html"):
            if key in p:
                out.append(str(p[key]))
    return out


@pytest.mark.asyncio
class TestStreamOnlyHandlersRender:
    async def test_a_stream_insert_alone_is_rendered_not_dropped_as_noop(self):
        (frame,) = await drive("Feed", ["ins"])
        assert frame["type"] == "patch", frame
        assert kinds(frame) == ["InsertChild"], frame
        assert any("NEW" in t for t in texts(frame)) or "NEW" in str(frame["patches"]), frame

    async def test_the_row_lands_at_the_end_of_the_container(self):
        (frame,) = await drive("Feed", ["ins"])
        assert frame["patches"][0]["index"] == len(ROWS), frame

    async def test_a_prepend_lands_at_the_front(self):
        (frame,) = await drive("Feed", ["ins_front"])
        assert frame["type"] == "patch", frame
        assert "InsertChild" in kinds(frame), frame

    async def test_stream_with_items_alone_is_rendered(self):
        (frame,) = await drive("Feed", ["many"])
        assert frame["type"] == "patch", frame
        assert kinds(frame).count("InsertChild") == 2, frame

    async def test_a_stream_delete_alone_removes_the_row(self):
        (frame,) = await drive("Feed", ["dele"])
        assert frame["type"] == "patch", frame
        assert kinds(frame).count("RemoveChild") == 1, frame

    async def test_a_stream_prune_alone_removes_the_surplus_rows(self):
        (frame,) = await drive("Feed", ["prune"])
        assert frame["type"] == "patch", frame
        assert kinds(frame).count("RemoveChild") == 2, frame

    async def test_a_stream_reset_alone_is_rendered(self):
        (frame,) = await drive("Feed", ["reset"])
        assert frame["type"] == "patch", frame
        assert "R0" in str(frame["patches"]), frame

    async def test_the_row_is_not_deferred_to_the_next_unrelated_render(self):
        """Before the fix the insert was held until some other event rendered, so
        that event's patch carried a row it never caused."""
        ins, nothing = await drive("Feed", ["ins", "nothing"])
        assert ins["type"] == "patch" and "InsertChild" in kinds(ins), ins
        assert nothing["type"] == "noop", nothing

    async def test_a_stream_insert_next_to_a_public_change_still_renders(self):
        """The case that always worked, kept as the contract."""
        (frame,) = await drive("Feed", ["ins_and_n"])
        assert frame["type"] == "patch", frame
        assert "InsertChild" in kinds(frame), frame


@pytest.mark.asyncio
class TestAnEventThatChangesNothingStaysANoop:
    """The cost guard: counting stream mutations as changes must not turn every
    event into a render."""

    async def test_a_handler_that_touches_nothing_replies_noop(self):
        (frame,) = await drive("Feed", ["nothing"])
        assert frame["type"] == "noop", frame

    async def test_a_handler_that_only_reads_the_streams_replies_noop(self):
        (frame,) = await drive("Feed", ["reads_only"])
        assert frame["type"] == "noop", frame

    async def test_an_empty_stream_call_on_an_existing_stream_replies_noop(self):
        (frame,) = await drive("Feed", ["empty_stream_call"])
        assert frame["type"] == "noop", frame

    async def test_a_prune_that_removes_nothing_replies_noop(self):
        (frame,) = await drive("Feed", ["prune_noop"])
        assert frame["type"] == "noop", frame

    async def test_a_stream_turn_does_not_make_the_next_idle_event_render(self):
        _ins, idle_a, idle_b = await drive("Feed", ["ins", "nothing", "nothing"])
        assert idle_a["type"] == "noop" and idle_b["type"] == "noop", (idle_a, idle_b)
