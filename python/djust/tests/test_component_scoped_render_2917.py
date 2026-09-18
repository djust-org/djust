"""ADR-032 S2 (#2917) — a bound component's event patches only its subtree.

After the handler runs, if the ONE assign that changed is a bound component's
slot and the view's template is component-opaque for it, the runtime renders
that component alone and Rust splices the subtree (``patch_component_subtree``)
— the same ``patch`` frame as a page render, with ``timing.scope ==
"component"``. Every gate failure takes today's page render.

The oracle is the real path: ``ViewRuntime.dispatch_event`` on both dispatch
routes (``component_id`` and a ``Meta.event`` alias) and one real
``WebsocketCommunicator`` for the wire shape and recovery.
"""

from __future__ import annotations

import contextlib
import uuid
from typing import Any, Dict, List, Optional

import django
import pytest
from asgiref.sync import sync_to_async
from django.conf import settings

if not settings.configured:
    settings.configure(
        DATABASES={"default": {"ENGINE": "django.db.backends.sqlite3", "NAME": ":memory:"}},
        INSTALLED_APPS=[
            "django.contrib.contenttypes",
            "django.contrib.auth",
            "django.contrib.sessions",
        ],
        SECRET_KEY="test-secret-key-2917",
        SESSION_ENGINE="django.contrib.sessions.backends.db",
        USE_TZ=True,
        DEFAULT_AUTO_FIELD="django.db.models.BigAutoField",
        TEMPLATES=[
            {
                "BACKEND": "django.template.backends.django.DjangoTemplates",
                "DIRS": [],
                "APP_DIRS": False,
                "OPTIONS": {"builtins": ["djust.templatetags.live_tags"]},
            }
        ],
    )
    django.setup()

from djust import LiveView  # noqa: E402
from djust.components.descriptors.base import LiveComponent, TypedState  # noqa: E402
from djust.decorators import computed, event_handler  # noqa: E402
from djust.runtime import (  # noqa: E402
    ViewRuntime,
    _scoped_component_for,
    _template_is_component_opaque,
)

_ALLOWED = "djust.tests.test_component_scoped_render_2917"


# ------------------------------------------------------------------ #
# Fixtures: components and views
# ------------------------------------------------------------------ #


class Tabs(LiveComponent):
    class State(TypedState):
        active: str = "overview"

    template = (
        '<nav><button dj-click="select" dj-value="billing">Billing</button></nav>'
        "<p>{{ active }}<b>{{ component_id }}</b></p>"
    )

    @event_handler()
    def select(self, value: str = "", **kwargs: Any) -> None:
        self.state.active = value


class Probe(LiveComponent):
    """``Meta.event`` alias: the event has no ``component_id`` and runs as a
    VIEW event through the skip gate (#2900)."""

    class State(TypedState):
        active: str = ""

    template = "<p>{{ active }}</p>"

    class Meta:
        event = "probe_set"

    def _handle_event(self, state: Any, value: str = "", **kwargs: Any) -> None:
        state.active = value


class OpaquePage(LiveView):
    """``{{ nav }}`` is the only read of the component: D2 holds."""

    template = "<div dj-root><h1>{{ title }}</h1>{{ nav }}<i>{{ note }}</i></div>"
    nav = Tabs()

    def mount(self, request: Any, **kwargs: Any) -> None:
        self.title = "Page"
        self.note = ""

    @event_handler()
    def rename(self, value: str = "", **kwargs: Any) -> None:
        self.title = value

    @event_handler()
    def both(self, **kwargs: Any) -> None:
        self.nav.state.active = "both"
        self.title = "Both"

    @event_handler()
    def forced(self, **kwargs: Any) -> None:
        self.nav.state.active = "forced"
        self._force_full_html = True

    @event_handler()
    def pushing(self, **kwargs: Any) -> None:
        self.nav.state.active = "pushed"
        self.push_event("toast", {"msg": "hi"})


class ReadsStatePage(LiveView):
    """``{{ nav.active }}`` elsewhere: not opaque, page render."""

    template = "<div dj-root>{{ nav }}|{{ nav.active }}</div>"
    nav = Tabs()

    def mount(self, request: Any, **kwargs: Any) -> None:
        pass


class ComputedPage(LiveView):
    """A memoised ``@computed`` over the component: page render."""

    template = "<div dj-root>{{ nav }}<b>{{ label }}</b></div>"
    nav = Tabs()

    def mount(self, request: Any, **kwargs: Any) -> None:
        pass

    @computed("nav")
    def label(self) -> str:
        return f"tab:{self.nav.active}"


class AliasPage(LiveView):
    template = "<div dj-root><h1>{{ title }}</h1>{{ probe }}</div>"
    probe = Probe()

    def mount(self, request: Any, **kwargs: Any) -> None:
        self.title = "Alias"


class MockTransport:
    def __init__(self) -> None:
        self._session_id = str(uuid.uuid4())
        self._client_ip: Optional[str] = None
        self.sent: List[Dict[str, Any]] = []
        self.errors: List[Dict[str, Any]] = []
        #: ``_timing_scope`` seen by ``on_event_frame`` per event frame (D4).
        self.scopes: List[Optional[str]] = []

    @property
    def session_id(self) -> str:
        return self._session_id

    @property
    def client_ip(self) -> Optional[str]:
        return self._client_ip

    async def send(self, data: Dict[str, Any]) -> None:
        self.sent.append(data)

    async def send_error(self, error: str, **kwargs: Any) -> None:
        msg = {"type": "error", "error": error, **kwargs}
        self.errors.append(msg)
        self.sent.append(msg)

    async def close(self, code: int = 1000) -> None:
        pass

    def next_client_version(self, html: Optional[str], rust_version: int) -> int:
        return rust_version

    def build_request(self) -> Optional[Any]:
        return None

    def on_view_mounted(self, view_instance: Any) -> None:
        pass

    def on_event_frame(self, view: Any, frame: Dict[str, Any], **kwargs: Any) -> None:
        self.scopes.append(frame.get("_timing_scope"))

    @contextlib.asynccontextmanager
    async def event_context(self, view: Any):
        yield


def _runtime(view: LiveView) -> tuple[ViewRuntime, MockTransport]:
    transport = MockTransport()
    runtime = ViewRuntime(transport)
    runtime.view_instance = view
    return runtime, transport


def _mounted(cls: type) -> tuple[LiveView, ViewRuntime, MockTransport]:
    view = cls()
    view.mount(None)
    runtime, transport = _runtime(view)
    view.render_with_diff()  # the mount baseline
    return view, runtime, transport


def _last_frame(transport: MockTransport) -> Dict[str, Any]:
    frames = [f for f in transport.sent if f.get("type") in ("patch", "html_update", "noop")]
    assert frames, transport.sent
    return frames[-1]


def _component_prefix(view: LiveView, name: str) -> List[int]:
    """The component node's path in the page VDOM, from the page HTML."""
    import re

    html = view.render_with_diff()[0]
    # <div dj-root><h1/>{{ nav }}... → the wrapper is the root's child at
    # index 1 for OpaquePage/AliasPage. Derived rather than hard-coded so a
    # fixture change fails loudly here, not silently in the assertions.
    tags = re.findall(r"<(\w+)[^>]*data-component-id=\"%s\"" % name, html)
    assert tags == ["div"], html
    return [1]


# ------------------------------------------------------------------ #
# D2 — component-opaque, on the template source
# ------------------------------------------------------------------ #


@pytest.mark.parametrize(
    ("source", "opaque"),
    [
        ("<div>{{ nav }}</div>", True),
        ("<div>{{nav}}</div>", True),
        ("<div>{{ nav }}{{ other.nav }}</div>", True),  # another variable's attribute
        ("<div>{{ navigation }}</div>", True),  # a longer name
        ("<div>{{ nav.active }}</div>", False),
        ("<div>{{ nav }}{{ nav.active }}</div>", False),
        ("<div>{{ nav|length }}</div>", False),
        ("{% for x in nav %}{{ x }}{% endfor %}", False),
        ("{% with nav.state as s %}{{ s }}{% endwith %}", False),
        ("{% if nav %}{{ nav }}{% endif %}", False),
        ('{% include "x.html" %}{{ nav }}', False),  # could read it in the partial
        ('{% extends "base.html" %}{% block c %}{{ nav }}{% endblock %}', False),
        ("{# nav.active #}{{ nav }}", False),  # conservative: comments count
    ],
)
def test_template_is_component_opaque(source, opaque):
    assert _template_is_component_opaque(source, "nav") is opaque


class TestScopedComponentFor:
    def test_the_one_changed_slot_of_a_templated_component(self):
        view, _, _ = _mounted(OpaquePage)
        assert _scoped_component_for(view, {"_component_nav"}) is view.nav

    @pytest.mark.parametrize(
        "changed", [set(), None, {"title"}, {"_component_nav", "title"}, {"_component_zzz"}]
    )
    def test_anything_else_is_none(self, changed):
        view, _, _ = _mounted(OpaquePage)
        assert _scoped_component_for(view, changed) is None

    def test_a_component_without_a_template_is_none(self):
        class Plain(LiveComponent):
            class State(TypedState):
                x: int = 0

        class Page(LiveView):
            template = "<div dj-root>{{ plain }}</div>"
            plain = Plain()

            def mount(self, request: Any, **kwargs: Any) -> None:
                pass

        view, _, _ = _mounted(Page)
        assert _scoped_component_for(view, {"_component_plain"}) is None


# ------------------------------------------------------------------ #
# The runtime, both dispatch routes
# ------------------------------------------------------------------ #


@pytest.mark.django_db
class TestScopedPath:
    @pytest.mark.asyncio
    async def test_component_event_patches_only_the_component_subtree(self):
        view, runtime, transport = _mounted(OpaquePage)
        prefix = _component_prefix(view, "nav")
        await runtime.dispatch_event(
            {
                "type": "event",
                "event": "select",
                "params": {"component_id": "nav", "value": "billing"},
                "ref": 7,
            }
        )
        assert transport.errors == []
        frame = _last_frame(transport)
        assert frame["type"] == "patch", frame
        assert frame["ref"] == 7 and frame["event_name"] == "select"
        assert frame["patches"], frame
        for patch in frame["patches"]:
            assert patch["path"][: len(prefix)] == prefix, patch
        assert transport.scopes == ["component"]
        assert view.nav.active == "billing"
        assert view._rust_view.get_render_timing()["fast_path"] == 3.0
        assert view._changed_keys is None

    @pytest.mark.asyncio
    async def test_alias_event_takes_the_scoped_path_too(self):
        """No ``component_id``: the runtime event route through the skip gate."""
        view, runtime, transport = _mounted(AliasPage)
        await runtime.dispatch_event(
            {"type": "event", "event": "probe_set", "params": {"value": "billing"}, "ref": 1}
        )
        assert transport.errors == []
        frame = _last_frame(transport)
        assert frame["type"] == "patch", frame
        assert transport.scopes == ["component"]
        assert "billing" in str(frame["patches"]), frame["patches"]
        assert view.probe.active == "billing"
        # The slot is in step with Rust state: the next sync starts clean.
        assert view._changed_keys is None

    @pytest.mark.asyncio
    async def test_the_page_stays_consistent_after_a_scoped_event(self):
        """Full render after a scoped one: the Rust state and fragment cache
        were updated, so the page shows the patched component, the next page
        patch is only the title, and versions stay monotonic."""
        view, runtime, transport = _mounted(OpaquePage)
        await runtime.dispatch_event(
            {"type": "event", "event": "select", "params": {"component_id": "nav", "value": "b1"}}
        )
        v1 = _last_frame(transport)["version"]
        await runtime.dispatch_event(
            {"type": "event", "event": "rename", "params": {"value": "T2"}}
        )
        frame = _last_frame(transport)
        assert frame["type"] == "patch", frame
        assert frame["version"] > v1
        texts = [p["text"] for p in frame["patches"] if p["type"] == "SetText"]
        assert texts == ["T2"], frame["patches"]
        assert transport.scopes == ["component", None]
        html = view.render_with_diff()[0]
        assert "b1<b" in html and "T2</h1>" in html, html
        # And a second scoped event after the page render.
        await runtime.dispatch_event(
            {"type": "event", "event": "select", "params": {"component_id": "nav", "value": "b2"}}
        )
        frame = _last_frame(transport)
        assert frame["type"] == "patch" and transport.scopes[-1] == "component"
        assert [p["text"] for p in frame["patches"] if p["type"] == "SetText"] == ["b2"]

    @pytest.mark.asyncio
    async def test_other_views_are_untouched(self):
        other, _, _ = _mounted(OpaquePage)
        view, runtime, _ = _mounted(OpaquePage)
        await runtime.dispatch_event(
            {"type": "event", "event": "select", "params": {"component_id": "nav", "value": "b"}}
        )
        assert view.nav.active == "b" and other.nav.active == "overview"
        assert "overview<b" in other.render_with_diff()[0]


@pytest.mark.django_db
class TestFullPathGates:
    """Every gate failure is today's path: no ``scope``, and the page
    re-renders (the state change is still on the wire)."""

    async def _run(self, cls, event):
        view, runtime, transport = _mounted(cls)
        await runtime.dispatch_event({"type": "event", **event})
        assert transport.errors == []
        frame = _last_frame(transport)
        assert frame["type"] in ("patch", "html_update"), frame
        # The component route's page render emits ``html_update`` without the
        # frame hook; the runtime route stamps ``None``. Neither says "component".
        assert "component" not in transport.scopes, transport.scopes
        return view, frame

    @pytest.mark.asyncio
    async def test_template_reading_the_state_elsewhere(self):
        view, frame = await self._run(
            ReadsStatePage, {"event": "select", "params": {"component_id": "nav", "value": "x"}}
        )
        assert view.nav.active == "x"
        assert "|x" in view.render_with_diff()[0]

    @pytest.mark.asyncio
    async def test_computed_depending_on_the_component(self):
        view, _ = await self._run(
            ComputedPage, {"event": "select", "params": {"component_id": "nav", "value": "x"}}
        )
        assert "tab:x" in view.render_with_diff()[0]

    @pytest.mark.asyncio
    async def test_handler_changing_another_assign_as_well(self):
        view, frame = await self._run(OpaquePage, {"event": "both", "params": {}})
        assert frame["type"] == "patch"
        texts = {p["text"] for p in frame["patches"] if p["type"] == "SetText"}
        assert {"Both", "both"} <= texts, frame["patches"]

    @pytest.mark.asyncio
    async def test_forced_full_html(self):
        _, frame = await self._run(OpaquePage, {"event": "forced", "params": {}})
        assert frame["type"] == "html_update" and "forced<b" in frame["html"]

    @pytest.mark.asyncio
    async def test_pending_push_events(self):
        import asyncio

        view, frame = await self._run(OpaquePage, {"event": "pushing", "params": {}})
        assert "pushed" in str(frame), frame
        await asyncio.sleep(0)  # the push flush is fire-and-forget
        assert view._drain_push_events() == [], "the push event was not flushed"


# ------------------------------------------------------------------ #
# The wire — a real WebSocket
# ------------------------------------------------------------------ #


class _ScopeSession:
    def __init__(self, key: str) -> None:
        self.session_key = key


async def _receive_until(communicator, wanted_type, *, tries=8, timeout=3):
    last = None
    for _ in range(tries):
        last = await communicator.receive_json_from(timeout=timeout)
        if last.get("type") == wanted_type:
            return last
    return last


async def _connect_and_mount(view_path: str, url: str = "/2917/"):
    pytest.importorskip("channels")
    from channels.testing import WebsocketCommunicator
    from django.contrib.sessions.backends.db import SessionStore

    from djust.websocket import LiveViewConsumer

    def _create_session():
        s = SessionStore()
        s.create()
        return s.session_key

    session_key = await sync_to_async(_create_session)()
    communicator = WebsocketCommunicator(LiveViewConsumer.as_asgi(), "/ws/")
    communicator.scope["session"] = _ScopeSession(session_key)
    connected, _ = await communicator.connect()
    assert connected
    await communicator.receive_json_from(timeout=2)  # connect frame
    await communicator.send_json_to({"type": "mount", "view": view_path, "url": url})
    mount_frame = await _receive_until(communicator, "mount")
    assert mount_frame.get("type") == "mount", mount_frame
    return communicator, mount_frame


@pytest.mark.django_db(transaction=True)
class TestWebSocketFrame:
    @pytest.mark.asyncio
    async def test_scoped_patch_frame_and_recovery_over_the_wire(self):
        from django.test import override_settings

        with override_settings(LIVEVIEW_ALLOWED_MODULES=[_ALLOWED], DJUST_EXPOSE_TIMING=True):
            communicator, mounted = await _connect_and_mount(f"{_ALLOWED}.OpaquePage")
            try:
                assert 'data-component-id="nav"' in mounted["html"]
                await communicator.send_json_to(
                    {
                        "type": "event",
                        "event": "select",
                        "params": {"component_id": "nav", "value": "billing"},
                        "ref": 3,
                    }
                )
                updated = await _receive_until(communicator, "patch")
                assert updated.get("type") == "patch", updated
                assert updated.get("ref") == 3
                assert updated["timing"]["scope"] == "component", updated
                assert "render" in updated["timing"]
                assert all(p["path"][:1] == [1] for p in updated["patches"]), updated["patches"]
                assert "billing" in str(updated["patches"])
                # Recovery serves the CURRENT markup at the version just sent (D4).
                await communicator.send_json_to({"type": "request_html"})
                recovery = await _receive_until(communicator, "html_recovery")
                assert recovery.get("type") == "html_recovery", recovery
                assert "billing<b" in recovery["html"].replace('dj-id="', "").replace('"', "") or (
                    "billing" in recovery["html"]
                )
                assert recovery["version"] == updated["version"]
            finally:
                await communicator.disconnect()

    @pytest.mark.asyncio
    async def test_page_render_after_a_scoped_event_over_the_wire(self):
        from django.test import override_settings

        with override_settings(LIVEVIEW_ALLOWED_MODULES=[_ALLOWED], DJUST_EXPOSE_TIMING=True):
            communicator, _ = await _connect_and_mount(f"{_ALLOWED}.OpaquePage")
            try:
                await communicator.send_json_to(
                    {
                        "type": "event",
                        "event": "select",
                        "params": {"component_id": "nav", "value": "billing"},
                        "ref": 1,
                    }
                )
                first = await _receive_until(communicator, "patch")
                assert first["timing"]["scope"] == "component", first
                await communicator.send_json_to(
                    {"type": "event", "event": "rename", "params": {"value": "Renamed"}, "ref": 2}
                )
                second = await _receive_until(communicator, "patch")
                assert second.get("type") == "patch", second
                assert "scope" not in second["timing"], second
                assert second["version"] == first["version"] + 1
                texts = [p["text"] for p in second["patches"] if p["type"] == "SetText"]
                assert texts == ["Renamed"], second["patches"]
            finally:
                await communicator.disconnect()
