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
