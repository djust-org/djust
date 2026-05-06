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
    from djust.live_view import LiveView

    for klass in type(view_instance).__mro__:
        if klass is LiveView or klass is object:
            break
        if "check_permissions" in klass.__dict__:
            return True
    return False


def _has_custom_get_object(view_instance) -> bool:
    """Check if the view subclass overrides get_object().

    Mirrors :func:`_has_custom_check_permissions` for the object-permission
    lifecycle (ADR-017, v0.9.5-1a). When a subclass between the user's
    view and ``LiveView`` defines ``get_object``, the lifecycle activates;
    otherwise :func:`check_object_permission` short-circuits as a no-op.
    """
    from djust.live_view import LiveView

    for klass in type(view_instance).__mro__:
        if klass is LiveView or klass is object:
            break
        if "get_object" in klass.__dict__:
            return True
    return False


def check_object_permission(view_instance, request) -> None:
    """Step 4 of ADR-017's auth onion — post-mount object-permission check.

    Called by ``websocket.py:handle_mount`` AFTER ``mount()`` has executed
    and URL-derived attrs (e.g. ``self.document_id``) are populated on the
    view. The pre-mount steps (login → role → ``check_permissions``) live
    inside :func:`check_view_auth`; this fourth step is split into a
    separate helper because ``get_object`` reads ``self.<x>_id`` which
    only exists after ``mount()`` runs.

    No-op when the subclass does not override ``get_object`` (Decision 6 —
    opt-in via override). When overridden:

    1. Call ``get_object()``, cache the result on ``self._object``.
    2. If non-None, call ``has_object_permission(request, obj)``.
    3. If False (or :class:`PermissionDenied` raised), re-raise
       ``PermissionDenied``.

    Returning ``None`` from ``get_object()`` is the recommended
    OWASP IDOR-mitigation pattern: deny via 404-shape (no object) rather
    than 403-shape (object exists but you can't access it). When the
    cached ``_object`` is ``None``, ``has_object_permission`` is NOT
    called — the caller raises 404 if it wants to.

    The framework also automates this 404-shape mapping for the common
    case: if ``get_object()`` raises ``django.core.exceptions.ObjectDoesNotExist``
    (parent of every ``Model.DoesNotExist``) or ``django.http.Http404``
    (raised by ``get_object_or_404``), the helper catches it and treats
    the object as ``None`` — skipping ``has_object_permission``. Note
    these are two SEPARATE catches: ``Http404`` does NOT inherit from
    ``ObjectDoesNotExist`` (its parent is ``Exception``), so both must
    be listed explicitly. Without this, a ``DoesNotExist`` from a naive
    ``Model.objects.get(pk=self.<x>_id)`` in ``get_object()`` would fall
    through to the outer ``except Exception`` in ``websocket.handle_mount``,
    where ``DEBUG=True`` would expose the exception class name and a
    traceback — confirming the object's nonexistence (information leak).
    Catching here makes the 404-shape pattern the default rather than
    developer discipline.

    Raises :class:`~django.core.exceptions.PermissionDenied` on denial.
    The caller in ``websocket.py`` translates that to a
    ``{"type": "error", "message": "Permission denied"}`` frame plus
    WebSocket close code 4403, mirroring the pre-mount denial path at
    ``websocket.py:1953-1955``.

    See ADR-017 § Decision 5 for the full rationale on why this is a
    separate helper rather than an extension of :func:`check_view_auth`.
    """
    if not _has_custom_get_object(view_instance):
        return

    from django.core.exceptions import ObjectDoesNotExist
    from django.http import Http404

    try:
        obj = view_instance.get_object()
    except (ObjectDoesNotExist, Http404):
        # OWASP IDOR mitigation: developer's get_object() did
        # `Model.objects.get(pk=self.<x>_id)` (raises DoesNotExist) or
        # `get_object_or_404(...)` (raises Http404) and the row doesn't
        # exist. Treat as 404-shape (no object) rather than letting the
        # exception propagate to the outer exception handler, which would
        # leak existence via DEBUG-mode tracebacks. The two catches are
        # listed explicitly because Http404 inherits from Exception, not
        # ObjectDoesNotExist.
        view_instance._object = None
        return

    view_instance._object = obj
    if obj is None:
        return

    ok = view_instance.has_object_permission(request, obj)
    if ok is False:
        logger.info(
            "Object-permission denied for %s: has_object_permission(...) returned False",
            view_instance.__class__.__name__,
        )
        raise PermissionDenied(f"Access denied for object on {view_instance.__class__.__name__}")


def check_view_auth_lightweight(view_instance, request) -> bool:
    """Return True if ``view_instance`` is allowed to mount under ``request``.

    Thin wrapper around :func:`check_view_auth` that returns a boolean
    instead of the redirect-url / None contract. Used by Sticky LiveViews
    (Phase B) to re-check a preserved child's auth posture against the
    NEW request at ``live_redirect`` time — a sticky view whose
    permissions are revoked mid-session must be unmounted at the next
    navigation, never silently retained.

    ``True`` = authorized (mount-eligible).
    ``False`` = denied (redirect required or permission missing).
    """
    try:
        return check_view_auth(view_instance, request) is None
    except PermissionDenied:
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
