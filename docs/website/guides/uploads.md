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

## Best Practices

- **Set `max_file_size`** based on your needs. Client-side validation rejects oversized files before upload begins; server-side validates after all chunks arrive.
- **Use file extensions** (`.jpg,.png`) for simple filtering or MIME types (`image/*`) for broader categories. Server-side magic byte checking prevents extension spoofing.
- **Always iterate fully** over `consume_uploaded_entries()` or call `cancel_upload()` for unwanted files. Temp files are cleaned up on WebSocket disconnect.
- **For large files**, increase `chunk_size` to reduce the number of WebSocket frames.
