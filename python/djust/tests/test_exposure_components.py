"""ADR-038 E2-7: components under the explicit policy, over the real runtime.

Decision D-h: a component assigned on the instance (``self.nav = Tabs()`` in
``mount``) is never discovered; the first explicit render raises a diagnostic
naming the attribute. Class-level declarations are registered providers:
ADR-031 descriptors, State-less components and ADR-034 interactive bindings,
each bound per view instance.

Persistence (v1): components are transient under the explicit policy. Their
state is not persisted and a reconnect remounts them from their declarations;
declared ``state(persist="server")`` view fields still restore.

Every explicit case has a legacy control that proves the same path ran. Only
the staged construction guard is bypassed; views are explicit from mount.
"""

import json
import logging
import re
from typing import Any

import pytest
from asgiref.sync import sync_to_async
from django.test import override_settings

from djust import LiveView, event_handler
from djust._exposure import ExposureConfigurationError
from djust.components._interactive import DropdownMenu
from djust.components.descriptors.base import LiveComponent, TypedState
from djust.decorators import state
from djust.runtime import ViewRuntime
from djust.tests.test_exposure_runtime import make_request
from djust.tests.test_runtime_state_save_tt_1894 import MockTransport

pytestmark = [pytest.mark.django_db(transaction=True)]


# ------------------------------------------------------------------ #
# Components
# ------------------------------------------------------------------ #


class Tabs(LiveComponent):
    class State(TypedState):
        active: str = "overview"

    template = '<p class="tabs">{{ active }}</p>'

    @event_handler()
    def select(self, value: str = "", **kwargs: Any) -> None:
        self.state.active = value


class AltTabs(Tabs):
    template = '<p class="alt-tabs">{{ active }}</p>'


class Badge(LiveComponent):
    """State-less: its declaration mounts once, from the class body."""

    template = '<i class="badge">{{ label }}</i>'

    def mount(self, **kwargs: Any) -> None:
        self.label = kwargs.get("label", "new")

    def get_context_data(self) -> dict:
        return {"label": self.label}


class Counter(LiveComponent):
    """State-less, with a nested component and mutable state of its own."""

    template = '<b class="counter">{{ clicks }}:{{ seen|join:"," }}{{ badge }}</b>'

    def mount(self, **kwargs: Any) -> None:
        self.clicks = 0
        self.seen: list = []
        self.badge = Badge(label="nested")

    def get_context_data(self) -> dict:
        return {"clicks": self.clicks, "seen": self.seen, "badge": self.badge}

    @event_handler()
    def bump(self, value: str = "", **kwargs: Any) -> None:
        self.clicks += 1
        self.seen.append(value)
        self.badge.label = value
        self.trigger_update()


# ------------------------------------------------------------------ #
# Views (the policy is set per test, before mount)
# ------------------------------------------------------------------ #


class ComponentPage(LiveView):
    exposure_policy = "legacy"
    template = "<div dj-root>{{ nav }}{{ counter }}<span>{{ count }}</span></div>"
    count = state(0, persist="server")
    nav = Tabs()
    counter = Counter()

    def get_context_data(self, **kwargs):
        return super().get_context_data(count=self.count, **kwargs)

    @event_handler()
    def increment(self, **kwargs):
        self.count += 1


class OverridePage(ComponentPage):
    """Subclass override: ``nav`` replaced, ``extra`` added, ``counter`` inherited."""

    template = "<div dj-root>{{ nav }}{{ counter }}{{ extra }}<span>{{ count }}</span></div>"
    count = state(0, persist="server")
    nav = AltTabs()
    extra = Tabs(active="extra")


class MenuPage(LiveView):
    exposure_policy = "legacy"
    template = "<div dj-root>{{ menu }}<p>{{ result }}</p></div>"
    result = state("initial")
    menu = DropdownMenu(label="Actions", items=[{"label": "Edit", "value": "edit"}])

    def get_context_data(self, **kwargs):
        return super().get_context_data(result=self.result, **kwargs)

    @menu.on.selected
    def menu_selected(self, component: DropdownMenu, value: str) -> None:
        self.result = "picked:" + value


class InstanceAssignedPage(LiveView):
    exposure_policy = "legacy"
    template = "<div dj-root>{{ sidebar }}<span>{{ count }}</span></div>"
    count = state(0, persist="server")

    def mount(self, request, **kwargs):
        self.sidebar = Badge(label="INSTANCE_VALUE_SENTINEL")

    def get_context_data(self, **kwargs):
        return super().get_context_data(count=self.count, **kwargs)


class ScopedPage(LiveView):
    exposure_policy = "legacy"
    template = "<div dj-root><h1>{{ title }}</h1>{{ nav }}</div>"
    title = state("Page")
    nav = Tabs()

    def get_context_data(self, **kwargs):
        return super().get_context_data(title=self.title, **kwargs)


# ------------------------------------------------------------------ #
# Harness
# ------------------------------------------------------------------ #


@pytest.fixture
def staged(monkeypatch):
    monkeypatch.setattr(LiveView, "_validate_exposure_configuration", lambda self: None)


async def mount(view_class, request=None):
    request = request or await sync_to_async(make_request)()
    transport = MockTransport()
    transport.build_request = lambda: request

    async def fresh_event_request(view):
        return await sync_to_async(make_request)(request.session.session_key)

    transport.explicit_event_request = fresh_event_request
    runtime = ViewRuntime(transport)
    with override_settings(LIVEVIEW_ALLOWED_MODULES=[__name__]):
        await runtime.dispatch_mount(
            {"type": "mount", "view": __name__ + "." + view_class.__name__, "url": request.path}
        )
    return runtime, transport, request


def strip_ids(html):
    return re.sub(r' dj-id="[^"]*"', "", html)


def mount_html(transport):
    frames = [frame for frame in transport.sent if frame.get("type") == "mount"]
    assert frames, transport.sent
    return strip_ids(frames[-1]["html"])


async def event(runtime, name, **params):
    await runtime.dispatch_event({"type": "event", "event": name, "params": params})


def rendered(runtime):
    return strip_ids(runtime.view_instance.render())


# ------------------------------------------------------------------ #
# Interactive (ADR-034) bindings are registered component providers
# ------------------------------------------------------------------ #


@pytest.mark.asyncio
@pytest.mark.parametrize("policy", ["legacy", "explicit"])
async def test_interactive_binding_renders_and_dispatches_under_explicit(
    staged, monkeypatch, policy
):
    monkeypatch.setattr(MenuPage, "exposure_policy", policy)
    runtime, transport, _ = await mount(MenuPage)
    assert not transport.errors, transport.errors
    html = mount_html(transport)
    view = runtime.view_instance
    # Both policies render the binding; explicit through the registered provider.
    assert "dj-dropdown-menu" in html and "Actions" in html, html
    binding = view.menu
    assert binding is not MenuPage.__dict__["menu"]
    assert f'data-component-id="{binding.component_id}"' in html

    await event(runtime, "toggle", component_id=binding.component_id)
    await event(runtime, "select", component_id=binding.component_id, value="edit")
    assert not transport.errors, transport.errors
    assert view.result == "picked:edit"
    assert binding.selected == "edit"
    assert "picked:edit" in rendered(runtime)


def test_interactive_declaration_joins_the_component_manifest():
    from djust._exposure_providers import components_provider

    class Page(LiveView):
        exposure_policy = "explicit"
        menu = DropdownMenu(label="A", items=[{"label": "B", "value": "b"}])
        nav = Tabs()

    class Sub(Page):
        nav = AltTabs()
        other = DropdownMenu(label="C", items=[{"label": "D", "value": "d"}])

    assert components_provider(Page).rendered == frozenset({"menu", "nav"})
    assert components_provider(Sub).rendered == frozenset({"menu", "nav", "other"})

    class Replaced(Page):
        # An interactive declaration replaced by an ordinary descriptor.
        menu = Tabs()

    view = object.__new__(Replaced)
    view._components, view._component_bindings, view._streams = {}, {}, {}
    context = view.get_context_data()
    assert set(context) == {"menu", "nav"}
    assert type(context["menu"]._descriptor) is Tabs

    class Shadowed(Page):
        @property
        def menu(self):
            raise AssertionError("a stale registry must not evaluate a replacement property")

    shadowed = object.__new__(Shadowed)
    shadowed._components, shadowed._component_bindings, shadowed._streams = {}, {}, {}
    from djust._exposure import ExposureError

    with pytest.raises(ExposureError, match="declaration"):
        shadowed.get_context_data()


# ------------------------------------------------------------------ #
# Per-view bindings: two same-class views never share component state
# ------------------------------------------------------------------ #


@pytest.mark.asyncio
async def test_two_explicit_views_do_not_share_stateless_component_state(staged, monkeypatch):
    monkeypatch.setattr(ComponentPage, "exposure_policy", "explicit")
    first, first_transport, _ = await mount(ComponentPage)
    second, second_transport, _ = await mount(ComponentPage)
    assert not first_transport.errors and not second_transport.errors
    assert '<b class="counter">0:<' in mount_html(first_transport)
    one, two = first.view_instance, second.view_instance
    assert one.counter is not two.counter
    assert one.counter is not ComponentPage.__dict__["counter"]
    assert one.counter.badge is not two.counter.badge
    assert one._components["counter"] is one.counter

    await event(first, "bump", component_id="counter", value="FIRST_ONLY")
    await event(first, "select", component_id="nav", value="billing")
    assert not first_transport.errors, first_transport.errors
    assert one.counter.clicks == 1 and one.nav.active == "billing"
    first_page = rendered(first)
    assert '<b class="counter">1:FIRST_ONLY<' in first_page
    assert '<i class="badge">FIRST_ONLY</i>' in first_page

    # The other view, and the class-level declaration, are untouched.
    second_page = rendered(second)
    assert '<b class="counter">0:<' in second_page
    assert '<i class="badge">nested</i>' in second_page
    assert "FIRST_ONLY" not in second_page and "billing" not in second_page
    assert "FIRST_ONLY" not in json.dumps(second_transport.sent, default=str)
    declaration = ComponentPage.__dict__["counter"]
    assert declaration.clicks == 0 and declaration.seen == [] and declaration._parent is None


def test_legacy_stateless_declaration_is_unchanged():
    """Legacy control: a State-less declaration is still the class-level object."""
    view = object.__new__(ComponentPage)
    view._components = {}
    assert view.counter is ComponentPage.__dict__["counter"]
    assert "_component_counter" not in view.__dict__


# ------------------------------------------------------------------ #
# Nested components and subclass override
# ------------------------------------------------------------------ #


@pytest.mark.asyncio
@pytest.mark.parametrize("policy", ["legacy", "explicit"])
async def test_nested_components_and_subclass_override_render(staged, monkeypatch, policy):
    monkeypatch.setattr(ComponentPage, "exposure_policy", policy)
    base, base_transport, _ = await mount(ComponentPage)
    sub, sub_transport, _ = await mount(OverridePage)
    assert not base_transport.errors and not sub_transport.errors
    base_html, sub_html = mount_html(base_transport), mount_html(sub_transport)

    assert '<p class="tabs">overview</p>' in base_html
    assert "alt-tabs" not in base_html
    assert '<p class="alt-tabs">overview</p>' in sub_html
    assert '<p class="tabs">extra</p>' in sub_html
    # The nested component renders inside its inherited parent component.
    for html in (base_html, sub_html):
        assert '<i class="badge">nested</i>' in html
    assert type(sub.view_instance.nav._descriptor) is AltTabs

    await event(sub, "select", component_id="extra", value="chosen")
    assert not sub_transport.errors, sub_transport.errors
    assert '<p class="tabs">chosen</p>' in rendered(sub)
    assert '<p class="tabs">chosen</p>' not in rendered(base)
    if policy == "legacy":
        # A legacy State-less declaration is shared by every view; unchanged.
        assert sub.view_instance.counter is base.view_instance.counter
        return

    # Explicit: the inherited State-less component and its nested child are
    # this view's own; mutating them leaves the base-class view untouched.
    assert sub.view_instance.counter is not base.view_instance.counter
    await event(sub, "bump", component_id="counter", value="SUB_ONLY")
    assert not sub_transport.errors, sub_transport.errors
    sub_page = rendered(sub)
    assert '<b class="counter">1:SUB_ONLY<' in sub_page
    assert '<i class="badge">SUB_ONLY</i>' in sub_page
    assert '<p class="alt-tabs">overview</p>' in sub_page
    base_page = rendered(base)
    assert "SUB_ONLY" not in base_page and '<i class="badge">nested</i>' in base_page


# ------------------------------------------------------------------ #
# D-h: instance-assigned components
# ------------------------------------------------------------------ #


def _get(view_class, rf):
    from django.contrib.auth.models import AnonymousUser
    from django.contrib.sessions.backends.db import SessionStore

    request = rf.get("/instance/")
    request.session = SessionStore()
    request.user = AnonymousUser()
    request.tenant = None
    return view_class.as_view()(request)


@pytest.mark.parametrize("policy", ["legacy", "explicit"])
def test_instance_assigned_component_raises_a_diagnostic_naming_it(
    staged, monkeypatch, rf, settings, policy
):
    monkeypatch.setattr(InstanceAssignedPage, "exposure_policy", policy)
    settings.DEBUG = True
    if policy == "legacy":
        # Control: legacy discovers the attribute and renders it.
        response = _get(InstanceAssignedPage, rf)
        assert response.status_code == 200
        assert "INSTANCE_VALUE_SENTINEL" in response.content.decode()
        return
    with pytest.raises(ExposureConfigurationError) as raised:
        _get(InstanceAssignedPage, rf)
    message = str(raised.value)
    assert "'sidebar'" in message and "class level" in message
    assert "INSTANCE_VALUE_SENTINEL" not in message
    assert raised.value.__cause__ is None and raised.value.__context__ is None


@pytest.mark.asyncio
async def test_instance_assigned_component_diagnostic_over_the_runtime(staged, monkeypatch, caplog):
    monkeypatch.setattr(InstanceAssignedPage, "exposure_policy", "explicit")
    with caplog.at_level(logging.DEBUG):
        runtime, transport, _ = await mount(InstanceAssignedPage)
    sent = json.dumps(transport.sent, default=str)
    assert not any(frame.get("type") == "mount" for frame in transport.sent)
    assert any(frame.get("type") == "error" for frame in transport.sent), transport.sent
    assert "Protected view operation failed" in caplog.text
    assert "INSTANCE_VALUE_SENTINEL" not in sent
    assert "INSTANCE_VALUE_SENTINEL" not in caplog.text


def test_private_or_registered_attributes_are_not_instance_assigned_components():
    view = object.__new__(ComponentPage)
    view.exposure_policy = "explicit"
    view._components, view._streams = {}, {}
    view._scratch = Tabs()  # private: never rendered under either policy
    context = view.get_context_data()
    assert set(context) == {"nav", "counter", "count"}


# ------------------------------------------------------------------ #
# Persistence decision: transient under explicit (remount)
# ------------------------------------------------------------------ #


@pytest.mark.asyncio
@pytest.mark.parametrize("policy", ["legacy", "explicit"])
async def test_component_state_is_transient_under_explicit(staged, monkeypatch, policy):
    from djust._exposure_sessions import server_state_adapter

    monkeypatch.setattr(ComponentPage, "exposure_policy", policy)
    if policy == "legacy":
        # The legacy per-event session save is opt-in.
        monkeypatch.setattr(ComponentPage, "enable_state_snapshot", True)
    runtime, transport, request = await mount(ComponentPage)
    assert not transport.errors, transport.errors
    view = runtime.view_instance
    await event(runtime, "select", component_id=view.nav.component_id, value="PERSIST_SENTINEL")
    if policy == "explicit":
        # (A legacy State-less declaration is shared; do not mutate it here.)
        await event(runtime, "bump", component_id="counter", value="COUNTER_SENTINEL")
    await event(runtime, "increment")
    assert not transport.errors, transport.errors
    assert runtime.view_instance.nav.active == "PERSIST_SENTINEL"

    session = await sync_to_async(type(request.session))(request.session.session_key)
    stored = json.dumps(await sync_to_async(session.load)(), default=str)
    if policy == "legacy":
        # Control: the legacy save path ran and wrote the component's state.
        assert "PERSIST_SENTINEL" in stored
        return

    # Explicit: the declared field persisted; component state did not.
    fresh = await sync_to_async(make_request)(request.session.session_key)
    adapter = await sync_to_async(server_state_adapter)(runtime.view_instance, fresh)
    assert await adapter.aload() == {"count": 1}
    assert "PERSIST_SENTINEL" not in stored and "COUNTER_SENTINEL" not in stored

    again = await sync_to_async(make_request)(request.session.session_key)
    second, second_transport, _ = await mount(ComponentPage, again)
    assert not second_transport.errors, second_transport.errors
    assert second.view_instance.count == 1
    assert second.view_instance.nav.active == "overview"
    assert second.view_instance.counter.clicks == 0
    html = mount_html(second_transport)
    assert "PERSIST_SENTINEL" not in html and "COUNTER_SENTINEL" not in html


# ------------------------------------------------------------------ #
# A component event whose scoped render fails (ADR-032 D6 fallback)
# ------------------------------------------------------------------ #


@pytest.mark.asyncio
@pytest.mark.parametrize("policy", ["legacy", "explicit"])
async def test_component_event_scoped_render_failure_is_value_free(
    staged, monkeypatch, caplog, policy
):
    monkeypatch.setattr(ScopedPage, "exposure_policy", policy)
    from djust.components.base import BoundComponent

    runtime, transport, _ = await mount(ScopedPage)
    assert not transport.errors, transport.errors
    calls = []
    original = BoundComponent.render

    def fail_once(self, *args, **kwargs):
        # The scoped render is the first component render after the handler;
        # the D6 full render that follows must succeed.
        if not calls:
            calls.append(True)
            raise ValueError("SCOPED_COMPONENT_SENTINEL")
        return original(self, *args, **kwargs)

    monkeypatch.setattr(BoundComponent, "render", fail_once)
    transport.sent.clear()
    caplog.clear()
    with caplog.at_level(logging.DEBUG):
        await event(runtime, "select", component_id="nav", value="billing")

    assert calls, "the scoped render never ran; the test is vacuous"
    assert not transport.errors, transport.errors
    # D6: the full render took over in the same event.
    frames = json.dumps(transport.sent, default=str)
    assert "billing" in frames
    assert "SCOPED_COMPONENT_SENTINEL" not in frames
    if policy == "legacy":
        assert "Scoped render of component 'nav' failed; full render" in caplog.text
        assert "SCOPED_COMPONENT_SENTINEL" in caplog.text
    else:
        assert "SCOPED_COMPONENT_SENTINEL" not in caplog.text
        assert "Protected view operation failed" in caplog.text
