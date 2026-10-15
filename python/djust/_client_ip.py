"""Trustworthy client-IP resolution for the WebSocket and SSE transports.

Both transports feed the resolved IP into security decisions (per-IP connection
limits + cooldown/ban in :mod:`djust.rate_limit`). Trusting a client-supplied
``X-Forwarded-For`` header for that is a rate-limit-bypass + cooldown-poisoning
vector (an attacker rotates XFF to dodge the cap, or spoofs a victim IP to lock
it out). This module centralises the safe resolution so the WS and SSE paths
cannot drift (finding #5).

Default (no trusted proxy configured): the real socket peer is used and XFF is
ignored. When the app runs behind N trusted reverse proxies, the deployer sets
``DJUST_TRUSTED_PROXY_COUNT = N``; the real client is then the Nth entry from
the RIGHT of the XFF chain (proxies append the address they saw, so the
rightmost entries are the trusted inner proxies and everything left of the Nth
is client-controlled / spoofable).
"""

from __future__ import annotations

import logging
from decimal import ROUND_FLOOR, Decimal, InvalidOperation
from typing import Optional

from django.conf import settings

logger = logging.getLogger(__name__)


# A coerced (non-``int``) count with this many integer digits or more is
# treated as invalid.
_MAX_COUNT_DIGITS = 4


def _trusted_proxy_count() -> int:
    """Number of trusted reverse-proxy hops in front of the app (>= 0).

    Integer values other than ``bool`` are clamped at zero; non-negative integers
    are used as given. ``None`` and values equal to zero return zero without
    warning. Other values are converted to decimal and floored exactly, then
    clamped at zero. Invalid and non-finite values, and values of 10,000 or
    more, yield zero. This conversion
    path logs a warning on each call. Configure an ``int`` to avoid coercion.
    """
    raw = getattr(settings, "DJUST_TRUSTED_PROXY_COUNT", 0)
    if isinstance(raw, int) and not isinstance(raw, bool):
        return max(0, raw)
    if raw in (0, None):  # unset / explicit default — no warning
        return 0
    try:
        decimal_value = Decimal(str(int(raw) if isinstance(raw, bool) else raw).strip())
        # Non-finite values, and magnitudes no real proxy chain has (the
        # exponent bound also keeps "1e999999999" from becoming a huge int),
        # trust no hop rather than every hop.
        coerced = (
            max(0, int(decimal_value.to_integral_value(rounding=ROUND_FLOOR)))
            if decimal_value.is_finite() and decimal_value.adjusted() < _MAX_COUNT_DIGITS
            else 0
        )
    except (InvalidOperation, TypeError, ValueError, OverflowError):
        coerced = 0
    # Log the offending value's TYPE, not the value itself: the setting is read
    # from ``settings`` (which CodeQL py/clear-text-logging-sensitive-data treats
    # as a sensitive source) and a misconfiguration is diagnosable from the type
    # ("you set a str, expected int") without echoing a config value to the log.
    logger.warning(
        "DJUST_TRUSTED_PROXY_COUNT must be a non-negative int (got type %s); using %d. "
        "Set it to an integer hop count (0 = trust only the socket peer).",
        type(raw).__name__,
        coerced,
    )
    return coerced


def resolve_client_ip(forwarded_for: Optional[str], peer: Optional[str]) -> Optional[str]:
    """Return the trustworthy client IP.

    Args:
        forwarded_for: Raw ``X-Forwarded-For`` header value (or ``None``).
        peer: The real socket peer (``REMOTE_ADDR`` / ASGI ``scope["client"]``).

    Resolution:
        * ``DJUST_TRUSTED_PROXY_COUNT`` is 0 (default) → return ``peer``; the
          client-controlled XFF header is ignored entirely.
        * ``DJUST_TRUSTED_PROXY_COUNT`` = N > 0 → the app is behind N trusted
          proxies that append to XFF, so the real client is ``chain[-N]``
          (peel N trusted hops from the right). If the chain is shorter than N
          (misconfiguration or a truncated/forged header), fall back to
          ``peer`` rather than trust a spoofable left-side value.
    """
    count = _trusted_proxy_count()
    if count <= 0 or not forwarded_for:
        return peer
    chain = [p.strip() for p in forwarded_for.split(",") if p.strip()]
    idx = len(chain) - count
    if idx < 0 or idx >= len(chain):
        # Chain shorter than the configured trusted-proxy count → don't trust
        # any of it; use the real socket peer.
        return peer
    return chain[idx] or peer
