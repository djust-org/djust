"""
Uploads Demo — File uploads via WebSocket with progress tracking.

Demonstrates:
    - allow_upload() to configure upload slots
    - Drag-and-drop upload zone
    - Progress bar during upload
    - Multiple file support
    - File size/type validation
    - consume_uploaded_entries() to process files
    - Thumbnail preview for images
"""

import base64
import os
import mimetypes
from pathlib import Path

from djust import LiveView
from djust.decorators import event_handler
from djust.uploads import UploadMixin


class UploadsDemoView(UploadMixin, LiveView):
    """
    File upload demo with drag-and-drop, progress, and preview.
    
    Features:
    - Drag-and-drop upload zone
    - Multiple file selection
    - Real-time progress bars
    - Image thumbnail previews
    - File type/size validation
    - Process uploaded files
    """
    
    template_name = "demos/uploads_demo.html"
    
    # File type icons
    FILE_ICONS = {
        "image": "🖼️",
        "video": "🎬",
        "audio": "🎵",
        "application/pdf": "📄",
        "text": "📝",
        "application/zip": "📦",
        "default": "📎",
    }

    def mount(self, request, **kwargs):
        """Configure upload slots."""
        # Configure image upload slot
        self.allow_upload(
            "images",
            accept=".jpg,.jpeg,.png,.gif,.webp",
            max_entries=5,
            max_file_size=5_000_000,  # 5MB
        )
        
        # Configure document upload slot
        self.allow_upload(
            "documents",
            accept=".pdf,.txt,.doc,.docx,.csv",
            max_entries=3,
            max_file_size=10_000_000,  # 10MB
        )
        
        # Processed files list
        self.processed_files = []
        self.upload_errors = []
        
        # Demo mode (don't actually save files)
        self.demo_mode = True

    def get_file_icon(self, mime_type):
        """Get icon for file type."""
        if mime_type:
            # Check exact match
            if mime_type in self.FILE_ICONS:
                return self.FILE_ICONS[mime_type]
            # Check category
            category = mime_type.split("/")[0]
            if category in self.FILE_ICONS:
                return self.FILE_ICONS[category]
        return self.FILE_ICONS["default"]

    def get_context_data(self):
        ctx = super().get_context_data()
        
        # Get upload state
        upload_ctx = self._get_upload_context()
        
        # Add icons and preview URLs to entries
        if "uploads" in upload_ctx:
            for slot_name, slot_data in upload_ctx["uploads"].items():
                for entry in slot_data.get("entries", []):
                    entry["icon"] = self.get_file_icon(entry.get("client_type"))
                    entry["is_image"] = entry.get("client_type", "").startswith("image/")
                    entry["size_display"] = self._format_size(entry.get("client_size", 0))
        
        ctx.update({
            "uploads": upload_ctx.get("uploads", {}),
            "processed_files": self.processed_files,
            "upload_errors": self.upload_errors,
            "demo_mode": self.demo_mode,
        })
        return ctx

    def _format_size(self, size_bytes):
        """Format file size for display."""
        if size_bytes < 1024:
            return f"{size_bytes} B"
        elif size_bytes < 1024 * 1024:
            return f"{size_bytes / 1024:.1f} KB"
        else:
            return f"{size_bytes / (1024 * 1024):.1f} MB"

    @event_handler
    def process_images(self, **kwargs):
        """Process uploaded images."""
        self.upload_errors = []
        
        for entry in self.consume_uploaded_entries("images"):
            try:
                # In a real app, you'd save to storage
                # For the demo, we just show what was uploaded
                
                # Create thumbnail preview (base64 for small images)
                thumbnail = None
                if entry.client_type.startswith("image/") and entry.client_size < 1_000_000:
                    data = entry.data
                    thumbnail = f"data:{entry.client_type};base64,{base64.b64encode(data).decode()}"
                
                self.processed_files.append({
                    "name": entry.client_name,
                    "type": entry.client_type,
                    "size": entry.client_size,
                    "size_display": self._format_size(entry.client_size),
                    "icon": self.get_file_icon(entry.client_type),
                    "thumbnail": thumbnail,
                    "is_image": True,
                })
                
            except Exception as e:
                self.upload_errors.append(f"Error processing {entry.client_name}: {str(e)}")
        
        if self.processed_files:
            self.push_event("toast", {
                "message": f"Processed {len(self.processed_files)} image(s)!",
                "type": "success",
            })

    @event_handler
    def process_documents(self, **kwargs):
        """Process uploaded documents."""
        self.upload_errors = []
        
        for entry in self.consume_uploaded_entries("documents"):
            try:
                self.processed_files.append({
                    "name": entry.client_name,
                    "type": entry.client_type,
                    "size": entry.client_size,
                    "size_display": self._format_size(entry.client_size),
                    "icon": self.get_file_icon(entry.client_type),
                    "thumbnail": None,
                    "is_image": False,
                })
                
            except Exception as e:
                self.upload_errors.append(f"Error processing {entry.client_name}: {str(e)}")
        
        if self.processed_files:
            self.push_event("toast", {
                "message": f"Processed document(s)!",
                "type": "success",
            })

    @event_handler
    def cancel_upload(self, slot=None, ref=None, **kwargs):
        """Cancel a specific upload."""
        if slot and ref:
            super().cancel_upload(slot, ref)

    @event_handler
    def clear_processed(self, **kwargs):
        """Clear processed files list."""
        self.processed_files = []
        self.upload_errors = []

    @event_handler
    def remove_file(self, index=None, **kwargs):
        """Remove a file from processed list."""
        try:
            idx = int(index)
            if 0 <= idx < len(self.processed_files):
                self.processed_files.pop(idx)
        except (ValueError, TypeError):
            pass
