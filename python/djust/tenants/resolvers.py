"""
Tenant resolution strategies for multi-tenant djust applications.

Supports multiple tenant identification methods:
- Subdomain: customer.example.com
- Path prefix: example.com/customer/...
- Header: X-Tenant-ID header
- Session: From session data or JWT claims
- Custom: User-defined callable

Configuration in settings.py::

    DJUST_CONFIG = {
        'TENANT_RESOLVER': 'subdomain',  # or 'path', 'header', 'session', 'custom'
        'TENANT_HEADER': 'X-Tenant-ID',
        'TENANT_SESSION_KEY': 'tenant_id',
        'TENANT_CUSTOM_RESOLVER': 'myapp.tenants.resolve_tenant',  # dotted path
        'TENANT_DEFAULT': None,  # Default tenant if none resolved
    }
"""

import logging
import re
from abc import ABC, abstractmethod
from typing import Any, Callable, Dict, Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from django.http import HttpRequest

logger = logging.getLogger(__name__)


class TenantInfo:
    """
    Container for resolved tenant information.

    Provides a consistent interface for accessing tenant data regardless
    of how the tenant was resolved.

    Attributes:
        id: Unique tenant identifier (string)
        name: Human-readable tenant name (optional)
        settings: Tenant-specific settings dict (optional)
        metadata: Additional tenant metadata (optional)
    """

    __slots__ = ("id", "name", "settings", "metadata", "_raw")

    def __init__(
        self,
        tenant_id: str,
        name: Optional[str] = None,
        settings: Optional[Dict[str, Any]] = None,
        metadata: Optional[Dict[str, Any]] = None,
        raw: Any = None,
    ):
        self.id = tenant_id
        self.name = name or tenant_id
        self.settings = settings or {}
        self.metadata = metadata or {}
        self._raw = raw  # Original tenant object (e.g., Django model instance)

    def __str__(self) -> str:
        return self.id

    def __repr__(self) -> str:
        return f"TenantInfo(id={self.id!r}, name={self.name!r})"

    def __eq__(self, other) -> bool:
        if isinstance(other, TenantInfo):
            return self.id == other.id
        if isinstance(other, str):
            return self.id == other
        return False

    def __hash__(self) -> int:
        return hash(self.id)

    @property
    def raw(self) -> Any:
        """Access the original tenant object (e.g., model instance)."""
        return self._raw

    def get_setting(self, key: str, default: Any = None) -> Any:
        """Get a tenant-specific setting."""
        return self.settings.get(key, default)


class TenantResolver(ABC):
    """
    Abstract base class for tenant resolution strategies.

    Subclasses implement `resolve()` to extract tenant information from
    an HTTP request.
    """

    @abstractmethod
    def resolve(self, request: "HttpRequest") -> Optional[TenantInfo]:
        """
        Resolve tenant from the given request.

        Args:
            request: Django HttpRequest object

        Returns:
            TenantInfo if tenant found, None otherwise
        """
        ...

    def get_config(self, key: str, default: Any = None) -> Any:
        """Get a value from DJUST_CONFIG."""
        try:
            from django.conf import settings

            config = getattr(settings, "DJUST_CONFIG", {})
            return config.get(key, default)
        except Exception:
            logger.debug("Could not load DJUST_CONFIG for key %s, using default", key)
            return default


class SubdomainResolver(TenantResolver):
    """
    Resolve tenant from subdomain: customer.example.com

    Configuration::

        DJUST_CONFIG = {
            'TENANT_RESOLVER': 'subdomain',
            'TENANT_SUBDOMAIN_EXCLUDE': ['www', 'api', 'admin'],  # Ignored subdomains
            'TENANT_MAIN_DOMAIN': 'example.com',  # Optional: explicit main domain
        }
    """

    def resolve(self, request: "HttpRequest") -> Optional[TenantInfo]:
        host = request.get_host().split(":")[0]  # Remove port

        # Get configured exclusions
        exclude = self.get_config("TENANT_SUBDOMAIN_EXCLUDE", ["www", "api", "admin"])
        main_domain = self.get_config("TENANT_MAIN_DOMAIN")

        # Split host into parts
        parts = host.split(".")

        if len(parts) < 2:
            return None

        # If main domain is configured, extract subdomain explicitly
        if main_domain:
            if not host.endswith(main_domain):
                return None
            subdomain = host[: -len(main_domain)].rstrip(".")
            if not subdomain or subdomain in exclude:
                return None
            return TenantInfo(tenant_id=subdomain)

        # Otherwise, assume first part is subdomain for multi-part hosts
        if len(parts) >= 3:
            subdomain = parts[0]
            if subdomain not in exclude:
                logger.debug("Resolved tenant from subdomain: %s", subdomain)
                return TenantInfo(tenant_id=subdomain)

        return None


class PathResolver(TenantResolver):
    """
    Resolve tenant from URL path: example.com/customer/dashboard

    Configuration::

        DJUST_CONFIG = {
            'TENANT_RESOLVER': 'path',
            'TENANT_PATH_POSITION': 1,  # Position in path (1 = first segment after /)
            'TENANT_PATH_EXCLUDE': ['admin', 'api', 'static'],  # Excluded paths
        }
    """

    def resolve(self, request: "HttpRequest") -> Optional[TenantInfo]:
        path = request.path.strip("/")
        if not path:
            return None

        parts = path.split("/")
        position = self.get_config("TENANT_PATH_POSITION", 1) - 1
        exclude = self.get_config("TENANT_PATH_EXCLUDE", ["admin", "api", "static", "media"])

        if len(parts) <= position:
            return None

        tenant_id = parts[position]

        if tenant_id in exclude:
            return None

        # Validate tenant ID format (alphanumeric, hyphens, underscores)
        if not re.match(r"^[a-zA-Z0-9_-]+$", tenant_id):
            return None

        logger.debug("Resolved tenant from path: %s", tenant_id)
        return TenantInfo(tenant_id=tenant_id)


class HeaderResolver(TenantResolver):
    """
    Resolve tenant from HTTP header: X-Tenant-ID

    Configuration::

        DJUST_CONFIG = {
            'TENANT_RESOLVER': 'header',
            'TENANT_HEADER': 'X-Tenant-ID',  # Header name
        }
    """

    def resolve(self, request: "HttpRequest") -> Optional[TenantInfo]:
        header_name = self.get_config("TENANT_HEADER", "X-Tenant-ID")

        # Django converts headers to META keys
        # X-Tenant-ID -> HTTP_X_TENANT_ID
        meta_key = f"HTTP_{header_name.upper().replace('-', '_')}"

        tenant_id = request.META.get(meta_key)

        if not tenant_id:
            # Also try lowercase (some proxies normalize)
            tenant_id = request.META.get(meta_key.lower())

        if tenant_id:
            logger.debug("Resolved tenant from header %s: %s", header_name, tenant_id)
            return TenantInfo(tenant_id=tenant_id)

        return None


class SessionResolver(TenantResolver):
    """
    Resolve tenant from session or JWT claims.

    Configuration::

        DJUST_CONFIG = {
            'TENANT_RESOLVER': 'session',
            'TENANT_SESSION_KEY': 'tenant_id',  # Session key
            'TENANT_JWT_CLAIM': 'tenant_id',  # JWT claim name (if using JWT)
        }
    """

    def resolve(self, request: "HttpRequest") -> Optional[TenantInfo]:
        session_key = self.get_config("TENANT_SESSION_KEY", "tenant_id")

        # Try session first
        if hasattr(request, "session"):
            tenant_id = request.session.get(session_key)
            if tenant_id:
                logger.debug("Resolved tenant from session: %s", tenant_id)
                return TenantInfo(tenant_id=str(tenant_id))

        # Try JWT claims (if user has jwt_payload attribute)
        if hasattr(request, "user") and hasattr(request.user, "jwt_payload"):
            jwt_claim = self.get_config("TENANT_JWT_CLAIM", "tenant_id")
            tenant_id = request.user.jwt_payload.get(jwt_claim)
            if tenant_id:
                logger.debug("Resolved tenant from JWT: %s", tenant_id)
                return TenantInfo(tenant_id=str(tenant_id))

        # Try user attribute (e.g., user.tenant_id from model)
        if hasattr(request, "user") and hasattr(request.user, "tenant_id"):
            tenant_id = request.user.tenant_id
            if tenant_id:
                logger.debug("Resolved tenant from user.tenant_id: %s", tenant_id)
                return TenantInfo(tenant_id=str(tenant_id))

        return None


class CustomResolver(TenantResolver):
    """
    Resolve tenant using a custom callable.

    Configuration::

        DJUST_CONFIG = {
            'TENANT_RESOLVER': 'custom',
            'TENANT_CUSTOM_RESOLVER': 'myapp.tenants.resolve_tenant',
        }

    The callable should have signature::

        def resolve_tenant(request: HttpRequest) -> Optional[TenantInfo]:
            # Your custom logic
            return TenantInfo(tenant_id='...')
    """

    _resolver_cache: Optional[Callable] = None

    def resolve(self, request: "HttpRequest") -> Optional[TenantInfo]:
        resolver = self._get_resolver()
        if not resolver:
            logger.warning("No custom tenant resolver configured")
            return None

        result = resolver(request)

        # Handle case where resolver returns string instead of TenantInfo
        if isinstance(result, str):
            return TenantInfo(tenant_id=result)

        return result

    def _get_resolver(self) -> Optional[Callable]:
        if self._resolver_cache is not None:
            return self._resolver_cache

        resolver_path = self.get_config("TENANT_CUSTOM_RESOLVER")
        if not resolver_path:
            return None

        try:
            from django.utils.module_loading import import_string

            self._resolver_cache = import_string(resolver_path)
            return self._resolver_cache
        except ImportError as e:
            logger.error("Failed to import custom tenant resolver %s: %s", resolver_path, e)
            return None


class ChainedResolver(TenantResolver):
    """
    Chain multiple resolvers, returning the first successful match.

    Configuration::

        DJUST_CONFIG = {
            'TENANT_RESOLVER': ['header', 'subdomain', 'session'],  # List of resolvers
        }
    """

    def __init__(self, resolvers: list):
        self.resolvers = resolvers

    def resolve(self, request: "HttpRequest") -> Optional[TenantInfo]:
        for resolver in self.resolvers:
            result = resolver.resolve(request)
            if result:
                return result
        return None


# Registry of built-in resolvers
RESOLVER_REGISTRY: Dict[str, type] = {
    "subdomain": SubdomainResolver,
    "path": PathResolver,
    "header": HeaderResolver,
    "session": SessionResolver,
    "custom": CustomResolver,
}


def get_tenant_resolver() -> TenantResolver:
    """
    Get the configured tenant resolver.

    Returns:
        Configured TenantResolver instance
    """
    try:
        from django.conf import settings

        config = getattr(settings, "DJUST_CONFIG", {})
    except Exception:
        logger.debug("Could not load DJUST_CONFIG for tenant resolver, using defaults")
        config = {}

    resolver_config = config.get("TENANT_RESOLVER", "subdomain")

    # Handle list of resolvers (chained)
    if isinstance(resolver_config, (list, tuple)):
        resolvers = []
        for name in resolver_config:
            if name in RESOLVER_REGISTRY:
                resolvers.append(RESOLVER_REGISTRY[name]())
            else:
                logger.warning("Unknown tenant resolver: %s", name)
        return ChainedResolver(resolvers)

    # Handle callable
    if callable(resolver_config):
        return _CallableResolver(resolver_config)

    # Handle string resolver name
    if resolver_config in RESOLVER_REGISTRY:
        return RESOLVER_REGISTRY[resolver_config]()

    logger.warning("Unknown tenant resolver: %s, using subdomain", resolver_config)
    return SubdomainResolver()


class _CallableResolver(TenantResolver):
    """Wrapper for callable resolvers."""

    def __init__(self, func: Callable):
        self.func = func

    def resolve(self, request: "HttpRequest") -> Optional[TenantInfo]:
        result = self.func(request)
        if isinstance(result, str):
            return TenantInfo(tenant_id=result)
        return result


def resolve_tenant(request: "HttpRequest") -> Optional[TenantInfo]:
    """
    Convenience function to resolve tenant from request.

    Args:
        request: Django HttpRequest

    Returns:
        TenantInfo if tenant found, None otherwise
    """
    resolver = get_tenant_resolver()
    tenant = resolver.resolve(request)

    # Apply default if configured
    if tenant is None:
        try:
            from django.conf import settings

            config = getattr(settings, "DJUST_CONFIG", {})
            default_tenant = config.get("TENANT_DEFAULT")
            if default_tenant:
                return TenantInfo(tenant_id=default_tenant)
        except Exception:
            logger.debug("Could not load DJUST_CONFIG for default tenant")

    return tenant
