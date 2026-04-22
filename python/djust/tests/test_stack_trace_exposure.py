"""Regression tests — stack-trace-exposure (PR closing CodeQL py/stack-trace-exposure alerts)."""

import json

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


def test_dispatch_serialize_error_does_not_leak_exception_message():
    """Verify dispatch.py's serialize_error path returns a generic message.

    The fix removes `str(exc)` from the api_error() call. Asserting on the
    source is a robust heuristic: the module is small and the string literal
    is unique.
    """
    import inspect

    from djust.api import dispatch

    src = inspect.getsource(dispatch)

    # Generic message must be present for the serialize_error path.
    assert (
        'api_error(500, "serialize_error", "Response transform' in src
        or '"serialize_error",\n            "Response transform' in src
        or '"serialize_error",\n            "Response transform' in src
    )

    # Leak-y patterns must be gone.
    assert 'api_error(500, "serialize_error", str(exc))' not in src
    assert 'api_error(500, "serialize_error", str(_exc))' not in src
