"""Tenant middleware for automatically resolving and injecting tenant into requests."""

from contextlib import contextmanager
from contextvars import ContextVar
from typing import TYPE_CHECKING, Callable, Iterator, Optional

from django.http import Http404

from .resolvers import get_tenant_resolver

if TYPE_CHECKING:
    from django.http import HttpRequest, HttpResponse

    from .resolvers import TenantInfo, TenantResolver

# Per-(async-task / thread) storage for the current tenant.
#
# SECURITY (Finding #6, CWE-636/CWE-862): this MUST be a ``contextvars.ContextVar``,
# NOT ``threading.local()``. The live (WebSocket/SSE) path runs view code via
# ``asgiref.sync.sync_to_async(thread_sensitive=True)``, which dispatches EVERY
# connection's sync work onto a SINGLE shared executor thread. With
# ``threading.local`` that thread's tenant value is shared across all concurrent
# connections — connection B can read the tenant connection A just set (a
# cross-tenant data-disclosure clobber). ``ContextVar`` is scoped to the current
# async task / context: ``sync_to_async`` copies the caller's ``contextvars``
# context into the executor call (and back), so each connection's tenant stays
# isolated. ``ContextVar`` also behaves correctly on the plain-sync HTTP path.
_current_tenant: "ContextVar[Optional[TenantInfo]]" = ContextVar(
    "djust_current_tenant", default=None
)


def get_current_tenant() -> "Optional[TenantInfo]":
    """Get the current tenant from the context-local storage.

    Returns:
        TenantInfo or None: Current tenant or None if not set
    """
    return _current_tenant.get()


def set_current_tenant(tenant: "Optional[TenantInfo]") -> None:
    """Set the current tenant in the context-local storage.

    Args:
        tenant (TenantInfo or None): Tenant to set
    """
    _current_tenant.set(tenant)


@contextmanager
def tenant_context(tenant: "Optional[TenantInfo]") -> Iterator[None]:
    """Bind *tenant* as the current tenant for the duration of the ``with`` block.

    Sets the :data:`_current_tenant` ContextVar on entry and restores the
    previous value on exit (via :meth:`ContextVar.reset`), so set/clear can't
    drift across the many early-return paths of the live event/mount handlers.

    Use this at every WebSocket/SSE mount and event-dispatch site so the
    tenant-scoped managers (:class:`~djust.tenants.managers.TenantManager`,
    :class:`~djust.tenants.managers.TenantQuerySet`) see the resolved tenant
    during live-path queries — without it they fall back to fail-closed
    (empty) querysets under STRICT_MODE (the safe default).

    Args:
        tenant (TenantInfo or None): Tenant to bind for the block.
    """
    token = _current_tenant.set(tenant)
    try:
        yield
    finally:
        _current_tenant.reset(token)


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

    def __init__(self, get_response: Callable[["HttpRequest"], "HttpResponse"]) -> None:
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

        self.resolver: Optional["TenantResolver"]
        if self._enabled:
            self.resolver = get_tenant_resolver()
        else:
            # Skip resolver instantiation too — it reads settings.
            self.resolver = None

    def __call__(self, request: "HttpRequest") -> "HttpResponse":
        if not self._enabled:
            # Preserve `request.tenant` attribute existence so consumer
            # code doing `getattr(request, "tenant", None)` keeps working
            # the same as before. (Direct `request.tenant.id` was already
            # broken whether the middleware ran with no resolver or not.)
            request.tenant = None
            return self.get_response(request)

        # Resolve tenant. self.resolver is non-None whenever self._enabled is
        # True (set together in __init__); the early return above covers the
        # disabled case, so this access is safe.
        assert self.resolver is not None
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
