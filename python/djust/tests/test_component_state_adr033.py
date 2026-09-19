"""ADR-033 S1 — a plain component is state a handler writes to.

Before this change ``self.rating.value = 5`` in a handler rendered four stars
forever: ``Rating`` is a plain ``Component``, every snapshot compared it by
``id()``, the write changed nothing the runtime looked at, and the frame was
``noop`` (ADR-033 M1–M3). Now the constructor kwargs are the component's
``state``, public attribute writes go through to it (D2), the ONE
change-detection rule (#2900, ``fingerprints_by_content``) walks it (D1), and
a component may narrow the walk with ``fingerprint_fields`` (D3).
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
        SECRET_KEY="test-secret-key-adr033",
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

from djust import Component, LiveView  # noqa: E402
from djust.change_detection import (  # noqa: E402
    DEFAULT_BUDGET,
    deep_fingerprint,
    fingerprints_by_content,
)
from djust.components.components.rating import Rating  # noqa: E402
from djust.components.descriptors.base import LiveComponent, TypedState  # noqa: E402
from djust.decorators import event_handler  # noqa: E402
from djust.runtime import ViewRuntime  # noqa: E402
from djust.websocket import _snapshot_assigns  # noqa: E402

_ALLOWED = "djust.tests.test_component_state_adr033"


# ------------------------------------------------------------------ #
# Fixtures: the ADR's own example, verbatim
# ------------------------------------------------------------------ #


class RatingView(LiveView):
    """The code a Django developer writes (ADR-033, summary)."""

    template = '<div dj-root dj-id="0">{{ rating }}</div>'

    def mount(self, request: Any, **kwargs: Any) -> None:
        self.rating = Rating(value=4, max_stars=5)

    @event_handler()
    def set_rating(self, value: Any, **kwargs: Any) -> None:
        self.rating.value = int(value)


class Toggle(LiveComponent):
    class State(TypedState):
        on: bool = False


class ToggleView(LiveView):
    template = '<div dj-root dj-id="0">{{ toggle.on }}</div>'
    toggle = Toggle()


def _full_stars(html: str) -> int:
    return html.count("rating-star-full")


# ------------------------------------------------------------------ #
# D1 — the one rule now covers plain components
# ------------------------------------------------------------------ #


class TestPlainComponentIsComparedByState:
    def test_m1_predicate_and_walk(self):
        r = Rating(value=4)
        assert fingerprints_by_content(r) is True
        assert r.state["value"] == 4
        assert deep_fingerprint(r)[0] == deep_fingerprint(r.state)[0], "the component IS its state"

    def test_m2_snapshot_assigns_sees_an_attribute_write(self):
        view = RatingView()
        view.mount(None)
        view.get_context_data()
        pre = _snapshot_assigns(view)
        view.rating.value = 2
        assert _snapshot_assigns(view) != pre

    def test_a_rebuilt_component_with_the_same_kwargs_compares_equal(self):
        """``Rating(value=self.value)`` in ``get_context_data`` used to count
        as changed every render because a fresh object has a fresh id()."""
        assert deep_fingerprint(Rating(value=3))[0] == deep_fingerprint(Rating(value=3))[0]
        assert deep_fingerprint(Rating(value=3))[0] != deep_fingerprint(Rating(value=4))[0]

    def test_a_component_inside_a_container_is_walked(self):
        rows = [Rating(value=1), Rating(value=2)]
        before = deep_fingerprint(rows)[0]
        rows[1].value = 5
        assert deep_fingerprint(rows)[0] != before

    def test_opt_out_restores_identity_comparison(self):
        class Frozen(Rating):
            _djust_fingerprint_state = False

        f = Frozen(value=1)
        assert fingerprints_by_content(f) is False
        before = deep_fingerprint(f)[0]
        f.value = 5
        assert deep_fingerprint(f)[0] == before, "opted out: reassign to re-render"

    def test_the_walk_is_budgeted_like_a_container(self):
        class Big(Component):
            template = "<i></i>"

        b = Big(items=list(range(DEFAULT_BUDGET + 10)))
        _, truncated = deep_fingerprint(b)
        assert truncated is True
        assert deep_fingerprint(Rating(value=1))[1] is False


# ------------------------------------------------------------------ #
# D3 — fingerprint_fields narrows the walk
# ------------------------------------------------------------------ #


class Table(Component):
    template = "<table></table>"
    fingerprint_fields = ("columns",)

    def __init__(self, columns=None, rows=None, sort_by="", **kwargs):
        super().__init__(columns=columns or [], rows=rows or [], sort_by=sort_by, **kwargs)
        self.columns = columns or []
        self.rows = rows or []
        self.sort_by = sort_by


class TestFingerprintFields:
    def test_unlisted_container_compares_by_identity(self):
        t = Table(columns=[{"key": "a"}], rows=[{"a": 1}])
        before = deep_fingerprint(t)[0]
        t.rows.append({"a": 2})
        assert deep_fingerprint(t)[0] == before, "an in-place append to unlisted data is a leaf"
        t.rows = list(t.rows)
        assert deep_fingerprint(t)[0] != before, "reassigning it is still seen"

    def test_unlisted_scalar_compares_by_value(self):
        t = Table(sort_by="a")
        before = deep_fingerprint(t)[0]
        t.sort_by = "b"
        assert deep_fingerprint(t)[0] != before
        t.sort_by = "a"
        assert deep_fingerprint(t)[0] == before

    def test_listed_container_is_walked(self):
        t = Table(columns=[{"key": "a"}])
        before = deep_fingerprint(t)[0]
        t.columns[0]["label"] = "A"
        assert deep_fingerprint(t)[0] != before

    def test_narrowing_never_meets_the_budget(self):
        t = Table(rows=[{"a": i} for i in range(DEFAULT_BUDGET + 10)])
        assert deep_fingerprint(t)[1] is False

    @pytest.mark.parametrize(
        "modname, clsname, bulk",
        [
            ("data_table", "DataTable", "rows"),
            ("data_grid", "DataGrid", "rows"),
            ("virtual_list", "VirtualList", "items"),
            ("bar_chart", "BarChart", "data"),
            ("line_chart", "LineChart", "series"),
            ("pie_chart", "PieChart", "segments"),
            ("sparkline", "Sparkline", "data"),
        ],
    )
    def test_shipped_data_components_declare_theirs(self, modname, clsname, bulk):
        import importlib

        cls = getattr(importlib.import_module(f"djust.components.components.{modname}"), clsname)
        assert isinstance(cls.fingerprint_fields, tuple)
        assert bulk not in cls.fingerprint_fields
        assert bulk in cls().state, "the data key is state — reassigning it re-renders"


# ------------------------------------------------------------------ #
# D2 — write-through, one contract on two classes
# ------------------------------------------------------------------ #


def _plain():
    return Rating(value=1), "value"


def _bound():
    view = ToggleView()
    view.mount(None)
    return view.toggle, "on"


@pytest.mark.parametrize("make", [_plain, _bound], ids=["Component", "BoundComponent"])
class TestWriteThrough:
    def test_public_attribute_write_lands_in_state(self, make):
        comp, key = make()
        setattr(comp, key, 3)
        assert comp.state[key] == 3
        assert getattr(comp, key) == 3

    def test_private_attribute_stays_off_the_state(self, make):
        comp, _ = make()
        comp._scratch = "x"
        assert "_scratch" not in comp.state

    def test_state_change_changes_the_fingerprint(self, make):
        comp, key = make()
        before = deep_fingerprint(comp)[0]
        setattr(comp, key, 3)
        assert deep_fingerprint(comp)[0] != before


class TestPlainComponentWriteThroughEdges:
    def test_a_computed_public_attribute_is_not_state(self):
        class Stars(Component):
            template = "<i></i>"

            def __init__(self, value=0, **kwargs):
                super().__init__(value=value, **kwargs)
                self.value = value
                self.glyphs = "*" * value  # derived, never a kwarg

        s = Stars(value=2)
        assert "glyphs" not in s.state
        s.glyphs = "***"
        assert "glyphs" not in s.state

    def test_a_declared_field_that_is_not_a_kwarg_is_state(self):
        class Paged(Component):
            template = "<i></i>"
            fingerprint_fields = ("page",)

        p = Paged()
        p.page = 2
        assert p.state == {"page": 2}

    def test_update_writes_through(self):
        r = Rating(value=1)
        r.update(value=4)
        assert r.state["value"] == 4
        assert _full_stars(r.render()) == 4

    def test_render_and_state_agree(self):
        r = Rating(value=1)
        r.value = 3
        assert _full_stars(r.render()) == 3 == r.state["value"]


# ------------------------------------------------------------------ #
# M3 — the frame the runtime emits for the ADR's example
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
    async def test_m3_the_in_place_write_patches(self):
        """ADR-033 M3, flipped: the handler writes ``self.rating.value`` and
        the runtime answers with a render, not ``noop``; a fresh full render
        shows the new value."""
        view = RatingView()
        view.mount(None)
        assert _full_stars(view.render_with_diff()[0]) == 4
        transport = MockTransport()
        runtime = ViewRuntime(transport)
        runtime.view_instance = view
        await runtime.dispatch_event(
            {"type": "event", "event": "set_rating", "params": {"value": "2"}, "ref": 1}
        )
        types = [f.get("type") for f in transport.sent]
        assert "noop" not in types, transport.sent
        assert any(t in ("patch", "html_update") for t in types), transport.sent
        assert _full_stars(str(transport.sent)) == 2 or _full_stars(view.render_with_diff()[0]) == 2


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


async def _connect_and_mount(view_path: str, url: str = "/adr033/"):
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
    async def test_the_natural_code_re_renders_over_the_wire(self):
        from django.test import override_settings

        with override_settings(LIVEVIEW_ALLOWED_MODULES=[_ALLOWED]):
            communicator, mounted = await _connect_and_mount(f"{_ALLOWED}.RatingView")
            try:
                assert _full_stars(mounted["html"]) == 4
                await communicator.send_json_to(
                    {"type": "event", "event": "set_rating", "params": {"value": "2"}, "ref": 1}
                )
                updated = await communicator.receive_json_from(timeout=5)
                assert updated.get("type") in ("patch", "html_update", "html_recovery"), (
                    f"a state write must re-render, not noop: {updated!r}"
                )
                assert "rating-star" in str(updated), updated
            finally:
                await communicator.disconnect()
