"""Regression tests — stack-trace-exposure (PR closing CodeQL py/stack-trace-exposure alerts)."""

import json

import pytest
from django.test import RequestFactory


def test_theme_inspector_api_generic_error_on_exception(monkeypatch):
    """theme_inspector_api must not leak exception messages to the client."""
    from djust.theming import inspector as inspector_mod

    factory = RequestFactory()
    req = factory.get("/theme/api/?design=minimalist&color=default")

    # Force an exception by monkeypatching get_theme_info to raise with
    # a highly specific error containing "SENTINEL_INTERNAL_STATE".
    def _boom(self, *a, **kw):
        raise RuntimeError("SENTINEL_INTERNAL_STATE should not leak")

    monkeypatch.setattr(inspector_mod.ThemeInspector, "get_theme_info", _boom)

    resp = inspector_mod.theme_inspector_api(req)
    assert resp.status_code == 500
    body = json.loads(resp.content)
    assert "SENTINEL_INTERNAL_STATE" not in body["error"]
    # Should reference log guidance
    lowered = body["error"].lower()
    assert "server logs" in lowered or "internal" in lowered


def test_theme_css_api_generic_error_on_exception(monkeypatch):
    """theme_css_api must not leak exception messages to the client."""
    from djust.theming import inspector as inspector_mod

    factory = RequestFactory()
    req = factory.get("/theme/css/?design=minimalist&color=default")

    def _boom(*a, **kw):
        raise RuntimeError("SENTINEL_INTERNAL_STATE should not leak")

    monkeypatch.setattr(inspector_mod, "generate_design_system_css", _boom)

    resp = inspector_mod.theme_css_api(req)
    assert resp.status_code == 500
    body = json.loads(resp.content)
    assert "SENTINEL_INTERNAL_STATE" not in body["error"]


@pytest.mark.django_db
def test_dispatch_serialize_error_does_not_leak_exception_message():
    """Behavior test: a raising serializer must produce a generic 500 envelope
    that contains neither the exception class name nor any of its message
    text. Replaces the prior `inspect.getsource` substring test (#1027) —
    behavior tests defend against regressions even when the leak vector
    changes (e.g. a future refactor moves the message-construction site).
    """
    import json as _json

    from django.contrib.auth import get_user_model
    from django.contrib.sessions.middleware import SessionMiddleware
    from django.test import RequestFactory

    from djust import LiveView
    from djust.api.dispatch import dispatch_api, reset_rate_buckets
    from djust.api.registry import register_api_view, reset_registry
    from djust.decorators import event_handler

    sentinel = "SENTINEL_SERIALIZE_LEAK_DEADBEEF_42"

    def _exploder(self):
        raise RuntimeError(sentinel)

    class ExplodingView(LiveView):
        api_name = "test.serialize_leak.v"

        @event_handler(expose_api=True, serialize=_exploder)
        def ping(self, **kwargs):
            return None

    reset_registry()
    reset_rate_buckets()
    register_api_view("test.serialize_leak.v", ExplodingView)

    rf = RequestFactory(enforce_csrf_checks=False)
    User = get_user_model()
    User.objects.create_user(username="leak_tester", password="pw")
    req = rf.post(
        "/djust/api/test.serialize_leak.v/ping",
        data=b"{}",
        content_type="application/json",
    )
    SessionMiddleware(lambda r: None).process_request(req)
    req.session.save()
    req.user = User.objects.get(username="leak_tester")
    req._dont_enforce_csrf_checks = True

    resp = dispatch_api(req, "test.serialize_leak.v", "ping")

    assert resp.status_code == 500
    payload = _json.loads(resp.content)
    assert payload["error"] == "serialize_error"
    # Generic message must be present.
    assert "Response transform" in payload["message"]
    # Sentinel + class name from the raised exception must NOT appear
    # anywhere in the serialized response.
    body_text = resp.content.decode("utf-8")
    assert sentinel not in body_text
    assert "RuntimeError" not in body_text
