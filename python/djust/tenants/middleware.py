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

        # Short-circuit: when neither DJUST_CONFIG['TENANT_RESOLVER'] nor
        # DJUST_TENANTS is configured, the middleware is effectively a
        # no-op — it would call SubdomainResolver, get back None, set
        # request.tenant=None, set+clear a thread-local, and run the
        # required-gate (which is False by default). For consumers who
        # have djust[tenants] installed but never opted in (single-tenant
        # deploys, scaffold starters, demo apps), that's pure overhead
        # on every request. Detect once at boot, switch __call__ to a
        # straight passthrough.
        from django.conf import settings

        djust_config = getattr(settings, "DJUST_CONFIG", {}) or {}
        djust_tenants = getattr(settings, "DJUST_TENANTS", {}) or {}
        self._enabled = "TENANT_RESOLVER" in djust_config or bool(djust_tenants)

        if self._enabled:
            self.resolver = get_tenant_resolver()
        else:
            # Skip resolver instantiation too — it reads settings.
            self.resolver = None

    def __call__(self, request):
        if not self._enabled:
            # Preserve `request.tenant` attribute existence so consumer
            # code doing `getattr(request, "tenant", None)` keeps working
            # the same as before. (Direct `request.tenant.id` was already
            # broken whether the middleware ran with no resolver or not.)
            request.tenant = None
            return self.get_response(request)

        # Resolve tenant
        tenant = self.resolver.resolve(request)

        # Set on request
        request.tenant = tenant

        # Set in thread-local storage (for use outside views)
        set_current_tenant(tenant)

        # Check if tenant is required — check both DJUST_CONFIG (core) and
        # DJUST_TENANTS (standalone compat) settings
        from django.conf import settings

        config = getattr(settings, "DJUST_TENANTS", {})
        required = config.get("REQUIRED", False)
        if not required:
            djust_config = getattr(settings, "DJUST_CONFIG", {})
            required = djust_config.get("TENANT_REQUIRED", False)

        if required and not tenant:
            raise Http404("Tenant not found")

        try:
            response = self.get_response(request)
            return response
        finally:
            # Clear thread-local after request
            set_current_tenant(None)
