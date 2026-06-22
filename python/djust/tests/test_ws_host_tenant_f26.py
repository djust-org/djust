"""Finding #26 — WS/runtime reconstructed request omits client Host.

The WebSocket ``handle_mount`` and ``ViewRuntime._build_request`` rebuild an
``HttpRequest`` via ``RequestFactory().get(...)`` with NO ``HTTP_HOST``, so
``request.get_host()`` defaulted to RequestFactory's ``"testserver"`` on the
live (WS) path. Host/subdomain ``TenantResolver``\\ s read ``request.get_host()``
and therefore misresolved the tenant to ``None`` on the live path — while the
HTTP (SSR) path, using the real request, resolved the correct tenant. With
``STRICT_MODE=False`` the tenant-scoped managers then returned UNSCOPED rows
(cross-tenant disclosure); with the default they returned ``.none()`` (broken
tenancy).

The fix (``djust.websocket.validated_host_from_scope``) extracts the handshake
Host from the ASGI scope, validates it against ``settings.ALLOWED_HOSTS`` using
the SAME logic as the CSWSH Origin gate, and propagates it (plus the TLS scheme)
into the reconstructed request — so host/subdomain resolution on the live path
matches HTTP exactly: no weaker, no stronger than the HTTP layer.

These tests assert HTTP↔WS tenant-resolution PARITY, the ALLOWED_HOSTS bound on
spoofing, the no-Host fallback for non-browser clients, and a gate-off proof
(#1468) that the propagation is the load-bearing change.
"""

from __future__ import annotations

import sys
from unittest.mock import patch

import pytest

from django.test import RequestFactory, override_settings

pytestmark = pytest.mark.tenants

from djust import LiveView  # noqa: E402
from djust.runtime import ViewRuntime  # noqa: E402
from djust.tenants.mixin import TenantMixin  # noqa: E402
from djust.tenants.resolvers import SubdomainResolver  # noqa: E402
from djust.websocket import validated_host_from_scope  # noqa: E402

# DJUST_CONFIG used across the suite: subdomain resolver, no main domain so a
# 3-part host like "acme.example.com" yields subdomain "acme".
_SUBDOMAIN_CONFIG = {"TENANT_RESOLVER": "subdomain"}

# ALLOWED_HOSTS that admit the tenant host (.example.com) AND the
# RequestFactory default ("testserver") so the no-Host fallback path still
# builds a usable request.
_ALLOWED = [".example.com", "testserver"]


class _TenantDocView(TenantMixin, LiveView):
    """A host/subdomain-tenanted view; records the resolved tenant id on mount.

    ``tenant_required = False`` so a missing/invalid host resolves to ``None``
    without raising Http404 — the fallback assertions check the resolved value,
    not a crash.
    """

    tenant_required = False
    template = (
        '<div dj-view="djust.tests.test_ws_host_tenant_f26._TenantDocView" dj-id="0">doc</div>'
    )

    def mount(self, request, **kwargs):
        # _ensure_tenant has already run in handle_mount before mount(); record
        # the resolved tenant id (or None) for the test to assert on.
        global _RESOLVED_TENANT_ID
        _RESOLVED_TENANT_ID = self._tenant.id if self._tenant else None
        self.doc = "x"


# Module-global capture written by _TenantDocView.mount.
_RESOLVED_TENANT_ID = "__unset__"

setattr(sys.modules[__name__], "_TenantDocView", _TenantDocView)
_VIEW_PATH = f"{__name__}._TenantDocView"


# --------------------------------------------------------------------------- #
# Axis 1 — HTTP↔WS tenant-resolution parity (the core finding).
# --------------------------------------------------------------------------- #
class TestHttpWsTenantParity:
    """The mounted view resolves the SAME tenant over HTTP and over WS."""

    @override_settings(ALLOWED_HOSTS=_ALLOWED, DJUST_CONFIG=_SUBDOMAIN_CONFIG)
    def test_http_real_request_resolves_acme(self):
        """Baseline: the HTTP (SSR) path resolves tenant 'acme' from the real
        request host. This is the behavior the WS path must match."""
        req = RequestFactory().get("/doc/1/", HTTP_HOST="acme.example.com")
        tenant = SubdomainResolver().resolve(req)
        assert tenant is not None
        assert tenant.id == "acme"

    @pytest.mark.django_db
    @override_settings(
        ALLOWED_HOSTS=_ALLOWED,
        DJUST_CONFIG=_SUBDOMAIN_CONFIG,
        LIVEVIEW_ALLOWED_MODULES=[__name__],
    )
    async def test_ws_mount_resolves_acme_not_none(self):
        """The live (WS) mount with handshake Host 'acme.example.com' resolves
        tenant 'acme' — NOT None. This is the regression the fix closes: before
        propagating the Host, the reconstructed request defaulted to
        'testserver' and the SubdomainResolver returned None."""
        pytest.importorskip("channels")
        from channels.testing import WebsocketCommunicator

        from djust.websocket import LiveViewConsumer

        global _RESOLVED_TENANT_ID
        _RESOLVED_TENANT_ID = "__unset__"

        communicator = WebsocketCommunicator(
            LiveViewConsumer.as_asgi(),
            "/ws/",
            headers=[(b"host", b"acme.example.com")],
        )
        connected, _ = await communicator.connect()
        assert connected
        try:
            await communicator.receive_json_from(timeout=2)
        except Exception:
            pass
        await communicator.send_json_to({"type": "mount", "view": _VIEW_PATH})
        mount_resp = await communicator.receive_json_from(timeout=2)
        assert mount_resp.get("type") != "navigate", f"unexpected redirect: {mount_resp!r}"
        await communicator.disconnect()

        assert _RESOLVED_TENANT_ID == "acme", (
            "WS mount resolved tenant %r; expected 'acme'. The handshake Host "
            "was not propagated into the reconstructed request (the request "
            "defaulted to 'testserver')." % (_RESOLVED_TENANT_ID,)
        )

    @pytest.mark.django_db
    @override_settings(ALLOWED_HOSTS=_ALLOWED, DJUST_CONFIG=_SUBDOMAIN_CONFIG)
    async def test_runtime_build_request_carries_host(self):
        """``ViewRuntime._build_request`` (url_change etc.) carries the same
        validated host from the WS scope, so runtime-rebuilt requests resolve
        the tenant identically to the mount request."""
        scope = {"headers": [(b"host", b"acme.example.com")], "scheme": "wss"}
        runtime = ViewRuntime(_NoopTransport(), scope=scope)
        request = await runtime._build_request(page_url="/doc/1/", params={})

        assert request.get_host() == "acme.example.com"
        assert request.is_secure() is True
        tenant = SubdomainResolver().resolve(request)
        assert tenant is not None and tenant.id == "acme"


# --------------------------------------------------------------------------- #
# Axis 2 — ALLOWED_HOSTS bound: a spoofed Host outside ALLOWED_HOSTS falls back.
# --------------------------------------------------------------------------- #
class TestAllowedHostsBound:
    @override_settings(ALLOWED_HOSTS=_ALLOWED)
    def test_helper_rejects_host_outside_allowed_hosts(self):
        """A Host NOT in ALLOWED_HOSTS yields (None, ...) — fall back, no spoof
        beyond what the HTTP layer would grant."""
        host, _ = validated_host_from_scope({"headers": [(b"host", b"evil.attacker.com")]})
        assert host is None

    @pytest.mark.django_db
    @override_settings(ALLOWED_HOSTS=_ALLOWED, DJUST_CONFIG=_SUBDOMAIN_CONFIG)
    async def test_runtime_build_request_falls_back_on_spoofed_host(self):
        """A reconstructed request built from a spoofed (non-ALLOWED_HOSTS) Host
        does not adopt it — it falls back to the RequestFactory default and so
        does not gain tenant-resolution authority beyond the HTTP layer. Not a
        crash."""
        scope = {"headers": [(b"host", b"evil.attacker.com")]}
        runtime = ViewRuntime(_NoopTransport(), scope=scope)
        request = await runtime._build_request(page_url="/doc/1/", params={})
        # Falls back to RequestFactory's default host, not the spoofed one.
        assert request.get_host() != "evil.attacker.com"
        assert request.get_host() == "testserver"


# --------------------------------------------------------------------------- #
# Axis 3 — no-Host fallback: non-browser clients (no Host header) still mount.
# --------------------------------------------------------------------------- #
class TestNoHostFallback:
    @override_settings(ALLOWED_HOSTS=_ALLOWED)
    def test_helper_returns_none_when_no_host_header(self):
        host, is_secure = validated_host_from_scope({"headers": []})
        assert host is None
        assert is_secure is False

    @override_settings(ALLOWED_HOSTS=_ALLOWED)
    def test_helper_returns_none_for_empty_scope(self):
        assert validated_host_from_scope(None) == (None, False)

    @pytest.mark.django_db
    @override_settings(ALLOWED_HOSTS=_ALLOWED, DJUST_CONFIG=_SUBDOMAIN_CONFIG)
    async def test_runtime_build_request_no_host_still_builds(self):
        """A scope with no Host header (non-browser client / WebsocketCommunicator
        sending no Host) still builds a usable request with the default host —
        current behavior preserved."""
        runtime = ViewRuntime(_NoopTransport(), scope={"headers": []})
        request = await runtime._build_request(page_url="/doc/1/", params={})
        assert request.get_host() == "testserver"


# --------------------------------------------------------------------------- #
# Axis 4 — secure/scheme propagation.
# --------------------------------------------------------------------------- #
class TestSchemePropagation:
    @override_settings(ALLOWED_HOSTS=_ALLOWED)
    def test_wss_scheme_marks_secure(self):
        host, is_secure = validated_host_from_scope(
            {"headers": [(b"host", b"acme.example.com")], "scheme": "wss"}
        )
        assert host == "acme.example.com"
        assert is_secure is True

    @override_settings(ALLOWED_HOSTS=_ALLOWED)
    def test_ws_scheme_not_secure(self):
        _, is_secure = validated_host_from_scope(
            {"headers": [(b"host", b"acme.example.com")], "scheme": "ws"}
        )
        assert is_secure is False


# --------------------------------------------------------------------------- #
# Axis 5 — malformed-Host rejection at the boundary (#F26 review hardening).
#
# ``validated_host_from_scope`` parses the Host with Django's own
# ``split_domain_port`` BEFORE calling ``validate_host`` — exactly as
# ``HttpRequest.get_host()`` does — so it rejects everything ``get_host()``
# rejects. The bug class this closes: ``validate_host`` does NOT format-validate,
# so a userinfo-smuggling or whitespace-padded Host that ``endswith`` a wildcard
# ALLOWED_HOSTS entry would be wrongly accepted if validated directly. These
# Hosts already failed CLOSED downstream (``get_host()`` raises ``DisallowedHost``
# → no tenant granted), but rejecting them at THIS boundary is defense-in-depth:
# the WS path never adopts a Host the HTTP path would 400.
# --------------------------------------------------------------------------- #
class TestMalformedHostRejected:
    # Hosts that endswith ".example.com" (so they slip past validate_host's
    # wildcard match) but are malformed per Django's host_validation_re.
    _MALFORMED = [
        b"evil.com@acme.example.com",  # userinfo smuggle (CWE-confusion)
        b" acme.example.com",  # leading whitespace
        b"acme.example.com ",  # trailing whitespace
        b"acme.example.com/../admin",  # path injected into Host
        b"acme.example.com\x00",  # NUL byte
    ]

    @override_settings(ALLOWED_HOSTS=_ALLOWED)
    @pytest.mark.parametrize("raw", _MALFORMED)
    def test_malformed_host_rejected(self, raw):
        """A malformed Host (rejected by split_domain_port) yields host=None —
        the WS path falls back rather than adopting it."""
        host, _ = validated_host_from_scope({"headers": [(b"host", raw)]})
        assert host is None, f"malformed Host {raw!r} was wrongly accepted as {host!r}"

    def test_userinfo_bypass_is_real_not_tautology(self):
        """Non-tautology proof: ``validate_host`` (no format-validation) WOULD
        accept the userinfo-smuggling Host because it ``endswith`` the wildcard,
        which is precisely why parse-then-validate is required. If this assertion
        ever fails, Django tightened validate_host and the hardening test above
        is no longer exercising a real bypass."""
        from django.http.request import validate_host

        # The naive (pre-hardening) check would accept this:
        assert validate_host("evil.com@acme.example.com", [".example.com"]) is True

    @override_settings(ALLOWED_HOSTS=_ALLOWED)
    def test_valid_host_with_port_still_accepted(self):
        """Positive control: the hardening must NOT reject a legitimate
        host:port — split_domain_port strips the port, validates the domain, and
        the FULL value (incl. port) passes through to HTTP_HOST."""
        host, _ = validated_host_from_scope({"headers": [(b"host", b"acme.example.com:8000")]})
        assert host == "acme.example.com:8000"

    @override_settings(ALLOWED_HOSTS=["[::1]", "testserver"])
    def test_valid_ipv6_literal_with_port_still_accepted(self):
        """Positive control: split_domain_port keeps IPv6 brackets; the bracketed
        domain validates and the full bracketed host:port passes through."""
        host, _ = validated_host_from_scope({"headers": [(b"host", b"[::1]:8000")]})
        assert host == "[::1]:8000"


# --------------------------------------------------------------------------- #
# Axis 6 — gate-off (#1468): reverting to factory.get(path) without HTTP_HOST
# makes the WS-resolves-correct-tenant assertion fail.
# --------------------------------------------------------------------------- #
class TestGateOff:
    @override_settings(ALLOWED_HOSTS=_ALLOWED, DJUST_CONFIG=_SUBDOMAIN_CONFIG)
    async def test_gate_off_helper_returns_no_host_breaks_resolution(self):
        """Gate-off proof: if ``validated_host_from_scope`` were neutered to
        always return (None, False) — i.e. the request is rebuilt WITHOUT
        HTTP_HOST as before the fix — the reconstructed request resolves the
        tenant to None, reproducing the finding. This pins the propagation as
        the load-bearing change (non-tautology)."""
        # _build_request imports the helper lazily from djust.websocket, so
        # patch it at the source module (not djust.runtime).
        with patch("djust.websocket.validated_host_from_scope", return_value=(None, False)):
            scope = {"headers": [(b"host", b"acme.example.com")], "scheme": "wss"}
            runtime = ViewRuntime(_NoopTransport(), scope=scope)
            request = await runtime._build_request(page_url="/doc/1/", params={})
            # Gated off → no HTTP_HOST → "testserver" → tenant None.
            assert request.get_host() == "testserver"
            tenant = SubdomainResolver().resolve(request)
            assert tenant is None, (
                "Gate-off expected tenant None (the pre-fix bug), got %r — the "
                "test does not actually exercise the host propagation." % (tenant,)
            )


# --------------------------------------------------------------------------- #
# Minimal transport stub for ViewRuntime construction.
# --------------------------------------------------------------------------- #
class _NoopTransport:
    session_id = "f26-test"

    @property
    def client_ip(self):
        return None

    async def send(self, msg):
        pass

    async def send_error(self, *a, **k):
        pass

    async def close(self, code: int = 1000):
        pass
