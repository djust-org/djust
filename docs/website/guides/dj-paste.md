# `dj-paste` — Paste event handling

`dj-paste` fires a server event when the user pastes content into a bound element. The client extracts structured payload from the `ClipboardEvent` — plain text, rich HTML, and any attached files — and sends it as a single handler call. No `dj-hook` needed.

Every chat app, rich-text editor, and data-import UI needs paste handling. `dj-paste` is the complete clipboard-to-server pipeline.

---

## Quick start

```html
<textarea dj-paste="handle_paste" placeholder="Paste anything..."></textarea>
```

```python
from djust import LiveView
from djust.decorators import event_handler

class ChatView(LiveView):
    template_name = "chat/room.html"

    @event_handler
    def handle_paste(self, text: str = "", html: str = "",
                     has_files: bool = False, files: list = None,
                     **kwargs):
        self.last_paste_preview = text[:200]
        if has_files:
            self.flash("Received %d file(s) — uploading..." % len(files))
```

## What gets sent to the handler

Every `dj-paste` event call carries four structured params:

| Param       | Type      | Content |
|-------------|-----------|---------|
| `text`      | `str`     | `clipboardData.getData('text/plain')` — always a string, even if the clipboard was empty. |
| `html`      | `str`     | `clipboardData.getData('text/html')` — rich paste from Word, Google Docs, a web page, etc. Empty when the source was plain text. |
| `has_files` | `bool`    | `True` if `clipboardData.files` was non-empty (i.e. the user copied an image or a file). |
| `files`     | `list[dict]` | File metadata: `{"name", "type", "size"}`. The actual bytes are NOT in this dict — see [Pasting files → uploads](#pasting-files--uploads). |

The client also forwards any positional args from the attribute syntax:

```html
<textarea dj-paste="handle_paste('chat', 42)"></textarea>
```

becomes `handle_paste('chat', 42, text=..., html=..., ...)` on the server. Access them as `kwargs["_args"]` if needed.

## Pasting files → uploads

By default `dj-paste` only sends *metadata* about pasted files. To actually upload the bytes, combine `dj-paste` with `dj-upload` on the same element — the client routes the clipboard `FileList` through the existing upload pipeline (the same path file inputs and drag-drop use):

```html
<div dj-paste="handle_paste" dj-upload="chat_image">
    Drop or paste an image here.
</div>
```

```python
class ChatView(LiveView):
    uploads = {
        "chat_image": {
            "max_file_size": 5 * 1024 * 1024,
            "accept": "image/*",
        }
    }

    @event_handler
    def handle_paste(self, text: str = "", has_files: bool = False, **kwargs):
        if has_files:
            # Files arrive via the upload pipeline — nothing more to do here.
            return
        self.draft = text
```

Pasted files fire the same `djust:upload:error` / `djust:upload:progress` events as file-input uploads, so progress indicators and size-limit errors Just Work.

## Suppressing the native paste

By default, `dj-paste` lets the browser's own paste happen too — the pasted text ends up in the textarea as usual, and your handler sees it via the event. That's the right behaviour for most editors and rich-text fields.

When you want to intercept the paste completely — for example when you're routing image paste to an upload slot and do **not** want the image's data URL dumped into a `<div contenteditable>` — add `dj-paste-suppress`:

```html
<div dj-paste="handle_paste"
     dj-paste-suppress
     dj-upload="chat_image"
     contenteditable>
    Paste an image here.
</div>
```

## Combining with `dj-confirm` and `dj-lock`

`dj-paste` participates in the standard interaction pipeline:

- `dj-confirm` — show a confirmation dialog before firing the event (useful for "are you sure you want to paste from clipboard?" privacy prompts).
- `dj-lock` — skip the event while an earlier handler is still running.

```html
<textarea dj-paste="handle_paste"
          dj-confirm="Import clipboard contents?"
          dj-lock>
</textarea>
```

## When to reach for `dj-paste` vs `dj-input`

- **`dj-input`** fires on every keystroke. The server sees the current value of the field. If a user pastes into a `dj-input`-bound textarea, you get the post-paste value as a single update — no paste metadata, no file support.
- **`dj-paste`** fires once per paste. You receive text + HTML + file metadata. The native paste still happens (unless suppressed), so you can pair `dj-paste` with `dj-input` on the same element without conflict:

```html
<textarea dj-input="set_draft"
          dj-paste="handle_rich_paste"
          placeholder="Type or paste..."></textarea>
```

Use `dj-input` for "keep the server copy of this field in sync" and `dj-paste` for "react specifically when something was pasted" (image upload, language detection on a code snippet, CSV parsing, markdown preview).

## Edge cases

- **Missing `clipboardData`** — very old browsers. The handler bails silently and the native paste still happens.
- **`getData('text/html')` throws** — some browsers block HTML reads from untrusted contexts. `dj-paste` catches the error and sends `html=""`.
- **Empty paste** — `text` and `html` are empty strings, `has_files=False`, `files=[]`. The handler still fires; you can use it as a "user attempted paste" signal.
- **Multiple files** — every file in `clipboardData.files` appears in the `files` list; the upload pipeline uploads them in sequence if `dj-upload` is set.

## See also

- [File uploads](uploads.md) — the `dj-upload` pipeline that `dj-paste` routes files through.
- [Event handlers](event-handlers.md) — the `@event_handler` decorator reference.
