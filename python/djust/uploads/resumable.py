"""Resumable uploads — survive WebSocket disconnects (ADR-010, issue #821).

Provides :class:`ResumableUploadWriter`, an :class:`~djust.uploads.UploadWriter`
wrapper that persists state into a pluggable state store on every chunk.
When the WS drops and reconnects, the server looks up the state entry by
``upload_id`` and replies to ``upload_resume`` with the last accepted
offset — the client picks up from there.

The wire protocol and failure modes are spec'd in
``docs/adr/010-resumable-uploads.md``.

Typical usage from a view::

    from djust import LiveView
    from djust.uploads import UploadMixin
    from djust.uploads.resumable import ResumableUploadWriter
    from djust.uploads.storage import get_default_store

    class BigUploadsView(LiveView, UploadMixin):
        def mount(self, request, **kwargs):
            self.allow_upload(
                "video",
                accept=".mp4",
                max_file_size=2_000_000_000,
                writer=ResumableUploadWriter.with_inner(MyS3Writer),
                resumable=True,
            )

``ResumableUploadWriter.with_inner`` is a class-factory helper: you
supply an inner :class:`UploadWriter` subclass (e.g. S3 multipart) and
get back a subclass of :class:`ResumableUploadWriter` that wraps it.
The resumable writer delegates ``open``/``write_chunk``/``close``/``abort``
to the inner writer and layers on state-store persistence + idempotent
replay of duplicate chunks.
"""

from __future__ import annotations

import logging
import time
from typing import Any, Callable, Dict, List, Optional, Tuple, Type

from . import UploadWriter
from .storage import (
    DEFAULT_TTL_SECONDS,
    UploadStateStore,
    UploadStateTooLarge,
    get_default_store,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Chunk-set compaction
# ---------------------------------------------------------------------------
#
# Storing ``chunks_received`` as a raw list of ints grows linearly with
# the file size (32,768 entries for a 2 GB file at 64 KB chunks). That
# blows the 16 KB state budget. We compact to a list of [start, end]
# inclusive ranges instead — contiguous uploads (the common case)
# collapse to a single ``[[0, 32767]]`` range which fits in 20 bytes.


def compact_chunks(chunks: List[int]) -> List[Tuple[int, int]]:
    """Compact a sorted list of chunk indices into [start, end] ranges.

    Handles unsorted / duplicate input defensively — sorts and dedupes
    before compacting. Empty input returns ``[]``.
    """
    if not chunks:
        return []
    ordered = sorted(set(int(c) for c in chunks))
    ranges: List[Tuple[int, int]] = []
    start = prev = ordered[0]
    for c in ordered[1:]:
        if c == prev + 1:
            prev = c
            continue
        ranges.append((start, prev))
        start = prev = c
    ranges.append((start, prev))
    return ranges


def expand_ranges(ranges: List[Any]) -> List[int]:
    """Expand [start, end] ranges back into a flat list of chunk indices."""
    out: List[int] = []
    for r in ranges or []:
        if not isinstance(r, (list, tuple)) or len(r) != 2:
            continue
        start, end = int(r[0]), int(r[1])
        out.extend(range(start, end + 1))
    return out


def bytes_received_from_ranges(ranges: List[Any], chunk_size: int) -> int:
    """Compute ``bytes_received`` from compacted ranges.

    This is an upper bound — we assume every chunk is ``chunk_size``
    bytes, which is true for every chunk except the last. The client
    reconciles the exact final offset from its own file length.
    """
    total = 0
    for r in ranges or []:
        if not isinstance(r, (list, tuple)) or len(r) != 2:
            continue
        start, end = int(r[0]), int(r[1])
        total += max(0, end - start + 1) * chunk_size
    return total


# ---------------------------------------------------------------------------
# ResumableUploadWriter
# ---------------------------------------------------------------------------


class ResumableUploadWriter(UploadWriter):
    """UploadWriter wrapper that persists state across WS disconnects.

    Concrete writers are built via :meth:`with_inner`, which binds an
    inner writer class::

        S3Resumable = ResumableUploadWriter.with_inner(
            MyS3MultipartWriter,
            state_store=my_redis_store,
            ttl_hours=48,
        )

    The returned class can then be passed to ``allow_upload(writer=...)``.

    Lifecycle:

    - ``__init__``: construct the inner writer and probe the state
      store. On store failure, log a WARNING and degrade gracefully —
      chunks still flow through the inner writer, but we can't resume
      on disconnect (same behavior as today's non-resumable path).
    - ``open()``: delegate to inner; persist an initial state entry.
    - ``write_chunk(data, chunk_index)``: persist chunk before calling
      inner.write_chunk. If the chunk_index is already in
      ``chunks_received``, skip the inner call (idempotent replay).
    - ``close()``: delegate to inner; delete the state entry.
    - ``abort(error)``: delegate to inner; delete the state entry.
    - ``snapshot_for_resume()``: return the state entry for the
      WebSocket consumer to reply to an ``upload_resume`` request.

    Thread-safety: the state store is contracted to be thread-safe;
    this writer just layers calls over it.
    """

    #: Factory-binding slot: the inner :class:`UploadWriter` subclass
    #: to delegate chunks to. Set by :meth:`with_inner`.
    _inner_writer_cls: Optional[Type[UploadWriter]] = None

    #: Default chunk size used to estimate ``bytes_received`` from the
    #: compacted ranges — should match the slot's ``chunk_size``. The
    #: accurate value is set on the first ``write_chunk`` call.
    _chunk_size: int = 64 * 1024

    def __init__(
        self,
        upload_id: str,
        filename: str,
        content_type: str,
        expected_size: Optional[int] = None,
        *,
        state_store: Optional[UploadStateStore] = None,
        ttl_hours: float = DEFAULT_TTL_SECONDS / 3600,
        session_key: Optional[str] = None,
    ) -> None:
        super().__init__(upload_id, filename, content_type, expected_size)
        self.state_store: UploadStateStore = state_store or get_default_store()
        self.ttl: int = int(ttl_hours * 3600)
        self.session_key: Optional[str] = session_key
        self._store_available: bool = True
        self._chunks_received: List[int] = []
        self._inner: Optional[UploadWriter] = None
        self._opened: bool = False
        self._finalized: bool = False

        inner_cls = type(self)._inner_writer_cls
        if inner_cls is None:
            raise RuntimeError(
                "ResumableUploadWriter must be subclassed via "
                "ResumableUploadWriter.with_inner(InnerWriterCls) before "
                "being passed to allow_upload(writer=...)."
            )
        self._inner = inner_cls(
            upload_id=upload_id,
            filename=filename,
            content_type=content_type,
            expected_size=expected_size,
        )

        # Store probe: if the store is unreachable, log once and fall
        # back to non-resumable behavior. We intentionally swallow
        # errors here — an unreachable Redis shouldn't tank the whole
        # upload system.
        try:
            self.state_store.get("__djust_probe__")
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "ResumableUploadWriter: state store %s is unavailable "
                "(%s) — falling back to non-resumable behavior for "
                "upload %s",
                type(self.state_store).__name__,
                exc,
                upload_id,
            )
            self._store_available = False

    # ------------------------------------------------------------------
    # Factory
    # ------------------------------------------------------------------

    @classmethod
    def with_inner(
        cls,
        inner_writer_cls: Type[UploadWriter],
        *,
        state_store: Optional[UploadStateStore] = None,
        ttl_hours: float = 24.0,
    ) -> Type["ResumableUploadWriter"]:
        """Return a :class:`ResumableUploadWriter` subclass bound to
        ``inner_writer_cls``.

        The returned class is what you pass to ``allow_upload(writer=)``.
        Passing :class:`ResumableUploadWriter` itself (without
        ``with_inner``) raises ``RuntimeError`` at construction time —
        a bare resumable writer has nothing to persist *to*.
        """
        fixed_store = state_store
        fixed_ttl_seconds = int(ttl_hours * 3600)

        class _Bound(cls):  # type: ignore[misc,valid-type]
            _inner_writer_cls = inner_writer_cls

            def __init__(self, **kwargs: Any) -> None:
                # Allow per-upload override of state_store / ttl; fall
                # back to the factory-bound defaults.
                kwargs.setdefault(
                    "state_store",
                    fixed_store if fixed_store is not None else get_default_store(),
                )
                kwargs.setdefault("ttl_hours", fixed_ttl_seconds / 3600)
                super().__init__(**kwargs)

        _Bound.__name__ = f"Resumable{inner_writer_cls.__name__}"
        _Bound.__qualname__ = _Bound.__name__
        return _Bound

    # ------------------------------------------------------------------
    # UploadWriter lifecycle hooks
    # ------------------------------------------------------------------

    def open(self) -> None:
        assert self._inner is not None
        self._inner.open()
        self._opened = True
        self._persist_initial_state()

    def write_chunk(self, chunk: bytes, chunk_index: int = 0) -> None:
        """Persist the chunk in state, then forward to the inner writer.

        ``chunk_index`` is an extension over the base ``UploadWriter``
        signature — see :meth:`_add_chunk_via_writer` in
        :class:`UploadManager` where it's wired up. The base class
        ignores ``chunk_index`` so non-resumable writers stay
        source-compatible.

        Idempotency: if ``chunk_index`` is already in
        ``chunks_received`` the inner write is skipped — this
        implements the "client resends a chunk we already have"
        resume path without double-writing to S3 / disk.
        """
        assert self._inner is not None
        if not self._opened:
            # Base UploadManager calls open() before first write_chunk,
            # but we defend against framework bugs here — a write
            # without open is a hard error.
            raise RuntimeError("ResumableUploadWriter.write_chunk() called before open()")
        if chunk_index in self._chunks_received:
            logger.debug(
                "ResumableUploadWriter: duplicate chunk %d for %s — "
                "idempotent replay, not re-forwarding to inner writer",
                chunk_index,
                self.upload_id,
            )
            return

        # Persist FIRST. If the inner write fails after we've updated
        # the state, the client's retry with the same chunk_index will
        # be treated as a duplicate and skipped — which means the
        # bytes are still missing from the backend. So we have to be
        # careful: only mark the chunk "received" after the inner
        # write has succeeded. State-before-backend for ordering, but
        # the "received" list update is conditioned on success.
        try:
            self._inner.write_chunk(chunk)
        except Exception:
            # Don't mark received — let the client retry.
            raise

        self._chunks_received.append(chunk_index)
        self._chunk_size = max(self._chunk_size, len(chunk))
        self._persist_chunk_progress()

    def close(self) -> Any:
        assert self._inner is not None
        try:
            result = self._inner.close()
        finally:
            self._finalized = True
            self._delete_state_entry()
        return result

    def abort(self, error: BaseException) -> None:
        assert self._inner is not None
        try:
            self._inner.abort(error)
        finally:
            self._delete_state_entry()

    # ------------------------------------------------------------------
    # Resume / snapshot
    # ------------------------------------------------------------------

    def snapshot_for_resume(self) -> Dict[str, Any]:
        """Return the state dict suitable for an ``upload_resumed`` reply.

        Returns ``{}`` when the state store is unavailable — caller
        treats this as "not resumable" and the client falls back to
        a full re-upload.
        """
        if not self._store_available:
            return {}
        state = self.state_store.get(self.upload_id)
        return state or {}

    # ------------------------------------------------------------------
    # State-store plumbing
    # ------------------------------------------------------------------

    def _base_state(self) -> Dict[str, Any]:
        return {
            "upload_id": self.upload_id,
            "filename": self.filename,
            "content_type": self.content_type,
            "expected_size": self.expected_size,
            "session_key": self.session_key,
            "chunk_size": self._chunk_size,
            "chunks_received_ranges": compact_chunks(self._chunks_received),
            "bytes_received": bytes_received_from_ranges(
                compact_chunks(self._chunks_received), self._chunk_size
            ),
            "created_at": time.time(),
            "last_updated": time.time(),
        }

    def _persist_initial_state(self) -> None:
        if not self._store_available:
            return
        try:
            self.state_store.set(self.upload_id, self._base_state(), ttl=self.ttl)
        except UploadStateTooLarge:
            # Can only happen with an attacker-crafted filename. Log,
            # mark the store unusable for this upload, and continue
            # — the inner writer still works, we just lose resume.
            logger.warning(
                "ResumableUploadWriter: initial state too large for "
                "upload %s — disabling resume for this upload",
                self.upload_id,
            )
            self._store_available = False
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "ResumableUploadWriter: state store write failed for "
                "upload %s (%s) — disabling resume for this upload",
                self.upload_id,
                exc,
            )
            self._store_available = False

    def _persist_chunk_progress(self) -> None:
        if not self._store_available:
            return
        partial = {
            "chunks_received_ranges": compact_chunks(self._chunks_received),
            "bytes_received": bytes_received_from_ranges(
                compact_chunks(self._chunks_received), self._chunk_size
            ),
            "last_updated": time.time(),
        }
        try:
            updated = self.state_store.update(self.upload_id, partial)
            if updated is None:
                # Entry vanished (TTL expiry mid-upload, or external
                # delete). Re-seed so later resumes still work.
                self.state_store.set(self.upload_id, self._base_state(), ttl=self.ttl)
        except UploadStateTooLarge:
            logger.warning(
                "ResumableUploadWriter: chunk state grew past limit for "
                "upload %s — disabling resume for this upload",
                self.upload_id,
            )
            self._store_available = False
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "ResumableUploadWriter: chunk state update failed for "
                "upload %s (%s) — disabling resume for this upload",
                self.upload_id,
                exc,
            )
            self._store_available = False

    def _delete_state_entry(self) -> None:
        if not self._store_available:
            return
        try:
            self.state_store.delete(self.upload_id)
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "ResumableUploadWriter: state delete failed for %s: %s",
                self.upload_id,
                exc,
            )


# ---------------------------------------------------------------------------
# WS resume helper
# ---------------------------------------------------------------------------


def resolve_resume_request(
    upload_id: str,
    session_key: Optional[str],
    store: Optional[UploadStateStore] = None,
    active_refs: Optional[Callable[[str], bool]] = None,
) -> Dict[str, Any]:
    """Build the payload for an ``upload_resumed`` reply.

    Returns a dict with one of three ``status`` values:

    - ``"resumed"`` — entry exists and session matches; reply contains
      ``bytes_received`` and ``chunks_received``.
    - ``"not_found"`` — no entry, or session mismatch (same response on
      purpose — we don't leak whether the ID exists).
    - ``"locked"`` — another session is actively uploading this ID.
      ``active_refs(upload_id)`` is called to check this; when not
      provided, the check is skipped.
    """
    store = store or get_default_store()
    try:
        entry = store.get(upload_id)
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "resolve_resume_request: state store read failed for %s: %s",
            upload_id,
            exc,
        )
        return {
            "type": "upload_resumed",
            "ref": upload_id,
            "status": "not_found",
            "bytes_received": 0,
            "chunks_received": [],
        }

    if entry is None:
        return {
            "type": "upload_resumed",
            "ref": upload_id,
            "status": "not_found",
            "bytes_received": 0,
            "chunks_received": [],
        }

    stored_session = entry.get("session_key")
    if stored_session is not None and stored_session != session_key:
        # Cross-session access attempt — same response as not_found
        # to avoid leaking existence of other users' upload ids.
        return {
            "type": "upload_resumed",
            "ref": upload_id,
            "status": "not_found",
            "bytes_received": 0,
            "chunks_received": [],
        }

    if active_refs is not None and active_refs(upload_id):
        return {
            "type": "upload_resumed",
            "ref": upload_id,
            "status": "locked",
            "bytes_received": entry.get("bytes_received", 0),
            "chunks_received": expand_ranges(entry.get("chunks_received_ranges", [])),
        }

    return {
        "type": "upload_resumed",
        "ref": upload_id,
        "status": "resumed",
        "bytes_received": entry.get("bytes_received", 0),
        "chunks_received": expand_ranges(entry.get("chunks_received_ranges", [])),
    }


__all__ = [
    "ResumableUploadWriter",
    "compact_chunks",
    "expand_ranges",
    "bytes_received_from_ranges",
    "resolve_resume_request",
]
