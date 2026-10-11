"""Server-originated turns run with the view's tenant bound.

A push hook (``handle_*`` called by ``push_to_view``), ``handle_info`` (a
database NOTIFY) and the ``start_async`` work they queue are application code,
and an event handler runs with its own tenant bound. They used to run with none
bound, so a tenant manager inside them saw no tenant, and a hook that relayed
with ``push_to_view(scope=...)`` published into the wrong tenant namespace.
"""

from __future__ import annotations

import pytest
from channels.layers import get_channel_layer
from django.test import override_settings

from djust import LiveView
from djust.config import config as djust_config
from djust.push import apush_to_view, push_to_view
from djust.tenants.mixin import TenantMixin

pytest.importorskip("channels")

from ._ws_frames import drain_extra  # noqa: E402
from .test_push_scope_tenant_isolation import (  # noqa: E402
    ACME,
    CONFIG,
    GLOBEX,
    HOSTS,
    _connect,
    _frames_until,
    _saw,
)

pytestmark = pytest.mark.tenants

VIEW = f"{__name__}._RelayView"
SEEN: list = []


def _bound(where):
    from djust.tenants.middleware import get_current_tenant

    tenant = get_current_tenant()
    SEEN.append((where, tenant.id if tenant else None))


class _RelayView(TenantMixin, LiveView):
    template = (
        '<div dj-root dj-view="djust.tests.test_push_turn_tenant_context._RelayView">'
        "{{ room }}|{% for m in notes %}[{{ m }}]{% endfor %}</div>"
    )
    _listen_channels = frozenset({"relay_probe"})

    def mount(self, request, **kwargs):
        self.room = request.GET.get("room", "general")
        self.notes = []
        self.push_scope = self.room

    def handle_relay(self, text: str = "", **kwargs):
        """A push hook that publishes on, without naming a tenant."""
        _bound("push-hook")
        self.start_async(self._work, name="from_push")
        push_to_view(VIEW, handler="handle_note", payload={"text": text}, scope=self.room)

    def handle_info(self, message):
        _bound("handle_info")
        push_to_view(VIEW, handler="handle_note", payload={"text": "FROM-NOTIFY"}, scope=self.room)

    def handle_note(self, text: str = "", **kwargs):
        self.notes = self.notes + [text]

    def _work(self):
        _bound("async-from-push")


@pytest.fixture(params=[None, 2], ids=["stock", "pool"])
def pool(request, monkeypatch):
    from djust import worker_pool

    monkeypatch.setitem(djust_config._config, "worker_threads", request.param)
    monkeypatch.setattr(worker_pool, "_pool", [])
    SEEN.clear()
    yield request.param
    SEEN.clear()


SETTINGS = dict(ALLOWED_HOSTS=HOSTS, DJUST_CONFIG=CONFIG, LIVEVIEW_ALLOWED_MODULES=[__name__])


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
@override_settings(**SETTINGS)
async def test_a_push_hook_and_its_async_work_run_with_the_views_tenant(pool):
    a = await _connect(VIEW, ACME, "general")
    try:
        await apush_to_view(
            VIEW, handler="handle_relay", payload={"text": "HOOK"}, scope="general", tenant="acme"
        )
        await _frames_until(a, "HOOK")
        # The hook's start_async work runs once the turn's lock is released.
        for _ in range(100):
            if ("async-from-push", "acme") in SEEN:
                break
            await __import__("asyncio").sleep(0.05)
        assert ("push-hook", "acme") in SEEN
        assert ("async-from-push", "acme") in SEEN
    finally:
        await a.disconnect()


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
@override_settings(**SETTINGS)
async def test_a_hook_that_relays_reaches_its_own_tenants_sessions_only(pool):
    a1 = await _connect(VIEW, ACME, "general")
    a2 = await _connect(VIEW, ACME, "general")
    b1 = await _connect(VIEW, GLOBEX, "general")
    try:
        await apush_to_view(
            VIEW,
            handler="handle_relay",
            payload={"text": "RELAYED"},
            scope="general",
            tenant="acme",
        )
        # Both of the tenant's sessions run the hook, so each relays; both get it.
        assert _saw(await _frames_until(a1, "RELAYED"), "RELAYED")
        assert _saw(await _frames_until(a2, "RELAYED"), "RELAYED")
        # The other tenant's session, in the same room string, gets neither the
        # push nor the relays; its own tenant's push is the barrier.
        await apush_to_view(
            VIEW,
            handler="handle_note",
            payload={"text": "BARRIER"},
            scope="general",
            tenant="globex",
        )
        frames = await _frames_until(b1, "BARRIER")
        assert not _saw(frames, "RELAYED")
        assert not _saw(await drain_extra(b1), "RELAYED")
    finally:
        for comm in (a1, a2, b1):
            await comm.disconnect()


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
@override_settings(**SETTINGS)
async def test_handle_info_runs_with_the_views_tenant_and_can_relay(pool):
    a1 = await _connect(VIEW, ACME, "general")
    b1 = await _connect(VIEW, GLOBEX, "general")
    try:
        await get_channel_layer().group_send(
            "djust_db_notify_relay_probe",
            {"type": "db_notify", "channel": "relay_probe", "payload": {}},
        )
        frames_a = await _frames_until(a1, "FROM-NOTIFY")
        frames_b = await _frames_until(b1, "FROM-NOTIFY")
        assert ("handle_info", "acme") in SEEN
        assert ("handle_info", "globex") in SEEN
        # Each tenant's relay stayed in its own tenant: the session rendered one
        # note, from its own handle_info, not two.
        for frames, comm in ((frames_a, a1), (frames_b, b1)):
            frames = frames + await drain_extra(comm)
            assert _saw(frames, "[FROM-NOTIFY]")
            assert not _saw(frames, "[FROM-NOTIFY][FROM-NOTIFY]")
    finally:
        for comm in (a1, b1):
            await comm.disconnect()
