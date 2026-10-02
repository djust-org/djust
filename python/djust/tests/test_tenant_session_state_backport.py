"""#2973: view state saved in the Django session is keyed by tenant.

Legacy views also keep their state in ``request.session["liveview_<path>"]``
(+ ``__private`` / ``_components`` / ``__sticky__``). Before #2973 that key had
no tenant, so one session on two tenants at the same URL restored one tenant's
state into the other's view:

- HTTP fallback POST: tenant B's event ran with tenant A's saved state (and
  A's ``tenant`` context key through the ``tenant`` property setter);
- WS/SSE reconnect with ``enable_state_snapshot = True``: tenant B's mount
  frame rendered tenant A's state.

Both are driven here through the real transport code, same session, same URL,
tenant resolved from the Host.
"""

from __future__ import annotations

import json

import pytest
from asgiref.sync import sync_to_async
from django.contrib.sessions.backends.db import SessionStore
from django.test import RequestFactory, override_settings

from djust import LiveView
from djust.decorators import event_handler
from djust.tenants.mixin import TenantMixin

pytestmark = [pytest.mark.django_db, pytest.mark.tenants]

_ALLOWED = [".example.com", "testserver"]
_CONFIG = {"TENANT_RESOLVER": "subdomain"}
_URL = "/session-state-2973/"
_SEEN: list = []


class _SecretView(TenantMixin, LiveView):
    enable_state_snapshot = True
    template = '<div dj-root dj-id="0">[{{ secret }}]</div>'

    def mount(self, request, **kwargs):
        self.secret = "fresh-" + self.tenant.id
        self._private_note = "private-" + self.tenant.id

    @event_handler()
    def keep(self, **kwargs):
        self.secret = "secret-of-" + self.tenant.id
        self._private_note = "private-of-" + self.tenant.id

    @event_handler()
    def read(self, **kwargs):
        _SEEN.append((self.tenant.id, self.secret, getattr(self, "_private_note", None)))


class _OptionalSecretView(_SecretView):
    tenant_required = False

    def mount(self, request, **kwargs):
        self.secret = "fresh-notenant"

    @event_handler()
    def read(self, **kwargs):
        _SEEN.append((None, self.secret, None))


class _PlainView(LiveView):
    enable_state_snapshot = True
    template = '<div dj-root dj-id="0">[{{ secret }}]</div>'

    def mount(self, request, **kwargs):
        self.secret = "fresh-plain"

    @event_handler()
    def keep(self, **kwargs):
        self.secret = "kept-plain"

    @event_handler()
    def read(self, **kwargs):
        _SEEN.append(("plain", self.secret))


@pytest.fixture(autouse=True)
def _settings_and_seen():
    _SEEN.clear()
    with override_settings(
        ALLOWED_HOSTS=_ALLOWED, DJUST_CONFIG=_CONFIG, LIVEVIEW_ALLOWED_MODULES=[__name__]
    ):
        yield


def _new_session() -> SessionStore:
    store = SessionStore()
    store.create()
    return store


def _get(view_cls, host, session):
    request = RequestFactory().get(_URL, HTTP_HOST=host)
    request.session = session
    view_cls().get(request)


def _post(view_cls, host, session, event):
    request = RequestFactory().post(
        _URL,
        data=json.dumps({"event": event, "params": {}}),
        content_type="application/json",
        HTTP_HOST=host,
    )
    request.session = session
    response = view_cls().post(request)
    assert response.status_code == 200, response.content
    return response


# --------------------------------------------------------------------------- #
# HTTP fallback POST
# --------------------------------------------------------------------------- #
class TestHttpFallbackPost:
    def test_post_on_tenant_b_does_not_run_with_tenant_a_state(self):
        session = _new_session()
        _get(_SecretView, "globex.example.com", session)
        _get(_SecretView, "acme.example.com", session)  # the newer write, tenant A

        _post(_SecretView, "globex.example.com", session, "read")

        assert _SEEN == [("globex", "fresh-globex", "private-globex")], _SEEN

    def test_each_tenant_restores_its_own_state(self):
        """Isolation by keying, not by dropping state: both get their own back."""
        session = _new_session()
        _get(_SecretView, "acme.example.com", session)
        _post(_SecretView, "acme.example.com", session, "keep")
        _get(_SecretView, "globex.example.com", session)

        _post(_SecretView, "acme.example.com", session, "read")
        _post(_SecretView, "globex.example.com", session, "read")

        assert _SEEN == [
            ("acme", "secret-of-acme", "private-of-acme"),
            ("globex", "fresh-globex", "private-globex"),
        ], _SEEN

    def test_session_keys_carry_the_tenant(self):
        session = _new_session()
        _get(_SecretView, "acme.example.com", session)
        keys = [k for k in session.keys() if k.startswith("liveview_")]
        assert keys and all(k.startswith("liveview_tenant:acme:") for k in keys), keys

    def test_unresolved_tenant_saves_and_restores_nothing(self):
        session = _new_session()
        _get(_OptionalSecretView, "testserver", session)
        assert not [k for k in session.keys() if k.startswith("liveview_")]
        # A key a non-tenant view left behind is not read by the tenant view.
        session[f"liveview_{_URL}"] = {"secret": "shared"}
        _post(_OptionalSecretView, "testserver", session, "read")
        assert _SEEN == [(None, "fresh-notenant", None)], _SEEN

    def test_view_without_the_hook_keeps_the_unprefixed_key(self):
        session = _new_session()
        _get(_PlainView, "acme.example.com", session)
        assert f"liveview_{_URL}" in session
        _post(_PlainView, "acme.example.com", session, "keep")
        _post(_PlainView, "acme.example.com", session, "read")
        assert _SEEN == [("plain", "kept-plain")]


# --------------------------------------------------------------------------- #
# WebSocket mount / reconnect
# --------------------------------------------------------------------------- #
async def _ws(view: str, host: str, session: SessionStore, *, event: str | None = None) -> str:
    from channels.testing import WebsocketCommunicator
    from djust.websocket import LiveViewConsumer

    communicator = WebsocketCommunicator(
        LiveViewConsumer.as_asgi(), "/ws/", headers=[(b"host", host.encode())]
    )
    communicator.scope["session"] = session
    connected, _ = await communicator.connect()
    assert connected
    await communicator.receive_json_from(timeout=2)
    await communicator.send_json_to({"type": "mount", "view": f"{__name__}.{view}", "url": _URL})
    frame = None
    for _ in range(6):
        frame = await communicator.receive_json_from(timeout=3)
        if frame.get("type") in ("mount", "error", "navigate"):
            break
    assert frame is not None and frame.get("type") == "mount", frame
    if event:
        await communicator.send_json_to({"type": "event", "event": event, "params": {}})
        for _ in range(6):
            reply = await communicator.receive_json_from(timeout=3)
            if reply.get("type") in ("patch", "html_update", "diff", "noop", "error"):
                break
    await communicator.disconnect()
    return frame.get("html") or ""


class TestWebSocketReconnect:
    @pytest.mark.asyncio
    async def test_reconnect_on_tenant_b_does_not_render_tenant_a_state(self):
        session = await sync_to_async(_new_session)()
        await _ws("_SecretView", "acme.example.com", session, event="keep")

        acme = await _ws("_SecretView", "acme.example.com", session)
        assert "[secret-of-acme]" in acme, (
            "precondition: tenant A's own reconnect restores its saved state; got " + acme
        )

        globex = await _ws("_SecretView", "globex.example.com", session)
        assert "secret-of-acme" not in globex, globex
        assert "[fresh-globex]" in globex, globex

    @pytest.mark.asyncio
    async def test_view_without_the_hook_still_restores_over_the_socket(self):
        session = await sync_to_async(_new_session)()
        await _ws("_PlainView", "acme.example.com", session, event="keep")
        html = await _ws("_PlainView", "acme.example.com", session)
        assert "[kept-plain]" in html, html

    @pytest.mark.asyncio
    async def test_unresolved_tenant_does_not_restore_a_shared_entry(self):
        session = await sync_to_async(_new_session)()

        def _plant():
            session[f"liveview_{_URL}"] = {"secret": "shared"}
            session.save()

        await sync_to_async(_plant)()
        html = await _ws("_OptionalSecretView", "testserver", session)
        assert "shared" not in html, html


# --------------------------------------------------------------------------- #
# Structural pins: one place derives the key
# --------------------------------------------------------------------------- #
class TestKeyDerivationHasOneHome:
    def _sources(self):
        import pathlib

        import djust

        pkg = pathlib.Path(djust.__file__).resolve().parent
        for path in pkg.rglob("*.py"):
            rel = path.relative_to(pkg).as_posix()
            if rel.startswith("tests/") or "/tests/" in rel:
                continue
            yield rel, path.read_text()

    def test_session_keys_are_only_built_by_the_tenant_helper_and_sticky_helpers(self):
        """A new ``f"liveview_{...}"`` site would silently skip the tenant scope."""
        found = {rel for rel, src in self._sources() if 'f"liveview_{' in src}
        assert found == {
            "_tenant_state.py",  # session_view_key
            "mixins/sticky.py",  # takes a path the caller already scoped
            "mixins/rust_bridge.py",  # state-backend view_key, then _saved_state_key
        }, found

    def test_state_backend_key_is_only_built_by_saved_state_key(self):
        import re

        src = dict(self._sources())["mixins/rust_bridge.py"]
        cache_key_rhs = set(re.findall(r"self\._cache_key = (.+)", src))
        assert cache_key_rhs == {
            "self._saved_state_key(session_key, view_key, template_hash_slot)",
            "None",
        } or cache_key_rhs == {
            "self._saved_state_key(session_key, view_key, template_hash_slot)"
        }, cache_key_rhs
        assert len(re.findall(r"self\._cache_key = self\._saved_state_key\(", src)) == 2


class TestScopeHelpers:
    def test_scopes(self):
        from djust._tenant_state import scoped_path, session_view_key, state_scope
        from djust.tenants.resolvers import TenantInfo

        plain = _PlainView()
        assert state_scope(plain) == ""
        assert session_view_key(plain, "/x/") == "liveview_/x/"

        tenant = _SecretView()
        tenant._tenant = TenantInfo(tenant_id="acme")
        assert session_view_key(tenant, "/x/") == "liveview_tenant:acme:/x/"
        assert scoped_path(tenant, "/x/") == "tenant:acme:/x/"

        tenant._tenant = None
        assert state_scope(tenant) is None
        assert session_view_key(tenant, "/x/") is None
        assert scoped_path(tenant, "/x/") is None

    def test_tenant_id_is_percent_encoded(self):
        from djust.tenants.resolvers import TenantInfo

        view = _SecretView()
        view._tenant = TenantInfo(tenant_id="a:b_c")
        assert view.get_state_key_prefix() == "tenant:a%3Ab_c"
