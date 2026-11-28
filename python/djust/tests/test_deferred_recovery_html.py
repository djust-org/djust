"""A WebSocket event turn that sends patches leaves the page unserialized (``_deferred_html``).

The recovery a client asks for after a failed patch (``request_html``) then
serializes the tree the turn committed. These tests drive a real
``WebsocketCommunicator`` and pin: the patch turn does not serialize the page;
recovery returns exactly the page a full render gives; a recovery after the
view moved on without re-arming gets a fresh render; a turn with more than 100
patches still compares sizes against the real page (the compression fallback);
and a render outside the event scope is unchanged.
"""

from __future__ import annotations

import pytest
from asgiref.sync import sync_to_async

from djust import LiveView, state
from djust import _deferred_html
from djust.decorators import event_handler
from djust.tests._frames import legacy_type


class _Table(LiveView):
    exposure_policy = "explicit"
    rows = state(list)
    title = state("t")
    template = (
        '<div dj-view="djust.tests.test_deferred_recovery_html._Table" dj-id="0">'
        "<h1>{{ title }}</h1><table>{% for r in rows %}<tr><td>{{ r.name }}</td>"
        "<td>{{ r.qty }}</td></tr>{% endfor %}</table></div>"
    )

    def mount(self, request, **kwargs):
        self.rows = [{"name": "row %d" % i, "qty": i} for i in range(200)]

    @event_handler()
    def one(self, **kwargs):
        rows = list(self.rows)
        rows[3] = {"name": "changed", "qty": 99}
        self.rows = rows

    @event_handler()
    def many(self, **kwargs):
        self.rows = [{"name": "new %d" % i, "qty": -i} for i in range(200)]


async def _receive_until(communicator, wanted, *, tries=6):
    last = None
    for _ in range(tries):
        last = await communicator.receive_json_from(timeout=30)
        if legacy_type(last) == wanted or last.get("type") == wanted:
            return last
    return last


async def _connect(view_path):
    from channels.testing import WebsocketCommunicator
    from django.contrib.sessions.backends.db import SessionStore

    from djust.websocket import LiveViewConsumer

    def create():
        s = SessionStore()
        s.create()
        return s.session_key

    key = await sync_to_async(create)()

    class _ScopeSession:
        def __init__(self, k):
            self.session_key = k

    consumers = []

    class _Recorded(LiveViewConsumer):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            consumers.append(self)

    communicator = WebsocketCommunicator(_Recorded.as_asgi(), "/ws/")
    communicator.djust_consumers = consumers
    communicator.scope["session"] = _ScopeSession(key)
    connected, _ = await communicator.connect()
    assert connected
    await communicator.receive_json_from()
    await communicator.send_json_to({"type": "mount", "view": view_path, "url": "/t/"})
    mounted = await _receive_until(communicator, "mount")
    assert mounted.get("type") == "mount", mounted
    return communicator


def _consumer_view(communicator):
    """The mounted view of the consumer ``_connect`` recorded for ``communicator``."""
    consumer = communicator.djust_consumers[-1]
    assert consumer.view_instance is not None, "no mounted consumer"
    return consumer, consumer.view_instance


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_a_patch_turn_defers_the_page_and_recovery_serializes_it(settings):
    pytest.importorskip("channels")
    settings.LIVEVIEW_ALLOWED_MODULES = [__name__]
    communicator = await _connect(__name__ + "._Table")
    consumer, view = _consumer_view(communicator)

    await communicator.send_json_to({"type": "event", "event": "one", "params": {}, "ref": 1})
    patched = await _receive_until(communicator, "patch")
    assert patched.get("patches"), patched
    version = _deferred_html.deferred_version(view)
    assert version is not None, "the patch turn must leave its page unserialized"
    assert consumer._recovery_html == "" and consumer._recovery_deferred == version
    assert view._rust_view.get_render_timing()["serialize_ms"] < 0.05

    expected = await sync_to_async(view._rust_view.committed_html)(version)
    assert "changed" in expected and "row 4" in expected

    await communicator.send_json_to({"type": "request_html"})
    recovery = await _receive_until(communicator, "html_recovery")
    assert recovery.get("type") == "html_recovery", recovery
    assert "changed" in recovery["html"] and "row 199" in recovery["html"]
    assert recovery["version"] == consumer._recovery_version
    await communicator.disconnect()


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_recovery_after_the_view_moved_on_renders_fresh(settings):
    pytest.importorskip("channels")
    settings.LIVEVIEW_ALLOWED_MODULES = [__name__]
    communicator = await _connect(__name__ + "._Table")
    consumer, view = _consumer_view(communicator)
    await communicator.send_json_to({"type": "event", "event": "one", "params": {}, "ref": 1})
    await _receive_until(communicator, "patch")
    stale = consumer._recovery_deferred

    def move_on():
        # A render that arms nothing (as a scoped child update does).
        view.title = "moved"
        view._sync_state_to_rust()
        view.render_with_diff()

    await sync_to_async(move_on)()
    assert await sync_to_async(view._rust_view.committed_html)(stale) is None

    await communicator.send_json_to({"type": "request_html"})
    recovery = await _receive_until(communicator, "html_recovery")
    assert recovery.get("type") == "html_recovery", recovery
    assert ">moved</h1>" in recovery["html"] and "changed" in recovery["html"]
    await communicator.disconnect()


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_many_patches_still_weigh_the_real_page(settings):
    """More than 100 patches: the compression test needs the page's size, so
    the deferred page is serialized for it (a 0-byte page always won)."""
    pytest.importorskip("channels")
    settings.LIVEVIEW_ALLOWED_MODULES = [__name__]
    communicator = await _connect(__name__ + "._Table")
    await communicator.send_json_to({"type": "event", "event": "many", "params": {}, "ref": 1})
    frame = await communicator.receive_json_from(timeout=30)
    while legacy_type(frame) not in ("patch", "html_update"):
        frame = await communicator.receive_json_from(timeout=30)
    if legacy_type(frame) == "html_update":
        assert "new 199" in frame["html"], "the fallback must carry the real page"
    else:
        assert len(frame["patches"]) > 100
    await communicator.disconnect()


def test_a_render_outside_the_scope_serializes_as_before(rf):
    view = _Table()
    request = rf.get("/t/")
    from django.contrib.sessions.backends.signed_cookies import SessionStore

    request.session = SessionStore()
    view.request = request
    view.mount(request)
    view.render_with_diff()
    view.one()
    html, patches, version = view.render_with_diff()
    assert patches and "changed" in html
    assert _deferred_html.deferred_version(view) is None
    with _deferred_html.deferring_html():
        view.many()
        html, patches, version = view.render_with_diff()
    assert html == "" and patches
    assert _deferred_html.deferred_version(view) == version
    assert "new 5" in _deferred_html.materialize(view, html, version)
