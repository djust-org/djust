---
title: "Tutorial: Build a file upload with live progress"
slug: tutorial-file-uploads-progress
section: guides
order: 43
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
- Stores one avatar per signed-in user, replacing (and deleting) the
  previous file.
- Surfaces a clear error message for each rejection case
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

The avatar belongs to a user, so store it on a per-user profile:

```python
# myapp/models.py
from django.conf import settings
from django.db import models


class Profile(models.Model):
    user = models.OneToOneField(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="profile"
    )
    avatar = models.FileField(upload_to="avatars/", blank=True)
```

Create the LiveView. The mixin is `UploadMixin`; configuration
happens in `mount()` via `allow_upload()`:

```python
# myapp/views.py
from uuid import uuid4

from django.core.files.base import ContentFile

from djust import LiveView, state
from djust.decorators import event_handler
from djust.uploads import UploadMixin, validate_magic_bytes

from .models import Profile


class AvatarView(UploadMixin, LiveView):
    template_name = "avatar.html"
    # Uploads write to your storage: never accept them from anonymous
    # visitors. Anonymous requests are redirected to LOGIN_URL.
    login_required = True

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
        profile = Profile.objects.filter(user=request.user).first()
        if profile and profile.avatar:
            self.avatar_url = profile.avatar.url
            self.avatar_name = profile.avatar.name.rsplit("/", 1)[-1]
            self.avatar_size = profile.avatar.size
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
  <!-- Filled by the script below for files refused before they upload. -->
  <p id="avatar-upload-error" role="alert" class="err" dj-update="ignore"></p>
</form>
```

And the few lines that drive the progress bar and the early-rejection
message. Put them in your base template, after the `dj-root` element,
because a `<script>` inside the reactive root doesn't run when the page
arrives by `dj-navigate`:

```html
<script>
  function avatarUploadError(text) {
    const el = document.getElementById("avatar-upload-error");
    if (el) el.textContent = text;  // textContent: file names are user input
  }
  window.addEventListener("djust:upload:progress", (e) => {
    const bar = document.getElementById("avatar-progress");
    if (bar && e.detail.uploadName === "avatar") {
      bar.value = e.detail.progress;
      avatarUploadError("");
    }
  });
  // Over max_file_size: the client refuses the file before sending it.
  window.addEventListener("djust:upload:error", (e) => {
    avatarUploadError(e.detail.file + " is larger than 5 MB.");
  });
  // Wrong extension, MIME type or active content (SVG, HTML): the server
  // refuses to register the upload and answers with an error frame.
  window.addEventListener("djust:error", (e) => {
    if (e.detail && String(e.detail.error).startsWith("Upload rejected")) {
      avatarUploadError("Only .jpg, .png and .webp images up to 5 MB are accepted.");
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
| `djust:upload:error` | Window event fired when the client refuses a file itself: `e.detail.file` and `e.detail.error` (`"File too large"`). |
| `djust:error` with `"Upload rejected …"` | The server refused to register the file (extension or MIME type outside `accept`, active content, or `max_entries` reached). No upload entry exists, so the view never sees it. |

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
        latest = None

        # Read each entry's bytes INSIDE the loop: the temp files are
        # deleted when the generator is exhausted. Drain it fully, so every
        # completed entry is cleaned up, and keep only the newest valid
        # file: a user who picks a second file before saving has two
        # completed entries, and only one of them is their avatar.
        for entry in self.consume_uploaded_entries("avatar"):
            data = entry.data
            # Magic-byte check: decide the type from the content, not
            # from the client's filename or MIME type.
            kind = next((m for m in _ACCEPTED if validate_magic_bytes(data, m)), None)
            if kind is None:
                self.error = "That doesn't look like a JPEG, PNG or WebP image."
                continue
            latest = (data, entry.safe_client_name, entry.client_name)

        if latest is None:
            if not self.error:
                rejected = [e.error for e in self.get_uploads("avatar") if e.error]
                self.error = rejected[0] if rejected else "No file uploaded yet."
            return

        data, safe_name, display_name = latest
        profile, _ = Profile.objects.get_or_create(user=self.request.user)
        if profile.avatar:
            profile.avatar.delete(save=False)  # one avatar per user
        # upload_to="avatars/" + a server-generated directory: the stored
        # path never comes from the client.
        profile.avatar.save(f"{uuid4().hex}/{safe_name}", ContentFile(data), save=True)
        self.error = ""
        self.avatar_url = profile.avatar.url
        self.avatar_name = display_name
        self.avatar_size = len(data)

    @event_handler
    def reset_avatar(self, **kwargs):
        self.avatar_url = ""
        self.avatar_name = ""
        self.avatar_size = 0
        self.error = ""
```

Four things to call out:

1. **`consume_uploaded_entries("avatar")`** is the way to read
   the bytes. It is a generator over the completed entries; each
   entry is yielded once, and all of them are removed and their temp
   files deleted when the generator is exhausted — calling it again
   returns nothing, which prevents accidental double-saves. Don't
   `list()` it and read the entries afterwards: by then their temp
   files are gone and `entry.data` is empty.
2. **`max_entries=1` limits uploads in flight, not completed ones.**
   Once the first file finishes, the user can pick another before
   pressing Save, so the handler can see two entries. It keeps the
   newest valid one, and deletes the previously stored avatar.
3. **`entry.client_name` is hostile input.** Build storage paths from
   `entry.safe_client_name` (a path-safe basename) under a
   server-generated directory, never from `client_name`, which can
   carry `../` segments. Use `client_name` only for display.
4. **Where each rejection shows up.** A file over `max_file_size` is
   refused by the client (`djust:upload:error`), and an extension or
   MIME type outside `accept` by the server at registration
   (`djust:error`, "Upload rejected …"). Neither creates an upload
   entry, so the view never hears about them; the Step 2 script shows
   the message. A file whose magic bytes don't match its declared type
   (`evil.exe` renamed to `cute.png`) uploads but never completes; its
   message is on the entry's `error`, which `save_avatar` shows. The
   explicit `validate_magic_bytes` check keeps the decision about what
   you store in your own code.

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
      │                         │     ↓ profile.avatar.save()
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
- **External storage (S3, GCS, Azure):** set the `FileField`'s
  `storage=` (or Django's default storage) to a configured backend;
  `ContentFile(data)` works with any of them. To
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
