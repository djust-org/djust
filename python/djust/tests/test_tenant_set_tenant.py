"""Regression tests for TenantMixin.set_tenant() (#2003).

Covers the new ``set_tenant()`` helper that lets a WebSocket event handler
switch the current tenant from a fresh/default session:

* it updates the *authoritative* in-memory view state (``self.tenant``), and
* it best-effort *mirrors* the tenant id into ``request.session`` **only** when
  a session-based resolver is configured (a no-op for subdomain/path/header/
  custom resolvers).

See ``TestSetTenant`` for the behavior-defining cases.
"""

from unittest.mock import Mock, patch

from djust.tenants.mixin import TenantMixin, TenantScopedMixin
from djust.tenants.resolvers import TenantInfo


def _make_view(cls, session=None):
    """Build a mixin instance with an optional request+session, no LiveView base."""
    obj = cls()
    request = Mock()
    if session is None:
        # request with no ``session`` attribute at all
        del request.session
    else:
        request.session = session
    obj.request = request
    return obj


def _config(**overrides):
    """patch('django.conf.settings') context manager pre-seeding DJUST_CONFIG."""
    cm = patch("django.conf.settings")
    return cm, overrides


class _View(TenantMixin):
    pass


class _ScopedView(TenantScopedMixin):
    model = Mock()


class TestSetTenant:
    """set_tenant() view-state + best-effort session-mirror semantics."""

    def test_updates_view_state(self):
        """set_tenant() sets self.tenant and marks it resolved (no request needed)."""
        obj = _View()  # no request attr at all
        with patch("django.conf.settings") as s:
            s.DJUST_CONFIG = {"TENANT_RESOLVER": "subdomain"}
            result = obj.set_tenant("acme")

        assert isinstance(result, TenantInfo)
        assert obj.tenant is not None
        assert obj.tenant.id == "acme"
        assert obj._tenant_resolved is True
        assert result is obj.tenant

    def test_mirrors_into_session_for_session_resolver(self):
        """With a session resolver + a request session, the id is mirrored in."""
        session = {}
        obj = _make_view(_View, session=session)
        with patch("django.conf.settings") as s:
            s.DJUST_CONFIG = {"TENANT_RESOLVER": "session", "TENANT_SESSION_KEY": "tenant_id"}
            obj.set_tenant("acme")

        # Authoritative view state.
        assert obj.tenant.id == "acme"
        # Best-effort mirror.
        assert session == {"tenant_id": "acme"}

    def test_mirrors_with_custom_session_key(self):
        """The mirror honors TENANT_SESSION_KEY."""
        session = {}
        obj = _make_view(_View, session=session)
        with patch("django.conf.settings") as s:
            s.DJUST_CONFIG = {"TENANT_RESOLVER": "session", "TENANT_SESSION_KEY": "org_id"}
            obj.set_tenant("acme")

        assert session == {"org_id": "acme"}

    def test_mirrors_for_chained_resolver_containing_session(self):
        """A chained resolver that includes 'session' still mirrors."""
        session = {}
        obj = _make_view(_View, session=session)
        with patch("django.conf.settings") as s:
            s.DJUST_CONFIG = {"TENANT_RESOLVER": ["header", "session"]}
            obj.set_tenant("acme")

        assert obj.tenant.id == "acme"
        assert session == {"tenant_id": "acme"}

    def test_noop_on_session_for_non_session_resolver(self):
        """A subdomain resolver must NOT write the session (no-op mirror)."""
        session = {}
        obj = _make_view(_View, session=session)
        with patch("django.conf.settings") as s:
            s.DJUST_CONFIG = {"TENANT_RESOLVER": "subdomain"}
            obj.set_tenant("acme")

        # View state still authoritative...
        assert obj.tenant.id == "acme"
        # ...but the session is untouched.
        assert session == {}

    def test_noop_on_session_for_header_resolver(self):
        """A header resolver is also a no-op on the session."""
        session = {}
        obj = _make_view(_View, session=session)
        with patch("django.conf.settings") as s:
            s.DJUST_CONFIG = {"TENANT_RESOLVER": "header"}
            obj.set_tenant("acme")

        assert obj.tenant.id == "acme"
        assert session == {}

    def test_no_request_session_is_safe(self):
        """Missing request.session (or missing request) never raises."""
        obj = _make_view(_View, session=None)  # request without a session attr
        with patch("django.conf.settings") as s:
            s.DJUST_CONFIG = {"TENANT_RESOLVER": "session"}
            obj.set_tenant("acme")

        assert obj.tenant.id == "acme"

    def test_coerces_non_str_id_to_str(self):
        """A non-str tenant id is coerced to str, matching resolver behavior."""
        session = {}
        obj = _make_view(_View, session=session)
        with patch("django.conf.settings") as s:
            s.DJUST_CONFIG = {"TENANT_RESOLVER": "session"}
            obj.set_tenant(42)

        assert obj.tenant.id == "42"
        assert session == {"tenant_id": "42"}

    def test_accepts_tenantinfo_instance(self):
        """Passing a TenantInfo preserves it (name/settings) and mirrors its id."""
        session = {}
        obj = _make_view(_View, session=session)
        info = TenantInfo(tenant_id="acme", name="Acme Corp", settings={"theme": "dark"})
        with patch("django.conf.settings") as s:
            s.DJUST_CONFIG = {"TENANT_RESOLVER": "session"}
            result = obj.set_tenant(info)

        assert result is info
        assert obj.tenant is info
        assert obj.tenant.name == "Acme Corp"
        assert session == {"tenant_id": "acme"}

    def test_overrides_previously_resolved_tenant(self):
        """set_tenant() overrides an existing resolved tenant (won't be re-resolved)."""
        obj = _make_view(_View, session={})
        obj._tenant = TenantInfo(tenant_id="old")
        obj._tenant_resolved = True
        with patch("django.conf.settings") as s:
            s.DJUST_CONFIG = {"TENANT_RESOLVER": "session"}
            obj.set_tenant("new")

        assert obj.tenant.id == "new"


class TestTenantScopedMixinExposesSetTenant:
    """TenantScopedMixin inherits set_tenant() and its scoped helpers see the new tenant."""

    def test_scoped_mixin_has_set_tenant(self):
        obj = _make_view(_ScopedView, session={})
        assert hasattr(obj, "set_tenant")

        with patch("django.conf.settings") as s:
            s.DJUST_CONFIG = {"TENANT_RESOLVER": "session"}
            obj.set_tenant("acme")

        assert obj.tenant.id == "acme"

    def test_scoped_queryset_uses_set_tenant_value(self):
        """After set_tenant(), get_tenant_queryset() filters on the new tenant id."""
        model = Mock()

        class _V(TenantScopedMixin):
            pass

        obj = _make_view(_V, session={})
        obj.model = model
        with patch("django.conf.settings") as s:
            s.DJUST_CONFIG = {"TENANT_RESOLVER": "session"}
            obj.set_tenant("acme")

        obj.get_tenant_queryset()
        model.objects.filter.assert_called_once_with(tenant_id="acme")
