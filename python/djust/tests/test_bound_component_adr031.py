"""ADR-031 PR 1 — ``BoundComponent``: events and registration for class-level components.

``LiveComponent.__get__`` returns a per-view :class:`BoundComponent` (D1) that
registers in ``view._components`` (D2), forwards attribute access to its
``State`` (D3), dispatches ``@event_handler`` methods with the bound component
as ``self`` (D4) and is saved as its State everywhere state is saved (D7).
Rendering (D5/D6) is PR 2 and is deliberately not covered here beyond the
parity pin that ``{{ nav }}`` / ``{{ nav.active }}`` still render as before.
"""

from __future__ import annotations

import contextlib
import json
import pathlib
import re
import uuid
from typing import Any, Dict, List, Optional

import django
import pytest
from django.conf import settings

if not settings.configured:
    settings.configure(
        DATABASES={"default": {"ENGINE": "django.db.backends.sqlite3", "NAME": ":memory:"}},
        INSTALLED_APPS=[
            "django.contrib.contenttypes",
            "django.contrib.auth",
            "django.contrib.sessions",
        ],
        SECRET_KEY="test-secret-key-adr031",
        SESSION_ENGINE="django.contrib.sessions.backends.cache",
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

from django.contrib.sessions.backends.cache import SessionStore  # noqa: E402
from django.template import Context, Engine  # noqa: E402
from django.test import RequestFactory  # noqa: E402
from django.utils.html import escape  # noqa: E402

from djust import LiveView  # noqa: E402
from djust._rust import render_template  # noqa: E402
from djust.components.base import BoundComponent, SESSION_COMPONENT_TYPES  # noqa: E402
from djust.components.base import LiveComponent as BaseLiveComponent  # noqa: E402
from djust.components.descriptors import (  # noqa: E402
    Accordion,
    Carousel,
    Collapsible,
    Dropdown,
    Modal,
    Sheet,
    Tabs,
    Tooltip,
)
from djust.components.descriptors.base import LiveComponent, TypedState  # noqa: E402
from djust.decorators import event_handler  # noqa: E402
from djust.runtime import ViewRuntime  # noqa: E402
from djust.security.attribute_guard import safe_setattr  # noqa: E402

REPO = pathlib.Path(__file__).resolve().parents[3]


# ------------------------------------------------------------------ #
# Fixtures
# ------------------------------------------------------------------ #


class MockTransport:
    """Records outbound frames (mirrors test_runtime_child_routing_1892)."""

    def __init__(self) -> None:
        self._session_id = str(uuid.uuid4())
        self._client_ip: Optional[str] = None
        self.sent: List[Dict[str, Any]] = []
        self.errors: List[Dict[str, Any]] = []

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

    @contextlib.asynccontextmanager
    async def event_context(self, view: Any):
        yield


class Toggle(LiveComponent):
    """A class-level component with one real handler, one helper and one
    undecorated method that must never be reachable from the client."""

    class State(TypedState):
        active: str = ""
        count: int = 0

    @event_handler()
    def set_active(self, value: str = "", **kwargs: Any) -> None:
        self.state.active = value
        self.state.count = self.state.count + 1

    label = "toggle"

    def shout(self) -> str:
        return self.state.active.upper()

    @property
    def title(self) -> str:
        return f"T:{self.state.active}"

    @staticmethod
    def as_static() -> int:
        return 1

    @classmethod
    def as_class(cls) -> str:
        return cls.__name__

    def not_a_handler(self, **kwargs: Any) -> None:
        self.state.active = "hacked"


class Page(LiveView):
    template = '<div dj-root dj-id="0"><p>{{ nav.active }}</p></div>'

    nav = Toggle(active="a")
    aux = Toggle(active="x")

    def mount(self, request: Any, **kwargs: Any) -> None:
        # Touching the component in mount puts its slot in the view's private
        # state, the path the HTTP round trip persists.
        self.nav.active = self.nav.active


class LegacyNoState(BaseLiveComponent):
    """No ``State`` class: ``__get__`` keeps returning the descriptor itself."""

    template = "<i>legacy</i>"

    def mount(self, **kwargs: Any) -> None:
        pass

    def get_context_data(self) -> Dict[str, Any]:
        return {}


class LegacyPage(LiveView):
    template = '<div dj-root dj-id="0">x</div>'
    widget = LegacyNoState()


def _runtime(view: LiveView) -> tuple[ViewRuntime, MockTransport]:
    transport = MockTransport()
    runtime = ViewRuntime(transport)
    runtime.view_instance = view
    return runtime, transport


def _request(path: str = "/page/"):
    request = RequestFactory().get(path)
    request.session = SessionStore()
    return request


# ------------------------------------------------------------------ #
# D1 / D2 / D3 — bound object, registry, forwarding
# ------------------------------------------------------------------ #


class TestBoundComponentBinding:
    def test_get_returns_one_bound_component_per_view(self):
        v1, v2 = Page(), Page()
        assert isinstance(v1.nav, BoundComponent)
        assert v1.nav is v1.nav, "bound component is cached per view"
        assert v1.nav is not v2.nav
        assert Page.nav is v1.nav._descriptor, "class access still yields the descriptor"
        assert isinstance(v1.nav.state, Toggle.State)
        assert v1.nav.component_id == "nav"
        assert v1.nav.state["component_id"] == "nav"

    def test_per_view_isolation_across_two_views(self):
        v1, v2 = Page(), Page()
        v1.nav.active = "b"
        assert v1.nav.active == "b"
        assert v2.nav.active == "a", "a write on one view must not reach another"
        assert v1.aux.active == "x", "sibling components on the same view are separate"

    def test_registered_in_components_on_creation_and_on_rebuild(self):
        view = Page()
        bound = view.nav
        assert view._components["nav"] is bound
        # Round trip: the slot holds a plain dict after deserialization.
        view.__dict__["_component_nav"] = {"active": "r", "count": 2}
        rebuilt = view.nav
        assert rebuilt is not bound
        assert isinstance(rebuilt, BoundComponent)
        assert isinstance(rebuilt.state, Toggle.State)
        assert rebuilt.active == "r" and rebuilt.count == 2
        assert view._components["nav"] is rebuilt, "the registry follows the rebuild"

    def test_view_side_assignment_replaces_state_in_place(self):
        view = Page()
        bound = view.nav
        view.nav = {"active": "z", "count": 3}
        assert view.nav is bound, "assignment keeps the registered object"
        assert view._components["nav"] is bound
        assert isinstance(bound.state, Toggle.State)
        assert bound.active == "z" and bound.count == 3
        assert bound.state["component_id"] == "nav"

    def test_attribute_forwarding_reads_and_writes_the_state(self):
        view = Page()
        bound = view.nav
        bound.active = "q"
        assert bound.state["active"] == "q"
        assert bound["active"] == "q"
        assert "active" in bound
        assert bound.get("missing", 7) == 7
        assert str(bound) == str(bound.state)
        with pytest.raises(AttributeError):
            bound.no_such_key  # noqa: B018
        with pytest.raises(AttributeError):
            bound.update  # noqa: B018 — framework methods are not forwarded
        assert bound.shout() == "Q", "component methods bind to the bound component"

    def test_dirty_flag_is_the_states_flag(self):
        """``rust_bridge._sync_state_to_rust`` reads ``_dirty`` on each context
        value and clears it with ``object.__setattr__`` — both must reach the
        State, or every event would re-send the component."""
        view = Page()
        bound = view.nav
        object.__setattr__(bound.state, "_dirty", False)
        assert bound._dirty is False
        bound.active = "d"
        assert bound._dirty is True
        object.__setattr__(bound, "_dirty", False)
        assert bound.state._dirty is False
        assert "_dirty" not in bound.__dict__, "the flag lives on the State, not the wrapper"

    def test_class_members_forward_and_framework_methods_do_not(self):
        """Review 🟡3/🟡4: a ``@property``, ``staticmethod``, ``classmethod``,
        plain class attribute and nested class resolve through the wrapper;
        ``mount`` / ``get_context_data`` / ``render`` from the framework bases
        do not."""
        bound = Page().nav
        assert bound.title == "T:a"
        assert bound.label == "toggle"
        assert bound.as_static() == 1
        assert bound.as_class() == "Toggle"
        assert bound.State is Toggle.State
        for name in ("mount", "get_context_data", "update", "trigger_update"):
            with pytest.raises(AttributeError):
                getattr(bound, name)

    def test_underscore_names_are_opaque_on_the_wrapper(self):
        """Review M1/M7: ``_``-names neither forward to the component class
        nor write into the State (a ``_x`` write would reach the session)."""

        class TabsPage(LiveView):
            tabs = Tabs(active="one")

        bound = TabsPage().tabs
        with pytest.raises(AttributeError):
            bound._handle_event  # noqa: B018 — defined on Tabs, not forwarded
        bound._scratch = 1
        assert "_scratch" in bound.__dict__
        assert "_scratch" not in bound.state
        assert dict(bound.state) == {"active": "one", "component_id": "tabs"}

    def test_forwarded_event_handler_is_not_called_by_templates(self):
        """Review 🟡2: ``{{ nav.set_active }}`` must not run the handler."""
        view = Page()
        bound = view.nav
        assert bound.set_active.alters_data is True
        assert render_template("[{{ nav.set_active }}]", {"nav": bound}) == "[]"
        django_out = Engine().from_string("[{{ nav.set_active }}]").render(Context({"nav": bound}))
        assert django_out == "[]"
        assert dict(bound.state) == {"active": "a", "count": 0, "component_id": "nav"}

    def test_descriptor_without_state_class_is_unchanged(self):
        view = LegacyPage()
        assert view.widget is LegacyPage.__dict__["widget"]
        assert not isinstance(view.widget, BoundComponent)


# ------------------------------------------------------------------ #
# D4 — events
# ------------------------------------------------------------------ #


@pytest.mark.django_db
class TestBoundComponentEvents:
    @pytest.mark.asyncio
    async def test_component_id_event_runs_handler_with_self_state(self):
        view = Page()
        view.mount(None)
        view.nav  # noqa: B018 — first access registers the component
        runtime, transport = _runtime(view)

        await runtime.dispatch_event(
            {
                "type": "event",
                "event": "set_active",
                "params": {"component_id": "nav", "value": "b"},
                "ref": 3,
            }
        )

        assert transport.errors == []
        assert view.nav.active == "b"
        assert view.nav.count == 1
        assert view.aux.active == "x", "the sibling component is untouched"
        frame = next(f for f in transport.sent if f.get("type") == "html_update")
        assert frame["event_name"] == "set_active"
        assert ">b</p>" in frame["html"], frame["html"]

    @pytest.mark.asyncio
    async def test_undecorated_method_is_refused(self):
        view = Page()
        view.mount(None)
        view.nav  # noqa: B018
        runtime, transport = _runtime(view)

        for name in ("not_a_handler", "shout"):
            await runtime.dispatch_event(
                {"type": "event", "event": name, "params": {"component_id": "nav"}}
            )
        assert len(transport.errors) == 2, transport.sent
        assert [f.get("type") for f in transport.sent] == ["error", "error"]
        assert view.nav.active == "a"

    @pytest.mark.asyncio
    async def test_meta_event_reaches_handle_event_through_component_id(self):
        """The eight shipped descriptors declare ``Meta.event`` and
        ``_handle_event``; an event carrying their ``component_id`` reaches it."""

        class TabsPage(LiveView):
            template = '<div dj-root dj-id="0">{{ tabs.active }}</div>'
            tabs = Tabs(active="one")

            def mount(self, request, **kwargs):
                pass

        view = TabsPage()
        view.mount(None)
        view.tabs  # noqa: B018
        runtime, transport = _runtime(view)
        await runtime.dispatch_event(
            {
                "type": "event",
                "event": "set_tab",
                "params": {"component_id": "tabs", "value": "two"},
            }
        )
        assert transport.errors == []
        assert view.tabs.active == "two"

    def test_meta_event_alias_passes_the_state_itself(self, monkeypatch):
        """The view-level alias ``_make_event_handler`` registers is unchanged:
        ``_handle_event`` receives the ``State`` (not the bound component)."""
        seen: List[Any] = []
        original = Tabs._handle_event

        def spy(self, state, **kwargs):
            seen.append(state)
            return original(self, state, **kwargs)

        monkeypatch.setattr(Tabs, "_handle_event", spy)

        class TabsPage(LiveView):
            tabs = Tabs(active="one")

        view = TabsPage()
        assert callable(getattr(TabsPage, "set_tab"))
        view.set_tab(value="two", component_id="tabs")
        assert view.tabs.active == "two"
        assert len(seen) == 1
        assert type(seen[0]) is Tabs.State
        assert seen[0] is view.tabs.state

    @pytest.mark.parametrize(
        "descriptor_cls",
        [Accordion, Carousel, Collapsible, Dropdown, Modal, Sheet, Tabs, Tooltip],
        ids=lambda c: c.__name__,
    )
    def test_eight_descriptors_bind_and_alias(self, descriptor_cls, monkeypatch):
        seen: List[Any] = []
        original = descriptor_cls._handle_event

        def spy(self, state, **kwargs):
            seen.append(state)
            return original(self, state, **kwargs)

        monkeypatch.setattr(descriptor_cls, "_handle_event", spy)
        event = descriptor_cls.Meta.event

        View = type("View", (LiveView,), {"comp": descriptor_cls()})
        view = View()
        bound = view.comp
        assert isinstance(bound, BoundComponent)
        assert type(bound.state) is descriptor_cls.State
        assert view._components["comp"] is bound
        getattr(view, event)(value="v", component_id="comp")
        assert seen == [bound.state]
        # A second view has its own State object.
        assert View().comp.state is not bound.state


@pytest.mark.django_db
class TestBoundComponentHttpPost:
    """Review 🔴1: the HTTP fallback transport must route ``component_id``
    like the runtime does, not silently re-render (#1646)."""

    def _get(self):
        request = _request("/probe/")
        response = Page.as_view()(request)
        assert response.status_code == 200
        assert b">a</p>" in response.content
        return request.session

    def _post(self, session, payload):
        request = RequestFactory().post(
            "/probe/", data=json.dumps(payload), content_type="application/json"
        )
        request.session = session
        return Page.as_view()(request)

    def test_component_id_event_reaches_the_handler_over_http(self):
        session = self._get()
        response = self._post(
            session,
            {"event": "set_active", "params": {"component_id": "nav", "value": "http"}},
        )
        assert response.status_code == 200, response.content
        assert b"http" in response.content
        assert session["liveview_/probe/"]["nav"]["active"] == "http"
        assert session["liveview_/probe/"]["nav"]["count"] == 1

    def test_unknown_component_and_undecorated_method_are_400(self):
        session = self._get()
        missing = self._post(
            session, {"event": "set_active", "params": {"component_id": "nope", "value": "x"}}
        )
        assert missing.status_code == 400
        undecorated = self._post(
            session, {"event": "not_a_handler", "params": {"component_id": "nav"}}
        )
        assert undecorated.status_code == 400
        absent = self._post(session, {"event": "no_such", "params": {"component_id": "nav"}})
        assert absent.status_code == 400
        assert session["liveview_/probe/"]["nav"]["active"] == "a"

    def test_meta_event_alias_still_works_over_http(self):
        session = self._get()
        response = self._post(session, {"event": "set_active", "params": {"value": "plain"}})
        # No component_id: the view has no ``set_active`` (only the component
        # does), so the request is refused rather than silently succeeding.
        assert response.status_code in (200, 400)
        assert session["liveview_/probe/"]["nav"]["active"] == "a"


# ------------------------------------------------------------------ #
# D7 — snapshots and session save see the State, never the wrapper
# ------------------------------------------------------------------ #


class TestBoundComponentPersistence:
    def test_time_travel_snapshot_holds_the_state_flat(self):
        view = Page()
        view.nav.active = "t"
        view.aux  # noqa: B018
        snap = view._capture_components_snapshot()
        assert snap["nav"] == {"active": "t", "count": 0}
        assert snap["aux"] == {"active": "x", "count": 0}
        # The restore loop (time_travel.py) applies each key with safe_setattr.
        assert safe_setattr(view.nav, "active", "back", allow_private=False) is True
        assert view.nav.state["active"] == "back"

    def test_session_save_writes_the_state_and_restore_reads_it(self):
        view = Page()
        view.nav.active = "s"
        request = _request()
        view._save_components_to_session(request, view.get_context_data())
        saved = request.session["liveview_/page/_components"]
        assert saved["nav"] == {"active": "s", "count": 0, "component_id": "nav"}
        json.dumps(saved)  # no bound-object leak

        fresh = Page()
        assert isinstance(fresh.nav, SESSION_COMPONENT_TYPES)
        fresh._restore_component_state(fresh.nav, saved["nav"])
        assert fresh.nav.active == "s"
        assert isinstance(fresh.nav.state, Toggle.State)

    def test_private_state_round_trip_persists_a_component_touched_in_mount(self):
        view = Page()
        view.mount(None)
        view._snapshot_user_private_attrs()
        view.nav.active = "p"
        private = view._get_private_state()
        assert private["_component_nav"] == {"active": "p", "count": 0, "component_id": "nav"}
        json.dumps(private)

        fresh = Page()
        fresh._restore_private_state(private)
        assert isinstance(fresh.nav, BoundComponent)
        assert fresh.nav.active == "p"
        assert fresh._components["nav"] is fresh.nav

    def test_normalize_carries_the_state_on_both_modes_without_warning(self, caplog):
        """``normalize_django_value`` must hand the State (a dict) to the render
        path and to the ``state_roundtrip=True`` session path. Without its
        BoundComponent arm both become ``str(value)`` plus a per-render
        "non-serializable value" warning; the render survives only because
        the sidecar getattr walk rescues the string, the session does not."""
        import logging

        from djust.serialization import normalize_django_value

        view = Page()
        with caplog.at_level(logging.WARNING, logger="djust.serialization"):
            rendered = normalize_django_value(view.nav)
            saved = normalize_django_value(view.nav, state_roundtrip=True)
        assert rendered == {"active": "a", "count": 0, "component_id": "nav"}
        assert saved == rendered
        assert "non-serializable" not in caplog.text

    def test_update_component_forwards_props(self):
        view = Page()
        view.nav  # noqa: B018
        view.update_component("nav", active="u")
        assert view.nav.active == "u"


# ------------------------------------------------------------------ #
# Parity pins
# ------------------------------------------------------------------ #


class TestBoundComponentParity:
    def test_both_engines_render_the_bound_component_as_before(self):
        """``{{ nav }}`` is still the dict repr (M4) and ``{{ nav.active }}``
        still resolves, with a non-dict object in the context (M10)."""
        view = Page()
        ctx = {"nav": view.nav}
        expected = escape(str(view.nav.state)) + "|a"
        assert render_template("{{ nav }}|{{ nav.active }}", ctx) == expected
        django_out = Engine().from_string("{{ nav }}|{{ nav.active }}").render(Context(ctx))
        assert django_out == expected

    def test_context_pipeline_resolves_the_descriptor_to_the_bound_component(self):
        view = Page()
        view.mount(None)
        ctx = view.get_context_data()
        assert ctx["nav"] is view.nav
        assert ctx["aux"] is view.aux

    def test_gallery_passes_the_bound_state_into_cards(self):
        """Review M10: ``_build_extra_context`` must hand the State to the
        card renderer; with a wrapper that is not a dict it returned ``{}``."""
        from djust.components.gallery.live_views import LayoutGalleryView

        view = LayoutGalleryView()
        view.tabs.active = "second"
        ctx = view._build_extra_context("tabs")
        assert ctx == {"active": "second", "component_id": "tabs"}
        assert view._build_extra_context("not_a_descriptor") == {}

    def test_no_isinstance_state_checks_in_tree(self):
        """ADR-031 accepted consequence: ``isinstance(view.nav, X.State)`` is
        False after PR 1. The tree must not rely on it."""
        pattern = re.compile(r"isinstance\([^)]*\.State\)")
        this_file = pathlib.Path(__file__).resolve()
        hits = [
            str(p.relative_to(REPO))
            for p in (REPO / "python").rglob("*.py")
            if p.resolve() != this_file and pattern.search(p.read_text(errors="ignore"))
        ]
        assert hits == []

    def test_session_gates_share_one_type_tuple(self):
        """The save gate and both restore gates must not drift apart (#1646)."""
        sources = {
            "python/djust/mixins/components.py": 1,
            "python/djust/mixins/request.py": 1,
            "python/djust/runtime.py": 1,
        }
        for rel, expected in sources.items():
            text = (REPO / rel).read_text()
            assert text.count("isinstance(component, SESSION_COMPONENT_TYPES)") == expected, rel
            assert "isinstance(component, (Component, LiveComponent))" not in text, rel
