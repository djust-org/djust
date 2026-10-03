"""#2973 part 2: saved view state is keyed by tenant.

One browser session (a cookie shared across ``*.example.com``) that reaches
two tenants on the same URL used to share one state-backend entry: the key was
``<session>_liveview_<path>_t<hash>`` with no tenant in it, and
``TenantMixin.get_state_key_prefix()`` was never called.

These tests drive the real mount path (``WebsocketCommunicator`` ->
``LiveViewConsumer`` -> ``_initialize_rust_view`` -> the in-memory state
backend), with the tenant resolved from the handshake Host, and one shared
session id.

Decisions pinned here:

- A ``TenantMixin`` view stores its state under ``tenant:<id>:<session>_...``;
  tenant B on the same session and URL gets a cache MISS, tenant A a HIT.
- A view without the tenant hook keeps the unprefixed key (no reset for
  projects that do not use tenants).
- Fail closed: a tenant view with no resolved tenant reads and writes nothing,
  rather than using the unprefixed key every non-tenant view shares.
"""

from __future__ import annotations

import pytest
from asgiref.sync import sync_to_async
from django.test import override_settings

from djust import LiveView
from djust.state_backends import memory as memory_mod
from djust.tenants.mixin import TenantMixin

pytestmark = [pytest.mark.django_db, pytest.mark.tenants]

_ALLOWED = [".example.com", "testserver"]
_CONFIG = {"TENANT_RESOLVER": "subdomain"}
_URL = "/state-keys-2973/"


class _TenantView(TenantMixin, LiveView):
    template = '<div dj-root dj-id="0">tenant view</div>'

    def mount(self, request, **kwargs):
        self.seen = True


class _OptionalTenantView(TenantMixin, LiveView):
    tenant_required = False
    template = '<div dj-root dj-id="0">optional tenant view</div>'

    def mount(self, request, **kwargs):
        self.seen = True


class _PlainView(LiveView):
    template = '<div dj-root dj-id="0">plain view</div>'

    def mount(self, request, **kwargs):
        self.seen = True


class _ScopeSession:
    def __init__(self, key):
        self.session_key = key


def _new_session_key() -> str:
    from django.contrib.sessions.backends.db import SessionStore

    s = SessionStore()
    s.create()
    return s.session_key


async def _mount(view: str, session_key: str, host: str = "testserver") -> dict:
    from channels.testing import WebsocketCommunicator
    from djust.websocket import LiveViewConsumer

    communicator = WebsocketCommunicator(
        LiveViewConsumer.as_asgi(), "/ws/", headers=[(b"host", host.encode())]
    )
    communicator.scope["session"] = _ScopeSession(session_key)
    connected, _ = await communicator.connect()
    assert connected
    await communicator.receive_json_from(timeout=2)  # connect frame
    await communicator.send_json_to({"type": "mount", "view": f"{__name__}.{view}", "url": _URL})
    frame = None
    for _ in range(5):
        frame = await communicator.receive_json_from(timeout=3)
        if frame.get("type") in ("mount", "error", "navigate"):
            break
    await communicator.disconnect()
    assert frame is not None and frame.get("type") == "mount", f"mount failed: {frame!r}"
    return frame


@pytest.fixture
def backend_spy(monkeypatch):
    """The live in-memory backend plus a list of the keys ``get`` HIT."""
    from djust.state_backends.registry import get_backend

    backend = get_backend()
    assert isinstance(backend, memory_mod.InMemoryStateBackend)
    backend.delete_all()
    hits: list[str] = []
    real_get = memory_mod.InMemoryStateBackend.get

    def spying_get(self, key):
        result = real_get(self, key)
        if result is not None:
            hits.append(key)
        return result

    monkeypatch.setattr(memory_mod.InMemoryStateBackend, "get", spying_get)
    yield backend, hits
    backend.delete_all()


def _keys(backend) -> list[str]:
    return sorted(k for k in backend._cache if not k.startswith("__"))


@pytest.fixture
def tenant_settings():
    with override_settings(
        ALLOWED_HOSTS=_ALLOWED, DJUST_CONFIG=_CONFIG, LIVEVIEW_ALLOWED_MODULES=[__name__]
    ):
        yield


@pytest.mark.usefixtures("tenant_settings")
class TestTenantKeyedState:
    @pytest.mark.asyncio
    async def test_two_tenants_one_session_one_url_keep_separate_state(self, backend_spy):
        backend, hits = backend_spy
        session = await sync_to_async(_new_session_key)()

        await _mount("_TenantView", session, "acme.example.com")
        assert hits == []
        await _mount("_TenantView", session, "globex.example.com")
        assert hits == [], (
            "tenant globex must not load the state tenant acme saved under the same "
            f"session and URL; hit {hits!r}"
        )

        keys = _keys(backend)
        assert len(keys) == 2
        assert sorted(k.split(":")[1] for k in keys) == ["acme", "globex"]
        assert all(k.startswith("tenant:") and f":{session}_" in k for k in keys)

        # Each tenant still gets ITS OWN entry back: the isolation is keying, not "no cache".
        await _mount("_TenantView", session, "acme.example.com")
        assert [k.split(":")[1] for k in hits] == ["acme"]
        await _mount("_TenantView", session, "globex.example.com")
        assert [k.split(":")[1] for k in hits] == ["acme", "globex"]

    @pytest.mark.asyncio
    async def test_view_without_the_tenant_hook_keeps_the_unprefixed_key(self, backend_spy):
        backend, hits = backend_spy
        session = await sync_to_async(_new_session_key)()

        await _mount("_PlainView", session, "acme.example.com")
        await _mount("_PlainView", session, "acme.example.com")

        (key,) = _keys(backend)
        assert key.startswith(f"{session}_liveview_{_URL}_t"), key
        assert len(hits) == 1

    @pytest.mark.asyncio
    async def test_unresolved_tenant_saves_and_loads_nothing(self, backend_spy):
        """Fail closed: ``tenant_required = False`` and no tenant -> no saved state."""
        backend, hits = backend_spy
        session = await sync_to_async(_new_session_key)()

        await _mount("_OptionalTenantView", session, "testserver")
        await _mount("_OptionalTenantView", session, "testserver")

        assert _keys(backend) == [], "an unresolved tenant must not write the shared key"
        assert hits == []

    @pytest.mark.asyncio
    async def test_unresolved_tenant_cannot_read_a_plain_views_entry(self, backend_spy):
        """The shared (unprefixed) namespace is what a fallback would have read."""
        backend, hits = backend_spy
        session = await sync_to_async(_new_session_key)()

        await _mount("_PlainView", session, "testserver")
        (plain_key,) = _keys(backend)
        await _mount("_OptionalTenantView", session, "testserver")

        assert hits == []
        assert _keys(backend) == [plain_key]


class TestStateKeyHelper:
    """The key builder, on both transports' call shape."""

    def _view(self, tenant_id):
        from djust.tenants.resolvers import TenantInfo

        view = _TenantView()
        view._tenant = TenantInfo(tenant_id=tenant_id) if tenant_id else None
        return view

    def test_tenant_view_key(self):
        key = self._view("acme")._saved_state_key("sess", "liveview_/x/", "_tabc12345")
        assert key == "tenant:acme:sess_liveview_/x/_tabc12345"

    def test_two_tenants_get_different_keys(self):
        a = self._view("acme")._saved_state_key("sess", "liveview_/x/", "")
        b = self._view("globex")._saved_state_key("sess", "liveview_/x/", "")
        assert a != b

    def test_no_tenant_is_none_not_the_shared_key(self):
        assert self._view(None)._saved_state_key("sess", "liveview_/x/", "") is None

    def test_view_without_hook_is_unchanged(self):
        assert _PlainView()._saved_state_key("sess", "liveview_/x/", "_t1") == (
            "sess_liveview_/x/_t1"
        )

    def test_custom_hook_returning_empty_means_no_saved_state(self):
        class Opt(TenantMixin, LiveView):
            def get_state_key_prefix(self):
                return ""

        assert Opt()._saved_state_key("s", "v", "") is None

    def test_http_path_uses_the_same_key(self, backend_spy):
        """The HTTP render path (request.session) builds the same tenant key."""
        from django.contrib.sessions.backends.db import SessionStore
        from django.test import RequestFactory

        backend, _ = backend_spy
        store = SessionStore()
        store.create()

        def render_for(host):
            request = RequestFactory().get(_URL, HTTP_HOST=host)
            request.session = store
            view = _TenantView()
            view.request = request
            view._ensure_tenant(request)
            view._initialize_rust_view(request)
            return view._cache_key

        with override_settings(ALLOWED_HOSTS=_ALLOWED, DJUST_CONFIG=_CONFIG):
            a = render_for("acme.example.com")
            b = render_for("globex.example.com")
        assert a.startswith(f"tenant:acme:{store.session_key}_liveview_{_URL}_t")
        assert b.startswith(f"tenant:globex:{store.session_key}_liveview_{_URL}_t")
        assert backend.get(a) is not None and backend.get(b) is not None
