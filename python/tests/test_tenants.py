"""
Tests for the multi-tenant support system.

Tests cover:
- Tenant resolution (subdomain, path, header, session)
- TenantMixin functionality
- State isolation between tenants
- Presence isolation between tenants
- Template context injection
"""

import pytest
import time
from unittest.mock import Mock, patch, MagicMock

from djust.tenants import (
    TenantInfo,
    TenantMixin,
    TenantScopedMixin,
    SubdomainResolver,
    PathResolver,
    HeaderResolver,
    SessionResolver,
    CustomResolver,
    ChainedResolver,
    resolve_tenant,
    get_tenant_resolver,
    TenantAwareMemoryBackend,
    TenantPresenceManager,
    context_processor,
)


# ============================================================================
# Test Fixtures and Helpers
# ============================================================================

class FakeUser:
    """Fake user for testing"""
    def __init__(self, username="testuser", user_id=1, tenant_id=None):
        self.username = username
        self.id = user_id
        self.is_authenticated = True
        if tenant_id:
            self.tenant_id = tenant_id


class FakeSession:
    """Fake session for testing"""
    def __init__(self, data=None):
        self._data = data or {}
        self.session_key = "test_session_123"
    
    def get(self, key, default=None):
        return self._data.get(key, default)
    
    def __setitem__(self, key, value):
        self._data[key] = value


class FakeRequest:
    """Fake request for testing tenant resolution"""
    def __init__(
        self,
        host="example.com",
        path="/",
        user=None,
        session=None,
        headers=None,
    ):
        self._host = host
        self.path = path
        self.user = user or FakeUser()
        self.session = session or FakeSession()
        self.META = {}
        
        # Add headers to META
        if headers:
            for key, value in headers.items():
                meta_key = f"HTTP_{key.upper().replace('-', '_')}"
                self.META[meta_key] = value
    
    def get_host(self):
        return self._host


class FakeView:
    """Fake view for testing mixins"""
    def __init__(self):
        if hasattr(super(), '__init__'):
            super().__init__()
        self.request = FakeRequest()
    
    def get_context_data(self, **kwargs):
        return {}
    
    def dispatch(self, request, *args, **kwargs):
        return None
    
    def get(self, request, *args, **kwargs):
        return None
    
    def post(self, request, *args, **kwargs):
        return None


@pytest.fixture(autouse=True)
def clear_tenant_backends():
    """Clear tenant backends before each test."""
    TenantAwareMemoryBackend.clear_all()
    TenantPresenceManager.clear_cache()
    yield
    TenantAwareMemoryBackend.clear_all()
    TenantPresenceManager.clear_cache()


# ============================================================================
# TenantInfo Tests
# ============================================================================

class TestTenantInfo:
    """Tests for TenantInfo container class."""
    
    def test_basic_creation(self):
        """Test basic TenantInfo creation."""
        tenant = TenantInfo(tenant_id="acme")
        assert tenant.id == "acme"
        assert tenant.name == "acme"  # Defaults to id
        assert tenant.settings == {}
        assert tenant.metadata == {}
    
    def test_full_creation(self):
        """Test TenantInfo with all fields."""
        tenant = TenantInfo(
            tenant_id="acme",
            name="Acme Corp",
            settings={"theme": "dark"},
            metadata={"plan": "enterprise"},
        )
        assert tenant.id == "acme"
        assert tenant.name == "Acme Corp"
        assert tenant.settings["theme"] == "dark"
        assert tenant.metadata["plan"] == "enterprise"
    
    def test_string_representation(self):
        """Test string representation."""
        tenant = TenantInfo(tenant_id="acme", name="Acme Corp")
        assert str(tenant) == "acme"
        assert repr(tenant) == "TenantInfo(id='acme', name='Acme Corp')"
    
    def test_equality(self):
        """Test equality comparison."""
        tenant1 = TenantInfo(tenant_id="acme")
        tenant2 = TenantInfo(tenant_id="acme", name="Different Name")
        tenant3 = TenantInfo(tenant_id="other")
        
        assert tenant1 == tenant2
        assert tenant1 != tenant3
        assert tenant1 == "acme"  # String comparison
        assert tenant1 != "other"
    
    def test_hash(self):
        """Test hash for use in sets/dicts."""
        tenant1 = TenantInfo(tenant_id="acme")
        tenant2 = TenantInfo(tenant_id="acme")
        
        tenant_set = {tenant1}
        assert tenant2 in tenant_set
    
    def test_get_setting(self):
        """Test get_setting helper."""
        tenant = TenantInfo(
            tenant_id="acme",
            settings={"theme": "dark", "logo": "/logo.png"}
        )
        assert tenant.get_setting("theme") == "dark"
        assert tenant.get_setting("logo") == "/logo.png"
        assert tenant.get_setting("missing") is None
        assert tenant.get_setting("missing", "default") == "default"
    
    def test_raw_object(self):
        """Test raw object storage."""
        raw = Mock(name="TenantModel")
        tenant = TenantInfo(tenant_id="acme", raw=raw)
        assert tenant.raw is raw


# ============================================================================
# Tenant Resolver Tests
# ============================================================================

class TestSubdomainResolver:
    """Tests for subdomain-based tenant resolution."""
    
    def test_resolve_subdomain(self):
        """Test basic subdomain resolution."""
        resolver = SubdomainResolver()
        request = FakeRequest(host="acme.example.com")
        
        tenant = resolver.resolve(request)
        assert tenant is not None
        assert tenant.id == "acme"
    
    def test_no_subdomain(self):
        """Test when no subdomain present."""
        resolver = SubdomainResolver()
        request = FakeRequest(host="example.com")
        
        tenant = resolver.resolve(request)
        assert tenant is None
    
    def test_excluded_subdomain(self):
        """Test excluded subdomains (www, api, admin)."""
        resolver = SubdomainResolver()
        
        for subdomain in ['www', 'api', 'admin']:
            request = FakeRequest(host=f"{subdomain}.example.com")
            tenant = resolver.resolve(request)
            assert tenant is None, f"{subdomain} should be excluded"
    
    def test_with_port(self):
        """Test subdomain with port number."""
        resolver = SubdomainResolver()
        request = FakeRequest(host="acme.example.com:8000")
        
        tenant = resolver.resolve(request)
        assert tenant is not None
        assert tenant.id == "acme"
    
    @patch('djust.tenants.resolvers.SubdomainResolver.get_config')
    def test_main_domain_config(self, mock_config):
        """Test with explicit main domain config."""
        mock_config.side_effect = lambda key, default=None: {
            'TENANT_SUBDOMAIN_EXCLUDE': ['www'],
            'TENANT_MAIN_DOMAIN': 'example.com',
        }.get(key, default)
        
        resolver = SubdomainResolver()
        request = FakeRequest(host="acme.example.com")
        
        tenant = resolver.resolve(request)
        assert tenant is not None
        assert tenant.id == "acme"


class TestPathResolver:
    """Tests for path-based tenant resolution."""
    
    def test_resolve_from_path(self):
        """Test basic path resolution."""
        resolver = PathResolver()
        request = FakeRequest(path="/acme/dashboard")
        
        tenant = resolver.resolve(request)
        assert tenant is not None
        assert tenant.id == "acme"
    
    def test_empty_path(self):
        """Test with empty path."""
        resolver = PathResolver()
        request = FakeRequest(path="/")
        
        tenant = resolver.resolve(request)
        assert tenant is None
    
    def test_excluded_path(self):
        """Test excluded paths (admin, api, static)."""
        resolver = PathResolver()
        
        for path in ['admin', 'api', 'static', 'media']:
            request = FakeRequest(path=f"/{path}/something")
            tenant = resolver.resolve(request)
            assert tenant is None, f"/{path}/ should be excluded"
    
    def test_invalid_tenant_id(self):
        """Test invalid tenant ID format."""
        resolver = PathResolver()
        
        # Special characters should be rejected
        request = FakeRequest(path="/ac$me/dashboard")
        tenant = resolver.resolve(request)
        assert tenant is None
    
    def test_valid_tenant_formats(self):
        """Test valid tenant ID formats."""
        resolver = PathResolver()
        
        valid_ids = ['acme', 'acme-corp', 'acme_corp', 'ACME', 'acme123']
        for tenant_id in valid_ids:
            request = FakeRequest(path=f"/{tenant_id}/dashboard")
            tenant = resolver.resolve(request)
            assert tenant is not None, f"{tenant_id} should be valid"
            assert tenant.id == tenant_id


class TestHeaderResolver:
    """Tests for header-based tenant resolution."""
    
    def test_resolve_from_header(self):
        """Test basic header resolution."""
        resolver = HeaderResolver()
        request = FakeRequest(headers={'X-Tenant-ID': 'acme'})
        
        tenant = resolver.resolve(request)
        assert tenant is not None
        assert tenant.id == "acme"
    
    def test_no_header(self):
        """Test when header not present."""
        resolver = HeaderResolver()
        request = FakeRequest()
        
        tenant = resolver.resolve(request)
        assert tenant is None
    
    @patch('djust.tenants.resolvers.HeaderResolver.get_config')
    def test_custom_header_name(self, mock_config):
        """Test with custom header name."""
        mock_config.return_value = 'X-Organization-ID'
        
        resolver = HeaderResolver()
        request = FakeRequest(headers={'X-Organization-ID': 'acme'})
        
        tenant = resolver.resolve(request)
        assert tenant is not None
        assert tenant.id == "acme"


class TestSessionResolver:
    """Tests for session-based tenant resolution."""
    
    def test_resolve_from_session(self):
        """Test basic session resolution."""
        resolver = SessionResolver()
        session = FakeSession({'tenant_id': 'acme'})
        request = FakeRequest(session=session)
        
        tenant = resolver.resolve(request)
        assert tenant is not None
        assert tenant.id == "acme"
    
    def test_no_session_data(self):
        """Test when session doesn't have tenant."""
        resolver = SessionResolver()
        request = FakeRequest()
        
        tenant = resolver.resolve(request)
        assert tenant is None
    
    def test_from_user_tenant_id(self):
        """Test resolution from user.tenant_id attribute."""
        resolver = SessionResolver()
        user = FakeUser(tenant_id='acme')
        request = FakeRequest(user=user)
        
        tenant = resolver.resolve(request)
        assert tenant is not None
        assert tenant.id == "acme"


class TestCustomResolver:
    """Tests for custom callable resolver."""
    
    def test_custom_resolver_returns_tenant_info(self):
        """Test custom resolver returning TenantInfo."""
        def custom_resolve(request):
            return TenantInfo(tenant_id='custom_tenant')
        
        resolver = CustomResolver()
        resolver._resolver_cache = custom_resolve
        
        request = FakeRequest()
        tenant = resolver.resolve(request)
        
        assert tenant is not None
        assert tenant.id == "custom_tenant"
    
    def test_custom_resolver_returns_string(self):
        """Test custom resolver returning string."""
        def custom_resolve(request):
            return 'string_tenant'
        
        resolver = CustomResolver()
        resolver._resolver_cache = custom_resolve
        
        request = FakeRequest()
        tenant = resolver.resolve(request)
        
        assert tenant is not None
        assert tenant.id == "string_tenant"
    
    def test_no_resolver_configured(self):
        """Test when no custom resolver is configured."""
        resolver = CustomResolver()
        resolver._resolver_cache = None
        
        with patch.object(resolver, 'get_config', return_value=None):
            request = FakeRequest()
            tenant = resolver.resolve(request)
            assert tenant is None


class TestChainedResolver:
    """Tests for chained resolver."""
    
    def test_first_match_wins(self):
        """Test that first matching resolver wins."""
        header_resolver = HeaderResolver()
        subdomain_resolver = SubdomainResolver()
        
        chained = ChainedResolver([header_resolver, subdomain_resolver])
        
        request = FakeRequest(
            host="subdomain.example.com",
            headers={'X-Tenant-ID': 'header_tenant'}
        )
        
        tenant = chained.resolve(request)
        assert tenant is not None
        assert tenant.id == "header_tenant"  # Header wins because it's first
    
    def test_fallback(self):
        """Test fallback to second resolver."""
        header_resolver = HeaderResolver()
        subdomain_resolver = SubdomainResolver()
        
        chained = ChainedResolver([header_resolver, subdomain_resolver])
        
        request = FakeRequest(host="acme.example.com")  # No header
        
        tenant = chained.resolve(request)
        assert tenant is not None
        assert tenant.id == "acme"  # Falls back to subdomain
    
    def test_no_match(self):
        """Test when no resolver matches."""
        header_resolver = HeaderResolver()
        subdomain_resolver = SubdomainResolver()
        
        chained = ChainedResolver([header_resolver, subdomain_resolver])
        
        request = FakeRequest(host="example.com")  # No header, no subdomain
        
        tenant = chained.resolve(request)
        assert tenant is None


# ============================================================================
# TenantMixin Tests
# ============================================================================

class TestTenantMixin:
    """Tests for TenantMixin functionality."""
    
    def test_tenant_property(self):
        """Test tenant property access."""
        class TestView(TenantMixin, FakeView):
            pass
        
        view = TestView()
        assert view.tenant is None
        
        # Set tenant
        tenant = TenantInfo(tenant_id='acme')
        view.tenant = tenant
        assert view.tenant == tenant
    
    def test_auto_resolution_on_get(self):
        """Test automatic tenant resolution on GET."""
        class TestView(TenantMixin, FakeView):
            tenant_required = False
        
        view = TestView()
        view.request = FakeRequest(host="acme.example.com")
        
        with patch('djust.tenants.mixin.resolve_tenant') as mock_resolve:
            mock_resolve.return_value = TenantInfo(tenant_id='acme')
            view.get(view.request)
            
            assert view.tenant is not None
            assert view.tenant.id == 'acme'
    
    def test_context_data_includes_tenant(self):
        """Test that tenant is added to context data."""
        class TestView(TenantMixin, FakeView):
            pass
        
        view = TestView()
        view._tenant = TenantInfo(tenant_id='acme', name='Acme Corp')
        view._tenant_resolved = True
        
        context = view.get_context_data()
        assert 'tenant' in context
        assert context['tenant'].id == 'acme'
        assert context['tenant'].name == 'Acme Corp'
    
    def test_tenant_required_raises_404(self):
        """Test that missing tenant raises 404 when required."""
        class TestView(TenantMixin, FakeView):
            tenant_required = True
        
        view = TestView()
        view.request = FakeRequest(host="example.com")  # No subdomain
        
        with patch('djust.tenants.mixin.resolve_tenant', return_value=None):
            from django.http import Http404
            with pytest.raises(Http404):
                view._ensure_tenant(view.request)
    
    def test_tenant_not_required(self):
        """Test that missing tenant doesn't raise when not required."""
        class TestView(TenantMixin, FakeView):
            tenant_required = False
        
        view = TestView()
        view.request = FakeRequest(host="example.com")
        
        with patch('djust.tenants.mixin.resolve_tenant', return_value=None):
            view._ensure_tenant(view.request)  # Should not raise
            assert view.tenant is None
    
    def test_custom_resolve_tenant(self):
        """Test overriding resolve_tenant method."""
        class TestView(TenantMixin, FakeView):
            def resolve_tenant(self, request):
                return TenantInfo(tenant_id='custom', name='Custom Tenant')
        
        view = TestView()
        view._ensure_tenant(view.request)
        
        assert view.tenant is not None
        assert view.tenant.id == 'custom'


class TestTenantScopedMixin:
    """Tests for TenantScopedMixin functionality."""
    
    def test_get_tenant_queryset(self):
        """Test get_tenant_queryset helper."""
        mock_model = Mock()
        mock_qs = Mock()
        mock_model.objects.filter.return_value = mock_qs
        mock_model.objects.none.return_value = Mock()
        
        class TestView(TenantScopedMixin, FakeView):
            model = mock_model
        
        view = TestView()
        view._tenant = TenantInfo(tenant_id='acme')
        view._tenant_resolved = True
        
        result = view.get_tenant_queryset()
        
        mock_model.objects.filter.assert_called_once_with(tenant_id='acme')
        assert result == mock_qs
    
    def test_get_tenant_queryset_no_tenant(self):
        """Test get_tenant_queryset returns empty when no tenant."""
        mock_model = Mock()
        mock_none = Mock()
        mock_model.objects.none.return_value = mock_none
        
        class TestView(TenantScopedMixin, FakeView):
            model = mock_model
        
        view = TestView()
        view._tenant = None
        view._tenant_resolved = True
        
        result = view.get_tenant_queryset()
        
        mock_model.objects.none.assert_called_once()
        assert result == mock_none
    
    def test_create_for_tenant(self):
        """Test create_for_tenant helper."""
        mock_model = Mock()
        mock_instance = Mock()
        mock_model.objects.create.return_value = mock_instance
        
        class TestView(TenantScopedMixin, FakeView):
            model = mock_model
        
        view = TestView()
        view._tenant = TenantInfo(tenant_id='acme')
        view._tenant_resolved = True
        
        result = view.create_for_tenant(name='Test Item')
        
        mock_model.objects.create.assert_called_once_with(
            name='Test Item',
            tenant_id='acme'
        )
        assert result == mock_instance


# ============================================================================
# Tenant Backend Tests
# ============================================================================

class TestTenantAwareMemoryBackend:
    """Tests for tenant-aware in-memory backend."""
    
    def test_join_and_list(self):
        """Test joining and listing presences."""
        backend = TenantAwareMemoryBackend(tenant_id='acme')
        
        record = backend.join('room:1', 'user1', {'name': 'Alice'})
        
        assert record['id'] == 'user1'
        assert record['tenant_id'] == 'acme'
        
        presences = backend.list('room:1')
        assert len(presences) == 1
        assert presences[0]['id'] == 'user1'
    
    def test_leave(self):
        """Test leaving presence."""
        backend = TenantAwareMemoryBackend(tenant_id='acme')
        
        backend.join('room:1', 'user1', {'name': 'Alice'})
        record = backend.leave('room:1', 'user1')
        
        assert record is not None
        assert record['id'] == 'user1'
        
        presences = backend.list('room:1')
        assert len(presences) == 0
    
    def test_count(self):
        """Test presence count."""
        backend = TenantAwareMemoryBackend(tenant_id='acme')
        
        assert backend.count('room:1') == 0
        
        backend.join('room:1', 'user1', {'name': 'Alice'})
        backend.join('room:1', 'user2', {'name': 'Bob'})
        
        assert backend.count('room:1') == 2
    
    def test_tenant_isolation(self):
        """Test that tenants are isolated from each other."""
        backend_acme = TenantAwareMemoryBackend(tenant_id='acme')
        backend_beta = TenantAwareMemoryBackend(tenant_id='beta')
        
        # Join same room in both tenants
        backend_acme.join('room:1', 'user1', {'name': 'Alice'})
        backend_beta.join('room:1', 'user2', {'name': 'Bob'})
        
        # Each tenant should only see their own users
        acme_presences = backend_acme.list('room:1')
        beta_presences = backend_beta.list('room:1')
        
        assert len(acme_presences) == 1
        assert acme_presences[0]['id'] == 'user1'
        
        assert len(beta_presences) == 1
        assert beta_presences[0]['id'] == 'user2'
    
    def test_cleanup_stale(self):
        """Test cleanup of stale presences."""
        backend = TenantAwareMemoryBackend(tenant_id='acme', timeout=0)
        
        backend.join('room:1', 'user1', {'name': 'Alice'})
        
        # Manually set old heartbeat
        backend._heartbeats['acme']['room:1:user1'] = time.time() - 100
        
        # Cleanup should remove stale
        removed = backend.cleanup_stale('room:1')
        assert removed == 1
        
        presences = backend.list('room:1')
        assert len(presences) == 0
    
    def test_clear_tenant(self):
        """Test clearing all data for a tenant."""
        backend = TenantAwareMemoryBackend(tenant_id='acme')
        backend.join('room:1', 'user1', {'name': 'Alice'})
        
        TenantAwareMemoryBackend.clear_tenant('acme')
        
        presences = backend.list('room:1')
        assert len(presences) == 0


class TestTenantPresenceManager:
    """Tests for TenantPresenceManager factory."""
    
    def test_for_tenant_creates_backend(self):
        """Test that for_tenant creates a backend."""
        backend = TenantPresenceManager.for_tenant('acme')
        
        assert backend is not None
        assert backend._tenant_id == 'acme'
    
    def test_for_tenant_caches_backends(self):
        """Test that backends are cached."""
        backend1 = TenantPresenceManager.for_tenant('acme')
        backend2 = TenantPresenceManager.for_tenant('acme')
        
        assert backend1 is backend2
    
    def test_different_tenants_different_backends(self):
        """Test that different tenants get different backends."""
        backend_acme = TenantPresenceManager.for_tenant('acme')
        backend_beta = TenantPresenceManager.for_tenant('beta')
        
        assert backend_acme is not backend_beta
        assert backend_acme._tenant_id == 'acme'
        assert backend_beta._tenant_id == 'beta'


# ============================================================================
# Context Processor Tests
# ============================================================================

class TestContextProcessor:
    """Tests for tenant context processor."""
    
    def test_adds_tenant_to_context(self):
        """Test that context processor adds tenant."""
        request = FakeRequest(host="acme.example.com")
        
        with patch('djust.tenants.mixin.resolve_tenant') as mock_resolve:
            mock_resolve.return_value = TenantInfo(tenant_id='acme')
            
            context = context_processor(request)
            
            assert 'tenant' in context
            assert context['tenant'].id == 'acme'
    
    def test_no_tenant_returns_none(self):
        """Test that None is returned when no tenant."""
        request = FakeRequest(host="example.com")
        
        with patch('djust.tenants.mixin.resolve_tenant', return_value=None):
            context = context_processor(request)
            
            assert 'tenant' in context
            assert context['tenant'] is None


# ============================================================================
# Integration Tests
# ============================================================================

class TestTenantIntegration:
    """Integration tests for the full tenant system."""
    
    def test_full_workflow(self):
        """Test complete tenant workflow."""
        # 1. Create view with tenant support
        class DashboardView(TenantMixin, FakeView):
            tenant_required = True
        
        # 2. Simulate request from tenant subdomain
        request = FakeRequest(host="acme.example.com")
        
        view = DashboardView()
        view.request = request
        
        # 3. Resolve tenant
        with patch('djust.tenants.mixin.resolve_tenant') as mock_resolve:
            mock_resolve.return_value = TenantInfo(
                tenant_id='acme',
                name='Acme Corp',
                settings={'theme': 'dark'}
            )
            
            view._ensure_tenant(request)
            
            # 4. Verify tenant is available
            assert view.tenant is not None
            assert view.tenant.id == 'acme'
            assert view.tenant.name == 'Acme Corp'
            assert view.tenant.get_setting('theme') == 'dark'
            
            # 5. Verify context includes tenant
            context = view.get_context_data()
            assert context['tenant'].id == 'acme'
    
    def test_multi_tenant_isolation(self):
        """Test that multiple tenants are fully isolated."""
        # Create presence backends for two tenants
        backend_acme = TenantPresenceManager.for_tenant('acme')
        backend_beta = TenantPresenceManager.for_tenant('beta')
        
        # Add users to same room in different tenants
        backend_acme.join('dashboard', 'alice', {'name': 'Alice'})
        backend_acme.join('dashboard', 'bob', {'name': 'Bob'})
        backend_beta.join('dashboard', 'charlie', {'name': 'Charlie'})
        
        # Verify isolation
        acme_users = backend_acme.list('dashboard')
        beta_users = backend_beta.list('dashboard')
        
        assert len(acme_users) == 2
        assert len(beta_users) == 1
        
        acme_ids = {u['id'] for u in acme_users}
        assert 'alice' in acme_ids
        assert 'bob' in acme_ids
        assert 'charlie' not in acme_ids
        
        beta_ids = {u['id'] for u in beta_users}
        assert 'charlie' in beta_ids
        assert 'alice' not in beta_ids
    
    def test_presence_key_scoping(self):
        """Test that presence keys are tenant-scoped."""
        class TestView(TenantMixin, FakeView):
            def get_presence_key(self):
                # Call TenantMixin's implementation
                base = "dashboard"
                if self._tenant:
                    return f"tenant:{self._tenant.id}:{base}"
                return base
        
        view = TestView()
        view._tenant = TenantInfo(tenant_id='acme')
        view._tenant_resolved = True
        
        key = view.get_presence_key()
        assert key == "tenant:acme:dashboard"
