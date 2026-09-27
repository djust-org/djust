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
- **Progress is a DOM event** the framework dispatches
  (`djust:upload:progress`); a few lines of JS point a `<progress>`
  element at it.
- **Validation lives on the server** and runs against the
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
| `UploadMixin.allow_upload()` configuration | [Uploads](uploads.md) |
| `dj-upload`, `dj-upload-drop`, `dj-upload-preview` directives + the `djust:upload:progress` event | [Uploads](uploads.md) |
| Server-side magic-byte validation | This tutorial |
| Pairing `@event_handler` with the upload completion lifecycle | This tutorial |

> **Prerequisites:** [Your First LiveView](../getting-started/first-liveview.md), the [search-as-you-type
> tutorial](tutorial-search-as-you-type.md) (optional but useful
> for the loading-state pattern). A working Django project with
> media storage configured (`MEDIA_ROOT`, `MEDIA_URL`).

---

## Step 1 — Configure the upload

Create the LiveView. The mixin is `UploadMixin`; configuration
happens in `mount()` via `allow_upload()`:

```python
# myapp/views.py
from uuid import uuid4

from django.core.files.storage import default_storage

from djust import LiveView, state
from djust.decorators import event_handler
from djust.uploads import UploadMixin, validate_magic_bytes


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

`auto_upload` defaults to `True`: the file starts streaming as soon
as the user picks or drops it, and the form submit just tells the
server to keep what already arrived.

---

## Step 2 — Add the drop zone, file input, and progress bar

```html
<!-- myapp/templates/avatar.html -->
<form dj-root dj-submit="save_avatar">
  <div dj-upload-drop="avatar" class="drop-zone">
    {% if not avatar_url %}
      <p>Drag your avatar here, or</p>
      <label class="file-button">
        Choose a file…
        <input type="file" dj-upload="avatar" hidden />
      </label>
      <p class="hint">.jpg / .png / .webp · 5 MB max</p>

      <div dj-upload-preview="avatar" class="preview"></div>
      <progress id="avatar-progress" dj-update="ignore" max="100" value="0"></progress>
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

And the few lines that drive the progress bar. Put them in your base
template, after the `dj-root` element, because a `<script>` inside
the reactive root doesn't run when the page arrives by `dj-navigate`:

```html
<script>
  window.addEventListener("djust:upload:progress", (e) => {
    const bar = document.getElementById("avatar-progress");
    if (bar && e.detail.uploadName === "avatar") {
      bar.value = e.detail.progress;
    }
  });
</script>
```

What each upload-specific piece does:

| Piece | Behavior |
|---|---|
| `dj-upload="avatar"` on `<input type="file">` | When the user picks a file, stream it into the `avatar` slot (`accept` and `multiple` are set on the input for you). |
| `dj-upload-drop="avatar"` on a wrapper `<div>` | Make this element a drop zone for the `avatar` slot. Adds the `upload-dragover` class while a file is being dragged over. |
| `dj-upload-preview="avatar"` | Container the client fills with a preview thumbnail for image files. |
| `djust:upload:progress` | Window event fired as the server acknowledges chunks; `e.detail` carries `uploadName`, `progress` (0–100) and `status`. |
| `dj-update="ignore"` + `id` on the `<progress>` | Keeps server re-renders from resetting the `value` the script sets. |

**The upload starts when the file is picked or dropped**, and the
progress bar fills as the chunks stream. Clicking "Save avatar"
then calls `save_avatar` on the server, which keeps the file that
arrived.

---

## Step 3 — The server-side handler

```python
# myapp/views.py — append to AvatarView

_ACCEPTED = {"image/jpeg": ".jpg", "image/png": ".png", "image/webp": ".webp"}


class AvatarView(UploadMixin, LiveView):
    # ... mount() as before ...

    @event_handler
    def save_avatar(self, **kwargs):
        self.error = ""
        saved = False

        # Read each entry INSIDE the loop: the entry's temp file is
        # deleted once the loop moves past it.
        for entry in self.consume_uploaded_entries("avatar"):
            data = entry.data
            # Magic-byte check: decide the type from the content, not
            # from the client's filename or MIME type.
            kind = next((m for m in _ACCEPTED if validate_magic_bytes(data, m)), None)
            if kind is None:
                self.error = "That doesn't look like a JPEG, PNG or WebP image."
                continue
            path = default_storage.save(
                f"avatars/{uuid4().hex}/{entry.safe_client_name}", entry.file
            )
            self.avatar_url = default_storage.url(path)
            self.avatar_name = entry.client_name
            self.avatar_size = len(data)
            saved = True

        if not saved and not self.error:
            rejected = [e.error for e in self.get_uploads("avatar") if e.error]
            self.error = rejected[0] if rejected else "No file uploaded yet."

    @event_handler
    def reset_avatar(self, **kwargs):
        self.avatar_url = ""
        self.avatar_name = ""
        self.avatar_size = 0
        self.error = ""
```

Three things to call out:

1. **`consume_uploaded_entries("avatar")`** is the way to read
   the bytes. It is a generator over the completed entries; each
   entry is yielded once and cleaned up after the loop moves on —
   calling it again returns nothing, which prevents accidental
   double-saves. Don't `list()` it and read the entries afterwards:
   by then their temp files are gone and `entry.data` is empty.
2. **`entry.client_name` is hostile input.** Build storage paths from
   `entry.safe_client_name` (a path-safe basename) under a
   server-generated directory, never from `client_name`, which can
   carry `../` segments. Use `client_name` only for display.
3. **Content decides the type.** djust already rejects files over
   `max_file_size`, extensions outside `accept`, and content whose
   magic bytes don't match the declared type (so `evil.exe` renamed
   to `cute.png` never completes). Rejected files never reach
   `consume_uploaded_entries()`; their message is on the entry's
   `error`, which the handler shows. The explicit
   `validate_magic_bytes` check keeps the decision about what you
   store in your own code.

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
      │ ──── start upload ─────►│  (UploadMixin checks size,
      │                         │   extension and type)
      │                         │
      │ ── chunk 1 (binary) ───►│
      │ ── chunk 2 (binary) ───►│  (each chunk is 64 KB)
      │ ── chunk N (binary) ───►│
      │ ◄ progress: 8% ─────────│  (interleaved with chunks)
      │ ◄ progress: 16% ────────│
      │ ◄ progress: 100% ───────│  (magic bytes checked)
      │                         │
      │  user clicks "Save"     │
      │ ─── dj-submit ─────────►│  → calls save_avatar()
      │                         │     ↓ consume_uploaded_entries()
      │                         │     ↓ magic-byte check
      │                         │     ↓ default_storage.save()
      │                         │     ↓ self.avatar_url = ...
      │ ◄── HTML diff ──────────│  (drop zone replaced with preview card)
```

The whole thing is one WebSocket session — the same one carrying
your normal events. `dj-form-pending` covers the in-flight UX on
the submit button, the progress event fills the `<progress>` bar, and the
final state change replaces the drop zone with the saved-avatar
card via the standard diff cycle.

---

## Where to go next

- **Multiple files at once:** raise `max_entries` (e.g. `max_entries=10`)
  and iterate `consume_uploaded_entries("attachments")`. The drop
  zone accepts a multi-select drag, and the progress event fires
  per-entry.
- **Preview-then-confirm:** the bytes stream on selection, but
  nothing is stored until `save_avatar` runs. The preview
  thumbnail plus a "Looks good?" submit button is already a
  confirm step; `window.djust.uploads.cancelUpload(ref)` stops a
  transfer the user changes their mind about.
- **External storage (S3, GCS, Azure):** swap `default_storage` for
  a configured backend. `entry.file` is a file-like object
  (`BytesIO`), so any storage backend that accepts one works. To
  stream chunks straight to the store instead, pass a `writer=` to
  `allow_upload()` (see [Uploads](uploads.md)).
- **Resumable uploads for very large files:** pass
  `resumable=True` to `allow_upload()`; see the
  [Uploads guide](uploads.md) — the framework persists per-session
  chunk ranges, so a dropped WebSocket reconnects and resumes
  mid-file.
- **Direct-to-S3 presigned uploads:** if you want the bytes to
  bypass the server entirely, use `@server_function` to mint a
  presigned URL and have the client PUT directly to S3 — same
  pattern as the [typeahead-with-server_function tutorial](tutorial-typeahead-server-function.md).

The five-primitive recipe (`UploadMixin`, `allow_upload`,
`dj-upload`, `djust:upload:progress`, `consume_uploaded_entries`) is
the same shape every upload feature uses — single file, multiple
files, image-with-crop, video-with-thumbnail, CSV import. Once
the avatar uploader works, dragging in more file types is mostly
new server-side validation.
