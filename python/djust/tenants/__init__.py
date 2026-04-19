"""
djust.tenants — Multi-tenant support for djust LiveViews.

Enables building SaaS applications with tenant isolation:
- Automatic tenant resolution from request (subdomain, path, header, session)
- Tenant-scoped state and presence backends
- Tenant context in templates

Quick Start::

    from djust import LiveView
    from djust.tenants import TenantMixin

    class DashboardView(TenantMixin, LiveView):
        template_name = 'dashboard.html'

        def mount(self, request, **kwargs):
            # self.tenant is automatically set from request
            self.items = Item.objects.filter(tenant=self.tenant.id)

Configuration in settings.py::

    DJUST_CONFIG = {
        # Tenant resolution strategy
        'TENANT_RESOLVER': 'subdomain',  # 'subdomain', 'path', 'header', 'session', 'custom'

        # Subdomain options
        'TENANT_SUBDOMAIN_EXCLUDE': ['www', 'api', 'admin'],
        'TENANT_MAIN_DOMAIN': 'example.com',

        # Path options (example.com/acme/dashboard)
        'TENANT_PATH_POSITION': 1,
        'TENANT_PATH_EXCLUDE': ['admin', 'api', 'static'],

        # Header option (X-Tenant-ID header)
        'TENANT_HEADER': 'X-Tenant-ID',

        # Session option
        'TENANT_SESSION_KEY': 'tenant_id',

        # Custom resolver (dotted path to callable)
        'TENANT_CUSTOM_RESOLVER': 'myapp.tenants.resolve_tenant',

        # Behavior options
        'TENANT_REQUIRED': True,  # Raise 404 if no tenant found
        'TENANT_DEFAULT': None,  # Default tenant if none resolved
        'TENANT_CONTEXT_NAME': 'tenant',  # Name in template context

        # Tenant-scoped presence backend
        'PRESENCE_BACKEND': 'tenant_redis',  # or 'tenant_memory'
        'PRESENCE_REDIS_URL': 'redis://localhost:6379/0',
    }

Template usage::

    {{ tenant.name }}
    {{ tenant.settings.theme }}
    {{ tenant.id }}
"""

from .resolvers import (
    TenantInfo,
    TenantResolver,
    SubdomainResolver,
    PathResolver,
    HeaderResolver,
    SessionResolver,
    CustomResolver,
    ChainedResolver,
    get_tenant_resolver,
    resolve_tenant,
    RESOLVER_REGISTRY,
)

from .mixin import (
    TenantMixin,
    TenantScopedMixin,
    TenantContextProcessor,
    context_processor,
)

from .backends import (
    TenantAwareBackendMixin,
    TenantAwareRedisBackend,
    TenantAwareMemoryBackend,
    TenantPresenceManager,
    get_tenant_presence_backend,
)

__all__ = [
    # Tenant info
    "TenantInfo",
    # Resolvers
    "TenantResolver",
    "SubdomainResolver",
    "PathResolver",
    "HeaderResolver",
    "SessionResolver",
    "CustomResolver",
    "ChainedResolver",
    "get_tenant_resolver",
    "resolve_tenant",
    "RESOLVER_REGISTRY",
    # Middleware
    "TenantMiddleware",
    "get_current_tenant",
    "set_current_tenant",
    # Managers
    "TenantManager",
    "TenantQuerySet",
    # Mixins
    "TenantMixin",
    "TenantScopedMixin",
    "TenantContextProcessor",
    "context_processor",
    # Backends
    "TenantAwareBackendMixin",
    "TenantAwareRedisBackend",
    "TenantAwareMemoryBackend",
    "TenantPresenceManager",
    "get_tenant_presence_backend",
    # Audit
    "AuditEvent",
    "AuditBackend",
    "LoggingAuditBackend",
    "DatabaseAuditBackend",
    "CallbackAuditBackend",
    "get_audit_backend",
    "emit_audit",
    "audit_action",
    # Security
    "SecurityHeadersMiddleware",
]

# Lazy imports for modules that require Django ORM
_LAZY_IMPORTS = {
    "TenantMiddleware": ".middleware",
    "get_current_tenant": ".middleware",
    "set_current_tenant": ".middleware",
    "TenantManager": ".managers",
    "TenantQuerySet": ".managers",
    "AuditEvent": ".audit",
    "AuditBackend": ".audit",
    "LoggingAuditBackend": ".audit",
    "DatabaseAuditBackend": ".audit",
    "CallbackAuditBackend": ".audit",
    "get_audit_backend": ".audit",
    "emit_audit": ".audit",
    "audit_action": ".audit",
    "SecurityHeadersMiddleware": ".security",
}


def __getattr__(name):
    if name in _LAZY_IMPORTS:
        import importlib

        module = importlib.import_module(_LAZY_IMPORTS[name], __package__)
        return getattr(module, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
