"""Tests for ``api_response()`` convention + ``@event_handler(serialize=...)`` override.

See ADR-008 §"Transport-conditional return values" resolution and the plan at
``docs/website/guides/http-api.md`` under "Transport-conditional returns".

Three code paths exercised:
1. Passthrough — no override, no convention → handler's return value used as-is.
2. Convention — view defines ``api_response()`` → called on HTTP, not on WS.
3. Per-handler override — ``serialize=<callable-or-str>`` on the handler.
"""

from __future__ import annotations

import asyncio
import json

import pytest
from django.contrib.auth import get_user_model
from django.contrib.sessions.middleware import SessionMiddleware
from django.test import RequestFactory

from djust.api.dispatch import (
    _apply_response_transform,
    dispatch_api,
    reset_rate_buckets,
)
from djust.api.registry import register_api_view, reset_registry
from djust.decorators import event_handler
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
def http_request():
    """Factory producing authenticated POST requests.

    Each invocation uses a unique username so multiple calls within a single
    test don't collide on the ``auth_user.username`` UNIQUE constraint.
    """
    rf = RequestFactory(enforce_csrf_checks=False)
    User = get_user_model()
    counter = {"n": 0}

    def make(body):
        counter["n"] += 1
        user = User.objects.create_user(username=f"alice{counter['n']}", password="pw")
        body_bytes = json.dumps(body).encode("utf-8")
        req = rf.post(
            "/djust/api/x/y/",
            data=body_bytes,
            content_type="application/json",
        )
        middleware = SessionMiddleware(lambda r: None)
        middleware.process_request(req)
        req.session.save()
        req.user = user
        req._dont_enforce_csrf_checks = True
        return req

    return make


# ─────────────────────────────────────────────────────────────────────────────
# Unit tests against _apply_response_transform directly
# ─────────────────────────────────────────────────────────────────────────────


def _make_handler(serialize=None, expose_api=True):
    """Produce a function with ``@event_handler``-style metadata for unit testing."""

    def fn(self, **kwargs):
        return "handler-return"

    if expose_api:
        fn._djust_decorators = {"event_handler": {"expose_api": True, "serialize": serialize}}
    return fn


def test_passthrough_when_no_api_response_and_no_serialize():
    class V:
        pass  # no api_response

    handler = _make_handler()
    assert _apply_response_transform(V(), handler, "x") == "x"


def test_convention_api_response_called_when_defined():
    class V:
        def api_response(self):
            return ["from-convention"]

    handler = _make_handler()
    assert _apply_response_transform(V(), handler, None) == ["from-convention"]


def test_convention_called_with_handler_return_for_two_arg_signature():
    class V:
        def api_response(self, result):
            return {"got": result}

    handler = _make_handler()
    assert _apply_response_transform(V(), handler, "hello") == {"got": "hello"}


def test_serialize_lambda_one_arg_receives_view():
    seen = {}

    def serializer(self):
        seen["view_type"] = type(self).__name__
        return ["ok"]

    class V:
        def api_response(self):
            raise AssertionError("api_response must NOT fire when serialize= is set")

    handler = _make_handler(serialize=serializer)
    result = _apply_response_transform(V(), handler, None)
    assert result == ["ok"]
    assert seen == {"view_type": "V"}


def test_serialize_lambda_two_arg_receives_view_and_return():
    def serializer(self, handler_return):
        return {"view": type(self).__name__, "ret": handler_return}

    handler = _make_handler(serialize=serializer)

    class V:
        pass

    result = _apply_response_transform(V(), handler, 42)
    assert result == {"view": "V", "ret": 42}


def test_serialize_zero_arg_callable_called_with_no_args():
    calls = []

    def serializer():
        calls.append("fire")
        return "bare"

    handler = _make_handler(serialize=serializer)

    class V:
        pass

    assert _apply_response_transform(V(), handler, None) == "bare"
    assert calls == ["fire"]


def test_serialize_str_resolves_method_on_view():
    class V:
        def serialize_claims(self):
            return ["c1", "c2"]

    handler = _make_handler(serialize="serialize_claims")
    assert _apply_response_transform(V(), handler, None) == ["c1", "c2"]


def test_serialize_str_raises_when_method_missing():
    class V:
        pass

    handler = _make_handler(serialize="does_not_exist")
    with pytest.raises(TypeError, match="names no callable method"):
        _apply_response_transform(V(), handler, None)


def test_serialize_invalid_type_raises():
    handler = _make_handler(serialize=42)  # neither callable, str, nor None

    class V:
        pass

    with pytest.raises(TypeError, match="must be None, a callable, or a method-name string"):
        _apply_response_transform(V(), handler, None)


def test_per_handler_serialize_wins_over_convention():
    class V:
        def api_response(self):
            raise AssertionError("api_response must NOT fire when serialize= is set")

    handler = _make_handler(serialize=lambda self: {"override": True})
    assert _apply_response_transform(V(), handler, None) == {"override": True}


def test_api_response_shadowed_by_non_callable_falls_through():
    class V:
        api_response = "not-callable"  # shadowed by a data attribute

    handler = _make_handler()
    assert _apply_response_transform(V(), handler, "raw") == "raw"


def test_async_api_response_is_awaited():
    class V:
        async def api_response(self):
            return "async-result"

    handler = _make_handler()
    assert _apply_response_transform(V(), handler, None) == "async-result"


def test_async_serialize_callable_is_awaited():
    async def serializer(self):
        await asyncio.sleep(0)
        return "await-ok"

    handler = _make_handler(serialize=serializer)

    class V:
        pass

    assert _apply_response_transform(V(), handler, None) == "await-ok"


def test_mixin_provided_api_response_resolves_via_mro():
    class ApiMixin:
        def api_response(self):
            return ["from-mixin"]

    class V(ApiMixin):
        pass

    handler = _make_handler()
    assert _apply_response_transform(V(), handler, None) == ["from-mixin"]


def test_handler_return_is_none_when_nothing_overrides():
    class V:
        pass

    handler = _make_handler()
    assert _apply_response_transform(V(), handler, None) is None


# ─────────────────────────────────────────────────────────────────────────────
# Decoration-time validation
# ─────────────────────────────────────────────────────────────────────────────


def test_decoration_time_typeerror_when_serialize_without_expose_api():
    with pytest.raises(TypeError, match="requires expose_api=True"):

        @event_handler(serialize=lambda self: [])
        def search(self, **kwargs):
            pass


# ─────────────────────────────────────────────────────────────────────────────
# End-to-end: dispatch_api honors both resolution paths
# ─────────────────────────────────────────────────────────────────────────────


def test_dispatch_convention_end_to_end(http_request):
    class ConventionView(LiveView):
        api_name = "conv.v"

        def api_response(self):
            # Reads state the handler just set — order-of-operations regression.
            return {"query": getattr(self, "search_query", None), "hits": [1, 2, 3]}

        @event_handler(expose_api=True)
        def search(self, value: str = "", **kwargs):
            self.search_query = value

    register_api_view("conv.v", ConventionView)
    response = dispatch_api(http_request({"value": "django"}), "conv.v", "search")
    assert response.status_code == 200
    payload = json.loads(response.content)
    assert payload["result"] == {"query": "django", "hits": [1, 2, 3]}
    # assigns diff still reflects the mutation.
    assert payload["assigns"].get("search_query") == "django"


def test_dispatch_convention_shared_across_handlers(http_request):
    """DRY lock-in: two handlers fall through to the same api_response()."""

    class MultiView(LiveView):
        api_name = "multi.v"

        def api_response(self):
            return {"shape": "uniform"}

        @event_handler(expose_api=True)
        def search(self, value: str = "", **kwargs):
            self.search_query = value

        @event_handler(expose_api=True)
        def clear(self, **kwargs):
            self.search_query = ""

    register_api_view("multi.v", MultiView)
    r1 = json.loads(dispatch_api(http_request({"value": "x"}), "multi.v", "search").content)
    r2 = json.loads(dispatch_api(http_request({}), "multi.v", "clear").content)
    assert r1["result"] == r2["result"] == {"shape": "uniform"}


def test_dispatch_per_handler_serialize_str_override(http_request):
    class MixedView(LiveView):
        api_name = "mixed.v"

        def api_response(self):
            return ["default-list"]

        @event_handler(expose_api=True, serialize="serialize_one")
        def save_claim(self, id: int = 0, **kwargs):
            return id

        def serialize_one(self):
            return {"saved": True}

    register_api_view("mixed.v", MixedView)
    response = dispatch_api(http_request({"id": 7}), "mixed.v", "save_claim")
    assert response.status_code == 200
    payload = json.loads(response.content)
    assert payload["result"] == {"saved": True}


def test_dispatch_serialize_str_missing_method_returns_500(http_request):
    class BadView(LiveView):
        api_name = "bad.v"

        @event_handler(expose_api=True, serialize="missing_method")
        def ping(self, **kwargs):
            return None

    register_api_view("bad.v", BadView)
    response = dispatch_api(http_request({}), "bad.v", "ping")
    assert response.status_code == 500
    payload = json.loads(response.content)
    assert payload["error"] == "serialize_error"
    # Error payload is sanitized (doesn't leak the serializer method name
    # or exception internals to the client). Details go to server logs.
    # Verify the generic sanitized message is returned.
    assert "Response transform" in payload["message"]


def test_dispatch_serializer_exception_returns_500(http_request):
    class ExplodingView(LiveView):
        api_name = "explode.v"

        @event_handler(expose_api=True, serialize=lambda self: 1 / 0)
        def ping(self, **kwargs):
            return None

    register_api_view("explode.v", ExplodingView)
    response = dispatch_api(http_request({}), "explode.v", "ping")
    assert response.status_code == 500
    payload = json.loads(response.content)
    assert payload["error"] == "serialize_error"


def test_dispatch_passthrough_when_nothing_overrides(http_request):
    """Handler return value is used as-is when no api_response and no serialize=."""

    class PassthroughView(LiveView):
        api_name = "pass.v"

        @event_handler(expose_api=True)
        def ping(self, **kwargs):
            return {"direct": True}

    register_api_view("pass.v", PassthroughView)
    payload = json.loads(dispatch_api(http_request({}), "pass.v", "ping").content)
    assert payload["result"] == {"direct": True}


def test_dispatch_permission_denied_in_api_response_surfaces_403(http_request):
    """Stage 11 regression: PermissionDenied raised from api_response() must be 403, not 500."""
    from django.core.exceptions import PermissionDenied

    class GuardedView(LiveView):
        api_name = "guarded.v"

        def api_response(self):
            raise PermissionDenied("nope")

        @event_handler(expose_api=True)
        def ping(self, **kwargs):
            return None

    register_api_view("guarded.v", GuardedView)
    response = dispatch_api(http_request({}), "guarded.v", "ping")
    assert response.status_code == 403
    payload = json.loads(response.content)
    assert payload["error"] == "permission_denied"
    assert payload["message"] == "nope"


def test_dispatch_permission_denied_in_serialize_surfaces_403(http_request):
    from django.core.exceptions import PermissionDenied

    def serializer(self):
        raise PermissionDenied("blocked by serializer")

    class GuardedView(LiveView):
        api_name = "guarded_ser.v"

        @event_handler(expose_api=True, serialize=serializer)
        def ping(self, **kwargs):
            return None

    register_api_view("guarded_ser.v", GuardedView)
    response = dispatch_api(http_request({}), "guarded_ser.v", "ping")
    assert response.status_code == 403
    payload = json.loads(response.content)
    assert payload["error"] == "permission_denied"
    assert payload["message"] == "blocked by serializer"


def test_api_request_flag_set_before_mount_runs(http_request):
    """Stage 11 regression: _api_request must be True during mount(), not just after."""
    captured = {}

    class MountReadsFlagView(LiveView):
        api_name = "mount_flag.v"

        def mount(self, request=None, **kwargs):
            captured["flag_in_mount"] = getattr(self, "_api_request", None)

        @event_handler(expose_api=True)
        def ping(self, **kwargs):
            captured["flag_in_handler"] = getattr(self, "_api_request", None)

    register_api_view("mount_flag.v", MountReadsFlagView)
    response = dispatch_api(http_request({}), "mount_flag.v", "ping")
    assert response.status_code == 200
    assert captured["flag_in_mount"] is True
    assert captured["flag_in_handler"] is True


def test_api_response_returning_none_is_used_verbatim():
    """An explicit None return from api_response() replaces the handler return."""

    class V:
        def api_response(self):
            return None

    handler = _make_handler()
    assert _apply_response_transform(V(), handler, "would-have-been-returned") is None


def test_serialize_str_resolves_staticmethod():
    """staticmethods accessed via getattr(view, 'name') are regular functions.

    The arity detector treats them as plain callables (not ismethod), so:
    - 0 positional → called with no args
    - 1 positional → called with (view,)
    - 2+ positional → called with (view, result)
    """

    class V:
        @staticmethod
        def serialize_static():
            return "static-zero-arg"

    handler = _make_handler(serialize="serialize_static")
    assert _apply_response_transform(V(), handler, None) == "static-zero-arg"


def test_callable_class_instance_works_as_serializer():
    """Callable-class-instance (with __call__) is treated as a plain callable."""

    class Callable:
        def __call__(self, view):
            return f"called-{type(view).__name__}"

    class V:
        pass

    handler = _make_handler(serialize=Callable())
    assert _apply_response_transform(V(), handler, None) == "called-V"
