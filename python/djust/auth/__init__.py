"""
djust.auth — Authentication and authorization for djust LiveViews.

Provides:
- View-level auth enforcement (before mount) and handler-level permission checks
- SignupView, DjustLoginView, and logout_view for standard auth flows
- LiveView-specific auth mixins (LoginRequiredLiveViewMixin, PermissionRequiredLiveViewMixin)
- OAuth/social auth helpers (requires django-allauth)
- URL patterns for signup/login/logout

Usage:
    # Class attributes (primary API — checked at WebSocket connect)
    class DashboardView(LiveView):
        login_required = True
        permission_required = "analytics.view_dashboard"

    # Dispatch-level mixins (checked at HTTP dispatch)
    from djust.auth import LoginRequiredLiveViewMixin

    class DashboardView(LoginRequiredLiveViewMixin, LiveView):
        template_name = "my_view.html"

    # URL patterns
    urlpatterns = [
        path("accounts/", include("djust.auth.urls")),
    ]
"""

# Core auth functions (originally djust/auth.py) — safe to import eagerly
# since they only depend on django.conf and django.core.exceptions
from .core import (
    LoginRequiredMixin,
    PermissionRequiredMixin,
    check_handler_permission,
    check_view_auth,
)

__all__ = [
    # Core auth (WebSocket-level)
    "check_view_auth",
    "check_handler_permission",
    "LoginRequiredMixin",
    "PermissionRequiredMixin",
    # Views (lazy)
    "SignupView",
    "DjustLoginView",
    "logout_view",
    # Forms (lazy)
    "SignupForm",
    # Dispatch-level mixins (lazy)
    "LoginRequiredLiveViewMixin",
    "PermissionRequiredLiveViewMixin",
    # Social/OAuth (lazy)
    "social_auth_providers",
]

# Lazy imports for modules that require Django app registry to be ready
_LAZY_IMPORTS = {
    "SignupView": ".views",
    "DjustLoginView": ".views",
    "logout_view": ".views",
    "SignupForm": ".forms",
    "LoginRequiredLiveViewMixin": ".mixins",
    "PermissionRequiredLiveViewMixin": ".mixins",
    "social_auth_providers": ".social",
}


def __getattr__(name):
    if name in _LAZY_IMPORTS:
        import importlib

        module = importlib.import_module(_LAZY_IMPORTS[name], __package__)
        return getattr(module, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
