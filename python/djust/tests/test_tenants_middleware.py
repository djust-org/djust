"""Tests for djust.tenants.middleware (from djust-tenants package)."""

import pytest
from django.http import Http404
from django.test import RequestFactory, override_settings

from djust.tenants.middleware import (
    TenantMiddleware,
    get_current_tenant,
    set_current_tenant,
)

pytestmark = pytest.mark.tenants


@pytest.fixture
def rf():
    return RequestFactory()


class TestTenantMiddleware:
    @override_settings(
        DJUST_CONFIG={
            "TENANT_RESOLVER": "subdomain",
            "TENANT_MAIN_DOMAIN": "example.com",
        }
    )
    def test_middleware_sets_tenant(self, rf):
        def get_response(request):
            assert hasattr(request, "tenant")
            assert get_current_tenant() is not None
            from django.http import HttpResponse

            return HttpResponse("OK")

        middleware = TenantMiddleware(get_response)
        request = rf.get("/")
        request.META["HTTP_HOST"] = "acme.example.com"
        response = middleware(request)
        assert response.status_code == 200

    @override_settings(
        DJUST_CONFIG={
            "TENANT_RESOLVER": "subdomain",
            "TENANT_MAIN_DOMAIN": "example.com",
        }
    )
    def test_middleware_clears_thread_local(self, rf):
        def get_response(request):
            from django.http import HttpResponse

            return HttpResponse("OK")

        middleware = TenantMiddleware(get_response)
        request = rf.get("/")
        request.META["HTTP_HOST"] = "acme.example.com"
        middleware(request)
        assert get_current_tenant() is None

    @override_settings(
        DJUST_CONFIG={
            "TENANT_RESOLVER": "subdomain",
            "TENANT_MAIN_DOMAIN": "example.com",
            "TENANT_REQUIRED": True,
        }
    )
    def test_middleware_raises_404_when_required(self, rf):
        def get_response(request):
            from django.http import HttpResponse

            return HttpResponse("OK")

        middleware = TenantMiddleware(get_response)
        request = rf.get("/")
        request.META["HTTP_HOST"] = "www.example.com"
        with pytest.raises(Http404, match="Tenant not found"):
            middleware(request)

    @override_settings(
        DJUST_CONFIG={
            "TENANT_RESOLVER": "subdomain",
            "TENANT_MAIN_DOMAIN": "example.com",
        }
    )
    def test_middleware_allows_no_tenant(self, rf):
        def get_response(request):
            assert hasattr(request, "tenant")
            assert request.tenant is None
            from django.http import HttpResponse

            return HttpResponse("OK")

        middleware = TenantMiddleware(get_response)
        request = rf.get("/")
        request.META["HTTP_HOST"] = "www.example.com"
        response = middleware(request)
        assert response.status_code == 200


class TestThreadLocalHelpers:
    def test_set_and_get(self):
        from djust.tenants.resolvers import TenantInfo

        tenant = TenantInfo(tenant_id="test")
        set_current_tenant(tenant)
        assert get_current_tenant() == tenant
        set_current_tenant(None)
        assert get_current_tenant() is None

    def test_default_is_none(self):
        set_current_tenant(None)
        assert get_current_tenant() is None


class TestTenantMiddlewareShortCircuit:
    """#1436: when neither DJUST_CONFIG['TENANT_RESOLVER'] nor
    DJUST_TENANTS is configured, the middleware bypasses the resolver
    entirely and just calls get_response. Saves per-request CPU for
    consumers who have djust[tenants] installed but never opted in.
    """

    @override_settings(DJUST_CONFIG={}, DJUST_TENANTS={})
    def test_short_circuits_when_unconfigured(self, rf):
        """Both DJUST_CONFIG and DJUST_TENANTS empty → no-op path."""
        called = []

        def get_response(request):
            called.append(request)
            from django.http import HttpResponse

            return HttpResponse("OK")

        middleware = TenantMiddleware(get_response)

        # The short-circuit branch must skip resolver instantiation.
        assert middleware._enabled is False
        assert middleware.resolver is None

        request = rf.get("/")
        request.META["HTTP_HOST"] = "acme.example.com"
        response = middleware(request)

        assert response.status_code == 200
        assert called == [request]
        # request.tenant is still set to None (attribute existence
        # preserved for `getattr(request, "tenant", None)` callers).
        assert request.tenant is None
        # Thread-local was NOT touched by the no-op path.
        assert get_current_tenant() is None

    @override_settings(
        DJUST_CONFIG={"TENANT_RESOLVER": "subdomain", "TENANT_MAIN_DOMAIN": "example.com"},
        DJUST_TENANTS={},
    )
    def test_does_not_short_circuit_with_djust_config(self, rf):
        """DJUST_CONFIG['TENANT_RESOLVER'] set → full path runs."""
        from django.http import HttpResponse

        middleware = TenantMiddleware(lambda r: HttpResponse("OK"))
        assert middleware._enabled is True
        assert middleware.resolver is not None

    @override_settings(
        DJUST_CONFIG={},
        DJUST_TENANTS={"RESOLVER": "subdomain"},
    )
    def test_does_not_short_circuit_with_djust_tenants(self, rf):
        """DJUST_TENANTS set (legacy/standalone shape) → full path runs."""
        from django.http import HttpResponse

        middleware = TenantMiddleware(lambda r: HttpResponse("OK"))
        assert middleware._enabled is True
        assert middleware.resolver is not None

    @override_settings(DJUST_CONFIG={}, DJUST_TENANTS={})
    def test_short_circuit_does_not_set_thread_local(self, rf):
        """The no-op path skips set/clear of the thread-local entirely —
        that's part of the savings."""
        from unittest.mock import patch

        from django.http import HttpResponse

        with patch(
            "djust.tenants.middleware.set_current_tenant",
        ) as mock_set:
            middleware = TenantMiddleware(lambda r: HttpResponse("OK"))
            request = rf.get("/")
            request.META["HTTP_HOST"] = "host.example.com"
            middleware(request)
            assert mock_set.call_count == 0
