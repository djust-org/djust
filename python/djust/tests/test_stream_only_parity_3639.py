"""Every place that decides "did the handler change anything" sees a stream (#3639).

``websocket._snapshot_assigns`` is the one function the skip decisions compare
before and after a handler, so the stream signal lives there and every caller
inherits it. This file pins each caller once, on its own transport:

==========================================  ======================================
decision                                    covered by
==========================================  ======================================
``ViewRuntime.dispatch_event`` over a       ``test_stream_only_handlers_3639.py``
WebSocket                                   (real ``WebsocketCommunicator``)
``ViewRuntime.dispatch_event`` over SSE     ``TestSseRuntime`` (real
                                            ``SSESessionTransport``)
``_tick_once`` (WebSocket tick)             ``TestTick``
HTTP POST fallback                          ``TestHttpFallback`` (renders every
                                            event; there is no skip decision to
                                            teach, so this pins that it finds the
                                            stream by itself)
API dispatch (``assigns`` in the answer)    ``TestSnapshotContract`` (a stream
                                            epoch never leaks into ``assigns``)
deferred-activity twins, component event    use ``_snapshot_assigns`` unchanged;
route                                       pinned by the call-site inventory
==========================================  ======================================

``LiveViewTestClient.send_event`` is not one of them: it calls the handler and
returns, and its render is a separate call, so it has no skip decision.
"""

from __future__ import annotations

import ast
import asyncio
import json
from pathlib import Path

import pytest
from django.contrib.sessions.middleware import SessionMiddleware
from django.test import RequestFactory

from djust import LiveView
from djust.decorators import event_handler

from .test_stream_only_handlers_3639 import Feed
from .test_temporary_assigns_append_3639 import MODULE as TEMP_MODULE  # noqa: F401
from .test_temporary_assigns_append_3639 import Temp

pytestmark = [pytest.mark.django_db]

MODULE = __name__


def _kinds(frame):
    return [p["type"] for p in frame.get("patches") or []]


class DictFeed(LiveView):
    """Like ``Feed`` with rows as dicts: the HTTP fallback round-trips the view's
    state through the session, which turns a dataclass into its repr."""

    template = (
        f'<div dj-view="{MODULE}.DictFeed" dj-id="0"><div id="list" dj-stream="rows">'
        '{% for r in streams.rows %}<div id="rows-{{ r.id }}">{{ r.body }}</div>{% endfor %}'
        "</div>|{{ n }}</div>"
    )

    def mount(self, request, **kwargs):
        self.stream("rows", [{"id": i, "body": f"B{i}"} for i in range(5)])
        self.n = 0

    @event_handler()
    def ins(self, **kwargs):
        self.stream_insert("rows", {"id": 100, "body": "NEW"})

    @event_handler()
    def dele(self, **kwargs):
        self.stream_delete("rows", {"id": 2, "body": "B2"})

    @event_handler()
    def nothing(self, **kwargs):
        pass


class DictTemp(LiveView):
    temporary_assigns = {"rows": []}
    template = (
        f'<div dj-view="{MODULE}.DictTemp" dj-id="0"><div id="list" dj-update="append">'
        '{% for r in rows %}<div id="rows-{{ r.id }}">{{ r.body }}</div>{% endfor %}'
        "</div>|{{ k }}</div>"
    )

    def mount(self, request, **kwargs):
        self.rows = [{"id": i, "body": f"B{i}"} for i in range(5)]
        self.k = 0

    @event_handler()
    def add(self, **kwargs):
        self.k += 1
        self.rows = [{"id": 100 + self.k, "body": f"NEW{self.k}"}]


class TickFeed(LiveView):
    tick_interval = 50
    template = f'<div dj-view="{MODULE}.TickFeed" dj-id="0"><div id="list">{{% for r in streams.rows %}}<div id="rows-{{{{ r.id }}}}">{{{{ r.body }}}}</div>{{% endfor %}}</div></div>'

    def mount(self, request, **kwargs):
        self.stream("rows", [{"id": 0, "body": "B0"}])

    def handle_tick(self):
        """Fills the feed to two rows and then stops. The stream is the ONLY state
        it touches, and it is also what decides whether there is anything to do."""
        if len(self._streams["rows"].items) < 2:
            self.stream_insert("rows", {"id": 1, "body": "B1"})


class _SseSession:
    """What the SSE transport needs of an ``SSESession``: a queue to push to."""

    def __init__(self):
        self.session_id = "sse-test"
        self.frames = []
        self._render_lock = asyncio.Lock()

    def push(self, data):
        self.frames.append(data)


def _sse_runtime(view):
    from djust.runtime import SSESessionTransport, ViewRuntime

    session = _SseSession()
    runtime = ViewRuntime(SSESessionTransport(session))
    view.mount(None)
    view.render_with_diff()
    runtime.view_instance = view
    return runtime, session


@pytest.mark.asyncio
class TestSseRuntime:
    async def test_a_stream_only_handler_renders_over_sse(self):
        runtime, session = _sse_runtime(Feed())
        await runtime.dispatch_event({"event": "ins", "params": {}})
        (frame,) = [f for f in session.frames if f.get("type") in ("patch", "noop")]
        assert frame["type"] == "patch" and _kinds(frame) == ["InsertChild"], frame

    async def test_an_idle_handler_is_a_noop_over_sse(self):
        runtime, session = _sse_runtime(Feed())
        await runtime.dispatch_event({"event": "nothing", "params": {}})
        assert [f["type"] for f in session.frames if f.get("type") in ("patch", "noop")] == ["noop"]

    async def test_a_stream_prune_alone_renders_over_sse(self):
        runtime, session = _sse_runtime(Feed())
        await runtime.dispatch_event({"event": "prune", "params": {}})
        (frame,) = [f for f in session.frames if f.get("type") in ("patch", "noop")]
        assert _kinds(frame).count("RemoveChild") == 2, frame

    async def test_temporary_rows_are_appended_over_sse(self):
        runtime, session = _sse_runtime(Temp())
        await runtime.dispatch_event({"event": "add", "params": {}})
        (frame,) = [f for f in session.frames if f.get("type") in ("patch", "noop")]
        assert [p["index"] for p in frame["patches"] if p["type"] == "InsertChild"] == [5], frame
        assert "RemoveChild" not in _kinds(frame), frame

    async def test_sse_and_websocket_agree_on_the_frame(self):
        """The same event answers with the same patch kinds on both transports."""
        from .test_stream_only_handlers_3639 import drive

        runtime, session = _sse_runtime(Feed())
        await runtime.dispatch_event({"event": "ins", "params": {}})
        (sse,) = [f for f in session.frames if f.get("type") in ("patch", "noop")]
        (ws,) = await drive("Feed", ["ins"])
        assert sse["type"] == ws["type"] and _kinds(sse) == _kinds(ws)


def _tick_consumer():
    from djust.websocket import LiveViewConsumer

    consumer = LiveViewConsumer()
    consumer.scope = {"session": None, "user": None}
    consumer.sent = []

    async def _capture(payload):
        consumer.sent.append(payload)

    consumer.send_json = _capture
    view = TickFeed()
    view.mount(None)
    view.render_with_diff()
    consumer.view_instance = view
    return consumer


@pytest.mark.asyncio
class TestTick:
    async def test_a_tick_whose_only_effect_is_a_stream_insert_is_sent(self):
        consumer = _tick_consumer()
        assert await consumer._tick_once() is True
        (frame,) = [f for f in consumer.sent if f.get("source") == "tick"]
        assert "InsertChild" in _kinds(frame), frame

    async def test_a_tick_after_the_stream_tick_is_idle_and_sends_nothing(self):
        consumer = _tick_consumer()
        await consumer._tick_once()
        consumer.sent.clear()
        assert await consumer._tick_once() is False
        assert not [f for f in consumer.sent if f.get("source") == "tick"]


def _session_request(factory):
    request = factory.get("/t/")
    SessionMiddleware(lambda r: None).process_request(request)
    request.session.save()
    return request


def _post(view, factory, get_request, event):
    post = factory.post(
        "/t/", data=json.dumps({"event": event, "params": {}}), content_type="application/json"
    )
    post.session = get_request.session
    return json.loads(view.post(post).content)


class TestHttpFallback:
    """The POST fallback renders every event, so it never skipped a stream-only
    handler; these pin that it sees the stream and the temporary rows itself."""

    def test_a_stream_insert_alone_is_in_the_answer(self):
        view, factory = DictFeed(), RequestFactory()
        get = _session_request(factory)
        view.get(get)
        body = _post(view, factory, get, "ins")
        assert [p["type"] for p in body["patches"]] == ["InsertChild"], body

    def test_a_stream_delete_alone_is_in_the_answer(self):
        view, factory = DictFeed(), RequestFactory()
        get = _session_request(factory)
        view.get(get)
        body = _post(view, factory, get, "dele")
        assert [p["type"] for p in body["patches"]].count("RemoveChild") == 1, body

    def test_an_idle_event_answers_with_no_patches(self):
        view, factory = DictFeed(), RequestFactory()
        get = _session_request(factory)
        view.get(get)
        assert _post(view, factory, get, "nothing")["patches"] == []

    def test_temporary_rows_are_appended(self):
        view, factory = DictTemp(), RequestFactory()
        get = _session_request(factory)
        view.get(get)
        first = _post(view, factory, get, "add")
        second = _post(view, factory, get, "add")
        assert [p["index"] for p in first["patches"] if p["type"] == "InsertChild"] == [5], first
        assert [p["index"] for p in second["patches"] if p["type"] == "InsertChild"] == [6], second
        assert "RemoveChild" not in [p["type"] for p in second["patches"]], second


class TestSnapshotContract:
    """``_snapshot_assigns`` is where the stream signal lives."""

    def _view(self):
        view = Feed()
        view.mount(None)
        return view

    def test_every_stream_mutation_changes_the_snapshot(self):
        from djust.websocket import _compute_changed_keys, _snapshot_assigns

        for mutate in (
            lambda v: v.stream_insert("rows", {"id": 9, "body": "x"}),
            lambda v: v.stream_delete("rows", {"id": 1, "body": "x"}),
            lambda v: v.stream_prune("rows", 1),
            lambda v: v.stream_reset("rows", [{"id": 7, "body": "y"}]),
            lambda v: v.stream("rows", [{"id": 8, "body": "z"}]),
            lambda v: v.stream("rows", [{"id": 8, "body": "z"}], reset=True),
        ):
            view = self._view()
            before = _snapshot_assigns(view)
            mutate(view)
            assert _compute_changed_keys(before, _snapshot_assigns(view)) == {"_streams"}

    def test_a_mutation_that_changed_nothing_leaves_the_snapshot_equal(self):
        from djust.websocket import _snapshot_assigns

        for no_change in (
            lambda v: v.stream_prune("rows", 99),
            lambda v: v.stream_delete("rows", {"id": 12345, "body": "absent"}),
            lambda v: v.stream("rows", []),
            lambda v: v.stream_reset("nope"),
            # ``limit=`` whose prune removes nothing, and a reset of a stream
            # that is new or already empty
            lambda v: v.stream("rows", [], limit=99),
            lambda v: v.stream("rows", [], at=0, limit=99),
            lambda v: v.stream("fresh", [], reset=True),
            lambda v: v.stream("fresh", [], limit=5),
        ):
            view = self._view()
            before = _snapshot_assigns(view)
            no_change(view)
            assert _snapshot_assigns(view) == before

    def test_resetting_a_stream_that_is_already_empty_changes_nothing(self):
        from djust.websocket import _snapshot_assigns

        view = self._view()
        view.stream_reset("rows")
        before = _snapshot_assigns(view)
        view.stream("rows", [], reset=True)
        view.stream_reset("rows")
        assert _snapshot_assigns(view) == before

    def test_a_limit_that_does_prune_counts(self):
        from djust.websocket import _compute_changed_keys, _snapshot_assigns

        view = self._view()
        before = _snapshot_assigns(view)
        view.stream("rows", [{"id": 9, "body": "x"}], limit=2)
        assert _compute_changed_keys(before, _snapshot_assigns(view)) == {"_streams"}

    def test_the_render_reset_does_not_register_as_a_change(self):
        """``_reset_streams`` runs after a render, outside the handler window."""
        from djust.websocket import _snapshot_assigns

        view = self._view()
        view.stream_insert("rows", {"id": 9, "body": "x"})
        before = _snapshot_assigns(view)
        view._reset_streams()
        assert _snapshot_assigns(view) == before

    def test_the_epoch_is_framework_state_and_stays_out_of_assigns(self):
        from djust.api.dispatch import _public_assigns_snapshot_diff

        view = self._view()
        view.stream_insert("rows", {"id": 9, "body": "x"})
        assert "_stream_epoch" in view._framework_attrs
        assert _public_assigns_snapshot_diff(view, {"_streams"}) == {}

    def test_the_epoch_is_not_part_of_the_view_state(self):
        view = self._view()
        view.stream_insert("rows", {"id": 9, "body": "x"})
        assert "_stream_epoch" not in view.get_state()


class TestCallSiteInventory:
    """A new "did the handler change anything" decision has to use
    ``_snapshot_assigns`` to see streams. This is the exact list of call sites,
    by module and enclosing function, so a new site in any of them (not just a
    new module) fails here and its author has to decide what a stream does there
    (the count-based pin of CLAUDE.md, #1125)."""

    EXPECTED = {
        # the HTTP API endpoint: the ``assigns`` diff (``_streams`` never reported)
        ("python/djust/api/dispatch.py", "_dispatch_api_authenticated"): 2,
        # the HTTP POST fallback: the DropdownMenu observation only
        ("python/djust/mixins/request.py", "_post_turn_body"): 2,
        # ViewRuntime: the event spine's pre/post and noop-token, the deferred
        # twin, the bound-component route
        ("python/djust/runtime.py", "_dispatch_component_event"): 3,
        ("python/djust/runtime.py", "_dispatch_event_render"): 2,
        ("python/djust/runtime.py", "_dispatch_single_event_body"): 2,
        ("python/djust/runtime.py", "_legacy_noop_snapshot"): 1,
        ("python/djust/runtime.py", "handler"): 1,
        # the consumer: its deferred twin and the tick
        ("python/djust/websocket.py", "_dispatch_single_event_body"): 2,
        ("python/djust/websocket.py", "_tick_locked"): 2,
        ("python/djust/websocket.py", "tick_turn"): 2,
    }

    def test_the_call_sites_are_exactly_the_ones_this_file_covers(self):
        from collections import Counter

        root = Path(__file__).resolve().parents[3]
        found = Counter()
        for path in (root / "python" / "djust").rglob("*.py"):
            if "tests" in path.parts:
                continue
            rel = str(path.relative_to(root))

            def visit(node, function):
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    function = node.name
                if isinstance(node, ast.Call):
                    func = node.func
                    name = func.id if isinstance(func, ast.Name) else getattr(func, "attr", None)
                    if name == "_snapshot_assigns":
                        found[(rel, function)] += 1
                for child in ast.iter_child_nodes(node):
                    visit(child, function)

            visit(ast.parse(path.read_text(encoding="utf-8")), None)
        assert dict(found) == self.EXPECTED, {
            "new or changed": {k: v for k, v in found.items() if self.EXPECTED.get(k) != v},
            "gone": {k: v for k, v in self.EXPECTED.items() if found.get(k) != v},
        }


class _StreamChild(LiveView):
    """A sticky child whose only handler effect is a stream insert."""

    sticky = True
    sticky_id = "child"
    template = (
        '<div>{% for r in streams.rows %}<p id="rows-{{ r.id }}">{{ r.body }}</p>{% endfor %}</div>'
    )

    def mount(self, request, **kwargs):
        self.stream("rows", [{"id": 0, "body": "B0"}])

    @event_handler()
    def ins(self, **kwargs):
        self.stream_insert("rows", {"id": 1, "body": "CHILD-NEW"})


class _Parent(LiveView):
    template = '<div dj-root dj-id="0"><h1>parent</h1></div>'

    def mount(self, request, **kwargs):
        pass


@pytest.mark.asyncio
class TestEmbeddedChild:
    """A ``view_id`` event re-renders the child's whole subtree and sends it as
    ``embedded_update`` whether or not state changed: there is no skip decision
    on that route to teach, so a stream-only child handler reaches the page."""

    async def test_a_stream_only_child_handler_sends_the_row(self):
        from djust.runtime import ViewRuntime

        sent = []

        class Transport:
            session_id = "child-test"
            client_ip = None

            async def send(self, data):
                sent.append(data)

            async def send_error(self, error, **kwargs):
                sent.append({"type": "error", "error": error, **kwargs})

            async def close(self, code=1000):
                pass

            def next_client_version(self, html, rust_version):
                return rust_version

            def build_request(self):
                return None

            def on_view_mounted(self, view_instance):
                pass

            from contextlib import asynccontextmanager

            @asynccontextmanager
            async def event_context(self, view):
                yield

        parent = _Parent()
        parent.mount(None)
        child = _StreamChild()
        child.mount(None)
        parent._register_child("child-1", child)
        runtime = ViewRuntime(Transport())
        runtime.view_instance = parent

        await runtime.dispatch_event(
            {"type": "event", "event": "ins", "params": {"view_id": "child-1"}, "ref": 1}
        )

        (frame,) = [f for f in sent if f.get("type") == "embedded_update"]
        assert "CHILD-NEW" in frame["html"], frame
        assert "B0" in frame["html"], frame
