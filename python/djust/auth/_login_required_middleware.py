"""Django's ``LoginRequiredMiddleware`` (Django 5.1+) on live transports.

The middleware runs for HTTP requests only, and asks one question of the
routed view callable: ``getattr(view_func, "login_required", True)``. Django's
``login_not_required`` marks either that callable (in the URLconf) or
``dispatch`` (``@method_decorator(login_not_required, name="dispatch")``,
which ``as_view()`` copies). A live mount (WebSocket, SSE, API dispatch,
``mount_batch``) authorizes through :func:`djust.auth.core.check_view_auth`
instead, so that function asks :func:`middleware_login_redirect`:

1. A view rendered inside a page (``{% live_render %}`` children, lazy
   children) takes its page's verdict: the page view was already checked, by
   the middleware itself on HTTP or here on a live transport. Django's
   middleware never gates what a page includes either.
2. A view whose own HTTP request Django dispatched (the page ``GET``, the HTTP
   POST event fallback) is not checked again: the installed middleware,
   including any subclass and its exemptions, already processed that request.
3. Any other mount is a top-level live mount of a class the client names. It
   is exempt only when the class's ``dispatch`` carries ``login_not_required``,
   when the request's route serves this exact class and the installed
   middleware's own ``process_view`` lets it through (so subclasses that
   exempt paths behave as on HTTP), or when the server's URLconf routes this
   exact class through a ``login_not_required`` entry. A class the URLconf
   does not route (a view mounted beside the page view) needs the
   ``dispatch`` marker; naming a public page's URL does not exempt it.

The refusal redirects where the middleware would (its ``login_url`` and
``redirect_field_name`` for that route). A view mounted beside the page view
(``dj-lazy`` hydration, ``mount_batch``) is refused on its own and the page
stays (see :class:`MiddlewareRedirect`). Without the middleware, or on
Django < 5.1, this is inert.
"""

import functools
import logging
import weakref
from typing import Any, Callable, Dict, Optional, Tuple

from django.conf import settings

logger = logging.getLogger(__name__)


class _Identities:
    """Objects tracked by identity (never by ``__hash__``/``__eq__``, which a
    view class may override), dropped when the object is collected."""

    def __init__(self) -> None:
        self._refs: Dict[int, Tuple["weakref.ref[Any]", Any]] = {}

    def set(self, obj: Any, value: Any = True) -> None:
        key = id(obj)

        def drop(ref: Any, key: int = key) -> None:
            entry = self._refs.get(key)
            if entry is not None and entry[0] is ref:
                self._refs.pop(key, None)

        try:
            ref = weakref.ref(obj, drop)
        except TypeError:  # not weak-referenceable: not tracked
            return
        self._refs[key] = (ref, value)

    def get(self, obj: Any) -> Any:
        entry = self._refs.get(id(obj))
        if entry is None or entry[0]() is not obj:
            return None
        return entry[1]


#: View instances created to render inside a page (rule 1).
_EMBEDDED = _Identities()
#: View instance -> weakref to the HTTP request Django dispatched to it (rule 2).
_HTTP_DISPATCHED = _Identities()


def mark_embedded(view_instance: Any) -> None:
    """Record that ``view_instance`` renders inside a page (a ``{% live_render %}``
    or lazy child), so it takes its page's verdict."""
    _EMBEDDED.set(view_instance)


def note_http_dispatch(view_instance: Any, request: Any) -> None:
    """Record that Django dispatched ``request`` to ``view_instance`` (its own
    ``get``/``post``), after the middleware chain processed it."""
    try:
        _HTTP_DISPATCHED.set(view_instance, weakref.ref(request))
    except TypeError:  # a request that is not weak-referenceable: not tracked
        pass


def _http_dispatched(view_instance: Any, request: Any) -> bool:
    ref = _HTTP_DISPATCHED.get(view_instance)
    return ref is not None and ref() is request


def _no_response(request: Any) -> None:  # pragma: no cover — never called
    return None


@functools.lru_cache(maxsize=16)
def _installed_middleware(middleware: tuple) -> Any:
    """An instance of the first ``LoginRequiredMiddleware`` (or subclass) in
    ``MIDDLEWARE``, or ``None``."""
    try:
        from django.contrib.auth.middleware import LoginRequiredMiddleware
    except ImportError:  # Django < 5.1
        return None
    from django.utils.module_loading import import_string

    for entry in middleware:
        try:
            cls = import_string(entry) if isinstance(entry, str) else entry
        except Exception:  # noqa: BLE001 — an unimportable entry is not this middleware
            continue
        if isinstance(cls, type) and issubclass(cls, LoginRequiredMiddleware):
            try:
                return cls(_no_response)
            except Exception:  # noqa: BLE001 — fall back to the stock rule (fail closed)
                logger.warning(
                    "Could not instantiate %s outside the request cycle; live mounts "
                    "apply LoginRequiredMiddleware's stock rule",
                    getattr(cls, "__qualname__", cls),
                )
                return LoginRequiredMiddleware(_no_response)
    return None


def _route_callback(request: Any) -> Any:
    """The view callable the request's path routes to, or ``None``."""
    func = getattr(getattr(request, "resolver_match", None), "func", None)
    if func is not None:
        return func
    path = getattr(request, "path_info", None)
    if not path:
        return None
    try:
        from django.urls import resolve

        return resolve(path, getattr(request, "urlconf", None)).func
    except Exception:  # noqa: BLE001 — an unroutable path exempts nothing
        return None


@functools.lru_cache(maxsize=16)
def _routed_callbacks(resolver: Any) -> Dict[type, Tuple[Callable[..., Any], ...]]:
    """Every class-based view ``resolver`` routes -> the callables routing it.

    Keyed on the resolver object: ``django.urls.clear_url_caches()`` (URLconf
    reloads) makes ``get_resolver`` build a new one, so nothing goes stale.
    """
    from django.urls import URLResolver

    found: Dict[type, list] = {}

    def walk(patterns: Any) -> None:
        for pattern in patterns:
            if isinstance(pattern, URLResolver):
                walk(pattern.url_patterns)
            else:
                callback = getattr(pattern, "callback", None)
                view_cls = getattr(callback, "view_class", None)
                if view_cls is not None:
                    found.setdefault(view_cls, []).append(callback)

    try:
        walk(resolver.url_patterns)
    except Exception:  # noqa: BLE001 — an unreadable URLconf routes nothing we can see
        return {}
    return {cls: tuple(callbacks) for cls, callbacks in found.items()}


def _routes_for(view_cls: type, request: Any) -> Tuple[Callable[..., Any], ...]:
    from django.urls import get_resolver

    urlconf = getattr(request, "urlconf", None)
    if urlconf is not None and not isinstance(urlconf, str):
        urlconf = getattr(urlconf, "__name__", None)
    try:
        resolver = get_resolver(urlconf or settings.ROOT_URLCONF)
    except Exception:  # noqa: BLE001 — no URLconf: nothing is routed
        return ()
    return _routed_callbacks(resolver).get(view_cls, ())


def _routed_view_classes(urlconf: str) -> frozenset:
    """Every class-based view the URLconf routes (introspection helper)."""
    from django.urls import get_resolver

    return frozenset(_routed_callbacks(get_resolver(urlconf)))


_routed_view_classes.cache_clear = _routed_callbacks.cache_clear  # type: ignore[attr-defined]


def _login_url(middleware: Any, view_func: Any) -> str:
    try:
        from django.shortcuts import resolve_url

        return str(resolve_url(middleware.get_login_url(view_func)))
    except Exception:  # noqa: BLE001
        return str(getattr(settings, "LOGIN_URL", None) or "/accounts/login/")


def _redirect(middleware: Any, request: Any, view_func: Any) -> str:
    """Where the installed middleware sends this request (its ``Location``,
    with its ``login_url`` and ``redirect_field_name``), or its login URL when
    it answers otherwise or cannot build the redirect."""
    try:
        response = middleware.handle_no_permission(request, view_func)
        location = response.get("Location") if response is not None else None
        if location:
            return str(location)
    except Exception:  # noqa: BLE001 — e.g. a host the live request cannot build
        pass
    return _login_url(middleware, view_func)


def _login_required_stub(request: Any) -> None:  # pragma: no cover — never called
    return None


class MiddlewareRedirect(str):
    """A login URL that this module's rule produced (not the view's own
    ``login_required``). A view mounted beside the page view answers it on
    its own (its container is refused) instead of moving the whole page."""


def is_middleware_redirect(url: Any) -> bool:
    """True when ``url`` is a refusal from Django's ``LoginRequiredMiddleware``
    rule (see :class:`MiddlewareRedirect`)."""
    return isinstance(url, MiddlewareRedirect)


def middleware_login_redirect(view_instance: Any, request: Any) -> Optional[str]:
    """Where Django's ``LoginRequiredMiddleware`` sends this request for this
    view, or ``None`` when it lets it through (or is not installed).

    See the module docstring for the three rules.
    """
    middleware = _installed_middleware(tuple(getattr(settings, "MIDDLEWARE", None) or ()))
    if middleware is None:
        return None
    if getattr(getattr(request, "user", None), "is_authenticated", False):
        return None
    # 1. Rendered inside a page: the page's verdict.
    if _EMBEDDED.get(view_instance):
        return None
    # 2. Django dispatched this request to this view: the middleware ran.
    if _http_dispatched(view_instance, request):
        return None
    # 3. A top-level live mount of a class the client names.
    view_cls = type(view_instance)
    if not getattr(getattr(view_cls, "dispatch", None), "login_required", True):
        return None
    func = _route_callback(request)
    if func is not None and getattr(func, "view_class", None) is view_cls:
        try:
            response = middleware.process_view(request, func, (), {})
        except Exception:  # noqa: BLE001 — a middleware that cannot answer refuses
            response = False
        if response is None:
            return None
        return MiddlewareRedirect(_redirect(middleware, request, func))
    routes = _routes_for(view_cls, request)
    if any(not getattr(route, "login_required", True) for route in routes):
        return None
    # The request's path is not this view's page (another route, or the SSE /
    # API endpoint): the login URL of the view's own route, without ``next``.
    return MiddlewareRedirect(_login_url(middleware, routes[0] if routes else _login_required_stub))


def login_required_by_middleware(view_instance: Any, request: Any) -> bool:
    """True when Django's ``LoginRequiredMiddleware`` is installed and would
    send this request for this view to the login page."""
    return middleware_login_redirect(view_instance, request) is not None
