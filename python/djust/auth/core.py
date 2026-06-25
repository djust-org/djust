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
from typing import Any, List, Optional, Union, cast

from django.conf import settings
from django.core.exceptions import ObjectDoesNotExist, PermissionDenied
from django.http import Http404

logger = logging.getLogger(__name__)


def check_view_auth(view_instance: Any, request: Any) -> Optional[str]:
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
    # ``LOGIN_URL`` is always a non-empty str (Django default
    # ``/accounts/login/``); cast for the strict ``login_url: str`` contract
    # without altering the original ``or``-fallback runtime behavior.
    login_url: str = cast(
        str,
        getattr(view_instance, "login_url", None)
        or getattr(settings, "LOGIN_URL", "/accounts/login/"),
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

    # 4. Honor Django's standard auth mixins (the AccessMixin family).
    #    These enforce via ``dispatch()`` on the HTTP path, but the WS/SSE
    #    mount path authorizes through this function rather than dispatch().
    #    Without this, LoginRequiredMixin / PermissionRequiredMixin /
    #    UserPassesTestMixin were enforced on the initial HTTP GET but
    #    silently bypassed over WebSocket (GHSA — finding #14).
    mixin_redirect = _check_django_access_mixins(view_instance, request, login_url)
    if mixin_redirect is not None:
        return mixin_redirect

    return None  # Auth passed


def _check_django_access_mixins(view_instance: Any, request: Any, login_url: str) -> Optional[str]:
    """Enforce ``django.contrib.auth.mixins`` (the AccessMixin family) on the
    WS/SSE mount path, mirroring each mixin's ``handle_no_permission()``.

    Returns a login-redirect URL on an *unauthenticated* denial, raises
    :class:`PermissionDenied` on an *authenticated* (or ``raise_exception``)
    denial — matching Django's HTTP-path semantics — or ``None`` when the view
    is not an AccessMixin subclass or all mixin checks pass.
    """
    try:
        from django.contrib.auth.mixins import (
            AccessMixin,
            LoginRequiredMixin,
            PermissionRequiredMixin,
            UserPassesTestMixin,
        )
    except Exception:  # noqa: BLE001 — django.contrib.auth not installed
        return None

    if not isinstance(view_instance, AccessMixin):
        return None

    # The mixin check methods (has_permission/test_func/get_login_url) read
    # ``self.request``; the WS mount may not have stamped it on the instance
    # yet (``LiveView.__init__`` sets ``self.request = None``). Set it when it
    # is absent or None, and restore the original afterwards so this auth check
    # stays side-effect-free.
    _MISSING = object()
    orig_request = view_instance.__dict__.get("request", _MISSING)
    need_set = orig_request is _MISSING or orig_request is None
    if need_set:
        view_instance.request = request
    try:
        user = getattr(request, "user", None)
        authed = bool(user is not None and getattr(user, "is_authenticated", False))

        failed = False
        if isinstance(view_instance, LoginRequiredMixin) and not authed:
            failed = True
        if not failed and isinstance(view_instance, PermissionRequiredMixin):
            failed = not view_instance.has_permission()
        if not failed and isinstance(view_instance, UserPassesTestMixin):
            failed = not view_instance.get_test_func()()
        if not failed:
            return None

        logger.info(
            "Auth denied for %s: Django auth mixin check failed (WS path)",
            view_instance.__class__.__name__,
        )
        # Mirror AccessMixin.handle_no_permission(): authenticated (or
        # raise_exception) => 403; anonymous => redirect to login.
        if bool(getattr(view_instance, "raise_exception", False)) or authed:
            try:
                msg = view_instance.get_permission_denied_message()
            except Exception:  # noqa: BLE001
                msg = ""
            raise PermissionDenied(msg or "Permission denied")
        try:
            return view_instance.get_login_url() or login_url
        except Exception:  # noqa: BLE001 — ImproperlyConfigured etc.
            return login_url
    finally:
        if need_set:
            try:
                if orig_request is _MISSING:
                    view_instance.__dict__.pop("request", None)
                else:
                    view_instance.request = orig_request
            except Exception as exc:  # noqa: BLE001 — best-effort restore
                logger.debug(
                    "check_view_auth: could not restore .request on %s: %s",
                    view_instance.__class__.__name__,
                    exc,
                )


def _has_custom_check_permissions(view_instance: Any) -> bool:
    """Check if the view subclass overrides check_permissions()."""
    from djust.live_view import LiveView

    for klass in type(view_instance).__mro__:
        if klass is LiveView or klass is object:
            break
        if "check_permissions" in klass.__dict__:
            return True
    return False


def _has_custom_get_object(view_instance: Any) -> bool:
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


def check_object_permission(view_instance: Any, request: Any) -> None:
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

    if obj is None:
        # Explicit "no object" — clear cache (was implicit before; making
        # it explicit so v0.9.5-1b's per-event re-check sees a consistent
        # post-state regardless of prior cache content).
        view_instance._object = None
        return

    # Run the permission check BEFORE populating the cache. Populating
    # the cache before the check (the v0.9.5-1a shape) was benign at
    # mount-time because the WS closed on denial — but for v0.9.5-1b's
    # per-event re-check, a denial now leaves the WS open, and a
    # poisoned cache would let subsequent events read a stale-allowed
    # `_object` for the same view instance. Strict ordering: check
    # first, cache second.
    ok = view_instance.has_object_permission(request, obj)
    if ok is False:
        logger.info(
            "Object-permission denied for %s: has_object_permission(...) returned False",
            view_instance.__class__.__name__,
        )
        # Do NOT touch view_instance._object — leaving the prior cache
        # value in place is the safest semantic (a fresh view starts at
        # None; a re-check on a previously-allowed view keeps the prior
        # legitimate value, which is fine because the next event will
        # re-run this check anyway in v0.9.5-1b).
        raise PermissionDenied(f"Access denied for object on {view_instance.__class__.__name__}")

    # Permission granted — populate the cache only now.
    view_instance._object = obj


def enforce_object_permission(view_instance: Any, request: Any) -> None:
    """Run the ADR-017 post-mount object-permission check on ANY render path.

    A shared chokepoint so object-level authorization is enforced uniformly,
    not just on the WS mount + event paths. Before this, the initial HTTP GET
    render (:meth:`RequestMixin.get`/``aget``), SPA ``url_change`` navigation
    (:meth:`ViewRuntime.dispatch_url_change`), and ``{% live_render %}``
    embedded children rendered an object-scoped view WITHOUT the object-level
    check, leaking denied objects (findings #10 / #11 / #12).

    Semantics:

    * No-op when the view does not opt into the lifecycle (no custom
      ``get_object`` override) — same as :func:`check_object_permission`.
    * Raises :class:`~django.core.exceptions.PermissionDenied` on denial.
    * **Fail-closed**: a ``None`` request (cannot authorize) or any
      non-``PermissionDenied`` exception from the developer's ``get_object`` /
      ``has_object_permission`` is treated as denial — mirroring the event-path
      handling in ``websocket_utils._validate_event_security`` (#1380, #1638).

    Callers translate the raised ``PermissionDenied`` into the transport's
    natural denial shape (HTTP 403, a ``permission_denied`` error frame, or a
    refused embed).
    """
    if not _has_custom_get_object(view_instance):
        return

    if request is None:
        # Object-scoped view but no request to authorize against → fail closed
        # (mirrors the #1380 sticky-child handling on the event path).
        logger.warning(
            "Object-permission check skipped (no request) for %s; failing closed",
            view_instance.__class__.__name__,
        )
        raise PermissionDenied("Access denied for this object.")

    try:
        check_object_permission(view_instance, request)
    except PermissionDenied:
        raise
    except Exception as exc:  # noqa: BLE001 — fail-closed by design
        logger.exception(
            "Object-permission check raised a non-PermissionDenied exception "
            "for %s; failing closed (denying)",
            view_instance.__class__.__name__,
        )
        raise PermissionDenied("Access denied for this object.") from exc


def _bind_current_tenant(tenant: Any) -> None:
    """Bind *tenant* into the current-tenant ContextVar (Finding #6).

    Lazily imports ``djust.tenants.middleware.set_current_tenant`` and is a
    no-op when the optional tenants module is unavailable. This is the single
    source of the tenant-bind step previously hand-copied as ``_bind_tenant``
    in ``websocket.py`` and ``runtime.py`` (both byte-identical). The transports
    keep their own ``_bind_tenant`` aliases for the NON-mount paths (disconnect
    cleanup, per-event re-bind); only the shared pre-mount sequence routes
    through here.
    """
    try:
        from ..tenants.middleware import set_current_tenant

        set_current_tenant(tenant)
    except Exception:  # noqa: BLE001 — tenants is optional; never break the live path
        pass


def run_pre_mount_auth(view_instance: Any, request: Any) -> Optional[str]:
    """Run the canonical pre-mount security sequence and return the auth verdict.

    Single-sources the ORDER in which every live mount path (WebSocket
    ``handle_mount``, generic ``ViewRuntime.dispatch_mount``, legacy SSE
    ``_sse_mount_view``) authorises and resolves tenancy BEFORE calling
    ``mount()``. The three transports already call the same leaf chokepoints
    (:func:`check_view_auth`, the view's ``_ensure_tenant`` hook, and the
    tenant ContextVar bind); this helper pins their *orchestration* so a future
    edit cannot silently reorder them or drop a step on one path (#1646 / #1853).

    Canonical sequence (matching the pre-existing per-transport copies exactly):

    1. ``redirect_url = check_view_auth(view_instance, request)``. This may
       raise :class:`~django.core.exceptions.PermissionDenied` (an authenticated
       user lacking a required permission) — the exception PROPAGATES unchanged
       so each transport keeps its own denial envelope (WS close 4403 / runtime
       error frame / SSE error push).
    2. If ``check_view_auth`` returns a redirect URL (anonymous user on a
       ``login_required`` view), RETURN it immediately — tenant resolution is
       SKIPPED on an auth denial, exactly as every transport did before (each
       ``return``\\ed before ``_ensure_tenant`` on denial). The caller maps the
       returned URL to its own navigate/redirect shape.
    3. Resolve tenancy: ``view_instance._ensure_tenant(request)`` when the view
       exposes the hook (TenantMixin). This may raise (e.g. ``Http404`` when a
       required tenant is unresolved) — the exception PROPAGATES so each
       transport keeps its own tenant-error envelope.
    4. Bind the resolved tenant into the ContextVar via
       :func:`_bind_current_tenant` so ``mount()`` + the initial render see the
       correct tenant in the tenant-scoped managers (fail-closed otherwise).

    Returns ``None`` when the mount may proceed, or a redirect-URL string when
    auth denies via redirect. Raises :class:`PermissionDenied` (auth) or any
    exception from ``_ensure_tenant`` (tenant resolution); callers wrap this in
    their existing transport-specific envelope, unchanged.

    NOTE: this is a *synchronous* helper (it calls only sync leaf functions);
    async callers invoke it via ``sync_to_async(run_pre_mount_auth)`` exactly as
    they previously wrapped ``check_view_auth`` / ``_ensure_tenant`` individually.
    """
    # 1 + 2. View-level auth (login / permission / custom / Django mixins).
    redirect_url = check_view_auth(view_instance, request)
    if redirect_url:
        # Auth denied via redirect — skip tenant resolve/bind, mirroring the
        # pre-existing per-transport early return on denial.
        return redirect_url

    # 3. Resolve tenancy (TenantMixin views only; no-op otherwise).
    if hasattr(view_instance, "_ensure_tenant"):
        view_instance._ensure_tenant(request)

    # 4. Bind the resolved tenant for the rest of the mount.
    _bind_current_tenant(getattr(view_instance, "_tenant", None))

    return None  # Mount may proceed.


def check_view_auth_lightweight(view_instance: Any, request: Any) -> bool:
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


def check_handler_permission(handler: Any, request: Any) -> bool:
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
    return bool(user.has_perms(perms))


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
