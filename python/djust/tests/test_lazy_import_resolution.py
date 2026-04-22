"""Regression tests for __all__ / __getattr__ lazy imports.

Each name in __all__ must resolve at runtime via the __getattr__
dispatcher. This guards against the class of bug CodeQL's py/undefined-export
flags — if a name is removed from _LAZY_IMPORTS but left in __all__,
`from djust.auth import X` raises AttributeError at import.
"""

import pytest


AUTH_NAMES = [
    "check_view_auth",
    "check_handler_permission",
    "LoginRequiredMixin",
    "PermissionRequiredMixin",
    "SignupView",
    "DjustLoginView",
    "logout_view",
    "SignupForm",
    "LoginRequiredLiveViewMixin",
    "PermissionRequiredLiveViewMixin",
    "social_auth_providers",
]


TENANTS_NAMES = [
    # Non-lazy (imported eagerly at top of module):
    "TenantInfo",
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
    "TenantMixin",
    "TenantScopedMixin",
    "TenantContextProcessor",
    "context_processor",
    "TenantAwareBackendMixin",
    "TenantAwareRedisBackend",
    "TenantAwareMemoryBackend",
    "TenantPresenceManager",
    "get_tenant_presence_backend",
    # Lazy (via __getattr__):
    "TenantMiddleware",
    "get_current_tenant",
    "set_current_tenant",
    "TenantManager",
    "TenantQuerySet",
    "AuditEvent",
    "AuditBackend",
    "LoggingAuditBackend",
    "DatabaseAuditBackend",
    "CallbackAuditBackend",
    "get_audit_backend",
    "emit_audit",
    "audit_action",
    "SecurityHeadersMiddleware",
]


@pytest.mark.parametrize("name", AUTH_NAMES)
def test_auth_all_names_resolve(name):
    import djust.auth as mod

    val = getattr(mod, name)
    assert val is not None, f"djust.auth.{name} resolved to None"


@pytest.mark.parametrize("name", TENANTS_NAMES)
def test_tenants_all_names_resolve(name):
    import djust.tenants as mod

    val = getattr(mod, name)
    assert val is not None, f"djust.tenants.{name} resolved to None"


def test_auth_all_matches_hasattr():
    import djust.auth as mod

    for name in mod.__all__:
        assert hasattr(mod, name), f"djust.auth.__all__ contains {name!r} but it doesn't resolve"


def test_tenants_all_matches_hasattr():
    import djust.tenants as mod

    for name in mod.__all__:
        assert hasattr(mod, name), f"djust.tenants.__all__ contains {name!r} but it doesn't resolve"
