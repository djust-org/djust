# Bidirectional binding inventory

**Date**: 2026-05-02
**Source**: Retro v0.9.2-6 / PR #1304 (dj-dialog reverse-sync)
**Closes**: #1308

## Background

djust's `dj-*` attribute bindings are predominantly server→client (one-way).
Native browser events that mutate element state independently of the server
(ESC on dialog, native form reset, video play/pause, `<details>` toggle)
don't dispatch to the server unless explicitly wired. PR #1304 closed #1267
for `<dialog>` via `dj-dialog-close-event="..."`. This audit inventories
the remaining gaps.

## Inventory

Legend:
- **S→C**: Server can drive element state changes via attribute morphs
- **C→S**: Native user action that changes element state dispatches to server
- **Covered**: Bidirectional sync exists (S→C and C→S)
- **Gap**: S→C works but C→S is missing

### Elements with bidirectional coverage

| Element | dj attribute(s) | S→C | C→S | Status |
|---|---|---|---|---|
| `<dialog>` | `dj-dialog`, `dj-dialog-close-event` | morph toggles `open`/`close` → `showModal()`/`close()` | native `close` → `handleEvent(eventName)` | Covered (#1267, #1304) |
| `<input>`, `<textarea>`, `<select>` | `dj-model`, `dj-model.lazy`, `dj-model.debounce-N` | server renders `value` attribute | native `input`/`change` → `sendEvent('update_model', ...)` | Covered (model-binding.js) |
| `<input type="file">` (upload zone) | `dj-upload`, `dj-upload-drop` | config via `uploadConfigs` object | native `change`/`drop`/`dragover` → `handleFileSelect()` | Covered (uploads.js) |
| `<textarea>` (CursorOverlay) | `dj-hook="CursorOverlay"` | `handleEvent('cursor_positions', ...)` renders carets | native `keyup`/`click`/`select`/`scroll` → `pushEvent('update_cursor', ...)` | Covered (cursor-overlay.js) |

### Elements with one-way coverage only (gaps)

| Element | dj attribute(s) | S→C path | C→S gap | Native events | Priority |
|---|---|---|---|---|---|
| `<details>` | None — djust doesn't bind `<details>` | N/A | User clicks `<summary>` → `open` attribute toggles. No server dispatch. | `toggle` | P3 |
| `<form>` | `dj-form` (submit), `dj-form-submit-on` (enter) | `handleEvent` on submit | Native `reset` event from `<button type="reset">` or `form.reset()` doesn't dispatch. Form fields cleared client-side, server state unchanged. | `reset` | P2 |
| `<video>`, `<audio>` | None — djust doesn't bind media elements | N/A | Native controls trigger `play`, `pause`, `ended`, `volumechange`, `timeupdate`. No server dispatch. | `play`, `pause`, `ended`, `volumechange`, `timeupdate` | P3 |
| `<input type="file">` (standalone, non-upload-zone) | None | N/A | Drag-drop or paste onto a file input outside an upload zone. Files selected but server unaware until form submit. | `change` | P3 |
| Fullscreen API | None — djust doesn't bind fullscreen state | N/A | `document.fullscreenElement` changes via `requestFullscreen()` / `exitFullscreen()` / ESC. No server dispatch. | `fullscreenchange` | P4 |
| Picture-in-Picture API | None — djust doesn't bind PiP state | N/A | `document.pictureInPictureElement` changes via `requestPictureInPicture()` / `exitPictureInPicture()`. No server dispatch. | `enterpictureinpicture`, `leavepictureinpicture` | P4 |

### Click-dispatch elements (not bidirectional state)

These `dj-click` / `data-*-event` attributes dispatch a user action to the
server on click — they aren't "bidirectional state" because the element has
no *independent client-side state* the server needs to stay in sync with:

| Attribute | Used by | What it does |
|---|---|---|
| `dj-click="event_name"` | All interactive components | Sends `handleEvent('event_name')` on click |
| `data-event="..."` | Various components (`data_table`, `image_lightbox`, etc.) | Click dispatches to named handler |
| `data-close-event="..."` | `modal`, `sheet`, `command_palette` | Click dispatches close handler |
| `data-*-event="..."` | `data_table` (sort, search, filter, page, prev, next, select, group_toggle, reorder, edit, visibility, row_drag, copy, import, expression) | Click/shortcut dispatches to named handler |
| `data-stream-event="..."` | `stream`, `live_update` | Server-push event name (not a user→server dispatch) |
| `dj-form-submit-on="enter\|keyup"` | Forms | Triggers form submit on enter/keyup |
| `dj-scroll-to` | Scroll anchors | Scroll position (not bidirectional state) |
| `dj-lazy-slot` | Lazy hydration | Hydration trigger (not bidirectional state) |

## Recommended follow-ups

Each gap follows the opt-in attribute pattern canonized in #1307
(`docs/conventions/opt-in-extensions.md`):

1. **`<form reset>` reverse-sync** (P2). Add `dj-form-reset-event="event_name"`
   attribute. JS: `form.addEventListener('reset', () => handleEvent(eventName))`.
   Closes the most likely real-world gap — reset buttons are common in filter
   forms and data-entry UIs.

2. **`<details>` toggle reverse-sync** (P3). Add `dj-details-toggle-event="event_name"`
   attribute. JS: `details.addEventListener('toggle', () => handleEvent(eventName))`.
   Straightforward — one native event, one boolean state (`open`).

3. **`<video>`/`<audio>` play-state reverse-sync** (P3). Add
   `dj-media-play-event`, `dj-media-pause-event`, `dj-media-ended-event`
   attributes. Likely scoped to play/pause/ended (the three most useful);
   volumechange/timeupdate would overwhelm the server.

4. **Standalone file input reverse-sync** (P3). Extend existing
   `dj-upload` / `dj-upload-drop` patterns to handle standalone file inputs
   (non-upload-zone) with a `dj-file-change-event` attribute.

5. **Fullscreen / PiP** (P4). Deferred indefinitely — these APIs are rarely
   used in djust apps and the server doesn't typically need to stay in sync
   with visibility state.

## Related

- Audit C original: `docs/audits/decorator-contract-2026-05.md` § 4 Weakness #2
- #1307: opt-in extensions canon doc (`docs/conventions/opt-in-extensions.md`)
- #1267: `<dialog>` close-event gap (fixed in #1304)
