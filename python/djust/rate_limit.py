"""
Server-side rate limiting for WebSocket events.

Uses a token bucket algorithm: tokens refill at a steady rate, and each event
consumes one token. Burst capacity allows short bursts of activity.
"""

import threading
import types
import time
import logging
from collections import OrderedDict
from typing import Any, Dict, Optional, Tuple

from ._client_ip import resolve_client_ip
from .security.log_sanitizer import sanitize_for_log

logger = logging.getLogger(__name__)

# Monotonic clock seam. Aliased once at module load so the token-bucket refill
# math reads through a single indirection. Production behavior is identical to
# calling ``time.monotonic()`` directly; the seam exists purely so tests can
# OWN THE CLOCK — patch this name with a controllable fake instead of the global
# ``time`` module — and assert burst-exhaustion deterministically (no wall-clock
# refill flake under CPU-saturated parallel runs). See tests/unit/test_event_security.py.
_monotonic = time.monotonic


class TokenBucket:
    """
    Token bucket rate limiter.

    Args:
        rate: Tokens added per second.
        burst: Maximum tokens (bucket capacity).
    """

    __slots__ = ("rate", "burst", "tokens", "last_refill")

    def __init__(self, rate: float, burst: int):
        self.rate = rate
        self.burst = burst
        self.tokens = float(burst)
        self.last_refill = _monotonic()

    def consume(self) -> bool:
        """Try to consume one token. Returns True if allowed."""
        now = _monotonic()
        elapsed = now - self.last_refill
        self.tokens = min(self.burst, self.tokens + elapsed * self.rate)
        self.last_refill = now

        if self.tokens >= 1.0:
            self.tokens -= 1.0
            return True
        return False


class ConnectionRateLimiter:
    """
    Per-connection rate limiter with a global bucket, per-handler buckets, and
    a dedicated higher-ceiling bucket for binary upload frames.

    Args:
        rate: Global tokens per second (default from config).
        burst: Global burst capacity (default from config).
        max_warnings: Warnings before disconnect (default from config).
        upload_rate: Upload-frame tokens per second. Legitimate uploads are
            high-volume (a 10 MB file is ~157 64 KB chunk frames), so this bucket
            is intentionally larger than the general per-message limit — but it
            MUST exist so a binary-frame flood still trips ``should_disconnect()``
            (#F17). Defaults to a higher ceiling than the global limit.
        upload_burst: Upload-frame burst capacity. Sized to let a full
            single-file upload land as one burst without throttling legit
            throughput, while a sustained flood depletes the bucket and warns.
    """

    def __init__(
        self,
        rate: float = 100,
        burst: int = 20,
        max_warnings: int = 3,
        upload_rate: float = 200,
        upload_burst: int = 400,
    ):
        self.global_bucket = TokenBucket(rate, burst)
        self.handler_buckets: Dict[str, TokenBucket] = {}
        # Dedicated bucket for binary upload frames (#F17). Without it, upload
        # frames early-return before the global gate and so are never throttled
        # nor counted toward the abuse-disconnect — the exact frame class an
        # attacker would flood with. Higher ceiling than the global bucket so
        # legitimate high-volume uploads are not throttled to nothing.
        self.upload_bucket = TokenBucket(upload_rate, upload_burst)
        self.warnings = 0
        self.max_warnings = max_warnings

    def check(self, event_name: str) -> bool:
        """
        Check if an event is allowed under global rate limit.

        Returns True if allowed, False if rate-limited.
        Per-handler limits are checked separately via check_handler().
        """
        if not self.global_bucket.consume():
            self.warnings += 1
            logger.warning(
                "Rate limit exceeded for message '%s' (warning %d/%d)",
                sanitize_for_log(event_name),
                self.warnings,
                self.max_warnings,
            )
            return False

        return True

    def check_handler(self, event_name: str) -> bool:
        """
        Check per-handler rate limit bucket (if registered).

        Returns True if allowed or no per-handler limit exists.
        """
        handler_bucket = self.handler_buckets.get(event_name)
        if handler_bucket and not handler_bucket.consume():
            self.warnings += 1
            logger.warning(
                "Per-handler rate limit exceeded for '%s' (warning %d/%d)",
                sanitize_for_log(event_name),
                self.warnings,
                self.max_warnings,
            )
            return False

        return True

    def check_upload(self) -> bool:
        """
        Check a binary upload frame against the dedicated upload bucket (#F17).

        Returns True if allowed, False if rate-limited. On failure this
        increments the shared warning counter exactly like ``check()`` /
        ``check_handler()``, so a sustained upload-frame flood trips
        ``should_disconnect()`` → ``close(4429)`` + cooldown — closing the
        bypass where binary upload frames were dispatched before the global
        rate gate and so were never counted toward the abuse-disconnect.
        """
        if not self.upload_bucket.consume():
            self.warnings += 1
            logger.warning(
                "Upload-frame rate limit exceeded (warning %d/%d)",
                self.warnings,
                self.max_warnings,
            )
            return False

        return True

    def should_disconnect(self) -> bool:
        """True if the connection has exceeded the max warning threshold."""
        return self.warnings >= self.max_warnings

    def register_handler_limit(self, event_name: str, rate: float, burst: int) -> None:
        """Register a per-handler rate limit (from @rate_limit decorator)."""
        self.handler_buckets[event_name] = TokenBucket(rate, burst)


class IPConnectionTracker:
    """Process-level tracker for per-IP connection counts and reconnection cooldowns."""

    def __init__(self) -> None:
        self._connections: Dict[str, int] = {}
        self._cooldowns: Dict[str, float] = {}
        self._lock = threading.Lock()

    def connect(self, ip: str, max_per_ip: int) -> bool:
        """Try to register a connection. Returns False if limit reached or in cooldown."""
        with self._lock:
            now = time.monotonic()
            cooldown_until = self._cooldowns.get(ip, 0)
            if now < cooldown_until:
                return False
            self._cooldowns.pop(ip, None)
            count = self._connections.get(ip, 0)
            if count >= max_per_ip:
                return False
            self._connections[ip] = count + 1
            return True

    def disconnect(self, ip: str) -> None:
        with self._lock:
            count = self._connections.get(ip, 0)
            if count <= 1:
                self._connections.pop(ip, None)
            else:
                self._connections[ip] = count - 1

    def add_cooldown(self, ip: str, seconds: float) -> None:
        with self._lock:
            self._cooldowns[ip] = time.monotonic() + seconds


ip_tracker = IPConnectionTracker()


def get_rate_limit_settings(handler: Any) -> Optional[dict]:
    """
    Get rate limit settings from a handler's @rate_limit decorator metadata.

    Returns dict with 'rate' and 'burst' keys, or None if not decorated.
    """
    decorators = getattr(handler, "_djust_decorators", {})
    return decorators.get("rate_limit")


# --------------------------------------------------------------------------- #
# Shared per-caller @rate_limit store (F27 + F28, ADR-008)
# --------------------------------------------------------------------------- #
#
# The per-handler ``@rate_limit`` decorator is meant to throttle a *caller*'s
# invocation rate of a specific handler (abuse prevention for OTP/email sends,
# expensive compute, brute-forceable actions, …). Historically each transport
# enforced it against an INDEPENDENT bucket store:
#
#   * WS  — the per-connection :class:`ConnectionRateLimiter.handler_buckets`,
#           so N WebSocket connections gave N× the configured limit (F27).
#   * SSE — the per-session limiter (same per-connection class).
#   * API — a process-level dict in ``api/dispatch.py``.
#
# A caller could therefore sum allowances across both connection count and
# transport. This module-level store is the SINGLE source of truth for the
# per-handler ``@rate_limit``: one ``(caller_key, handler_name)`` bucket shared
# by all three transports, so a given caller has ONE budget per handler
# regardless of connection count or transport.
#
# NOTE: this does NOT replace the per-connection ``ConnectionRateLimiter``'s
# *global* per-message abuse-disconnect (#17 — ``check()`` / ``check_upload()``
# / ``should_disconnect()``). Connection-flood control is legitimately
# per-connection; only the per-HANDLER ``@rate_limit`` is unified here.

_HANDLER_BUCKET_CAP = 10_000  # LRU cap: evict oldest entry when full to bound memory.
# Keyed ``(caller, handler_identity(owner, name))`` — the second element is the
# callable object (hashed by identity), NOT a name. See handler_identity.
_handler_buckets: "OrderedDict[Tuple[str, Any], TokenBucket]" = OrderedDict()
_handler_buckets_lock = threading.Lock()

# Returned by handler_identity for a callable whose identity cannot be relied on
# (a callable instance, functools.partial, a per-access wrapper). Kept as a named
# sentinel so a call site fails closed with a reason rather than keying on
# something unstable. Its repr is what the log line prints.
UNKEYABLE = type("UNKEYABLE_HANDLER", (), {"__repr__": lambda self: "<unkeyable handler>"})()

UNKEYABLE_HANDLER_REASON = (
    "this handler's identity cannot be relied on, so keying a rate-limit bucket "
    "on it would either collide two handlers into one budget or mint a fresh "
    "bucket on every access (applying no limit). Use a plain method, or declare "
    "the handler explicitly."
)


def caller_key(request: Any, client_ip: Optional[str] = None) -> str:
    """Stable per-caller identity for the shared per-handler ``@rate_limit``.

    Mirrors the SSE owner-principal identity model (Findings #24/#25) so a
    caller is keyed consistently across WS, SSE, and the HTTP API:

        * authenticated caller  -> ``user:<pk>``
        * anonymous w/ session  -> ``session:<session_key>``
        * otherwise             -> ``ip:<client_ip>``

    Args:
        request: The view's ``request`` (carries ``.user`` and ``.session``),
            or ``None`` when no request is available.
        client_ip: The already-:func:`djust._client_ip.resolve_client_ip`-resolved
            client IP (WS/SSE resolve this at connect; the HTTP API resolves it
            from the request). Used only for the anonymous-no-session fallback.

    The IP fallback uses the *resolved* client IP — never a raw ``REMOTE_ADDR``
    — so ``DJUST_TRUSTED_PROXY_COUNT`` governs the API limiter identically to
    the live transports (closes F28: no shared-proxy bucket, no XFF spoof).
    """
    user = getattr(request, "user", None) if request is not None else None
    if user is not None and getattr(user, "is_authenticated", False):
        pk = getattr(user, "pk", None)
        if pk is not None:
            return f"user:{pk}"
    session = getattr(request, "session", None) if request is not None else None
    session_key = getattr(session, "session_key", None) if session is not None else None
    if session_key:
        return f"session:{session_key}"
    return f"ip:{client_ip or 'unknown'}"


def _owner_classes(owner: Any) -> list:
    """The classes that can declare a client-invocable handler for this owner.

    The owner's own class, and — for an ADR-031 bound component — the component
    class behind the proxy's descriptor, which is where its handlers live
    (``LiveComponent.__get__`` resolves through ``type(self._descriptor).__mro__``).
    The hop is restricted to that one framework type, and read with
    ``object.__getattribute__`` so a ``_descriptor`` property on an ordinary view
    is not executed as a side effect of enforcing a rate limit.
    """
    classes = [owner if isinstance(owner, type) else type(owner)]
    if not isinstance(owner, type):
        from .components.base import BoundComponent

        if isinstance(owner, BoundComponent):
            classes.append(type(object.__getattribute__(owner, "_descriptor")))
    return classes


def _owner_declares(owner: Any, func: Any) -> bool:
    """Whether ``func`` is a function declared on this owner's class.

    Compares by identity (``is``), never ``==`` or ``in`` against class values, so
    an object with a custom ``__eq__``/``__hash__`` cannot collapse two
    declarations into one identity.

    Only the **exact builtin** ``staticmethod``/``classmethod`` wrappers are
    unwrapped. A *subclass* of ``staticmethod`` is still an ``isinstance`` of it,
    but it can override ``__get__`` to mint a callable per access and override
    ``__func__`` as a property — reading ``__func__`` would then execute
    the subclass's own code and hand back the minted closure, which would be accepted
    as a declared function, so every call would land in its own bucket and the
    limit would apply not at all (measured: six admissions at ``burst=2``). Gating
    on the exact type refuses subclass wrappers instead of unwrapping them, and
    makes the ``__func__`` read safe by construction: with subclasses excluded,
    ``__func__`` resolves through the builtin slot descriptor on the real type and
    there is nothing left to override.
    """
    for cls in _owner_classes(owner):
        for klass in cls.__mro__:
            # Walk a private copy: iterating ``vars(klass)`` directly can raise
            # on a free-threaded build when another thread writes a first-use
            # class cache into that live dict mid-walk. ``mappingproxy.copy()``
            # copies under the dict's lock (the 1.3 line uses the same copy via
            # ``djust._class_snapshot.namespace``).
            for value in klass.__dict__.copy().values():
                if type(value) is staticmethod or type(value) is classmethod:
                    value = value.__func__
                if value is func:
                    return True
    return False


def handler_identity(owner: Any, handler: Any) -> Any:
    """Canonical identity for a handler — the bucket's second key.

    Keying on the name a request used lets one callable that answers to two names
    hold a budget **per name**, so a caller multiplies the limit by the number of
    names. The contract is one budget per *handler*, so the key must describe the
    handler, not the route to it.

    The key is the **function that will actually execute** — ``handler.__func__``
    for a bound method (a bound method is a fresh object per access, so keying on
    it would put every call in its own bucket), else the handler itself — and it
    is accepted only when it is a **declaration of this owner**, i.e. it appears
    in the owner class's MRO (see :func:`_owner_declares`).

    Requiring both properties together is the point, and each rule alone is wrong:

    * *Provenance alone* (keying on whatever the name is declared as) describes a
      callable that may never run. A class can declare ``send_alias`` and then
      have ``mount`` shadow it with ``self.send_alias = self.expensive_send``; the
      declaration lookup keys the shadowed name on a method nothing will execute,
      so one running callable holds two budgets.
    * *The executed callable alone* is not necessarily stable. A ``property`` or
      custom descriptor can return a freshly decorated function on every access —
      an ordinary ``FunctionType``, indistinguishable from a method by inspecting
      it — and keying on that would give every call its own bucket and apply **no
      limit at all**, while looking correct.

    The intersection has neither failure: an alias (declared or assigned on the
    instance) resolves to the same function as the name it aliases and shares its
    budget; a factory-made function stays distinct from its sibling because they
    are different objects, both declared; a minted closure is refused because it
    is not in the MRO while the descriptor that produced it is.

    Identity is the key and ``is`` is the only comparison: the buckets dict holds the function
    itself, so it cannot be collected or recycled behind the key. The store is
    process-level, so identity need only be stable within one process — which a
    class declaration is, for the class's lifetime.
    """
    func = handler.__func__ if isinstance(handler, types.MethodType) else handler
    if not isinstance(func, (types.FunctionType, types.BuiltinFunctionType)):
        return UNKEYABLE
    if _owner_declares(owner, func):
        return func
    return UNKEYABLE


def handler_rate_check(caller: str, handler_id: Any, settings: Optional[dict]) -> bool:
    """Per-caller token-bucket check for a handler's ``@rate_limit`` settings.

    The SINGLE source of truth shared by WS, SSE, and the HTTP API. Buckets are
    process-level and keyed on ``(caller, handler_id)`` so a caller's budget
    for a handler is shared across every connection and transport.

    Args:
        caller: A :func:`caller_key` result.
        handler_id: The bucket's second key — a :func:`handler_identity` result.
            Historically a name string; a name still works (strings hash by
            value), but call sites pass the callable's identity so aliases share
            one budget.
        settings: The handler's ``@rate_limit`` settings dict
            (``{"rate": …, "burst": …}``) or ``None`` when the handler is not
            decorated.

    Returns:
        True if allowed (or no per-handler limit), False if rate-limited.
    """
    if not settings:
        return True
    if handler_id is UNKEYABLE:
        # Fail closed, and name the reason. Bucketing this would be worse than
        # refusing: a colliding key shares (or WEAKENS) another handler's limit,
        # and an unstable key silently applies no limit at all.
        logger.error(
            "Refusing to apply a per-handler @rate_limit to %r: %s",
            handler_id,
            UNKEYABLE_HANDLER_REASON,
        )
        return False
    limit = (settings["rate"], settings["burst"])
    key = (caller, handler_id)
    with _handler_buckets_lock:
        bucket = _handler_buckets.get(key)
        # A bucket carries the settings it was created with, and nothing updates
        # them. If the same key is later seen with DIFFERENT settings, the second
        # handler would silently run on the first one's budget — and when the
        # first is the looser of the two, the stricter handler's limit is
        # WEAKENED rather than merely shared. Distinct handlers now have distinct
        # identities, so a mismatch means two handlers collided, or a limit was
        # changed at runtime. Fail closed WITHOUT touching the bucket: resetting
        # would hand out a fresh burst, and keying on the settings would let
        # alternating limits mint one budget per settings value.
        # The bucket's own (rate, burst) IS the record of the settings it was
        # created with — TokenBucket is __slots__-ed, so there is no separate
        # attribute to keep in sync.
        if bucket is not None and (bucket.rate, bucket.burst) != limit:
            logger.error(
                "Per-handler @rate_limit differs for one bucket key (%r): %r -> %r. "
                "Refusing the call: two handlers sharing an identity would let the "
                "looser limit govern the stricter one. Consumed budget is left "
                "intact rather than reset.",
                key[1],
                (bucket.rate, bucket.burst),
                limit,
            )
            return False
        if bucket is None:
            bucket = TokenBucket(rate=settings["rate"], burst=settings["burst"])
            _handler_buckets[key] = bucket
            # LRU eviction: cap the dict so a hostile caller cycling identities
            # cannot inflate memory without bound.
            while len(_handler_buckets) > _HANDLER_BUCKET_CAP:
                _handler_buckets.popitem(last=False)
        else:
            # Touch for LRU order.
            _handler_buckets.move_to_end(key)
    return bucket.consume()


def caller_key_for_request(request: Any) -> str:
    """Resolve a :func:`caller_key` from a Django request, resolving the IP.

    The single place the ``HTTP_X_FORWARDED_FOR`` / ``REMOTE_ADDR`` pair is
    turned into a client identity. ``resolve_client_ip`` honors
    ``DJUST_TRUSTED_PROXY_COUNT``, so behind a reverse proxy the buckets are
    per-real-client (no shared-proxy bucket) and an XFF header cannot spoof the
    key (closes F28). Both HTTP transports reach the store through here.
    """
    meta = getattr(request, "META", None) or {}
    client_ip = resolve_client_ip(meta.get("HTTP_X_FORWARDED_FOR"), meta.get("REMOTE_ADDR"))
    return caller_key(request, client_ip)


def http_handler_rate_check(request: Any, owner: Any, handler_name: str, handler: Any) -> bool:
    """Per-handler ``@rate_limit`` check for an HTTP-transport request.

    The HTTP counterpart of the WS/SSE chokepoint's inline check
    (``websocket_utils._validate_event_security``), and the SINGLE helper both
    HTTP transports use — the HTTP API (``api/dispatch._rate_limit_check``) and
    the page-POST fallback (``mixins/request.post``). They previously differed:
    the API enforced the shared store, the page-POST fallback enforced nothing,
    so a handler throttled on every other transport was unthrottled there.

    Callers pass the handler and the request; everything else (setting lookup,
    caller identity, IP resolution) happens here so the two HTTP paths cannot
    drift apart again. The bucket is keyed on the caller and the handler's
    declaration (:func:`handler_identity`), the same key the WS/SSE chokepoint
    uses, so budgets are shared across transports, and one callable reachable
    under two names holds one budget. The IP fallback resolves through
    :func:`djust._client_ip.resolve_client_ip` (honoring
    ``DJUST_TRUSTED_PROXY_COUNT``) rather than trusting a raw ``REMOTE_ADDR`` —
    closing F28 for both paths: per-real-client buckets behind a proxy, no XFF
    spoof.

    Args:
        request: The Django request (carries ``.user``, ``.session``, ``.META``).
        owner: The view or component the handler was resolved on. The handler
            must be declared in its class's MRO; a rate-limited handler that
            is not is refused (see :func:`handler_identity`).
        handler_name: The event/function name the request used. Not part of
            the bucket key.
        handler: The resolved handler; its ``@rate_limit`` metadata is read
            here, and it is the bucket's second key (via
            :func:`handler_identity`).

    Returns:
        True if allowed (or the handler declares no limit), False if
        rate-limited.
    """
    settings = get_rate_limit_settings(handler)
    if settings is None:
        return True
    # Keyed on the handler's canonical identity, not ``handler_name`` (the
    # client-supplied route to it) — see handler_identity: one callable under two
    # names is one handler and holds one budget.
    return handler_rate_check(
        caller_key_for_request(request), handler_identity(owner, handler), settings
    )


def reset_handler_buckets() -> None:
    """Clear the shared per-caller @rate_limit state — used by tests."""
    with _handler_buckets_lock:
        _handler_buckets.clear()
