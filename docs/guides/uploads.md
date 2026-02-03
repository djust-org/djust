# File Uploads

djust provides Phoenix-style file uploads over WebSocket with progress tracking, drag-and-drop support, and validation.

## Quick Start

```python
from djust import LiveView, event_handler
from djust.uploads import UploadMixin
from django.core.files.storage import default_storage

class ProfileView(LiveView, UploadMixin):
    template_name = "profile.html"

    def mount(self, request, **kwargs):
        self.avatar_url = None
        # Configure the upload slot
        self.allow_upload(
            'avatar',
            accept='.jpg,.png,.webp',
            max_file_size=5_000_000,  # 5MB
        )

    @event_handler
    def save_profile(self):
        # Process completed uploads
        for entry in self.consume_uploaded_entries('avatar'):
            path = default_storage.save(
                f'avatars/{entry.client_name}',
                entry.file
            )
            self.avatar_url = default_storage.url(path)
```

```html
<!-- profile.html -->
<form dj-submit="save_profile">
    <div class="upload-area" dj-upload="avatar">
        <input type="file" name="avatar" accept=".jpg,.png,.webp">
        <p>Drop image here or click to browse</p>
    </div>

    {% for entry in uploads.avatar.entries %}
        <div class="upload-entry">
            <span>{{ entry.client_name }}</span>
            {% if entry.complete %}
                <span class="success">✓ Uploaded</span>
            {% elif entry.error %}
                <span class="error">{{ entry.error }}</span>
            {% else %}
                <progress value="{{ entry.progress }}" max="100"></progress>
            {% endif %}
        </div>
    {% endfor %}

    {% if avatar_url %}
        <img src="{{ avatar_url }}" alt="Avatar preview">
    {% endif %}

    <button type="submit">Save Profile</button>
</form>
```

## allow_upload Configuration

Configure upload slots with `allow_upload()` in your `mount()` method:

```python
def mount(self, request, **kwargs):
    self.allow_upload(
        name='documents',           # Slot name (referenced in template)
        accept='.pdf,.doc,.docx',   # Allowed extensions
        max_entries=5,              # Max files per slot (default: 1)
        max_file_size=10_000_000,   # Max bytes per file (default: 10MB)
        chunk_size=64 * 1024,       # Chunk size for transfer (default: 64KB)
        auto_upload=True,           # Start upload on selection (default: True)
    )
```

### Accept Formats

The `accept` parameter supports:
- Extensions: `.jpg,.png,.gif`
- MIME types: `image/*`, `application/pdf`
- Mixed: `.jpg,.png,image/webp`

```python
# Images only
self.allow_upload('photos', accept='.jpg,.png,.gif,.webp')

# PDFs and Word docs
self.allow_upload('docs', accept='.pdf,.doc,.docx')

# Any image type
self.allow_upload('images', accept='image/*')
```

## Template: dj-upload Attribute

Mark upload areas with `dj-upload="slot_name"`:

```html
<!-- Simple file input -->
<div dj-upload="avatar">
    <input type="file" accept=".jpg,.png">
</div>

<!-- Drag and drop area -->
<div dj-upload="documents" class="drop-zone">
    <input type="file" multiple accept=".pdf,.docx">
    <p>Drag files here or click to browse</p>
</div>
```

The `dj-upload` container automatically handles:
- File selection via `<input type="file">`
- Drag and drop
- Progress tracking
- Chunked upload over WebSocket

## Processing Uploads

Use `consume_uploaded_entries()` to process completed uploads:

```python
@event_handler
def save_files(self):
    for entry in self.consume_uploaded_entries('documents'):
        # entry.client_name  — Original filename
        # entry.client_type  — MIME type
        # entry.client_size  — File size in bytes
        # entry.data         — File content as bytes
        # entry.file         — BytesIO file-like object
        
        # Save to Django storage
        from django.core.files.storage import default_storage
        path = default_storage.save(f'uploads/{entry.client_name}', entry.file)
        
        # Or create a model instance
        Document.objects.create(
            name=entry.client_name,
            file=entry.file,
            uploaded_by=self.request.user,
        )
```

### UploadEntry Properties

| Property | Type | Description |
|----------|------|-------------|
| `client_name` | str | Original filename |
| `client_type` | str | MIME type from browser |
| `client_size` | int | Expected file size in bytes |
| `data` | bytes | Complete file content |
| `file` | BytesIO | File-like object for Django storage |
| `progress` | int | Upload progress (0-100) |
| `complete` | bool | True when upload finished |
| `error` | str | Error message if validation failed |

## Upload Progress

Track upload progress in templates:

```html
{% for entry in uploads.photos.entries %}
<div class="upload-item">
    <span class="filename">{{ entry.client_name }}</span>
    
    {% if entry.complete %}
        <span class="status success">✓ Complete</span>
    {% elif entry.error %}
        <span class="status error">✗ {{ entry.error }}</span>
    {% else %}
        <div class="progress-bar">
            <div class="progress-fill" style="width: {{ entry.progress }}%"></div>
        </div>
        <span class="percent">{{ entry.progress }}%</span>
    {% endif %}
</div>
{% endfor %}
```

## Multiple Files

Allow multiple file uploads per slot:

```python
def mount(self, request, **kwargs):
    self.allow_upload('photos', accept='.jpg,.png', max_entries=10)
```

```html
<div dj-upload="photos">
    <input type="file" multiple accept=".jpg,.png">
    <p>Select up to 10 photos</p>
</div>

<div class="preview-grid">
    {% for entry in uploads.photos.entries %}
        {% if entry.complete %}
            <img src="data:{{ entry.client_type }};base64,{{ entry.data|base64 }}" />
        {% endif %}
    {% endfor %}
</div>
```

## Validation

### Client-Side Validation

Extensions and file sizes are validated before upload starts:

```python
self.allow_upload(
    'avatar',
    accept='.jpg,.png',       # Only these extensions
    max_file_size=5_000_000,  # Max 5MB
)
```

If a file fails validation, it appears in `entries` with an error:

```html
{% for entry in uploads.avatar.entries %}
    {% if entry.error %}
        <div class="error">{{ entry.client_name }}: {{ entry.error }}</div>
    {% endif %}
{% endfor %}
```

### Magic Byte Validation

djust validates file content by checking magic bytes, not just extensions. This prevents users from renaming malicious files:

```python
# Supported types with magic byte validation:
# - image/jpeg, image/png, image/gif, image/webp
# - application/pdf
# - application/zip
# - video/mp4
# - audio/mpeg
```

If content doesn't match the claimed type, the upload fails with an error.

## Cancel Uploads

Cancel an in-progress upload:

```python
@event_handler
def cancel_upload(self, ref, **kwargs):
    self.cancel_upload('photos', ref)
```

```html
{% for entry in uploads.photos.entries %}
    {% if not entry.complete and not entry.error %}
        <button dj-click="cancel_upload" dj-value-ref="{{ entry.ref }}">
            Cancel
        </button>
    {% endif %}
{% endfor %}
```

## Get Uploads (Non-Consuming)

Get upload entries without consuming them:

```python
# In handler or template context
entries = self.get_uploads('photos')  # Returns list of UploadEntry

# Check if any uploads are in progress
in_progress = [e for e in entries if not e.complete and not e.error]

# Check for completed uploads
completed = [e for e in entries if e.complete]
```

## Full Example: Image Gallery Upload

```python
from djust import LiveView, event_handler
from djust.uploads import UploadMixin
from django.core.files.storage import default_storage
import uuid

class GalleryUploadView(LiveView, UploadMixin):
    template_name = "gallery/upload.html"

    def mount(self, request, **kwargs):
        self.gallery_id = kwargs.get('id')
        self.allow_upload(
            'images',
            accept='.jpg,.jpeg,.png,.gif,.webp',
            max_entries=20,
            max_file_size=10_000_000,  # 10MB per image
        )
        self.uploaded_images = []
        self.error_message = None

    @event_handler
    def save_gallery(self):
        saved_count = 0
        for entry in self.consume_uploaded_entries('images'):
            try:
                # Generate unique filename
                ext = entry.client_name.split('.')[-1]
                filename = f"{uuid.uuid4()}.{ext}"
                path = default_storage.save(f'galleries/{self.gallery_id}/{filename}', entry.file)
                
                # Create database record
                GalleryImage.objects.create(
                    gallery_id=self.gallery_id,
                    image=path,
                    original_name=entry.client_name,
                )
                saved_count += 1
            except Exception as e:
                self.error_message = f"Failed to save {entry.client_name}: {e}"
        
        if saved_count > 0:
            self.push_event("flash", {"message": f"Saved {saved_count} images!", "type": "success"})

    @event_handler
    def cancel_upload(self, ref, **kwargs):
        self.cancel_upload('images', ref)
```

```html
<!-- gallery/upload.html -->
<div class="gallery-upload">
    <h2>Upload Images to Gallery</h2>

    {% if error_message %}
        <div class="alert error">{{ error_message }}</div>
    {% endif %}

    <div dj-upload="images" class="drop-zone">
        <input type="file" multiple accept=".jpg,.jpeg,.png,.gif,.webp">
        <div class="drop-zone-content">
            <svg><!-- Upload icon --></svg>
            <p>Drag images here or click to browse</p>
            <small>Up to 20 images, max 10MB each</small>
        </div>
    </div>

    <div class="upload-list">
        {% for entry in uploads.images.entries %}
        <div class="upload-item {% if entry.error %}error{% elif entry.complete %}complete{% endif %}">
            <div class="preview">
                {% if entry.complete %}
                    <img src="data:{{ entry.client_type }};base64,{{ entry.data|base64 }}">
                {% else %}
                    <div class="placeholder"></div>
                {% endif %}
            </div>
            
            <div class="info">
                <span class="name">{{ entry.client_name }}</span>
                <span class="size">{{ entry.client_size|filesizeformat }}</span>
            </div>

            <div class="status">
                {% if entry.complete %}
                    <span class="success">✓</span>
                {% elif entry.error %}
                    <span class="error">{{ entry.error }}</span>
                {% else %}
                    <progress value="{{ entry.progress }}" max="100"></progress>
                    <button dj-click="cancel_upload" dj-value-ref="{{ entry.ref }}">✗</button>
                {% endif %}
            </div>
        </div>
        {% empty %}
        <p class="empty">No images selected</p>
        {% endfor %}
    </div>

    {% if uploads.images.entries %}
    <div class="actions">
        <button dj-click="save_gallery" 
                {% if not uploads.images.entries|selectattr('complete')|list %}disabled{% endif %}>
            Save to Gallery
        </button>
    </div>
    {% endif %}
</div>

<style>
.drop-zone {
    border: 2px dashed #ccc;
    border-radius: 8px;
    padding: 40px;
    text-align: center;
    cursor: pointer;
    transition: border-color 0.2s;
}
.drop-zone:hover, .drop-zone.drag-over {
    border-color: #007bff;
}
.upload-item {
    display: flex;
    align-items: center;
    padding: 8px;
    border-bottom: 1px solid #eee;
}
.upload-item .preview img {
    width: 48px;
    height: 48px;
    object-fit: cover;
    border-radius: 4px;
}
</style>
```

## Tips

1. **Always validate on server**: Magic byte validation is automatic, but add business logic validation in your handler.

2. **Show upload limits**: Display file size limits and allowed types to users.

3. **Handle errors gracefully**: Check `entry.error` and show meaningful messages.

4. **Clean up on unmount**: Upload temp files are cleaned automatically when the view disconnects.

5. **Large file uploads**: For very large files, consider increasing `chunk_size` for better throughput:
   ```python
   self.allow_upload('video', max_file_size=500_000_000, chunk_size=256*1024)
   ```
