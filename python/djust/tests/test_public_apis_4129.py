"""#4129 — public APIs for what applications reached through private names.

Each public form is driven through the real render path (the runtime event
spine, ``server_push``) and asserted on the frames the client receives, not on
the private flag it happens to set: a method that set the flag but whose flag
nobody read would fail here.
"""

from __future__ import annotations

import pytest

from djust import LiveView
from djust.decorators import event_handler
from djust.tests.test_skip_render_force_parity_2834 import _consumer_with_view
from djust.tests.test_transport_behavioral_parity import _event_runtime_with_view

_HERE = "djust.tests.test_public_apis_4129"


def _updates(sent):
    return [f for f in sent if f.get("type") in ("patch", "html_update")]


def _noops(sent):
    return [f for f in sent if f.get("type") == "noop"]


class _Base(LiveView):
    exposure_policy = "legacy"

    def mount(self, request, **kwargs):
        self.c = 0

    def get_context_data(self, **kwargs):
        return {"c": self.c}


class RenderControlView(_Base):
    template = f'<div dj-root dj-view="{_HERE}.RenderControlView" dj-id="0">c={{{{ c }}}}</div>'

    @event_handler()
    def bump_quietly(self, **kwargs):
        self.c += 1
        self.skip_render()

    @event_handler()
    def db_only(self, **kwargs):
        self.request_full_render()

    @event_handler()
    def db_only_unforced(self, **kwargs):
        pass

    @event_handler()
    def request_then_cancel(self, **kwargs):
        self.set_changed_keys()
        self.cancel_full_render()

    @event_handler()
    def cancel_only(self, **kwargs):
        self.cancel_full_render()

    @event_handler()
    def reset(self, **kwargs):
        self.reset_submitted_form()

    @event_handler()
    def queue_then_clear(self, **kwargs):
        self.push_event("a", {"n": 1})
        self.push_event("b", {"n": 2})
        self.dropped = self.clear_push_events()
        self.push_event("c", {"n": 3})
        self.c += 1

    def handle_push(self, **kwargs):
        self.c += 1
        self.skip_render()


async def _run(view, event):
    runtime, transport = _event_runtime_with_view(view)
    runtime.view_instance.mount(None)
    await runtime.dispatch_event({"type": "event", "event": event, "params": {}})
    return runtime.view_instance, transport.sent


@pytest.mark.django_db
@pytest.mark.asyncio
class TestRenderControls:
    async def test_skip_render_from_an_event_handler_sends_no_render(self):
        view, sent = await _run(RenderControlView(), "bump_quietly")
        assert view.c == 1
        assert not _updates(sent), sent
        assert _noops(sent), sent
        assert view._skip_render is False, "the request lasts one turn"

    async def test_skip_render_from_a_push_handler_sends_no_render(self):
        consumer = _consumer_with_view(RenderControlView)
        await consumer.server_push({"handler": "handle_push", "payload": {}})
        assert consumer.view_instance.c == 1
        assert not _updates(consumer.sent), consumer.sent

    async def test_request_full_render_renders_a_turn_that_changed_no_state(self):
        _view, sent = await _run(RenderControlView(), "db_only")
        assert [f["type"] for f in _updates(sent)] == ["html_update"], sent

    async def test_without_the_request_the_same_turn_is_skipped(self):
        """Control for the test above: the render comes from the request."""
        _view, sent = await _run(RenderControlView(), "db_only_unforced")
        assert not _updates(sent), sent

    async def test_cancel_full_render_withdraws_this_turns_request(self):
        view, sent = await _run(RenderControlView(), "request_then_cancel")
        assert not _updates(sent), sent
        assert view._force_full_html is False

    async def test_cancel_keeps_a_full_render_the_framework_required(self):
        """A restore whose state differs from the page sets the flag before
        any handler runs; the handler's cancel must not drop it, or the page
        and the diff baseline disagree."""
        view = RenderControlView()
        runtime, transport = _event_runtime_with_view(view)
        view.mount(None)
        view._force_full_html = True  # what the restore path sets
        await runtime.dispatch_event(
            {"type": "event", "event": "request_then_cancel", "params": {}}
        )
        assert [f["type"] for f in _updates(transport.sent)] == ["html_update"], transport.sent

    async def test_a_cancel_in_a_later_turn_does_not_reach_back(self):
        """The request of an earlier turn is settled when that turn ends: a
        cancel in a later turn must not clear a full render the framework
        required in between."""
        view = RenderControlView()
        runtime, transport = _event_runtime_with_view(view)
        view.mount(None)
        await runtime.dispatch_event({"type": "event", "event": "db_only", "params": {}})
        assert _updates(transport.sent)
        transport.sent.clear()
        view._force_full_html = True  # framework-required, between turns
        await runtime.dispatch_event({"type": "event", "event": "cancel_only", "params": {}})
        assert [f["type"] for f in _updates(transport.sent)] == ["html_update"], transport.sent

    async def test_reset_submitted_form_reaches_the_client(self):
        view, sent = await _run(RenderControlView(), "reset")
        frames = _updates(sent)
        assert frames and frames[-1].get("reset_form") is True, sent
        assert view._should_reset_form is False, "consumed by the turn that sent it"


class TestPushEvents:
    def test_clear_push_events_drops_what_was_queued_before_it(self):
        view = RenderControlView()
        view.mount(None)
        view.queue_then_clear()
        assert view.dropped == [("a", {"n": 1}), ("b", {"n": 2})]
        assert view._pending_push_events == [("c", {"n": 3})]


class TestPrivateNamesStillWork:
    def test_the_methods_set_the_documented_private_flags(self):
        view = RenderControlView()
        view.skip_render()
        view.reset_submitted_form()
        view.request_full_render()
        assert view._skip_render is True
        assert view._should_reset_form is True
        assert view._force_full_html is True


# --- theme ---------------------------------------------------------------


@pytest.mark.theming
@pytest.mark.django_db
def test_get_theme_state_reads_the_current_mode():
    from django.contrib.auth.models import AnonymousUser
    from django.contrib.sessions.backends.db import SessionStore
    from django.test import RequestFactory

    from djust.theming import ThemeMixin

    class Themed(ThemeMixin, LiveView):
        exposure_policy = "legacy"
        template = "<div dj-root>{{ theme_mode }}</div>"

    request = RequestFactory().get("/")
    request.user = AnonymousUser()
    request.session = SessionStore()
    request.session.create()
    view = Themed()
    view.request = request
    view.mount(request)
    state = view.get_theme_state()
    assert state is view._theme_state
    assert state.mode == view.theme_mode
    assert state.resolved_mode in ("light", "dark")

    # A view restored from a session never ran mount(): read from the request.
    restored = Themed()
    restored.request = request
    assert restored.get_theme_state().mode == state.mode


# --- testing helpers -----------------------------------------------------


def test_render_template_string_uses_the_rust_engine():
    from djust.testing import render_template_string

    assert render_template_string("{{ n|floatformat:2 }}-{{ s }}", {"n": 1.5, "s": "<b>"}) == (
        "1.50-&lt;b&gt;"
    )


def test_render_component_renders_a_registered_component():
    from djust import Assign, component, get_function_component
    from djust.components.function_component import _COMPONENT_REGISTRY
    from djust.testing import render_component

    @component(name="pa4129_badge", assigns=[Assign("status", str, required=True)])
    def badge(assigns):
        return '<span class="badge badge-%s">%s</span>' % (assigns["status"], assigns["status"])

    try:
        assert get_function_component("pa4129_badge") is badge
        html = render_component("pa4129_badge", status="done")
        assert html == '<span class="badge badge-done">done</span>'
        with pytest.raises(RuntimeError, match="Required assign 'status'"):
            render_component("pa4129_badge")
        with pytest.raises(LookupError):
            render_component("pa4129_missing")
    finally:
        _COMPONENT_REGISTRY.pop("pa4129_badge", None)


# --- introspection -------------------------------------------------------


class InventoryLoginView(LiveView):
    exposure_policy = "explicit"
    login_required = True
    permission_required = ["app.view_thing"]
    template = "<div dj-root></div>"


class InventoryOpenView(LiveView):
    exposure_policy = "explicit"
    template = "<div dj-root></div>"


def test_live_views_lists_project_views_and_not_framework_ones():
    from djust.introspection import live_views

    found = live_views()
    assert InventoryLoginView in found and InventoryOpenView in found
    assert LiveView not in found
    assert all(not c.__module__.startswith("djust.") or "test" in c.__module__ for c in found)
    assert found == sorted(found, key=lambda c: (c.__module__, c.__qualname__))
    assert InventoryLoginView in live_views(app_label="djust")
    assert InventoryLoginView not in live_views(app_label="nosuchapp")


def test_view_auth_info_matches_the_audit_reading():
    from djust.introspection import view_auth_info
    from djust.management.commands.djust_audit import _extract_auth_info

    info = view_auth_info(InventoryLoginView)
    assert info == {"login_required": True, "permission_required": ["app.view_thing"]}
    assert info == _extract_auth_info(InventoryLoginView)
    assert view_auth_info(InventoryOpenView) == {}
