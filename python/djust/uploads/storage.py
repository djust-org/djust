"""State stores for resumable uploads (ADR-010, issue #821).

The state store is the piece that survives WebSocket disconnects.
``ResumableUploadWriter`` persists ``{upload_id: state_dict}`` entries on
every accepted chunk; on WS reconnect the consumer looks up the entry
and replies with ``upload_resumed`` so the client can continue from the
last accepted offset.

Two implementations ship in core:

- :class:`InMemoryUploadState` — process-local dict + lock. Default.
  State is lost on process restart. Fine for dev + single-process
  deployments.
- :class:`RedisUploadState` — requires the ``djust[redis]`` extra.
  Shared across processes, survives restart within the TTL.

The :class:`UploadStateStore` protocol is narrow on purpose — swap in
your own implementation (e.g. Postgres, DynamoDB) if you have a reason.

**State size**: every ``set`` / ``update`` call is capped at 16 KB per
entry (:attr:`MAX_STATE_SIZE_BYTES`). Exceeding it raises
:exc:`UploadStateTooLarge` — see the ADR for the attack model we're
defending against.
"""

from __future__ import annotations

import json
import logging
import threading
import time
from typing import Any, Dict, Optional, Protocol, runtime_checkable

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Size + TTL limits
# ---------------------------------------------------------------------------

#: Max JSON-encoded size of a single state entry. A 2 GB file at 64 KB
#: chunks is 32,768 indices; encoded as run-length-compressed ranges
#: (``[[0, 32767]]``) that's under 20 bytes. 16 KB is a generous margin
#: and bounds per-upload memory/Redis usage — see ADR-010.
MAX_STATE_SIZE_BYTES: int = 16 * 1024

#: Default TTL for a state entry in seconds — 24 hours.
DEFAULT_TTL_SECONDS: int = 24 * 60 * 60


class UploadStateTooLarge(ValueError):
    """Raised when a state dict exceeds :data:`MAX_STATE_SIZE_BYTES`.

    Signals a bug in the writer or an attack attempt (filling
    ``chunks_received`` with noncontiguous indices to bloat the entry).
    Callers should ``abort()`` the upload and delete any partial state.
    """


class UploadStateLocked(RuntimeError):
    """Raised when two WS sessions try to resume the same ``upload_id``.

    v1 policy is to reject the second session with this error — see
    ADR-010 §Rejected alternatives for why we picked reject-over-
    takeover.
    """


# ---------------------------------------------------------------------------
# Protocol
# ---------------------------------------------------------------------------


@runtime_checkable
class UploadStateStore(Protocol):
    """Storage protocol for resumable upload state.

    Implementations must be thread-safe — chunks from a single upload
    can arrive on different WS worker threads, and ``update()`` must
    serialize writes to a given ``upload_id``.
    """

    def get(self, upload_id: str) -> Optional[Dict[str, Any]]:
        """Return the state dict for ``upload_id`` or ``None`` if absent."""

    def set(self, upload_id: str, state: Dict[str, Any], ttl: int) -> None:
        """Overwrite the state entry for ``upload_id`` with ``state`` and
        (re)set the expiry to ``ttl`` seconds from now.

        Raises :exc:`UploadStateTooLarge` if the JSON-encoded state
        exceeds :data:`MAX_STATE_SIZE_BYTES`.
        """

    def update(self, upload_id: str, partial: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """Merge ``partial`` into the existing entry for ``upload_id``.

        Returns the updated state dict, or ``None`` if the entry doesn't
        exist (update is a no-op — we never resurrect a deleted entry).
        TTL is preserved from the prior entry.
        """

    def delete(self, upload_id: str) -> None:
        """Remove the state entry for ``upload_id``. No-op if absent."""


# ---------------------------------------------------------------------------
# In-memory implementation
# ---------------------------------------------------------------------------


class InMemoryUploadState:
    """Process-local state store — dict + lock + lazy TTL.

    State is not shared across processes and is lost on restart. Fine
    for dev and single-process deploys. Production deployments running
    multiple workers (daphne / uvicorn workers + gunicorn) MUST use a
    shared store such as :class:`RedisUploadState`.

    TTL is enforced lazily on access: expired entries are cleaned up
    when next read / written. A background purge thread would add
    complexity without meaningful benefit (the entry never returns from
    ``get()`` once expired, so there's no user-visible leak).
    """

    def __init__(self) -> None:
        self._entries: Dict[str, Dict[str, Any]] = {}
        # Per-entry expiry deadlines — monotonic wall-clock epoch.
        self._expires_at: Dict[str, float] = {}
        self._lock = threading.RLock()

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _is_expired(self, upload_id: str, now: Optional[float] = None) -> bool:
        deadline = self._expires_at.get(upload_id)
        if deadline is None:
            return True
        if now is None:
            now = time.time()
        return now >= deadline

    def _purge_if_expired(self, upload_id: str) -> None:
        """Remove the entry if it's past TTL. Caller holds the lock."""
        if upload_id in self._entries and self._is_expired(upload_id):
            self._entries.pop(upload_id, None)
            self._expires_at.pop(upload_id, None)

    @staticmethod
    def _check_size(state: Dict[str, Any]) -> bytes:
        """Return the JSON-encoded bytes; raise if too large."""
        encoded = json.dumps(state, separators=(",", ":")).encode("utf-8")
        if len(encoded) > MAX_STATE_SIZE_BYTES:
            raise UploadStateTooLarge(
                "Upload state exceeds %d bytes (got %d)" % (MAX_STATE_SIZE_BYTES, len(encoded))
            )
        return encoded

    # ------------------------------------------------------------------
    # Protocol implementation
    # ------------------------------------------------------------------

    def get(self, upload_id: str) -> Optional[Dict[str, Any]]:
        with self._lock:
            self._purge_if_expired(upload_id)
            entry = self._entries.get(upload_id)
            # Return a deep copy so callers can't accidentally mutate
            # our internal state — saves a whole class of concurrency
            # bugs.
            return json.loads(json.dumps(entry)) if entry is not None else None

    def set(self, upload_id: str, state: Dict[str, Any], ttl: int = DEFAULT_TTL_SECONDS) -> None:
        self._check_size(state)
        with self._lock:
            self._entries[upload_id] = json.loads(json.dumps(state))
            self._expires_at[upload_id] = time.time() + ttl

    def update(self, upload_id: str, partial: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        with self._lock:
            self._purge_if_expired(upload_id)
            existing = self._entries.get(upload_id)
            if existing is None:
                return None
            merged = {**existing, **partial}
            self._check_size(merged)
            self._entries[upload_id] = merged
            # Preserve TTL — the docstring promises this.
            return json.loads(json.dumps(merged))

    def delete(self, upload_id: str) -> None:
        with self._lock:
            self._entries.pop(upload_id, None)
            self._expires_at.pop(upload_id, None)

    # ------------------------------------------------------------------
    # Test-only helpers
    # ------------------------------------------------------------------

    def _force_expire(self, upload_id: str) -> None:
        """Test hook: mark an entry as expired without waiting for TTL."""
        with self._lock:
            self._expires_at[upload_id] = 0.0

    def _size(self) -> int:
        """Test hook: count of live (non-expired) entries."""
        with self._lock:
            now = time.time()
            return sum(1 for uid in list(self._entries) if not self._is_expired(uid, now))


# ---------------------------------------------------------------------------
# Redis implementation
# ---------------------------------------------------------------------------


class RedisUploadState:
    """Redis-backed state store for multi-process / multi-host deploys.

    Each entry is stored as a JSON blob under ``djust:upload:<upload_id>``
    with Redis native TTL handling expiry. ``update()`` uses a
    ``WATCH``/``MULTI`` transaction to avoid lost writes when two chunks
    arrive concurrently.

    Requires the ``djust[redis]`` extra (``pip install djust[redis]``).
    Accepts any Redis client exposing the ``redis-py`` interface —
    pass :class:`redis.Redis` directly, a :class:`redis.Sentinel` master
    handle, or a mock for tests.
    """

    KEY_PREFIX = "djust:upload:"

    def __init__(self, client: Any, key_prefix: Optional[str] = None) -> None:
        # Don't import redis at module load — keeps the base install
        # free of the optional dependency.
        self._client = client
        self._key_prefix = key_prefix or self.KEY_PREFIX

    def _key(self, upload_id: str) -> str:
        return f"{self._key_prefix}{upload_id}"

    def get(self, upload_id: str) -> Optional[Dict[str, Any]]:
        raw = self._client.get(self._key(upload_id))
        if raw is None:
            return None
        if isinstance(raw, bytes):
            raw = raw.decode("utf-8")
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            logger.warning("Corrupt JSON in upload state for %s; discarding", upload_id)
            self.delete(upload_id)
            return None

    def set(self, upload_id: str, state: Dict[str, Any], ttl: int = DEFAULT_TTL_SECONDS) -> None:
        encoded = json.dumps(state, separators=(",", ":")).encode("utf-8")
        if len(encoded) > MAX_STATE_SIZE_BYTES:
            raise UploadStateTooLarge(
                "Upload state exceeds %d bytes (got %d)" % (MAX_STATE_SIZE_BYTES, len(encoded))
            )
        # SETEX == SET + EXPIRE atomically. ex= kwarg preferred on modern
        # redis-py; falls back to setex positional form for older clients.
        try:
            self._client.set(self._key(upload_id), encoded, ex=ttl)
        except TypeError:
            self._client.setex(self._key(upload_id), ttl, encoded)

    def update(self, upload_id: str, partial: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        key = self._key(upload_id)
        # WATCH/MULTI to make the read-merge-write atomic across workers.
        # ``pipeline`` is the redis-py entry point; we fall back to a
        # non-atomic read-modify-write if the client doesn't support it
        # (e.g. tests passing a dict-like mock).
        pipeline_factory = getattr(self._client, "pipeline", None)
        if pipeline_factory is None:
            existing = self.get(upload_id)
            if existing is None:
                return None
            merged = {**existing, **partial}
            # Preserve TTL — but the mock path can't read it atomically,
            # so just re-use DEFAULT_TTL_SECONDS. The real redis path
            # below handles TTL preservation correctly.
            self.set(upload_id, merged, ttl=DEFAULT_TTL_SECONDS)
            return merged

        try:
            with pipeline_factory() as pipe:
                while True:
                    try:
                        pipe.watch(key)
                        raw = pipe.get(key)
                        if raw is None:
                            pipe.unwatch()
                            return None
                        if isinstance(raw, bytes):
                            raw = raw.decode("utf-8")
                        existing = json.loads(raw)
                        merged = {**existing, **partial}
                        encoded = json.dumps(merged, separators=(",", ":")).encode("utf-8")
                        if len(encoded) > MAX_STATE_SIZE_BYTES:
                            pipe.unwatch()
                            raise UploadStateTooLarge(
                                "Upload state exceeds %d bytes (got %d)"
                                % (MAX_STATE_SIZE_BYTES, len(encoded))
                            )
                        # Preserve TTL: read remaining ttl, re-apply.
                        remaining = self._client.ttl(key)
                        if remaining is None or remaining < 0:
                            remaining = DEFAULT_TTL_SECONDS
                        pipe.multi()
                        pipe.set(key, encoded, ex=int(remaining))
                        pipe.execute()
                        return merged
                    except Exception as exc:
                        # WatchError from redis-py means another client
                        # wrote between WATCH and EXEC — retry the
                        # transaction. Anything else bubbles out.
                        exc_name = type(exc).__name__
                        if exc_name == "WatchError":
                            continue
                        raise
        except UploadStateTooLarge:
            raise
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "Redis pipeline update failed for upload %s; falling back to non-atomic update: %s",
                upload_id,
                exc,
            )
            existing = self.get(upload_id)
            if existing is None:
                return None
            merged = {**existing, **partial}
            self.set(upload_id, merged, ttl=DEFAULT_TTL_SECONDS)
            return merged

    def delete(self, upload_id: str) -> None:
        try:
            self._client.delete(self._key(upload_id))
        except Exception as exc:  # noqa: BLE001
            logger.warning("Redis delete failed for upload %s: %s", upload_id, exc)


# ---------------------------------------------------------------------------
# Default store resolution
# ---------------------------------------------------------------------------

_default_store: Optional[UploadStateStore] = None
_default_store_lock = threading.Lock()


def get_default_store() -> UploadStateStore:
    """Return the process-wide default upload state store.

    Lazily constructed as an :class:`InMemoryUploadState`. Override via
    :func:`set_default_store` to swap in a Redis-backed store at app
    startup — see :func:`djust.apps.DjustAppConfig.ready` for the
    canonical place.
    """
    global _default_store
    with _default_store_lock:
        if _default_store is None:
            _default_store = InMemoryUploadState()
        return _default_store


def set_default_store(store: UploadStateStore) -> None:
    """Replace the process-wide default upload state store.

    Typically called once at app startup (e.g. in ``AppConfig.ready()``).
    Passing a store that doesn't satisfy :class:`UploadStateStore`
    raises ``TypeError``.
    """
    global _default_store
    if not isinstance(store, UploadStateStore):
        raise TypeError(
            "set_default_store() requires an UploadStateStore-compatible "
            "object (got %s)" % type(store).__name__
        )
    with _default_store_lock:
        _default_store = store


def _reset_default_store_for_tests() -> None:
    """Test-only helper — reset the module-level default store."""
    global _default_store
    with _default_store_lock:
        _default_store = None


__all__ = [
    "MAX_STATE_SIZE_BYTES",
    "DEFAULT_TTL_SECONDS",
    "UploadStateStore",
    "UploadStateTooLarge",
    "UploadStateLocked",
    "InMemoryUploadState",
    "RedisUploadState",
    "get_default_store",
    "set_default_store",
]
