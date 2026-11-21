"""A ``{% live_render ... lazy=True %}`` child over a live connection (#4167).

Only the page GET ran the queued lazy fill, on the GET's own (discarded) view.
The socket's view never held the child, so its events answered "Embedded view
not found", and every socket render queued another fill nobody ran. On a live
runtime the child is now rendered in place with the non-lazy mount checks and
registered, so its events route; a refused one shows the lazy failure
envelope and is not routable. The page GET still defers.
"""

import copy
import json

import pytest
from asgiref.sync import sync_to_async
from django.contrib.auth.models import AnonymousUser
from django.contrib.sessions.backends.db import SessionStore
from django.test import RequestFactory, override_settings

from djust import LiveView, event_handler
from djust.decorators import state

# asyncio_mode = "auto" runs the async tests; an asyncio mark would also land on
# the sync test below and warn (#4167 review N3).
pytestmark = [pytest.mark.django_db(transaction=True)]

MODULE = __name__
PATH = "/lazy-4167/"
WS_SETTINGS = dict(
    LIVEVIEW_ALLOWED_MODULES=[MODULE], DEBUG=False, DJUST_CONFIG={}, DJUST_TENANTS={}
)


class LazyChild(LiveView):
    exposure_policy = "explicit"
    template = '<div>Lazy={{ count }}<button dj-click="increment">+</button></div>'
    count = state(0)
    mounts: list = []

    def mount(self, request, **kwargs):
        type(self).mounts.append(1)
        self.count = 1

    @event_handler()
    def increment(self, **kwargs):
        self.count += 1

    def get_context_data(self, **kwargs):
        return super().get_context_data(count=self.count, **kwargs)


class LazyPage(LiveView):
    exposure_policy = "explicit"
    template_name = "lazy_4167_page.html"
    n = state(0, persist="server")

    @event_handler()
    def bump(self, **kwargs):
        self.n += 1

    def get_context_data(self, **kwargs):
        return super().get_context_data(n=self.n, **kwargs)


class RefusedChild(LazyChild):
    login_required = True
    count = state(0)


class RefusedPage(LazyPage):
    template_name = "lazy_4167_refused.html"
    n = state(0, persist="server")


class FailingChild(LazyChild):
    count = state(0)

    def mount(self, request, **kwargs):
        raise RuntimeError("secret detail")


class FailingNoIdPage(LazyPage):
    template_name = "lazy_4167_failing_noid.html"
    n = state(0, persist="server")


class LegacyLazyChild(LiveView):
    exposure_policy = "legacy"  # legacy subject: #4167 a legacy lazy child routes over the socket
    template = '<div>Legacy={{ pokes }}<button dj-click="poke">x</button></div>'

    def mount(self, request, **kwargs):
        self.pokes = 0

    @event_handler()
    def poke(self, **kwargs):
        self.pokes += 1


class LegacyLazyPage(LiveView):
    exposure_policy = "legacy"  # legacy subject: #4167 a legacy lazy child routes over the socket
    template_name = "lazy_4167_legacy.html"


@pytest.fixture(autouse=True)
async def close_worker_connections(transactional_db):
    from django.db import connections
    from django.db.backends.base.base import BaseDatabaseWrapper

    def snapshot():
        return {
            id(conn): conn.connection is not None for conn in connections.all(initialized_only=True)
        }

    def close_new_connections(before):
        for conn in connections.all(initialized_only=True):
            if conn.connection is None or before.get(id(conn), False):
                continue
            if conn.vendor == "sqlite" and conn.is_in_memory_db():
                BaseDatabaseWrapper.close(conn)
            else:
                conn.close()

    before = await sync_to_async(snapshot)()
    yield
    await sync_to_async(close_new_connections)(before)


@pytest.fixture(autouse=True)
def staged(monkeypatch, settings, tmp_path):
    monkeypatch.setattr(LiveView, "_validate_exposure_configuration", lambda self: None)
    settings.DJUST_LIVE_RENDER_ALLOWED_MODULES = [MODULE]
    templates = copy.deepcopy(settings.TEMPLATES)
    templates[0]["DIRS"] = [str(tmp_path), *templates[0].get("DIRS", [])]
    settings.TEMPLATES = templates
    LazyChild.mounts = []
    for name, child, view_id in (
        ("lazy_4167_page.html", "LazyChild", "lz"),
        ("lazy_4167_refused.html", "RefusedChild", "lz"),
        ("lazy_4167_legacy.html", "LegacyLazyChild", "ll"),
        ("lazy_4167_failing_noid.html", "FailingChild", None),
    ):
        id_kwarg = f' view_id="{view_id}"' if view_id else ""
        (tmp_path / name).write_text(
            "{% load live_tags %}<html><body><div dj-root>Page={{ n }}"
            f'{{% live_render "{MODULE}.{child}" lazy=True{id_kwarg} %}}'
            "</div></body></html>"
        )


def new_session():
    session = SessionStore()
    session.create()
    return session.session_key


def page_get(view_cls, session_key):
    request = RequestFactory().get(PATH)
    request.session = SessionStore(session_key)
    request.user = AnonymousUser()
    response = view_cls.as_view()(request)
    if hasattr(response, "render"):
        response.render()
    return response.content.decode()


async def connect(session_key, view, *, prerendered=True):
    from channels.testing import WebsocketCommunicator

    from djust.websocket import LiveViewConsumer

    comm = WebsocketCommunicator(LiveViewConsumer.as_asgi(), "/ws/")
    comm.scope["session"] = SessionStore(session_key)
    comm.scope["user"] = AnonymousUser()
    connected, _ = await comm.connect()
    assert connected
    await comm.receive_json_from()
    await comm.send_json_to(
        {
            "type": "mount",
            "view": f"{MODULE}.{view}",
            "url": PATH,
            "has_prerendered": prerendered,
        }
    )
    frame = await comm.receive_json_from()
    assert frame["type"] == "mount", frame
    return comm, frame


async def event_reply(comm, event, **params):
    await comm.send_json_to({"type": "event", "event": event, "params": params})
    while True:
        frame = await comm.receive_json_from()
        if frame.get("type") in {"embedded_update", "error", "patch", "html_update", "noop"}:
            return frame


async def test_lazy_explicit_child_routes_over_websocket_after_page_get():
    key = await sync_to_async(new_session)()
    with override_settings(**WS_SETTINGS):
        html = await sync_to_async(page_get)(LazyPage, key)
        # The page GET still defers the child and fills the slot.
        assert "<dj-lazy-slot" in html and 'data-status="ok"' in html, html
        comm, _ = await connect(key, "LazyPage")
        try:
            mounts = len(LazyChild.mounts)
            reply = await event_reply(comm, "increment", view_id="lz")
            assert reply["type"] == "embedded_update", reply
            assert "Lazy=2" in reply["html"]
            reply = await event_reply(comm, "bump")
            assert reply["type"] in {"patch", "html_update"}, reply
            reply = await event_reply(comm, "increment", view_id="lz")
            assert reply["type"] == "embedded_update", reply
            # The live child is reused across the parent's renders.
            assert "Lazy=3" in reply["html"]
            assert len(LazyChild.mounts) == mounts
        finally:
            await comm.disconnect()


async def test_lazy_legacy_child_routes_over_websocket_after_page_get():
    key = await sync_to_async(new_session)()
    with override_settings(**WS_SETTINGS):
        await sync_to_async(page_get)(LegacyLazyPage, key)
        comm, _ = await connect(key, "LegacyLazyPage")
        try:
            reply = await event_reply(comm, "poke", view_id="ll")
            assert reply["type"] == "embedded_update", reply
            assert "Legacy=1" in reply["html"]
        finally:
            await comm.disconnect()


async def test_lazy_child_refused_at_socket_mount_shows_failure_not_a_route():
    from django.utils.html import escape

    # The HTTP fill's text for a denial, on both transports (#4167 review N2).
    # The socket frame's HTML is re-serialized, so its quotes are not entities.
    denied = "child %r denied access" % f"{MODULE}.RefusedChild"
    key = await sync_to_async(new_session)()
    with override_settings(**WS_SETTINGS):
        html = await sync_to_async(page_get)(RefusedPage, key)
        assert escape(denied) in html, html
        comm, frame = await connect(key, "RefusedPage", prerendered=False)
        try:
            assert denied in frame["html"], frame["html"]
            assert "Traceback" not in frame["html"] and "login" not in frame["html"].lower()
            reply = await event_reply(comm, "increment", view_id="lz")
            assert reply["type"] == "error" and reply["error"] == "Embedded view not found"
        finally:
            await comm.disconnect()


async def test_socket_renders_stash_no_lazy_thunks(monkeypatch):
    from djust.runtime import ViewRuntime

    views = []
    real = ViewRuntime.dispatch_event

    async def spy(self, *args, **kwargs):
        views.append(self.view_instance)
        return await real(self, *args, **kwargs)

    monkeypatch.setattr(ViewRuntime, "dispatch_event", spy)
    key = await sync_to_async(new_session)()
    with override_settings(**WS_SETTINGS):
        comm, _ = await connect(key, "LazyPage", prerendered=False)
        try:
            for _ in range(3):
                reply = await event_reply(comm, "bump")
                assert reply["type"] in {"patch", "html_update"}, reply
        finally:
            await comm.disconnect()
    assert views
    assert not getattr(views[-1], "_lazy_thunks", None), views[-1]._lazy_thunks


async def test_socket_mount_without_prerender_sends_the_child_not_a_slot():
    key = await sync_to_async(new_session)()
    with override_settings(**WS_SETTINGS):
        comm, frame = await connect(key, "LazyPage", prerendered=False)
        await comm.disconnect()
    assert "Lazy=1" in frame["html"], frame["html"]
    assert "<dj-lazy-slot" not in json.dumps(frame)


def test_renders_lazy_inline_walks_to_the_root():
    from djust.templatetags.live_tags import _renders_lazy_inline

    class Node:
        def __init__(self, parent=None, live=False):
            self._parent_view = parent
            if live:
                self.__dict__["_djust_live_runtime"] = True

    root = Node(live=True)
    assert _renders_lazy_inline(Node(Node(root)))
    assert not _renders_lazy_inline(Node(Node(Node())))


async def test_failed_lazy_child_without_view_id_names_no_empty_id():
    """#4167 review N1: a child with no ``view_id=`` that fails on the socket
    reads "Lazy child failed.", not an empty id; no exception text."""
    key = await sync_to_async(new_session)()
    with override_settings(**WS_SETTINGS):
        comm, frame = await connect(key, "FailingNoIdPage", prerendered=False)
        await comm.disconnect()
    assert "Lazy child failed." in frame["html"], frame["html"]
    assert "Lazy child  failed" not in frame["html"]
    assert "secret detail" not in frame["html"] and "RuntimeError" not in frame["html"]


def test_module_does_not_mark_its_sync_test_asyncio():
    """#4167 review N3: no module-wide asyncio mark (it warns on a sync test)."""
    assert all(mark.name != "asyncio" for mark in pytestmark)
