"""Tests for ``@server_function`` and ``dispatch_server_function`` (v0.7.0).

These cover the decorator contract (including the mutual-exclusion ban
against stacking with ``@event_handler``) and the HTTP dispatch pipeline
(auth, CSRF, param coercion, permission checks, rate limiting, async
support, and the JSON envelope shape).
"""

from __future__ import annotations

import json

import pytest
from django.contrib.auth import get_user_model
from django.contrib.sessions.middleware import SessionMiddleware
from django.test import RequestFactory

from djust.api.dispatch import dispatch_server_function, reset_rate_buckets
from djust.api.registry import register_api_view, reset_registry
from djust.decorators import (
    event_handler,
    is_server_function,
    permission_required,
    rate_limit,
    server_function,
)
from djust.live_view import LiveView

pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def _clean_state():
    reset_registry()
    reset_rate_buckets()
    yield
    reset_registry()
    reset_rate_buckets()


@pytest.fixture
def authed_request():
    """Factory producing authenticated POST requests with session + CSRF exempt."""
    rf = RequestFactory(enforce_csrf_checks=False)
    User = get_user_model()

    def make(body=None, user=None, enforce_csrf=False):
        if user is None:
            user = User.objects.create_user(username="alice", password="pw")
        if body is None:
            data = b""
        elif isinstance(body, bytes):
            data = body
        else:
            data = json.dumps(body).encode("utf-8")
        request = rf.post(
            "/djust/api/call/x/y/",
            data=data,
            content_type="application/json",
        )
        SessionMiddleware(lambda r: None).process_request(request)
        request.session.save()
        request.user = user
        if not enforce_csrf:
            request._dont_enforce_csrf_checks = True
        return request, user

    return make


def test_basic_call_returns_result(authed_request):
    class V(LiveView):
        api_name = "sf.basic"

        @server_function
        def greet(self, name: str = "world", **kwargs):
            return f"hello {name}"

    register_api_view("sf.basic", V)
    request, _ = authed_request({"params": {"name": "carla"}})
    resp = dispatch_server_function(request, "sf.basic", "greet")
    assert resp.status_code == 200, resp.content
    body = json.loads(resp.content)
    assert set(body.keys()) == {"result"}
    assert body["result"] == "hello carla"


def test_missing_view_returns_404(authed_request):
    request, _ = authed_request({})
    resp = dispatch_server_function(request, "nope.nowhere", "whatever")
    assert resp.status_code == 404
    assert json.loads(resp.content)["error"] == "unknown_view"


def test_missing_function_returns_404(authed_request):
    class V(LiveView):
        api_name = "sf.mf"

        @server_function
        def exists(self, **kwargs):
            return 1

    register_api_view("sf.mf", V)
    request, _ = authed_request({})
    resp = dispatch_server_function(request, "sf.mf", "imaginary")
    assert resp.status_code == 404
    assert json.loads(resp.content)["error"] == "unknown_function"


def test_function_not_decorated_rejected(authed_request):
    class V(LiveView):
        api_name = "sf.plain"

        def bare(self, **kwargs):
            return "nope"

    register_api_view("sf.plain", V)
    request, _ = authed_request({})
    resp = dispatch_server_function(request, "sf.plain", "bare")
    assert resp.status_code == 404
    assert json.loads(resp.content)["error"] == "not_a_server_function"


def test_param_coercion_by_type_hint(authed_request):
    class V(LiveView):
        api_name = "sf.coerce"

        @server_function
        def sum_pair(self, a: int, b: int, **kwargs):
            return a + b

    register_api_view("sf.coerce", V)
    # The client sends "5" / "7" as strings; coercion must turn them into ints.
    request, _ = authed_request({"params": {"a": "5", "b": "7"}})
    resp = dispatch_server_function(request, "sf.coerce", "sum_pair")
    assert resp.status_code == 200, resp.content
    assert json.loads(resp.content)["result"] == 12


def test_permission_denied_returns_403(authed_request):
    class V(LiveView):
        api_name = "sf.perm"

        @server_function
        @permission_required("app.super_secret")
        def secret(self, **kwargs):
            return "nope"

    register_api_view("sf.perm", V)
    request, _ = authed_request({})
    resp = dispatch_server_function(request, "sf.perm", "secret")
    assert resp.status_code == 403
    assert json.loads(resp.content)["error"] == "permission_denied"


def test_csrf_required():
    """Server functions ALWAYS enforce CSRF — no auth-class opt-out."""

    class V(LiveView):
        api_name = "sf.csrf"

        @server_function
        def ping(self, **kwargs):
            return "pong"

    register_api_view("sf.csrf", V)
    User = get_user_model()
    user, _ = User.objects.get_or_create(username="csrfuser")
    rf = RequestFactory(enforce_csrf_checks=True)
    request = rf.post(
        "/djust/api/call/sf.csrf/ping/",
        data=b"{}",
        content_type="application/json",
    )
    SessionMiddleware(lambda r: None).process_request(request)
    request.session.save()
    request.user = user
    # Intentionally NOT setting _dont_enforce_csrf_checks — the CSRF middleware
    # must reject the request.
    resp = dispatch_server_function(request, "sf.csrf", "ping")
    assert resp.status_code == 403
    assert json.loads(resp.content)["error"] == "csrf_failed"


def test_async_server_function_awaited(authed_request):
    class V(LiveView):
        api_name = "sf.async"

        @server_function
        async def hello(self, name: str = "world", **kwargs):
            # async def branch — dispatcher must route through
            # _call_possibly_async and await the coroutine.
            return f"async {name}"

    register_api_view("sf.async", V)
    request, _ = authed_request({"params": {"name": "bob"}})
    resp = dispatch_server_function(request, "sf.async", "hello")
    assert resp.status_code == 200, resp.content
    assert json.loads(resp.content)["result"] == "async bob"


def test_return_value_serialized_as_json(authed_request):
    class V(LiveView):
        api_name = "sf.json"

        @server_function
        def items(self, **kwargs):
            # Mixed types + nested structure — DjangoJSONEncoder handles it.
            return {"count": 2, "rows": [{"id": 1}, {"id": 2}], "ok": True}

    register_api_view("sf.json", V)
    request, _ = authed_request({})
    resp = dispatch_server_function(request, "sf.json", "items")
    assert resp.status_code == 200
    body = json.loads(resp.content)
    assert body == {"result": {"count": 2, "rows": [{"id": 1}, {"id": 2}], "ok": True}}


def test_flat_body_rejected_with_400(authed_request):
    """Flat bodies like ``{"q": "foo"}`` must be rejected.

    The JS client always wraps params as ``{"params": {...}}``; tightening
    the server to match eliminates the ambiguity where a caller's own
    field named ``params`` would be silently unwrapped.
    """

    class V(LiveView):
        api_name = "sf.flatbody"

        @server_function
        def search(self, q: str = "", **kwargs):
            return q

    register_api_view("sf.flatbody", V)
    # Flat shape — no ``params`` wrapper.
    request, _ = authed_request({"q": "foo"})
    resp = dispatch_server_function(request, "sf.flatbody", "search")
    assert resp.status_code == 400
    assert json.loads(resp.content)["error"] == "invalid_body"


def test_body_with_params_plus_other_key_rejected(authed_request):
    """``{"params": {...}, "other": ...}`` must be rejected, not silently unwrapped."""

    class V(LiveView):
        api_name = "sf.mixedbody"

        @server_function
        def echo(self, x: int = 0, **kwargs):
            return x

    register_api_view("sf.mixedbody", V)
    request, _ = authed_request({"params": {"x": 1}, "other": 2})
    resp = dispatch_server_function(request, "sf.mixedbody", "echo")
    assert resp.status_code == 400
    assert json.loads(resp.content)["error"] == "invalid_body"


def test_dual_decoration_raises_at_decoration_time():
    """Stacking @event_handler + @server_function is a TypeError at import time.

    The order is tested in both directions — put @event_handler outer then
    inner, and vice-versa — because Python applies decorators bottom-up.
    """
    with pytest.raises(TypeError, match="cannot be both"):

        class _ViewA(LiveView):
            api_name = "sf.dual_a"

            @event_handler
            @server_function
            def bad(self, **kwargs):
                return None

    with pytest.raises(TypeError, match="cannot be both"):

        class _ViewB(LiveView):
            api_name = "sf.dual_b"

            @server_function
            @event_handler
            def bad(self, **kwargs):
                return None


def test_unauthenticated_returns_401_when_login_required():
    """Anonymous callers are blocked when login_required=True (default-secure)."""

    class V(LiveView):
        api_name = "sf.auth.lr"
        login_required = True

        @server_function
        def h(self, **kwargs):
            return "ok"

    register_api_view("sf.auth.lr", V)
    rf = RequestFactory(enforce_csrf_checks=False)
    request = rf.post(
        "/djust/api/call/sf.auth.lr/h/",
        data=b"{}",
        content_type="application/json",
    )
    request._dont_enforce_csrf_checks = True
    # No request.user attached → anonymous → check_view_auth returns redirect_url.
    resp = dispatch_server_function(request, "sf.auth.lr", "h")
    assert resp.status_code == 401
    assert json.loads(resp.content)["error"] == "login_required"


def test_anonymous_allowed_when_no_login_required():
    """With the hard-coded auth check removed (#1316), anonymous callers
    can invoke @server_function when the view doesn't set login_required."""

    class V(LiveView):
        api_name = "sf.anon"

        @server_function
        def greet(self, name: str = "world", **kwargs):
            return f"hello {name}"

    register_api_view("sf.anon", V)
    rf = RequestFactory(enforce_csrf_checks=False)
    request = rf.post(
        "/djust/api/call/sf.anon/greet/",
        data=json.dumps({"params": {"name": "anon"}}).encode("utf-8"),
        content_type="application/json",
    )
    request._dont_enforce_csrf_checks = True
    # No request.user — but that's OK, login_required is not set.
    resp = dispatch_server_function(request, "sf.anon", "greet")
    assert resp.status_code == 200, resp.content
    body = json.loads(resp.content)
    assert body["result"] == "hello anon"


def test_handler_permission_required_still_enforced():
    """@permission_required on the handler is still checked by
    check_handler_permission (#1316)."""

    class V(LiveView):
        api_name = "sf.handlerperm"

        @server_function
        @permission_required("auth.can_invoke")
        def secure(self, **kwargs):
            return "secret"

    register_api_view("sf.handlerperm", V)
    rf = RequestFactory(enforce_csrf_checks=False)
    User = get_user_model()
    user = User.objects.create_user(username="bob", password="pw")
    request = rf.post(
        "/djust/api/call/sf.handlerperm/secure/",
        data=b"{}",
        content_type="application/json",
    )
    SessionMiddleware(lambda r: None).process_request(request)
    request.session.save()
    request.user = user
    request._dont_enforce_csrf_checks = True

    # Bob doesn't have the permission → 403.
    resp = dispatch_server_function(request, "sf.handlerperm", "secure")
    assert resp.status_code == 403
    assert json.loads(resp.content)["error"] == "permission_denied"


def test_rate_limit_exceeded_returns_429(authed_request):
    class V(LiveView):
        api_name = "sf.rl"

        @server_function
        @rate_limit(rate=0.01, burst=2)
        def ping(self, **kwargs):
            return "pong"

    register_api_view("sf.rl", V)
    # Burst = 2 → 3rd call within the same refill window is denied.
    request, user = authed_request({})
    assert dispatch_server_function(request, "sf.rl", "ping").status_code == 200
    request, _ = authed_request({}, user=user)
    assert dispatch_server_function(request, "sf.rl", "ping").status_code == 200
    request, _ = authed_request({}, user=user)
    resp = dispatch_server_function(request, "sf.rl", "ping")
    assert resp.status_code == 429
    assert json.loads(resp.content)["error"] == "rate_limited"


def test_is_server_function_helper():
    """is_server_function reflects the decorator metadata contract."""

    @server_function
    def x(self):
        return 1

    def y(self):
        return 2

    assert is_server_function(x) is True
    assert is_server_function(y) is False


def test_non_serializable_return_returns_function_error(authed_request):
    """Non-JSON-serializable returns surface as the documented 500 envelope.

    Regression guard: ``JsonResponse`` serialization used to run OUTSIDE the
    try/except guarding the handler invocation, so a return value like
    ``{"bad": {1, 2, 3}}`` (sets aren't JSON-serializable) escaped as an
    unhandled ``TypeError`` and Django surfaced it as a generic 500
    ``http_error`` — contradicting the contract documented in
    ``docs/website/guides/server-functions.md``. The fix wraps serialization
    so the envelope stays ``{"error": "function_error", ...}``.
    """

    class V(LiveView):
        api_name = "sf.badret"

        @server_function
        def oops(self, **kwargs):
            # ``set`` is not JSON-serializable via DjangoJSONEncoder.
            return {"bad": {1, 2, 3}}

    register_api_view("sf.badret", V)
    request, _ = authed_request({})
    resp = dispatch_server_function(request, "sf.badret", "oops")
    assert resp.status_code == 500
    body = json.loads(resp.content)
    assert body["error"] == "function_error"
    assert "not JSON-serializable" in body["message"]


def test_url_call_route_resolves_before_generic_dispatch():
    """``call/<slug>/<fn>/`` must precede ``<slug>/<handler>/`` in URL matching.

    This guards against regressions where a future edit to ``default_api_urlpatterns``
    moves the generic dispatch pattern above the call route, which would
    silently shadow every server-function request with a 404 from the
    ADR-008 dispatcher.
    """
    from django.urls import resolve

    from djust.api.dispatch import DjustServerFunctionView

    patterns = []
    from djust.api.urls import default_api_urlpatterns

    # Build a URLconf-like object by iterating the patterns directly; the
    # Django default URL resolver uses the module's ``urlpatterns`` attr,
    # which is exactly ``default_api_urlpatterns()`` for ``djust.api.urls``.
    from django.urls import URLResolver
    from django.urls.resolvers import RegexPattern

    resolver = URLResolver(RegexPattern(r"^"), default_api_urlpatterns())
    match = resolver.resolve("call/some_slug/some_fn/")
    assert match.url_name == "djust-api-call"
    # ``func`` on a CBV is the ``cls.as_view()`` function; its wrapped view
    # class is exposed via ``view_class``.
    assert getattr(match.func, "view_class", None) is DjustServerFunctionView
    # And the generic pattern still resolves for the ADR-008 dispatch shape.
    alt = resolver.resolve("some_slug/some_fn/")
    assert alt.url_name == "djust-api-dispatch"
    _ = patterns, resolve  # silence unused-name lint
