# File Uploads

djust provides chunked binary file uploads over WebSocket, with client-side previews, progress tracking, drag-and-drop support, and server-side validation. Inspired by Phoenix LiveView's upload system.

## Overview

- **UploadMixin** - Server-side mixin with `allow_upload()` configuration and `consume_uploaded_entries()` processing
- **Chunked binary transfer** - Files are split into 64KB chunks and sent as binary WebSocket frames
- **Template directives** - `dj-upload`, `dj-upload-drop`, `dj-upload-preview`, `dj-upload-progress`
- **Validation** - File size limits, extension filtering, MIME type checking, and magic byte verification
- **Progress tracking** - Real-time progress updates via `djust:upload:progress` DOM events

## Quick Start

### 1. Configure uploads in your view

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

### 2. Add upload elements to your template

```html
<form dj-submit="save_avatar">
    <!-- File input bound to the 'avatar' upload slot -->
    <input type="file" dj-upload="avatar">

    <!-- Image preview container -->
    <div dj-upload-preview="avatar"></div>

    <!-- Progress bar container -->
    <div dj-upload-progress="avatar"></div>

    <button type="submit">Save</button>
</form>
```

### 3. Drag-and-drop zone

```html
<div dj-upload-drop="avatar" class="drop-zone">
    <p>Drag and drop your avatar here</p>
    <input type="file" dj-upload="avatar">
    <div dj-upload-preview="avatar"></div>
</div>
```

## API Reference

### UploadMixin Methods

#### `allow_upload(name, accept="", max_entries=1, max_file_size=10_000_000, chunk_size=65536, auto_upload=True)`

Configure a named upload slot. Call this in `mount()`.

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `name` | `str` | required | Upload slot name, referenced in templates as `dj-upload="name"`. |
| `accept` | `str` | `""` | Comma-separated accepted extensions or MIME types (e.g., `".jpg,.png"` or `"image/*"`). Empty allows all. |
| `max_entries` | `int` | `1` | Maximum number of files for this slot. Sets `multiple` attribute automatically if > 1. |
| `max_file_size` | `int` | `10_000_000` | Maximum file size in bytes (default 10MB). |
| `chunk_size` | `int` | `65536` | Chunk size for transfer (default 64KB). |
| `auto_upload` | `bool` | `True` | Start upload immediately when files are selected. |

```python
# Single image upload
self.allow_upload('avatar', accept='.jpg,.png,.webp',
                  max_entries=1, max_file_size=5_000_000)

# Multiple document upload
self.allow_upload('documents', accept='.pdf,.docx',
                  max_entries=10, max_file_size=20_000_000)
```

#### `consume_uploaded_entries(name) -> Generator[UploadEntry]`

Consume completed upload entries for a named slot. Yields `UploadEntry` objects and cleans them up after iteration.

```python
for entry in self.consume_uploaded_entries('avatar'):
    # entry.client_name - Original filename
    # entry.client_type - MIME type
    # entry.client_size - File size in bytes
    # entry.data        - Complete file as bytes
    # entry.file        - File as BytesIO object
    # entry.progress    - Upload progress (0-100)
    # entry.complete    - Whether upload is complete
    # entry.error       - Error message or None
    save_file(entry.client_name, entry.file)
```

#### `get_uploads(name) -> list[UploadEntry]`

Get all entries (including in-progress) for an upload slot. Does not consume them.

#### `cancel_upload(name, ref)`

Cancel a specific upload by its reference ID.

### UploadEntry Properties

| Property | Type | Description |
|----------|------|-------------|
| `client_name` | `str` | Original filename from the client |
| `client_type` | `str` | MIME type from the client |
| `client_size` | `int` | Expected file size in bytes |
| `data` | `bytes` | Complete file content as bytes |
| `file` | `BytesIO` | File-like object for use with Django's storage API |
| `progress` | `int` | Upload progress percentage (0-100) |
| `complete` | `bool` | Whether the upload is finished |
| `error` | `str` or `None` | Error message if validation failed |

### Template Directives

#### `dj-upload="name"`

Bind a file input to an upload slot. The `accept` and `multiple` attributes are set automatically from the upload configuration.

```html
<input type="file" dj-upload="avatar">
```

#### `dj-upload-drop="name"`

Create a drag-and-drop zone for file uploads. The `upload-dragover` CSS class is added during drag operations.

```html
<div dj-upload-drop="photos" class="drop-zone">
    Drop files here or <input type="file" dj-upload="photos">
</div>

<style>
.drop-zone { border: 2px dashed #ccc; padding: 2rem; text-align: center; }
.drop-zone.upload-dragover { border-color: #007bff; background: #f0f8ff; }
</style>
```

#### `dj-upload-preview="name"`

Container for image previews. Automatically populated when image files are selected. Each preview includes:
- `.upload-preview-item` wrapper
- `.upload-preview-image` (for image files)
- `.upload-preview-name` (filename)
- `.upload-preview-size` (formatted size)

```html
<div dj-upload-preview="avatar" class="preview-container"></div>
```

#### `dj-upload-progress="name"`

Container for progress bars. Each uploading file gets:
- `.upload-progress-item[data-upload-ref]` wrapper
- `.upload-progress-name` (filename)
- `.upload-progress-track` > `.upload-progress-bar[role=progressbar]`
- `.upload-progress-text` (percentage)

```html
<div dj-upload-progress="documents"></div>
```

### Client-Side Events

| Event | Detail | Description |
|-------|--------|-------------|
| `djust:upload:progress` | `{ref, progress, status, uploadName}` | Upload progress update |
| `djust:upload:error` | `{file, error}` | Client-side validation error |

```javascript
window.addEventListener('djust:upload:progress', (e) => {
    console.log(`${e.detail.uploadName}: ${e.detail.progress}%`);
});
```

## Examples

### Image Upload with Preview

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

### Document Upload with Validation Feedback

```python
class DocumentUploadView(UploadMixin, LiveView):
    template_name = 'upload_docs.html'

    def mount(self, request, **kwargs):
        self.uploaded_files = []
        self.errors = []
        self.allow_upload('docs',
            accept='.pdf,.docx,.txt',
            max_entries=3,
            max_file_size=20_000_000,
        )

    @event_handler()
    def process_uploads(self, **kwargs):
        self.errors = []
        for entry in self.consume_uploaded_entries('docs'):
            if entry.error:
                self.errors.append(f"{entry.client_name}: {entry.error}")
            else:
                path = default_storage.save(
                    f'documents/{entry.client_name}', entry.file
                )
                self.uploaded_files.append({
                    'name': entry.client_name,
                    'size': entry.client_size,
                    'url': default_storage.url(path),
                })
```

## Best Practices

### File Size Limits

- Set `max_file_size` based on your application's needs. The default is 10MB.
- Client-side validation rejects oversized files before upload begins.
- Server-side validation double-checks size after all chunks are received (with 10% tolerance for encoding overhead).

### Accepted File Types

- Use file extensions (`.jpg,.png`) for simple filtering, or MIME types (`image/*`) for broader categories.
- Server-side validation includes magic byte checking for common file types (JPEG, PNG, GIF, WebP, PDF, etc.) to prevent extension spoofing.

### Progress Feedback

- Progress bars are automatically created in `dj-upload-progress` containers.
- Listen for the `djust:upload:progress` event for custom progress UI.
- The `status` field in progress events is one of: `"uploading"`, `"complete"`, `"error"`, `"cancelled"`.

### Cleanup

- `consume_uploaded_entries()` cleans up temp files after iteration. Always iterate fully or call `cancel_upload()` for files you do not want.
- Temp files are stored in a session-specific directory and cleaned up on WebSocket disconnect.
- For large files, consider increasing `chunk_size` to reduce the number of WebSocket frames.
