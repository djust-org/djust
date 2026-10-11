"""Scoped server push is tenant-scoped (``push_scope`` group names include the tenant).

A ``push_scope`` room or document id is a plain string, and tenants choose the
same ones (``general``, ``7``). The group of a scope is named from the view
path, the tenant and the scope, on the sessions' side
(``sync_push_scope_groups``) and on the publishing side
(``push_to_view(tenant=...)``, defaulting to the tenant bound to the running
context), so each tenant has its own ``general``. A view that is not
tenant-scoped keeps its group names.
"""

from __future__ import annotations

import asyncio
import hashlib
import json

import pytest
from asgiref.sync import sync_to_async
from django.test import override_settings

from djust import LiveView
from djust.decorators import event_handler
from djust.push import (
    AMBIENT_TENANT,
    apush_to_view,
    push_scope_group_name,
    push_to_view,
    sync_push_scope_groups,
    view_group_name,
)
from djust.tenants.mixin import TenantMixin
from djust.tenants.resolvers import TenantInfo

pytest.importorskip("channels")

from ._ws_frames import drain_extra, receive_until  # noqa: E402

pytestmark = pytest.mark.tenants

VIEW = f"{__name__}._RoomView"
CONFIG = {"TENANT_RESOLVER": "subdomain"}
HOSTS = [".example.com", "testserver"]
ACME, GLOBEX = "acme.example.com", "globex.example.com"


class _RoomView(TenantMixin, LiveView):
    template = (
        '<div dj-root dj-view="djust.tests.test_push_scope_tenant_isolation._RoomView">'
        "{{ room }}|{% for m in messages %}[{{ m }}]{% endfor %}</div>"
    )

    def mount(self, request, **kwargs):
        self.room = request.GET.get("room", "general")
        self.messages = []
        self.push_scope = self.room

    @event_handler()
    def say(self, text: str = "", **kwargs):
        # No ``tenant=``: the handler runs with its own tenant bound.
        push_to_view(
            VIEW,
            handler="handle_incoming",
            payload={"text": text, "src": self._tenant.id},
            scope=self.room,
        )

    @event_handler()
    def switch(self, tenant: str = "", **kwargs):
        self.set_tenant(tenant)

    @event_handler()
    def relay_to_plain(self, text: str = "", **kwargs):
        # A tenant view's handler pushing to a view that is not tenant-scoped.
        push_to_view(PLAIN, handler="handle_incoming", payload={"text": text}, scope=self.room)

    def handle_incoming(self, text: str = "", src: str = "", **kwargs):
        self.messages = self.messages + [f"{src}:{text}"]


class _OverridingView(_RoomView):
    """Changes the scope of its saved state; its push namespace stays its tenant's."""

    def get_state_key_prefix(self):
        return "org:9"


class _PlainRoomView(LiveView):
    """Not tenant-scoped: group names and delivery are what they were."""

    template = (
        '<div dj-root dj-view="djust.tests.test_push_scope_tenant_isolation._PlainRoomView">'
        "{{ room }}|{% for m in messages %}[{{ m }}]{% endfor %}</div>"
    )

    def mount(self, request, **kwargs):
        self.room = request.GET.get("room", "general")
        self.messages = []
        self.push_scope = self.room

    def handle_incoming(self, text: str = "", src: str = "", **kwargs):
        self.messages = self.messages + [f"{src}:{text}"]


PLAIN = f"{__name__}._PlainRoomView"


# ---------------------------------------------------------------------------
# Group names
# ---------------------------------------------------------------------------


def test_unqualified_group_name_is_unchanged():
    legacy = hashlib.sha256(f"{VIEW}\x00general".encode()).hexdigest()[:40]
    assert push_scope_group_name(VIEW, "general") == f"djust_scope_{legacy}"
    assert push_scope_group_name(VIEW, "general", "") == f"djust_scope_{legacy}"


def test_the_tenant_is_part_of_the_group_name():
    acme = push_scope_group_name(VIEW, "general", "tenant:acme")
    globex = push_scope_group_name(VIEW, "general", "tenant:globex")
    assert acme != globex
    assert acme == push_scope_group_name(VIEW, "general", "tenant:acme")
    assert push_scope_group_name(VIEW, "7", "tenant:acme") == push_scope_group_name(
        VIEW, 7, "tenant:acme"
    )
    for name in (acme, globex):
        assert name != push_scope_group_name(VIEW, "general")
        assert name != view_group_name(VIEW)


@pytest.mark.parametrize(
    "scope",
    [
        "general\x00tenant:acme",
        'general", "tenant:acme',
        'general", "tenant:acme", "x',
        "tenant:acme",
        "general\\",
    ],
)
def test_no_scope_string_spells_another_tenants_group(scope):
    """Whatever a scope string contains, it names a group in its own tenant's namespace."""
    target = push_scope_group_name(VIEW, "general", "tenant:acme")
    assert push_scope_group_name(VIEW, scope, "tenant:globex") != target
    assert push_scope_group_name(VIEW, scope, "") != target
    assert push_scope_group_name(VIEW, scope, "tenant:acme") != target


def test_tenant_scope_for_id_is_the_state_key_prefix():
    from djust._tenant_state import tenant_scope_for_id

    for tenant_id in ("acme", "a:b", "x/y z", 42):
        view = _RoomView()
        view._tenant = TenantInfo(str(tenant_id))
        assert tenant_scope_for_id(tenant_id) == view.get_state_key_prefix()


# ---------------------------------------------------------------------------
# Publishing side
# ---------------------------------------------------------------------------


class _Records:
    """Warnings ``djust.push`` logged, as a context manager.

    Attached to the logger itself and re-enabled for the block: another test in
    the run may have switched off propagation of the ``djust`` logger or
    disabled the existing loggers (``dictConfig``), which ``caplog`` (a root
    handler) cannot see past.
    """

    def __init__(self):
        import logging

        self.records = []
        outer = self

        class _Handler(logging.Handler):
            def emit(self, record):
                outer.records.append(record)

        self.handler = _Handler(level=logging.WARNING)
        self.logger = logging.getLogger("djust.push")

    def __enter__(self):
        self._saved = (self.logger.level, self.logger.disabled)
        self.logger.addHandler(self.handler)
        self.logger.setLevel("WARNING")
        self.logger.disabled = False
        return self

    def __exit__(self, *exc):
        self.logger.removeHandler(self.handler)
        self.logger.setLevel(self._saved[0])
        self.logger.disabled = self._saved[1]

    def messages(self, needle):
        return [r.getMessage() for r in self.records if needle in r.getMessage()]


@pytest.fixture
def push_log():
    with _Records() as log:
        yield log


@pytest.fixture
def sent(monkeypatch):
    sent = []

    class _Layer:
        async def group_send(self, group, message):
            sent.append(group)

    monkeypatch.setattr("djust.push.get_channel_layer", lambda: _Layer())
    return sent


def test_push_to_view_names_the_tenant(sent):
    push_to_view(VIEW, handler="handle_incoming", scope="r1", tenant="acme")
    push_to_view(VIEW, handler="handle_incoming", scope="r1", tenant=TenantInfo("acme"))
    push_to_view(VIEW, handler="handle_incoming", scope="r1", tenant="globex")
    push_to_view(VIEW, handler="handle_incoming", scope="r1", tenant=None)
    asyncio.run(apush_to_view(VIEW, handler="handle_incoming", scope=5, tenant="acme"))
    assert sent == [
        push_scope_group_name(VIEW, "r1", "tenant:acme"),
        push_scope_group_name(VIEW, "r1", "tenant:acme"),
        push_scope_group_name(VIEW, "r1", "tenant:globex"),
        push_scope_group_name(VIEW, "r1"),
        push_scope_group_name(VIEW, 5, "tenant:acme"),
    ]


def test_push_to_view_defaults_to_the_bound_tenant(sent):
    from djust.tenants.middleware import tenant_context

    assert AMBIENT_TENANT is not None
    with tenant_context(None):  # a test earlier in the run may leave one bound
        push_to_view(VIEW, handler="handle_incoming", scope="r1")
    with tenant_context(TenantInfo("acme")):
        push_to_view(VIEW, handler="handle_incoming", scope="r1")
        # An explicit tenant wins over the bound one, and None means none.
        push_to_view(VIEW, handler="handle_incoming", scope="r1", tenant="globex")
        push_to_view(VIEW, handler="handle_incoming", scope="r1", tenant=None)
        # A push without a scope is the view-wide push, tenant or not.
        push_to_view(VIEW, handler="handle_incoming")
    assert sent == [
        push_scope_group_name(VIEW, "r1"),
        push_scope_group_name(VIEW, "r1", "tenant:acme"),
        push_scope_group_name(VIEW, "r1", "tenant:globex"),
        push_scope_group_name(VIEW, "r1"),
        view_group_name(VIEW),
    ]


# ---------------------------------------------------------------------------
# Subscribing side
# ---------------------------------------------------------------------------


class _FakeLayer:
    def __init__(self):
        self.members = set()

    async def group_add(self, group, channel):
        self.members.add(group)

    async def group_discard(self, group, channel):
        self.members.discard(group)


class _FakeConsumer:
    def __init__(self):
        self.channel_layer = _FakeLayer()
        self.channel_name = "chan"
        self._view_path = VIEW
        self._push_scope_groups = {}


def _tenant_view(tenant_id):
    view = _RoomView()
    view.push_scope = "general"
    if tenant_id is not None:
        view._tenant = TenantInfo(tenant_id)
    return view


def test_a_tenant_view_with_no_resolved_tenant_joins_no_scoped_group():
    consumer = _FakeConsumer()
    asyncio.run(sync_push_scope_groups(consumer, _tenant_view(None)))
    assert consumer.channel_layer.members == set()
    assert consumer._push_scope_groups == {}


def test_a_tenant_view_joins_its_tenants_group_and_follows_a_tenant_change():
    consumer = _FakeConsumer()
    view = _tenant_view("acme")
    asyncio.run(sync_push_scope_groups(consumer, view))
    acme = push_scope_group_name(VIEW, "general", "tenant:acme")
    assert consumer.channel_layer.members == {acme}
    assert consumer._push_scope_groups == {"general": acme}

    view.set_tenant("globex")
    asyncio.run(sync_push_scope_groups(consumer, view))
    globex = push_scope_group_name(VIEW, "general", "tenant:globex")
    assert consumer.channel_layer.members == {globex}
    assert consumer._push_scope_groups == {"general": globex}


def test_a_view_that_is_not_tenant_scoped_keeps_its_group():
    class _Plain(LiveView):
        push_scope = "general"

    consumer = _FakeConsumer()
    asyncio.run(sync_push_scope_groups(consumer, _Plain()))
    assert consumer.channel_layer.members == {push_scope_group_name(VIEW, "general")}


# ---------------------------------------------------------------------------
# End to end: real consumers, the in-memory channel layer, two tenants
# ---------------------------------------------------------------------------


async def _connect(view, host, room):
    from channels.testing import WebsocketCommunicator
    from django.contrib.sessions.backends.db import SessionStore

    from djust.websocket import LiveViewConsumer

    def _create_session():
        s = SessionStore()
        s.create()
        return s.session_key

    key = await sync_to_async(_create_session)()

    class _ScopeSession:
        session_key = key

    comm = WebsocketCommunicator(
        LiveViewConsumer.as_asgi(), "/ws/", headers=[(b"host", host.encode())]
    )
    comm.scope["session"] = _ScopeSession()
    connected, _ = await comm.connect()
    assert connected
    await comm.receive_json_from(timeout=3)
    await comm.send_json_to({"type": "mount", "view": view, "url": f"/r/?room={room}"})
    for _ in range(8):
        frame = await comm.receive_json_from(timeout=3)
        if frame.get("type") == "mount":
            return comm
    raise AssertionError(f"no mount frame: {frame!r}")


def _text(frame):
    return json.dumps(frame.get("patches")) + json.dumps(frame.get("html"))


async def _frames_until(comm, marker):
    return await receive_until(
        comm,
        lambda fs: any(
            f.get("type") in ("patch", "html_update") and marker in _text(f) for f in fs
        ),
        what=f"a frame containing {marker!r}",
    )


def _saw(frames, marker):
    return any(marker in _text(f) for f in frames)


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
@override_settings(ALLOWED_HOSTS=HOSTS, DJUST_CONFIG=CONFIG, LIVEVIEW_ALLOWED_MODULES=[__name__])
async def test_a_push_for_one_tenants_room_does_not_reach_another_tenants_room():
    a1 = await _connect(VIEW, ACME, "general")
    a2 = await _connect(VIEW, ACME, "general")  # the same tenant: control
    b1 = await _connect(VIEW, GLOBEX, "general")  # another tenant, the SAME room string
    try:
        await a1.send_json_to(
            {"type": "event", "event": "say", "params": {"text": "ACME-NOTE"}, "ref": 1}
        )
        # The control: tenant A's other session gets it.
        assert _saw(await _frames_until(a2, "ACME-NOTE"), "ACME-NOTE")
        # a1 has its own event response, so its handler (and the push it
        # sent) is done. B handles its channel messages in order: a push of
        # its own tenant is the barrier, and A's push, had it been delivered,
        # arrived first.
        await receive_until(a1, lambda fs: bool(fs), what="a1's event response")
        await apush_to_view(
            VIEW,
            handler="handle_incoming",
            payload={"text": "BARRIER", "src": "ops"},
            scope="general",
            tenant="globex",
        )
        frames = await _frames_until(b1, "BARRIER")
        assert not _saw(frames, "ACME-NOTE"), "tenant A's push was applied in tenant B's view"
        assert not _saw(await drain_extra(b1), "ACME-NOTE")
    finally:
        for comm in (a1, a2, b1):
            await comm.disconnect()


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
@override_settings(ALLOWED_HOSTS=HOSTS, DJUST_CONFIG=CONFIG, LIVEVIEW_ALLOWED_MODULES=[__name__])
async def test_a_task_publishes_to_one_tenant_with_tenant():
    a = await _connect(VIEW, ACME, "general")
    b = await _connect(VIEW, GLOBEX, "general")
    try:
        await apush_to_view(
            VIEW,
            handler="handle_incoming",
            payload={"text": "FOR-GLOBEX", "src": "celery"},
            scope="general",
            tenant="globex",
        )
        assert _saw(await _frames_until(b, "FOR-GLOBEX"), "FOR-GLOBEX")
        # A tenant-less publish reaches no tenant view (fail closed); the next
        # tenant-addressed one is the barrier for both sessions.
        await apush_to_view(
            VIEW,
            handler="handle_incoming",
            payload={"text": "NO-TENANT", "src": "celery"},
            scope="general",
            tenant=None,
        )
        await apush_to_view(
            VIEW,
            handler="handle_incoming",
            payload={"text": "BARRIER-A", "src": "celery"},
            scope="general",
            tenant="acme",
        )
        frames_a = await _frames_until(a, "BARRIER-A")
        assert not _saw(frames_a, "FOR-GLOBEX")
        assert not _saw(frames_a, "NO-TENANT")
    finally:
        for comm in (a, b):
            await comm.disconnect()


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
@override_settings(ALLOWED_HOSTS=HOSTS, DJUST_CONFIG=CONFIG, LIVEVIEW_ALLOWED_MODULES=[__name__])
async def test_a_view_that_is_not_tenant_scoped_still_gets_an_unqualified_push():
    p = await _connect(PLAIN, ACME, "general")
    try:
        await apush_to_view(
            PLAIN,
            handler="handle_incoming",
            payload={"text": "PLAIN-PUSH", "src": "celery"},
            scope="general",
        )
        assert _saw(await _frames_until(p, "PLAIN-PUSH"), "PLAIN-PUSH")
    finally:
        await p.disconnect()


# ---------------------------------------------------------------------------
# The target decides whether the tenant matters; one derivation; logging
# ---------------------------------------------------------------------------


def test_a_push_to_a_view_that_is_not_tenant_scoped_is_unqualified(sent):
    from djust.tenants.middleware import tenant_context

    with tenant_context(TenantInfo("acme")):
        push_to_view(PLAIN, handler="handle_incoming", scope="r1")  # bound tenant
        push_to_view(PLAIN, handler="handle_incoming", scope="r1", tenant="globex")
    assert sent == [push_scope_group_name(PLAIN, "r1")] * 2


def test_a_view_this_process_has_not_imported_follows_the_tenant(sent):
    unknown = "not_imported_anywhere.views.RoomView"
    push_to_view(unknown, handler="handle_incoming", scope="r1", tenant="acme")
    push_to_view(unknown, handler="handle_incoming", scope="r1", tenant=None)
    assert sent == [
        push_scope_group_name(unknown, "r1", "tenant:acme"),
        push_scope_group_name(unknown, "r1"),
    ]


def test_a_tenant_less_push_to_a_tenant_view_is_logged_once(sent, push_log):
    from djust import push

    from djust.tenants.middleware import tenant_context

    push._WARNED_NO_TENANT.discard(VIEW)
    with tenant_context(None):  # a test earlier in the run may leave one bound
        for _ in range(3):
            push_to_view(VIEW, handler="handle_incoming", scope="room-unique-value")
    push_to_view(VIEW, handler="handle_incoming", scope="r1", tenant="acme")
    warnings = push_log.messages("has no tenant")
    assert len(warnings) == 1
    assert "room-unique-value" not in warnings[0]  # value-free
    assert VIEW in warnings[0]


def test_an_unresolved_tenant_is_logged_once_until_it_resolves(push_log):
    consumer = _FakeConsumer()
    view = _tenant_view(None)
    for _ in range(3):
        asyncio.run(sync_push_scope_groups(consumer, view))
    assert len(push_log.messages("no resolved tenant")) == 1
    view._tenant = TenantInfo("acme")
    asyncio.run(sync_push_scope_groups(consumer, view))
    assert consumer.channel_layer.members == {push_scope_group_name(VIEW, "general", "tenant:acme")}
    view._tenant = None
    asyncio.run(sync_push_scope_groups(consumer, view))
    assert len(push_log.messages("no resolved tenant")) == 2


def test_overriding_the_state_key_prefix_does_not_move_the_push_namespace(sent):
    view = _OverridingView()
    view.push_scope = "general"
    view._tenant = TenantInfo("acme")
    assert view.get_state_key_prefix() == "org:9"
    consumer = _FakeConsumer()
    asyncio.run(sync_push_scope_groups(consumer, view))
    push_to_view(VIEW, handler="handle_incoming", scope="general", tenant="acme")
    assert consumer.channel_layer.members == {sent[0]}


def test_the_hook_is_not_called_on_the_loop():
    calls = []

    class _Counting(_RoomView):
        def get_state_key_prefix(self):
            calls.append(1)
            return super().get_state_key_prefix()

    view = _Counting()
    view.push_scope = "general"
    view._tenant = TenantInfo("acme")
    asyncio.run(sync_push_scope_groups(_FakeConsumer(), view))
    assert calls == []


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
@override_settings(ALLOWED_HOSTS=HOSTS, DJUST_CONFIG=CONFIG, LIVEVIEW_ALLOWED_MODULES=[__name__])
async def test_a_tenant_views_handler_reaches_a_view_that_is_not_tenant_scoped():
    t = await _connect(VIEW, ACME, "general")
    p = await _connect(PLAIN, GLOBEX, "general")
    try:
        await t.send_json_to(
            {"type": "event", "event": "relay_to_plain", "params": {"text": "TO-PLAIN"}, "ref": 1}
        )
        assert _saw(await _frames_until(p, "TO-PLAIN"), "TO-PLAIN")
    finally:
        for comm in (t, p):
            await comm.disconnect()


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
@override_settings(ALLOWED_HOSTS=HOSTS, DJUST_CONFIG=CONFIG, LIVEVIEW_ALLOWED_MODULES=[__name__])
async def test_a_handler_that_switches_tenant_moves_the_session_to_the_new_tenants_room():
    a = await _connect(VIEW, ACME, "general")
    try:
        await a.send_json_to(
            {"type": "event", "event": "switch", "params": {"tenant": "globex"}, "ref": 1}
        )
        await receive_until(a, lambda fs: bool(fs), what="the switch's response")
        for text, tenant in (("OLD-TENANT", "acme"), ("NEW-TENANT", "globex")):
            await apush_to_view(
                VIEW,
                handler="handle_incoming",
                payload={"text": text, "src": "ops"},
                scope="general",
                tenant=tenant,
            )
        frames = await _frames_until(a, "NEW-TENANT")
        assert not _saw(frames, "OLD-TENANT")
    finally:
        await a.disconnect()


def test_a_tenant_less_push_to_a_view_this_process_has_not_imported_is_logged_once(sent, push_log):
    from djust import push
    from djust.tenants.middleware import tenant_context

    unknown = "not_imported_anywhere.views.OtherView"
    push._WARNED_NO_TENANT.discard(unknown)
    with tenant_context(None):  # a test earlier in the run may leave one bound
        for _ in range(3):
            push_to_view(unknown, handler="handle_incoming", scope="room-unique-value")
        # Naming no tenant on purpose, or naming one, is not a mistake.
        push_to_view("not_imported_anywhere.views.ThirdView", handler="h", scope="r", tenant=None)
        push_to_view("not_imported_anywhere.views.ThirdView", handler="h", scope="r", tenant="acme")
    warnings = push_log.messages("has not imported")
    assert len(warnings) == 1
    assert unknown in warnings[0]
    assert "room-unique-value" not in warnings[0]  # value-free
    assert not push_log.messages("ThirdView")
