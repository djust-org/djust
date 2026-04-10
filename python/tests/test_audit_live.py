"""
Tests for ``djust_audit --live`` runtime probe (#661).

Tests are split into two layers:

1. **Pure-function unit tests** for ``check_security_headers``,
   ``check_cookies``, ``_extract_max_age``, ``_looks_like_version_leak``,
   and the cookie name extractor. These take a ``FetchResult`` as input
   so they can exercise every finding code without any network I/O.

2. **End-to-end tests** via a threaded ``http.server`` bound to localhost
   that serves canned responses with controlled headers/cookies. These
   exercise ``fetch``, ``probe_paths``, and the top-level
   ``run_live_audit`` orchestrator against a real HTTP server.
"""

import contextlib
import json as json_module
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Dict, List, Optional

from djust.audit_live import (
    LIVE_FINDING_CODES,
    FetchResult,
    LiveFinding,
    _cookie_name,
    _extract_max_age,
    _looks_like_version_leak,
    check_cookies,
    check_security_headers,
    fetch,
    run_live_audit,
)


def _ids(findings):
    return {f.code for f in findings}


def _make_result(
    url: str = "https://example.com/",
    status: int = 200,
    headers: Optional[Dict[str, str]] = None,
    cookies: Optional[List[str]] = None,
) -> FetchResult:
    """Build a FetchResult for unit tests without network I/O."""
    # Note: header keys are stored lowercase by the real fetch()
    lowered = {k.lower(): v for k, v in (headers or {}).items()}
    return FetchResult(
        url=url,
        status=status,
        headers=lowered,
        cookies=cookies or [],
    )


# ---------------------------------------------------------------------------
# Finding codes
# ---------------------------------------------------------------------------


class TestFindingCodes:
    def test_all_codes_have_severity_and_description(self):
        for code, (severity, desc) in LIVE_FINDING_CODES.items():
            assert severity in ("error", "warning", "info")
            assert desc

    def test_finding_make_uses_canonical_severity(self):
        f = LiveFinding.make("L001", "x")
        assert f.severity == "error"
        f = LiveFinding.make("L002", "x")
        assert f.severity == "warning"
        f = LiveFinding.make("L007", "x")
        assert f.severity == "info"

    def test_finding_format_line_with_url(self):
        f = LiveFinding.make("L001", "CSP missing", url="https://e.com/")
        line = f.format_line()
        assert "djust.L001" in line
        assert "https://e.com/" in line
        assert "CSP missing" in line

    def test_finding_to_dict(self):
        f = LiveFinding.make("L005", "hsts short", url="u", details="42 days")
        d = f.to_dict()
        assert d["code"] == "L005"
        assert d["severity"] == "warning"
        assert d["details"] == "42 days"


# ---------------------------------------------------------------------------
# Pure helpers
# ---------------------------------------------------------------------------


class TestExtractMaxAge:
    def test_basic(self):
        assert _extract_max_age("max-age=31536000") == 31_536_000

    def test_with_spaces(self):
        assert _extract_max_age("max-age = 15724800 ; includeSubDomains") == 15_724_800

    def test_case_insensitive(self):
        assert _extract_max_age("MAX-AGE=900") == 900

    def test_missing(self):
        assert _extract_max_age("includeSubDomains") is None


class TestVersionLeak:
    def test_nginx_version(self):
        assert _looks_like_version_leak("nginx/1.25.3") is True

    def test_apache(self):
        assert _looks_like_version_leak("Apache/2.4.58") is True

    def test_python(self):
        assert _looks_like_version_leak("Python/3.12.9") is True

    def test_safe_server(self):
        assert _looks_like_version_leak("nginx") is False
        assert _looks_like_version_leak("cloudflare") is False


class TestCookieName:
    def test_basic(self):
        assert _cookie_name("sessionid=abc123; Path=/") == "sessionid"

    def test_no_equals(self):
        assert _cookie_name("nocookie") == "nocookie"

    def test_with_spaces(self):
        assert _cookie_name("  csrftoken=xyz  ") == "csrftoken"


# ---------------------------------------------------------------------------
# Security header checks
# ---------------------------------------------------------------------------


class TestSecurityHeaderChecks:
    def test_all_clean(self):
        """A response with all recommended headers produces no findings."""
        result = _make_result(
            headers={
                "Strict-Transport-Security": "max-age=31536000; includeSubDomains; preload",
                "Content-Security-Policy": "default-src 'self'",
                "X-Frame-Options": "DENY",
                "X-Content-Type-Options": "nosniff",
                "Referrer-Policy": "same-origin",
                "Permissions-Policy": "camera=(), microphone=()",
                "Cross-Origin-Opener-Policy": "same-origin",
                "Cross-Origin-Resource-Policy": "same-site",
            }
        )
        findings = check_security_headers(result)
        assert findings == []

    def test_csp_missing(self):
        result = _make_result(
            headers={
                "Strict-Transport-Security": "max-age=31536000; includeSubDomains; preload",
                "X-Frame-Options": "DENY",
                "X-Content-Type-Options": "nosniff",
                "Referrer-Policy": "same-origin",
                "Permissions-Policy": "camera=()",
                "Cross-Origin-Opener-Policy": "same-origin",
                "Cross-Origin-Resource-Policy": "same-site",
            }
        )
        assert "L001" in _ids(check_security_headers(result))

    def test_csp_unsafe_inline(self):
        result = _make_result(
            headers={
                "Content-Security-Policy": "script-src 'self' 'unsafe-inline'",
                "Strict-Transport-Security": "max-age=31536000; includeSubDomains; preload",
                "X-Frame-Options": "DENY",
                "X-Content-Type-Options": "nosniff",
                "Referrer-Policy": "same-origin",
                "Permissions-Policy": "camera=()",
                "Cross-Origin-Opener-Policy": "same-origin",
                "Cross-Origin-Resource-Policy": "same-site",
            }
        )
        assert "L002" in _ids(check_security_headers(result))

    def test_csp_unsafe_eval(self):
        result = _make_result(
            headers={
                "Content-Security-Policy": "script-src 'self' 'unsafe-eval'",
            }
        )
        assert "L003" in _ids(check_security_headers(result))

    def test_hsts_missing(self):
        result = _make_result(headers={})
        assert "L004" in _ids(check_security_headers(result))

    def test_hsts_short_max_age(self):
        result = _make_result(
            headers={"Strict-Transport-Security": "max-age=900; includeSubDomains; preload"}
        )
        assert "L005" in _ids(check_security_headers(result))

    def test_hsts_missing_include_subdomains(self):
        result = _make_result(headers={"Strict-Transport-Security": "max-age=31536000; preload"})
        assert "L006" in _ids(check_security_headers(result))

    def test_hsts_missing_preload(self):
        result = _make_result(
            headers={"Strict-Transport-Security": "max-age=31536000; includeSubDomains"}
        )
        assert "L007" in _ids(check_security_headers(result))

    def test_hsts_not_required_on_http(self):
        """HTTP URLs shouldn't trigger HSTS findings."""
        result = _make_result(url="http://example.com/", headers={})
        codes = _ids(check_security_headers(result))
        assert "L004" not in codes
        assert "L005" not in codes

    def test_x_frame_options_missing(self):
        result = _make_result(headers={})
        assert "L008" in _ids(check_security_headers(result))

    def test_x_content_type_options_missing(self):
        result = _make_result(headers={})
        assert "L009" in _ids(check_security_headers(result))

    def test_x_content_type_options_wrong_value(self):
        result = _make_result(headers={"X-Content-Type-Options": "something-else"})
        assert "L009" in _ids(check_security_headers(result))

    def test_referrer_policy_missing(self):
        result = _make_result(headers={})
        assert "L010" in _ids(check_security_headers(result))

    def test_permissions_policy_missing(self):
        result = _make_result(headers={})
        assert "L011" in _ids(check_security_headers(result))

    def test_coop_missing(self):
        result = _make_result(headers={})
        assert "L012" in _ids(check_security_headers(result))

    def test_corp_missing(self):
        result = _make_result(headers={})
        assert "L013" in _ids(check_security_headers(result))

    def test_server_version_leak(self):
        result = _make_result(headers={"Server": "nginx/1.25.3"})
        assert "L014" in _ids(check_security_headers(result))

    def test_server_no_version_no_finding(self):
        result = _make_result(headers={"Server": "nginx"})
        assert "L014" not in _ids(check_security_headers(result))

    def test_powered_by_present(self):
        result = _make_result(headers={"X-Powered-By": "Django 5.1"})
        assert "L015" in _ids(check_security_headers(result))


# ---------------------------------------------------------------------------
# Cookie checks
# ---------------------------------------------------------------------------


class TestCookieChecks:
    def test_clean_cookies(self):
        result = _make_result(
            cookies=[
                "sessionid=abc; Path=/; HttpOnly; Secure; SameSite=Lax",
                "csrftoken=xyz; Path=/; HttpOnly; Secure; SameSite=Lax",
            ]
        )
        assert check_cookies(result) == []

    def test_session_missing_httponly(self):
        result = _make_result(cookies=["sessionid=abc; Path=/; Secure; SameSite=Lax"])
        assert "L020" in _ids(check_cookies(result))

    def test_session_missing_secure_on_https(self):
        result = _make_result(cookies=["sessionid=abc; Path=/; HttpOnly; SameSite=Lax"])
        assert "L021" in _ids(check_cookies(result))

    def test_session_missing_secure_ok_on_http(self):
        """HTTP URLs shouldn't require Secure — it would be ineffective."""
        result = _make_result(
            url="http://example.com/",
            cookies=["sessionid=abc; Path=/; HttpOnly; SameSite=Lax"],
        )
        assert "L021" not in _ids(check_cookies(result))

    def test_session_missing_samesite(self):
        result = _make_result(cookies=["sessionid=abc; Path=/; HttpOnly; Secure"])
        assert "L022" in _ids(check_cookies(result))

    def test_csrf_missing_httponly(self):
        result = _make_result(cookies=["csrftoken=xyz; Path=/; Secure; SameSite=Lax"])
        assert "L023" in _ids(check_cookies(result))

    def test_csrf_missing_secure_on_https(self):
        result = _make_result(cookies=["csrftoken=xyz; Path=/; HttpOnly; SameSite=Lax"])
        assert "L024" in _ids(check_cookies(result))

    def test_non_session_cookie_ignored(self):
        """Cookies we don't recognize should not produce findings."""
        result = _make_result(cookies=["tracking_id=xyz; Path=/"])
        assert check_cookies(result) == []


# ---------------------------------------------------------------------------
# End-to-end via a local HTTP server
# ---------------------------------------------------------------------------


class _MockHandler(BaseHTTPRequestHandler):
    """Serves canned responses based on path. Configured via class attributes."""

    # Set these by the test before starting the server
    response_headers: Dict[str, str] = {}
    response_cookies: List[str] = []
    status_by_path: Dict[str, int] = {}

    def log_message(self, format, *args):
        pass  # silence

    def do_GET(self):
        status = self.status_by_path.get(self.path, 200)
        self.send_response(status)
        for k, v in self.response_headers.items():
            self.send_header(k, v)
        for c in self.response_cookies:
            self.send_header("Set-Cookie", c)
        self.send_header("Content-Length", "2")
        self.end_headers()
        self.wfile.write(b"OK")


@contextlib.contextmanager
def _mock_server(headers=None, cookies=None, status_by_path=None):
    """Start a threaded HTTPServer on localhost:0 and yield its base URL."""
    handler = _MockHandler
    handler.response_headers = headers or {}
    handler.response_cookies = cookies or []
    handler.status_by_path = status_by_path or {}

    server = HTTPServer(("127.0.0.1", 0), handler)
    port = server.server_address[1]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{port}"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


class TestFetch:
    def test_fetch_200(self):
        with _mock_server(headers={"X-Test": "value"}) as base:
            result = fetch(base + "/")
        assert result.status == 200
        assert result.header("x-test") == "value"

    def test_fetch_404_still_returns_result(self):
        with _mock_server(status_by_path={"/missing": 404}) as base:
            result = fetch(base + "/missing")
        assert result.status == 404

    def test_fetch_extra_headers(self):
        """Custom headers are passed through."""
        with _mock_server() as base:
            result = fetch(base + "/", extra_headers={"X-Custom": "hi"})
        # The mock handler doesn't echo request headers, so we just check
        # no exception was raised.
        assert result.status == 200


class TestRunLiveAuditE2E:
    """End-to-end run against a real (local) HTTP server."""

    def test_finds_missing_headers(self):
        """A server with no security headers produces header findings."""
        with _mock_server() as base:
            report = run_live_audit(
                base + "/",
                probe_websocket=False,
                skip_path_probes=True,
            )
        codes = _ids(report.findings)
        # Should flag several missing headers even though HTTP (no HSTS findings)
        assert "L001" in codes  # CSP missing
        assert "L008" in codes  # X-Frame-Options missing
        assert "L009" in codes  # X-Content-Type-Options missing
        assert "L010" in codes  # Referrer-Policy missing
        assert "L011" in codes  # Permissions-Policy missing

    def test_clean_http_server_passes_header_checks(self):
        """A server with all recommended headers passes all header checks."""
        with _mock_server(
            headers={
                "Content-Security-Policy": "default-src 'self'",
                "X-Frame-Options": "DENY",
                "X-Content-Type-Options": "nosniff",
                "Referrer-Policy": "same-origin",
                "Permissions-Policy": "camera=()",
                "Cross-Origin-Opener-Policy": "same-origin",
                "Cross-Origin-Resource-Policy": "same-site",
            },
        ) as base:
            report = run_live_audit(
                base + "/",
                probe_websocket=False,
                skip_path_probes=True,
            )
        # No error-level findings
        assert report.errors == 0

    def test_unreachable_target(self):
        """An unreachable URL produces an L090 finding."""
        # Use a port that should never accept connections
        report = run_live_audit(
            "http://127.0.0.1:1/",
            probe_websocket=False,
            skip_path_probes=True,
        )
        assert "L090" in _ids(report.findings)

    def test_path_probes_flag_exposed_env(self):
        """A server that returns 200 for /.env triggers L041."""
        with _mock_server(
            headers={"X-Content-Type-Options": "nosniff"},
            status_by_path={"/.env": 200, "/.git/config": 404, "/__debug__/": 404},
        ) as base:
            report = run_live_audit(
                base + "/",
                probe_websocket=False,
                skip_path_probes=False,
            )
        assert "L041" in _ids(report.findings)

    def test_websocket_probe_skip_finding(self):
        """--no-websocket-probe records an L061 info finding."""
        with _mock_server() as base:
            report = run_live_audit(
                base + "/",
                probe_websocket=False,
                skip_path_probes=True,
            )
        assert "L061" in _ids(report.findings)

    def test_report_summary_counts(self):
        with _mock_server() as base:
            report = run_live_audit(
                base + "/",
                probe_websocket=False,
                skip_path_probes=True,
            )
        d = report.to_dict()
        assert "summary" in d
        assert d["summary"]["errors"] + d["summary"]["warnings"] + d["summary"]["infos"] == len(
            report.findings
        )


# ---------------------------------------------------------------------------
# Management-command integration
# ---------------------------------------------------------------------------


class TestDjustAuditLiveCLI:
    """Thin sanity checks of the --live flag wiring via call_command."""

    def test_invalid_header_format_fails(self):
        """--header 'badformat' (no colon) exits with code 2."""
        import io

        import pytest
        from django.core.management import call_command

        with _mock_server() as base:
            out = io.StringIO()
            err = io.StringIO()
            with pytest.raises(SystemExit) as exc_info:
                call_command(
                    "djust_audit",
                    "--live",
                    base + "/",
                    "--header",
                    "badformat",
                    stdout=out,
                    stderr=err,
                )
            assert exc_info.value.code == 2
            assert "Name: Value" in err.getvalue()

    def test_live_json_output_parses(self):
        """--live --json produces valid parseable JSON."""
        import io

        from django.core.management import call_command

        with _mock_server(headers={"Content-Security-Policy": "default-src 'self'"}) as base:
            out = io.StringIO()
            try:
                call_command(
                    "djust_audit",
                    "--live",
                    base + "/",
                    "--json",
                    "--no-websocket-probe",
                    "--skip-path-probes",
                    stdout=out,
                )
            except SystemExit:
                pass  # Exit 1 is fine — we still get the JSON output before it
        parsed = json_module.loads(out.getvalue())
        assert "target" in parsed
        assert "findings" in parsed
        assert "summary" in parsed
