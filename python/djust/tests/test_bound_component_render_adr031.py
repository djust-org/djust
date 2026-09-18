"""ADR-031 PR 2 — ``{{ nav }}`` renders a class-level component's template (D5)
with a state-hash render cache (D6).

The oracle is the real path: a LiveView with ``nav = Tabs(...)`` rendered
through ``render_with_diff`` (HTTP GET and the WebSocket runtime), a click
inside the rendered markup reaching the component's handler with
``self.state``, and the re-render reflecting it on that view only.
"""

from __future__ import annotations

import contextlib
import json
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
        SECRET_KEY="test-secret-key-adr031-render",
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
from django.test import RequestFactory  # noqa: E402
from django.utils.html import escape  # noqa: E402

from djust import LiveView  # noqa: E402
from djust.components import base as components_base  # noqa: E402
from djust.components.base import BoundComponent  # noqa: E402
from djust.components.descriptors import Tabs as ShippedTabs  # noqa: E402
from djust.components.descriptors.base import LiveComponent, TypedState  # noqa: E402
from djust.decorators import event_handler  # noqa: E402
from djust.runtime import ViewRuntime  # noqa: E402
from djust.serialization import normalize_django_value  # noqa: E402


class MockTransport:
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


class NoTemplate(LiveComponent):
    class State(TypedState):
        active: str = "x"


class Page(LiveView):
    template = '<div dj-root dj-id="0">{{ nav }}|{{ nav.active }}|{{ plain }}</div>'
    nav = Tabs(active="overview")
    plain = NoTemplate()

    def mount(self, request: Any, **kwargs: Any) -> None:
        pass


def _runtime(view: LiveView) -> tuple[ViewRuntime, MockTransport]:
    transport = MockTransport()
    runtime = ViewRuntime(transport)
    runtime.view_instance = view
    return runtime, transport


def _html(transport: MockTransport) -> str:
    frame = next(f for f in transport.sent if f.get("type") == "html_update")
    return frame["html"]


# ------------------------------------------------------------------ #
# D5 — str(bound) renders the template with this view's State
# ------------------------------------------------------------------ #


class TestBoundComponentRender:
    def test_str_renders_template_with_state_wrapped_in_component_id(self):
        view = Page()
        html = str(view.nav)
        assert html.startswith('<div data-component-id="nav">')
        assert "<p>overview<b>nav</b></p>" in html
        assert html.endswith("</div>")
        view.nav.active = "billing"
        assert "<p>billing<b>nav</b></p>" in str(view.nav)

    def test_render_is_per_view(self):
        v1, v2 = Page(), Page()
        v1.nav.active = "billing"
        assert "<p>billing" in str(v1.nav)
        assert "<p>overview" in str(v2.nav)

    def test_template_less_component_keeps_the_dict_repr(self):
        """The opt-in gate-off: no template → ``str(state)`` as before ADR-031."""
        view = Page()
        assert str(view.plain) == str(view.plain.state)
        with pytest.raises(ValueError):
            view.plain.render()

    def test_normalize_carries_html_for_render_and_state_for_the_session(self):
        view = Page()
        rendered = normalize_django_value(view.nav)
        assert rendered == str(view.nav)
        assert normalize_django_value(view.nav, state_roundtrip=True) == {
            "active": "overview",
            "component_id": "nav",
        }
        assert normalize_django_value(view.plain) == {"active": "x", "component_id": "plain"}

    def test_http_get_renders_the_component_and_its_attributes(self):
        request = RequestFactory().get("/tabs/")
        request.session = SessionStore()
        response = Page.as_view()(request)
        body = response.content.decode()
        assert response.status_code == 200
        assert 'data-component-id="nav"' in body
        assert "overview<b" in body and ">nav</b></p>" in body, body
        assert "|overview|" in body, "{{ nav.active }} still resolves next to {{ nav }}"
        # A template-less component is still the dict repr (M4; this path
        # autoescapes it, ``render_with_diff`` does not — both pre-existing).
        plain = str(Page().plain.state)
        assert plain in body or escape(plain) in body


# ------------------------------------------------------------------ #
# D6 — render cache on the state hash, never on _dirty
# ------------------------------------------------------------------ #


class TestBoundComponentRenderCache:
    def test_unchanged_state_is_not_re_rendered(self, monkeypatch):
        calls: List[str] = []
        original = components_base._render_template_with_fallback

        def counting(template: str, context: Dict[str, Any]) -> str:
            calls.append(template)
            return original(template, context)

        monkeypatch.setattr(components_base, "_render_template_with_fallback", counting)
        view = Page()
        first = str(view.nav)
        second = str(view.nav)
        assert first == second
        assert len(calls) == 1, "same state hash → cached HTML"
        view.nav.active = "billing"
        third = str(view.nav)
        assert "<p>billing" in third
        assert len(calls) == 2
        # Writing the same value back does not change the hash.
        view.nav.active = "billing"
        str(view.nav)
        assert len(calls) == 2
        # A mutation that bypasses ``TypedState.__setitem__`` (so
        # ``_cached_html`` is NOT cleared) is still caught by the hash.
        dict.update(view.nav.state, {"active": "overview"})
        assert view.nav.state._cached_html is not None
        assert "<p>overview" in str(view.nav)
        assert len(calls) == 3

    def test_rendering_does_not_touch_dirty(self):
        view = Page()
        object.__setattr__(view.nav.state, "_dirty", False)
        str(view.nav)
        assert view.nav.state._dirty is False
        view.nav.active = "billing"
        assert view.nav.state._dirty is True
        str(view.nav)
        assert view.nav.state._dirty is True, "render never clears the change-detection flag"
        assert view.nav.state._render_hash == view.nav._state_hash()


# ------------------------------------------------------------------ #
# End to end — a click inside the rendered markup reaches the handler
# ------------------------------------------------------------------ #


@pytest.mark.django_db
class TestBoundComponentRenderRoundTrip:
    @pytest.mark.asyncio
    async def test_click_inside_rendered_markup_reaches_handler_and_re_renders(self):
        view = Page()
        view.mount(None)
        runtime, transport = _runtime(view)
        # Mount-shaped first render through the real path.
        html, _patches, _version = view.render_with_diff()
        assert 'data-component-id="nav"' in html
        assert 'dj-click="select"' in html
        # The real path stamps dj-id on every element; match around them.
        assert "overview<b" in html and ">nav</b></p>" in html, html

        await runtime.dispatch_event(
            {
                "type": "event",
                "event": "select",
                "params": {"component_id": "nav", "value": "billing"},
            }
        )
        assert transport.errors == []
        assert view.nav.active == "billing"
        out = _html(transport)
        assert "billing<b" in out and ">nav</b></p>" in out, out
        assert "|billing|" in out

    @pytest.mark.asyncio
    async def test_other_views_are_untouched(self):
        other = Page()
        view = Page()
        view.mount(None)
        runtime, transport = _runtime(view)
        view.render_with_diff()
        await runtime.dispatch_event(
            {
                "type": "event",
                "event": "select",
                "params": {"component_id": "nav", "value": "billing"},
            }
        )
        assert view.nav.active == "billing"
        assert other.nav.active == "overview"
        assert "<p>overview" in str(other.nav)

    def test_shipped_descriptor_without_template_is_unchanged(self):
        class ShippedPage(LiveView):
            template = '<div dj-root dj-id="0">{{ tabs }}</div>'
            tabs = ShippedTabs(active="one")

        view = ShippedPage()
        assert isinstance(view.tabs, BoundComponent)
        assert view.tabs.template is None and view.tabs.template_name is None
        assert str(view.tabs) == str(view.tabs.state)
        html, _p, _v = view.render_with_diff()
        # The engine's map display (ADR-031 M4), byte-identical to before.
        assert str(view.tabs.state) in html, html
        json.dumps(normalize_django_value(view.tabs, state_roundtrip=True))
