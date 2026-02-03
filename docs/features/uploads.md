# File Uploads

djust provides WebSocket-based file uploads with chunked transfer, progress tracking, client-side previews, and drag-and-drop support. Inspired by Phoenix LiveView's upload API.

## Core Concepts

- **`UploadMixin`** — Add upload support to any LiveView
- **`allow_upload()`** — Configure upload slots with validation
- **`consume_uploaded_entries()`** — Process completed uploads
- **`dj-upload`** — Template directive for file inputs
- **Chunked transfer** — Large files sent in 64KB chunks over WebSocket

## Basic Usage

```python
from djust import LiveView
from djust.uploads import UploadMixin
from djust.decorators import event_handler
from django.core.files.storage import default_storage

class ProfileView(LiveView, UploadMixin):
    template_name = "profile.html"

    def mount(self, request, **kwargs):
        # Configure upload slot
        self.allow_upload(
            "avatar",
            accept=".jpg,.png,.webp",
            max_entries=1,
            max_file_size=5_000_000  # 5MB
        )
        self.avatar_url = None

    @event_handler
    def save_avatar(self, **kwargs):
        for entry in self.consume_uploaded_entries("avatar"):
            # entry.client_name = original filename
            # entry.client_type = MIME type
            # entry.data = bytes
            # entry.file = BytesIO file object
            
            path = default_storage.save(
                f"avatars/{entry.client_name}",
                entry.file
            )
            self.avatar_url = default_storage.url(path)
```

```html
<!-- profile.html -->
<div class="avatar-upload">
    <!-- File input bound to upload slot -->
    <input type="file" dj-upload="avatar" />
    
    <!-- Preview container (auto-populated) -->
    <div dj-upload-preview="avatar"></div>
    
    <!-- Progress bar (auto-updated) -->
    <div dj-upload-progress="avatar"></div>
    
    <button dj-click="save_avatar">Save Avatar</button>
</div>

{% if avatar_url %}
    <img src="{{ avatar_url }}" alt="Avatar" />
{% endif %}
```

## UploadMixin API

### `allow_upload(name, **options)`

Configure a named upload slot. Call in `mount()`.

```python
self.allow_upload(
    name="documents",
    accept=".pdf,.doc,.docx",
    max_entries=5,
    max_file_size=10_000_000,
    chunk_size=64 * 1024,
    auto_upload=True
)
```

| Option | Type | Default | Description |
|--------|------|---------|-------------|
| `name` | `str` | **required** | Slot name (used in templates) |
| `accept` | `str` | `""` | Comma-separated extensions or MIME types |
| `max_entries` | `int` | `1` | Max files for this slot |
| `max_file_size` | `int` | `10_000_000` | Max size in bytes (10MB) |
| `chunk_size` | `int` | `65536` | Transfer chunk size (64KB) |
| `auto_upload` | `bool` | `True` | Start upload immediately on selection |

### `consume_uploaded_entries(name)`

Generator that yields completed upload entries. Entries are cleaned up after iteration.

```python
for entry in self.consume_uploaded_entries("documents"):
    # Process each file
    save_document(entry.client_name, entry.data)
```

### `get_uploads(name)`

Get all entries (including in-progress) for an upload slot.

```python
uploads = self.get_uploads("documents")
# Returns list of UploadEntry objects
```

### `cancel_upload(name, ref)`

Cancel a specific upload by its ref.

```python
@event_handler
def cancel_file(self, ref, **kwargs):
    self.cancel_upload("documents", ref)
```

## UploadEntry Properties

| Property | Type | Description |
|----------|------|-------------|
| `entry.ref` | `str` | Unique upload reference ID |
| `entry.client_name` | `str` | Original filename |
| `entry.client_type` | `str` | MIME type from client |
| `entry.client_size` | `int` | Expected file size |
| `entry.progress` | `int` | Upload progress (0-100) |
| `entry.complete` | `bool` | True when upload finished |
| `entry.error` | `str` | Error message if failed |
| `entry.data` | `bytes` | File content as bytes |
| `entry.file` | `BytesIO` | File-like object for Django storage |

## Template Directives

### `dj-upload="name"`

Bind a file input to an upload slot.

```html
<!-- Single file -->
<input type="file" dj-upload="avatar" />

<!-- Multiple files (if max_entries > 1) -->
<input type="file" dj-upload="documents" multiple />
```

The `accept` attribute is auto-set from your `allow_upload()` config.

### `dj-upload-drop="name"`

Create a drag-and-drop zone.

```html
<div dj-upload-drop="documents" class="drop-zone">
    <p>Drag files here or click to browse</p>
    <input type="file" dj-upload="documents" />
</div>

<style>
.drop-zone {
    border: 2px dashed #ccc;
    padding: 2rem;
    text-align: center;
}
.drop-zone.upload-dragover {
    border-color: #007bff;
    background: #f0f7ff;
}
</style>
```

### `dj-upload-preview="name"`

Container for image previews. Auto-populated when files are selected.

```html
<div dj-upload-preview="avatar" class="previews">
    <!-- Auto-generated preview items -->
</div>

<style>
.previews {
    display: flex;
    gap: 1rem;
}
.upload-preview-item img {
    max-width: 100px;
    max-height: 100px;
}
</style>
```

### `dj-upload-progress="name"`

Container for progress bars. Auto-populated during upload.

```html
<div dj-upload-progress="documents" class="progress-list">
    <!-- Auto-generated progress bars -->
</div>

<style>
.upload-progress-item {
    margin-bottom: 0.5rem;
}
.upload-progress-track {
    background: #eee;
    height: 4px;
    border-radius: 2px;
}
.upload-progress-bar {
    background: #007bff;
    height: 100%;
    transition: width 0.2s;
}
</style>
```

## File Validation

### Extension/MIME Validation

```python
self.allow_upload(
    "images",
    accept=".jpg,.jpeg,.png,.gif,.webp",  # By extension
)

self.allow_upload(
    "media",
    accept="image/*,video/*",  # By MIME category
)

self.allow_upload(
    "docs",
    accept=".pdf,application/msword",  # Mixed
)
```

### Magic Bytes Validation

djust validates file content (not just extension) using magic bytes:

| MIME Type | Magic Bytes |
|-----------|-------------|
| `image/jpeg` | `FF D8 FF` |
| `image/png` | `89 50 4E 47` |
| `image/gif` | `GIF87a` or `GIF89a` |
| `image/webp` | `RIFF...WEBP` |
| `application/pdf` | `%PDF` |
| `application/zip` | `PK` |

If a user renames `virus.exe` to `photo.jpg`, the upload is rejected.

### Size Validation

```python
self.allow_upload(
    "documents",
    max_file_size=50_000_000,  # 50MB
)
```

Size is validated both:
1. **Client-side** — before upload starts
2. **Server-side** — as chunks arrive

## Progress Tracking

Listen for upload progress events in JavaScript:

```javascript
// Progress event
window.addEventListener('djust:upload:progress', (e) => {
    const { ref, progress, status, uploadName } = e.detail;
    console.log(`${uploadName}: ${progress}% (${status})`);
});

// Error event
window.addEventListener('djust:upload:error', (e) => {
    const { file, error } = e.detail;
    alert(`Upload failed: ${file} - ${error}`);
});
```

## Complete Example: Document Upload

```python
# views.py
from djust import LiveView
from djust.uploads import UploadMixin
from djust.decorators import event_handler
from django.core.files.storage import default_storage
from .models import Document

class DocumentsView(LiveView, UploadMixin):
    template_name = "documents.html"

    def mount(self, request, **kwargs):
        self.allow_upload(
            "files",
            accept=".pdf,.doc,.docx,.txt",
            max_entries=10,
            max_file_size=25_000_000,  # 25MB
        )
        self.user = request.user
        self.documents = list(Document.objects.filter(user=self.user))
        self.upload_status = None

    @event_handler
    def upload_files(self, **kwargs):
        uploaded = []
        for entry in self.consume_uploaded_entries("files"):
            # Save to storage
            path = default_storage.save(
                f"documents/{self.user.id}/{entry.client_name}",
                entry.file
            )
            
            # Create database record
            doc = Document.objects.create(
                user=self.user,
                name=entry.client_name,
                file_path=path,
                size=entry.client_size,
                mime_type=entry.client_type,
            )
            uploaded.append(doc)
        
        self.documents = list(Document.objects.filter(user=self.user))
        self.upload_status = f"Uploaded {len(uploaded)} file(s)"

    @event_handler
    def delete_document(self, doc_id, **kwargs):
        doc = Document.objects.filter(id=doc_id, user=self.user).first()
        if doc:
            default_storage.delete(doc.file_path)
            doc.delete()
            self.documents = list(Document.objects.filter(user=self.user))

    @event_handler
    def cancel_upload(self, ref, **kwargs):
        self.cancel_upload("files", ref)
```

```html
<!-- documents.html -->
<div class="document-manager">
    <h2>My Documents</h2>

    <!-- Upload area -->
    <div dj-upload-drop="files" class="drop-zone">
        <p>📁 Drag files here</p>
        <p>or</p>
        <label class="file-btn">
            Browse Files
            <input type="file" dj-upload="files" multiple hidden />
        </label>
        <p class="hint">PDF, DOC, DOCX, TXT — Max 25MB each</p>
    </div>

    <!-- Preview selected files -->
    <div dj-upload-preview="files" class="preview-list"></div>

    <!-- Upload progress -->
    <div dj-upload-progress="files" class="progress-list"></div>

    <!-- Pending uploads indicator -->
    {% with pending=uploads.files.entries %}
    {% if pending %}
        <div class="pending-uploads">
            {{ pending|length }} file(s) ready to upload
            <button dj-click="upload_files" class="btn-primary">
                Upload All
            </button>
        </div>
    {% endif %}
    {% endwith %}

    <!-- Status message -->
    {% if upload_status %}
        <div class="status-message">{{ upload_status }}</div>
    {% endif %}

    <!-- Document list -->
    <ul class="document-list">
        {% for doc in documents %}
        <li>
            <span class="doc-name">{{ doc.name }}</span>
            <span class="doc-size">{{ doc.size|filesizeformat }}</span>
            <button dj-click="delete_document" 
                    dj-value-doc_id="{{ doc.id }}"
                    dj-confirm="Delete this document?">
                🗑️
            </button>
        </li>
        {% empty %}
        <li class="empty">No documents yet</li>
        {% endfor %}
    </ul>
</div>

<style>
.drop-zone {
    border: 2px dashed #ccc;
    border-radius: 8px;
    padding: 2rem;
    text-align: center;
    transition: all 0.2s;
}
.drop-zone.upload-dragover {
    border-color: #007bff;
    background: #f0f7ff;
}
.file-btn {
    display: inline-block;
    padding: 0.5rem 1rem;
    background: #007bff;
    color: white;
    border-radius: 4px;
    cursor: pointer;
}
.hint {
    color: #666;
    font-size: 0.875rem;
}
.progress-list {
    margin: 1rem 0;
}
.upload-progress-item {
    display: flex;
    align-items: center;
    gap: 1rem;
    margin-bottom: 0.5rem;
}
.upload-progress-track {
    flex: 1;
    background: #eee;
    height: 6px;
    border-radius: 3px;
    overflow: hidden;
}
.upload-progress-bar {
    background: #28a745;
    height: 100%;
    transition: width 0.1s;
}
</style>
```

## Upload Flow

1. **Client selects files** — `dj-upload` input or drag-and-drop
2. **Client validates** — Size and extension checks
3. **Client sends `upload_register`** — Announces file to server
4. **Server validates** — Checks config, creates entry
5. **Client sends chunks** — Binary WebSocket frames (64KB each)
6. **Server reassembles** — Writes to temp file
7. **Server validates content** — Magic bytes check
8. **Event handler processes** — `consume_uploaded_entries()`
9. **Cleanup** — Temp files removed

## API Reference

### UploadMixin Methods

| Method | Description |
|--------|-------------|
| `allow_upload(name, **opts)` | Configure upload slot |
| `consume_uploaded_entries(name)` | Yield completed entries |
| `get_uploads(name)` | Get all entries for slot |
| `cancel_upload(name, ref)` | Cancel upload by ref |

### Template Directives

| Directive | Description |
|-----------|-------------|
| `dj-upload="name"` | File input bound to slot |
| `dj-upload-drop="name"` | Drag-and-drop zone |
| `dj-upload-preview="name"` | Image preview container |
| `dj-upload-progress="name"` | Progress bar container |

### JavaScript Events

| Event | Detail | Description |
|-------|--------|-------------|
| `djust:upload:progress` | `{ref, progress, status, uploadName}` | Progress update |
| `djust:upload:error` | `{file, error}` | Upload error |
