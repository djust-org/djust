"""#3211: an explicit view's event is committed, never saved best-effort.

Before #3211 an explicit view that also set the legacy ``enable_state_snapshot``
flag had its event save routed to a best-effort save, which logged a failed or
slow save and acknowledged the turn anyway, bypassing ADR-038 E3. 2.0 removed
the flag and that save. What these tests keep: every event route of an explicit
view (the view's own handler, a component declaration's output handler, a
component's ``send_parent``) commits the declared state before it answers, and
a failed commit withholds the success frame.

The views here pass the real ``_validate_exposure_configuration``: no staged
construction bypass.
"""

import asyncio
import json

import pytest
from asgiref.sync import sync_to_async
from django.contrib.sessions.backends.db import SessionStore
from django.test import override_settings

from djust import LiveView, event_handler
from djust._exposure_sessions import server_state_adapter
from djust.components._interactive import DropdownMenu
from djust.components.descriptors.base import LiveComponent as DescriptorComponent
from djust.decorators import state
from djust.runtime import ViewRuntime
from djust.tests.test_exposure_runtime import RuntimeView, make_request
from djust.tests.test_runtime_state_save_tt_1894 import MockTransport

pytestmark = [pytest.mark.asyncio, pytest.mark.django_db(transaction=True)]

SUCCESS_FRAMES = {"patch", "html_update", "noop"}


class CommittedView(RuntimeView):
    """An explicit view with server-persisted fields."""

    count = state(0, persist="server")
    hidden = state("SERVER_SENTINEL", persist="server")


class MenuPage(LiveView):
    """A component declaration whose output handler writes a declared field."""

    exposure_policy = "explicit"
    template = "<div dj-root>{{ menu }}<p>{{ result }}</p></div>"
    result = state("initial", persist="server")
    menu = DropdownMenu(label="Actions", items=[{"label": "Edit", "value": "edit"}])

    @menu.on.selected
    def menu_selected(self, component: DropdownMenu, value: str) -> None:
        self.result = "picked:" + value


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
    assert not transport.errors, transport.errors
    assert runtime.view_instance is not None
    transport.sent.clear()
    return runtime, transport, request


async def stored(view, key):
    fresh = await sync_to_async(make_request)(key)
    adapter = await sync_to_async(server_state_adapter)(view, fresh)
    return await adapter.aload()


@pytest.fixture
def failing_store(monkeypatch):
    """Every session write fails, sync and async alike."""

    def arm():
        def save(self, *args, **kwargs):
            raise OSError("STORE_SENTINEL")

        async def asave(self, *args, **kwargs):
            raise OSError("STORE_SENTINEL")

        monkeypatch.setattr(SessionStore, "save", save)
        monkeypatch.setattr(SessionStore, "asave", asave)

    return arm


def test_the_views_pass_the_real_configuration_check():
    CommittedView()._validate_exposure_configuration()
    MenuPage()._validate_exposure_configuration()


async def test_an_explicit_failed_save_is_not_acked(failing_store):
    """The #3206 review probe (``..._failed_save_still_acks``), now inverted:
    a failed save withholds the success frame, as for any explicit view."""
    runtime, transport, _ = await mount(CommittedView)
    failing_store()
    await runtime.dispatch_event({"type": "event", "event": "increment", "params": {}})

    assert not [f for f in transport.sent if f.get("type") in SUCCESS_FRAMES], transport.sent
    [error] = transport.errors
    assert error["code"] == "state_error"
    assert "STORE_SENTINEL" not in json.dumps(transport.errors)


async def test_an_explicit_save_goes_through_the_explicit_commit():
    runtime, transport, request = await mount(CommittedView)
    await runtime.dispatch_event({"type": "event", "event": "increment", "params": {}})

    assert not transport.errors, transport.errors
    assert any(f.get("type") in {"patch", "html_update"} for f in transport.sent)
    assert await stored(runtime.view_instance, request.session.session_key) == {
        "count": 6,
        "hidden": "SERVER_SENTINEL",
    }
    # Nothing legacy-shaped was written for the view.
    session = await sync_to_async(SessionStore)(request.session.session_key)
    keys = await sync_to_async(lambda: list(session.load()))()
    assert not [k for k in keys if k.startswith("liveview_")], keys


async def test_component_declaration_event_commits_explicit_state():
    """The component-declaration route (#1646 twin of the view route): the
    output handler's write to a declared field is an explicit commit."""
    runtime, transport, request = await mount(MenuPage)
    view = runtime.view_instance
    await runtime.dispatch_event(
        {
            "type": "event",
            "event": "select",
            "params": {"component_id": view.menu.component_id, "value": "edit"},
        }
    )

    assert not transport.errors, transport.errors
    assert view.result == "picked:edit"
    assert await stored(view, request.session.session_key) == {"result": "picked:edit"}


async def test_component_declaration_failed_save_is_not_acked(failing_store):
    runtime, transport, _ = await mount(MenuPage)
    view = runtime.view_instance
    failing_store()
    await runtime.dispatch_event(
        {
            "type": "event",
            "event": "select",
            "params": {"component_id": view.menu.component_id, "value": "edit"},
        }
    )

    assert not [f for f in transport.sent if f.get("type") in SUCCESS_FRAMES], transport.sent
    [error] = transport.errors
    assert error["code"] == "state_error"


# ------------------------------------------------------------------ #
# #3229 review I1/I2: every component event on an explicit view commits,
# and waiters see the event even when the commit fails.
# ------------------------------------------------------------------ #


class Pinger(DescriptorComponent):
    """A state-less component whose handler reaches the parent via send_parent."""

    template = "<b>ping</b>"

    def mount(self, **kwargs):
        self.n = 0

    def get_context_data(self):
        return {"n": self.n}

    @event_handler()
    def ping(self, **kwargs):
        self.n += 1
        self.send_parent("pinged")


class PingPage(LiveView):
    exposure_policy = "explicit"
    template = "<div dj-root>{{ pinger }}<span>{{ count }}</span></div>"
    count = state(0, persist="server")
    pinger = Pinger()

    def get_context_data(self, **kwargs):
        return super().get_context_data(count=self.count, **kwargs)

    def handle_component_event(self, component_id, event, data):
        self.count += 1


def _ping(view):
    return {
        "type": "event",
        "event": "ping",
        "params": {"component_id": view.pinger.component_id},
    }


async def test_send_parent_write_from_a_component_is_committed():
    runtime, transport, request = await mount(PingPage)
    view = runtime.view_instance
    await runtime.dispatch_event(_ping(view))

    assert not transport.errors, transport.errors
    assert view.count == 1
    assert await stored(view, request.session.session_key) == {"count": 1}


async def test_send_parent_write_with_a_failing_store_is_not_acked(failing_store):
    """The #3229 reviewer's probe: acked with no commit before the fix."""
    runtime, transport, _ = await mount(PingPage)
    view = runtime.view_instance
    failing_store()
    await runtime.dispatch_event(_ping(view))

    assert view.count == 1, "the parent's declared field was written"
    assert not [f for f in transport.sent if f.get("type") in SUCCESS_FRAMES], transport.sent
    [error] = transport.errors
    assert error["code"] == "state_error"


async def test_waiters_see_a_component_event_whose_save_fails(failing_store):
    """The view route notifies waiters before it commits; so does this one."""
    runtime, transport, _ = await mount(MenuPage)
    view = runtime.view_instance
    waiter = asyncio.ensure_future(view.wait_for_event("select", timeout=5))
    await asyncio.sleep(0)
    failing_store()
    await runtime.dispatch_event(
        {
            "type": "event",
            "event": "select",
            "params": {"component_id": view.menu.component_id, "value": "edit"},
        }
    )
    assert transport.errors and transport.errors[0]["code"] == "state_error"
    seen = await asyncio.wait_for(waiter, 1)
    assert seen.get("component_id") == view.menu.component_id
