"""Security regression tests for the OpenAPI schema gate (finding #29).

``OpenAPISchemaView`` (``/djust/api/openapi.json``) enumerates the entire
``expose_api`` attack surface — endpoint URLs, internal view-class + handler
names, parameter names/types, and handler docstrings. Before this fix it was
served to anonymous clients with no DEBUG/auth gate (CWE-200 / CWE-651).

The gate (``_openapi_gate`` / ``OpenAPISchemaView.get``) is secure-by-default
with this precedence (first match wins):

1. ``settings.DEBUG`` is True → serve.
2. ``settings.DJUST_API_OPENAPI_PUBLIC`` is True → serve.
3. The request is authenticated → serve.
4. Otherwise → non-disclosing 404 (NOT 403).

These tests assert the documented behavior via ``RequestFactory`` against the
real view callable. The gate-off self-test (#1468) at the bottom proves the
anonymous-blocked assertion is non-tautological: neutering the gate makes the
default-deny test fail because the schema is served to an anonymous client.
"""

from __future__ import annotations

import json

import pytest
from django.contrib.auth import get_user_model
from django.contrib.auth.models import AnonymousUser
from django.test import RequestFactory, override_settings

from djust.api.openapi import OpenAPISchemaView, _openapi_gate
from djust.api.registry import register_api_view, reset_registry
from djust.decorators import event_handler
from djust.live_view import LiveView

pytestmark = pytest.mark.django_db

# Strings the schema leaks; a gated 404 body must contain none of them.
_LEAKED_TOKENS = ("f29.secret", "secret_handler", "SecretView", "token_id", "Sensitive")


def _get(user=None):
    """Build a GET request for the schema endpoint, optionally with ``request.user``."""
    rf = RequestFactory()
    request = rf.get("/djust/api/openapi.json")
    if user is not None:
        request.user = user
    return OpenAPISchemaView.as_view()(request)


class TestOpenAPIGateF29:
    """Gate behavior for the OpenAPI schema endpoint (finding #29)."""

    @pytest.fixture(autouse=True)
    def _clean_registry(self):
        reset_registry()
        yield
        reset_registry()

    @pytest.fixture
    def _exposed_view(self):
        """Register a view with one exposed handler so the schema is non-empty.

        The handler name (``secret_handler``), view-class name (``SecretView``),
        a parameter name (``token_id``), and the docstring (``Sensitive ...``)
        are all things the schema would leak — the tests assert they are absent
        from a gated response body and present in a served one.
        """

        class SecretView(LiveView):
            api_name = "f29.secret"

            @event_handler(expose_api=True)
            def secret_handler(self, token_id: int = 0, **kwargs):
                """Sensitive internal handler docstring."""
                return None

        register_api_view("f29.secret", SecretView)
        return SecretView

    @override_settings(DEBUG=False, DJUST_API_OPENAPI_PUBLIC=False)
    def test_anonymous_no_debug_no_optin_returns_non_disclosing_404(self, _exposed_view):
        """Default posture: anonymous + DEBUG off + opt-in unset → 404, no schema."""
        resp = _get(AnonymousUser())
        assert resp.status_code == 404, "anonymous request must be gated to 404 by default"
        body = resp.content.decode("utf-8")
        for token in _LEAKED_TOKENS:
            assert token not in body, f"gated 404 body must not disclose {token!r}"
        # Non-disclosing: 404 (existence hidden), not 403 (existence confirmed).
        assert resp.status_code != 403

    @override_settings(DEBUG=False, DJUST_API_OPENAPI_PUBLIC=False)
    def test_missing_request_user_fails_closed_to_404(self, _exposed_view):
        """No AuthenticationMiddleware (no ``request.user``) → fail-closed 404."""
        resp = _get(user=None)  # no request.user assigned
        assert resp.status_code == 404

    @override_settings(DEBUG=True)
    def test_debug_true_serves_schema(self, _exposed_view):
        """DEBUG=True → serve the full schema (dev convenience)."""
        resp = _get(AnonymousUser())
        assert resp.status_code == 200
        schema = json.loads(resp.content)
        assert schema["openapi"] == "3.1.0"
        assert "/djust/api/f29.secret/secret_handler/" in schema["paths"]

    @override_settings(DEBUG=False, DJUST_API_OPENAPI_PUBLIC=True)
    def test_opt_in_public_serves_schema_to_anonymous(self, _exposed_view):
        """DJUST_API_OPENAPI_PUBLIC=True → operator opt-in serves anonymous."""
        resp = _get(AnonymousUser())
        assert resp.status_code == 200
        schema = json.loads(resp.content)
        assert "/djust/api/f29.secret/secret_handler/" in schema["paths"]

    @override_settings(DEBUG=False, DJUST_API_OPENAPI_PUBLIC=False)
    def test_authenticated_user_serves_schema(self, _exposed_view):
        """Authenticated request (DEBUG off, opt-in unset) → serve the schema."""
        User = get_user_model()
        user = User.objects.create_user(username="dev", password="pw")
        resp = _get(user)
        assert resp.status_code == 200
        schema = json.loads(resp.content)
        assert "/djust/api/f29.secret/secret_handler/" in schema["paths"]

    # --- Helper-level precedence assertions (no HTTP round-trip) ---

    @override_settings(DEBUG=False, DJUST_API_OPENAPI_PUBLIC=False)
    def test_gate_returns_response_for_anonymous(self):
        """``_openapi_gate`` returns a 404 HttpResponse (not None) when refused."""
        rf = RequestFactory()
        request = rf.get("/djust/api/openapi.json")
        request.user = AnonymousUser()
        gate = _openapi_gate(request)
        assert gate is not None
        assert gate.status_code == 404

    @override_settings(DEBUG=False, DJUST_API_OPENAPI_PUBLIC=False)
    def test_gate_returns_none_for_authenticated(self):
        """``_openapi_gate`` returns None (serve) for an authenticated request."""
        User = get_user_model()
        user = User.objects.create_user(username="dev2", password="pw")
        rf = RequestFactory()
        request = rf.get("/djust/api/openapi.json")
        request.user = user
        assert _openapi_gate(request) is None

    # --- Gate-off self-test (#1468): proves the default-deny test is non-tautological ---

    @override_settings(DEBUG=False, DJUST_API_OPENAPI_PUBLIC=False)
    def test_gate_off_would_serve_schema_to_anonymous(self, _exposed_view, monkeypatch):
        """Neuter the gate (always return None) → the schema IS served to anonymous.

        This is the gate-off counterpart of
        ``test_anonymous_no_debug_no_optin_returns_non_disclosing_404``: with the
        gate disabled, the same anonymous request that returns 404 above instead
        returns 200 + the full schema, confirming the gate (not some unrelated
        setup) is what produces the 404.
        """
        import djust.api.openapi as openapi_mod

        monkeypatch.setattr(openapi_mod, "_openapi_gate", lambda request: None)
        resp = _get(AnonymousUser())
        assert resp.status_code == 200
        body = resp.content.decode("utf-8")
        # Every token the gated test asserts is absent is now present.
        for token in _LEAKED_TOKENS:
            assert token in body, f"with gate off, schema should disclose {token!r}"
