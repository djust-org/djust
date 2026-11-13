"""#2973: view state saved in the Django session is keyed by tenant.

The legacy page-POST fallback kept a view's state in
``request.session["liveview_<path>"]``; before #2973 that key had no tenant,
so one session on two tenants at the same URL restored one tenant's state into
the other's view. 2.0 removed that session state (and the WS/SSE reconnect
restore); what is left is the key derivation the legacy lazy child still uses
(``session_view_key``), pinned below.
"""

from __future__ import annotations


import pytest
from django.test import override_settings

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
