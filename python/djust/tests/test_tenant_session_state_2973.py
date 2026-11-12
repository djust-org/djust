"""#2973: view state saved in the Django session is keyed by tenant.

Legacy views also keep their state in ``request.session["liveview_<path>"]``
(+ ``__private`` / ``_components`` / ``__sticky__``). Before #2973 that key had
no tenant, so one session on two tenants at the same URL restored one tenant's
state into the other's view:

- HTTP fallback POST: tenant B's event ran with tenant A's saved state (and
  A's ``tenant`` context key through the ``tenant`` property setter).

It is driven here through the real transport code, same session, same URL,
tenant resolved from the Host. (The WS/SSE reconnect restore of an
``enable_state_snapshot`` view, the other path #2973 fixed, was removed in 2.0.)
"""

from __future__ import annotations

import json

import pytest
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
    exposure_policy = "legacy"
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
    exposure_policy = "legacy"
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
            # Private attributes are not kept (2.0): mount() rebuilt this one.
            ("acme", "secret-of-acme", "private-acme"),
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

    def test_session_keys_are_only_built_by_the_tenant_helper(self):
        """A new ``f"liveview_{...}"`` site would silently skip the tenant scope."""
        found = {rel for rel, src in self._sources() if 'f"liveview_{' in src}
        assert found == {
            "_tenant_state.py",  # session_view_key
            "mixins/rust_bridge.py",  # state-backend view_key, then _saved_state_key
        }, found

    def test_state_backend_key_is_only_built_by_saved_state_key(self):
        import re

        src = dict(self._sources())["mixins/rust_bridge.py"]
        cache_key_rhs = set(re.findall(r"self\._cache_key = (.+)", src))
        assert cache_key_rhs == {"cache_key", "None"} or cache_key_rhs == {"cache_key"}, (
            cache_key_rhs
        )
        assert len(re.findall(r"cache_key = self\._saved_state_key\(", src)) == 2


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
