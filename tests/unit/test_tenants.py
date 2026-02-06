"""
Tests for djust multi-tenant feature.

Tests cover:
- TenantMixin tenant resolution from request
- Resolver types (subdomain, path, header, session)
- Tenant isolation in state backends
- Error handling when tenant not found
- Tenant context availability in templates
"""

import time
import pytest
from unittest.mock import Mock, patch


# === TenantInfo Tests ===


class TestTenantInfo:
    """Test TenantInfo data container."""

    def test_basic_creation(self):
        """Test creating TenantInfo with minimal args."""
        from djust.tenants.resolvers import TenantInfo

        tenant = TenantInfo(tenant_id="acme")

        assert tenant.id == "acme"
        assert tenant.name == "acme"  # Defaults to id
        assert tenant.settings == {}
        assert tenant.metadata == {}

    def test_full_creation(self):
        """Test creating TenantInfo with all args."""
        from djust.tenants.resolvers import TenantInfo

        raw_obj = {"db_id": 123}
        tenant = TenantInfo(
            tenant_id="acme",
            name="Acme Corp",
            settings={"theme": "dark", "max_users": 100},
            metadata={"plan": "enterprise"},
            raw=raw_obj,
        )

        assert tenant.id == "acme"
        assert tenant.name == "Acme Corp"
        assert tenant.settings == {"theme": "dark", "max_users": 100}
        assert tenant.metadata == {"plan": "enterprise"}
        assert tenant.raw == raw_obj

    def test_str_repr(self):
        """Test string representations."""
        from djust.tenants.resolvers import TenantInfo

        tenant = TenantInfo(tenant_id="acme", name="Acme Corp")

        assert str(tenant) == "acme"
        assert repr(tenant) == "TenantInfo(id='acme', name='Acme Corp')"

    def test_equality(self):
        """Test equality comparisons."""
        from djust.tenants.resolvers import TenantInfo

        tenant1 = TenantInfo(tenant_id="acme")
        tenant2 = TenantInfo(tenant_id="acme", name="Different Name")
        tenant3 = TenantInfo(tenant_id="other")

        # Same id = equal
        assert tenant1 == tenant2
        assert tenant1 != tenant3

        # Compare with string
        assert tenant1 == "acme"
        assert tenant1 != "other"

    def test_hash(self):
        """Test that TenantInfo is hashable."""
        from djust.tenants.resolvers import TenantInfo

        tenant1 = TenantInfo(tenant_id="acme")
        tenant2 = TenantInfo(tenant_id="acme")

        # Same hash
        assert hash(tenant1) == hash(tenant2)

        # Can use in sets
        tenants = {tenant1, tenant2}
        assert len(tenants) == 1

    def test_get_setting(self):
        """Test get_setting helper."""
        from djust.tenants.resolvers import TenantInfo

        tenant = TenantInfo(
            tenant_id="acme",
            settings={"theme": "dark", "features": ["a", "b"]},
        )

        assert tenant.get_setting("theme") == "dark"
        assert tenant.get_setting("features") == ["a", "b"]
        assert tenant.get_setting("missing") is None
        assert tenant.get_setting("missing", "default") == "default"


# === Resolver Tests ===


class TestSubdomainResolver:
    """Test SubdomainResolver."""

    def test_resolve_simple_subdomain(self):
        """Test resolving tenant from subdomain."""
        from djust.tenants.resolvers import SubdomainResolver

        resolver = SubdomainResolver()
        request = Mock()
        request.get_host.return_value = "acme.example.com"

        def config_side_effect(key, default=None):
            if key == "TENANT_SUBDOMAIN_EXCLUDE":
                return ["www", "api", "admin"]
            return default

        with patch.object(resolver, "get_config", side_effect=config_side_effect):
            tenant = resolver.resolve(request)

        assert tenant is not None
        assert tenant.id == "acme"

    def test_resolve_subdomain_with_port(self):
        """Test resolving tenant from subdomain with port."""
        from djust.tenants.resolvers import SubdomainResolver

        resolver = SubdomainResolver()
        request = Mock()
        request.get_host.return_value = "acme.example.com:8000"

        def config_side_effect(key, default=None):
            if key == "TENANT_SUBDOMAIN_EXCLUDE":
                return ["www", "api", "admin"]
            return default

        with patch.object(resolver, "get_config", side_effect=config_side_effect):
            tenant = resolver.resolve(request)

        assert tenant is not None
        assert tenant.id == "acme"

    def test_excludes_www(self):
        """Test that www subdomain is excluded."""
        from djust.tenants.resolvers import SubdomainResolver

        resolver = SubdomainResolver()
        request = Mock()
        request.get_host.return_value = "www.example.com"

        with patch.object(
            resolver,
            "get_config",
            side_effect=lambda key, default=None: ["www", "api", "admin"]
            if key == "TENANT_SUBDOMAIN_EXCLUDE"
            else default,
        ):
            tenant = resolver.resolve(request)

        assert tenant is None

    def test_excludes_api(self):
        """Test that api subdomain is excluded."""
        from djust.tenants.resolvers import SubdomainResolver

        resolver = SubdomainResolver()
        request = Mock()
        request.get_host.return_value = "api.example.com"

        with patch.object(
            resolver,
            "get_config",
            side_effect=lambda key, default=None: ["www", "api", "admin"]
            if key == "TENANT_SUBDOMAIN_EXCLUDE"
            else default,
        ):
            tenant = resolver.resolve(request)

        assert tenant is None

    def test_main_domain_only_returns_none(self):
        """Test that main domain without subdomain returns None."""
        from djust.tenants.resolvers import SubdomainResolver

        resolver = SubdomainResolver()
        request = Mock()
        request.get_host.return_value = "example.com"

        with patch.object(resolver, "get_config", return_value=None):
            tenant = resolver.resolve(request)

        assert tenant is None

    def test_with_explicit_main_domain(self):
        """Test resolution with explicitly configured main domain."""
        from djust.tenants.resolvers import SubdomainResolver

        resolver = SubdomainResolver()
        request = Mock()
        request.get_host.return_value = "acme.myapp.io"

        def config_side_effect(key, default=None):
            if key == "TENANT_MAIN_DOMAIN":
                return "myapp.io"
            if key == "TENANT_SUBDOMAIN_EXCLUDE":
                return ["www"]
            return default

        with patch.object(resolver, "get_config", side_effect=config_side_effect):
            tenant = resolver.resolve(request)

        assert tenant is not None
        assert tenant.id == "acme"

    def test_wrong_main_domain_returns_none(self):
        """Test that wrong main domain returns None."""
        from djust.tenants.resolvers import SubdomainResolver

        resolver = SubdomainResolver()
        request = Mock()
        request.get_host.return_value = "acme.other.com"

        def config_side_effect(key, default=None):
            if key == "TENANT_MAIN_DOMAIN":
                return "myapp.io"
            return default

        with patch.object(resolver, "get_config", side_effect=config_side_effect):
            tenant = resolver.resolve(request)

        assert tenant is None


class TestPathResolver:
    """Test PathResolver."""

    def test_resolve_from_path(self):
        """Test resolving tenant from URL path."""
        from djust.tenants.resolvers import PathResolver

        resolver = PathResolver()
        request = Mock()
        request.path = "/acme/dashboard"

        with patch.object(
            resolver,
            "get_config",
            side_effect=lambda key, default=None: 1
            if key == "TENANT_PATH_POSITION"
            else ["admin", "api", "static", "media"]
            if key == "TENANT_PATH_EXCLUDE"
            else default,
        ):
            tenant = resolver.resolve(request)

        assert tenant is not None
        assert tenant.id == "acme"

    def test_resolve_from_path_position_2(self):
        """Test resolving from different path position."""
        from djust.tenants.resolvers import PathResolver

        resolver = PathResolver()
        request = Mock()
        request.path = "/v1/acme/dashboard"

        with patch.object(
            resolver,
            "get_config",
            side_effect=lambda key, default=None: 2
            if key == "TENANT_PATH_POSITION"
            else ["admin", "api"]
            if key == "TENANT_PATH_EXCLUDE"
            else default,
        ):
            tenant = resolver.resolve(request)

        assert tenant is not None
        assert tenant.id == "acme"

    def test_excludes_admin(self):
        """Test that admin path is excluded."""
        from djust.tenants.resolvers import PathResolver

        resolver = PathResolver()
        request = Mock()
        request.path = "/admin/users"

        with patch.object(
            resolver,
            "get_config",
            side_effect=lambda key, default=None: 1
            if key == "TENANT_PATH_POSITION"
            else ["admin", "api", "static", "media"]
            if key == "TENANT_PATH_EXCLUDE"
            else default,
        ):
            tenant = resolver.resolve(request)

        assert tenant is None

    def test_empty_path_returns_none(self):
        """Test that empty path returns None."""
        from djust.tenants.resolvers import PathResolver

        resolver = PathResolver()
        request = Mock()
        request.path = "/"

        with patch.object(resolver, "get_config", return_value=None):
            tenant = resolver.resolve(request)

        assert tenant is None

    def test_invalid_tenant_id_format(self):
        """Test that invalid tenant ID format is rejected."""
        from djust.tenants.resolvers import PathResolver

        resolver = PathResolver()
        request = Mock()
        request.path = "/invalid!id/page"  # Contains invalid char

        with patch.object(
            resolver,
            "get_config",
            side_effect=lambda key, default=None: 1
            if key == "TENANT_PATH_POSITION"
            else []
            if key == "TENANT_PATH_EXCLUDE"
            else default,
        ):
            tenant = resolver.resolve(request)

        assert tenant is None

    def test_valid_tenant_id_formats(self):
        """Test valid tenant ID formats."""
        from djust.tenants.resolvers import PathResolver

        resolver = PathResolver()

        valid_ids = ["acme", "acme-corp", "acme_corp", "Acme123", "a1-b2_c3"]

        for tenant_id in valid_ids:
            request = Mock()
            request.path = f"/{tenant_id}/dashboard"

            with patch.object(
                resolver,
                "get_config",
                side_effect=lambda key, default=None: 1
                if key == "TENANT_PATH_POSITION"
                else []
                if key == "TENANT_PATH_EXCLUDE"
                else default,
            ):
                tenant = resolver.resolve(request)

            assert tenant is not None, f"Failed for {tenant_id}"
            assert tenant.id == tenant_id


class TestHeaderResolver:
    """Test HeaderResolver."""

    def test_resolve_from_header(self):
        """Test resolving tenant from HTTP header."""
        from djust.tenants.resolvers import HeaderResolver

        resolver = HeaderResolver()
        request = Mock()
        request.META = {"HTTP_X_TENANT_ID": "acme"}

        with patch.object(resolver, "get_config", return_value="X-Tenant-ID"):
            tenant = resolver.resolve(request)

        assert tenant is not None
        assert tenant.id == "acme"

    def test_resolve_from_custom_header(self):
        """Test resolving tenant from custom header name."""
        from djust.tenants.resolvers import HeaderResolver

        resolver = HeaderResolver()
        request = Mock()
        request.META = {"HTTP_X_ORGANIZATION_ID": "acme"}

        with patch.object(resolver, "get_config", return_value="X-Organization-ID"):
            tenant = resolver.resolve(request)

        assert tenant is not None
        assert tenant.id == "acme"

    def test_missing_header_returns_none(self):
        """Test that missing header returns None."""
        from djust.tenants.resolvers import HeaderResolver

        resolver = HeaderResolver()
        request = Mock()
        request.META = {}

        with patch.object(resolver, "get_config", return_value="X-Tenant-ID"):
            tenant = resolver.resolve(request)

        assert tenant is None


class TestSessionResolver:
    """Test SessionResolver."""

    def test_resolve_from_session(self):
        """Test resolving tenant from session."""
        from djust.tenants.resolvers import SessionResolver

        resolver = SessionResolver()
        request = Mock()
        request.session = {"tenant_id": "acme"}

        with patch.object(resolver, "get_config", return_value="tenant_id"):
            tenant = resolver.resolve(request)

        assert tenant is not None
        assert tenant.id == "acme"

    def test_resolve_from_jwt_claims(self):
        """Test resolving tenant from JWT claims."""
        from djust.tenants.resolvers import SessionResolver

        resolver = SessionResolver()
        request = Mock()
        request.session = {}
        request.user = Mock()
        request.user.jwt_payload = {"tenant_id": "acme"}

        with patch.object(
            resolver,
            "get_config",
            side_effect=lambda key, default=None: "tenant_id"
            if key in ("TENANT_SESSION_KEY", "TENANT_JWT_CLAIM")
            else default,
        ):
            tenant = resolver.resolve(request)

        assert tenant is not None
        assert tenant.id == "acme"

    def test_resolve_from_user_attribute(self):
        """Test resolving tenant from user.tenant_id."""
        from djust.tenants.resolvers import SessionResolver

        resolver = SessionResolver()
        request = Mock()
        request.session = {}
        request.user = Mock(spec=["tenant_id"])
        request.user.tenant_id = "acme"

        with patch.object(resolver, "get_config", return_value="tenant_id"):
            tenant = resolver.resolve(request)

        assert tenant is not None
        assert tenant.id == "acme"

    def test_session_priority_over_jwt(self):
        """Test that session takes priority over JWT."""
        from djust.tenants.resolvers import SessionResolver

        resolver = SessionResolver()
        request = Mock()
        request.session = {"tenant_id": "session_tenant"}
        request.user = Mock()
        request.user.jwt_payload = {"tenant_id": "jwt_tenant"}

        with patch.object(resolver, "get_config", return_value="tenant_id"):
            tenant = resolver.resolve(request)

        assert tenant.id == "session_tenant"

    def test_no_session_returns_none(self):
        """Test that no session data returns None."""
        from djust.tenants.resolvers import SessionResolver

        resolver = SessionResolver()
        request = Mock(spec=["session", "user"])
        request.session = {}
        request.user = Mock(spec=[])  # User without tenant_id or jwt_payload

        with patch.object(resolver, "get_config", return_value="tenant_id"):
            tenant = resolver.resolve(request)

        assert tenant is None


class TestChainedResolver:
    """Test ChainedResolver."""

    def test_returns_first_match(self):
        """Test that first matching resolver wins."""
        from djust.tenants.resolvers import ChainedResolver, TenantInfo

        resolver1 = Mock()
        resolver1.resolve.return_value = None

        resolver2 = Mock()
        resolver2.resolve.return_value = TenantInfo(tenant_id="acme")

        resolver3 = Mock()
        resolver3.resolve.return_value = TenantInfo(tenant_id="other")

        chained = ChainedResolver([resolver1, resolver2, resolver3])
        request = Mock()

        tenant = chained.resolve(request)

        assert tenant.id == "acme"
        resolver1.resolve.assert_called_once()
        resolver2.resolve.assert_called_once()
        resolver3.resolve.assert_not_called()

    def test_returns_none_if_no_match(self):
        """Test that None is returned if no resolver matches."""
        from djust.tenants.resolvers import ChainedResolver

        resolver1 = Mock()
        resolver1.resolve.return_value = None

        resolver2 = Mock()
        resolver2.resolve.return_value = None

        chained = ChainedResolver([resolver1, resolver2])
        request = Mock()

        tenant = chained.resolve(request)

        assert tenant is None


class TestGetTenantResolver:
    """Test get_tenant_resolver factory function."""

    def test_default_is_subdomain(self):
        """Test default resolver is subdomain."""
        from djust.tenants.resolvers import get_tenant_resolver, SubdomainResolver

        with patch("django.conf.settings") as mock_settings:
            mock_settings.DJUST_CONFIG = {}
            resolver = get_tenant_resolver()

        assert isinstance(resolver, SubdomainResolver)

    def test_creates_configured_resolver(self):
        """Test creating configured resolver type."""
        from djust.tenants.resolvers import get_tenant_resolver, HeaderResolver

        with patch("django.conf.settings") as mock_settings:
            mock_settings.DJUST_CONFIG = {"TENANT_RESOLVER": "header"}
            resolver = get_tenant_resolver()

        assert isinstance(resolver, HeaderResolver)

    def test_creates_chained_resolver(self):
        """Test creating chained resolver from list."""
        from djust.tenants.resolvers import get_tenant_resolver, ChainedResolver

        with patch("django.conf.settings") as mock_settings:
            mock_settings.DJUST_CONFIG = {"TENANT_RESOLVER": ["header", "subdomain"]}
            resolver = get_tenant_resolver()

        assert isinstance(resolver, ChainedResolver)
        assert len(resolver.resolvers) == 2


class TestResolveTenant:
    """Test resolve_tenant convenience function."""

    def test_applies_default_tenant(self):
        """Test that default tenant is applied when none resolved."""
        from djust.tenants.resolvers import resolve_tenant

        request = Mock()
        request.get_host.return_value = "example.com"  # No subdomain

        with patch("django.conf.settings") as mock_settings:
            mock_settings.DJUST_CONFIG = {
                "TENANT_RESOLVER": "subdomain",
                "TENANT_DEFAULT": "default_tenant",
            }
            tenant = resolve_tenant(request)

        assert tenant is not None
        assert tenant.id == "default_tenant"


# === TenantMixin Tests ===


class TestTenantMixin:
    """Test TenantMixin functionality."""

    def test_tenant_property(self):
        """Test tenant property access."""
        from djust.tenants.mixin import TenantMixin
        from djust.tenants.resolvers import TenantInfo

        class TestClass(TenantMixin):
            pass

        obj = TestClass()
        assert obj.tenant is None

        tenant = TenantInfo(tenant_id="acme")
        obj.tenant = tenant
        assert obj.tenant == tenant
        assert obj._tenant_resolved is True

    def test_resolve_tenant_method(self):
        """Test resolve_tenant method."""
        from djust.tenants.mixin import TenantMixin
        from djust.tenants.resolvers import TenantInfo

        class TestClass(TenantMixin):
            pass

        obj = TestClass()
        request = Mock()

        with patch("djust.tenants.mixin.resolve_tenant") as mock_resolve:
            mock_resolve.return_value = TenantInfo(tenant_id="acme")
            tenant = obj.resolve_tenant(request)

        assert tenant.id == "acme"

    def test_ensure_tenant_resolves_once(self):
        """Test that tenant is only resolved once."""
        from djust.tenants.mixin import TenantMixin
        from djust.tenants.resolvers import TenantInfo

        class TestClass(TenantMixin):
            pass

        obj = TestClass()
        request = Mock()

        with patch("djust.tenants.mixin.resolve_tenant") as mock_resolve:
            mock_resolve.return_value = TenantInfo(tenant_id="acme")

            # First call resolves
            obj._ensure_tenant(request)
            assert mock_resolve.call_count == 1

            # Second call should not re-resolve
            obj._ensure_tenant(request)
            assert mock_resolve.call_count == 1

    def test_tenant_required_raises_404(self):
        """Test that missing tenant raises 404 when required."""
        from djust.tenants.mixin import TenantMixin
        from django.http import Http404

        class TestClass(TenantMixin):
            tenant_required = True

        obj = TestClass()
        request = Mock()

        with patch("djust.tenants.mixin.resolve_tenant", return_value=None):
            with pytest.raises(Http404):
                obj._ensure_tenant(request)

    def test_tenant_not_required_allows_none(self):
        """Test that missing tenant is allowed when not required."""
        from djust.tenants.mixin import TenantMixin

        class TestClass(TenantMixin):
            tenant_required = False

        obj = TestClass()
        request = Mock()

        with patch("djust.tenants.mixin.resolve_tenant", return_value=None):
            obj._ensure_tenant(request)
            assert obj.tenant is None

    def test_get_context_data_includes_tenant(self):
        """Test that tenant is added to context data."""
        from djust.tenants.mixin import TenantMixin
        from djust.tenants.resolvers import TenantInfo

        class ParentClass:
            def get_context_data(self, **kwargs):
                return {"existing": "value"}

        class TestClass(TenantMixin, ParentClass):
            pass

        obj = TestClass()
        obj._tenant = TenantInfo(tenant_id="acme", name="Acme Corp")

        with patch("django.conf.settings") as mock_settings:
            mock_settings.DJUST_CONFIG = {}
            context = obj.get_context_data()

        assert context["existing"] == "value"
        assert context["tenant"] == obj._tenant
        assert context["tenant"].name == "Acme Corp"

    def test_custom_context_name(self):
        """Test custom tenant context name."""
        from djust.tenants.mixin import TenantMixin
        from djust.tenants.resolvers import TenantInfo

        class TestClass(TenantMixin):
            tenant_context_name = "organization"

        obj = TestClass()
        obj._tenant = TenantInfo(tenant_id="acme")

        with patch("django.conf.settings") as mock_settings:
            mock_settings.DJUST_CONFIG = {}
            context = obj.get_context_data()

        assert "organization" in context
        assert context["organization"].id == "acme"

    def test_get_presence_key_scoped(self):
        """Test that presence key is tenant-scoped."""
        from djust.tenants.mixin import TenantMixin
        from djust.tenants.resolvers import TenantInfo

        class TestClass(TenantMixin):
            pass

        obj = TestClass()
        obj._tenant = TenantInfo(tenant_id="acme")

        key = obj.get_presence_key()
        assert key == "tenant:acme:TestClass"

    def test_get_presence_key_without_tenant(self):
        """Test presence key without tenant."""
        from djust.tenants.mixin import TenantMixin

        class TestClass(TenantMixin):
            pass

        obj = TestClass()
        obj._tenant = None

        key = obj.get_presence_key()
        assert key == "TestClass"

    def test_get_state_key_prefix(self):
        """Test state key prefix generation."""
        from djust.tenants.mixin import TenantMixin
        from djust.tenants.resolvers import TenantInfo

        class TestClass(TenantMixin):
            pass

        obj = TestClass()
        obj._tenant = TenantInfo(tenant_id="acme")

        prefix = obj.get_state_key_prefix()
        assert prefix == "tenant:acme"


class TestTenantScopedMixin:
    """Test TenantScopedMixin functionality."""

    def test_get_tenant_queryset(self):
        """Test get_tenant_queryset returns filtered queryset."""
        from djust.tenants.mixin import TenantScopedMixin
        from djust.tenants.resolvers import TenantInfo

        # Mock model
        mock_model = Mock()
        mock_queryset = Mock()
        mock_model.objects.filter.return_value = mock_queryset
        mock_model.objects.none.return_value = []

        class TestClass(TenantScopedMixin):
            model = mock_model

        obj = TestClass()
        obj._tenant = TenantInfo(tenant_id="acme")

        result = obj.get_tenant_queryset()

        mock_model.objects.filter.assert_called_once_with(tenant_id="acme")
        assert result == mock_queryset

    def test_get_tenant_queryset_no_tenant(self):
        """Test get_tenant_queryset returns empty when no tenant."""
        from djust.tenants.mixin import TenantScopedMixin

        mock_model = Mock()
        empty_qs = Mock()
        mock_model.objects.none.return_value = empty_qs

        class TestClass(TenantScopedMixin):
            model = mock_model

        obj = TestClass()
        obj._tenant = None

        result = obj.get_tenant_queryset()

        mock_model.objects.none.assert_called_once()
        assert result == empty_qs

    def test_create_for_tenant(self):
        """Test create_for_tenant adds tenant_id."""
        from djust.tenants.mixin import TenantScopedMixin
        from djust.tenants.resolvers import TenantInfo

        mock_model = Mock()
        mock_instance = Mock()
        mock_model.objects.create.return_value = mock_instance

        class TestClass(TenantScopedMixin):
            model = mock_model

        obj = TestClass()
        obj._tenant = TenantInfo(tenant_id="acme")

        result = obj.create_for_tenant(name="Test Item")

        mock_model.objects.create.assert_called_once_with(
            name="Test Item",
            tenant_id="acme",
        )
        assert result == mock_instance

    def test_create_for_tenant_no_tenant_raises(self):
        """Test create_for_tenant raises when no tenant."""
        from djust.tenants.mixin import TenantScopedMixin

        class TestClass(TenantScopedMixin):
            model = Mock()

        obj = TestClass()
        obj._tenant = None

        with pytest.raises(ValueError, match="tenant not resolved"):
            obj.create_for_tenant(name="Test")


# === Backend Tests ===


class TestTenantAwareMemoryBackend:
    """Test TenantAwareMemoryBackend."""

    @pytest.fixture(autouse=True)
    def clear_backend(self):
        """Clear backend state before each test."""
        from djust.tenants.backends import TenantAwareMemoryBackend

        TenantAwareMemoryBackend.clear_all()
        yield
        TenantAwareMemoryBackend.clear_all()

    def test_join_creates_presence(self):
        """Test joining a presence group."""
        from djust.tenants.backends import TenantAwareMemoryBackend

        backend = TenantAwareMemoryBackend(tenant_id="acme")

        record = backend.join("room:123", "user1", {"name": "Alice"})

        assert record["id"] == "user1"
        assert record["tenant_id"] == "acme"
        assert record["meta"]["name"] == "Alice"
        assert "joined_at" in record

    def test_leave_removes_presence(self):
        """Test leaving a presence group."""
        from djust.tenants.backends import TenantAwareMemoryBackend

        backend = TenantAwareMemoryBackend(tenant_id="acme")

        backend.join("room:123", "user1", {"name": "Alice"})
        record = backend.leave("room:123", "user1")

        assert record is not None
        assert record["id"] == "user1"

        # Should be gone from list
        assert backend.list("room:123") == []

    def test_list_returns_all_presences(self):
        """Test listing all presences."""
        from djust.tenants.backends import TenantAwareMemoryBackend

        backend = TenantAwareMemoryBackend(tenant_id="acme")

        backend.join("room:123", "user1", {"name": "Alice"})
        backend.join("room:123", "user2", {"name": "Bob"})

        presences = backend.list("room:123")

        assert len(presences) == 2
        user_ids = {p["id"] for p in presences}
        assert user_ids == {"user1", "user2"}

    def test_count_returns_correct_count(self):
        """Test counting presences."""
        from djust.tenants.backends import TenantAwareMemoryBackend

        backend = TenantAwareMemoryBackend(tenant_id="acme")

        assert backend.count("room:123") == 0

        backend.join("room:123", "user1", {})
        assert backend.count("room:123") == 1

        backend.join("room:123", "user2", {})
        assert backend.count("room:123") == 2

    def test_tenant_isolation(self):
        """Test that tenants are isolated from each other."""
        from djust.tenants.backends import TenantAwareMemoryBackend

        backend_acme = TenantAwareMemoryBackend(tenant_id="acme")
        backend_globex = TenantAwareMemoryBackend(tenant_id="globex")

        # Both tenants join the same room name
        backend_acme.join("room:123", "user1", {"name": "Acme User"})
        backend_globex.join("room:123", "user1", {"name": "Globex User"})

        # Each tenant should only see their own data
        acme_presences = backend_acme.list("room:123")
        globex_presences = backend_globex.list("room:123")

        assert len(acme_presences) == 1
        assert len(globex_presences) == 1

        assert acme_presences[0]["meta"]["name"] == "Acme User"
        assert globex_presences[0]["meta"]["name"] == "Globex User"

    def test_heartbeat_updates_timestamp(self):
        """Test that heartbeat updates timestamp."""
        from djust.tenants.backends import TenantAwareMemoryBackend

        backend = TenantAwareMemoryBackend(tenant_id="acme", timeout=5)

        backend.join("room:123", "user1", {})

        # Get initial heartbeat
        initial_heartbeat = backend._heartbeats["acme"]["room:123:user1"]

        time.sleep(0.1)
        backend.heartbeat("room:123", "user1")

        new_heartbeat = backend._heartbeats["acme"]["room:123:user1"]
        assert new_heartbeat > initial_heartbeat

    def test_cleanup_stale_removes_old(self):
        """Test cleanup removes stale presences."""
        from djust.tenants.backends import TenantAwareMemoryBackend

        backend = TenantAwareMemoryBackend(tenant_id="acme", timeout=0.1)

        backend.join("room:123", "user1", {})
        assert backend.count("room:123") == 1

        # Wait for timeout
        time.sleep(0.2)

        cleaned = backend.cleanup_stale("room:123")
        assert cleaned == 1
        assert backend.count("room:123") == 0

    def test_health_check(self):
        """Test health check returns status."""
        from djust.tenants.backends import TenantAwareMemoryBackend

        backend = TenantAwareMemoryBackend(tenant_id="acme")
        backend.join("room:123", "user1", {})

        health = backend.health_check()

        assert health["status"] == "healthy"
        assert health["backend"] == "tenant_memory"
        assert health["tenant_id"] == "acme"
        assert health["presence_count"] == 1

    def test_clear_tenant(self):
        """Test clearing tenant data."""
        from djust.tenants.backends import TenantAwareMemoryBackend

        backend = TenantAwareMemoryBackend(tenant_id="acme")
        backend.join("room:123", "user1", {})

        TenantAwareMemoryBackend.clear_tenant("acme")

        # Create new backend for same tenant - should be empty
        backend2 = TenantAwareMemoryBackend(tenant_id="acme")
        assert backend2.count("room:123") == 0


class TestTenantPresenceManager:
    """Test TenantPresenceManager factory."""

    @pytest.fixture(autouse=True)
    def clear_cache(self):
        """Clear manager cache before each test."""
        from djust.tenants.backends import TenantPresenceManager, TenantAwareMemoryBackend

        TenantPresenceManager.clear_cache()
        TenantAwareMemoryBackend.clear_all()
        yield
        TenantPresenceManager.clear_cache()
        TenantAwareMemoryBackend.clear_all()

    def test_returns_backend_for_tenant(self):
        """Test getting backend for a tenant."""
        from djust.tenants.backends import TenantPresenceManager, TenantAwareMemoryBackend

        with patch("django.conf.settings") as mock_settings:
            mock_settings.DJUST_CONFIG = {"PRESENCE_BACKEND": "memory"}

            backend = TenantPresenceManager.for_tenant("acme")

        assert isinstance(backend, TenantAwareMemoryBackend)
        assert backend.tenant_id == "acme"

    def test_caches_backend_instances(self):
        """Test that backend instances are cached."""
        from djust.tenants.backends import TenantPresenceManager

        with patch("django.conf.settings") as mock_settings:
            mock_settings.DJUST_CONFIG = {"PRESENCE_BACKEND": "memory"}

            backend1 = TenantPresenceManager.for_tenant("acme")
            backend2 = TenantPresenceManager.for_tenant("acme")

        assert backend1 is backend2

    def test_different_tenants_get_different_backends(self):
        """Test that different tenants get different backends."""
        from djust.tenants.backends import TenantPresenceManager

        with patch("django.conf.settings") as mock_settings:
            mock_settings.DJUST_CONFIG = {"PRESENCE_BACKEND": "memory"}

            backend_acme = TenantPresenceManager.for_tenant("acme")
            backend_globex = TenantPresenceManager.for_tenant("globex")

        assert backend_acme is not backend_globex
        assert backend_acme.tenant_id == "acme"
        assert backend_globex.tenant_id == "globex"


class TestContextProcessor:
    """Test TenantContextProcessor."""

    def test_context_processor_adds_tenant(self):
        """Test context processor adds tenant to context."""
        from djust.tenants.mixin import context_processor
        from djust.tenants.resolvers import TenantInfo

        request = Mock()

        with patch("djust.tenants.mixin.resolve_tenant") as mock_resolve:
            mock_resolve.return_value = TenantInfo(tenant_id="acme", name="Acme Corp")

            with patch("django.conf.settings") as mock_settings:
                mock_settings.DJUST_CONFIG = {}
                context = context_processor(request)

        assert "tenant" in context
        assert context["tenant"].id == "acme"
        assert context["tenant"].name == "Acme Corp"

    def test_context_processor_custom_name(self):
        """Test context processor uses custom context name."""
        from djust.tenants.mixin import context_processor
        from djust.tenants.resolvers import TenantInfo

        request = Mock()

        with patch("djust.tenants.mixin.resolve_tenant") as mock_resolve:
            mock_resolve.return_value = TenantInfo(tenant_id="acme")

            with patch("django.conf.settings") as mock_settings:
                mock_settings.DJUST_CONFIG = {"TENANT_CONTEXT_NAME": "org"}
                context = context_processor(request)

        assert "org" in context
        assert context["org"].id == "acme"
