"""Regression tests for trustworthy client-IP resolution (finding #5).

Before the fix, the WS (`_get_client_ip`) and SSE (`_client_ip_from_request`)
paths took the LEFTMOST X-Forwarded-For value unconditionally — a client-spoofable
value used for per-IP rate limiting + cooldown. An attacker could rotate XFF to
bypass the connection cap, or spoof a victim IP to lock it out.
"""

from django.test import RequestFactory, override_settings

from djust._client_ip import resolve_client_ip
from djust.rate_limit import IPConnectionTracker


# --- core resolver ---


def test_default_ignores_forwarded_for_uses_peer():
    """No trusted proxy configured => the socket peer wins, XFF is ignored."""
    assert resolve_client_ip("6.6.6.6, 203.0.113.10", "203.0.113.10") == "203.0.113.10"


def test_default_no_xff_uses_peer():
    assert resolve_client_ip(None, "203.0.113.10") == "203.0.113.10"


@override_settings(DJUST_TRUSTED_PROXY_COUNT=1)
def test_one_trusted_proxy_takes_rightmost():
    """Behind 1 trusted proxy, the real client is the rightmost XFF entry."""
    assert resolve_client_ip("6.6.6.6, 203.0.113.10", "lb") == "203.0.113.10"


@override_settings(DJUST_TRUSTED_PROXY_COUNT=2)
def test_two_trusted_proxies_peel_two_from_right():
    # chain: client, proxy1-saw  → with 2 trusted hops the client is chain[-2]
    assert resolve_client_ip("realclient, p1", "p2") == "realclient"


@override_settings(DJUST_TRUSTED_PROXY_COUNT=2)
def test_chain_shorter_than_count_falls_back_to_peer():
    """Forged/short chain (fewer than the configured hops) => use the peer."""
    assert resolve_client_ip("6.6.6.6", "realpeer") == "realpeer"


@override_settings(DJUST_TRUSTED_PROXY_COUNT="bad")
def test_malformed_setting_treated_as_zero_and_warns(caplog):
    import logging

    with caplog.at_level(logging.WARNING, logger="djust._client_ip"):
        assert resolve_client_ip("6.6.6.6, peer", "peer") == "peer"
    assert any("DJUST_TRUSTED_PROXY_COUNT" in r.message for r in caplog.records), (
        "a malformed proxy-count setting must be logged, not silently ignored"
    )


@override_settings(DJUST_TRUSTED_PROXY_COUNT=1.0)
def test_float_setting_coerces_with_warning(caplog):
    """A float count is coerced (fail-safe) but warns — it's a config footgun."""
    import logging

    with caplog.at_level(logging.WARNING, logger="djust._client_ip"):
        # 1.0 -> 1 trusted hop => rightmost entry
        assert resolve_client_ip("6.6.6.6, 203.0.113.10", "lb") == "203.0.113.10"
    assert any("DJUST_TRUSTED_PROXY_COUNT" in r.message for r in caplog.records)


@override_settings(DJUST_TRUSTED_PROXY_COUNT=True)
def test_bool_setting_does_not_grant_a_hop_silently(caplog):
    """`True` must not be silently treated as 1 trusted hop without a warning."""
    import logging

    with caplog.at_level(logging.WARNING, logger="djust._client_ip"):
        resolve_client_ip("6.6.6.6, 203.0.113.10", "lb")
    assert any("DJUST_TRUSTED_PROXY_COUNT" in r.message for r in caplog.records)


# --- WS adapter ---


def test_ws_get_client_ip_ignores_forged_xff_by_default():
    from djust.websocket import LiveViewConsumer

    c = LiveViewConsumer()
    c.scope = {
        "headers": [(b"x-forwarded-for", b"6.6.6.6, 203.0.113.10")],
        "client": ("203.0.113.10", 54321),
    }
    assert c._get_client_ip() == "203.0.113.10", "WS trusted a forged leftmost XFF"


# --- SSE adapter ---


def test_sse_client_ip_ignores_forged_xff_by_default():
    from djust.sse import _client_ip_from_request

    req = RequestFactory().get(
        "/", HTTP_X_FORWARDED_FOR="6.6.6.6, 203.0.113.10", REMOTE_ADDR="203.0.113.10"
    )
    assert _client_ip_from_request(req) == "203.0.113.10", "SSE trusted a forged leftmost XFF"


# --- the security impact: rate-limit bypass is closed ---


def test_rate_limit_not_bypassable_by_rotating_xff():
    """With XFF ignored, many connections from one host map to one peer IP =>
    the per-IP cap is enforced (no bypass)."""
    from djust.websocket import LiveViewConsumer

    t = IPConnectionTracker()
    MAX = 2
    accepted = 0
    for i in range(20):
        c = LiveViewConsumer()
        c.scope = {
            "headers": [(b"x-forwarded-for", ("10.0.0.%d" % i).encode())],
            "client": ("203.0.113.10", 1000 + i),  # same real peer every time
        }
        ip = c._get_client_ip()
        if t.connect(ip, MAX):
            accepted += 1
    assert accepted == MAX, "rate-limit bypassable: %d connections accepted under cap %d" % (
        accepted,
        MAX,
    )
