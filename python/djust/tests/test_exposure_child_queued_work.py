"""Explicit child work queued at mount or by a parent turn (ADR-038 E3-3).

Before E3-3 the runtime drained an explicit child's queue only from a routed
child event: ``start_async`` in a child's ``mount()`` or queued on a child by a
parent handler sat on the child until its next routed event (and ran under that
event's batch), or never ran if no such event came. The work must instead run
under the child's own owned, authorized path as soon as the turn that queued it
has been acknowledged.
"""

import asyncio

import pytest
from asgiref.sync import sync_to_async
from django.contrib.sessions.backends.db import SessionStore

from djust import LiveView, event_handler
from djust._exposure_children import child_state_key
from djust.tests.test_exposure_child_events import EventChild, EventParent, mount

pytestmark = [pytest.mark.asyncio, pytest.mark.django_db(transaction=True)]

WORK_SENTINEL = "QUEUED_WORK_RESULT_SENTINEL"


@pytest.fixture(autouse=True)
def staged(monkeypatch, settings):
    monkeypatch.setattr(LiveView, "_validate_exposure_configuration", lambda self: None)
    settings.DJUST_LIVE_RENDER_ALLOWED_MODULES = [
        "djust.tests.test_exposure_child_events",
        __name__,
    ]


class LegacyQueuedChild(LiveView):
    sticky = True
    sticky_id = "legacy"
    template = "<div>Legacy={{ count }}</div>"

    def mount(self, request, **kwargs):
        self.count = 1

        def work():
            self.count = 7

        self.start_async(work, name="mount-job")

    @event_handler()
    def poke(self):
        pass

    def get_context_data(self, **kwargs):
        return super().get_context_data(count=self.count, **kwargs)


class LegacyQueuedParent(LiveView):
    template = (
        "<div dj-root>{% load live_tags %}{% live_render "
        f'"{__name__}.LegacyQueuedChild" sticky=True %}}</div>'
    )


def observe_completion(monkeypatch, transport):
    completed = asyncio.Event()
    original = transport.send

    async def send(frame):
        await original(frame)
        if frame.get("type") == "async_complete":
            completed.set()

    monkeypatch.setattr(transport, "send", send)
    return completed


def advertised_batches(frames):
    return {
        frame["async_batch"]
        for frame in frames
        if frame.get("async_batch") and frame.get("type") != "async_complete"
    }


async def drain(child):
    handles = tuple(getattr(child, "_async_task_handles", ()))
    assert handles, "Queued explicit child work was not dispatched to its owner"
    await asyncio.wait_for(asyncio.gather(*handles, return_exceptions=True), 3)


def queue_on_mount(monkeypatch, *, revoke=False):
    original = EventChild.mount
    seen = []

    def mount_child(self, request, **kwargs):
        original(self, request, **kwargs)

        def work():
            if revoke:
                session = SessionStore(self._djust_queue_session_key)
                session["child_denied"] = True
                session.save()
            self.count = 7
            return WORK_SENTINEL

        self._djust_queue_session_key = request.session.session_key
        self.start_async(work, name="mount-job")

    def allowed(self, request):
        seen.append(request)
        return not request.session.get("child_denied", False)

    monkeypatch.setattr(EventChild, "mount", mount_child)
    monkeypatch.setattr(EventChild, "check_permissions", allowed)
    return seen


async def test_mount_queued_child_work_arrives_as_owned_scoped_update(monkeypatch):
    seen = queue_on_mount(monkeypatch)
    runtime, transport, request = await mount()
    completed = observe_completion(monkeypatch, transport)
    root = runtime.view_instance
    child = root._get_child_view("menu")
    await drain(child)
    await asyncio.wait_for(completed.wait(), 2)

    updates = [f for f in transport.sent if f.get("type") == "embedded_update"]
    assert len(updates) == 1
    assert updates[0]["view_id"] == "menu"
    assert updates[0]["source"] == "async"
    assert "Count=7" in updates[0]["html"]
    assert WORK_SENTINEL not in repr(transport.sent)
    assert "SERVER_SENTINEL" not in repr(transport.sent)
    assert not transport.errors
    # Authority was reloaded for the completion: a fresh session object, not
    # the mount request's cached one.
    assert any(r.session is not request.session for r in seen[-1:])
    # The mount frame advertised no parent batch, and the completion's batch
    # is the child's own, not one any parent acknowledgement advertised.
    mount_frame = transport.sent[0]
    assert mount_frame["type"] == "mount"
    assert "async_batch" not in mount_frame and "async_pending" not in mount_frame
    completions = [f["async_batch"] for f in transport.sent if f.get("type") == "async_complete"]
    assert len(completions) == 1
    assert completions[0] not in advertised_batches(transport.sent)
    stored = await sync_to_async(SessionStore(request.session.session_key).load)()
    assert stored[child_state_key(request.path, ("menu",))]["state"]["values"]["count"] == 7


async def test_mount_queued_child_work_is_dropped_when_authority_is_revoked(monkeypatch):
    queue_on_mount(monkeypatch, revoke=True)
    runtime, transport, request = await mount()
    child = runtime.view_instance._get_child_view("menu")
    await drain(child)
    await asyncio.sleep(0)
    assert not any(f.get("type") == "embedded_update" for f in transport.sent)
    assert transport.errors and transport.errors[-1]["code"] == "async_error"
    assert WORK_SENTINEL not in repr(transport.sent)
    stored = await sync_to_async(SessionStore(request.session.session_key).load)()
    assert stored[child_state_key(request.path, ("menu",))]["state"]["values"]["count"] == 1


async def test_parent_turn_queued_child_work_runs_under_child_owner(monkeypatch):
    @event_handler()
    def kick(self):
        child = self._get_child_view("menu")

        def work():
            child.count = 8

        child.start_async(work, name="parent-job")

    monkeypatch.setattr(EventParent, "kick", kick, raising=False)
    runtime, transport, request = await mount()
    completed = observe_completion(monkeypatch, transport)
    child = runtime.view_instance._get_child_view("menu")
    await runtime.dispatch_event({"type": "event", "event": "kick", "params": {}, "ref": 5})
    ack = next(f for f in transport.sent if f.get("ref") == 5)
    assert "async_pending" not in ack and "async_batch" not in ack
    await drain(child)
    await asyncio.wait_for(completed.wait(), 2)
    updates = [f for f in transport.sent if f.get("type") == "embedded_update"]
    assert [(u["view_id"], u["source"]) for u in updates] == [("menu", "async")]
    assert "Count=8" in updates[0]["html"]
    completions = [f["async_batch"] for f in transport.sent if f.get("type") == "async_complete"]
    assert len(completions) == 1 and completions[0] not in advertised_batches(transport.sent)
    assert not transport.errors
    stored = await sync_to_async(SessionStore(request.session.session_key).load)()
    assert stored[child_state_key(request.path, ("menu",))]["state"]["values"]["count"] == 8


async def test_legacy_child_mount_queue_keeps_its_existing_behavior():
    """Legacy control: the pre-E3-3 behavior (work waits for a routed event)."""
    runtime, transport, _ = await mount(view_class=LegacyQueuedParent)
    child = runtime.view_instance._get_child_view("legacy")
    assert child is not None and "mount-job" in child._async_tasks
    await asyncio.sleep(0.05)
    assert "mount-job" in child._async_tasks
    assert not any(f.get("source") == "async" for f in transport.sent)
    await runtime.dispatch_event(
        {"type": "event", "event": "poke", "params": {"view_id": "legacy"}}
    )
    await asyncio.wait_for(asyncio.gather(*tuple(child._async_task_handles)), 3)
    assert child.count == 7
    assert any(
        f.get("type") == "embedded_update" and f.get("source") == "async" for f in transport.sent
    )
