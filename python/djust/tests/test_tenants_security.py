"""Tests for djust.tenants.security (from djust-tenants package)."""

import pytest
from django.http import HttpResponse
from django.test import RequestFactory, override_settings

from djust.tenants.resolvers import TenantInfo
from djust.tenants.security import SecurityHeadersMiddleware

pytestmark = pytest.mark.tenants


@pytest.fixture
def factory():
    return RequestFactory()


def _make_middleware(response_factory=None):
    if response_factory is None:

        def response_factory(request):
            return HttpResponse("ok")

    return SecurityHeadersMiddleware(response_factory)


class TestSecurityHeaders:
    def test_default_headers_present(self, factory):
        request = factory.get("/")
        middleware = _make_middleware()
        response = middleware(request)

        assert response["X-Content-Type-Options"] == "nosniff"
        assert response["X-Frame-Options"] == "DENY"
        assert response["Referrer-Policy"] == "strict-origin-when-cross-origin"
        assert response["Permissions-Policy"] == ("camera=(), microphone=(), geolocation=()")
        assert response["Cross-Origin-Opener-Policy"] == "same-origin"

    def test_csp_default(self, factory):
        request = factory.get("/")
        middleware = _make_middleware()
        response = middleware(request)
        assert response["Content-Security-Policy"] == "default-src 'self'"

    @override_settings(
        DJUST_TENANTS={
            "SECURITY_HEADERS": True,
            "CSP_DEFAULT": "default-src 'self' https://cdn.example.com",
        }
    )
    def test_custom_csp(self, factory):
        request = factory.get("/")
        middleware = _make_middleware()
        response = middleware(request)
        assert "https://cdn.example.com" in response["Content-Security-Policy"]

    @override_settings(DJUST_TENANTS={"SECURITY_HEADERS": False})
    def test_disabled(self, factory):
        request = factory.get("/")
        middleware = _make_middleware()
        response = middleware(request)
        assert "X-Content-Type-Options" not in response
        assert "Content-Security-Policy" not in response

    def test_does_not_override_existing_csp(self, factory):
        def response_with_csp(request):
            resp = HttpResponse("ok")
            resp["Content-Security-Policy"] = "custom-policy"
            return resp

        request = factory.get("/")
        middleware = SecurityHeadersMiddleware(response_with_csp)
        response = middleware(request)
        assert response["Content-Security-Policy"] == "custom-policy"


class TestTenantAwareCSP:
    def test_tenant_csp_domains_appended(self, factory):
        request = factory.get("/")
        request.tenant = TenantInfo(
            tenant_id="acme",
            settings={"csp_allowed_domains": "https://acme.cdn.com"},
        )
        middleware = _make_middleware()
        response = middleware(request)
        csp = response["Content-Security-Policy"]
        assert "default-src 'self'" in csp
        assert "https://acme.cdn.com" in csp

    def test_no_tenant_uses_default_csp(self, factory):
        request = factory.get("/")
        middleware = _make_middleware()
        response = middleware(request)
        assert response["Content-Security-Policy"] == "default-src 'self'"

    def test_tenant_without_csp_setting(self, factory):
        request = factory.get("/")
        request.tenant = TenantInfo(tenant_id="basic")
        middleware = _make_middleware()
        response = middleware(request)
        assert response["Content-Security-Policy"] == "default-src 'self'"

    def test_csp_injection_blocked(self, factory):
        """Malicious tenant CSP domains with directives should be rejected."""
        request = factory.get("/")
        request.tenant = TenantInfo(
            tenant_id="evil",
            settings={"csp_allowed_domains": "https://evil.com; script-src 'unsafe-inline'"},
        )
        middleware = _make_middleware()
        response = middleware(request)
        # Should NOT contain the injected directive
        assert "unsafe-inline" not in response["Content-Security-Policy"]
        assert response["Content-Security-Policy"] == "default-src 'self'"
