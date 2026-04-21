"""Tests for djust.api.dispatch (ADR-008 HTTP API transport)."""

from __future__ import annotations

import json

import pytest
from django.contrib.auth import get_user_model
from django.contrib.sessions.middleware import SessionMiddleware
from django.test import RequestFactory

from djust.api.dispatch import dispatch_api, reset_rate_buckets
from djust.api.registry import register_api_view, reset_registry
from djust.decorators import event_handler, permission_required, rate_limit
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
def authenticated_request_factory():
    """Factory producing authenticated POST requests with session + CSRF exempt."""
    rf = RequestFactory(enforce_csrf_checks=False)
    User = get_user_model()

    def make(body, user=None, slug_kwargs=None):
        if user is None:
            user = User.objects.create_user(username="alice", password="pw")
        body_bytes = json.dumps(body).encode("utf-8") if not isinstance(body, bytes) else body
        request = rf.post(
            "/djust/api/x/y/",
            data=body_bytes,
            content_type="application/json",
        )
        # Session attached so request.user works the Django way.
        middleware = SessionMiddleware(lambda r: None)
        middleware.process_request(request)
        request.session.save()
        request.user = user
        # RequestFactory does not set the Django test-client CSRF bypass flag.
        request._dont_enforce_csrf_checks = True
        return request, user

    return make


def _make_view_cls(name="V", slug="dispatch.v", body=None):
    """Build a dynamic LiveView subclass used as a dispatch target."""
    body = body or {}
    attrs = {"api_name": slug}
    attrs.update(body)
    return type(name, (LiveView,), attrs)


def test_404_when_view_slug_unknown(authenticated_request_factory):
    request, _ = authenticated_request_factory({})
    resp = dispatch_api(request, "does-not-exist", "noop")
    assert resp.status_code == 404
    assert json.loads(resp.content)["error"] == "unknown_view"


def test_404_when_handler_not_exposed(authenticated_request_factory):
    class V(LiveView):
        api_name = "dispatch.hidden"

        @event_handler  # NOT expose_api=True
        def go(self, **kwargs):
            return "x"

    register_api_view("dispatch.hidden", V)
    request, _ = authenticated_request_factory({})
    resp = dispatch_api(request, "dispatch.hidden", "go")
    assert resp.status_code == 404
    assert json.loads(resp.content)["error"] == "handler_not_exposed"


def test_404_when_handler_does_not_exist(authenticated_request_factory):
    class V(LiveView):
        api_name = "dispatch.empty"

        @event_handler(expose_api=True)
        def real(self, **kwargs):
            return None

    register_api_view("dispatch.empty", V)
    request, _ = authenticated_request_factory({})
    resp = dispatch_api(request, "dispatch.empty", "imaginary")
    assert resp.status_code == 404
    assert json.loads(resp.content)["error"] == "unknown_handler"


def test_401_when_unauthenticated():
    class V(LiveView):
        api_name = "dispatch.auth"

        @event_handler(expose_api=True)
        def h(self, **kwargs):
            return "ok"

    register_api_view("dispatch.auth", V)
    rf = RequestFactory(enforce_csrf_checks=False)
    request = rf.post("/djust/api/dispatch.auth/h/", data=b"{}", content_type="application/json")
    request._dont_enforce_csrf_checks = True
    # No request.user attached — SessionAuth returns None → 401.
    resp = dispatch_api(request, "dispatch.auth", "h")
    assert resp.status_code == 401
    assert json.loads(resp.content)["error"] == "unauthenticated"


def test_400_on_malformed_json(authenticated_request_factory):
    class V(LiveView):
        api_name = "dispatch.badjson"

        @event_handler(expose_api=True)
        def h(self, **kwargs):
            return None

    register_api_view("dispatch.badjson", V)
    request, _ = authenticated_request_factory(b"not-json")
    resp = dispatch_api(request, "dispatch.badjson", "h")
    assert resp.status_code == 400
    assert json.loads(resp.content)["error"] == "invalid_json"


def test_400_on_missing_required_param(authenticated_request_factory):
    class V(LiveView):
        api_name = "dispatch.required"

        @event_handler(expose_api=True)
        def greet(self, name: str, **kwargs):
            return f"hello {name}"

    register_api_view("dispatch.required", V)
    request, _ = authenticated_request_factory({})
    resp = dispatch_api(request, "dispatch.required", "greet")
    assert resp.status_code == 400
    body = json.loads(resp.content)
    assert body["error"] == "invalid_params"
    assert "details" in body


def test_200_happy_path_with_coercion_and_diff(authenticated_request_factory):
    class V(LiveView):
        api_name = "dispatch.happy"
        counter = 0

        def mount(self, request, **kwargs):
            self.counter = 0

        @event_handler(expose_api=True)
        def increment(self, by: int = 1, **kwargs):
            self.counter = self.counter + by
            return self.counter

    register_api_view("dispatch.happy", V)
    # Pass "5" as string — coercion must turn it into int.
    request, _ = authenticated_request_factory({"by": "5"})
    resp = dispatch_api(request, "dispatch.happy", "increment")
    assert resp.status_code == 200, resp.content
    body = json.loads(resp.content)
    assert body["result"] == 5
    assert body["assigns"]["counter"] == 5


def test_403_when_permission_required_denies(authenticated_request_factory):
    class V(LiveView):
        api_name = "dispatch.perm"

        @event_handler(expose_api=True)
        @permission_required("app.super_secret")
        def secret(self, **kwargs):
            return "nope"

    register_api_view("dispatch.perm", V)
    request, _ = authenticated_request_factory({})
    resp = dispatch_api(request, "dispatch.perm", "secret")
    assert resp.status_code == 403
    assert json.loads(resp.content)["error"] == "permission_denied"


def test_429_when_rate_limit_exceeded(authenticated_request_factory):
    class V(LiveView):
        api_name = "dispatch.rl"

        @event_handler(expose_api=True)
        @rate_limit(rate=0.01, burst=2)  # 2 tokens, trickle refill
        def ping(self, **kwargs):
            return "pong"

    register_api_view("dispatch.rl", V)
    # Burst = 2 → 3rd call within the same refill window is denied.
    request, user = authenticated_request_factory({})
    assert dispatch_api(request, "dispatch.rl", "ping").status_code == 200
    request, _ = authenticated_request_factory({}, user=user)
    assert dispatch_api(request, "dispatch.rl", "ping").status_code == 200
    request, _ = authenticated_request_factory({}, user=user)
    resp = dispatch_api(request, "dispatch.rl", "ping")
    assert resp.status_code == 429
    assert json.loads(resp.content)["error"] == "rate_limited"


def test_500_when_handler_raises(authenticated_request_factory, caplog):
    class V(LiveView):
        api_name = "dispatch.boom"

        @event_handler(expose_api=True)
        def broken(self, **kwargs):
            raise RuntimeError("internal detail not to leak")

    register_api_view("dispatch.boom", V)
    request, _ = authenticated_request_factory({})
    resp = dispatch_api(request, "dispatch.boom", "broken")
    assert resp.status_code == 500
    body = json.loads(resp.content)
    assert body["error"] == "handler_error"
    # The internal exception message MUST NOT leak to the client.
    assert "internal detail not to leak" not in resp.content.decode("utf-8")


def test_response_envelope_shape(authenticated_request_factory):
    class V(LiveView):
        api_name = "dispatch.envelope"
        name = ""

        def mount(self, request, **kwargs):
            self.name = ""

        @event_handler(expose_api=True)
        def set_name(self, name: str = "", **kwargs):
            self.name = name

    register_api_view("dispatch.envelope", V)
    request, _ = authenticated_request_factory({"name": "carla"})
    resp = dispatch_api(request, "dispatch.envelope", "set_name")
    assert resp.status_code == 200
    body = json.loads(resp.content)
    assert set(body.keys()) == {"result", "assigns"}
    assert body["result"] is None
    assert body["assigns"] == {"name": "carla"}


def test_custom_auth_class_with_csrf_exempt():
    """A token-style auth class can opt out of CSRF."""

    class HeaderTokenAuth:
        csrf_exempt = True

        def authenticate(self, request):
            if request.META.get("HTTP_X_API_TOKEN") == "secret":
                User = get_user_model()
                user, _ = User.objects.get_or_create(username="tokenuser")
                return user
            return None

    class V(LiveView):
        api_name = "dispatch.token"
        api_auth_classes = [HeaderTokenAuth]

        @event_handler(expose_api=True)
        def whoami(self, **kwargs):
            return getattr(self.request.user, "username", None)

    register_api_view("dispatch.token", V)
    rf = RequestFactory(enforce_csrf_checks=True)
    request = rf.post(
        "/djust/api/dispatch.token/whoami/",
        data=b"{}",
        content_type="application/json",
        HTTP_X_API_TOKEN="secret",
    )
    SessionMiddleware(lambda r: None).process_request(request)
    request.session.save()
    # Intentionally NOT setting _dont_enforce_csrf_checks: the token auth opts out.
    resp = dispatch_api(request, "dispatch.token", "whoami")
    assert resp.status_code == 200, resp.content
    assert json.loads(resp.content)["result"] == "tokenuser"


def test_csrf_required_for_session_auth():
    """Session auth is not csrf-exempt — a request without the bypass flag must 403."""

    class V(LiveView):
        api_name = "dispatch.csrf"

        @event_handler(expose_api=True)
        def h(self, **kwargs):
            return "ok"

    register_api_view("dispatch.csrf", V)
    User = get_user_model()
    user, _ = User.objects.get_or_create(username="csrfuser")
    rf = RequestFactory(enforce_csrf_checks=True)
    request = rf.post(
        "/djust/api/dispatch.csrf/h/",
        data=b"{}",
        content_type="application/json",
    )
    SessionMiddleware(lambda r: None).process_request(request)
    request.session.save()
    request.user = user
    # NOTE: intentionally NOT setting _dont_enforce_csrf_checks.
    resp = dispatch_api(request, "dispatch.csrf", "h")
    assert resp.status_code == 403
    assert json.loads(resp.content)["error"] == "csrf_failed"


def test_rate_bucket_lru_cap_bounds_memory():
    """Rotating caller identities cannot inflate the bucket dict without bound."""
    from djust.api import dispatch as dispatch_mod

    dispatch_mod.reset_rate_buckets()
    original_cap = dispatch_mod._RATE_BUCKET_CAP
    dispatch_mod._RATE_BUCKET_CAP = 5
    try:

        class V(LiveView):
            api_name = "dispatch.lru"

            @event_handler(expose_api=True)
            @rate_limit(rate=100, burst=10)
            def ping(self, **kwargs):
                return "pong"

        register_api_view("dispatch.lru", V)
        rf = RequestFactory(enforce_csrf_checks=False)
        # Fire 20 requests from 20 distinct IPs — dict must not exceed 5.
        for i in range(20):
            request = rf.post(
                "/djust/api/dispatch.lru/ping/",
                data=b"{}",
                content_type="application/json",
                REMOTE_ADDR=f"10.0.0.{i}",
            )
            SessionMiddleware(lambda r: None).process_request(request)
            request.session.save()
            request._dont_enforce_csrf_checks = True
            # Unauthenticated: SessionAuth fails. Use a custom auth that accepts all.
            request.user = None  # force anonymous path
            # We can't actually call dispatch_api without an auth pass; instead
            # call the internal helper directly.
            dispatch_mod._rate_limit_check(request, "ping", V.ping)
        assert len(dispatch_mod._rate_buckets) == 5
    finally:
        dispatch_mod._RATE_BUCKET_CAP = original_cap


def test_empty_body_defaults_to_empty_object(authenticated_request_factory):
    class V(LiveView):
        api_name = "dispatch.empty_body"

        @event_handler(expose_api=True)
        def ping(self, **kwargs):
            return "ok"

    register_api_view("dispatch.empty_body", V)
    request, _ = authenticated_request_factory(b"")
    resp = dispatch_api(request, "dispatch.empty_body", "ping")
    assert resp.status_code == 200
    assert json.loads(resp.content)["result"] == "ok"
