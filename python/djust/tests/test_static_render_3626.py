"""``static_render = True``: a view that keeps no diff baseline (#3626).

A page that renders once and does not change (Point Guard's ER diagram: about
200 KB of markup, no handlers of its own) paid for the rendered tree and the
renderer's context copy in every stored snapshot, although nothing ever diffed
against them. These tests measure the stored snapshot through the real HTTP GET
and the real WebSocket consumer, and pin how each later render of such a view
behaves: it is a full render, never a patch, and never wrong.
"""

from __future__ import annotations

import json
import warnings

import pytest
from asgiref.sync import sync_to_async
from django.test import RequestFactory, override_settings
from django.utils.safestring import mark_safe

from djust import LiveView
from djust.decorators import event_handler
from djust.state_backends import get_backend, set_backend
from djust.state_backends.base import DjustPerformanceWarning, describe_snapshot
from djust.state_backends.memory import InMemoryStateBackend

pytestmark = [pytest.mark.django_db]

MODULE = __name__

# About 200 KB of SVG-like markup in 2,000 nodes, built once per process like a
# cached diagram: the size and shape of Point Guard's /admin/data-model.
DIAGRAM = mark_safe(
    "<svg>"
    + "".join(
        '<g id="n%d"><rect x="%d" y="%d" width="120" height="40"></rect>'
        "<text>table_%d column_%d</text></g>" % (i, i % 97, i // 97, i, i)
        for i in range(2000)
    )
    + "</svg>"
)


def _root(name: str, body: str) -> str:
    return f'<div dj-root dj-view="{MODULE}.{name}" dj-id="0">{body}</div>'


_BODY = (
    '<p id="menu">{% if open %}open{% else %}closed{% endif %}</p><div id="d">{{ diagram }}</div>'
)


class Diagram(LiveView):
    """No handlers of its own; ``toggle`` stands in for a page shell's handler
    (an account menu), which an event can still reach."""

    template = _root("Diagram", _BODY)

    def mount(self, request, **kwargs):
        self.open = False

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["diagram"] = DIAGRAM
        return context

    @event_handler()
    def toggle(self, **kwargs):
        self.open = not self.open

    @event_handler()
    def nothing(self, **kwargs):
        pass


class StaticDiagram(Diagram):
    static_render = True
    template = _root("StaticDiagram", _BODY)


class RecordedStaticDiagram(StaticDiagram):
    """Remembers the instances the socket mounted, to read the live view."""

    template = _root("RecordedStaticDiagram", _BODY)
    _mounted: list = []

    def mount(self, request, **kwargs):
        super().mount(request, **kwargs)
        type(self)._mounted.append(self)


class StaticDiagramWithBadge(StaticDiagram):
    template = _root("StaticDiagramWithBadge", '<p id="b">badge-{{ badge }}</p>' + _BODY)

    def mount(self, request, **kwargs):
        super().mount(request, **kwargs)
        self.badge = "none"
        self.start_async(self._load_badge)

    def _load_badge(self):
        self.badge = "3"


class SmallPage(LiveView):
    template = _root("SmallPage", "<p>{{ n }}</p>")

    def mount(self, request, **kwargs):
        self.n = 0

    @event_handler()
    def bump(self, **kwargs):
        self.n += 1


@pytest.fixture
def backend():
    previous = get_backend()
    fresh = InMemoryStateBackend(state_size_warning_kb=100)
    set_backend(fresh)
    yield fresh
    set_backend(previous)


def _stored(backend: InMemoryStateBackend) -> dict:
    """The one entry the backend holds: its bytes, its parts, and the size it recorded."""
    assert len(backend._cache) == 1, list(backend._cache)
    (key, (blob, _ts)) = next(iter(backend._cache.items()))
    parts = describe_snapshot(blob, top=5)
    assert parts is not None
    return {
        "total": len(blob),
        "state": parts["state"],
        "tree": parts["tree"],
        "template": parts["template"],
        "recorded": backend._state_sizes.get(key),
    }


def _get(view_class, session_key=None):
    from djust.tests.test_exposure_runtime import make_request

    request = make_request(session_key)
    request.path = request.path_info = "/diagram/"
    response = view_class.as_view()(request)
    assert response.status_code == 200
    return request, response.content.decode()


def _post(view_class, initial, event, params=None):
    request = RequestFactory().post(
        "/diagram/",
        data=json.dumps(params or {}),
        content_type="application/json",
        HTTP_X_DJUST_EVENT=event,
    )
    request.user, request.session, request.tenant = initial.user, initial.session, None
    response = view_class().post(request)
    assert response.status_code == 200, response.content
    return json.loads(response.content)


# --------------------------------------------------------------------------
# The Rust method
# --------------------------------------------------------------------------


class TestReleaseBaseline:
    def test_the_next_render_is_full_and_keeps_counting(self):
        from djust._rust import RustLiveView

        view = RustLiveView("<div dj-root><p>{{ a }}</p></div>", [])
        view.update_state({"a": "one"})
        view.render_with_diff()
        view.update_state({"a": "two"})
        _html, patches, version = view.render_with_diff()
        assert patches is not None and version == 2
        view.release_baseline()
        view.update_state({"a": "three"})
        html, patches, version = view.render_with_diff()
        assert patches is None  # nothing to diff against: full HTML
        assert "three" in html
        assert version == 3  # ``reset()`` would have restarted at 1

    def test_it_drops_the_tree_from_the_snapshot(self):
        from djust._rust import RustLiveView

        view = RustLiveView("<div dj-root>{{ a }}</div>", [])
        view.update_state({"a": "x" * 20_000})
        view.render_with_diff()
        before = describe_snapshot(view.serialize_msgpack())
        view.release_baseline()
        view.retain_state_keys([])
        after = describe_snapshot(view.serialize_msgpack())
        assert before["tree"] > 20_000 and before["state"] > 20_000
        assert after["tree"] <= 1 and after["state"] <= 1


# --------------------------------------------------------------------------
# What is stored, after the GET
# --------------------------------------------------------------------------


class TestTheStoredSnapshot:
    def test_a_static_view_stores_the_template_and_the_version_only(self, backend):
        _get(StaticDiagram)
        static = _stored(backend)
        backend.delete_all()
        _get(Diagram)
        normal = _stored(backend)
        # Measured: normal 474,054 bytes (state 204,004, tree 269,862; the same
        # on main, where nothing here changes a normal view), static 196 bytes.
        print("\nstored after GET: normal=%(total)d (state %(state)d, tree %(tree)d)" % normal)
        print("stored after GET: static=%(total)d (state %(state)d, tree %(tree)d)" % static)
        assert normal["total"] > 350_000
        assert normal["tree"] > 150_000 and normal["state"] > 150_000
        assert static["tree"] <= 1 and static["state"] <= 1
        assert static["total"] <= static["template"] + 64
        assert static["total"] < 1_000

    def test_the_size_warning_measures_what_is_stored(self, backend):
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            _get(StaticDiagram)
        static = _stored(backend)
        assert static["recorded"] == static["total"]
        assert not [w for w in caught if issubclass(w.category, DjustPerformanceWarning)]

        backend.delete_all()
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            _get(Diagram)
        normal = _stored(backend)
        assert normal["recorded"] == normal["total"]
        fired = [w for w in caught if issubclass(w.category, DjustPerformanceWarning)]
        assert fired
        # The advice names the remedy measured above.
        assert "static_render = True: it stores neither part" in str(fired[0].message)

    def test_the_page_is_the_same(self, backend):
        _request, static_html = _get(StaticDiagram)
        _request, normal_html = _get(Diagram)
        for html in (static_html, normal_html):
            assert DIAGRAM[:200] in html and "table_1999" in html and ">closed<" in html

    def test_a_normal_view_is_unchanged(self, backend):
        assert Diagram.static_render is False
        _get(SmallPage)
        stored = _stored(backend)
        assert stored["tree"] > 1 and stored["state"] > 1


# --------------------------------------------------------------------------
# The HTTP fallback
# --------------------------------------------------------------------------


class TestTheHttpFallback:
    @pytest.mark.parametrize("in_session", [True, False])
    def test_an_event_answers_the_whole_page_with_consecutive_versions(self, backend, in_session):
        with override_settings(LIVEVIEW_CONFIG={"view_state_in_session": in_session}):
            initial, _html = _get(StaticDiagram)
            first = _post(StaticDiagram, initial, "toggle")
            second = _post(StaticDiagram, initial, "nothing")
            third = _post(StaticDiagram, initial, "toggle")
        for frame in (first, second, third):
            assert "patches" not in frame, frame.keys()
            assert "table_1999" in frame["html"]
        assert first["version"] + 1 == second["version"]
        assert second["version"] + 1 == third["version"]
        if in_session:
            # The state the session holds carries the toggles across requests.
            assert ">open<" in first["html"] and ">open<" not in third["html"]
        assert _stored(backend)["total"] < 1_000

    def test_a_normal_view_still_patches(self, backend):
        initial, _html = _get(Diagram)
        frame = _post(Diagram, initial, "toggle")
        assert frame["patches"] and "html" not in frame


# --------------------------------------------------------------------------
# The WebSocket: mount, events, navigation, reconnect
# --------------------------------------------------------------------------


class _Socket:
    def __init__(self, session_key):
        self.session_key = session_key
        self.communicator = None

    async def __aenter__(self):
        from channels.testing import WebsocketCommunicator

        from djust.websocket import LiveViewConsumer

        key = self.session_key

        class Session:
            session_key = key

        self.communicator = WebsocketCommunicator(
            LiveViewConsumer.as_asgi(),
            "/ws/",
            headers=[(b"host", b"testserver"), (b"origin", b"http://testserver")],
        )
        self.communicator.scope["session"] = Session()
        connected, _ = await self.communicator.connect()
        assert connected
        await self.communicator.receive_json_from(timeout=3)
        return self

    async def __aexit__(self, *exc):
        await self.communicator.disconnect()

    async def until(self, *types):
        for _ in range(12):
            frame = await self.communicator.receive_json_from(timeout=5)
            if frame.get("type") in types:
                return frame
        raise AssertionError("no %s frame" % (types,))

    async def mount(self, view_class, url="/diagram/"):
        await self.communicator.send_json_to(
            {"type": "mount", "view": f"{MODULE}.{view_class.__name__}", "url": url}
        )
        return await self.until("mount")

    async def event(self, name, ref, **params):
        await self.communicator.send_json_to(
            {"type": "event", "event": name, "params": params, "ref": ref}
        )
        return await self.until("patch", "html_update", "noop", "embedded_update", "error")


async def _session_key():
    from django.contrib.sessions.backends.db import SessionStore

    def make():
        session = SessionStore()
        session.create()
        return session.session_key

    return await sync_to_async(make)()


@pytest.mark.asyncio
class TestTheWebSocket:
    async def test_an_event_sends_the_whole_page_and_nothing_is_kept(self, backend):
        key = await _session_key()
        with override_settings(LIVEVIEW_ALLOWED_MODULES=[MODULE]):
            async with _Socket(key) as socket:
                RecordedStaticDiagram._mounted.clear()
                mounted = await socket.mount(RecordedStaticDiagram)
                assert "table_1999" in mounted["html"]
                first = await socket.event("toggle", 1)
                second = await socket.event("toggle", 2)
                quiet = await socket.event("nothing", 3)
                # The live view in this process holds no tree and no context either.
                (live,) = RecordedStaticDiagram._mounted
                held = describe_snapshot(live._rust_view.serialize_msgpack())
        assert first["type"] == second["type"] == "html_update"
        assert ">open<" in first["html"] and "table_1999" in first["html"]
        assert ">closed<" in second["html"]
        assert second["version"] == first["version"] + 1
        assert quiet["type"] == "noop"  # nothing changed: nothing rendered
        assert held["tree"] <= 1 and held["state"] <= 1

    async def test_a_normal_view_patches(self, backend):
        key = await _session_key()
        with override_settings(LIVEVIEW_ALLOWED_MODULES=[MODULE]):
            async with _Socket(key) as socket:
                await socket.mount(Diagram)
                frame = await socket.event("toggle", 1)
        assert frame["type"] == "patch" and frame["patches"]

    async def test_navigating_away_and_back_and_reconnecting_render_the_page(self, backend):
        key = await _session_key()
        with override_settings(LIVEVIEW_ALLOWED_MODULES=[MODULE]):
            async with _Socket(key) as socket:
                first = await socket.mount(StaticDiagram)
                away = await socket.mount(SmallPage, url="/small/")  # dj-navigate
                back = await socket.mount(StaticDiagram)
                after_back = await socket.event("toggle", 1)
            async with _Socket(key) as socket:  # reconnect: same session, new socket
                again = await socket.mount(StaticDiagram)
        for frame in (first, back, again):
            assert "table_1999" in frame["html"] and ">closed<" in frame["html"]
        assert ">0</p>" in away["html"] and "table_1999" not in away["html"]
        assert after_back["type"] == "html_update" and ">open<" in after_back["html"]

    async def test_a_mount_after_the_get_starts_from_the_small_entry(self, backend):
        request, _html = await sync_to_async(_get)(StaticDiagram)
        assert _stored(backend)["total"] < 1_000
        with override_settings(LIVEVIEW_ALLOWED_MODULES=[MODULE]):
            async with _Socket(request.session.session_key) as socket:
                mounted = await socket.mount(StaticDiagram)
                frame = await socket.event("toggle", 1)
        assert "table_1999" in mounted["html"]
        assert frame["type"] == "html_update" and ">open<" in frame["html"]
        for entry in backend._cache.values():
            assert len(entry[0]) < 1_000

    @pytest.mark.django_db(transaction=True)
    async def test_work_started_at_mount_answers_the_whole_page(self, backend):
        """A page shell that loads something after the first render (``start_async``
        in ``mount``, like nav badges) re-renders once more: in full."""
        key = await _session_key()
        with override_settings(LIVEVIEW_ALLOWED_MODULES=[MODULE]):
            async with _Socket(key) as socket:
                mounted = await socket.mount(StaticDiagramWithBadge)
                result = None
                for _ in range(12):
                    frame = await socket.communicator.receive_json_from(timeout=5)
                    if frame.get("source") == "async":
                        result = frame
                        break
        assert ">badge-none<" in mounted["html"]
        assert result is not None and result["type"] == "html_update", result
        assert ">badge-3<" in result["html"] and "table_1999" in result["html"]
        assert result["version"] == mounted["version"] + 1


# --------------------------------------------------------------------------
# Embedded (sticky) children: each view's flag is its own
# --------------------------------------------------------------------------


class Dock(LiveView):
    sticky = True
    sticky_id = "dock"
    template = "<div>{% for n in items %}<span>{{ n }}</span>{% endfor %}</div>"

    def mount(self, request, **kwargs):
        self.items = ["a", "b"]

    @event_handler()
    def drop(self, **kwargs):
        self.items = self.items[1:]

    def get_context_data(self, **kwargs):
        return {"items": self.items, "view": self}


class StaticDock(Dock):
    static_render = True


def _host(name, child):
    return (
        "{% load live_tags %}"
        f'<div dj-root dj-view="{MODULE}.{name}" dj-id="0">'
        '<p id="menu">{% if open %}open{% else %}closed{% endif %}</p>'
        f'{{% live_render "{MODULE}.{child}" sticky=True %}}'
        '<div id="d">{{ diagram }}</div></div>'
    )


class StaticHost(StaticDiagram):
    template = _host("StaticHost", "Dock")

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["view"] = self
        return context


class NormalHostOfStaticDock(Diagram):
    template = _host("NormalHostOfStaticDock", "StaticDock")

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["view"] = self
        return context


@pytest.mark.asyncio
class TestEmbeddedChildren:
    async def test_a_child_with_handlers_inside_a_static_page(self, backend):
        key = await _session_key()
        with override_settings(LIVEVIEW_ALLOWED_MODULES=[MODULE]):
            async with _Socket(key) as socket:
                mounted = await socket.mount(StaticHost)
                child = await socket.event("drop", 1, view_id="dock")
                page = await socket.event("toggle", 2)
        assert ">a</span>" in mounted["html"]
        assert child["type"] == "embedded_update" and child["view_id"] == "dock"
        assert ">a</span>" not in child["html"] and ">b</span>" in child["html"]
        assert page["type"] == "html_update" and ">open<" in page["html"]
        assert "table_1999" in page["html"]

    async def test_a_static_child_inside_a_normal_page(self, backend):
        key = await _session_key()
        with override_settings(LIVEVIEW_ALLOWED_MODULES=[MODULE]):
            async with _Socket(key) as socket:
                await socket.mount(NormalHostOfStaticDock)
                child = await socket.event("drop", 1, view_id="dock")
                page = await socket.event("toggle", 2)
        assert child["type"] == "embedded_update"
        assert ">a</span>" not in child["html"] and ">b</span>" in child["html"]
        assert page["type"] == "patch" and page["patches"]  # the page keeps its baseline
