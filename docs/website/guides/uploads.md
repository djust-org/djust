---
title: "File Uploads"
slug: uploads
section: guides
order: 3
level: intermediate
description: "Chunked file uploads over WebSocket with progress tracking and drag-and-drop"
---

# File Uploads

djust provides chunked binary file uploads over WebSocket, with client-side previews, progress tracking, drag-and-drop support, and server-side validation.

## What You Get

- **UploadMixin** -- Server-side configuration with `allow_upload()` and `consume_uploaded_entries()`
- **Chunked transfer** -- Files are split into 64KB chunks as binary WebSocket frames
- **Template directives** -- `dj-upload`, `dj-upload-drop`, `dj-upload-preview`, `dj-upload-progress`
- **Validation** -- File size limits, extension filtering, MIME type checking, magic byte verification
- **Progress tracking** -- Real-time updates via `djust:upload:progress` DOM events

## Quick Start

### 1. Configure Uploads in Your View

```python
from djust import LiveView
from djust.uploads import UploadMixin
from djust.decorators import event_handler
from django.core.files.storage import default_storage

class ProfileView(UploadMixin, LiveView):
    template_name = 'profile.html'

    def mount(self, request, **kwargs):
        self.avatar_url = ""
        self.allow_upload('avatar',
            accept='.jpg,.png,.webp',
            max_entries=1,
            max_file_size=5_000_000,  # 5MB
        )

    @event_handler()
    def save_avatar(self, **kwargs):
        for entry in self.consume_uploaded_entries('avatar'):
            path = default_storage.save(
                f'avatars/{entry.client_name}', entry.file
            )
            self.avatar_url = default_storage.url(path)
```

### 2. Add Upload Elements to Your Template

```html
<form dj-submit="save_avatar">
    <input type="file" dj-upload="avatar">
    <div dj-upload-preview="avatar"></div>
    <div dj-upload-progress="avatar"></div>
    <button type="submit">Save</button>
</form>
```

### 3. Add Drag-and-Drop

```html
<div dj-upload-drop="avatar" class="drop-zone">
    <p>Drag and drop your avatar here</p>
    <input type="file" dj-upload="avatar">
    <div dj-upload-preview="avatar"></div>
</div>

<style>
.drop-zone { border: 2px dashed #ccc; padding: 2rem; text-align: center; }
.drop-zone.upload-dragover { border-color: #007bff; background: #f0f8ff; }
</style>
```

## `allow_upload()` Parameters

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `name` | `str` | required | Upload slot name, referenced in templates as `dj-upload="name"` |
| `accept` | `str` | `""` | Comma-separated extensions or MIME types (e.g., `".jpg,.png"` or `"image/*"`) |
| `max_entries` | `int` | `1` | Maximum files for this slot. Sets `multiple` automatically if > 1. |
| `max_file_size` | `int` | `10_000_000` | Maximum file size in bytes (default 10MB) |
| `chunk_size` | `int` | `65536` | Chunk size for transfer (default 64KB) |
| `auto_upload` | `bool` | `True` | Start upload immediately when files are selected |

```python
# Single image
self.allow_upload('avatar', accept='.jpg,.png,.webp',
                  max_entries=1, max_file_size=5_000_000)

# Multiple documents
self.allow_upload('documents', accept='.pdf,.docx',
                  max_entries=10, max_file_size=20_000_000)
```

## UploadEntry Properties

| Property | Type | Description |
|----------|------|-------------|
| `client_name` | `str` | Original filename |
| `client_type` | `str` | MIME type |
| `client_size` | `int` | Expected file size in bytes |
| `data` | `bytes` | Complete file content |
| `file` | `BytesIO` | File-like object for Django's storage API |
| `progress` | `int` | Upload progress percentage (0-100) |
| `complete` | `bool` | Whether the upload is finished |
| `error` | `str` or `None` | Error message if validation failed |

## Template Directives

| Directive | Description |
|-----------|-------------|
| `dj-upload="name"` | Bind a file input to an upload slot. `accept` and `multiple` are set automatically. |
| `dj-upload-drop="name"` | Create a drag-and-drop zone. Adds `upload-dragover` CSS class during drag. |
| `dj-upload-preview="name"` | Container for image previews (auto-populated for image files). |
| `dj-upload-progress="name"` | Container for progress bars with `.upload-progress-bar[role=progressbar]`. |

## Client-Side Events

```javascript
window.addEventListener('djust:upload:progress', (e) => {
    // e.detail: {ref, progress, status, uploadName}
    // status: "uploading" | "complete" | "error" | "cancelled"
    console.log(`${e.detail.uploadName}: ${e.detail.progress}%`);
});
```

## Example: Image Gallery Upload

```python
class GalleryView(UploadMixin, LiveView):
    template_name = 'gallery.html'

    def mount(self, request, **kwargs):
        self.images = []
        self.allow_upload('photos',
            accept='.jpg,.png,.webp,.gif',
            max_entries=5,
            max_file_size=10_000_000,
        )

    @event_handler()
    def upload_photos(self, **kwargs):
        for entry in self.consume_uploaded_entries('photos'):
            path = default_storage.save(
                f'gallery/{entry.client_name}', entry.file
            )
            self.images.append({
                'url': default_storage.url(path),
                'name': entry.client_name,
            })
```

```html
<div dj-upload-drop="photos" class="drop-zone">
    <p>Drag photos here or click to browse</p>
    <input type="file" dj-upload="photos">
</div>

<div dj-upload-preview="photos" class="preview-grid"></div>
<div dj-upload-progress="photos"></div>

<button dj-click="upload_photos">Upload All</button>

<div class="gallery">
    {% for img in images %}
    <img src="{{ img.url }}" alt="{{ img.name }}">
    {% endfor %}
</div>
```

## Direct-to-S3 streaming with `UploadWriter`

By default, uploaded chunks are buffered into a temp file on the djust server, then your event handler reads the finished file via `entry.data` / `entry.file`. For large files or server-to-server pipelines (S3, GCS, Azure Blob, a CDN origin), you can bypass the server temp file entirely and pipe each chunk straight to its destination.

Pass an `UploadWriter` subclass to `allow_upload(writer=...)`. When a writer is configured, djust instantiates it lazily on the first chunk, calls `write_chunk(bytes)` for each client chunk, and calls `close()` on completion (or `abort(error)` on any failure path — including client cancellation, size-limit overflow, and WebSocket disconnect).

### The `UploadWriter` contract

```python
from djust.uploads import UploadWriter

class MyWriter(UploadWriter):
    # Constructor is called per-upload on the first chunk:
    #   (upload_id, filename, content_type, expected_size)

    def open(self) -> None:
        """Called exactly once before the first write_chunk.
        Raise to reject the upload (abort() is called with the exception)."""

    def write_chunk(self, chunk: bytes) -> None:
        """Called once per WebSocket binary frame with the raw bytes.
        Raise to abort the upload."""

    def close(self):
        """Called on successful completion. The return value is stored on
        UploadEntry.writer_result and is template-accessible."""
        return {"url": "..."}

    def abort(self, error: BaseException) -> None:
        """Called on ANY failure path with the raw exception.
        Must not raise (any exception is logged and swallowed).
        Use this to release server-side resources, e.g. AbortMultipartUpload."""
```

Guarantees:

- `open()` is called at most once.
- `write_chunk()` is never called before `open()` succeeds.
- After `close()` or `abort()` returns, no further methods are invoked on the instance.
- Writer instances are **isolated per upload** — no shared state across concurrent uploads.
- Writers are **synchronous**. If you need async I/O, use `sync_to_async` / `asyncio.run_coroutine_threadsafe` at the boundary inside your methods.

> **⚠ Security: never use `self.filename` verbatim as a destination path/key.**
> `self.filename` comes from the client-supplied `File.name` and is fully attacker-controlled. Strings like `../../etc/passwd`, absolute paths, URL-encoded nulls, or paths intended to overwrite other users' objects will all flow through verbatim unless you sanitize.
>
> **Always scope the destination to a safe namespace:** derive the S3 key (or filesystem path) from the authenticated user id, a server-generated UUID, and a sanitized basename. Example pattern used in the S3 writer below:
>
> ```python
> from pathlib import Path
> from uuid import uuid4
>
> def _safe_key(self) -> str:
>     safe = Path(self.filename).name  # strip any directory components
>     return f"uploads/user-{self.user_id}/{uuid4()}-{safe}"
> ```
>
> The `self.user_id` comes from your `__init__` override — pass the authenticated user at `allow_upload(writer=...)` time via a closure or factory. Never trust `self.filename` alone for routing.

### S3 multipart upload (full example)

```python
import boto3
from pathlib import Path
from uuid import uuid4
from djust import LiveView
from djust.uploads import UploadMixin, BufferedUploadWriter

class S3MultipartWriter(BufferedUploadWriter):
    buffer_threshold = 5 * 1024 * 1024  # 5 MB — S3 MPU minimum part size

    def _safe_key(self) -> str:
        # Client-supplied filename is untrusted — strip directory components
        # and scope to a server-generated UUID namespace.
        safe = Path(self.filename).name
        return f"uploads/{uuid4()}-{safe}"

    def open(self):
        self._s3 = boto3.client("s3")
        self._key = self._safe_key()
        self._mpu = self._s3.create_multipart_upload(
            Bucket="my-bucket",
            Key=self._key,
            ContentType=self.content_type,
        )
        self._parts = []

    def on_part(self, part: bytes, part_num: int) -> None:
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
        return {
            "bucket": "my-bucket",
            "key": self._key,
            "url": f"https://my-bucket.s3.amazonaws.com/{self._key}",
        }

    def abort(self, error):
        mpu = getattr(self, "_mpu", None)
        if mpu:
            self._s3.abort_multipart_upload(
                Bucket="my-bucket",
                Key=self._key,
                UploadId=mpu["UploadId"],
            )


class UploadView(LiveView, UploadMixin):
    def mount(self, request, **kwargs):
        self.allow_upload(
            "asset",
            writer=S3MultipartWriter,
            max_file_size=500_000_000,  # 500 MB
            accept=".jpg,.png,.mp4",
        )

    def save_uploads(self):
        for entry in self.consume_uploaded_entries("asset"):
            # entry.writer_result is whatever on_complete() returned
            url = entry.writer_result["url"]
            # Persist the URL on your model, emit a toast, etc.
```

### Why `BufferedUploadWriter`?

Clients send whatever chunk size they send (djust's default is 64 KB per WebSocket binary frame). S3's multipart upload API requires every part except the last to be at least **5 MB**. `BufferedUploadWriter` accumulates raw client chunks into an internal buffer and emits `on_part(part, part_num)` calls aligned to your threshold — so you write S3-compliant code without thinking about the raw frame size. If you're targeting a destination with no minimum part size (a pure HTTP pipe, a local Ceph, a custom CDN API), subclass `UploadWriter` directly and handle chunks as they arrive.

### Error handling

`abort(error)` is called on **every** failure path with the raw exception object:

| Trigger | Exception passed to `abort()` |
|---------|-------------------------------|
| `open()` raised | the exception raised by `open()` |
| `write_chunk()` raised | the exception raised by `write_chunk()` |
| `close()` raised | the exception raised by `close()` |
| Total bytes exceeds `max_file_size` | `ValueError("File size exceeds limit (N bytes)")` |
| Client sent a cancel frame | `ConnectionAbortedError("upload cancelled")` |
| WebSocket session closed with upload in flight | `ConnectionAbortedError("session closed")` |

If your own `abort()` implementation raises, djust logs the traceback and swallows it — a failing S3 `AbortMultipartUpload` is a cleanup problem, not a correctness problem for the rest of the view.

### Limitations of the writer path

- **No magic-byte validation.** The disk-buffered path runs magic-byte checks (e.g. verify a `.png` really starts with `\x89PNG`) because it has the full file. The writer path streams — if you need content validation, buffer the first N bytes yourself in `write_chunk()` and validate before forwarding.
- **`entry.data` / `entry.file` are empty.** The raw bytes never sat anywhere djust could hand them to you. Use `entry.writer_result` (whatever `close()` returned) instead.
- **No temp file cleanup required.** Because no temp file was created, `entry.cleanup()` is a no-op for writer uploads.

### Key-template convention for `s3_events`

When you use the S3 event webhook (`djust.contrib.uploads.s3_events.s3_event_webhook`) to receive `ObjectCreated` notifications and fire the `on_upload_complete(...)` hook, djust needs a way to map the incoming S3 key back to the `upload_id` that your app registered a hook against.

`parse_s3_event` does this by finding the **first UUID-shaped path segment** (32–36 hex/dash characters) in the object key. That means your presign step must produce keys that follow the convention:

```
uploads/<upload_id_uuid>/<original_filename>
```

or, when bucketing by tenant:

```
<tenant_id>/<upload_id_uuid>/<original_filename>
```

Both work because the parser scans **every** segment, not just the first — the first UUID-shaped segment wins.

**If no path segment looks UUID-shaped**, `upload_id` silently falls back to the full key, a `DEBUG` log entry is emitted on the `djust.contrib.uploads.s3_events` logger, and your hook **will not fire** (because it was registered under a UUID, not the full key). This is the #1 source of "my hook isn't being called" reports.

Debugging a silent hook:

```python
import logging
logging.getLogger("djust.contrib.uploads.s3_events").setLevel(logging.DEBUG)
```

Re-run the webhook delivery. The log will show the key that failed to match and the convention you need to follow.

**Alternative: custom upload-id routing.** If you embed the upload id elsewhere — an `x-amz-meta-upload-id` header, a JWT in the key prefix, a DB lookup keyed on the S3 key — parse the SNS payload yourself and bypass `parse_s3_event` entirely. The helper is a best-effort convention for the common case; it's not mandatory.

## Resumable uploads

Network hiccups, backgrounded mobile tabs, and brief WebSocket disconnects should not kill a long upload. Resumable uploads persist chunk-level state server-side so the transfer picks up where it left off on reconnect.

### How it works

Add `resumable=True` to any `allow_upload()` slot. When a client uploads with this flag set and the WebSocket drops mid-transfer, the browser automatically sends an `upload_resume` message on reconnect with the last confirmed byte offset. djust resumes the transfer from that offset — no re-sending of already-received chunks.

The state survives **WS reconnects on the same server process**. A server restart wipes in-memory state (see [State stores](#state-stores) for cross-process persistence).

### Enable it

```python
from djust.uploads import UploadMixin

class VideoUploadView(UploadMixin, LiveView):
    template_name = 'video_upload.html'

    def mount(self, request, **kwargs):
        self.allow_upload(
            'video',
            accept='.mp4,.mov,.webm',
            max_file_size=500_000_000,  # 500 MB
            resumable=True,
        )
```

That's all that's needed for the basic path. The client-side `dj-upload` directive handles the resume protocol automatically.

### State stores

By default, chunk receipts are held in process memory (`InMemoryUploadState`). This works for single-process servers and the common dev-server case. For multi-process deployments (gunicorn, uvicorn workers) or production environments where worker restarts should not kill uploads, plug in a shared store.

#### RedisUploadState

```python
# settings.py
DJUST = {
    'upload_state_store': 'djust.uploads.stores.RedisUploadState',
    'upload_state_redis_url': 'redis://localhost:6379/0',
    # Optional: TTL for upload state records (default: 1 hour)
    'upload_state_ttl': 3600,
}

# views.py — pass store config to the slot
self.allow_upload(
    'video',
    resumable=True,
    resumable_store='redis',       # selects the configured store
    resumable_store_options={
        'redis_url': 'redis://localhost:6379/0',
        'ttl': 3600,
    },
)
```

The store is instantiated lazily on first use. djust reads `DJUST['upload_state_store']` as the global default and `resumable_store` per-slot to override it.

#### Custom store

Implement the store protocol:

```python
from djust.uploads.stores import UploadStateStore

class MyUploadStateStore(UploadStateStore):
    async def get_offset(self, upload_id: str) -> int | None:
        # Return byte offset, or None if upload_id not found
        ...

    async def set_offset(self, upload_id: str, offset: int) -> None:
        # Persist chunk offset
        ...

    async def clear_offset(self, upload_id: str) -> None:
        # Called on completion or explicit cancel
        ...
```

Stores are sync or async — djust handles both transparently.

### ResumableUploadWriter

For large files that also use a custom destination (S3, GCS, Azure Blob), combine `ResumableUploadWriter` with the state store:

```python
from djust.uploads import ResumableUploadWriter, BufferedUploadWriter
import boto3

class ResumableS3Writer(ResumableUploadWriter, BufferedUploadWriter):
    """S3 MPU writer that cooperates with the resumable upload state."""

    def open(self):
        self._s3 = boto3.client("s3")
        self._key = f"uploads/{self.upload_id}"
        self._mpu = self._s3.create_multipart_upload(
            Bucket="my-bucket",
            Key=self._key,
            ContentType=self.content_type,
        )
        self._parts = []
        self._received_bytes = 0

    def write_chunk(self, chunk: bytes):
        self._received_bytes += len(chunk)
        # BufferedUploadWriter.flush() calls on_part() at the 5 MB boundary

    def on_part(self, part: bytes, part_num: int) -> None:
        resp = self._s3.upload_part(
            Bucket="my-bucket",
            Key=self._key,
            UploadId=self._mpu["UploadId"],
            PartNumber=part_num,
            Body=part,
        )
        self._parts.append({"ETag": resp["ETag"], "PartNumber": part_num})

    def close(self):
        self._s3.complete_multipart_upload(
            Bucket="my-bucket",
            Key=self._key,
            UploadId=self._mpu["UploadId"],
            MultipartUpload={"Parts": self._parts},
        )
        return {"url": f"https://my-bucket.s3.amazonaws.com/{self._key}"}

    def abort(self, error):
        mpu = getattr(self, "_mpu", None)
        if mpu:
            self._s3.abort_multipart_upload(
                Bucket="my-bucket",
                Key=self._key,
                UploadId=mpu["UploadId"],
            )
```

The `ResumableUploadWriter` base class adds the state-store integration: on each `write_chunk`, it also records the cumulative byte count. If the upload is interrupted and resumes, `open()` is called again with `upload_id` set and can skip the already-confirmed bytes.

### Failure-mode matrix

| Failure | With `resumable=True` | Without |
|---------|----------------------|---------|
| WS drops, reconnects < TTL | Resumes from last chunk | Upload aborted |
| Browser tab backgrounded (mobile) | Resumes on foreground | Upload aborted |
| Server process restart (in-memory store) | Upload aborted | Upload aborted |
| Server restart (Redis store) | Resumes from last chunk | Upload aborted |
| Client closes tab | Upload aborted | Upload aborted |
| Second tab tries to resume same upload | Rejected with error | n/a |

### Client-side behavior

The browser client automatically:

1. Caches `upload_id` + last confirmed offset in `IndexedDB`
2. On WS reconnect, sends `upload_resume {upload_id, offset}`
3. Receives `upload_resume_ack {accepted: true, offset}` and continues from that byte
4. If the server rejects the resume (state expired, slot full), falls back to starting over from byte 0

The `djust:upload:progress` event fires with `status: "resuming"` during the protocol handshake.

## Best Practices

- **Set `max_file_size`** based on your needs. Client-side validation rejects oversized files before upload begins; server-side validates after all chunks arrive.
- **Use file extensions** (`.jpg,.png`) for simple filtering or MIME types (`image/*`) for broader categories. Server-side magic byte checking prevents extension spoofing.
- **Always iterate fully** over `consume_uploaded_entries()` or call `cancel_upload()` for unwanted files. Temp files are cleaned up on WebSocket disconnect.
- **For large files**, increase `chunk_size` to reduce the number of WebSocket frames — or better, switch to `UploadWriter` and stream directly to your object store.
- **For direct-to-S3 uploads**, subclass `BufferedUploadWriter` (not `UploadWriter` directly) so you get S3-compliant 5 MB parts without buffering raw client chunks. Always implement `abort()` to call `AbortMultipartUpload` — otherwise failed uploads will leak stranded multipart uploads in your bucket that you'll keep paying for.
