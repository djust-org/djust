"""Removal then re-addition of an explicit child in the same slot (ADR-038 E3-7).

Builds on ``test_parent_render_removes_omitted_child_and_its_stored_state``:
render, remove, render again. The slot gets a fresh mount, the pruned envelope
is not resurrected, and the disposed instance cannot re-register.
"""

import pytest
from asgiref.sync import sync_to_async
from django.contrib.sessions.backends.db import SessionStore

from djust import LiveView, event_handler
from djust._exposure_children import child_state_key
from djust.decorators import state
from djust.tests.test_exposure_child_events import EventChild, mount

pytestmark = [pytest.mark.asyncio, pytest.mark.django_db(transaction=True)]

MODULE = __name__


class ToggleParent(LiveView):
    exposure_policy = "explicit"
    show_child = state(True, persist="server")
    template = (
        "<div dj-root>{% load live_tags %}{% if show_child %}{% live_render "
        '"djust.tests.test_exposure_child_events.EventChild" sticky=True object_id=1 %}'
        "{% endif %}</div>"
    )

    def get_context_data(self, **kwargs):
        return super().get_context_data(show_child=self.show_child, **kwargs)

    @event_handler()
    def toggle(self):
        self.show_child = not self.show_child


class LegacyToggleChild(LiveView):
    sticky = True
    sticky_id = "legacy-menu"
    template = "<div>Legacy={{ count }}</div>"

    def mount(self, request, **kwargs):
        self.count = 1

    def get_context_data(self, **kwargs):
        return super().get_context_data(count=self.count, **kwargs)


class LegacyToggleParent(LiveView):
    template = (
        "<div dj-root>{% load live_tags %}{% if show_child %}{% live_render "
        f'"{MODULE}.LegacyToggleChild" sticky=True %}}'
        "{% endif %}</div>"
    )

    def mount(self, request, **kwargs):
        self.show_child = True

    def get_context_data(self, **kwargs):
        return super().get_context_data(show_child=self.show_child, **kwargs)

    @event_handler()
    def toggle(self):
        self.show_child = not self.show_child


@pytest.fixture(autouse=True)
def staged(monkeypatch, settings):
    monkeypatch.setattr(LiveView, "_validate_exposure_configuration", lambda self: None)
    settings.DJUST_LIVE_RENDER_ALLOWED_MODULES = [MODULE, "djust.tests.test_exposure_child_events"]


async def load(request):
    return await sync_to_async(SessionStore(request.session.session_key).load)()


async def toggle(runtime):
    await runtime.dispatch_event({"type": "event", "event": "toggle", "params": {}})


async def test_readded_slot_gets_fresh_mount_without_resurrecting_state(monkeypatch):
    mounts = []
    original = EventChild.mount

    def counted(self, request, **kwargs):
        mounts.append(self)
        original(self, request, **kwargs)

    monkeypatch.setattr(EventChild, "mount", counted)
    runtime, transport, request = await mount(view_class=ToggleParent)
    root = runtime.view_instance
    key = child_state_key(request.path, ("menu",))
    old = root._get_child_view("menu")
    await runtime.dispatch_event(
        {"type": "event", "event": "increment", "params": {"view_id": "menu"}}
    )
    assert old.count == 2 and (await load(request))[key]["state"]["values"]["count"] == 2

    await toggle(runtime)  # remove
    assert not transport.errors, transport.errors
    assert root._get_child_view("menu") is None and old._djust_child_disposed
    assert key not in await load(request)

    await toggle(runtime)  # re-add in the same slot
    assert not transport.errors, transport.errors
    new = root._get_child_view("menu")
    assert new is not None and new is not old
    assert mounts == [old, new]
    assert new.count == 1
    assert (await load(request))[key]["state"]["values"]["count"] == 1

    # The disposed instance can neither re-register nor receive events.
    with pytest.raises(RuntimeError):
        root._register_child("menu-old", old)
    assert root._get_child_view("menu") is new
    await runtime.dispatch_event(
        {"type": "event", "event": "increment", "params": {"view_id": "menu"}}
    )
    assert (old.count, new.count) == (2, 2)
    assert (await load(request))[key]["state"]["values"]["count"] == 2


async def test_readded_slot_after_reconnect_does_not_restore_pruned_envelope():
    runtime, transport, request = await mount(view_class=ToggleParent)
    await runtime.dispatch_event(
        {"type": "event", "event": "increment", "params": {"view_id": "menu"}}
    )
    await toggle(runtime)
    assert child_state_key(request.path, ("menu",)) not in await load(request)
    # A new connection with the slot shown again mounts fresh.
    restored, _, _ = await mount(request.session.session_key, view_class=ToggleParent)
    assert restored.view_instance._get_child_view("menu") is None
    await toggle(restored)
    assert restored.view_instance._get_child_view("menu").count == 1


async def test_legacy_readd_is_unchanged():
    """Legacy control: a legacy sticky slot also remounts after removal."""
    runtime, transport, _ = await mount(view_class=LegacyToggleParent)
    root = runtime.view_instance
    old = root._get_child_view("legacy-menu")
    assert old is not None
    await toggle(runtime)
    await toggle(runtime)
    assert not transport.errors, transport.errors
    assert root._get_child_view("legacy-menu") is not None
