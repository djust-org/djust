"""
Runtime security-header and path probe for ``djust_audit --live`` (#661).

The static `djust_audit` / `djust_check` pipeline cannot see a class of
production security issues that only appear at runtime: security headers
correctly configured in ``settings.py`` but stripped by an nginx ingress,
a CloudFront behavior, a service-mesh sidecar, or a response-rewriting
middleware running after ``CSPMiddleware``. The NYC Claims pentest
(2026-04-10) caught a critical case where ``django-csp`` was fully
configured but the ``Content-Security-Policy`` header was absent from
production responses.

This module implements the runtime probe: fetch a URL with ``urllib``
(stdlib — no new dependency), inspect the headers, cookies, and a few
information-disclosure paths, and optionally open a WebSocket handshake
with a hostile ``Origin`` header to verify CSWSH defense end-to-end.

Findings use stable error codes ``djust.L001``–``djust.L099`` so CI
configs can suppress specific checks by number, not by brittle message
matching.

See issue #661.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from http.client import HTTPResponse
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urljoin, urlparse

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Findings
# ---------------------------------------------------------------------------


LIVE_FINDING_CODES = {
    # ---- headers ----
    "L001": ("error", "Content-Security-Policy header missing"),
    "L002": ("warning", "CSP contains 'unsafe-inline'"),
    "L003": ("warning", "CSP contains 'unsafe-eval'"),
    "L004": ("error", "Strict-Transport-Security header missing"),
    "L005": ("warning", "HSTS max-age below 1 year (31536000)"),
    "L006": ("info", "HSTS missing 'includeSubDomains'"),
    "L007": ("info", "HSTS missing 'preload'"),
    "L008": ("error", "X-Frame-Options header missing"),
    "L009": ("error", "X-Content-Type-Options header missing"),
    "L010": ("warning", "Referrer-Policy header missing"),
    "L011": ("warning", "Permissions-Policy header missing"),
    "L012": ("info", "Cross-Origin-Opener-Policy header missing"),
    "L013": ("info", "Cross-Origin-Resource-Policy header missing"),
    "L014": ("warning", "Server header leaks version information"),
    "L015": ("warning", "X-Powered-By header present"),
    # ---- cookies ----
    "L020": ("error", "Session cookie missing HttpOnly"),
    "L021": ("error", "Session cookie missing Secure (on HTTPS URL)"),
    "L022": ("warning", "Session cookie missing SameSite"),
    "L023": ("error", "CSRF cookie missing HttpOnly"),
    "L024": ("error", "CSRF cookie missing Secure (on HTTPS URL)"),
    # ---- path probes ----
    "L040": ("error", "/.git/config is publicly accessible"),
    "L041": ("error", "/.env is publicly accessible"),
    "L042": ("error", "Django debug toolbar / __debug__ is publicly accessible"),
    "L043": ("info", "/robots.txt not present"),
    "L044": ("info", "/.well-known/security.txt not present (RFC 9116)"),
    # ---- websocket probe ----
    "L060": ("error", "WebSocket accepts cross-origin handshake (CSWSH)"),
    "L061": ("info", "WebSocket probe skipped (--no-websocket-probe)"),
    "L062": ("info", "WebSocket probe skipped (websockets package not installed)"),
    # ---- connectivity ----
    "L090": ("error", "Target URL unreachable"),
    "L091": ("error", "Target URL returned non-2xx status"),
}


@dataclass
class LiveFinding:
    """A single runtime probe finding."""

    code: str
    severity: str  # "error" / "warning" / "info"
    message: str
    url: Optional[str] = None
    details: Optional[str] = None

    @classmethod
    def make(
        cls,
        code: str,
        message: str,
        url: Optional[str] = None,
        details: Optional[str] = None,
    ) -> "LiveFinding":
        severity = LIVE_FINDING_CODES.get(code, ("error", ""))[0]
        return cls(code=code, severity=severity, message=message, url=url, details=details)

    def format_line(self) -> str:
        prefix = {"error": "ERROR", "warning": "WARN", "info": "INFO"}.get(
            self.severity, self.severity.upper()
        )
        url_part = f" [{self.url}]" if self.url else ""
        line = f"{prefix} [djust.{self.code}]{url_part} {self.message}"
        if self.details:
            line += f" ({self.details})"
        return line

    def to_dict(self) -> Dict[str, Any]:
        return {
            "code": self.code,
            "severity": self.severity,
            "message": self.message,
            "url": self.url,
            "details": self.details,
        }


# ---------------------------------------------------------------------------
# HTTP fetching (stdlib — no new dependency)
# ---------------------------------------------------------------------------


@dataclass
class FetchResult:
    """Result of a single HTTP fetch — headers, cookies, status, URL."""

    url: str
    status: int
    headers: Dict[str, str]  # case-insensitive via lowercased keys
    cookies: List[str]  # raw Set-Cookie lines
    body_preview: str = ""

    def header(self, name: str) -> Optional[str]:
        return self.headers.get(name.lower())


def fetch(
    url: str,
    extra_headers: Optional[Dict[str, str]] = None,
    timeout: float = 10.0,
    method: str = "GET",
) -> FetchResult:
    """Fetch a URL using stdlib urllib. Returns headers + status.

    Only ``http://`` and ``https://`` schemes are permitted. Other schemes
    (``file://``, ``ftp://``, ``jar:``, etc.) are rejected with ``ValueError``
    to avoid a security tool inadvertently exfiltrating local files or
    following hostile redirects to non-HTTP schemes.

    Raises:
        ValueError: if the URL scheme is not http or https.
        urllib.error.URLError: on connection failure / DNS failure. Caller
            should convert these to L090 findings.
    """
    import urllib.request

    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        raise ValueError(f"fetch() only supports http:// and https:// URLs, got {parsed.scheme!r}")

    req = urllib.request.Request(url, method=method)
    req.add_header("User-Agent", "djust-audit-live/1.0")
    if extra_headers:
        for k, v in extra_headers.items():
            req.add_header(k, v)

    try:
        # Scheme validated above to http/https only — safe to urlopen.
        resp: HTTPResponse = urllib.request.urlopen(req, timeout=timeout)  # nosec B310
        status = resp.status
        raw_headers = resp.getheaders()
        body = resp.read(2048).decode("utf-8", errors="replace")
    except urllib.error.HTTPError as e:
        # HTTP errors (4xx, 5xx) still produce a response we can inspect.
        status = e.code
        raw_headers = list(e.headers.items())
        body = ""

    headers: Dict[str, str] = {}
    cookies: List[str] = []
    for k, v in raw_headers:
        lk = k.lower()
        if lk == "set-cookie":
            cookies.append(v)
        else:
            # Collapse duplicate headers into comma-joined values
            headers[lk] = f"{headers[lk]}, {v}" if lk in headers else v

    return FetchResult(
        url=url,
        status=status,
        headers=headers,
        cookies=cookies,
        body_preview=body,
    )


# ---------------------------------------------------------------------------
# Header checks
# ---------------------------------------------------------------------------


def check_security_headers(result: FetchResult) -> List[LiveFinding]:
    """Run all header checks on a fetched response."""
    findings: List[LiveFinding] = []
    is_https = result.url.startswith("https://")

    # L001/L002/L003 — Content-Security-Policy
    csp = result.header("content-security-policy")
    if not csp:
        findings.append(
            LiveFinding.make(
                "L001",
                "Content-Security-Policy header missing",
                url=result.url,
                details=(
                    "Common causes: CSPMiddleware not in MIDDLEWARE; an ingress/proxy "
                    "is stripping the header; a response middleware runs after "
                    "CSPMiddleware and removes it. Verify with "
                    "`curl -sI localhost:8000/` in dev."
                ),
            )
        )
    else:
        if "'unsafe-inline'" in csp:
            findings.append(
                LiveFinding.make(
                    "L002",
                    "CSP contains 'unsafe-inline' — negates most XSS defense",
                    url=result.url,
                    details="See djust-org/djust#655 for nonce-based CSP support.",
                )
            )
        if "'unsafe-eval'" in csp:
            findings.append(
                LiveFinding.make(
                    "L003",
                    "CSP contains 'unsafe-eval' — allows dynamic code execution",
                    url=result.url,
                )
            )

    # L004-L007 — Strict-Transport-Security
    hsts = result.header("strict-transport-security")
    if is_https:
        if not hsts:
            findings.append(
                LiveFinding.make("L004", "Strict-Transport-Security header missing", url=result.url)
            )
        else:
            max_age = _extract_max_age(hsts)
            if max_age is not None and max_age < 31_536_000:
                findings.append(
                    LiveFinding.make(
                        "L005",
                        f"HSTS max-age is {max_age} seconds (~{max_age // 86400} days) — "
                        f"below 1 year (31536000)",
                        url=result.url,
                    )
                )
            if "includesubdomains" not in hsts.lower():
                findings.append(
                    LiveFinding.make("L006", "HSTS missing 'includeSubDomains'", url=result.url)
                )
            if "preload" not in hsts.lower():
                findings.append(
                    LiveFinding.make("L007", "HSTS missing 'preload' directive", url=result.url)
                )

    # L008 — X-Frame-Options
    xfo = result.header("x-frame-options")
    if not xfo:
        findings.append(LiveFinding.make("L008", "X-Frame-Options header missing", url=result.url))

    # L009 — X-Content-Type-Options
    xcto = result.header("x-content-type-options")
    if not xcto or xcto.lower().strip() != "nosniff":
        findings.append(
            LiveFinding.make(
                "L009",
                "X-Content-Type-Options header missing or not set to 'nosniff'",
                url=result.url,
            )
        )

    # L010 — Referrer-Policy
    if not result.header("referrer-policy"):
        findings.append(LiveFinding.make("L010", "Referrer-Policy header missing", url=result.url))

    # L011 — Permissions-Policy
    if not result.header("permissions-policy"):
        findings.append(
            LiveFinding.make(
                "L011",
                "Permissions-Policy header missing",
                url=result.url,
                details="Recommend: 'camera=(), microphone=(), geolocation=(), payment=()'",
            )
        )

    # L012 — Cross-Origin-Opener-Policy
    if not result.header("cross-origin-opener-policy"):
        findings.append(
            LiveFinding.make("L012", "Cross-Origin-Opener-Policy header missing", url=result.url)
        )

    # L013 — Cross-Origin-Resource-Policy
    if not result.header("cross-origin-resource-policy"):
        findings.append(
            LiveFinding.make("L013", "Cross-Origin-Resource-Policy header missing", url=result.url)
        )

    # L014 — Server header version leak
    server = result.header("server")
    if server and _looks_like_version_leak(server):
        findings.append(
            LiveFinding.make(
                "L014",
                f"Server header leaks version: {server!r}",
                url=result.url,
                details="Set `server_tokens off` in nginx or equivalent in your reverse proxy.",
            )
        )

    # L015 — X-Powered-By present
    powered_by = result.header("x-powered-by")
    if powered_by:
        findings.append(
            LiveFinding.make(
                "L015",
                f"X-Powered-By header present: {powered_by!r}",
                url=result.url,
            )
        )

    return findings


def _extract_max_age(hsts: str) -> Optional[int]:
    """Extract the max-age value from an HSTS header."""
    match = re.search(r"max-age\s*=\s*(\d+)", hsts, re.IGNORECASE)
    if match:
        try:
            return int(match.group(1))
        except ValueError:
            return None
    return None


_VERSION_LEAK_PATTERNS = [
    r"\bnginx/\d",
    r"\bapache/\d",
    r"\bpython/\d",
    r"\bwerkzeug/\d",
    r"\bgunicorn/\d",
    r"\buvicorn/\d",
    r"\bdaphne/\d",
]


def _looks_like_version_leak(server: str) -> bool:
    """True if the Server header contains something that looks like a version."""
    for pat in _VERSION_LEAK_PATTERNS:
        if re.search(pat, server, re.IGNORECASE):
            return True
    return False


# ---------------------------------------------------------------------------
# Cookie checks
# ---------------------------------------------------------------------------


SESSION_COOKIE_NAMES = ("sessionid", "session", "sid")
CSRF_COOKIE_NAMES = ("csrftoken", "csrf", "xsrf-token")


def check_cookies(result: FetchResult) -> List[LiveFinding]:
    """Validate HttpOnly / Secure / SameSite on session and CSRF cookies."""
    findings: List[LiveFinding] = []
    is_https = result.url.startswith("https://")

    for cookie_line in result.cookies:
        name = _cookie_name(cookie_line).lower()
        attrs = cookie_line.lower()

        if name in SESSION_COOKIE_NAMES:
            if "httponly" not in attrs:
                findings.append(
                    LiveFinding.make(
                        "L020",
                        f"Session cookie {name!r} missing HttpOnly",
                        url=result.url,
                    )
                )
            if is_https and "secure" not in attrs:
                findings.append(
                    LiveFinding.make(
                        "L021",
                        f"Session cookie {name!r} missing Secure (HTTPS response)",
                        url=result.url,
                    )
                )
            if "samesite" not in attrs:
                findings.append(
                    LiveFinding.make(
                        "L022",
                        f"Session cookie {name!r} missing SameSite",
                        url=result.url,
                    )
                )

        if name in CSRF_COOKIE_NAMES:
            if "httponly" not in attrs:
                findings.append(
                    LiveFinding.make(
                        "L023",
                        f"CSRF cookie {name!r} missing HttpOnly",
                        url=result.url,
                    )
                )
            if is_https and "secure" not in attrs:
                findings.append(
                    LiveFinding.make(
                        "L024",
                        f"CSRF cookie {name!r} missing Secure (HTTPS response)",
                        url=result.url,
                    )
                )

    return findings


def _cookie_name(cookie_line: str) -> str:
    """Extract the name= portion of a Set-Cookie header."""
    eq = cookie_line.find("=")
    if eq == -1:
        return cookie_line.strip()
    return cookie_line[:eq].strip()


# ---------------------------------------------------------------------------
# Path probes
# ---------------------------------------------------------------------------


PATH_PROBES: List[Tuple[str, str, str]] = [
    # (path, code, severity — filled in from LIVE_FINDING_CODES)
    ("/.git/config", "L040", "error"),
    ("/.env", "L041", "error"),
    ("/__debug__/", "L042", "error"),
]

INFO_PROBES: List[Tuple[str, str]] = [
    ("/robots.txt", "L043"),
    ("/.well-known/security.txt", "L044"),
]


def probe_paths(
    base_url: str,
    extra_headers: Optional[Dict[str, str]] = None,
    timeout: float = 10.0,
) -> List[LiveFinding]:
    """Check common information-disclosure paths.

    ``base_url`` is expected to be a scheme://host[:port] (the path is
    replaced for each probe).
    """
    findings: List[LiveFinding] = []
    parsed = urlparse(base_url)
    root = f"{parsed.scheme}://{parsed.netloc}"

    for path, code, _ in PATH_PROBES:
        url = urljoin(root, path)
        try:
            r = fetch(url, extra_headers=extra_headers, timeout=timeout)
        except Exception as exc:
            logger.debug("path probe failed for %s: %s", url, exc)
            continue
        if 200 <= r.status < 300:
            findings.append(
                LiveFinding.make(
                    code,
                    f"{path} returned {r.status}",
                    url=url,
                    details="This file should not be publicly accessible.",
                )
            )

    for path, code in INFO_PROBES:
        url = urljoin(root, path)
        try:
            r = fetch(url, extra_headers=extra_headers, timeout=timeout)
        except Exception as exc:
            logger.debug("path probe failed for %s: %s", url, exc)
            continue
        if r.status == 404:
            findings.append(LiveFinding.make(code, f"{path} not present", url=url))

    return findings


# ---------------------------------------------------------------------------
# WebSocket CSWSH probe
# ---------------------------------------------------------------------------


def probe_websocket_origin(
    base_url: str,
    path: str = "/ws/live/",
    timeout: float = 10.0,
) -> List[LiveFinding]:
    """Attempt to open a WebSocket handshake with a hostile Origin.

    Returns:
        * ``[]`` — the server rejected the handshake (expected / passing case).
        * ``[LiveFinding(L060)]`` — the server accepted the cross-origin handshake
          (CSWSH vulnerability).
        * ``[LiveFinding(L062)]`` — the ``websockets`` package isn't installed,
          so we can't probe. Logged as INFO, not an error — the probe is
          informational when the tooling is missing.
    """
    try:
        import websockets.sync.client as ws_sync_client  # type: ignore
    except ImportError:
        return [
            LiveFinding.make(
                "L062",
                "WebSocket probe skipped: websockets package not installed",
                url=base_url,
                details="Install with `pip install websockets` to enable this check.",
            )
        ]

    parsed = urlparse(base_url)
    scheme = "wss" if parsed.scheme == "https" else "ws"
    ws_url = f"{scheme}://{parsed.netloc}{path}"

    try:
        conn = ws_sync_client.connect(
            ws_url,
            additional_headers={"Origin": "https://evil.example"},
            open_timeout=timeout,
        )
    except Exception as exc:
        # Any exception during handshake means the server rejected us —
        # the expected/passing behavior.
        logger.debug("websocket probe rejected at handshake: %s", exc)
        return []

    # Handshake succeeded — CSWSH vulnerability.
    try:
        conn.close()
    except Exception as exc:
        # Best-effort close; the vulnerability finding is the important part.
        logger.debug("websocket close after CSWSH probe failed: %s", exc)
    return [
        LiveFinding.make(
            "L060",
            "WebSocket accepted cross-origin handshake (CSWSH vulnerability)",
            url=ws_url,
            details=(
                "Expected: HTTP 403 at handshake or close code 4403 from the consumer. "
                "Actual: connection opened with Origin: https://evil.example. "
                "Wrap the WebSocket router in "
                "channels.security.websocket.AllowedHostsOriginValidator "
                "(DjustMiddlewareStack does this by default since djust 0.4.1). "
                "See djust-org/djust#653."
            ),
        )
    ]


# ---------------------------------------------------------------------------
# Orchestrator
# ---------------------------------------------------------------------------


@dataclass
class LiveAuditReport:
    """Aggregated result of all live probes for one target URL."""

    target: str
    findings: List[LiveFinding] = field(default_factory=list)
    pages_fetched: int = 0
    errors: int = 0
    warnings: int = 0
    infos: int = 0

    def add(self, finding: LiveFinding) -> None:
        self.findings.append(finding)
        if finding.severity == "error":
            self.errors += 1
        elif finding.severity == "warning":
            self.warnings += 1
        else:
            self.infos += 1

    def extend(self, findings: List[LiveFinding]) -> None:
        for f in findings:
            self.add(f)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "target": self.target,
            "pages_fetched": self.pages_fetched,
            "findings": [f.to_dict() for f in self.findings],
            "summary": {
                "errors": self.errors,
                "warnings": self.warnings,
                "infos": self.infos,
            },
        }


def run_live_audit(
    target: str,
    paths: Optional[List[str]] = None,
    extra_headers: Optional[Dict[str, str]] = None,
    timeout: float = 10.0,
    probe_websocket: bool = True,
    skip_path_probes: bool = False,
) -> LiveAuditReport:
    """Execute a full live audit against a target URL.

    Args:
        target: Base URL (``https://myapp.example.com``). Scheme is required.
        paths: Additional paths to fetch and inspect beyond the root URL.
            Each path is fetched and its headers/cookies are checked.
        extra_headers: Additional request headers (e.g. basic auth for
            staging environments).
        timeout: Per-request timeout in seconds.
        probe_websocket: When True, attempt the CSWSH handshake probe.
        skip_path_probes: When True, skip the information-disclosure
            path probes (``/.git/config``, ``/.env``, etc.). Useful for
            environments behind a WAF that would 403 everything.

    Returns:
        ``LiveAuditReport`` with all findings and summary counts.
    """
    import urllib.error

    report = LiveAuditReport(target=target)

    urls_to_fetch = [target]
    if paths:
        for p in paths:
            if p.startswith("http://") or p.startswith("https://"):
                urls_to_fetch.append(p)
            else:
                urls_to_fetch.append(urljoin(target, p))

    # Fetch each URL and run header + cookie checks
    for url in urls_to_fetch:
        try:
            result = fetch(url, extra_headers=extra_headers, timeout=timeout)
        except urllib.error.URLError as exc:
            report.add(
                LiveFinding.make(
                    "L090",
                    f"Target URL unreachable: {exc}",
                    url=url,
                )
            )
            continue
        except Exception as exc:
            report.add(
                LiveFinding.make(
                    "L090",
                    f"Unexpected error fetching URL: {exc}",
                    url=url,
                )
            )
            continue

        report.pages_fetched += 1
        if result.status >= 400:
            report.add(
                LiveFinding.make(
                    "L091",
                    f"URL returned HTTP {result.status}",
                    url=url,
                )
            )
        report.extend(check_security_headers(result))
        report.extend(check_cookies(result))

    # Path probes — only on the base target, not the --paths overrides
    if not skip_path_probes:
        report.extend(probe_paths(target, extra_headers=extra_headers, timeout=timeout))

    # WebSocket CSWSH probe
    if probe_websocket:
        report.extend(probe_websocket_origin(target, timeout=timeout))
    else:
        report.add(
            LiveFinding.make(
                "L061",
                "WebSocket probe skipped (--no-websocket-probe)",
                url=target,
            )
        )

    return report
