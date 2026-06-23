"""Regression tests for the 2026-06 CodeQL log-hardening pass.

Each test reproduces the exact CodeQL finding and asserts the fix:

- #2421 ``py/clear-text-logging-sensitive-data`` — ``_trusted_proxy_count()``
  logs the *type* of a bad ``DJUST_TRUSTED_PROXY_COUNT``, never the raw
  ``settings`` value (which CodeQL treats as a sensitive source).
- #2422 ``py/log-injection`` — ``_openapi_gate()`` sanitizes ``request.path``
  (CR/LF stripped) before logging.
- #2423 ``py/log-injection`` — the observability ``_gate()`` sanitizes
  ``request.path`` before logging.
"""

import logging

from django.test import RequestFactory, override_settings

from djust._client_ip import _trusted_proxy_count
from djust.api.openapi import _openapi_gate
from djust.observability.views import _gate as _observability_gate

_CRLF_PATH = "/x\r\nFORGED-LOG-LINE: injected\r\n"


class TestClientIpMisconfigLoggingClearText:
    """#2421 — the misconfig warning must not echo the raw settings value."""

    @override_settings(DJUST_TRUSTED_PROXY_COUNT="leak-me-SEKRIT-VALUE")
    def test_warning_logs_type_not_raw_value(self, caplog):
        with caplog.at_level(logging.WARNING, logger="djust._client_ip"):
            count = _trusted_proxy_count()
        assert count == 0  # coerced to the safe default
        text = caplog.text
        assert "leak-me-SEKRIT-VALUE" not in text  # raw value never logged
        assert "str" in text  # the actionable TYPE is shown instead

    @override_settings(DJUST_TRUSTED_PROXY_COUNT=3)
    def test_valid_value_logs_nothing(self, caplog):
        with caplog.at_level(logging.WARNING, logger="djust._client_ip"):
            count = _trusted_proxy_count()
        assert count == 3
        assert caplog.text == ""  # no warning for a valid value


class TestOpenApiGateLogInjection:
    """#2422 — _openapi_gate sanitizes request.path before logging."""

    @override_settings(DEBUG=False, DJUST_API_OPENAPI_PUBLIC=False)
    def test_gate_strips_crlf_from_logged_path(self, caplog):
        req = RequestFactory().get("/djust/api/openapi.json")
        req.path = _CRLF_PATH  # attacker-controlled, with CRLF injection
        req.user = None  # unauthenticated -> rejection path (logs)
        with caplog.at_level(logging.WARNING, logger="djust.api.openapi"):
            resp = _openapi_gate(req)
        assert resp is not None and resp.status_code == 404
        msg = caplog.records[-1].getMessage()
        assert "\n" not in msg and "\r" not in msg  # no forged log line
        assert "FORGED-LOG-LINE" in msg  # content preserved, but on ONE line


class TestObservabilityGateLogInjection:
    """#2423 — observability _gate sanitizes request.path before logging."""

    @override_settings(DEBUG=True)
    def test_gate_strips_crlf_from_logged_path(self, caplog):
        req = RequestFactory().get("/__djust__/observability/", REMOTE_ADDR="203.0.113.7")
        req.path = _CRLF_PATH
        with caplog.at_level(logging.WARNING, logger="djust.observability.views"):
            resp = _observability_gate(req)
        assert resp is not None and resp.status_code == 404
        msg = caplog.records[-1].getMessage()
        assert "\n" not in msg and "\r" not in msg
        assert "FORGED-LOG-LINE" in msg
