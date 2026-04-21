"""Parity tests: HTTP and WebSocket transports share the same handler stack.

Per ADR-008 §"Decision": the HTTP transport is a thin adapter over the existing
handler pipeline. These tests verify the guarantee that a handler change in one
place affects both transports — not two validation paths, not two permission
checks, not two rate-limit buckets.
"""

from __future__ import annotations

import json

import pytest
from django.contrib.auth import get_user_model
from django.contrib.sessions.middleware import SessionMiddleware
from django.test import RequestFactory

from djust.api.dispatch import dispatch_api, reset_rate_buckets
from djust.api.registry import register_api_view, reset_registry
from djust.decorators import event_handler
from djust.live_view import LiveView
from djust.validation import get_handler_signature_info, validate_handler_params

pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def _clean_state():
    reset_registry()
    reset_rate_buckets()
    yield
    reset_registry()
    reset_rate_buckets()


def _auth_request(body):
    User = get_user_model()
    user, _ = User.objects.get_or_create(username="parity")
    rf = RequestFactory(enforce_csrf_checks=False)
    request = rf.post(
        "/djust/api/parity/h/",
        data=json.dumps(body).encode("utf-8"),
        content_type="application/json",
    )
    SessionMiddleware(lambda r: None).process_request(request)
    request.session.save()
    request.user = user
    request._dont_enforce_csrf_checks = True
    return request


def test_coercion_parity_int_from_string():
    """Same `str -> int` coercion runs for both transports."""

    class V(LiveView):
        api_name = "parity.coerce"
        value = 0

        def mount(self, request, **kwargs):
            self.value = 0

        @event_handler(expose_api=True)
        def set_count(self, count: int = 0, **kwargs):
            self.value = count
            return self.value

    register_api_view("parity.coerce", V)

    # HTTP path: send "42" as JSON string.
    request = _auth_request({"count": "42"})
    resp = dispatch_api(request, "parity.coerce", "set_count")
    body = json.loads(resp.content)
    assert body["result"] == 42
    assert body["assigns"]["value"] == 42

    # Direct validation path (same function the WS consumer uses).
    v = V()
    result = validate_handler_params(v.set_count, {"count": "42"}, "set_count")
    assert result["valid"] is True
    assert result["coerced_params"]["count"] == 42


def test_signature_source_of_truth_is_decorator_metadata():
    """The OpenAPI schema and WS validation both read the same metadata."""

    class V(LiveView):
        api_name = "parity.sig"

        @event_handler(expose_api=True, description="Does a thing")
        def act(self, item_id: int, quantity: int = 1, **kwargs):
            return None

    register_api_view("parity.sig", V)
    sig_info = get_handler_signature_info(V.act)
    required = {p["name"] for p in sig_info["params"] if p["required"]}
    assert required == {"item_id"}
    # The decorator metadata is the single source — any param list change propagates
    # to both the WS validator (which reads _djust_decorators) and the OpenAPI
    # generator (which reads get_handler_signature_info on the same function object).
    from djust.api.openapi import build_schema

    schema = build_schema()
    body = schema["paths"]["/djust/api/parity.sig/act/"]["post"]["requestBody"]["content"][
        "application/json"
    ]["schema"]
    assert set(body["required"]) == required


def test_handler_not_exposed_is_never_callable_via_http():
    """The WS consumer can call any @event_handler; HTTP must refuse without expose_api."""

    class V(LiveView):
        api_name = "parity.hidden"

        @event_handler  # Not expose_api=True
        def ws_only(self, **kwargs):
            return "internal"

    register_api_view("parity.hidden", V)
    request = _auth_request({})
    resp = dispatch_api(request, "parity.hidden", "ws_only")
    # Handler exists and is a valid event_handler — but HTTP must still refuse.
    assert resp.status_code == 404
    assert json.loads(resp.content)["error"] == "handler_not_exposed"


def test_permission_required_enforced_on_http_path():
    """``@permission_required`` denies the call regardless of transport."""
    from djust.decorators import permission_required

    class V(LiveView):
        api_name = "parity.perm"

        @event_handler(expose_api=True)
        @permission_required("app.never_granted")
        def restricted(self, **kwargs):
            return "unreachable"

    register_api_view("parity.perm", V)
    request = _auth_request({})
    resp = dispatch_api(request, "parity.perm", "restricted")
    assert resp.status_code == 403
