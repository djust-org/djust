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
from uuid import uuid4

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
            # safe_client_name, never client_name: see the note below.
            path = default_storage.save(
                f'avatars/{uuid4().hex}/{entry.safe_client_name}', entry.file
            )
            self.avatar_url = default_storage.url(path)
```

> **Security: build storage paths from `entry.safe_client_name`, never `entry.client_name`.**
> `client_name` is the raw filename the browser sent and is attacker-controlled. A name like `../../x` raises `SuspiciousFileOperation` on `FileSystemStorage` and is a valid key on object stores (S3, GCS, Azure), where it can overwrite or misplace other objects (CWE-22 / CWE-73). `safe_client_name` is a basename with directory components, control bytes and `..`/dotfile tricks removed. Two users can still upload the same name, and some stores (S3 with django-storages' default settings, for example) overwrite an existing key, so the examples also add a server-generated `uuid4()` directory. Use `client_name` only for display, through normal auto-escaping.

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
| `client_name` | `str` | Original filename as sent by the client (untrusted; display only) |
| `safe_client_name` | `str` | Path-safe basename of `client_name`; use this for storage paths and keys |
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
                f'gallery/{uuid4().hex}/{entry.safe_client_name}', entry.file
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

<!-- doc-snippet-check: skip -->
```python
import boto3
from pathlib import Path
from uuid import uuid4
from djust import LiveView
from djust.decorators import event_handler
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

    @event_handler()
    def save_uploads(self, **kwargs):
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

Add `resumable=True` to an `allow_upload()` slot whose writer is a `ResumableUploadWriter` (see [ResumableUploadWriter](#resumableuploadwriter)). When the WebSocket drops mid-transfer, the browser sends an `upload_resume` message with the upload's `ref` on reconnect. The server replies with the bytes and chunk indices it has already received, and the client continues from there — no re-sending of already-received chunks.

When the WebSocket drops with an upload in flight, djust **suspends** a `ResumableUploadWriter` instead of aborting it: the state entry stays, and the inner writer (an open S3 multipart upload, say) is not aborted. The suspended upload waits for its client for up to 10 minutes (or the state's TTL, if shorter), at most 32 per process; past that, or when the cap is exceeded, the oldest is aborted as before. On reconnect the same session's `upload_resume` re-attaches it and the remaining chunks continue the same writer. A cancel, a writer error or a size-limit violation still aborts it and deletes the state. (Before 1.2.1 every disconnect aborted it, so resume never worked; #2972.)

Resume continues the live writer, so it works for a reconnect **to the same server process**. When the reconnect lands on another process, or after a server restart, the server answers `not_found` and the client starts over from byte 0 — even with a shared state store such as Redis, which only lets `UploadStatusView` report progress across processes.

### Enable it

```python
from djust.uploads import UploadMixin, ResumableUploadWriter

# MyWriter is any UploadWriter / BufferedUploadWriter subclass.
ResumableMyWriter = ResumableUploadWriter.with_inner(MyWriter)

class VideoUploadView(UploadMixin, LiveView):
    template_name = 'video_upload.html'

    def mount(self, request, **kwargs):
        self.allow_upload(
            'video',
            accept='.mp4,.mov,.webm',
            max_file_size=500_000_000,  # 500 MB
            writer=ResumableMyWriter,
            resumable=True,
        )
```

The client-side `dj-upload` directive handles the resume protocol automatically. Server-side chunk state is recorded only by a `ResumableUploadWriter`: with `resumable=True` and no such writer, nothing is persisted, the server answers the resume request with `not_found`, and the client starts over from byte 0.

### State stores

By default, chunk receipts are held in process memory (`InMemoryUploadState`). This works for single-process servers and the common dev-server case. For multi-process deployments (gunicorn, uvicorn workers), plug in a shared store so any worker can answer `UploadStatusView` and the owner check. A shared store does not by itself let an upload continue on another process: resuming needs the suspended writer, which lives in the process that received the earlier chunks.

#### RedisUploadState

There are no settings for the store. Construct `RedisUploadState` with a Redis client and install it as the process-wide default with `set_default_store()`, typically once in `AppConfig.ready()`:

```python
# apps.py
import redis
from django.apps import AppConfig
from djust.uploads.storage import RedisUploadState, set_default_store

class MyAppConfig(AppConfig):
    name = "myapp"

    def ready(self):
        set_default_store(
            RedisUploadState(redis.Redis.from_url("redis://localhost:6379/0"))
        )
```

`ResumableUploadWriter` uses the default store unless you bind one explicitly: `ResumableUploadWriter.with_inner(MyWriter, state_store=my_store, ttl_hours=48)`. State entries expire after 24 hours by default (`DEFAULT_TTL_SECONDS`).

#### Custom store

Implement the `UploadStateStore` protocol. It is **synchronous**: callers invoke the methods directly, so `async def` methods would return un-awaited coroutines.

```python
from djust.uploads.storage import set_default_store

class MyUploadStateStore:
    def get(self, upload_id: str) -> dict | None:
        # Return the state dict, or None if absent.
        ...

    def set(self, upload_id: str, state: dict, ttl: int) -> None:
        # Overwrite the entry and (re)set its expiry to ttl seconds.
        # Raise UploadStateTooLarge if the JSON-encoded state exceeds
        # MAX_STATE_SIZE_BYTES (16 KB).
        ...

    def update(self, upload_id: str, partial: dict) -> dict | None:
        # Merge partial into the existing entry and return it; return None
        # (and do nothing) if the entry doesn't exist. Keep the old TTL.
        ...

    def delete(self, upload_id: str) -> None:
        # Remove the entry; no-op if absent.
        ...

set_default_store(MyUploadStateStore())
```

Implementations must be thread-safe: chunks of one upload can arrive on different worker threads. `set_default_store()` raises `TypeError` for an object that doesn't satisfy the protocol.

### ResumableUploadWriter

For large files that also use a custom destination (S3, GCS, Azure Blob), wrap your writer with `ResumableUploadWriter.with_inner()`. Don't subclass `ResumableUploadWriter` directly or combine it with another writer by multiple inheritance: a class without a bound inner writer raises `RuntimeError` on the first chunk.

<!-- doc-snippet-check: skip -->
```python
from djust.uploads import ResumableUploadWriter

# S3MultipartWriter is the BufferedUploadWriter subclass from the
# "S3 multipart upload" section above; it needs no changes.
ResumableS3Writer = ResumableUploadWriter.with_inner(S3MultipartWriter, ttl_hours=24)

class VideoUploadView(UploadMixin, LiveView):
    def mount(self, request, **kwargs):
        self.allow_upload(
            "asset",
            writer=ResumableS3Writer,
            resumable=True,
            max_file_size=500_000_000,
        )
```

The wrapper delegates `open()`, `write_chunk()`, `close()` and `abort()` to the inner writer and adds the state-store integration: it forwards each new chunk to the inner writer and, once that succeeds, records the chunk index in the store. Chunks already recorded are skipped when a resumed upload replays them, so the inner writer does no offset handling of its own. It deletes the state entry on `close()` or `abort()`. If the store is unreachable when the writer is created, it logs a warning and the upload continues without resume support.

### Failure-mode matrix

| Failure | With `resumable=True` | Without |
|---------|----------------------|---------|
| WS drops, reconnects < TTL | Intended: resumes from last chunk. At rc10: restarts from byte 0 (see the known issue above) | Upload aborted |
| Browser tab backgrounded (mobile) | Intended: resumes on foreground. At rc10: restarts from byte 0 if the WebSocket dropped | Upload aborted |
| Server process restart (in-memory store) | Upload aborted | Upload aborted |
| Server restart (Redis store) | Intended: resumes from last chunk. At rc10: a graceful shutdown runs the same disconnect abort, so expect a restart from byte 0 | Upload aborted |
| Client closes tab | Upload aborted | Upload aborted |
| Second tab tries to resume same upload | Resume refused (`locked`); that tab starts a fresh upload | n/a |

### Client-side behavior

The browser client automatically:

1. Records the upload's `ref` in `IndexedDB`, keyed by a file hint (name + size + lastModified). If IndexedDB is unavailable, it keeps the record in memory, so resume works within the same page load only.
2. On WS reconnect, sends `upload_resume {ref}`
3. Receives `upload_resumed {status, bytes_received, chunks_received}` and, when `status` is `"resumed"`, continues past the chunks the server already has
4. If the server answers `not_found` (state expired, or a different session) or `locked` (another session is resuming the same upload), falls back to starting over from byte 0

There is no separate progress status for the handshake: `djust:upload:progress` keeps reporting `uploading` / `complete` (and `error` / `cancelled`).

## Best Practices

- **Set `max_file_size`** based on your needs. Client-side validation rejects oversized files before upload begins; server-side validates after all chunks arrive.
- **Use file extensions** (`.jpg,.png`) for simple filtering or MIME types (`image/*`) for broader categories. Server-side magic byte checking prevents extension spoofing.
- **Always iterate fully** over `consume_uploaded_entries()` or call `cancel_upload()` for unwanted files. Temp files are cleaned up on WebSocket disconnect.
- **For large files**, increase `chunk_size` to reduce the number of WebSocket frames — or better, switch to `UploadWriter` and stream directly to your object store.
- **For direct-to-S3 uploads**, subclass `BufferedUploadWriter` (not `UploadWriter` directly) so you get S3-compliant 5 MB parts without buffering raw client chunks. Always implement `abort()` to call `AbortMultipartUpload` — otherwise failed uploads will leak stranded multipart uploads in your bucket that you'll keep paying for.
