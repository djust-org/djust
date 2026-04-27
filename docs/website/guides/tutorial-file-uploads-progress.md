---
title: "Tutorial: Build a file upload with live progress"
slug: tutorial-file-uploads-progress
section: guides
order: 64
level: intermediate
description: "Build an avatar uploader with drag-and-drop, a real-time progress bar, server-side validation, and a confirmation card — using only djust's UploadMixin and a few template directives. Files chunk over the WebSocket, so you don't ship a multipart form or a separate upload endpoint."
---

# Tutorial: Build a file upload with live progress

Most uploads in production look the same: a drop zone, a progress
indicator, a server-side validation pass, and a confirmation card.
Stitching that together with a normal HTML `<form enctype="multipart/form-data">`
plus XHR progress events is doable but tedious — the JS owns the
upload state, the server owns the validation, and you need glue
code on both sides to keep them in sync.

djust's `UploadMixin` collapses the whole flow:

- **Files chunk over the existing WebSocket** as binary frames
  (64 KB chunks). No multipart form, no separate upload endpoint.
- **Progress is a DOM event** the framework dispatches; you wire a
  `<progress>` element with one attribute.
- **Validation lives entirely on the server** and runs against the
  fully-assembled bytes — no client-side `accept=` lying about file
  type.
- **The same LiveView** that renders the form also handles the
  upload completion, so showing the saved file is a normal state
  reassignment.

By the end of this tutorial you'll have an avatar uploader that:

- Accepts `.jpg / .png / .webp`, max 5 MB, single file.
- Has a drag-and-drop drop zone with a hover state.
- Shows a live progress bar while the file streams over WebSocket.
- Validates magic bytes server-side (so renaming `evil.exe` to
  `cute.png` is rejected).
- Renders the saved avatar inline on success, with size and
  filename, and a "Replace" button to start over.
- Surfaces a typed error message for any of the rejection cases
  (too big, wrong type, magic-byte mismatch).

| You'll learn | Documented in |
|---|---|
| `UploadMixin.allow_upload()` configuration | [Uploads](/guides/uploads/) |
| `dj-upload`, `dj-upload-drop`, `dj-upload-progress` directives | [Uploads](/guides/uploads/) |
| Server-side magic-byte validation | This tutorial |
| Pairing `@event_handler` with the upload completion lifecycle | This tutorial |

> **Prerequisites:** [Quickstart](/getting-started/), the [search-as-you-type
> tutorial](/guides/tutorial-search-as-you-type/) (optional but useful
> for the loading-state pattern). A working Django project with
> media storage configured (`MEDIA_ROOT`, `MEDIA_URL`).

---

## Step 1 — Configure the upload

Create the LiveView. The mixin is `UploadMixin`; configuration
happens in `mount()` via `allow_upload()`:

```python
# myapp/views.py
from djust import LiveView, state
from djust.uploads import UploadMixin
from djust.decorators import event_handler

from django.core.files.storage import default_storage


class AvatarView(UploadMixin, LiveView):
    template_name = "avatar.html"

    avatar_url = state("")
    avatar_name = state("")
    avatar_size = state(0)
    error = state("")

    def mount(self, request, **kwargs):
        self.allow_upload(
            "avatar",
            accept=".jpg,.jpeg,.png,.webp",
            max_entries=1,
            max_file_size=5_000_000,  # 5 MB
        )
```

`allow_upload(name, ...)` registers an upload slot keyed by `name`.
Templates and consumption methods address that slot by the same
name — so `dj-upload="avatar"` writes into this slot and
`self.consume_uploaded_entries("avatar")` reads from it.

---

## Step 2 — Add the drop zone, file input, and progress bar

```html
<!-- myapp/templates/avatar.html -->
<form dj-submit="save_avatar">
  <div dj-upload-drop="avatar" class="drop-zone">
    {% if not avatar_url %}
      <p>Drag your avatar here, or</p>
      <label class="file-button">
        Choose a file…
        <input type="file" dj-upload="avatar" hidden />
      </label>
      <p class="hint">.jpg / .png / .webp · 5 MB max</p>

      <div dj-upload-preview="avatar" class="preview"></div>
      <progress dj-upload-progress="avatar" max="100" value="0"></progress>
    {% else %}
      <img src="{{ avatar_url }}" alt="" class="avatar" />
      <p>
        <strong>{{ avatar_name }}</strong>
        &middot; {{ avatar_size|filesizeformat }}
      </p>
      <button type="button" dj-click="reset_avatar">Replace</button>
    {% endif %}
  </div>

  {% if not avatar_url %}
    <button type="submit" dj-form-pending="disabled">
      <span dj-form-pending="hide">Save avatar</span>
      <span dj-form-pending="show" hidden>Saving&hellip;</span>
    </button>
  {% endif %}

  {% if error %}
    <p role="alert" class="err">{{ error }}</p>
  {% endif %}
</form>
```

What each upload-specific attribute does:

| Attribute | Behavior |
|---|---|
| `dj-upload="avatar"` on `<input type="file">` | When the user picks a file, queue it into the `avatar` slot. Doesn't start the upload yet. |
| `dj-upload-drop="avatar"` on a wrapper `<div>` | Make this element a drop zone for the `avatar` slot. Adds the `upload-dragover` class while a file is being dragged over. |
| `dj-upload-preview="avatar"` | Render an inline preview thumbnail (for image types) before the upload starts. |
| `dj-upload-progress="avatar"` | The framework writes percentage progress (0–100) into the `value` attribute as the file streams. Works with native `<progress>` for free. |

**The actual upload is triggered by the form submit** — clicking
"Save avatar" tells djust to start chunking the queued file over
the WebSocket and call `save_avatar` on the server when the bytes
have all arrived.

---

## Step 3 — The server-side handler

```python
# myapp/views.py — append to AvatarView

import imghdr  # stdlib magic-byte detection (deprecated in 3.13;
                # in modern code use Pillow's Image.open or python-magic)


_MAGIC_TO_EXT = {"jpeg": ".jpg", "png": ".png", "webp": ".webp"}


class AvatarView(UploadMixin, LiveView):
    # ... mount() as before ...

    @event_handler
    def save_avatar(self, **kwargs):
        self.error = ""

        entries = list(self.consume_uploaded_entries("avatar"))
        if not entries:
            self.error = "No file selected."
            return

        entry = entries[0]

        # Magic-byte check — never trust the client filename or accept= attr.
        kind = imghdr.what(entry.file)
        entry.file.seek(0)
        if kind not in _MAGIC_TO_EXT:
            self.error = (
                f"That doesn't look like an image we accept "
                f"(detected type: {kind or 'unknown'})."
            )
            return

        path = default_storage.save(
            f"avatars/{entry.client_name}", entry.file
        )

        self.avatar_url = default_storage.url(path)
        self.avatar_name = entry.client_name
        self.avatar_size = entry.size

    @event_handler
    def reset_avatar(self, **kwargs):
        self.avatar_url = ""
        self.avatar_name = ""
        self.avatar_size = 0
        self.error = ""
```

Three things to call out:

1. **`consume_uploaded_entries("avatar")`** is the only way to read
   the bytes. It returns an iterator of entries; each entry is
   exposed once per upload — calling it again returns nothing,
   which prevents accidental double-saves.
2. **`entry.client_name` is hostile input.** Use `default_storage.save`
   (which sanitizes paths and avoids overwrites) instead of writing
   directly to a path you build from `client_name`.
3. **Magic-byte check is the real validator.** The `accept=`
   attribute on the client and the `accept=` parameter on
   `allow_upload()` are convenience filters; they don't stop a
   determined attacker from POSTing arbitrary bytes. Run an actual
   detection against the uploaded bytes before saving.

---

## Step 4 — Style the drop zone

```html
<style>
  .drop-zone {
    display: flex;
    flex-direction: column;
    align-items: center;
    gap: 0.75rem;
    padding: 2rem;
    border: 2px dashed var(--color-border, #d4d4d8);
    border-radius: 0.5rem;
    transition: border-color 0.15s, background 0.15s;
  }
  .drop-zone.upload-dragover {
    border-color: var(--color-accent, #3b82f6);
    background: rgba(59, 130, 246, 0.04);
  }
  .file-button {
    display: inline-block;
    padding: 0.5rem 1rem;
    border: 1px solid var(--color-border, #d4d4d8);
    border-radius: 0.25rem;
    cursor: pointer;
  }
  .preview img {
    max-width: 240px;
    height: auto;
    border-radius: 0.25rem;
  }
  progress {
    width: 100%;
    height: 6px;
    margin-top: 0.5rem;
  }
  .avatar {
    width: 96px;
    height: 96px;
    border-radius: 50%;
    object-fit: cover;
  }
  .err { color: #dc2626; }
</style>
```

The framework adds the `upload-dragover` class to the drop zone
while a file is being dragged over the page (so the visual hover
state activates without any JS from you).

---

## What just happened, end to end

```
   Browser                    Server
      │                         │
      │  user picks "me.png"    │
      │  (just queued)          │
      │                         │
      │  user clicks "Save"     │
      │ ──── start upload ─────►│  (UploadMixin queues an
      │                         │   upload session)
      │                         │
      │ ── chunk 1 (binary) ───►│
      │ ── chunk 2 (binary) ───►│  (each chunk is 64 KB)
      │ ── chunk N (binary) ───►│
      │ ◄ progress: 8% ─────────│  (interleaved with chunks)
      │ ◄ progress: 16% ────────│
      │ ◄ progress: 100% ───────│
      │                         │
      │ ─── upload complete ───►│  → calls save_avatar()
      │                         │     ↓ consume_uploaded_entries()
      │                         │     ↓ magic-byte check
      │                         │     ↓ default_storage.save()
      │                         │     ↓ self.avatar_url = ...
      │ ◄── HTML diff ──────────│  (drop zone replaced with preview card)
```

The whole thing is one WebSocket session — the same one carrying
your normal events. `dj-form-pending` covers the in-flight UX on
the submit button, the `<progress>` element auto-updates, and the
final state change replaces the drop zone with the saved-avatar
card via the standard diff cycle.

---

## Where to go next

- **Multiple files at once:** raise `max_entries` (e.g. `max_entries=10`)
  and iterate `consume_uploaded_entries("attachments")`. The drop
  zone accepts a multi-select drag, and the progress event fires
  per-entry.
- **Real-time preview-then-confirm:** show a "Looks good?" preview
  step BEFORE the upload starts by listening for `dj-upload`'s
  `change` event in JS and showing a confirmation modal. The
  upload only starts when the form submits.
- **External storage (S3, GCS, Azure):** swap `default_storage` for
  a configured backend. Because the chunks land in
  `entry.file` (a real Django `UploadedFile`), any storage backend
  that accepts a file-like object works.
- **Resumable uploads for very large files:** see the
  [Uploads guide § resumable uploads](/guides/uploads/) — the
  framework persists per-session chunk ranges, so a dropped
  WebSocket reconnects and resumes mid-file.
- **Direct-to-S3 presigned uploads:** if you want the bytes to
  bypass the server entirely, use `@server_function` to mint a
  presigned URL and have the client PUT directly to S3 — same
  pattern as the [typeahead-with-server_function tutorial](/guides/tutorial-typeahead-server-function.md).

The five-primitive recipe (`UploadMixin`, `allow_upload`,
`dj-upload`, `dj-upload-progress`, `consume_uploaded_entries`) is
the same shape every upload feature uses — single file, multiple
files, image-with-crop, video-with-thumbnail, CSV import. Once
the avatar uploader works, dragging in more file types is mostly
new server-side validation.
