"""A server push is stamped with its tenant, and a tenant view checks the stamp.

Defence in depth for the tenant namespaces of the push groups: the view-wide
group has no tenant in its name, and a group name can be wrong, so the message
carries the tenant it is for and a tenant view applies only a push stamped for
its own tenant or for none. A message without a stamp (an older process) and a
tenant-less publish (a broadcast) are applied as before.
"""

from __future__ import annotations

import asyncio

import pytest
from channels.layers import get_channel_layer
from django.test import override_settings

from djust import LiveView
from djust.decorators import event_handler
from djust.push import (
    _server_push_message,
    apush_to_view,
    push_scope_group_name,
    push_tenant_matches,
    push_to_view,
    view_group_name,
)
from djust.tenants.resolvers import TenantInfo

pytest.importorskip("channels")

from ._ws_frames import drain_extra, wait_until  # noqa: E402
from .test_push_scope_tenant_isolation import (  # noqa: E402
    ACME,
    CONFIG,
    GLOBEX,
    HOSTS,
    PLAIN,
    VIEW,
    _connect,
    _frames_until,
    _RoomView,
    _Records,
    _saw,
    _tenant_view,
)

pytestmark = pytest.mark.tenants

SETTINGS = dict(
    ALLOWED_HOSTS=HOSTS,
    DJUST_CONFIG=CONFIG,
    LIVEVIEW_ALLOWED_MODULES=[__name__, PLAIN.rsplit(".", 1)[0]],
)
# This module's own view: a tenant view that pushes view-wide from an event.
WIDE = f"{__name__}._WideView"


class _WideView(_RoomView):
    template = (
        '<div dj-root dj-view="djust.tests.test_push_tenant_stamp._WideView">'
        "{{ room }}|{% for m in messages %}[{{ m }}]{% endfor %}</div>"
    )

    @event_handler()
    def announce(self, text: str = "", **kwargs):
        push_to_view(WIDE, handler="handle_incoming", payload={"text": text, "src": "x"})

    @event_handler()
    def broadcast(self, text: str = "", **kwargs):
        push_to_view(
            WIDE, handler="handle_incoming", payload={"text": text, "src": "x"}, tenant=None
        )


# ---------------------------------------------------------------------------
# The stamp
# ---------------------------------------------------------------------------


@pytest.fixture
def messages(monkeypatch):
    sent = []

    class _Layer:
        async def group_send(self, group, message):
            sent.append((group, message))

    monkeypatch.setattr("djust.push.get_channel_layer", lambda: _Layer())
    return sent


def test_the_message_carries_the_tenant_it_is_for(messages):
    from djust.tenants.middleware import tenant_context

    push_to_view(VIEW, handler="h", scope="r1", tenant="acme")  # scoped, explicit
    push_to_view(VIEW, handler="h", tenant="acme")  # view-wide, explicit
    with tenant_context(TenantInfo("globex")):
        push_to_view(VIEW, handler="h")  # view-wide, bound tenant
        push_to_view(VIEW, handler="h", tenant=None)  # a broadcast
        push_to_view(PLAIN, handler="h")  # a view that is not tenant-scoped
    asyncio.run(apush_to_view(VIEW, handler="h", tenant="acme"))
    assert [(g, m.get("tenant_scope", "")) for g, m in messages] == [
        (push_scope_group_name(VIEW, "r1", "tenant:acme"), "tenant:acme"),
        (view_group_name(VIEW), "tenant:acme"),
        (view_group_name(VIEW), "tenant:globex"),
        (view_group_name(VIEW), ""),
        (view_group_name(PLAIN), ""),
        (view_group_name(VIEW), "tenant:acme"),
    ]


def test_a_view_wide_push_with_no_tenant_is_not_a_warning(messages):
    from djust import push

    push._WARNED_NO_TENANT.discard(VIEW)
    from djust.tenants.middleware import tenant_context

    with _Records() as log, tenant_context(None):
        push_to_view(VIEW, handler="h")
    assert not log.messages("has no tenant")


# ---------------------------------------------------------------------------
# The check
# ---------------------------------------------------------------------------


def _stamped(scope):
    return _server_push_message(None, "h", {}, "g", scope)


def test_a_tenant_view_applies_its_own_tenants_and_unstamped_pushes_only():
    acme = _tenant_view("acme")
    assert push_tenant_matches(acme, _stamped("tenant:acme"))
    assert not push_tenant_matches(acme, _stamped("tenant:globex"))
    assert push_tenant_matches(acme, _stamped(""))  # a broadcast
    old = _stamped("")
    assert "tenant_scope" not in old  # a message for no tenant has no key
    old.pop("tenant_scope", None)  # as from a process that predates the stamp
    assert push_tenant_matches(acme, old)


def test_a_view_that_is_not_tenant_scoped_applies_any_push():
    class _Plain(LiveView):
        pass

    assert push_tenant_matches(_Plain(), _stamped("tenant:acme"))


def test_a_tenant_view_with_no_resolved_tenant_applies_only_unstamped_pushes():
    view = _tenant_view(None)
    assert not push_tenant_matches(view, _stamped("tenant:acme"))
    assert push_tenant_matches(view, _stamped(""))


# ---------------------------------------------------------------------------
# End to end
# ---------------------------------------------------------------------------


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
@override_settings(**SETTINGS)
async def test_a_view_wide_push_from_a_tenant_applies_only_in_that_tenant():
    a1 = await _connect(WIDE, ACME, "r1")
    a2 = await _connect(WIDE, ACME, "r2")
    b = await _connect(WIDE, GLOBEX, "r1")
    try:
        await a1.send_json_to(
            {"type": "event", "event": "announce", "params": {"text": "ANNOUNCE-A"}, "ref": 1}
        )
        assert _saw(await _frames_until(a2, "ANNOUNCE-A"), "ANNOUNCE-A")
        # b handles its messages in order: its own tenant's push is the barrier.
        await apush_to_view(
            WIDE,
            handler="handle_incoming",
            payload={"text": "BARRIER", "src": "x"},
            tenant="globex",
        )
        frames = await _frames_until(b, "BARRIER")
        assert not _saw(frames, "ANNOUNCE-A")
        assert not _saw(await drain_extra(b), "ANNOUNCE-A")
    finally:
        for comm in (a1, a2, b):
            await comm.disconnect()


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
@override_settings(**SETTINGS)
async def test_a_broadcast_and_an_unstamped_message_apply_in_every_tenant():
    a = await _connect(WIDE, ACME, "r1")
    b = await _connect(WIDE, GLOBEX, "r1")
    try:
        # A tenant-less publisher (a Celery task) and ``tenant=None`` broadcast.
        from djust.tenants.middleware import tenant_context

        with tenant_context(None):  # a task has no tenant bound
            await apush_to_view(
                WIDE, handler="handle_incoming", payload={"text": "BROADCAST", "src": "x"}
            )
        for comm in (a, b):
            assert _saw(await _frames_until(comm, "BROADCAST"), "BROADCAST")
        # What a process that predates the stamp sends: no tenant_scope key.
        message = _server_push_message(
            None, "handle_incoming", {"text": "UNSTAMPED", "src": "x"}, view_group_name(WIDE)
        )
        assert "tenant_scope" not in message
        await get_channel_layer().group_send(view_group_name(WIDE), message)
        for comm in (a, b):
            assert _saw(await _frames_until(comm, "UNSTAMPED"), "UNSTAMPED")
    finally:
        for comm in (a, b):
            await comm.disconnect()


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
@override_settings(**SETTINGS)
async def test_a_push_stamped_for_another_tenant_is_dropped_even_in_the_right_group():
    """The stamp is a second layer: a message that reached a tenant's scope group
    by any route is still checked."""
    b = await _connect(WIDE, GLOBEX, "general")
    try:
        group = push_scope_group_name(WIDE, "general", "tenant:globex")
        wrong = _server_push_message(
            None, "handle_incoming", {"text": "WRONG-TENANT", "src": "x"}, group, "tenant:acme"
        )
        right = _server_push_message(
            None, "handle_incoming", {"text": "RIGHT-TENANT", "src": "x"}, group, "tenant:globex"
        )
        layer = get_channel_layer()
        await layer.group_send(group, wrong)
        await layer.group_send(group, right)
        frames = await _frames_until(b, "RIGHT-TENANT")
        assert not _saw(frames, "WRONG-TENANT")
    finally:
        await b.disconnect()


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
@override_settings(**SETTINGS)
async def test_a_view_that_is_not_tenant_scoped_applies_a_stamped_push():
    p = await _connect(PLAIN, GLOBEX, "general")
    try:
        group = push_scope_group_name(PLAIN, "general")
        message = _server_push_message(
            None, "handle_incoming", {"text": "STAMPED-TO-PLAIN", "src": "x"}, group, "tenant:acme"
        )
        await get_channel_layer().group_send(group, message)
        assert _saw(await _frames_until(p, "STAMPED-TO-PLAIN"), "STAMPED-TO-PLAIN")
    finally:
        await p.disconnect()


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
@override_settings(**SETTINGS)
async def test_a_queued_push_is_checked_again_when_the_view_changed_tenant(monkeypatch):
    """A push that found the view busy is queued and applied after the turn that
    held the lock. If that turn moved the view to another tenant
    (``set_tenant()``), the push stamped for the old tenant is dropped when the
    queue is drained, as it would have been on arrival."""
    from djust.websocket import LiveViewConsumer

    consumers = []
    connect = LiveViewConsumer.connect

    async def _recording_connect(self, *args, **kwargs):
        consumers.append(self)
        return await connect(self, *args, **kwargs)

    monkeypatch.setattr(LiveViewConsumer, "connect", _recording_connect)
    a = await _connect(WIDE, ACME, "r1")
    try:
        (consumer,) = consumers
        view = consumer.view_instance
        # As an event turn holds them.
        await consumer._render_lock.acquire()
        consumer._processing_user_event = True
        try:
            await apush_to_view(
                WIDE,
                handler="handle_incoming",
                payload={"text": "OLD-TENANT", "src": "x"},
                scope="r1",
                tenant="acme",
            )
            await apush_to_view(
                WIDE,
                handler="handle_incoming",
                payload={"text": "FOR-EVERY-TENANT", "src": "x"},
                tenant=None,
            )
            await wait_until(lambda: len(consumer._deferred_pushes) == 2, what="two deferrals")
            # The turn moves the view to another tenant, then ends.
            view.set_tenant("globex")
        finally:
            consumer._processing_user_event = False
            consumer._render_lock.release()
        frames = await _frames_until(a, "FOR-EVERY-TENANT")
        assert not _saw(frames, "OLD-TENANT")
        assert not _saw(await drain_extra(a), "OLD-TENANT")
        assert view.messages == ["x:FOR-EVERY-TENANT"]
    finally:
        await a.disconnect()
