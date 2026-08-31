"""Out-of-band snapshot storage for large bug-capture blobs (B7 iter C, #1561).

A :class:`~djust.bug_capture.BugCapture` normally travels *inline*: the
whole payload is base64 in the URL fragment
(``djbug1.<base64>``). That is the right default — the blob is
self-contained, needs no server, and survives being pasted into a chat
window. It stops working when the captured state is big: browsers,
proxies and issue trackers all start truncating somewhere in the low
kilobytes.

Iter C adds an opt-in escape hatch. When a store is configured AND the
base64 payload exceeds :data:`DEFAULT_INLINE_LIMIT`, the encoder writes
the payload to the store and emits an *indirect* blob instead::

    djbug1.store.<opaque-id>

``<opaque-id>`` is ``secrets.token_urlsafe(16)`` — 128 bits of entropy,
22 characters from the base64url alphabet. Because that alphabet has no
``.``, an indirect blob can never be confused with an inline one.

Security model (READ THIS BEFORE USING)
---------------------------------------

**The opaque id is a bearer capability.** Anyone who obtains it — from
a browser history entry, a proxy log, a screenshot, a chat backlog, a
``Referer`` header — can fetch the stored snapshot in full, from any
client that can reach the same store. There is no per-recipient
authorization, no revocation, and no audit trail. This is a deliberate
trade (it is what makes a blob shareable by paste), but it means the
*only* bound on exposure is the TTL: a leaked id is live until it
expires. Choose the shortest TTL your workflow tolerates, keep the
production opt-in off unless you mean it, and keep using ``scrub`` /
``time_travel_excluded_fields`` — the store changes *where* PII lives,
never *whether* it is PII.

**Ids are validated before they reach the store.** The decoder never
passes an arbitrary caller-supplied string to the backend: an id must
match :data:`_ID_RE` exactly (22 base64url characters) or decoding fails
before any store lookup. Without that check, a hostile
``djbug1.store.djust:session:abc123`` handed to the replay viewer would
turn a read-only debug page into an arbitrary-key reader for whatever
else lives in the same Redis. The key prefix
(:data:`DEFAULT_KEY_PREFIX`) is a second, independent bound on the same
attack.

**Redis must actually require authentication.** :class:`RedisSnapshotStore`
refuses by default to attach to a Redis that accepts unauthenticated
clients. The check is not a config flag taken on trust and not a scan of
the URL string — it opens a second, *credential-stripped* connection to
the same server and tries to run a command. If that anonymous
connection succeeds, the server is open to anyone who can route to it,
and we refuse regardless of what the configured URL claims. A URL that
carries a password proves only that *we* authenticated; it says nothing
about whether the server requires anyone else to. See
:meth:`RedisSnapshotStore._assert_server_requires_auth`.

Configuration
-------------

::

    LIVEVIEW_CONFIG = {
        # No store (default): behaviour is exactly as it was in iter A/B —
        # every blob is inline, and nothing in this module is constructed.
        "bug_capture_store": None,

        # Process-local, dev-only. An indirect blob is resolvable only by
        # the process that produced it, so it is NOT shareable.
        "bug_capture_store": "memory",

        # Shareable. Requires the `redis` package and an authenticated server.
        "bug_capture_store": {
            "backend": "redis",
            "url": "redis://:s3cret@redis.internal:6379/3",
            "ttl": 3600,
        },
    }

A :class:`SnapshotStore` instance or a dotted path to a factory is also
accepted; see :func:`get_store`.
"""

from __future__ import annotations

import logging
import re
import secrets
import threading
import time
from abc import ABC, abstractmethod
from collections import OrderedDict
from typing import Any, Dict, Optional, Tuple

logger = logging.getLogger(__name__)

#: Payloads whose base64 length exceeds this go to the configured store.
#: 1.5 KB keeps the resulting ``djbug1.<base64>`` comfortably inside the
#: ~2 KB budget where URL handling stays boring across browsers, proxies
#: and issue trackers. Override with
#: ``LIVEVIEW_CONFIG['bug_capture_inline_limit']``.
DEFAULT_INLINE_LIMIT = 1536

#: Default TTL for a stored snapshot, in seconds. A leaked opaque id is
#: live until this expires — see the module docstring.
DEFAULT_TTL = 3600

#: Namespace for stored keys. A second bound (after id validation) on
#: what an attacker-supplied indirect blob can address.
DEFAULT_KEY_PREFIX = "djust:bugcapture:"

#: The exact shape ``secrets.token_urlsafe(16)`` produces: 16 random
#: bytes → 22 characters of the base64url alphabet, unpadded. Anchored
#: with ``fullmatch`` at every use site.
_ID_RE = re.compile(r"[A-Za-z0-9_-]{22}")

#: The marker that distinguishes an indirect blob from an inline one.
#: ``.`` is not in the base64url alphabet, so an inline payload can never
#: start with this.
STORE_MARKER = "store."


def new_snapshot_id() -> str:
    """Return a fresh opaque snapshot id (128 bits of entropy)."""
    return secrets.token_urlsafe(16)


def is_valid_snapshot_id(snapshot_id: Any) -> bool:
    """True when *snapshot_id* is exactly the shape :func:`new_snapshot_id` emits.

    Called on untrusted input before any store lookup. Deliberately
    strict: a length- and alphabet-bounded id cannot be used to address
    keys outside this module's namespace, whatever else shares the
    backing store.
    """
    return isinstance(snapshot_id, str) and _ID_RE.fullmatch(snapshot_id) is not None


class SnapshotStore(ABC):
    """Where an over-threshold bug-capture payload lives instead of the URL.

    Implementations store an opaque ``str`` payload (the base64 body of
    a ``djbug1.`` blob — *not* the decoded JSON) under a generated id,
    and expire it after a TTL. They are never handed a caller-supplied
    id that has not passed :func:`is_valid_snapshot_id`.
    """

    #: TTL applied when :meth:`put` is called without an explicit one.
    default_ttl: int = DEFAULT_TTL

    @abstractmethod
    def put(self, payload: str, ttl: Optional[int] = None) -> str:
        """Store *payload* and return the opaque id that retrieves it.

        Args:
            payload: The base64 body of a ``djbug1.`` blob.
            ttl: Lifetime in seconds; falls back to :attr:`default_ttl`.

        Returns:
            An id matching :func:`is_valid_snapshot_id`.
        """

    @abstractmethod
    def get(self, snapshot_id: str) -> Optional[str]:
        """Return the payload stored under *snapshot_id*, or ``None``.

        ``None`` covers both "never existed" and "expired" — the two are
        deliberately indistinguishable to a caller, so a probe cannot
        use the response to confirm that a guessed id was ever valid.
        """


class InMemorySnapshotStore(SnapshotStore):
    """Process-local store. Dev and tests only.

    An indirect blob produced against this store is resolvable **only by
    the process that produced it** — it is not shareable with a
    teammate, and it does not survive a reload. That is the whole reason
    the default is "no store at all" rather than this: silently turning
    a shareable inline blob into a process-local reference would be a
    worse default than a long URL.

    Bounded: at most *max_entries* live snapshots, oldest evicted first,
    so a long-running dev server can't accumulate captured state without
    limit.
    """

    def __init__(self, default_ttl: int = DEFAULT_TTL, max_entries: int = 128) -> None:
        self.default_ttl = int(default_ttl)
        self._max_entries = int(max_entries)
        self._lock = threading.Lock()
        # id -> (expires_at_monotonic, payload)
        self._entries: "OrderedDict[str, Tuple[float, str]]" = OrderedDict()

    def put(self, payload: str, ttl: Optional[int] = None) -> str:
        snapshot_id = new_snapshot_id()
        expires_at = time.monotonic() + float(ttl if ttl is not None else self.default_ttl)
        with self._lock:
            self._purge_expired_locked()
            self._entries[snapshot_id] = (expires_at, payload)
            while len(self._entries) > self._max_entries:
                self._entries.popitem(last=False)
        return snapshot_id

    def get(self, snapshot_id: str) -> Optional[str]:
        if not is_valid_snapshot_id(snapshot_id):
            return None
        with self._lock:
            self._purge_expired_locked()
            entry = self._entries.get(snapshot_id)
        return entry[1] if entry is not None else None

    def _purge_expired_locked(self) -> None:
        now = time.monotonic()
        expired = [key for key, (expires_at, _) in self._entries.items() if expires_at <= now]
        for key in expired:
            del self._entries[key]


class UnauthenticatedRedisError(RuntimeError):
    """Raised when a Redis server accepts unauthenticated clients.

    Its own class (rather than a bare ``RuntimeError``) so a deployer who
    genuinely runs Redis behind a mechanism Redis itself cannot see —
    mutual TLS, a unix socket with filesystem permissions — can catch
    exactly this and nothing else while deciding whether to pass
    ``require_auth=False``.
    """


class RedisSnapshotStore(SnapshotStore):
    """Redis-backed store. The shareable one.

    Refuses by default to attach to a Redis that accepts unauthenticated
    clients — see :meth:`_assert_server_requires_auth` and the module
    docstring's security model.

    Args:
        url: Redis connection URL, as ``redis.from_url`` accepts it.
        ttl: Snapshot lifetime in seconds.
        key_prefix: Namespace for stored keys.
        require_auth: When True (default), refuse to attach unless the
            server rejects an anonymous connection. Set False ONLY when
            the connection is authenticated by something outside Redis's
            own view of it (mTLS, a permission-bounded unix socket) —
            never merely because the URL carries a password, which
            proves nothing about what the server demands of anyone else.
        socket_timeout: Applied to both connect and command, so a
            firewalled host fails fast instead of hanging an encode.
    """

    def __init__(
        self,
        url: str,
        ttl: int = DEFAULT_TTL,
        key_prefix: str = DEFAULT_KEY_PREFIX,
        require_auth: bool = True,
        socket_timeout: float = 3.0,
    ) -> None:
        try:
            import redis
        except ImportError as exc:  # pragma: no cover - environment-dependent
            raise ImportError(
                "redis-py is required for RedisSnapshotStore. Install with: pip install redis"
            ) from exc

        self._redis = redis
        self._url = url
        self.default_ttl = int(ttl)
        self._key_prefix = key_prefix
        self._socket_timeout = socket_timeout

        # ORDER IS LOAD-BEARING: probe the server for anonymous access
        # BEFORE using the configured credentials for anything. A URL
        # carrying a password against an open server must fail with the
        # refusal below, not with whatever error the server returns for
        # an unnecessary AUTH.
        if require_auth:
            self._assert_server_requires_auth()
        else:
            logger.warning(
                "RedisSnapshotStore: require_auth=False — the "
                "unauthenticated-server refusal is DISABLED for %s. "
                "Captured LiveView state may contain user PII.",
                _redact(url),
            )

        self._client = redis.from_url(
            url,
            socket_timeout=socket_timeout,
            socket_connect_timeout=socket_timeout,
        )
        self._client.ping()

    # -- the refusal ------------------------------------------------------

    def _assert_server_requires_auth(self) -> None:
        """Refuse unless the server rejects an *anonymous* connection.

        The check deliberately ignores the configured URL's credentials.
        Deciding "is this Redis protected?" by looking at the URL is the
        classic validation-before-normalization mistake: ``redis://:@h``
        has an ``@`` and no password, ``redis://h?password=x`` has a
        password and no ``@``, ``redis://u:p%40ss@h`` percent-encodes
        one, and *every* one of them can point at a server with no
        ``requirepass`` at all. The only question that matters is what
        the server does with a client that presents no credentials, so
        that is the question we ask it.

        Credentials are stripped structurally, not textually: redis-py
        parses the URL into connection kwargs (decoding percent-escapes,
        resolving userinfo-vs-query precedence), and we drop the
        credential keys from that parsed mapping. No string surgery on
        the URL, so there is no encoding for an attacker to hide in.

        Fails closed: an inconclusive probe (host unreachable, timeout,
        an unrecognized error) refuses just like a successful one.
        """
        redis = self._redis
        try:
            source_pool = redis.ConnectionPool.from_url(self._url)
        except Exception as exc:
            raise UnauthenticatedRedisError(
                "RedisSnapshotStore could not parse the configured Redis URL "
                "well enough to verify that the server requires authentication: "
                "%s" % exc
            ) from exc

        probe_kwargs: Dict[str, Any] = dict(source_pool.connection_kwargs)
        for credential_key in ("username", "password", "credential_provider"):
            probe_kwargs.pop(credential_key, None)
        probe_kwargs["socket_timeout"] = self._socket_timeout
        probe_kwargs["socket_connect_timeout"] = self._socket_timeout

        probe_pool = redis.ConnectionPool(
            connection_class=source_pool.connection_class, **probe_kwargs
        )
        probe = redis.Redis(connection_pool=probe_pool)
        try:
            probe.ping()
        except redis.AuthenticationError:
            # The server demanded credentials from an anonymous client.
            # This is the only outcome that lets us proceed.
            return
        except redis.ResponseError as exc:
            if "NOAUTH" in str(exc).upper():
                return
            raise UnauthenticatedRedisError(
                "RedisSnapshotStore could not determine whether %s requires "
                "authentication (unexpected reply to an anonymous PING: %s). "
                "Refusing to store captured LiveView state on a server whose "
                "auth posture is unknown." % (_redact(self._url), exc)
            ) from exc
        except redis.RedisError as exc:
            raise UnauthenticatedRedisError(
                "RedisSnapshotStore could not reach %s to verify that it "
                "requires authentication (%s: %s). Refusing to attach — this "
                "check fails closed." % (_redact(self._url), type(exc).__name__, exc)
            ) from exc
        else:
            raise UnauthenticatedRedisError(
                "RedisSnapshotStore refuses to attach to %s: the server "
                "answered a PING from a connection carrying NO credentials, so "
                "anyone who can route to it can read every captured snapshot — "
                "which may contain user PII. A password in the configured URL "
                "does not change this; it proves only that WE authenticated, "
                "not that the server requires anyone else to. Fix the server "
                "(set `requirepass`, or an ACL user with a password), or — only "
                "if the connection is authenticated by a mechanism Redis itself "
                "cannot see, such as mutual TLS — pass require_auth=False." % _redact(self._url)
            )
        finally:
            try:
                probe.close()
            except Exception:  # pragma: no cover - best-effort cleanup
                logger.debug("RedisSnapshotStore: probe connection close failed", exc_info=True)

    # -- SnapshotStore ----------------------------------------------------

    def put(self, payload: str, ttl: Optional[int] = None) -> str:
        lifetime = int(ttl if ttl is not None else self.default_ttl)
        for _ in range(3):
            snapshot_id = new_snapshot_id()
            # nx=True so a (vanishingly improbable) id collision can
            # never silently overwrite somebody else's snapshot.
            if self._client.set(self._key(snapshot_id), payload, ex=lifetime, nx=True):
                return snapshot_id
        raise RuntimeError(  # pragma: no cover - requires 3 128-bit collisions
            "RedisSnapshotStore could not allocate an unused snapshot id"
        )

    def get(self, snapshot_id: str) -> Optional[str]:
        if not is_valid_snapshot_id(snapshot_id):
            return None
        raw = self._client.get(self._key(snapshot_id))
        if raw is None:
            return None
        if isinstance(raw, bytes):
            try:
                return raw.decode("ascii")
            except UnicodeDecodeError:
                return None
        return str(raw)

    def _key(self, snapshot_id: str) -> str:
        return "%s%s" % (self._key_prefix, snapshot_id)


def _redact(url: str) -> str:
    """Return *url* with any userinfo replaced, for safe logging."""
    return re.sub(r"//[^/@]*@", "//<redacted>@", str(url))


# ---------------------------------------------------------------------------
# Configuration resolution
# ---------------------------------------------------------------------------

# Cached resolved store, keyed on the raw config value it was built from.
# Rebuilding on every encode would mean a Redis handshake per capture;
# never rebuilding would mean `override_settings` (and a settings reload)
# silently kept the old store. Comparing against the config value that
# produced the cached instance gets both.
_STORE_CACHE: Optional[Tuple[Any, Optional[SnapshotStore]]] = None
_STORE_CACHE_LOCK = threading.Lock()


def _live_config(key: str, default: Any = None) -> Any:
    """Read *key* from ``settings.LIVEVIEW_CONFIG``.

    Read from settings directly rather than through the
    ``djust.config`` singleton on purpose: that singleton snapshots
    ``LIVEVIEW_CONFIG`` once at import time, so a store swapped under
    ``override_settings`` (or by a settings reload) would never be seen.
    A store is resolved at most once per distinct config value, so the
    extra ``getattr`` costs nothing measurable.
    """
    try:
        from django.conf import settings
    except Exception:  # pragma: no cover - Django always available in djust
        return default
    try:
        live_cfg = getattr(settings, "LIVEVIEW_CONFIG", None) or {}
    except Exception:  # pragma: no cover - settings not configured
        return default
    if not isinstance(live_cfg, dict):
        return default
    value = live_cfg.get(key)
    return default if value is None else value


def inline_limit() -> int:
    """Base64 length above which a payload goes to the store, if one is configured."""
    try:
        return int(_live_config("bug_capture_inline_limit", DEFAULT_INLINE_LIMIT))
    except (TypeError, ValueError):
        logger.warning(
            "LIVEVIEW_CONFIG['bug_capture_inline_limit'] is not an integer; "
            "using the default of %d",
            DEFAULT_INLINE_LIMIT,
        )
        return DEFAULT_INLINE_LIMIT


def get_store() -> Optional[SnapshotStore]:
    """Return the configured :class:`SnapshotStore`, or ``None`` if unconfigured.

    ``None`` — the default — means every blob stays inline and nothing
    in this module is constructed: no import of ``redis``, no
    connection, no cache entry. That is the zero-cost-when-unused shape
    djust applies to every optional subsystem, and here it is also the
    safer default, because an indirect blob is only as shareable as the
    store behind it.

    Accepted values of ``LIVEVIEW_CONFIG['bug_capture_store']``:

    - ``None`` / absent / ``False`` — no store (default).
    - ``"memory"`` — :class:`InMemorySnapshotStore` (dev only).
    - a ``dict`` — ``{"backend": "memory"|"redis", ...}``; remaining keys
      are passed to the backend's constructor (``url``, ``ttl``,
      ``key_prefix``, ``require_auth`` for Redis).
    - a :class:`SnapshotStore` instance — used as-is.
    - a dotted path to a :class:`SnapshotStore` subclass or a
      zero-argument factory returning one.

    A misconfigured store is loud, not silent: construction errors
    propagate to the caller of ``encode()`` rather than degrading to an
    inline blob, so a deployer who asked for Redis never quietly gets
    process-local URLs instead.
    """
    global _STORE_CACHE

    spec = _live_config("bug_capture_store", None)
    cache = _STORE_CACHE
    if cache is not None and cache[0] == spec:
        return cache[1]

    with _STORE_CACHE_LOCK:
        cache = _STORE_CACHE
        if cache is not None and cache[0] == spec:
            return cache[1]
        store = _build_store(spec)
        _STORE_CACHE = (spec, store)
        return store


def reset_store_cache() -> None:
    """Drop the cached store. For tests and settings reloads."""
    global _STORE_CACHE
    with _STORE_CACHE_LOCK:
        _STORE_CACHE = None


def _build_store(spec: Any) -> Optional[SnapshotStore]:
    if spec is None or spec is False or spec == "":
        return None

    if isinstance(spec, SnapshotStore):
        return spec

    if isinstance(spec, str):
        if spec in ("memory", "inmemory", "in-memory"):
            return InMemorySnapshotStore()
        if spec == "redis":
            raise ValueError(
                "LIVEVIEW_CONFIG['bug_capture_store'] = 'redis' needs a URL. Use "
                "{'backend': 'redis', 'url': 'redis://:password@host:6379/0'}."
            )
        return _build_from_dotted_path(spec)

    if isinstance(spec, dict):
        options = dict(spec)
        backend = options.pop("backend", None) or options.pop("BACKEND", None)
        if backend in (None, "memory", "inmemory", "in-memory"):
            # `ttl` is the spelling every backend takes in config; the
            # in-memory constructor's parameter is `default_ttl` (it is
            # also the attribute name on the ABC), so accept both here
            # rather than making the config shape backend-dependent.
            if "ttl" in options:
                options["default_ttl"] = options.pop("ttl")
            return InMemorySnapshotStore(**options)
        if backend == "redis":
            if "url" not in options:
                raise ValueError(
                    "LIVEVIEW_CONFIG['bug_capture_store'] with backend 'redis' "
                    "requires a 'url' key."
                )
            return RedisSnapshotStore(**options)
        if isinstance(backend, str):
            built = _resolve_dotted(backend)(**options)
            if not isinstance(built, SnapshotStore):
                raise ValueError(
                    "LIVEVIEW_CONFIG['bug_capture_store']['backend'] = %r produced "
                    "%s, which is not a SnapshotStore" % (backend, type(built).__name__)
                )
            return built
        raise ValueError(
            "LIVEVIEW_CONFIG['bug_capture_store']['backend'] must be "
            "'memory', 'redis', or a dotted path; got %r" % (backend,)
        )

    raise ValueError(
        "LIVEVIEW_CONFIG['bug_capture_store'] must be None, 'memory', a dict, "
        "a dotted path, or a SnapshotStore instance; got %s" % type(spec).__name__
    )


def _build_from_dotted_path(path: str) -> SnapshotStore:
    built = _resolve_dotted(path)()
    if not isinstance(built, SnapshotStore):
        raise ValueError(
            "LIVEVIEW_CONFIG['bug_capture_store'] = %r produced %s, "
            "which is not a SnapshotStore" % (path, type(built).__name__)
        )
    return built


def _resolve_dotted(path: str) -> Any:
    from django.utils.module_loading import import_string

    try:
        return import_string(path)
    except ImportError as exc:
        raise ValueError(
            "LIVEVIEW_CONFIG['bug_capture_store'] = %r could not be imported: %s" % (path, exc)
        ) from exc


__all__ = [
    "DEFAULT_INLINE_LIMIT",
    "DEFAULT_KEY_PREFIX",
    "DEFAULT_TTL",
    "STORE_MARKER",
    "InMemorySnapshotStore",
    "RedisSnapshotStore",
    "SnapshotStore",
    "UnauthenticatedRedisError",
    "get_store",
    "inline_limit",
    "is_valid_snapshot_id",
    "new_snapshot_id",
    "reset_store_cache",
]
