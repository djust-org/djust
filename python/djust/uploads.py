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
        UploadEntry.writer_result (template-accessible)."""
        return None

    def abort(self, error: BaseException) -> None:
        """Called on any failure path with the raw exception. Override to
        clean up partial state (e.g. S3 AbortMultipartUpload). Must not
        raise."""
        return None


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
        """Buffer the chunk; emit full parts while buffer exceeds threshold."""
        self._buf.extend(chunk)
        threshold = self.buffer_threshold
        while len(self._buf) >= threshold:
            part = bytes(self._buf[:threshold])
            del self._buf[:threshold]
            self._part_num += 1
            self.on_part(part, self._part_num)

    def close(self) -> Any:
        """Flush any remaining buffered bytes as a final partial part,
        then call on_complete() and return its value."""
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

    def _add_chunk_via_writer(self, entry: UploadEntry, config: UploadConfig, data: bytes) -> bool:
        """Route a chunk through the writer. Returns True on success.

        Lazily instantiates the writer and calls open() on the first chunk.
        On any exception from open() or write_chunk(), calls writer.abort()
        and marks the entry failed.
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
            entry.writer_instance.write_chunk(data)
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
        entry.writer_result. Returns True on success."""
        writer = entry.writer_instance
        if writer is None:
            return False
        try:
            entry.writer_result = writer.close()
        except Exception as exc:  # noqa: BLE001
            entry._error = "Upload writer failed to finalize"
            logger.exception(
                "UploadWriter.close() raised for upload %s: %s",
                entry.ref,
                exc,
            )
            self._safe_abort_writer(entry, exc)
            return False
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
            if not self._add_chunk_via_writer(entry, config, data):
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

        Returns:
            UploadConfig object
        """
        mgr = self._ensure_upload_manager()
        return mgr.configure(
            name=name,
            accept=accept,
            max_entries=max_entries,
            max_file_size=max_file_size,
            chunk_size=chunk_size,
            auto_upload=auto_upload,
            writer=writer,
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
