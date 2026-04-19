from __future__ import annotations

from django.conf import settings


class SecurityHeadersMiddleware:
    """OWASP security headers middleware with tenant-aware CSP."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        response = self.get_response(request)

        config = getattr(settings, "DJUST_TENANTS", {})
        if not config.get("SECURITY_HEADERS", True):
            return response

        response["X-Content-Type-Options"] = "nosniff"
        response["X-Frame-Options"] = "DENY"
        response["Referrer-Policy"] = "strict-origin-when-cross-origin"
        response["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
        response["Cross-Origin-Opener-Policy"] = "same-origin"

        if "Content-Security-Policy" not in response:
            csp = config.get("CSP_DEFAULT", "default-src 'self'")
            tenant = getattr(request, "tenant", None)
            if tenant is not None:
                allowed = None
                if hasattr(tenant, "get_setting"):
                    allowed = tenant.get_setting("csp_allowed_domains")
                if allowed:
                    csp = f"{csp} {allowed}"
            response["Content-Security-Policy"] = csp

        return response
