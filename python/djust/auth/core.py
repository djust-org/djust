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

import dis
import functools
import inspect
import logging
import weakref
from collections.abc import Iterator
from typing import Any, Callable, List, NamedTuple, Optional, Union, cast

from django.conf import settings
from django.core.exceptions import ObjectDoesNotExist, PermissionDenied
from django.http import Http404

logger = logging.getLogger(__name__)


def check_view_auth(
    view_instance: Any, request: Any, *, http_dispatched: bool = False, event: bool = False
) -> Optional[str]:
    """Check view-level auth. Returns None if OK, or a redirect URL if denied.

    Called by websocket.py before mount(). Checks in order:
    1. login_required -- is user authenticated?
    2. permission_required -- does user have Django permission(s)?
    3. check_permissions() -- custom hook (if overridden by subclass)
    4. Django's LoginRequiredMixin / PermissionRequiredMixin /
       UserPassesTestMixin, replayed.
    5. Code that only Django's HTTP request runs: the view's
       ``setup``/``dispatch``/``get``/``post`` overrides, the decorators
       applied to them with ``@method_decorator``, and the wrappers its
       URLconf routes put around ``as_view()`` (read from the server's
       URLconf, never from the URL a client sends). Django's
       ``login_required``, ``staff_member_required``, ``user_passes_test``
       and ``permission_required`` (and decorators built on
       ``user_passes_test``) are replayed; any other gate (a custom
       ``AccessMixin`` subclass's ``dispatch()``, code that checks the user
       and denies by any means) is refused unless the view declares a rule
       that covers it, because no live transport runs it (see
       :func:`live_auth_refusals`). What applies only to ``post()`` is
       checked for events (``event=True`` and :func:`check_event_auth`),
       not for the mount.

    Args:
        view_instance: The LiveView instance being mounted.
        request: The Django request object.
        http_dispatched: True only on the HTTP GET render, which Django's
            ``View.dispatch`` reached after running the view's own
            ``dispatch()`` and method decorators. Skips step 5 there.
        event: True when the request carries an event (the HTTP POST event
            fallback, the API): step 5 then also applies what only the
            view's ``post()`` runs.

    Returns:
        None if auth passes, or a login URL string to redirect to.

    Raises:
        PermissionDenied: If user is authenticated but lacks required
            permissions. Matches Django's PermissionRequiredMixin behavior.
    """
    login_required = getattr(view_instance, "login_required", None) or any(
        _is_djust_auth_login_mixin(k) for k in type(view_instance).__mro__
    )
    permission_required = getattr(view_instance, "permission_required", None)
    # ``LOGIN_URL`` is always a non-empty str (Django default
    # ``/accounts/login/``); cast for the strict ``login_url: str`` contract
    # without altering the original ``or``-fallback runtime behavior.
    login_url: str = cast(
        str,
        getattr(view_instance, "login_url", None)
        or getattr(settings, "LOGIN_URL", "/accounts/login/"),
    )

    # 1. Login check (also Django's LoginRequiredMiddleware, see
    #    djust.auth._login_required_middleware)
    from ._login_required_middleware import middleware_login_redirect

    middleware_redirect = (
        None if login_required else middleware_login_redirect(view_instance, request)
    )
    if login_required or middleware_redirect is not None:
        user = getattr(request, "user", None)
        if user is None or not getattr(user, "is_authenticated", False):
            logger.info(
                "Auth denied for %s: user not authenticated",
                view_instance.__class__.__name__,
            )
            return middleware_redirect or login_url

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

    # 5. Access control that only the HTTP request runs. The live mount, the
    #    HTTP POST event fallback, the API and embedded children never call
    #    the view's setup()/dispatch()/get()/post() or its URLconf wrappers,
    #    so Django's own auth decorators found there are replayed here, and
    #    any other gate refuses the request unless the view declares a rule
    #    that covers it (see live_auth_refusals); the system check
    #    (djust.S012) reports the same views.
    if not http_dispatched:
        return _http_only_access(
            view_instance, request, login_url, _PHASE_ALL if event else _PHASE_MOUNT
        )

    return None  # Auth passed


def check_event_auth(view_instance: Any, request: Any) -> Optional[str]:
    """Access control that only the view's ``post()`` runs, for a live event.

    Django runs a decorator applied to ``post`` (or a ``post()`` override)
    for an HTTP POST, not for the GET that renders the page, so the live
    mount does not apply it (:func:`check_view_auth`); every event does.
    Same contract as :func:`check_view_auth`: None when allowed, a login URL
    for an anonymous denial, :class:`PermissionDenied` for an authenticated
    one. Cheap for views with no such code (one cached lookup).
    """
    if not has_event_access_control(type(view_instance), _request_urlconf(request)):
        return None
    login_url: str = cast(
        str,
        getattr(view_instance, "login_url", None)
        or getattr(settings, "LOGIN_URL", "/accounts/login/"),
    )
    return _http_only_access(view_instance, request, login_url, _PHASE_EVENT)


def has_event_access_control(view_cls: type, urlconf: Optional[str] = None) -> bool:
    """Whether :func:`check_event_auth` has anything to check for ``view_cls``
    (no database access, safe to call from async code)."""
    analysis = _full_analysis(view_cls, urlconf)
    return any(r.event for r in analysis.replays) or any(
        f.event and _refusable(f) for f in analysis.findings
    )


def _http_only_access(
    view_instance: Any, request: Any, login_url: str, phase: str
) -> Optional[str]:
    """Step 5 of :func:`check_view_auth` for ``phase``."""
    view_cls = type(view_instance)
    analysis = _full_analysis(view_cls, _request_urlconf(request))
    replay_redirect = _replay_auth_decorators(
        view_instance,
        request,
        login_url,
        tuple(r for r in analysis.replays if _in_phase(r.event, phase)),
    )
    if replay_redirect is not None:
        return replay_redirect
    refusals = tuple(
        f.label
        for f in analysis.findings
        if _in_phase(f.event, phase) and _refusable(f) and not _covered(view_cls, f)
    )
    if refusals:
        logger.warning(
            "Auth denied for %s: its access control (%s) runs only on the HTTP "
            "request; declare permission_required or check_permissions() "
            "(or login_required for a login-only check) on the view (djust.S012)",
            view_instance.__class__.__name__,
            "; ".join(refusals),
        )
        user = getattr(request, "user", None)
        if user is not None and getattr(user, "is_authenticated", False):
            raise PermissionDenied(
                "This view's access control is not declared for live transports."
            )
        return login_url
    return None


# --------------------------------------------------------------------------- #
# Access control that only Django's HTTP request runs (step 5 above).
#
# Django's ``View.as_view()`` callback runs a view's ``setup()``, its own
# ``dispatch()``, the decorators applied to ``dispatch``/``get``/``post`` with
# ``@method_decorator``, its ``get()``/``post()``, and whatever the URLconf
# wraps around that callback. No live transport does, so each such piece of
# code on a LiveView is classified once per class (and once per URLconf for
# the wrappers):
#
# * Django's ``login_required``, ``staff_member_required``,
#   ``user_passes_test(...)`` and ``permission_required(...)`` -- applied with
#   ``@method_decorator``, wrapped around ``as_view()`` in the URLconf (or by
#   an ``as_view()`` override), or built into another package's decorator on
#   top of ``user_passes_test`` -- are *replayed*: their test runs against the
#   request's user on every live path. ``login_not_required`` is not access
#   control.
# * A *gate* (code that checks the user and denies: a custom ``AccessMixin``
#   subclass's ``dispatch()``; an override that reads the user and raises,
#   calls ``handle_no_permission()`` / ``redirect_to_login()`` or returns a
#   redirect, an error response or a page of its own; a decorator or URLconf wrapper whose code
#   checks the user) refuses the live request unless the view declares a
#   rule that covers it.
# * An override that only *reads* the user (``is_authenticated``,
#   ``is_staff``, ``has_perm``) without denying is reported by djust.V013 at
#   startup but not refused.
# * Any other decorator or URLconf wrapper is *unknown*: see
#   _UNKNOWN_DECORATORS_REFUSE.
#
# What only ``post()`` runs (a decorator applied to ``post``, a ``post()``
# override) applies to events, not to the mount: Django does not run it for
# the GET that renders the page.
#
# This is a heuristic over code objects; declared djust auth
# (``login_required`` / ``permission_required`` / ``check_permissions()``) is
# the contract every transport enforces.
# --------------------------------------------------------------------------- #

#: Whether a decorator or URLconf wrapper that is neither Django's nor
#: djust's, and whose code shows no sign of access control, refuses live
#: requests (True) or is only reported at startup by djust.V013 (False).
#: False on this patch line, so a CSP, rate-limit or logging decorator or
#: wrapper does not stop a LiveView from mounting; one whose code checks the
#: user is still refused.
_UNKNOWN_DECORATORS_REFUSE = False

#: Names whose use in an overridden ``setup``/``dispatch``/``get``/``post``
#: (or in a project decorator applied to one) marks it as access control.
#: Matches the AST heuristic of the S012 source scan, plus the AccessMixin API.
_ACCESS_REFERENCE_NAMES = frozenset(
    {
        "PermissionDenied",
        "is_authenticated",
        "is_anonymous",
        "is_staff",
        "is_superuser",
        "has_perm",
        "has_perms",
        "has_module_perms",
        "has_permission",
        "HttpResponseForbidden",
        "redirect_to_login",
        "handle_no_permission",
        "test_func",
        "get_test_func",
        "login_required",
        "permission_required",
        "user_passes_test",
        "staff_member_required",
        "check_permissions",
    }
)

#: Access names that produce a denial: code using one of them gates the view
#: rather than only reading who the user is.
_DENIAL_NAMES = frozenset(
    {
        "PermissionDenied",
        "HttpResponseForbidden",
        "redirect_to_login",
        "handle_no_permission",
        "login_required",
        "user_passes_test",
        "permission_required",
        "staff_member_required",
    }
)

#: Other ways to deny: code that checks the user and uses one of these (or
#: raises anything, or builds a response with an explicit ``status``) gates
#: the view too. A response the code builds itself (``render(...)``,
#: ``HttpResponse(...)``) is a denial as well: the users it is returned to do
#: not get the view.
_RESPONSE_DENIAL_NAMES = frozenset(
    {
        "redirect",
        "resolve_url",
        "render",
        "render_to_response",
        "TemplateResponse",
        "HttpResponse",
        "JsonResponse",
        "StreamingHttpResponse",
        "FileResponse",
        "http_method_not_allowed",
        "get_object_or_404",
        "get_list_or_404",
        "aget_object_or_404",
        "aget_list_or_404",
        "Http404",
        "HttpResponseRedirect",
        "HttpResponsePermanentRedirect",
        "HttpResponseNotFound",
        "HttpResponseBadRequest",
        "HttpResponseNotAllowed",
        "HttpResponseGone",
        "HttpResponseServerError",
    }
)

#: Access names a login-only check uses. A gate that references nothing else
#: is covered by a declared ``login_required``; any other gate needs
#: ``permission_required`` or ``check_permissions()``.
_LOGIN_ONLY_NAMES = frozenset(
    {
        "is_authenticated",
        "is_anonymous",
        "PermissionDenied",
        "HttpResponseForbidden",
        "redirect_to_login",
        "handle_no_permission",
        "login_required",
    }
)

#: The methods Django's ``View`` runs for an HTTP request that no live
#: transport does. ``post`` applies to events only.
_HTTP_ONLY_METHODS = ("setup", "dispatch", "get", "post")
_EVENT_ONLY_METHODS = frozenset({"post"})

#: Mixins from packages built for djust whose ``dispatch``/``get``/``post``
#: hold no access control and whose work djust already does on live paths
#: (djust-tenants' tenant resolution runs in ``run_pre_mount_auth``).
_NON_ACCESS_MIXINS = frozenset(
    {
        ("djust_tenants.mixin", "TenantMixin"),
        ("djust_tenants.mixin", "TenantScopedMixin"),
    }
)

_GATE = "gate"
_PREDICATE = "predicate"
_UNKNOWN = "unknown-decorator"

_PHASE_MOUNT = "mount"
_PHASE_EVENT = "event"
_PHASE_ALL = "all"


class _Replay(NamedTuple):
    """A Django auth decorator whose test is replayed on live transports."""

    label: str
    test: Callable[[Any], Any]
    login_url: Optional[str]
    #: True when only ``post()`` runs it (events, not the mount).
    event: bool = False


class _Finding(NamedTuple):
    """HTTP-only code on a LiveView that a live request cannot run."""

    label: str
    kind: str  # _GATE, _PREDICATE or _UNKNOWN
    #: True when the rule goes beyond "is logged in", so a declared
    #: ``login_required`` does not cover it.
    beyond_login: bool
    #: The decorator or wrapper, for _UNKNOWN.
    obj: Any = None
    #: True when only ``post()`` runs it (events, not the mount).
    event: bool = False
    #: The class whose method holds it; None for a URLconf wrapper.
    owner: Any = None


class _Analysis(NamedTuple):
    replays: "tuple[_Replay, ...]"
    findings: "tuple[_Finding, ...]"


_analysis_cache: "weakref.WeakKeyDictionary[type, _Analysis]" = weakref.WeakKeyDictionary()


def _in_phase(event: bool, phase: str) -> bool:
    if phase == _PHASE_ALL:
        return True
    return event is (phase == _PHASE_EVENT)


def is_framework_module(module: str) -> bool:
    """True for Django's modules and djust's own production modules.

    Their ``dispatch``/``get``/``post`` overrides are either replayed on live
    transports (Django's three auth mixins, djust's tenant hook) or carry no
    access control. Only ``djust`` and ``djust.*`` are djust's: a project app
    or another package named ``djust_*`` is not framework code, and djust's
    test and example modules are not either. Shared with the V013 and S012
    system checks so all three agree.
    """
    if module == "django" or module.startswith("django."):
        return True
    if module == "djust" or module.startswith("djust."):
        return "test" not in module and "example" not in module
    return False


def is_non_access_mixin(klass: Any) -> bool:
    """A mixin from a package built for djust whose HTTP-only methods hold no
    access control (see ``_NON_ACCESS_MIXINS``); V013 and step 5 skip it."""
    return (getattr(klass, "__module__", None), getattr(klass, "__name__", None)) in (
        _NON_ACCESS_MIXINS
    )


def _is_djust_auth_login_mixin(klass: Any) -> bool:
    """The ``djust-auth`` package's ``LoginRequiredLiveViewMixin``.

    It enforces login only in ``dispatch()`` and sets no attribute, so it is
    read as ``login_required = True`` (as djust's own copy declares).
    """
    return (
        getattr(klass, "__module__", None) == "djust_auth.mixins"
        and getattr(klass, "__name__", None) == "LoginRequiredLiveViewMixin"
    )


def _declares_login(view_cls: type) -> bool:
    """A login rule every transport enforces: ``login_required`` truthy,
    djust-auth's login mixin, or Django's replayed ``LoginRequiredMixin``."""
    if getattr(view_cls, "login_required", None):
        return True
    if any(_is_djust_auth_login_mixin(k) for k in view_cls.__mro__):
        return True
    try:
        from django.contrib.auth.mixins import LoginRequiredMixin
    except Exception:  # noqa: BLE001 — django.contrib.auth not installed
        return False
    return issubclass(view_cls, LoginRequiredMixin)


def _declares_rule(view_cls: type) -> bool:
    """A rule beyond login that every transport enforces: a truthy
    ``permission_required``, a ``check_permissions()`` override, or Django's
    replayed ``UserPassesTestMixin``."""
    if getattr(view_cls, "permission_required", None):
        return True
    from djust.live_view import LiveView

    for klass in view_cls.__mro__:
        if klass is LiveView or klass is object:
            break
        if "check_permissions" in klass.__dict__:
            return True
    try:
        from django.contrib.auth.mixins import UserPassesTestMixin
    except Exception:  # noqa: BLE001 — django.contrib.auth not installed
        return False
    return issubclass(view_cls, UserPassesTestMixin)


def declares_live_auth(view_cls: type) -> bool:
    """True when the view declares auth that every transport enforces.

    That is a truthy ``login_required`` or ``permission_required``, a
    ``check_permissions()`` override, or djust-auth's
    ``LoginRequiredLiveViewMixin``. ``login_required = False`` does not count:
    it marks a view public, which says nothing about a gate in ``dispatch()``.
    Whether a declaration covers a given gate is decided by
    :func:`live_auth_refusals`.
    """
    if getattr(view_cls, "login_required", None) or getattr(view_cls, "permission_required", None):
        return True
    if any(_is_djust_auth_login_mixin(k) for k in view_cls.__mro__):
        return True
    from djust.live_view import LiveView

    for klass in view_cls.__mro__:
        if klass is LiveView or klass is object:
            break
        if "check_permissions" in klass.__dict__:
            return True
    return False


def _nested_codes(code: Any) -> "list[Any]":
    """``code`` and the code objects nested in it (bounded)."""
    found: list[Any] = []
    stack = [code]
    while stack and len(found) < 64:
        current = stack.pop()
        found.append(current)
        stack.extend(c for c in current.co_consts if inspect.iscode(c))
    return found


def _code_names(code: Any) -> "frozenset[str]":
    """Names used by ``code`` and the functions nested in it."""
    names: set[str] = set()
    for current in _nested_codes(code):
        names.update(current.co_names)
    return frozenset(names)


def _is_django_denial(value: Any) -> bool:
    """Whether ``value`` is a response class, or one of Django's denial
    helpers (``redirect``, ``render``, ``get_object_or_404``, ...), whatever
    name the module imported it under."""
    if isinstance(value, type):
        from django.http import HttpResponseBase

        try:
            return issubclass(value, HttpResponseBase)
        except TypeError:
            return False
    module = getattr(value, "__module__", None)
    name = getattr(value, "__name__", None)
    return (
        isinstance(module, str)
        and module.startswith("django.")
        and isinstance(name, str)
        and name in (_RESPONSE_DENIAL_NAMES | _DENIAL_NAMES)
    )


def _denies(code: Any, namespace: Any = None) -> bool:
    """Whether ``code`` (or a function nested in it) can deny a request by a
    means other than the access names: it raises, redirects, returns a 404,
    another error response or a response of its own, or builds a response
    with an explicit ``status``. ``namespace`` (the function's module
    globals) resolves names imported under another name
    (``from django.shortcuts import redirect as go``)."""
    for current in _nested_codes(code):
        if set(current.co_names) & _RESPONSE_DENIAL_NAMES:
            return True
        if any(name.startswith("HttpResponse") for name in current.co_names):
            return True
        if isinstance(namespace, dict) and any(
            inst.opname in ("LOAD_GLOBAL", "LOAD_NAME")
            and isinstance(inst.argval, str)
            and _is_django_denial(namespace.get(inst.argval))
            for inst in dis.get_instructions(current)
        ):
            return True
        if any(isinstance(c, tuple) and "status" in c for c in current.co_consts):
            return True  # keyword names of a call such as HttpResponse(status=403)
        if any(i.opname == "RAISE_VARARGS" for i in dis.get_instructions(current)):
            return True
    return False


#: The user attributes a login-only check reads. A gate that reads any other
#: attribute of the user (``groups``, ``role``, ``pk``, ``is_staff``, through
#: ``getattr`` or a helper) is a rule beyond login.
_LOGIN_ONLY_USER_ATTRS = frozenset({"is_authenticated", "is_anonymous"})

#: Instructions that load a name or an attribute by name.
_NAME_LOADS = frozenset(
    {
        "LOAD_ATTR",
        "LOAD_METHOD",
        "LOAD_FAST",
        "LOAD_FAST_CHECK",
        "LOAD_FAST_AND_CLEAR",
        "LOAD_DEREF",
        "LOAD_CLASSDEREF",
        "LOAD_FROM_DICT_OR_DEREF",
        "LOAD_GLOBAL",
        "LOAD_NAME",
    }
)


def _loads_user(inst: Any, tracked: "set[str]") -> "Optional[bool]":
    """True when ``inst`` loads the user (a name in ``tracked``, such as
    ``request.user`` or a local ``user``); None when it loads it together
    with another value (a fused instruction); False otherwise."""
    argval = inst.argval
    if isinstance(argval, str):
        # LOAD_FAST_BORROW (Python 3.14+) and any later LOAD_FAST variant.
        if inst.opname in _NAME_LOADS or inst.opname.startswith("LOAD_FAST"):
            return argval in tracked
        if inst.opname.startswith("LOAD_") and inst.opname != "LOAD_CONST" and argval in tracked:
            return None  # a load of the user djust cannot classify: fail closed
        return False
    if isinstance(argval, tuple) and any(isinstance(v, str) and v in tracked for v in argval):
        # LOAD_FAST_LOAD_FAST, STORE_FAST_LOAD_FAST and similar fused
        # instructions (Python 3.13+).
        return None
    return False


def _user_attributes(code: Any) -> "frozenset[str]":
    """The attributes ``code`` (and the functions nested in it) reads from
    the user: ``request.user.X``, ``user.X`` (also through a local alias such
    as ``u = request.user``) and ``getattr(request.user, "X")``. ``"*"``
    stands for a use djust cannot name (the user passed to a helper, compared
    or stored elsewhere)."""
    if code is None:
        return frozenset()
    listings = [list(dis.get_instructions(c)) for c in _nested_codes(code)]
    tracked = {"user"}
    for _ in range(4):  # local aliases of the user, to a fixpoint
        grew = False
        for insts in listings:
            for i, inst in enumerate(insts[:-1]):
                nxt = insts[i + 1]
                if (
                    _loads_user(inst, tracked)
                    and nxt.opname.startswith("STORE_")
                    and isinstance(nxt.argval, str)
                    and nxt.argval not in tracked
                    and nxt.opname != "STORE_ATTR"
                ):
                    tracked.add(nxt.argval)
                    grew = True
        if not grew:
            break
    attrs: set[str] = set()
    for insts in listings:
        for i, inst in enumerate(insts):
            loaded = _loads_user(inst, tracked)
            if loaded is False:
                continue
            following: Any = insts[i + 1] if loaded and i + 1 < len(insts) else None
            if following is None:
                attrs.add("*")
            elif following.opname in ("LOAD_ATTR", "LOAD_METHOD") and isinstance(
                following.argval, str
            ):
                attrs.add(following.argval)
            elif following.opname.startswith("STORE_") and following.argval in tracked:
                continue  # an alias, read where it is used
            elif (
                following.opname == "LOAD_CONST"
                and isinstance(following.argval, str)
                and any(
                    p.opname in ("LOAD_GLOBAL", "LOAD_NAME") and p.argval in ("getattr", "hasattr")
                    for p in insts[max(0, i - 4) : i]
                )
            ):
                attrs.add(following.argval)  # getattr(request.user, "X", ...)
            else:
                attrs.add("*")
    return frozenset(attrs)


def _beyond_login(names: "frozenset[str]", user_attrs: "frozenset[str]") -> bool:
    """Whether a gate goes beyond "is logged in": it uses an access name
    other than the login-only ones, or reads a user attribute other than
    ``is_authenticated`` / ``is_anonymous``."""
    return not names <= _LOGIN_ONLY_NAMES or bool(user_attrs - _LOGIN_ONLY_USER_ATTRS)


def _func_module(obj: Any) -> str:
    target = getattr(obj, "func", obj) if isinstance(obj, functools.partial) else obj
    target = getattr(target, "__func__", target)
    if inspect.isfunction(target):
        # The module that defined the code: functools.wraps copies the wrapped
        # function's __module__ onto a wrapper.
        defined_in = target.__globals__.get("__name__")
        if isinstance(defined_in, str):
            return defined_in
    module = getattr(target, "__module__", None) or getattr(type(target), "__module__", "")
    return module or ""


def _is_django_auth_callable(obj: Any) -> bool:
    """A callable from ``django.contrib.auth`` / ``django.contrib.admin``
    (other than ``login_not_required``)."""
    if not callable(obj) or isinstance(obj, type):
        return False
    if _is_login_not_required(obj):
        return False
    return _func_module(obj).startswith(("django.contrib.auth", "django.contrib.admin"))


class _DjangoAuthDecorators(NamedTuple):
    login_required: Any
    staff_member_required: Any
    login_not_required: Any
    user_passes_test_code: Any
    permission_required_code: Any
    #: The code objects of the wrapper ``user_passes_test(...)`` puts around
    #: a view (sync and async): what ``login_required``,
    #: ``permission_required`` and ``staff_member_required`` return too.
    view_wrapper_codes: "frozenset[Any]"


_DJANGO_AUTH: "list[_DjangoAuthDecorators]" = []


def _django_auth() -> Optional[_DjangoAuthDecorators]:
    """Django's auth decorators and the code objects their factories return."""
    if _DJANGO_AUTH:
        return _DJANGO_AUTH[0]
    try:
        from django.contrib.auth import decorators as auth_decorators
    except Exception:  # noqa: BLE001 — django.contrib.auth not installed
        return None
    try:
        from django.contrib.admin.views.decorators import staff_member_required
    except Exception:  # noqa: BLE001 — django.contrib.admin not installed
        staff_member_required = None

    def _sync_view(request: Any) -> None:
        return None

    async def _async_view(request: Any) -> None:
        return None

    upt = auth_decorators.user_passes_test(bool)
    found = _DjangoAuthDecorators(
        login_required=auth_decorators.login_required,
        staff_member_required=staff_member_required,
        login_not_required=getattr(auth_decorators, "login_not_required", None),
        user_passes_test_code=upt.__code__,
        permission_required_code=auth_decorators.permission_required("x.y").__code__,
        view_wrapper_codes=frozenset({upt(_sync_view).__code__, upt(_async_view).__code__}),
    )
    _DJANGO_AUTH.append(found)
    return found


def _is_login_not_required(obj: Any) -> bool:
    dj = _django_auth()
    return dj is not None and dj.login_not_required is not None and obj is dj.login_not_required


def _closure_value(func: Any, name: str) -> Any:
    code = func.__code__
    return func.__closure__[code.co_freevars.index(name)].cell_contents


def _is_authenticated_test(user: Any) -> bool:
    return bool(getattr(user, "is_authenticated", False))


def _is_active_staff_test(user: Any) -> bool:
    return bool(getattr(user, "is_active", False) and getattr(user, "is_staff", False))


def _permission_test(perms: Any, raise_exception: bool) -> Callable[[Any], bool]:
    perm_tuple = (perms,) if isinstance(perms, str) else tuple(perms)

    def check_perms(user: Any) -> bool:
        if user.has_perms(perm_tuple):
            return True
        if raise_exception:
            raise PermissionDenied
        return False

    return check_perms


def _replay_for(decorator: Any) -> "Union[_Replay, bool, None]":
    """A replay of Django auth ``decorator``.

    Returns the replay; ``True`` for ``login_not_required`` (not access
    control); ``False`` for a Django auth decorator that cannot be read (the
    caller treats it as a gate); ``None`` when it is not a Django auth
    decorator.
    """
    dj = _django_auth()
    if dj is None:
        return None
    target = decorator
    kwargs: dict[str, Any] = {}
    if isinstance(decorator, functools.partial):
        if decorator.args:
            return False if _is_django_auth_callable(decorator.func) else None
        target = decorator.func
        kwargs = dict(decorator.keywords)
    if dj.login_not_required is not None and target is dj.login_not_required:
        return True
    if target is dj.login_required:
        return _Replay(
            "@method_decorator(login_required)", _is_authenticated_test, kwargs.get("login_url")
        )
    if dj.staff_member_required is not None and target is dj.staff_member_required:
        return _Replay(
            "@method_decorator(staff_member_required)",
            _is_active_staff_test,
            kwargs.get("login_url", "admin:login"),
        )
    if kwargs:
        return False if _is_django_auth_callable(target) else None
    code = getattr(target, "__code__", None)
    try:
        if code is not None and code is dj.user_passes_test_code:
            return _Replay(
                "@method_decorator(user_passes_test(...))",
                _closure_value(target, "test_func"),
                _closure_value(target, "login_url"),
            )
        if code is not None and code is dj.permission_required_code:
            return _Replay(
                "@method_decorator(permission_required(...))",
                _permission_test(
                    _closure_value(target, "perms"), bool(_closure_value(target, "raise_exception"))
                ),
                _closure_value(target, "login_url"),
            )
    except (ValueError, IndexError, TypeError, AttributeError):
        return False
    return False if _is_django_auth_callable(target) else None


def _view_wrapper_replay(label: str, wrapper: Any) -> Optional[_Replay]:
    """The replay of a wrapper ``user_passes_test(...)`` put around a view
    (``login_required(view)``, ``permission_required(p)(view)``,
    ``staff_member_required(view)`` and decorators built on them), or None."""
    dj = _django_auth()
    if dj is None or getattr(wrapper, "__code__", None) not in dj.view_wrapper_codes:
        return None
    try:
        test = _closure_value(wrapper, "test_func")
    except (ValueError, IndexError, TypeError, AttributeError):
        return None
    login_url = getattr(wrapper, "login_url", None)  # set by Django 5.1+
    for holder, name in ((wrapper, "login_url"), (wrapper, "_redirect_to_login")):
        try:
            value = _closure_value(holder, name)
        except (ValueError, IndexError, TypeError, AttributeError):
            continue
        if name == "_redirect_to_login":
            try:
                value = _closure_value(value, "login_url")
            except (ValueError, IndexError, TypeError, AttributeError):
                continue
        login_url = value
        break
    return _Replay(label, test, login_url)


def _trial_apply(decorator: Any) -> "Optional[tuple[Any, Any]]":
    """Apply a decorator read from ``@method_decorator`` to a stand-in view,
    as Django's ``method_decorator`` applies it to the method on every
    request. Returns (what it returned, the stand-in), or None when it is
    framework code or cannot be applied that way."""
    if _django_auth() is None or is_framework_module(_func_module(decorator)):
        return None

    def _stand_in(*args: Any, **kwargs: Any) -> None:
        return None

    try:
        return decorator(_stand_in), _stand_in
    except Exception:  # noqa: BLE001 — not a plain view decorator
        return None


def _trial_replays(label: str, applied: "Optional[tuple[Any, Any]]") -> "Optional[list[_Replay]]":
    """Replays for a decorator from another package built on
    ``user_passes_test`` (django-otp's ``otp_required``, say).

    ``applied`` is :func:`_trial_apply`'s result. When the decorator returned
    a chain of ``user_passes_test`` wrappers ending at the stand-in, each
    wrapper's test is replayed; anything else returns None and the decorator
    is classified by its code and by the code of what it returned.
    """
    if applied is None:
        return None
    layer, stand_in = applied
    replays: list[_Replay] = []
    for _ in range(8):
        if layer is stand_in:
            return replays or None
        replay = _view_wrapper_replay(label, layer)
        if replay is None:
            return None
        replays.append(replay)
        layer = getattr(layer, "__wrapped__", None)
    return None


try:
    from django.views import View as _View
except Exception:  # noqa: BLE001 — Django not importable
    _View = None  # type: ignore[assignment,misc]


class _References(NamedTuple):
    """What :func:`_scan_references` found in a decorator or wrapper."""

    #: Access names its code uses.
    names: "frozenset[str]"
    #: Whether it closes over a Django auth decorator.
    auth_decorator: bool
    #: The user attributes read by its functions that can deny a request.
    user_attrs: "frozenset[str]"

    def checks_user(self) -> bool:
        return bool(self.names or self.auth_decorator or self.user_attrs)


def _scan_references(root: Any, *, skip: Any = None) -> _References:
    """Access names used by ``root``'s own code, the functions it closes over,
    its ``__wrapped__`` chain and a ``functools.partial``'s arguments, outside
    Django and djust; whether it closes over a Django auth decorator; and the
    user attributes read by those functions that can deny a request.

    ``skip`` (the method a wrapper wraps) is not scanned.
    """
    names: set[str] = set()
    user_attrs: set[str] = set()
    auth_decorator = False
    stack = [root]
    seen: set[int] = set()
    budget = 64
    while stack and budget > 0:
        obj = stack.pop()
        if obj is None or obj is skip or id(obj) in seen:
            continue
        seen.add(id(obj))
        budget -= 1
        if isinstance(obj, functools.partial):
            stack.append(obj.func)
            stack.extend(obj.args)
            stack.extend(obj.keywords.values())
            continue
        if obj is not root and _is_django_auth_callable(obj):
            auth_decorator = True
            continue
        if isinstance(obj, type):
            # A class a decorator closes over (decorator_from_middleware's
            # middleware); a view class is the decorated code, not a check.
            if not is_framework_module(getattr(obj, "__module__", "") or "") and not (
                _View is not None and issubclass(obj, _View)
            ):
                for value in list(vars(obj).values()):
                    func = getattr(value, "__func__", value)
                    if inspect.isfunction(func):
                        stack.append(func)
            continue
        func = getattr(obj, "__func__", obj)
        if inspect.isfunction(func):
            if not is_framework_module(_func_module(func)):
                names.update(_code_names(func.__code__) & _ACCESS_REFERENCE_NAMES)
                attrs = _user_attributes(func.__code__)
                if attrs and _denies(func.__code__, func.__globals__):
                    user_attrs.update(attrs)
            for freevar, cell in zip(func.__code__.co_freevars, func.__closure__ or ()):
                if freevar == "__class__":  # zero-argument super(), not a dependency
                    continue
                try:
                    stack.append(cell.cell_contents)
                except ValueError:  # empty cell
                    continue
            stack.append(getattr(func, "__wrapped__", None))
            continue
        if callable(obj) and not inspect.isbuiltin(obj):
            call = getattr(type(obj), "__call__", None)
            if inspect.isfunction(call):
                stack.append(call)
    return _References(frozenset(names), auth_decorator, frozenset(user_attrs))


_METHOD_DECORATOR_CODE: Any = None


def _method_decorator_code() -> Any:
    """The code object Django's ``method_decorator`` wrappers share."""
    global _METHOD_DECORATOR_CODE
    if _METHOD_DECORATOR_CODE is None:
        from django.utils.decorators import method_decorator

        def _probe(self: Any) -> None:  # noqa: dead-method-allowed (passed to method_decorator by value)
            return None

        _METHOD_DECORATOR_CODE = method_decorator(lambda f: f)(_probe).__code__
    return _METHOD_DECORATOR_CODE


def _unwrap_method_decorator(func: Any) -> "tuple[list[Any], Any, bool]":
    """Return (decorators, innermost function, readable) of a
    ``method_decorator`` chain. ``readable`` is False when a wrapper that looks
    like Django's could not be read (a changed Django, say)."""
    decorators: list[Any] = []
    wrapper_code = _method_decorator_code()
    current = func
    for _ in range(32):
        if getattr(current, "__code__", None) is not wrapper_code:
            if "_multi_decorate" in (getattr(current, "__qualname__", "") or ""):
                return decorators, current, False
            break
        try:
            decorators.extend(list(_closure_value(current, "decorators")))
            current = _closure_value(current, "method")
        except (ValueError, IndexError, TypeError, AttributeError):
            return decorators, current, False
    return decorators, current, True


def _decorator_name(decorator: Any) -> str:
    code = getattr(decorator, "__code__", None)
    if code is not None and getattr(decorator, "__wrapped__", None) is not None:
        # functools.wraps copied the wrapped method's name; name the wrapper.
        return str(getattr(code, "co_qualname", code.co_name))
    target = (
        getattr(decorator, "func", decorator)
        if isinstance(decorator, functools.partial)
        else decorator
    )
    return getattr(target, "__qualname__", None) or type(target).__qualname__


def _gate_from_refs(label: str, refs: _References) -> _Finding:
    return _Finding(label, _GATE, refs.auth_decorator or _beyond_login(refs.names, refs.user_attrs))


def _classify_decorator(
    label: str, decorator: Any, skip: Any = None, *, trial: bool = False
) -> Any:
    """Classify one decorator applied to an HTTP-only method, or one wrapper
    layer around a routed ``as_view()`` callback.

    Returns a replay (or a list of them), a finding, or None. ``trial``
    (decorators read from ``@method_decorator``, not yet applied) allows
    applying it to a stand-in view: to find a ``user_passes_test`` chain, and
    to read the wrapper it returns (a decorator that delegates to another
    one, ``return staff_required(view)``, is classified by what it returns).
    """
    replay = _replay_for(decorator)
    if isinstance(replay, _Replay):
        return replay
    if replay is True:  # login_not_required
        return None
    if replay is False:
        return _Finding("%s (a Django auth decorator djust cannot replay)" % label, _GATE, True)
    applied = _trial_apply(decorator) if trial else None
    replays = _trial_replays(label, applied)
    if replays:
        return replays
    refs = _scan_references(decorator, skip=skip)
    if applied is not None:
        produced = _scan_references(applied[0], skip=applied[1])
        refs = _References(
            refs.names | produced.names,
            refs.auth_decorator or produced.auth_decorator,
            refs.user_attrs | produced.user_attrs,
        )
    if refs.checks_user():
        return _gate_from_refs("%s, which checks the user" % label, refs)
    if is_framework_module(_func_module(decorator)):
        return None
    return _Finding(label, _UNKNOWN, True, decorator)


def _add_verdict(verdict: Any, replays: "list[_Replay]", findings: "list[_Finding]") -> None:
    if isinstance(verdict, list):
        replays.extend(verdict)
    elif isinstance(verdict, _Replay):
        replays.append(verdict)
    elif verdict is not None:
        findings.append(verdict)


def _method_findings(klass: type, name: str, attr: Any) -> "tuple[list[_Replay], list[_Finding]]":
    """Classify ``klass.__dict__[name]`` (``setup``, ``dispatch``, ``get`` or
    ``post``)."""
    try:
        from django.contrib.auth.mixins import (
            AccessMixin,
            LoginRequiredMixin,
            PermissionRequiredMixin,
            UserPassesTestMixin,
        )
    except Exception:  # noqa: BLE001 — django.contrib.auth not installed
        AccessMixin = LoginRequiredMixin = PermissionRequiredMixin = UserPassesTestMixin = None  # type: ignore[assignment,misc]

    replays: list[_Replay] = []
    findings: list[_Finding] = []
    label = "%s.%s()" % (klass.__qualname__, name)
    method_decorator_code = _method_decorator_code()

    # Unwrap the method: Django's ``@method_decorator`` chains and plain
    # decorators applied directly to it (functools.wraps), in any order and
    # nesting; each decorator found is classified, the innermost function is
    # the override.
    layer = getattr(attr, "__func__", attr)
    for _ in range(32):
        if getattr(layer, "__code__", None) is method_decorator_code or (
            "_multi_decorate" in (getattr(layer, "__qualname__", "") or "")
        ):
            decorators, inner_method, readable = _unwrap_method_decorator(layer)
            if not readable:
                findings.append(
                    _Finding("%s has a @method_decorator djust cannot read" % label, _GATE, True)
                )
            for decorator in decorators:
                verdict = _classify_decorator(
                    "%s is wrapped by @method_decorator(%s)" % (label, _decorator_name(decorator)),
                    decorator,
                    trial=True,
                )
                if isinstance(verdict, _Replay):
                    verdict = verdict._replace(label="%s on %s" % (verdict.label, label))
                _add_verdict(verdict, replays, findings)
            if not readable or inner_method is layer:
                break
            layer = inner_method
            continue
        wrapped = getattr(layer, "__wrapped__", None)
        if wrapped is None or not inspect.isfunction(layer):
            break
        _add_verdict(
            _classify_decorator(
                "%s is wrapped by %s" % (label, _decorator_name(layer)), layer, skip=wrapped
            ),
            replays,
            findings,
        )
        layer = wrapped
    inner = layer

    if is_framework_module(_func_module(inner)):
        return replays, findings
    code = getattr(getattr(inner, "__func__", inner), "__code__", None)
    namespace = getattr(getattr(inner, "__func__", inner), "__globals__", None)
    if code is None:
        call = getattr(type(inner), "__call__", None)
        code = getattr(call, "__code__", None)
        namespace = getattr(call, "__globals__", None)
    names = (_code_names(code) & _ACCESS_REFERENCE_NAMES) if code is not None else frozenset()
    # Any attribute it reads from the user (groups, role, pk, getattr(user,
    # ...)), not only the access names: a check of one of them that denies is
    # a gate too, and one beyond login.
    user_attrs = _user_attributes(code)
    denies = code is not None and _denies(code, namespace)
    gate = _Finding("%s checks the user" % label, _GATE, _beyond_login(names, user_attrs))

    if name == "dispatch" and AccessMixin is not None and issubclass(klass, AccessMixin):
        replayed = (LoginRequiredMixin, PermissionRequiredMixin, UserPassesTestMixin)
        if not issubclass(klass, replayed):
            # A project access mixin: its dispatch() is the rule.
            findings.append(_Finding("%s on an AccessMixin subclass" % label, _GATE, True))
        elif names or (user_attrs and denies):
            findings.append(gate)
        return replays, findings
    if names & _DENIAL_NAMES or ((names or user_attrs) and denies):
        # Checks the user and denies: raises, redirects, returns an error
        # response, or uses an access-denial name.
        findings.append(gate)
    elif names:
        findings.append(
            _Finding(
                "%s reads the user without refusing" % label,
                _PREDICATE,
                _beyond_login(names, user_attrs),
            )
        )
    return replays, findings


def _analyse(view_cls: type) -> _Analysis:
    """The view class's own HTTP-only code (its URLconf wrappers are read by
    :func:`_url_analysis`)."""
    try:
        return _analysis_cache[view_cls]
    except (KeyError, TypeError):
        pass
    replays: list[_Replay] = []
    findings: list[_Finding] = []
    for klass in view_cls.__mro__:
        if (
            klass is object
            or is_framework_module(getattr(klass, "__module__", "") or "")
            or _is_djust_auth_login_mixin(klass)
            or is_non_access_mixin(klass)
        ):
            continue
        for name in _HTTP_ONLY_METHODS:
            attr = klass.__dict__.get(name)
            if attr is None:
                continue
            method_replays, method_findings = _method_findings(klass, name, attr)
            event = name in _EVENT_ONLY_METHODS
            replays.extend(r._replace(event=event) for r in method_replays)
            findings.extend(f._replace(event=event, owner=klass) for f in method_findings)
    result = _Analysis(tuple(replays), tuple(findings))
    try:
        _analysis_cache[view_cls] = result
    except TypeError:
        pass
    return result


# --------------------------------------------------------------------------- #
# URLconf wrappers. Read from the server's URLconf (the default ROOT_URLCONF
# and, when middleware set one, ``request.urlconf``), never from the URL a
# live client sends, and cached per resolver.
# --------------------------------------------------------------------------- #


def _url_patterns(urlconf: Optional[str] = None) -> "Iterator[tuple[str, Any]]":
    """(route, callback) for every URL pattern of ``urlconf``."""
    from django.urls import get_resolver

    def _walk(node: Any, prefix: str) -> "Iterator[tuple[str, Any]]":
        pattern = str(getattr(node, "pattern", ""))
        nested = getattr(node, "url_patterns", None)
        if nested is not None:
            for child in nested:
                yield from _walk(child, prefix + pattern)
            return
        callback = getattr(node, "callback", None)
        if callback is not None:
            yield prefix + pattern, callback

    yield from _walk(get_resolver(urlconf), "")


def _liveview_class_of(callback: Any) -> Optional[type]:
    from djust.live_view import LiveView

    view_class = getattr(callback, "view_class", None)
    if isinstance(view_class, type) and issubclass(view_class, LiveView):
        return view_class
    return None


def routed_liveview_callbacks(urlconf: Optional[str] = None) -> "Iterator[tuple[str, Any, type]]":
    """(route, callback, LiveView class) for every URL pattern routing a LiveView.

    ``functools.wraps`` copies ``view_class`` onto a decorator's wrapper, so a
    callback wrapped in the URLconf still names its class.
    """
    for route, callback in _url_patterns(urlconf):
        view_class = _liveview_class_of(callback)
        if view_class is not None:
            yield route, callback, view_class


def _hidden_liveview(callback: Any) -> Optional[type]:
    """The LiveView a URLconf callback wraps without ``functools.wraps``
    (found in the functions it closes over, bounded), or None."""
    if _liveview_class_of(callback) is not None:
        return None
    stack = [callback]
    seen: set[int] = set()
    budget = 32
    while stack and budget > 0:
        obj = stack.pop()
        if obj is None or id(obj) in seen:
            continue
        seen.add(id(obj))
        budget -= 1
        if obj is not callback:
            found = _liveview_class_of(obj)
            if found is not None:
                return found
        if isinstance(obj, functools.partial):
            stack.append(obj.func)
            stack.extend(obj.args)
            stack.extend(obj.keywords.values())
            continue
        func = getattr(obj, "__func__", obj)
        if inspect.isfunction(func):
            for cell in func.__closure__ or ():
                try:
                    stack.append(cell.cell_contents)
                except ValueError:  # empty cell
                    continue
            stack.append(getattr(func, "__wrapped__", None))
    return None


def unreadable_url_wrappers(urlconf: Optional[str] = None) -> "dict[type, tuple[str, ...]]":
    """LiveView class -> the URLconf routes that wrap its ``as_view()`` in a
    function without ``functools.wraps``. djust cannot see such a wrapper (it
    hides the view class), so it does not apply it on live transports;
    djust.V013 names the route."""
    from django.urls import get_resolver

    resolver = get_resolver(urlconf)
    cached = _unreadable_cache.get(urlconf)
    if cached is not None and cached[0] is resolver:
        return cached[1]
    found: dict[type, list[str]] = {}
    for route, callback in _url_patterns(urlconf):
        view_class = _hidden_liveview(callback)
        if view_class is not None:
            found.setdefault(view_class, []).append(route)
    result = {cls: tuple(routes) for cls, routes in found.items()}
    _unreadable_cache[urlconf] = (resolver, result)
    return result


_unreadable_cache: "dict[Optional[str], tuple[Any, dict[type, tuple[str, ...]]]]" = {}


#: Per URLconf: (resolver, class -> analysis, (module, qualname) -> analysis).
_url_cache: "dict[Optional[str], tuple[Any, dict[type, _Analysis], dict[tuple[str, str], _Analysis]]]" = {}


def invalidate_url_auth_cache() -> None:
    """Forget what the URLconf routes wrap each LiveView in (rebuilt on the
    next live request). Hot view replacement calls it after reloading a
    module."""
    _url_cache.clear()
    _unreadable_cache.clear()


def _request_urlconf(request: Any) -> Optional[str]:
    """A URLconf middleware set on the request (server-side), else None."""
    urlconf = getattr(request, "urlconf", None) if request is not None else None
    return urlconf if isinstance(urlconf, str) else None


def _callback_analysis(route: str, callback: Any) -> _Analysis:
    """Classify each wrapper layer around one routed ``as_view()`` callback."""
    replays: list[_Replay] = []
    findings: list[_Finding] = []
    layer = callback
    for _ in range(16):
        wrapped = getattr(layer, "__wrapped__", None)
        if wrapped is None:
            break
        replay = _view_wrapper_replay(
            "the URLconf route %r wraps it in Django's user_passes_test" % route, layer
        )
        name = getattr(getattr(layer, "__code__", None), "co_qualname", None) or _decorator_name(
            layer
        )
        label = "the URLconf route %r wraps it in %s" % (route, name)
        if replay is not None:
            replays.append(replay)
        elif _is_django_auth_callable(layer):
            findings.append(
                _Finding("%s (a Django auth wrapper djust cannot replay)" % label, _GATE, True)
            )
        elif not is_framework_module(_func_module(layer)):
            _add_verdict(_classify_decorator(label, layer, skip=wrapped), replays, findings)
        layer = wrapped
    return _Analysis(tuple(replays), tuple(findings))


def _class_key(view_cls: type) -> "tuple[str, str]":
    return (getattr(view_cls, "__module__", "") or "", getattr(view_cls, "__qualname__", ""))


def _url_maps(
    urlconf: Optional[str],
) -> "tuple[dict[type, _Analysis], dict[tuple[str, str], _Analysis]]":
    """View class -> what its routes wrap it in, for ``urlconf``; and the same
    keyed by the class's module and qualified name."""
    if urlconf is None and not getattr(settings, "ROOT_URLCONF", None):
        return {}, {}
    from django.urls import get_resolver

    try:
        resolver = get_resolver(urlconf)
    except Exception:  # noqa: BLE001 — a broken URLconf serves nothing anyway
        return {}, {}
    cached = _url_cache.get(urlconf)
    if cached is not None and cached[0] is resolver:
        return cached[1], cached[2]
    by_class: dict[type, tuple[list[_Replay], list[_Finding]]] = {}
    try:
        routes = list(routed_liveview_callbacks(urlconf))
    except Exception:  # noqa: BLE001 — Django's own URL check reports it
        logger.warning("djust could not read the URLconf for live auth checks")
        return {}, {}
    for route, callback, view_class in routes:
        analysis = _callback_analysis(route, callback)
        entry = by_class.setdefault(view_class, ([], []))
        entry[0].extend(analysis.replays)
        entry[1].extend(analysis.findings)
    result = {cls: _Analysis(tuple(r), tuple(f)) for cls, (r, f) in by_class.items() if r or f}
    by_name: dict[tuple[str, str], _Analysis] = {}
    for cls, analysis in result.items():
        key = _class_key(cls)
        prior = by_name.get(key)
        by_name[key] = (
            analysis
            if prior is None
            else _Analysis(prior.replays + analysis.replays, prior.findings + analysis.findings)
        )
    _url_cache[urlconf] = (resolver, result, by_name)
    return result, by_name


def _url_map(urlconf: Optional[str]) -> "dict[type, _Analysis]":
    """View class -> what its routes wrap it in, for ``urlconf``."""
    return _url_maps(urlconf)[0]


def _url_lookup(view_cls: type, urlconf: Optional[str]) -> Optional[_Analysis]:
    by_class, by_name = _url_maps(urlconf)
    found = by_class.get(view_cls)
    if found is None:
        # A class reloaded by hot view replacement (DEBUG) is a new object the
        # URLconf, still holding the old one, does not name: match it by name.
        found = by_name.get(_class_key(view_cls))
    return found


def _url_analysis(view_cls: type, urlconf: Optional[str] = None) -> _Analysis:
    """What the server's URLconf routes wrap ``view_cls`` in. A class routed
    more than once gets every route's wrappers: a live request does not say
    (in a way djust can trust) which route it came from."""
    empty = _Analysis((), ())
    found = _url_lookup(view_cls, None) or empty
    if urlconf is not None:
        extra = _url_lookup(view_cls, urlconf) or empty
        found = _Analysis(found.replays + extra.replays, found.findings + extra.findings)
    return found


def _full_analysis(view_cls: type, urlconf: Optional[str] = None) -> _Analysis:
    own = _analyse(view_cls)
    routed = _url_analysis(view_cls, urlconf)
    if not routed.replays and not routed.findings:
        return own
    return _Analysis(own.replays + routed.replays, own.findings + routed.findings)


def _covered_by_declaration(view_cls: type, beyond_login: bool) -> bool:
    """Whether the view's declared rule covers a gate. A declared login rule
    covers a login-only gate; a declared rule beyond login covers any."""
    if _declares_rule(view_cls):
        return True
    return not beyond_login and _declares_login(view_cls)


def _covered(view_cls: type, finding: _Finding) -> bool:
    return _covered_by_declaration(view_cls, finding.beyond_login)


def http_only_access_control(view_cls: type) -> "tuple[str, ...]":
    """Describe the gates on ``view_cls`` that only the HTTP request runs.

    These are what step 5 refuses unless the view declares a rule covering
    them (:func:`live_auth_refusals` applies the declarations): a custom
    ``AccessMixin`` subclass's ``dispatch()``, an override, decorator or
    URLconf wrapper that checks the user and denies, and (when
    ``_UNKNOWN_DECORATORS_REFUSE``) an unknown decorator or wrapper. Django's
    replayed auth decorators are not listed. Returns an empty tuple when there
    is none.
    """
    return tuple(f.label for f in _full_analysis(view_cls).findings if _refusable(f))


def _refusable(finding: _Finding) -> bool:
    return finding.kind == _GATE or (finding.kind == _UNKNOWN and _UNKNOWN_DECORATORS_REFUSE)


def live_auth_refusals(view_cls: type) -> "tuple[str, ...]":
    """The gates step 5 refuses for ``view_cls`` (at mount or, for what only
    ``post()`` runs, for events): those its declarations do not cover. A
    declared ``login_required`` (or djust-auth's / Django's login mixin)
    covers a login-only gate; ``permission_required``,
    ``check_permissions()`` or ``UserPassesTestMixin`` covers any gate."""
    return tuple(f.label for f in live_auth_refused_findings(view_cls))


def live_auth_refused_findings(view_cls: type) -> "tuple[_Finding, ...]":
    """The findings behind :func:`live_auth_refusals` (``beyond_login`` says
    whether a declared ``login_required`` could cover one)."""
    return tuple(
        f for f in _full_analysis(view_cls).findings if _refusable(f) and not _covered(view_cls, f)
    )


def live_auth_reports(view_cls: type) -> "tuple[_Finding, ...]":
    """Findings the system checks report as warnings (djust.V013) without a
    runtime refusal: overrides that read the user without refusing and (when
    unknown decorators are not refused) unknown decorators and URLconf
    wrappers. Declared rules cover them as in :func:`live_auth_refusals`."""
    return tuple(
        f
        for f in _full_analysis(view_cls).findings
        if not _refusable(f) and not _covered(view_cls, f)
    )


def _resolve_login_url(url: Optional[str], default: str) -> str:
    if not url:
        return default
    try:
        from django.shortcuts import resolve_url

        return str(resolve_url(url))
    except Exception:  # noqa: BLE001 — NoReverseMatch (admin not routed) etc.
        return default


def _await_test(make: Callable[[], Any]) -> Any:
    """Await the awaitable ``make()`` returns from synchronous code.

    In a worker thread (``sync_to_async``, which is how every live transport
    calls the auth check) ``async_to_sync`` awaits it on the serving event
    loop. A thread that is running an event loop itself cannot block on it
    (the test may need that loop, or the thread ``sync_to_async`` uses), so
    the check refuses there with a clear error instead of hanging: call
    :func:`check_view_auth` through ``sync_to_async`` from async code.
    """
    import asyncio

    async def _run() -> Any:
        return await make()

    try:
        asyncio.get_running_loop()
    except RuntimeError:
        from asgiref.sync import async_to_sync

        return async_to_sync(_run)()
    pending = make()
    if inspect.iscoroutine(pending):
        pending.close()  # never awaited; avoid the "never awaited" warning
    logger.error(
        "djust cannot run an async access test on the event loop's own thread; "
        "call check_view_auth() through sync_to_async (the request is refused)"
    )
    raise RuntimeError(
        "An async access test cannot run on the event loop thread; call "
        "check_view_auth() through sync_to_async."
    )


def _run_test(test: Callable[[Any], Any], user: Any) -> Any:
    """Run a replayed test; an async test (or one returning an awaitable,
    such as Django's async ``permission_required`` check) is awaited."""
    if inspect.iscoroutinefunction(test):
        return _await_test(lambda: test(user))
    result = test(user)
    if inspect.isawaitable(result):
        return _await_test(lambda: result)
    return result


def _replay_auth_decorators(
    view_instance: Any, request: Any, login_url: str, replays: "tuple[_Replay, ...]"
) -> Optional[str]:
    """Run the tests of the Django auth decorators and wrappers found for the
    view (``@method_decorator``, URLconf wrappers, decorators built on
    ``user_passes_test``).

    A failed test sends an anonymous user to the decorator's login URL and
    raises :class:`PermissionDenied` for an authenticated one (as djust's own
    declarations and the replayed mixins do on live transports). A test that
    raises ``PermissionDenied`` (``permission_required(...,
    raise_exception=True)``) propagates; a test that raises anything else
    counts as failed.
    """
    if not replays:
        return None
    user = getattr(request, "user", None)
    authed = bool(user is not None and getattr(user, "is_authenticated", False))
    for replay in replays:
        if user is None:
            return login_url
        try:
            passed = _run_test(replay.test, user)
        except PermissionDenied:
            raise
        except Exception:  # noqa: BLE001 — a test that crashes denies
            logger.warning(
                "Auth denied for %s: %s raised an exception (replayed on a live transport)",
                view_instance.__class__.__name__,
                replay.label,
            )
            passed = False
        if passed:
            continue
        logger.info(
            "Auth denied for %s: %s failed (replayed on a live transport)",
            view_instance.__class__.__name__,
            replay.label,
        )
        if authed:
            raise PermissionDenied("Permission denied")
        return _resolve_login_url(replay.login_url, login_url)
    return None


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
