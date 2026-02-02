"""
Upload Demo Views

Demonstrates djust file upload capabilities:
1. Profile picture upload with preview
2. Multiple file upload with progress bars
3. Drag-and-drop zone
"""

import os
from djust.live_view import LiveView
from djust.uploads import UploadMixin


class ProfileUploadView(LiveView, UploadMixin):
    """Single file upload with image preview."""

    template_name = "upload-demo/profile.html"

    def mount(self, request, **kwargs):
        self.avatar_url = None
        self.message = ""
        self.allow_upload(
            "avatar",
            accept=".jpg,.jpeg,.png,.webp",
            max_entries=1,
            max_file_size=5_000_000,  # 5MB
        )

    def save_avatar(self):
        """Handle avatar save after upload completes."""
        for entry in self.consume_uploaded_entries("avatar"):
            # In a real app, use Django's default_storage
            upload_dir = "/tmp/djust_uploads"
            os.makedirs(upload_dir, exist_ok=True)
            path = os.path.join(upload_dir, entry.client_name)
            with open(path, "wb") as f:
                f.write(entry.data)
            self.avatar_url = f"/uploads/{entry.client_name}"
            self.message = f"Saved {entry.client_name} ({len(entry.data)} bytes)"

    def clear_avatar(self):
        self.avatar_url = None
        self.message = ""


class MultiUploadView(LiveView, UploadMixin):
    """Multiple file upload with progress tracking."""

    template_name = "upload-demo/multi.html"

    def mount(self, request, **kwargs):
        self.uploaded_files = []
        self.allow_upload(
            "documents",
            accept=".pdf,.txt,.csv,.json,.jpg,.png",
            max_entries=5,
            max_file_size=10_000_000,  # 10MB
        )

    def save_files(self):
        """Process all uploaded files."""
        for entry in self.consume_uploaded_entries("documents"):
            self.uploaded_files.append({
                "name": entry.client_name,
                "type": entry.client_type,
                "size": len(entry.data),
            })

    def clear_files(self):
        self.uploaded_files = []


class DragDropView(LiveView, UploadMixin):
    """Drag-and-drop upload zone demo."""

    template_name = "upload-demo/dragdrop.html"

    def mount(self, request, **kwargs):
        self.gallery = []
        self.allow_upload(
            "images",
            accept=".jpg,.jpeg,.png,.gif,.webp",
            max_entries=10,
            max_file_size=8_000_000,  # 8MB
        )

    def process_images(self):
        """Process dropped images."""
        for entry in self.consume_uploaded_entries("images"):
            self.gallery.append({
                "name": entry.client_name,
                "type": entry.client_type,
                "size": len(entry.data),
            })

    def clear_gallery(self):
        self.gallery = []
