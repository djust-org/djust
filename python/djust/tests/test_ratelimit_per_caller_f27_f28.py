"""Shared per-caller rate-limit bucket across transports (F27 + F28, ADR-008).

F27: the per-handler ``@rate_limit`` decorator was enforced against
INDEPENDENT bucket stores per WebSocket connection (the per-connection
``ConnectionRateLimiter.handler_buckets``) and per transport (WS / SSE /
HTTP API). Opening N connections gave N× the configured limit; a caller
hitting a handler over both WS and the API summed the two allowances.

F28: the HTTP-API caller key used raw ``REMOTE_ADDR`` instead of the
#5-hardened ``resolve_client_ip`` (which honors ``DJUST_TRUSTED_PROXY_COUNT``),
so behind a proxy all unauthenticated callers collapsed to one bucket, and an
XFF→REMOTE_ADDR middleware made the key client-spoofable.

The fix: a SINGLE process-level, LRU-capped per-caller bucket store in
``djust.rate_limit`` keyed ``(caller_key, handler_name)``, used by all three
transports for the per-handler ``@rate_limit``. ``caller_key`` resolves its IP
fallback through ``resolve_client_ip`` (F28). The per-connection
``ConnectionRateLimiter``'s GLOBAL per-message abuse-disconnect (#17) is
explicitly NOT moved — it stays per-connection.

Net invariant: a given caller has ONE ``@rate_limit`` budget per handler,
regardless of connection count or transport.
"""

from __future__ import annotations

import sys
from unittest.mock import AsyncMock, MagicMock

import pytest

from django.contrib.auth.models import AnonymousUser
from django.test import RequestFactory, override_settings

from djust import LiveView
from djust.decorators import event_handler, rate_limit
from djust.rate_limit import (
    ConnectionRateLimiter,
    caller_key,
    handler_rate_check,
    reset_handler_buckets,
)


# --------------------------------------------------------------------------- #
# Fixtures / helpers
# --------------------------------------------------------------------------- #


@pytest.fixture(autouse=True)
def _clear_shared_store():
    """Each test starts with an empty shared per-caller bucket store."""
    reset_handler_buckets()
    yield
    reset_handler_buckets()


class _RateLimitedView(LiveView):
    """A view with a per-handler @rate_limit(rate=5, burst=5) handler."""

    template = (
        '<div dj-view="djust.tests.test_ratelimit_per_caller_f27_f28._RateLimitedView" '
        'dj-id="0">x</div>'
    )

    def mount(self, request, **kwargs):
        self.calls = 0

    @event_handler()
    @rate_limit(rate=5, burst=5)
    def expensive_send(self, **kwargs):
        self.calls += 1


setattr(sys.modules[__name__], "_RateLimitedView", _RateLimitedView)


class _SlowRefillView(LiveView):
    """A view whose @rate_limit refills slowly (burst=5, rate=0.1/s) so that no
    meaningful refill happens within an end-to-end WS test window — keeping the
    accepted-count assertion deterministic regardless of round-trip timing."""

    template = (
        '<div dj-view="djust.tests.test_ratelimit_per_caller_f27_f28._SlowRefillView" '
        'dj-id="0">x</div>'
    )

    def mount(self, request, **kwargs):
        self.calls = 0

    @event_handler()
    @rate_limit(rate=0.1, burst=5)
    def expensive_send(self, **kwargs):
        self.calls += 1


setattr(sys.modules[__name__], "_SlowRefillView", _SlowRefillView)


class _AuthedUser:
    """Minimal authenticated stand-in carrying a stable pk."""

    is_authenticated = True
    is_active = True
    is_anonymous = False

    def __init__(self, pk):
        self.pk = pk


def _make_view(request):
    view = _RateLimitedView()
    view.request = request
    view.mount(request)
    return view


def _mock_ws(client_ip="203.0.113.9"):
    """Minimal mock for the ``ws`` arg of _validate_event_security."""
    ws = MagicMock()
    ws.send_error = AsyncMock()
    ws.send_json = AsyncMock()
    ws.close = AsyncMock()
    ws._client_ip = client_ip
    return ws


def _request_for_user(pk):
    rf = RequestFactory()
    request = rf.get("/")
    request.user = _AuthedUser(pk)
    return request


# --------------------------------------------------------------------------- #
# F27 — multi-connection / multi-context (the per-connection multiplication)
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_f27_two_connections_same_caller_share_one_budget():
    """Two separate connections (own ConnectionRateLimiter each) for the SAME
    caller invoking a @rate_limit(rate=5,burst=5) handler get a COMBINED 5
    allowed calls, NOT 10. This exercises the real ``_validate_event_security``
    chokepoint that every WS event flows through.

    gate-off (#1468): reverting the per-handler @rate_limit to the
    per-connection ``ConnectionRateLimiter.handler_buckets`` makes this allow
    10 (5 per connection) and the assertion fails.
    """
    from djust.websocket_utils import _validate_event_security

    # Same authenticated user (pk=42) on two independent connections.
    view_a = _make_view(_request_for_user(42))
    view_b = _make_view(_request_for_user(42))
    rl_a = ConnectionRateLimiter()  # connection #1's per-connection limiter
    rl_b = ConnectionRateLimiter()  # connection #2's per-connection limiter
    ws_a = _mock_ws()
    ws_b = _mock_ws()

    allowed = 0
    # Interleave 10 calls across the two connections; only 5 should pass.
    for i in range(10):
        if i % 2 == 0:
            handler = await _validate_event_security(ws_a, "expensive_send", view_a, rl_a)
        else:
            handler = await _validate_event_security(ws_b, "expensive_send", view_b, rl_b)
        if handler is not None:
            allowed += 1

    assert allowed == 5, (
        f"combined allowance across two connections for one caller must be 5 "
        f"(per-caller), not 10 (per-connection); got {allowed}"
    )


@pytest.mark.asyncio
async def test_f27_distinct_callers_get_independent_budgets():
    """Two DIFFERENT callers each get their own full budget (the shared store
    is per-caller, not a single global bucket)."""
    from djust.websocket_utils import _validate_event_security

    view_a = _make_view(_request_for_user(1))
    view_b = _make_view(_request_for_user(2))
    rl_a = ConnectionRateLimiter()
    rl_b = ConnectionRateLimiter()
    ws_a = _mock_ws()
    ws_b = _mock_ws()

    allowed_a = 0
    for _ in range(5):
        if (await _validate_event_security(ws_a, "expensive_send", view_a, rl_a)) is not None:
            allowed_a += 1
    allowed_b = 0
    for _ in range(5):
        if (await _validate_event_security(ws_b, "expensive_send", view_b, rl_b)) is not None:
            allowed_b += 1
    assert allowed_a == 5 and allowed_b == 5, (
        f"distinct callers must each get the full burst; got a={allowed_a} b={allowed_b}"
    )


@pytest.mark.django_db
@override_settings(
    LIVEVIEW_ALLOWED_MODULES=[__name__],
    # Raise max_warnings so the GLOBAL per-connection abuse-disconnect (#17)
    # does not close a socket mid-test — this test isolates the per-HANDLER
    # shared budget, not the abuse-disconnect (which has its own test below).
    LIVEVIEW_CONFIG={"rate_limit": {"max_warnings": 1000}},
)
async def test_f27_two_websocket_connections_combined_limit():
    """End-to-end: two real ``WebsocketCommunicator`` connections for the same
    scope user invoking a @rate_limit(burst=5) handler get a combined 5
    allowed; the 6th (across both sockets) is rate-limited.

    This is the highest-fidelity reproduction of F27 — N WS connections used to
    yield N× the configured limit.
    """
    from djust.config import config as djust_config

    djust_config.reset()
    pytest.importorskip("channels")
    from channels.testing import WebsocketCommunicator

    from djust.websocket import LiveViewConsumer

    # Use the slow-refill view so token refill over the WS round-trip window
    # cannot grant an extra call — the accepted count is deterministic at burst.
    view_path = f"{__name__}._SlowRefillView"

    def _scope_user_mw(app, user):
        async def mw(scope, receive, send):
            scope = dict(scope)
            scope["user"] = user
            scope.setdefault("session", {})
            return await app(scope, receive, send)

        return mw

    shared_user = _AuthedUser(pk=777)

    async def _connect():
        app = _scope_user_mw(LiveViewConsumer.as_asgi(), shared_user)
        comm = WebsocketCommunicator(app, "/ws/")
        connected, _ = await comm.connect()
        assert connected
        try:
            await comm.receive_json_from(timeout=2)  # connect ack
        except Exception:
            pass
        await comm.send_json_to({"type": "mount", "view": view_path})
        mount_resp = await comm.receive_json_from(timeout=2)
        assert mount_resp.get("type") != "navigate", f"unexpected redirect: {mount_resp!r}"
        return comm

    comm_a = await _connect()
    comm_b = await _connect()

    async def _fire_and_classify(comm):
        """Send one event; return True if accepted, False if rate-limited."""
        await comm.send_json_to({"type": "event", "event": "expensive_send", "params": {}})
        limited = False
        # Drain a few frames looking for an error / rate-limit signal.
        for _ in range(4):
            try:
                out = await comm.receive_json_from(timeout=1)
            except Exception:
                break
            t = out.get("type")
            if t in ("error", "rate_limit_exceeded"):
                limited = True
                break
            if t in ("update", "patch", "html_update", "noop"):
                break
        return not limited

    try:
        accepted = 0
        # Interleave 10 events across the two sockets; only 5 should be accepted.
        for i in range(10):
            comm = comm_a if i % 2 == 0 else comm_b
            if await _fire_and_classify(comm):
                accepted += 1
        assert accepted == 5, (
            f"two WS connections for one caller must share a 5-call budget "
            f"(F27); got {accepted} accepted"
        )
    finally:
        await comm_a.disconnect()
        await comm_b.disconnect()
        djust_config.reset()


# --------------------------------------------------------------------------- #
# F27 — cross-transport: WS chokepoint and HTTP API share one budget
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_f27_cross_transport_ws_and_api_share_budget():
    """The same caller hitting the same handler over the WS path and the HTTP
    API path consumes from ONE shared budget — not two independent ones.

    gate-off (#1468): if the API kept its own ``api/dispatch._rate_buckets``
    dict (pre-fix), the API calls below would all succeed independently and the
    combined count would be 10 instead of 5.
    """
    from djust.websocket_utils import _validate_event_security
    from djust.api.dispatch import _rate_limit_check

    # WS side: authenticated user pk=99.
    ws_view = _make_view(_request_for_user(99))
    rl = ConnectionRateLimiter()
    ws = _mock_ws()

    # API side: same authenticated user pk=99 → same caller_key "user:99".
    rf = RequestFactory()
    api_request = rf.post("/djust/api/x/expensive_send/")
    api_request.user = _AuthedUser(99)

    accepted = 0
    # 3 over WS, then 3 over API — combined budget is 5.
    for _ in range(3):
        if (await _validate_event_security(ws, "expensive_send", ws_view, rl)) is not None:
            accepted += 1
    for _ in range(3):
        if _rate_limit_check(api_request, "expensive_send", _RateLimitedView.expensive_send):
            accepted += 1

    assert accepted == 5, (
        f"same caller across WS + API must share one 5-call budget for the "
        f"handler (F27); got {accepted}"
    )


def test_f27_shared_store_caller_key_keys_buckets():
    """Two calls with the SAME caller_key share a bucket; a different
    caller_key gets its own. Direct test of the shared store contract."""
    settings = {"rate": 5, "burst": 5}
    key_a = "user:5"
    key_b = "user:6"

    allowed_a = sum(handler_rate_check(key_a, "h", settings) for _ in range(8))
    allowed_b = sum(handler_rate_check(key_b, "h", settings) for _ in range(8))
    assert allowed_a == 5, f"caller A burst should cap at 5; got {allowed_a}"
    assert allowed_b == 5, f"caller B (distinct key) gets its own 5; got {allowed_b}"

    # No-settings handlers are never limited.
    assert all(handler_rate_check(key_a, "free", None) for _ in range(20))


# --------------------------------------------------------------------------- #
# F28 — API caller key resolves IP via resolve_client_ip (trusted-proxy aware)
# --------------------------------------------------------------------------- #


@override_settings(DJUST_TRUSTED_PROXY_COUNT=1)
def test_f28_trusted_proxy_distinguishes_real_clients():
    """With one trusted proxy, two requests sharing the proxy peer (REMOTE_ADDR)
    but different XFF client IPs get DIFFERENT caller keys (per real client).

    gate-off (#1468): reverting _caller_key to raw ``REMOTE_ADDR`` makes both
    keys identical (the proxy IP) and this assertion fails.
    """
    from djust.api.dispatch import _caller_key

    # Standard 1-proxy topology: the proxy sets XFF to the real client IP and
    # forwards; REMOTE_ADDR is the proxy. With count=1, resolve_client_ip peels
    # the single trusted hop from the right → the real client.
    rf = RequestFactory()
    req1 = rf.get("/", HTTP_X_FORWARDED_FOR="1.1.1.1", REMOTE_ADDR="10.0.0.1")
    req2 = rf.get("/", HTTP_X_FORWARDED_FOR="2.2.2.2", REMOTE_ADDR="10.0.0.1")
    req1.user = AnonymousUser()
    req2.user = AnonymousUser()

    key1 = _caller_key(req1)
    key2 = _caller_key(req2)
    assert key1 == "ip:1.1.1.1", f"trusted-proxy peel should yield real client; got {key1}"
    assert key2 == "ip:2.2.2.2", f"trusted-proxy peel should yield real client; got {key2}"
    assert key1 != key2, "distinct real clients behind a trusted proxy must not share a bucket"


def test_f28_no_trusted_proxy_ignores_xff():
    """With DJUST_TRUSTED_PROXY_COUNT unset (0), XFF is ignored and both
    requests key by the socket peer (REMOTE_ADDR) — so a spoofed XFF can't
    cycle the bucket key."""
    from djust.api.dispatch import _caller_key

    rf = RequestFactory()
    req1 = rf.get("/", HTTP_X_FORWARDED_FOR="6.6.6.6", REMOTE_ADDR="10.0.0.1")
    req2 = rf.get("/", HTTP_X_FORWARDED_FOR="7.7.7.7", REMOTE_ADDR="10.0.0.1")
    req1.user = AnonymousUser()
    req2.user = AnonymousUser()

    key1 = _caller_key(req1)
    key2 = _caller_key(req2)
    assert key1 == "ip:10.0.0.1", f"XFF must be ignored without trusted proxy; got {key1}"
    assert key1 == key2, "untrusted XFF must not cycle the bucket key (peer-keyed)"


def test_f28_authenticated_caller_keyed_by_user_pk():
    """An authenticated API caller keys by user pk regardless of IP/XFF."""
    from djust.api.dispatch import _caller_key

    rf = RequestFactory()
    req = rf.get("/", HTTP_X_FORWARDED_FOR="6.6.6.6", REMOTE_ADDR="10.0.0.1")
    req.user = _AuthedUser(pk=314)
    assert _caller_key(req) == "user:314"


# --------------------------------------------------------------------------- #
# #17 — the GLOBAL per-message abuse-disconnect stays per-connection (untouched)
# --------------------------------------------------------------------------- #


def test_global_per_message_abuse_disconnect_intact():
    """The per-connection ConnectionRateLimiter still trips should_disconnect()
    after a per-message flood on a single connection — the per-handler shared
    store does NOT touch this global gate (#17).
    """
    rl = ConnectionRateLimiter(rate=1, burst=1, max_warnings=3)
    # First message consumes the single token.
    assert rl.check("event") is True
    # Subsequent messages exhaust the bucket and accrue warnings.
    for _ in range(3):
        rl.check("event")
    assert rl.should_disconnect() is True, (
        "a per-message flood on one connection must still trip the global "
        "abuse-disconnect (#17 — per-connection, not unified)"
    )


def test_global_check_independent_of_shared_handler_store():
    """A handler-store rejection for one caller does NOT consume the global
    per-message bucket, and vice versa — the two are separate gates."""
    settings = {"rate": 1, "burst": 1}
    # Drain the shared per-handler bucket for a caller.
    assert handler_rate_check("user:1", "h", settings) is True
    assert handler_rate_check("user:1", "h", settings) is False

    # A fresh connection's global bucket is unaffected by the above.
    rl = ConnectionRateLimiter(rate=100, burst=20)
    assert rl.check("h") is True


# --------------------------------------------------------------------------- #
# caller_key helper unit coverage (user / session / ip precedence)
# --------------------------------------------------------------------------- #


def test_caller_key_precedence_user_then_session_then_ip():
    """user pk > session key > resolved IP."""
    # Authenticated.
    auth_req = MagicMock()
    auth_req.user = _AuthedUser(pk=10)
    assert caller_key(auth_req, "9.9.9.9") == "user:10"

    # Anonymous with a session key.
    anon_req = MagicMock()
    anon_req.user = AnonymousUser()
    anon_req.session = MagicMock(session_key="abc123")
    assert caller_key(anon_req, "9.9.9.9") == "session:abc123"

    # Anonymous, no session → resolved IP.
    bare_req = MagicMock()
    bare_req.user = AnonymousUser()
    bare_req.session = None
    assert caller_key(bare_req, "9.9.9.9") == "ip:9.9.9.9"

    # No request at all → ip fallback (unknown when no ip).
    assert caller_key(None, None) == "ip:unknown"
