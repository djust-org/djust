"""
Authentication and authorization for djust LiveViews.

Provides view-level auth enforcement (before mount) and handler-level
permission checks (before event handler execution).

Usage:
    # Class attributes (primary API)
    class DashboardView(LiveView):
        login_required = True
        permission_required = "analytics.view_dashboard"

    # Mixins (Django-familiar convenience)
    from djust.auth import LoginRequiredMixin, PermissionRequiredMixin

    class DashboardView(LoginRequiredMixin, PermissionRequiredMixin, LiveView):
        permission_required = "analytics.view_dashboard"

    # Custom hook
    class ProjectView(LiveView):
        login_required = True

        def check_permissions(self, request):
            project = Project.objects.get(pk=self.kwargs.get("pk"))
            return project.team.members.filter(user=request.user).exists()
"""

import logging
from typing import List, Optional, Union

from django.conf import settings
from django.core.exceptions import PermissionDenied

logger = logging.getLogger(__name__)


def check_view_auth(view_instance, request) -> Optional[str]:
    """Check view-level auth. Returns None if OK, or a redirect URL if denied.

    Called by websocket.py before mount(). Checks in order:
    1. login_required -- is user authenticated?
    2. permission_required -- does user have Django permission(s)?
    3. check_permissions() -- custom hook (if overridden by subclass)

    Args:
        view_instance: The LiveView instance being mounted.
        request: The Django request object.

    Returns:
        None if auth passes, or a login URL string to redirect to.

    Raises:
        PermissionDenied: If user is authenticated but lacks required
            permissions. Matches Django's PermissionRequiredMixin behavior.
    """
    login_required = getattr(view_instance, "login_required", None)
    permission_required = getattr(view_instance, "permission_required", None)
    login_url = getattr(view_instance, "login_url", None) or getattr(
        settings, "LOGIN_URL", "/accounts/login/"
    )

    # 1. Login check
    if login_required:
        user = getattr(request, "user", None)
        if user is None or not getattr(user, "is_authenticated", False):
            logger.info(
                "Auth denied for %s: user not authenticated",
                view_instance.__class__.__name__,
            )
            return login_url

    # 2. Permission check
    if permission_required:
        user = getattr(request, "user", None)
        if user is None:
            return login_url
        perms: tuple = (
            (permission_required,)
            if isinstance(permission_required, str)
            else tuple(permission_required)
        )
        if not user.has_perms(perms):
            logger.info(
                "Auth denied for %s: user lacks permission(s) %s",
                view_instance.__class__.__name__,
                perms,
            )
            # Authenticated but lacking perms → 403 (matches Django's
            # PermissionRequiredMixin). Unauthenticated → redirect to login.
            if getattr(user, "is_authenticated", False):
                raise PermissionDenied(f"User lacks required permission(s): {', '.join(perms)}")
            return login_url

    # 3. Custom hook (only if subclass overrides it)
    if _has_custom_check_permissions(view_instance):
        try:
            result = view_instance.check_permissions(request)
            if result is False:
                logger.info(
                    "Auth denied for %s: check_permissions() returned False",
                    view_instance.__class__.__name__,
                )
                return login_url
        except PermissionDenied:
            logger.info(
                "Auth denied for %s: check_permissions() raised PermissionDenied",
                view_instance.__class__.__name__,
            )
            return login_url

    return None  # Auth passed


def _has_custom_check_permissions(view_instance) -> bool:
    """Check if the view subclass overrides check_permissions()."""
    from .live_view import LiveView

    for klass in type(view_instance).__mro__:
        if klass is LiveView or klass is object:
            break
        if "check_permissions" in klass.__dict__:
            return True
    return False


def check_handler_permission(handler, request) -> bool:
    """Check handler-level @permission_required. Returns True if OK.

    Args:
        handler: The event handler method (may have _djust_decorators metadata).
        request: The Django request object.

    Returns:
        True if the user has the required permission(s), False otherwise.
    """
    meta = getattr(handler, "_djust_decorators", {})
    perm = meta.get("permission_required")
    if perm is None:
        return True
    user = getattr(request, "user", None)
    if user is None:
        return False
    perms: tuple = (perm,) if isinstance(perm, str) else tuple(perm)
    return user.has_perms(perms)


class LoginRequiredMixin:
    """Mixin that sets login_required = True. Django-familiar convenience.

    Usage:
        class MyView(LoginRequiredMixin, LiveView):
            template_name = "my_view.html"
    """

    login_required: bool = True


class PermissionRequiredMixin:
    """Mixin that enforces permission_required. Set the attribute on your view.

    Implicitly requires login too.

    Usage:
        class MyView(PermissionRequiredMixin, LiveView):
            permission_required = "myapp.view_item"
            template_name = "my_view.html"
    """

    login_required: bool = True
    permission_required: Optional[Union[str, List[str]]] = None
