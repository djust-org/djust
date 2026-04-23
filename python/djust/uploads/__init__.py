"""
File upload support for djust LiveView.

Inspired by Phoenix LiveView's upload API:
- allow_upload() to configure upload slots
- consume_uploaded_entries() to process completed uploads
- Binary WebSocket frames for chunked file transfer
- Client-side preview and progress tracking

Usage:
    class ProfileView(LiveView, UploadMixin):
        template_name = 'profile.html'

        def mount(self, request, **kwargs):
            self.allow_upload('avatar', accept='.jpg,.png,.webp',
                              max_entries=1, max_file_size=5_000_000)

        def handle_event(self, event, params):
            if event == 'save':
                for entry in self.consume_uploaded_entries('avatar'):
                    path = default_storage.save(f'avatars/{entry.client_name}', entry.file)
                    self.avatar_url = path
"""

import io
import logging
import os
import struct
import tempfile
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Generator, List, Optional, Set, Tuple, Type

logger = logging.getLogger(__name__)

# ============================================================================
# Magic bytes for file type validation
# ============================================================================

MAGIC_BYTES: Dict[str, List[Tuple[bytes, int]]] = {
    # (magic_bytes, offset)
    "image/jpeg": [(b"\xff\xd8\xff", 0)],
    "image/png": [(b"\x89PNG\r\n\x1a\n", 0)],
    "image/gif": [(b"GIF87a", 0), (b"GIF89a", 0)],
    "image/webp": [(b"RIFF", 0), (b"WEBP", 8)],  # Must match both
    "image/svg+xml": [(b"<svg", 0), (b"<?xml", 0)],
    "application/pdf": [(b"%PDF", 0)],
    "application/zip": [(b"PK\x03\x04", 0)],
    "video/mp4": [(b"ftyp", 4)],
    "audio/mpeg": [(b"\xff\xfb", 0), (b"\xff\xf3", 0), (b"\xff\xf2", 0), (b"ID3", 0)],
}

# Extension to MIME type mapping
EXT_TO_MIME: Dict[str, str] = {
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".png": "image/png",
    ".gif": "image/gif",
    ".webp": "image/webp",
    ".svg": "image/svg+xml",
    ".pdf": "application/pdf",
    ".zip": "application/zip",
    ".mp4": "video/mp4",
    ".mp3": "audio/mpeg",
    ".txt": "text/plain",
    ".csv": "text/csv",
    ".json": "application/json",
    ".html": "text/html",
    ".css": "text/css",
    ".js": "application/javascript",
}


def validate_magic_bytes(data: bytes, expected_mime: str) -> bool:
    """
    Validate file content by checking magic bytes against expected MIME type.

    Returns True if magic bytes match, or if we don't have magic byte info
    for the MIME type (permissive for unknown types).
    """
    if not data:
        return False

    signatures = MAGIC_BYTES.get(expected_mime)
    if not signatures:
        # No magic bytes defined for this type — allow it
        return True

    # Need enough data to check signatures
    if len(data) < 16:
        return False

    # Special case for WebP: must match RIFF at 0 AND WEBP at 8
    if expected_mime == "image/webp":
        return data[:4] == b"RIFF" and data[8:12] == b"WEBP"

    # For other types, any signature match is sufficient
    for magic, offset in signatures:
        if data[offset : offset + len(magic)] == magic:
            return True

    return False


def mime_from_extension(filename: str) -> Optional[str]:
    """Get MIME type from file extension."""
    ext = Path(filename).suffix.lower()
    return EXT_TO_MIME.get(ext)


# ============================================================================
# Upload data structures
# ============================================================================

# Binary frame protocol:
# [1 byte: frame_type] [16 bytes: upload_ref UUID] [payload...]
#
# Frame types:
#   0x01 = chunk data:    [frame_type][ref][4 bytes: chunk_index][chunk_data]
#   0x02 = upload complete: [frame_type][ref]
#   0x03 = cancel:         [frame_type][ref]

FRAME_CHUNK = 0x01
FRAME_COMPLETE = 0x02
FRAME_CANCEL = 0x03

# Header: 1 byte type + 16 bytes UUID
FRAME_HEADER_SIZE = 17


def parse_upload_frame(data: bytes) -> Optional[Dict[str, Any]]:
    """Parse a binary upload frame from the WebSocket."""
    if len(data) < FRAME_HEADER_SIZE:
        return None

    frame_type = data[0]
    ref_bytes = data[1:17]

    try:
        ref = str(uuid.UUID(bytes=ref_bytes))
    except ValueError:
        return None

    if frame_type == FRAME_CHUNK:
        if len(data) < FRAME_HEADER_SIZE + 4:
            return None
        chunk_index = struct.unpack(">I", data[17:21])[0]
        chunk_data = data[21:]
        return {
            "type": "chunk",
            "ref": ref,
            "chunk_index": chunk_index,
            "data": chunk_data,
        }
    elif frame_type == FRAME_COMPLETE:
        return {"type": "complete", "ref": ref}
    elif frame_type == FRAME_CANCEL:
        return {"type": "cancel", "ref": ref}

    return None


def build_progress_message(ref: str, progress: int, status: str = "uploading") -> Dict[str, Any]:
    """Build a progress update message to send to the client."""
    return {
        "type": "upload_progress",
        "ref": ref,
        "progress": progress,  # 0-100
        "status": status,  # uploading, complete, error, cancelled
    }


# ============================================================================
# UploadWriter — direct-to-destination byte stream
# ============================================================================


class UploadWriter:
    """Base class for direct-to-destination upload writers.

    Subclass this to pipe raw upload chunks directly into S3, GCS, Azure
    Blob, etc. without buffering to disk. A writer instance is created
    lazily per upload on the first chunk.

    Lifecycle (all sync):

        1. __init__(upload_id, filename, content_type, expected_size)
           Instantiated per upload on the first chunk.
        2. open()
           Called exactly once, before the first write_chunk(). May raise to
           reject the upload; the exception is passed to abort().
        3. write_chunk(chunk: bytes)
           Called once per WebSocket binary frame with the raw bytes. May
           raise to abort; the exception is passed to abort().
        4. close() -> Any
           Called on successful upload completion. The return value is stored
           on the UploadEntry as ``writer_result`` and is template-accessible
           (e.g. ``{{ entry.writer_result.url }}``).
        5. abort(error)
           Called on any failure path (open/write_chunk raised, client
           cancelled, transport disconnected, size limit exceeded). The
           error argument is the raw exception object.

    Important guarantees:

    - ``open()`` is called at most once.
    - ``write_chunk()`` is never called before ``open()`` succeeds.
    - After ``close()`` returns, no further methods are invoked.
    - After ``abort()`` returns, no further methods are invoked.
    - Writer instances are isolated per upload — no shared state.
    - ``abort()`` must not raise; any exception is logged and swallowed to
      avoid leaking cleanup failures into the request path.
    """

    def __init__(
        self,
        upload_id: str,
        filename: str,
        content_type: str,
        expected_size: Optional[int] = None,
    ):
        self.upload_id = upload_id
        self.filename = filename
        self.content_type = content_type
        self.expected_size = expected_size

    def open(self) -> None:
        """Called once before the first write_chunk(). Override to set up
        resources (e.g. start an S3 multipart upload)."""
        return None

    def write_chunk(self, chunk: bytes) -> None:
        """Called once per WebSocket binary frame. Must be overridden."""
        raise NotImplementedError("UploadWriter subclasses must implement write_chunk()")

    def close(self) -> Any:
        """Called on upload completion. The return value is stored on
        ``UploadEntry.writer_result`` and surfaced to templates + JS via
        ``get_upload_state``.

        **The return value MUST be JSON-serializable.** The websocket
        state-update pathway serializes every upload entry to the client
        using ``DjangoJSONEncoder``; a non-JSON-serializable return (e.g.
        a live boto3 client object, an open file handle, a custom class
        without ``__json__``) will raise ``TypeError`` when the next state
        update is emitted — *after* the upload has already succeeded.

        Safe return shapes:

        - ``None``
        - Primitive scalars (``str``, ``int``, ``float``, ``bool``)
        - Lists / tuples / dicts of the above (recursively)
        - Objects that ``DjangoJSONEncoder`` can serialize (``datetime``,
          ``Decimal``, ``UUID``, ``Promise``, etc.)

        A good pattern is to return a ``dict`` describing the upload
        outcome — URL, ETag, size, storage key — not the underlying
        client handle.
        """
        return None

    def abort(self, error: BaseException) -> None:
        """Called on any failure path with the raw exception. Override to
        clean up partial state (e.g. S3 AbortMultipartUpload). Must not
        raise."""
        return None


# Cache mapping writer class → accepts-chunk-index? Keeps the per-chunk
# dispatch path fast (no inspect.signature() call on every chunk).
_WRITER_ACCEPTS_CHUNK_INDEX_CACHE: Dict[Type[UploadWriter], bool] = {}


def _writer_accepts_chunk_index(writer: UploadWriter) -> bool:
    """Return True if ``writer.write_chunk`` accepts a ``chunk_index`` kwarg.

    Introspects the bound ``write_chunk`` method once per class and
    caches the result. Resumable writers declare
    ``write_chunk(self, chunk, chunk_index=0)``; legacy writers declare
    ``write_chunk(self, chunk)`` and we keep calling them that way so
    existing third-party writer subclasses keep working.
    """
    import inspect

    cls = type(writer)
    cached = _WRITER_ACCEPTS_CHUNK_INDEX_CACHE.get(cls)
    if cached is not None:
        return cached
    try:
        sig = inspect.signature(writer.write_chunk)
    except (TypeError, ValueError):
        _WRITER_ACCEPTS_CHUNK_INDEX_CACHE[cls] = False
        return False
    accepts = "chunk_index" in sig.parameters or any(
        p.kind is inspect.Parameter.VAR_KEYWORD for p in sig.parameters.values()
    )
    _WRITER_ACCEPTS_CHUNK_INDEX_CACHE[cls] = accepts
    return accepts


class BufferedUploadWriter(UploadWriter):
    """UploadWriter helper that accumulates chunks until a configurable
    threshold, then emits fixed-size parts.

    Clients send whatever chunk size they send (today: 64 KB). S3 multipart
    upload requires each part except the last to be >= 5 MB. Subclass
    ``BufferedUploadWriter`` and override ``on_part(part, part_num)`` and
    ``on_complete()`` to get 5-MB-aligned parts without worrying about the
    raw client chunk size.

    Example (S3)::

        from pathlib import Path
        from uuid import uuid4

        class S3Writer(BufferedUploadWriter):
            buffer_threshold = 5 * 1024 * 1024  # 5 MB — S3 MPU min

            def open(self):
                # SECURITY: self.filename is client-supplied and attacker-
                # controlled. Scope to a server-generated namespace; never
                # use self.filename verbatim as a destination key/path.
                safe = Path(self.filename).name
                self._key = f"uploads/{uuid4()}-{safe}"
                self._s3 = boto3.client("s3")
                self._mpu = self._s3.create_multipart_upload(
                    Bucket="my-bucket", Key=self._key
                )
                self._parts = []

            def on_part(self, part, part_num):
                resp = self._s3.upload_part(
                    Bucket="my-bucket",
                    Key=self._key,
                    UploadId=self._mpu["UploadId"],
                    PartNumber=part_num,
                    Body=part,
                )
                self._parts.append({"ETag": resp["ETag"], "PartNumber": part_num})

            def on_complete(self):
                self._s3.complete_multipart_upload(
                    Bucket="my-bucket",
                    Key=self._key,
                    UploadId=self._mpu["UploadId"],
                    MultipartUpload={"Parts": self._parts},
                )
                return {"key": self._key, "url": f"s3://my-bucket/{self._key}"}

            def abort(self, error):
                if getattr(self, "_mpu", None):
                    self._s3.abort_multipart_upload(
                        Bucket="my-bucket",
                        Key=self._key,
                        UploadId=self._mpu["UploadId"],
                    )
    """

    # Default threshold = S3 multipart upload minimum part size.
    buffer_threshold: int = 5 * 1024 * 1024

    def __init__(
        self,
        upload_id: str,
        filename: str,
        content_type: str,
        expected_size: Optional[int] = None,
    ):
        super().__init__(upload_id, filename, content_type, expected_size)
        self._buf = bytearray()
        self._part_num = 0
        self._finalized = False

    def write_chunk(self, chunk: bytes) -> None:
        """Buffer the chunk; emit full parts while buffer exceeds threshold.

        Raises ``RuntimeError`` if called after ``close()`` has returned —
        the upload manager is contracted not to do this (post-close calls
        are treated as a framework bug, not a user error).
        """
        if self._finalized:
            raise RuntimeError(
                "write_chunk() called on a finalized BufferedUploadWriter — "
                "the upload manager is expected to stop dispatching after close()"
            )
        self._buf.extend(chunk)
        threshold = self.buffer_threshold
        while len(self._buf) >= threshold:
            part = bytes(self._buf[:threshold])
            del self._buf[:threshold]
            self._part_num += 1
            self.on_part(part, self._part_num)

    def close(self) -> Any:
        """Flush any remaining buffered bytes as a final partial part,
        then call on_complete() and return its value.

        Repeated ``close()`` calls are a no-op — once finalized, the writer
        returns the same ``on_complete()`` snapshot without re-emitting the
        final partial part.
        """
        if self._finalized:
            return self.on_complete()
        if self._buf:
            self._part_num += 1
            final_part = bytes(self._buf)
            self._buf = bytearray()
            self.on_part(final_part, self._part_num)
        self._finalized = True
        return self.on_complete()

    def on_part(self, part: bytes, part_num: int) -> None:
        """Override to handle each part (S3-min-sized except the last)."""
        return None

    def on_complete(self) -> Any:
        """Override to finalize the upload and return a result (e.g. URL)."""
        return None


@dataclass
class UploadConfig:
    """Configuration for an upload slot."""

    name: str
    accept: str = ""  # Comma-separated extensions: ".jpg,.png"
    max_entries: int = 1
    max_file_size: int = 10_000_000  # 10MB default
    chunk_size: int = 64 * 1024  # 64KB chunks
    auto_upload: bool = True  # Start upload immediately on file selection
    accepted_extensions: Set[str] = field(default_factory=set)
    accepted_mimes: Set[str] = field(default_factory=set)
    writer: Optional[Type[UploadWriter]] = None  # If set, bypass disk buffering
    # v0.5.7 (#821) — opt-in resumable upload protocol. When True, the
    # UploadManager wraps the configured writer (or uses a default
    # tempfile-backed resumable writer if none is set) so chunks are
    # persisted into an external state store, surviving WS disconnects.
    # See docs/adr/010-resumable-uploads.md.
    resumable: bool = False

    def __post_init__(self):
        if self.accept:
            for part in self.accept.split(","):
                part = part.strip().lower()
                if part.startswith("."):
                    self.accepted_extensions.add(part)
                    mime = EXT_TO_MIME.get(part)
                    if mime:
                        self.accepted_mimes.add(mime)
                elif "/" in part:
                    self.accepted_mimes.add(part)

    def validate_extension(self, filename: str) -> bool:
        """Check if filename extension is in accepted list."""
        if not self.accepted_extensions:
            return True  # No restriction
        ext = Path(filename).suffix.lower()
        return ext in self.accepted_extensions

    def validate_mime(self, mime_type: str) -> bool:
        """Check if MIME type is accepted."""
        if not self.accepted_mimes:
            return True
        # Support wildcards like "image/*"
        for accepted in self.accepted_mimes:
            if accepted == mime_type:
                return True
            if accepted.endswith("/*"):
                category = accepted.split("/")[0]
                if mime_type.startswith(category + "/"):
                    return True
        return False


@dataclass
class UploadEntry:
    """Represents a single uploaded file."""

    ref: str
    upload_name: str  # The upload slot name (e.g., 'avatar')
    client_name: str  # Original filename
    client_type: str  # MIME type from client
    client_size: int  # Expected size from client
    _chunks: Dict[int, bytes] = field(default_factory=dict, repr=False)
    _temp_path: Optional[str] = field(default=None, repr=False)
    _total_received: int = field(default=0, repr=False)
    _complete: bool = field(default=False, repr=False)
    _error: Optional[str] = field(default=None, repr=False)
    _created_at: float = field(default_factory=time.time, repr=False)
    # Writer path — populated when the upload slot was configured with a
    # writer= kwarg. writer_instance holds the live per-upload writer;
    # writer_result holds the value returned from writer.close() and is
    # template-accessible as entry.writer_result.
    writer_instance: Optional[UploadWriter] = field(default=None, repr=False)
    writer_result: Any = field(default=None, repr=False)
    _writer_opened: bool = field(default=False, repr=False)
    _writer_aborted: bool = field(default=False, repr=False)

    @property
    def data(self) -> bytes:
        """Get the complete file data as bytes."""
        if self._temp_path and os.path.exists(self._temp_path):
            with open(self._temp_path, "rb") as f:
                return f.read()
        # Reassemble from chunks
        result = bytearray()
        for i in sorted(self._chunks.keys()):
            result.extend(self._chunks[i])
        return bytes(result)

    @property
    def file(self) -> io.BytesIO:
        """Get the file as a file-like object (BytesIO)."""
        return io.BytesIO(self.data)

    @property
    def progress(self) -> int:
        """Upload progress as percentage (0-100)."""
        if self.client_size == 0:
            return 100
        return min(100, int(self._total_received / self.client_size * 100))

    @property
    def complete(self) -> bool:
        return self._complete

    @property
    def error(self) -> Optional[str]:
        return self._error

    def add_chunk(self, chunk_index: int, data: bytes) -> None:
        """Add a chunk of data."""
        self._chunks[chunk_index] = data
        self._total_received += len(data)

    def finalize(self, temp_dir: str) -> bool:
        """
        Write chunks to temp file and validate.

        Returns True if validation passed.
        """
        # Reassemble data
        data = self.data

        # Size check
        if len(data) > self.client_size * 1.1:  # 10% tolerance for encoding
            self._error = f"File too large: {len(data)} bytes (max {self.client_size})"
            return False

        # Magic bytes validation
        expected_mime = self.client_type or mime_from_extension(self.client_name)
        if expected_mime and not validate_magic_bytes(data, expected_mime):
            self._error = f"File content doesn't match expected type: {expected_mime}"
            return False

        # Write to temp file
        try:
            fd, path = tempfile.mkstemp(dir=temp_dir, suffix=Path(self.client_name).suffix)
            with os.fdopen(fd, "wb") as f:
                f.write(data)
            self._temp_path = path
            self._chunks.clear()  # Free memory
            self._complete = True
            return True
        except OSError as e:
            self._error = f"Failed to write temp file: {e}"
            return False

    def cleanup(self) -> None:
        """Remove temp file."""
        if self._temp_path and os.path.exists(self._temp_path):
            try:
                os.unlink(self._temp_path)
            except OSError:
                pass  # Best-effort cleanup; temp file may already be removed
        self._chunks.clear()


# ============================================================================
# Upload Manager (per-session state)
# ============================================================================


class UploadManager:
    """
    Manages upload state for a single LiveView session.

    Handles:
    - Upload slot configuration
    - Chunk reassembly
    - Temp file management
    - Cleanup on disconnect
    """

    def __init__(self, temp_dir: Optional[str] = None):
        self._configs: Dict[str, UploadConfig] = {}
        self._entries: Dict[str, UploadEntry] = {}  # ref -> UploadEntry
        self._name_to_refs: Dict[str, List[str]] = {}  # upload_name -> [refs]
        self._temp_dir = temp_dir or tempfile.mkdtemp(prefix="djust_uploads_")
        self._csrf_token: Optional[str] = None

    def configure(
        self,
        name: str,
        accept: str = "",
        max_entries: int = 1,
        max_file_size: int = 10_000_000,
        chunk_size: int = 64 * 1024,
        auto_upload: bool = True,
        writer: Optional[Type[UploadWriter]] = None,
        resumable: bool = False,
    ) -> UploadConfig:
        """Configure an upload slot."""
        config = UploadConfig(
            name=name,
            accept=accept,
            max_entries=max_entries,
            max_file_size=max_file_size,
            chunk_size=chunk_size,
            auto_upload=auto_upload,
            writer=writer,
            resumable=resumable,
        )
        self._configs[name] = config
        if name not in self._name_to_refs:
            self._name_to_refs[name] = []
        return config

    # ------------------------------------------------------------------
    # Writer path
    # ------------------------------------------------------------------

    def _safe_abort_writer(self, entry: UploadEntry, error: BaseException) -> None:
        """Call writer.abort(error), swallowing (but logging) any exception.

        abort() is a cleanup hook and must never itself propagate — a
        failing S3 AbortMultipartUpload is a cleanup problem, not a
        correctness problem for the rest of the view.
        """
        writer = entry.writer_instance
        if writer is None or entry._writer_aborted:
            return
        entry._writer_aborted = True
        try:
            writer.abort(error)
        except Exception:  # noqa: BLE001 — abort() must never raise
            logger.exception("UploadWriter.abort() raised for upload %s", entry.ref)

    def _add_chunk_via_writer(
        self,
        entry: UploadEntry,
        config: UploadConfig,
        data: bytes,
        chunk_index: int = 0,
    ) -> bool:
        """Route a chunk through the writer. Returns True on success.

        Lazily instantiates the writer and calls open() on the first chunk.
        On any exception from open() or write_chunk(), calls writer.abort()
        and marks the entry failed.

        ``chunk_index`` is forwarded to writers whose ``write_chunk``
        signature accepts it (e.g. :class:`~djust.uploads.resumable.
        ResumableUploadWriter`). Writers with the base
        ``write_chunk(self, chunk)`` signature see the raw bytes only.
        """
        writer_cls = config.writer
        assert writer_cls is not None  # caller guards
        if entry.writer_instance is None:
            try:
                entry.writer_instance = writer_cls(
                    upload_id=entry.ref,
                    filename=entry.client_name,
                    content_type=entry.client_type,
                    expected_size=entry.client_size,
                )
            except Exception as exc:  # noqa: BLE001
                # Do NOT surface the raw exception message to the client —
                # writer implementations may embed IAM ARNs, bucket names,
                # or endpoint URLs in their exceptions. Log the full trace
                # server-side for debugging; return a generic message to the
                # client.
                entry._error = "Upload writer failed to initialize"
                logger.exception(
                    "UploadWriter instantiation failed for upload %s: %s",
                    entry.ref,
                    exc,
                )
                return False
        if not entry._writer_opened:
            try:
                entry.writer_instance.open()
            except Exception as exc:  # noqa: BLE001
                entry._error = "Upload writer failed to initialize"
                logger.exception(
                    "UploadWriter.open() raised for upload %s: %s",
                    entry.ref,
                    exc,
                )
                self._safe_abort_writer(entry, exc)
                return False
            entry._writer_opened = True
        try:
            # Forward chunk_index to writers that accept it (e.g.
            # ResumableUploadWriter). Base UploadWriter subclasses with
            # ``write_chunk(self, chunk)`` keep the original 1-arg
            # signature — we detect the accept-chunk-index variant via
            # a quick signature peek and cache the result on the
            # writer class to keep the per-chunk path fast.
            writer = entry.writer_instance
            if _writer_accepts_chunk_index(writer):
                writer.write_chunk(data, chunk_index=chunk_index)
            else:
                writer.write_chunk(data)
        except Exception as exc:  # noqa: BLE001
            entry._error = "Upload writer rejected chunk"
            logger.exception(
                "UploadWriter.write_chunk() raised for upload %s: %s",
                entry.ref,
                exc,
            )
            self._safe_abort_writer(entry, exc)
            return False
        entry._total_received += len(data)
        return True

    def _finalize_writer(self, entry: UploadEntry) -> bool:
        """Call writer.close() and store the return value on
        entry.writer_result. Returns True on success.

        The return value is validated as JSON-serializable (see
        ``UploadWriter.close`` docstring). A non-serializable return is
        logged at ERROR level and the upload is aborted, so a bad writer
        implementation surfaces immediately instead of causing a
        downstream state-update failure.
        """
        import json as _json

        from django.core.serializers.json import DjangoJSONEncoder

        writer = entry.writer_instance
        if writer is None:
            return False
        try:
            result = writer.close()
        except Exception as exc:  # noqa: BLE001
            entry._error = "Upload writer failed to finalize"
            logger.exception(
                "UploadWriter.close() raised for upload %s: %s",
                entry.ref,
                exc,
            )
            self._safe_abort_writer(entry, exc)
            return False

        # Defensive: validate close() return is JSON-serializable before
        # storing it on the entry. If it's not, the next state update
        # would raise TypeError AFTER the upload has already succeeded —
        # surface the misconfiguration loudly here instead.
        try:
            _json.dumps(result, cls=DjangoJSONEncoder)
        except (TypeError, ValueError) as exc:
            entry._error = (
                "UploadWriter.close() returned a non-JSON-serializable value. "
                "Return a dict / list / primitive describing the outcome, "
                "not a client handle."
            )
            logger.error(
                "UploadWriter.close() return not JSON-serializable for upload %s (writer=%s): %s",
                entry.ref,
                type(writer).__name__,
                exc,
            )
            self._safe_abort_writer(entry, exc)
            return False

        entry.writer_result = result
        entry._complete = True
        return True

    def get_config(self, name: str) -> Optional[UploadConfig]:
        return self._configs.get(name)

    def register_entry(
        self,
        upload_name: str,
        ref: str,
        client_name: str,
        client_type: str,
        client_size: int,
    ) -> Optional[UploadEntry]:
        """
        Register a new upload entry. Called when client announces a file selection.

        Returns the entry, or None if validation fails.
        """
        config = self._configs.get(upload_name)
        if not config:
            logger.warning("No upload config for '%s'", upload_name)
            return None

        # Check max entries
        current_refs = self._name_to_refs.get(upload_name, [])
        active_refs = [
            r for r in current_refs if r in self._entries and not self._entries[r].complete
        ]
        if len(active_refs) >= config.max_entries:
            logger.warning("Max entries (%d) reached for '%s'", config.max_entries, upload_name)
            return None

        # Check file size
        if client_size > config.max_file_size:
            logger.warning("File too large: %d > %d", client_size, config.max_file_size)
            return None

        # Check extension
        if not config.validate_extension(client_name):
            logger.warning("Extension not accepted: %s", client_name)
            return None

        # Check MIME
        if client_type and not config.validate_mime(client_type):
            logger.warning("MIME type not accepted: %s", client_type)
            return None

        entry = UploadEntry(
            ref=ref,
            upload_name=upload_name,
            client_name=client_name,
            client_type=client_type,
            client_size=client_size,
        )
        self._entries[ref] = entry
        self._name_to_refs.setdefault(upload_name, []).append(ref)
        return entry

    def add_chunk(self, ref: str, chunk_index: int, data: bytes) -> Optional[int]:
        """
        Add a chunk to an upload.

        Returns progress (0-100) or None if ref not found or the chunk
        was rejected (size limit, writer error).

        When the upload slot is configured with writer=, the chunk is
        piped to the writer and nothing is buffered to RAM or disk.
        """
        entry = self._entries.get(ref)
        if not entry:
            return None

        # Fast-path: once an upload has been aborted (size limit, writer
        # rejection, etc.) any in-flight chunks from the client still
        # arrive for some round-trips before the client observes the
        # error. Drop them at a single DEBUG log rather than re-entering
        # the abort path / writer teardown for every trailing chunk.
        # (#824: a real "stop sending" push-event to the client is a
        # separate, larger work item — tracked as a follow-up.)
        if entry._error is not None or entry._writer_aborted:
            logger.debug(
                "Dropping chunk for already-aborted upload %s (error=%r)",
                ref,
                entry._error,
            )
            return None

        config = self._configs.get(entry.upload_name)
        if config and entry._total_received + len(data) > config.max_file_size:
            err = ValueError(
                "File size exceeds limit (%d bytes)" % config.max_file_size,
            )
            entry._error = "File size exceeds limit"
            if config.writer is not None:
                self._safe_abort_writer(entry, err)
            return None

        if config is not None and config.writer is not None:
            if not self._add_chunk_via_writer(entry, config, data, chunk_index=chunk_index):
                return None
            return entry.progress

        entry.add_chunk(chunk_index, data)
        return entry.progress

    def complete_upload(self, ref: str) -> Optional[UploadEntry]:
        """
        Mark upload as complete and finalize.

        For writer-configured slots, calls writer.close() and stores its
        return value on entry.writer_result. For disk-buffered slots,
        writes chunks to a temp file and runs magic-byte validation.

        Returns the entry if successful, None if finalization failed.
        """
        entry = self._entries.get(ref)
        if not entry:
            return None

        config = self._configs.get(entry.upload_name)
        if config is not None and config.writer is not None:
            if self._finalize_writer(entry):
                return entry
            logger.warning("Upload writer finalize failed for %s: %s", ref, entry.error)
            return None

        if entry.finalize(self._temp_dir):
            return entry
        else:
            logger.warning("Upload validation failed for %s: %s", ref, entry.error)
            return None

    def cancel_upload(self, ref: str) -> None:
        """Cancel and clean up an upload.

        For writer-configured slots, calls writer.abort() with a
        ConnectionAbortedError so the writer can release server-side
        resources (e.g. S3 AbortMultipartUpload).
        """
        entry = self._entries.pop(ref, None)
        if entry:
            if entry.writer_instance is not None and not entry._complete:
                self._safe_abort_writer(entry, ConnectionAbortedError("upload cancelled"))
            entry.cleanup()
            refs = self._name_to_refs.get(entry.upload_name, [])
            if ref in refs:
                refs.remove(ref)

    def consume_entries(self, upload_name: str) -> Generator[UploadEntry, None, None]:
        """
        Consume completed upload entries for a given upload slot.

        Yields UploadEntry objects and removes them from the manager.
        This is the primary API for processing uploads in event handlers.
        """
        refs = self._name_to_refs.get(upload_name, [])
        consumed = []

        for ref in refs:
            entry = self._entries.get(ref)
            if entry and entry.complete:
                yield entry
                consumed.append(ref)

        # Remove consumed entries
        for ref in consumed:
            entry = self._entries.pop(ref, None)
            if entry:
                entry.cleanup()
            if ref in refs:
                refs.remove(ref)

    def get_entries(self, upload_name: str) -> List[UploadEntry]:
        """Get all entries (including in-progress) for an upload slot."""
        refs = self._name_to_refs.get(upload_name, [])
        return [self._entries[r] for r in refs if r in self._entries]

    def get_upload_state(self) -> Dict[str, Any]:
        """
        Get upload state for template rendering.

        Returns a dict suitable for template context:
        {
            'avatar': {
                'config': {...},
                'entries': [...],
                'errors': [...]
            }
        }
        """
        state = {}
        for name, config in self._configs.items():
            entries = self.get_entries(name)
            state[name] = {
                "config": {
                    "name": config.name,
                    "accept": config.accept,
                    "max_entries": config.max_entries,
                    "max_file_size": config.max_file_size,
                    "chunk_size": config.chunk_size,
                    "auto_upload": config.auto_upload,
                    "resumable": config.resumable,
                },
                "entries": [
                    {
                        "ref": e.ref,
                        "client_name": e.client_name,
                        "client_type": e.client_type,
                        "client_size": e.client_size,
                        "progress": e.progress,
                        "complete": e.complete,
                        "error": e.error,
                        "writer_result": e.writer_result,
                    }
                    for e in entries
                ],
                "errors": [e.error for e in entries if e.error],
            }
        return state

    def cleanup(self) -> None:
        """Clean up all uploads and temp directory."""
        for entry in self._entries.values():
            if entry.writer_instance is not None and not entry._complete:
                self._safe_abort_writer(entry, ConnectionAbortedError("session closed"))
            entry.cleanup()
        self._entries.clear()
        self._name_to_refs.clear()

        # Remove temp directory if empty
        try:
            if self._temp_dir and os.path.isdir(self._temp_dir):
                os.rmdir(self._temp_dir)
        except OSError:
            pass  # Directory not empty, leave it


# ============================================================================
# UploadMixin — mix into LiveView classes
# ============================================================================


class UploadMixin:
    """
    Mixin that adds file upload support to a LiveView.

    Usage:
        class MyView(LiveView, UploadMixin):
            def mount(self, request, **kwargs):
                self.allow_upload('avatar', accept='.jpg,.png', max_entries=1,
                                  max_file_size=5_000_000)

            def save(self):
                for entry in self.consume_uploaded_entries('avatar'):
                    # entry.client_name, entry.client_type, entry.data, entry.file
                    save_file(entry)
    """

    _upload_manager: Optional[UploadManager] = None
    # Issue #889: record each ``allow_upload()`` call as a JSON-
    # serializable dict so the WS consumer's state-restoration path
    # (which skips ``mount()``) can replay them and rebuild the
    # ``UploadManager``. The live ``_upload_manager`` instance itself
    # is not JSON-serializable and is silently dropped by
    # ``_get_private_state()``; this replay list is.
    _upload_configs_saved: Optional[List[Dict[str, Any]]] = None
    # Issue #892: version tag for saved config dicts. Bumped whenever
    # the replay contract changes (new kwarg added/renamed/removed on
    # ``allow_upload``). ``_restore_upload_configs`` uses this to
    # decide how defensively to replay — unknown / older versions fall
    # back to the "bare-minimum replay" path. See ADR-009.
    _upload_configs_version: int = 1

    def _ensure_upload_manager(self) -> UploadManager:
        if self._upload_manager is None:
            self._upload_manager = UploadManager()
        return self._upload_manager

    def allow_upload(
        self,
        name: str,
        accept: str = "",
        max_entries: int = 1,
        max_file_size: int = 10_000_000,
        chunk_size: int = 64 * 1024,
        auto_upload: bool = True,
        writer: Optional[Type[UploadWriter]] = None,
        resumable: bool = False,
    ) -> UploadConfig:
        """
        Configure a named upload slot.

        Args:
            name: Upload slot name (referenced in templates as dj-upload="name")
            accept: Comma-separated accepted file extensions (e.g., ".jpg,.png")
            max_entries: Maximum number of files for this slot
            max_file_size: Maximum file size in bytes (default 10MB)
            chunk_size: Chunk size for transfer (default 64KB)
            auto_upload: Start upload immediately on selection
            writer: Optional ``UploadWriter`` subclass. When set, chunks
                bypass disk buffering and are piped directly through the
                writer's ``write_chunk()`` (e.g. straight to S3). See
                ``UploadWriter`` and ``BufferedUploadWriter`` in
                ``djust.uploads``. When omitted (default), uploads are
                buffered to a temp file and validated by magic bytes.
                ⚠️ Not session-serializable — views that use a
                non-default writer and rely on the HTTP→WS state
                restoration path (the default production flow) will
                get a warning at restoration time and fall back to the
                buffered writer. If you need a custom writer, ensure
                your app uses a fresh-mount WS flow.

        Returns:
            UploadConfig object
        """
        mgr = self._ensure_upload_manager()
        cfg = mgr.configure(
            name=name,
            accept=accept,
            max_entries=max_entries,
            max_file_size=max_file_size,
            chunk_size=chunk_size,
            auto_upload=auto_upload,
            writer=writer,
            resumable=resumable,
        )
        # Issue #889: track the call args in a JSON-serializable list
        # so the WS consumer's state-restoration path can replay them.
        # ``writer=`` is intentionally excluded — classes don't
        # JSON-serialize; documented above.
        if self._upload_configs_saved is None:
            self._upload_configs_saved = []
        self._upload_configs_saved.append(
            {
                "name": name,
                "accept": accept,
                "max_entries": max_entries,
                "max_file_size": max_file_size,
                "chunk_size": chunk_size,
                "auto_upload": auto_upload,
                "resumable": resumable,
                # writer deliberately omitted — see docstring caveat.
                "_had_writer": writer is not None,
                # Issue #892: tag each saved dict with the schema
                # version that produced it, so restores in a later
                # djust version can decide how defensively to replay.
                "_version": self._upload_configs_version,
            }
        )
        return cfg

    def _restore_upload_configs(self) -> None:
        """Replay the ``allow_upload()`` call list to rebuild the manager.

        Called by the WebSocket consumer's state-restoration path (when
        ``mount()`` is skipped because pre-rendered session state
        exists). Without this, ``_upload_manager`` stays at the class-
        default ``None`` and any upload request fails with "No uploads
        configured for this view" (issue #889).

        If any recorded call originally used a custom ``writer=``
        class, log a warning at restoration time — the writer class
        isn't session-round-trippable, so the restored config falls
        back to the default buffered writer. Apps relying on custom
        writers should arrange for a fresh-mount WS flow.

        Schema-change defensive replay (issue #892): if a saved dict
        contains kwargs the *current* ``allow_upload`` signature doesn't
        recognize (djust upgrade between session save and WS connect),
        or is missing a now-required kwarg, the per-dict ``**kwargs``
        call raises ``TypeError``. We catch it, log a WARNING with the
        slot + the mismatched keys, and fall back to
        ``allow_upload(name)`` — bare-minimum replay — so uploads for
        that slot still work, just without the custom config (extension
        filter, size limit, etc.). Better than killing the whole
        restoration and losing every other slot on the page. See
        ADR-009 for the pattern.
        """
        saved = self._upload_configs_saved
        if not saved:
            return
        # Reset first so the replayed ``allow_upload`` calls re-populate
        # the list on this instance — keeps the bookkeeping consistent
        # across subsequent round-trips.
        self._upload_configs_saved = None
        for cfg in saved:
            # Strip bookkeeping-only keys before the ``**cfg`` splat —
            # they're not ``allow_upload`` kwargs.
            cfg.pop("_version", None)
            had_writer = cfg.pop("_had_writer", False)
            if had_writer:
                logger.warning(
                    "UploadMixin._restore_upload_configs: upload slot %r was "
                    "originally configured with a custom writer= class, but "
                    "writer classes are not session-serializable. Falling "
                    "back to the default buffered writer. See issue #889 "
                    "caveats.",
                    cfg.get("name"),
                )
            try:
                self.allow_upload(**cfg)
            except TypeError as exc:
                # Schema drift — djust version changed between save and
                # restore. Fall back to a bare-minimum replay so the
                # slot still works (issue #892).
                slot_name = cfg.get("name")
                logger.warning(
                    "UploadMixin._restore_upload_configs: saved config for slot "
                    "%r has kwargs incompatible with the current "
                    "allow_upload() signature (%s) — falling back to "
                    "allow_upload(%r) with defaults. See issue #892 / "
                    "ADR-009.",
                    slot_name,
                    exc,
                    slot_name,
                )
                if not slot_name:
                    # Can't even recover a slot name — skip entirely,
                    # don't want to crash the restore path.
                    continue
                try:
                    self.allow_upload(slot_name)
                except Exception as fallback_exc:  # noqa: BLE001
                    logger.warning(
                        "UploadMixin._restore_upload_configs: fallback "
                        "allow_upload(%r) also failed: %s",
                        slot_name,
                        fallback_exc,
                    )

    def consume_uploaded_entries(self, name: str) -> Generator[UploadEntry, None, None]:
        """
        Consume completed upload entries for the named slot.

        Yields UploadEntry objects. After iteration, entries are cleaned up.

        Args:
            name: Upload slot name

        Yields:
            UploadEntry objects with .client_name, .client_type, .data, .file
        """
        if self._upload_manager:
            yield from self._upload_manager.consume_entries(name)

    def cancel_upload(self, name: str, ref: str) -> None:
        """Cancel a specific upload by ref."""
        if self._upload_manager:
            self._upload_manager.cancel_upload(ref)

    def get_uploads(self, name: str) -> List[UploadEntry]:
        """Get all entries (including in-progress) for an upload slot."""
        if self._upload_manager:
            return self._upload_manager.get_entries(name)
        return []

    def _get_upload_context(self) -> Dict[str, Any]:
        """Get upload state for template context."""
        if self._upload_manager:
            return {"uploads": self._upload_manager.get_upload_state()}
        return {}

    def _cleanup_uploads(self) -> None:
        """Clean up all uploads. Called on disconnect."""
        if self._upload_manager:
            self._upload_manager.cleanup()
            self._upload_manager = None


# ============================================================================
# Object-storage error taxonomy (re-exported from djust.contrib.uploads.errors)
# ============================================================================
# Surfaces ``UploadError`` and friends at ``djust.uploads.UploadError`` so
# application code can ``except UploadError`` without importing the
# contrib subpackage (which may pull in optional vendor SDKs in its own
# submodules). The contrib package is the source of truth; this import
# is purely re-export for API convenience.

from djust.contrib.uploads.errors import (  # noqa: E402  — re-export
    UploadCredentialError,
    UploadError,
    UploadNetworkError,
    UploadQuotaError,
)

__all__ = [
    # Pre-existing public surface
    "UploadWriter",
    "BufferedUploadWriter",
    "UploadConfig",
    "UploadEntry",
    "UploadManager",
    "UploadMixin",
    "validate_magic_bytes",
    "mime_from_extension",
    "parse_upload_frame",
    "build_progress_message",
    # v0.5.7 — object-storage error taxonomy (#820, #822)
    "UploadError",
    "UploadNetworkError",
    "UploadCredentialError",
    "UploadQuotaError",
]

# Re-export resumable bits so ``from djust.uploads import
# ResumableUploadWriter`` works — the submodules are the source of
# truth, this is just convenience.
try:
    from .resumable import ResumableUploadWriter, resolve_resume_request  # noqa: F401  — re-export

    __all__.extend(["ResumableUploadWriter", "resolve_resume_request"])
except ImportError:
    # Resumable subsystem depends on nothing optional today, but guard
    # the import so a partial install can't break the base package.
    pass

try:
    from .storage import (  # noqa: F401  — re-export
        InMemoryUploadState,
        RedisUploadState,
        UploadStateStore,
        UploadStateTooLarge,
        get_default_store,
        set_default_store,
    )

    __all__.extend(
        [
            "InMemoryUploadState",
            "RedisUploadState",
            "UploadStateStore",
            "UploadStateTooLarge",
            "get_default_store",
            "set_default_store",
        ]
    )
except ImportError:
    pass
