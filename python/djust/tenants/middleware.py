"""Tenant middleware for automatically resolving and injecting tenant into requests."""

from threading import local

from django.http import Http404

from .resolvers import get_tenant_resolver

# Thread-local storage for current tenant
_thread_locals = local()


def get_current_tenant():
    """Get the current tenant from thread-local storage.

    Returns:
        TenantInfo or None: Current tenant or None if not set
    """
    return getattr(_thread_locals, "tenant", None)


def set_current_tenant(tenant):
    """Set the current tenant in thread-local storage.

    Args:
        tenant (TenantInfo or None): Tenant to set
    """
    _thread_locals.tenant = tenant


class TenantMiddleware:
    """Middleware that resolves tenant and injects into request.

    Usage:
        # settings.py
        MIDDLEWARE = [
            # ...
            'djust.tenants.middleware.TenantMiddleware',
        ]

        DJUST_TENANTS = {
            'RESOLVER': 'subdomain',
            'REQUIRED': True,  # Raise 404 if no tenant found
        }

    The middleware will:
    1. Resolve tenant using configured resolver
    2. Set request.tenant
    3. Set thread-local tenant (for use outside request context)
    4. Optionally raise 404 if REQUIRED=True and no tenant found
    """

    def __init__(self, get_response):
        self.get_response = get_response
        self.resolver = get_tenant_resolver()

    def __call__(self, request):
        # Resolve tenant
        tenant = self.resolver.resolve(request)

        # Set on request
        request.tenant = tenant

        # Set in thread-local storage (for use outside views)
        set_current_tenant(tenant)

        # Check if tenant is required
        from django.conf import settings

        config = getattr(settings, "DJUST_TENANTS", {})

        if config.get("REQUIRED", False) and not tenant:
            raise Http404("Tenant not found")

        try:
            response = self.get_response(request)
            return response
        finally:
            # Clear thread-local after request
            set_current_tenant(None)
