"""#2900 — a class-level component's state change must not be auto-skipped.

ADR-031 (#2895) put a ``BoundComponent`` in the view slot that used to hold
the ``State`` dict. Every change-detection snapshot compared that slot by
``id()`` (a wrapper is "not a plain container"), so a handler that mutated the
State through the view-level ``Meta.event`` alias left ``pre == post`` and the
runtime answered ``noop`` — click a tab, get nothing. The component_id path
never showed it because it always emits a full ``html_update``.

The fix is ONE rule, ``change_detection.fingerprints_by_content`` /
``_walk``'s unwrapping, shared by ``_snapshot_assigns``, the dirty baseline,
``@computed`` and ``_sync_state_to_rust``; each is pinned here, and the
headline path is asserted on the FRAME a real ``WebsocketCommunicator``
receives.
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
        SECRET_KEY="test-secret-key-2900",
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
from djust.change_detection import deep_fingerprint, fingerprints_by_content  # noqa: E402
from djust.components.descriptors import Tabs  # noqa: E402
from djust.components.descriptors.base import LiveComponent, TypedState  # noqa: E402
from djust.decorators import computed, event_handler  # noqa: E402
from djust.runtime import ViewRuntime  # noqa: E402
from djust.websocket import _snapshot_assigns  # noqa: E402

_ALLOWED = "djust.tests.test_bound_component_skip_gate_2900"


class Probe(LiveComponent):
    """The issue's shape: ``Meta.event`` alias + ``_handle_event``."""

    class State(TypedState):
        active: str = ""

    class Meta:
        event = "probe_set"

    def _handle_event(self, state: Any, value: str = "", **kwargs: Any) -> None:
        state.active = value


class Switch(LiveComponent):
    """The D4 shape: an ``@event_handler`` on the component."""

    class State(TypedState):
        on: bool = False

    @event_handler()
    def flip(self, **kwargs: Any) -> None:
        self.state.on = not self.state.on


class ProbeView(LiveView):
    template = (
        '<div dj-root dj-id="0"><p>{{ probe.active }}</p><i>{{ switch.on }}</i>'
        "<b>{{ label }}</b></div>"
    )
    probe = Probe()
    switch = Switch()

    def mount(self, request: Any, **kwargs: Any) -> None:
        pass

    @computed("probe")
    def label(self) -> str:
        return f"tab:{self.probe.active}"


# ------------------------------------------------------------------ #
# The rule, pinned at each of its callers
# ------------------------------------------------------------------ #


class TestOneFingerprintRule:
    def test_predicate_and_walk_see_the_state(self):
        view = ProbeView()
        bound = view.probe
        assert fingerprints_by_content(bound) is True
        assert fingerprints_by_content(bound.state) is True
        assert fingerprints_by_content(object()) is False
        before = deep_fingerprint(bound)[0]
        assert before == deep_fingerprint(bound.state)[0], "the wrapper IS its State"
        bound.active = "2"
        assert deep_fingerprint(bound)[0] != before

    def test_snapshot_assigns_changes_when_the_alias_mutates_the_state(self):
        """The issue's minimal repro, run: ``pre == post`` was True."""
        view = ProbeView()
        view.mount(None)
        view.get_context_data()
        pre = _snapshot_assigns(view)
        view.probe_set(value="2")
        assert view.probe.active == "2"
        post = _snapshot_assigns(view)
        assert pre != post, "the runtime's skip gate must see the State change"

    def test_computed_sees_the_state(self):
        """``@computed("probe")`` keys its cache on the dependency's
        fingerprint; a State mutation must invalidate it. (The dirty
        BASELINE skips every ``_``-prefixed slot, so ``_component_probe``
        was never in it — before or after ADR-031; not this issue.)"""
        view = ProbeView()
        view.mount(None)
        view.get_context_data()
        assert view.label == "tab:"
        view.probe.active = "billing"
        assert view.label == "tab:billing", "@computed's dependency key walks the State"

    def test_state_field_named_items_is_walked(self):
        """Review 🟡3: ``items: list`` on a State is a property shadowing
        ``dict.items``; the walk must not call it."""

        class Menu(LiveComponent):
            class State(TypedState):
                items: list = []

        class MenuView(LiveView):
            menu = Menu()

        view = MenuView()
        view.menu.state["items"] = ["a"]
        before = deep_fingerprint(view.menu)[0]
        view.menu.state["items"].append("b")
        assert deep_fingerprint(view.menu)[0] != before
        _snapshot_assigns(view)  # the slot is walked here too; no crash is the pin

    def test_fallback_sync_re_renders_a_nested_mutation(self):
        """Review 🟡1: the ``rust_bridge`` arm decides on the no-``changed_keys``
        fallback sync — an out-of-band mutation (a background task, a server
        push) followed by a direct render. The component's own template must
        show the new state."""

        class Rows(LiveComponent):
            class State(TypedState):
                rows: list = []

            template = "<b>{{ rows|length }}</b>"

        class RowsView(LiveView):
            template = '<div dj-root dj-id="0">{{ grid }}</div>'
            grid = Rows()

            def mount(self, request, **kwargs):
                pass

        view = RowsView()
        view.mount(None)
        view.grid.state["rows"] = []
        html, _p, _v = view.render_with_diff()
        assert ">0</b>" in html
        view.grid.state["rows"].append("x")  # no dirty flag, same wrapper id()
        html, _p, _v = view.render_with_diff()
        assert ">1</b>" in html, html

    def test_instance_component_stays_a_leaf(self):
        """Only the descriptor slot changes meaning; an instance component is
        still compared by id() (a reassignment is seen, an attribute write is
        not — Phoenix's rule, unchanged)."""
        from djust.components.base import LiveComponent as InstanceComponent

        class Widget(InstanceComponent):
            template = "<i>{{ n }}</i>"

            def mount(self, **kwargs):
                self.n = 0

            def get_context_data(self):
                return {"n": self.n}

        w = Widget(component_id="w")
        assert fingerprints_by_content(w) is False
        assert deep_fingerprint(w)[0] == deep_fingerprint(w)[0]


# ------------------------------------------------------------------ #
# The frame the runtime emits
# ------------------------------------------------------------------ #


class MockTransport:
    def __init__(self) -> None:
        self._session_id = str(uuid.uuid4())
        self._client_ip: Optional[str] = None
        self.sent: List[Dict[str, Any]] = []

    @property
    def session_id(self) -> str:
        return self._session_id

    @property
    def client_ip(self) -> Optional[str]:
        return self._client_ip

    async def send(self, data: Dict[str, Any]) -> None:
        self.sent.append(data)

    async def send_error(self, error: str, **kwargs: Any) -> None:
        self.sent.append({"type": "error", "error": error, **kwargs})

    async def close(self, code: int = 1000) -> None:
        pass

    def next_client_version(self, html: Optional[str], rust_version: int) -> int:
        return rust_version

    def build_request(self) -> Optional[Any]:
        return None

    def on_view_mounted(self, view_instance: Any) -> None:
        pass

    @contextlib.asynccontextmanager
    async def event_context(self, view: Any):
        yield


@pytest.mark.django_db
class TestRuntimeFrame:
    @pytest.mark.asyncio
    async def test_view_level_alias_event_is_not_a_noop(self):
        """No ``component_id``: the alias runs as a VIEW event through the
        skip gate. Before #2900 the frame was ``noop``."""
        view = ProbeView()
        view.mount(None)
        view.render_with_diff()
        transport = MockTransport()
        runtime = ViewRuntime(transport)
        runtime.view_instance = view
        await runtime.dispatch_event(
            {"type": "event", "event": "probe_set", "params": {"value": "billing"}, "ref": 1}
        )
        types = [f.get("type") for f in transport.sent]
        assert "noop" not in types, transport.sent
        assert any(t in ("patch", "html_update") for t in types), transport.sent
        assert view.probe.active == "billing"


# ------------------------------------------------------------------ #
# The headline path — a real WebSocket, the frame the client receives
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


async def _connect_and_mount(view_path: str, url: str = "/2900/"):
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
    async def test_descriptor_alias_event_re_renders_over_the_wire(self):
        from django.test import override_settings

        with override_settings(LIVEVIEW_ALLOWED_MODULES=[_ALLOWED]):
            communicator, mounted = await _connect_and_mount(f"{_ALLOWED}.ProbeView")
            try:
                assert "<p" in mounted["html"]
                await communicator.send_json_to(
                    {
                        "type": "event",
                        "event": "probe_set",
                        "params": {"value": "billing"},
                        "ref": 1,
                    }
                )
                updated = await communicator.receive_json_from(timeout=5)
                assert updated.get("type") != "error", updated
                assert updated.get("type") in ("patch", "html_update", "html_recovery"), (
                    f"a state change must re-render, not noop: {updated!r}"
                )
                blob = str(updated)
                assert "billing" in blob, updated
                assert "tab:billing" in blob, "the @computed over the State re-rendered too"
            finally:
                await communicator.disconnect()

    @pytest.mark.asyncio
    async def test_component_handler_event_re_renders_over_the_wire(self):
        """The D4 path over the wire, with ``component_id`` — always a full
        ``html_update``; pinned so the two paths are read side by side."""
        from django.test import override_settings

        with override_settings(LIVEVIEW_ALLOWED_MODULES=[_ALLOWED]):
            communicator, _ = await _connect_and_mount(f"{_ALLOWED}.ProbeView")
            try:
                await communicator.send_json_to(
                    {
                        "type": "event",
                        "event": "flip",
                        "params": {"component_id": "switch"},
                        "ref": 2,
                    }
                )
                updated = await communicator.receive_json_from(timeout=5)
                assert updated.get("type") in ("patch", "html_update"), updated
                assert "True" in str(updated), updated
            finally:
                await communicator.disconnect()


def test_shipped_descriptor_alias_changes_the_snapshot():
    """The eight shipped descriptors ride the same alias; one of them, run."""

    class TabsView(LiveView):
        tabs = Tabs(active="one")

    view = TabsView()
    view.tabs  # noqa: B018 — bind
    pre = _snapshot_assigns(view)
    view.set_tab(value="two", component_id="tabs")
    assert view.tabs.active == "two"
    assert _snapshot_assigns(view) != pre
