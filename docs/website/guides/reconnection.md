---
title: "Reconnection Resilience"
slug: reconnection
section: guides
order: 15
level: intermediate
description: "How djust handles WebSocket disconnections, form recovery, backoff with jitter, and custom reconnection hooks."
---

# Reconnection Resilience

djust automatically reconnects when a WebSocket connection drops and restores form state so users never lose their work. This guide covers how the reconnection system works and how to customize it.

## How Reconnection Works

When the WebSocket connection drops (server restart, network hiccup, sleep/wake), djust:

1. Shows a reconnecting banner with attempt count
2. Retries with exponential backoff and jitter (prevents thundering herd)
3. On successful reconnect, remounts the LiveView
4. Automatically recovers form field values that differ from server defaults
5. Fires `dj-auto-recover` handlers for custom state restoration (WebSocket and SSE)

## Backoff with Jitter

djust uses the **AWS full-jitter** strategy for reconnection delays:

- **Min delay**: 500ms
- **Max delay**: 30s (capped)
- **Max attempts**: 10
- Each attempt uses a random delay between `500ms` and `min(base * 2^attempt, 30000ms)`

This prevents hundreds of clients from reconnecting simultaneously after a server restart (thundering herd problem).

## Reconnection UI

During reconnection, djust provides several UI hooks:

### CSS Classes on `<body>`

| Class | Applied when |
|---|---|
| `dj-connected` | WebSocket connection is open |
| `dj-disconnected` | WebSocket connection is lost |

### Reconnection Banner

A fixed banner appears at the top of the page showing the current attempt number (e.g., "Reconnecting... (attempt 2 of 10)"). The banner is automatically removed on successful reconnect.

Style with CSS. The default amber colours are set as an inline style, so your
rule needs `!important` to win:

```css
.dj-reconnecting-banner {
    /* Override default amber banner (inline style, hence !important) */
    background: #dc2626 !important;
    color: white !important;
}
```

### Data Attributes and CSS Custom Properties

During reconnection, `<body>` receives:

| Attribute / Property | Value |
|---|---|
| `data-dj-reconnect-attempt` | Current attempt number (e.g., `"3"`) |
| `--dj-reconnect-attempt` | CSS custom property with attempt number |

Use the CSS custom property for progressive styling:

```css
/* Increase urgency as attempts increase */
body[data-dj-reconnect-attempt] .offline-indicator {
    opacity: calc(0.3 + var(--dj-reconnect-attempt) * 0.07);
}
```

All reconnection UI state (banner, attributes, properties) is cleared on successful reconnect or intentional disconnect.

## Form Recovery

After a successful reconnect, djust automatically scans all form fields inside the `[dj-view]` container that have `dj-change` or `dj-input` attributes. For each field:

1. Compare the current DOM value against the server-rendered default
2. If they differ, fire a synthetic change event to the server
3. The server handler updates its state, keeping client and server in sync

This means a user can be typing in a form, briefly lose connection, reconnect, and continue without losing any input.

### How Defaults Are Determined

| Field type | DOM value | Server default |
|---|---|---|
| Text / textarea / number / email | `field.value` | `value` attribute (or `defaultValue` for textarea) |
| Checkbox / radio | `field.checked` | Presence of `checked` attribute |
| Select | `field.value` | `option[selected]` value, or first option |

### Fields that are not recovered

- **Password fields** (`type="password"`, or any field with
  `autocomplete="new-password"` / `"current-password"`): their value is not held
  across the reconnect, not written back into the new markup and not sent to the
  server, so a user re-types it. Drafts and `dj-auto-recover` apply the same rule
  (one shared check).
- **Disabled fields** and `type="file"` fields.
- Fields marked `dj-no-recover` or `dj-force-value`.

Hidden fields keep being recovered when they carry `dj-change` / `dj-input`; drafts
and the `dj-auto-recover` envelope keep saving and sending hidden and disabled
fields, whose values are server-rendered (a hidden field often carries the record
id).

### Opting Out with `dj-no-recover`

Add `dj-no-recover` to any field that should **not** be automatically recovered:

```html
<!-- This field will NOT be restored on reconnect -->
<input type="text" name="scratch" dj-change="on_change" dj-no-recover />

<!-- These fields WILL be restored normally -->
<input type="text" name="title" dj-change="save_title" />
<input type="email" name="email" dj-change="save_email" />
```

Use `dj-no-recover` for:

- Temporary/scratch fields that should reset on reconnect
- Fields where server state is the source of truth
- Search fields where stale queries should not replay

### Interaction with `dj-auto-recover`

Fields inside a `dj-auto-recover` container are **skipped** by automatic form recovery. The custom handler takes precedence:

```html
<!-- Automatic recovery handles these fields -->
<input name="title" dj-change="save" />
<input name="email" dj-change="save" />

<!-- Custom recovery handler owns this section -->
<div dj-auto-recover="restore_editor_state" data-editor-id="main">
    <!-- Fields here are NOT auto-recovered -->
    <textarea name="content" dj-change="update_content"></textarea>
    <input name="cursor_pos" type="hidden" dj-change="update_cursor" />
</div>
```

### Custom Recovery with `dj-auto-recover`

For views with complex state that cannot be inferred from form values alone (canvas state, editor cursors, drag positions), use `dj-auto-recover`:

```html
<div dj-auto-recover="restore_state" data-canvas-id="main">
    <input name="brush_size" value="5" />
    <input name="color" value="#ff0000" />
</div>
```

On reconnect, djust fires the `restore_state` handler with two dictionaries.
The handler declares the ones it uses as parameters, under the strict
parameter policy:

- `form_values: dict[str, Any]`: the container's form field values, keyed by
  `name`. A group of same-named checkboxes and a `<select multiple>` are a list
  of the checked or selected values, a radio group is the checked radio's value,
  a lone checkbox is a boolean, and every other control is its text. Password
  fields are left out; hidden and disabled fields are included
- `data_attrs: dict[str, str]`: the container element's `data-*` attributes,
  keyed without the `data-` prefix (`data-canvas-id` becomes `canvas-id`)

```python
from typing import Any

@event_handler(parameter_policy="strict")
def restore_state(self, form_values: dict[str, Any], data_attrs: dict[str, str]):
    self.canvas_id = data_attrs.get("canvas-id", "")
    self.brush_size = int(form_values.get("brush_size", 5))
    self.color = form_values.get("color", "#ff0000")
```

The browser sends only the dictionaries the handler declares (a handler that
needs only the form declares only `form_values`; a `**values: Any` catch-all
receives both), and adds the container's `dj-value-*` attributes as ordinary
arguments. A `dj-value-*` attribute named `form-values` or `data-attrs` is
refused, because it would collide with a generated value.

The values come from the browser, so they are validated like every event
argument. `dict[str, T]` accepts a JSON object with string keys and checks each
value as `T`; the event is refused, before the handler runs, if a value has the
wrong type (a number in `data_attrs`, say) or the arguments pass the event
limits: 1,024 entries in one dictionary or list, 10,000 values in all,
65,536 characters of text in all (keys included), or 32 levels of nesting. The browser applies the same limits
before it sends, so a container whose fields hold more text than that is not
recovered. Use `dict[str, Any]` for `form_values`, since one form mixes text,
booleans and lists; validate the values you read as you would any other input.

Recovery runs after a WebSocket reconnect and after the SSE stream reconnects;
both keep what the user had typed through the reconnect's mount. The HTTP
fallback has no reconnect, so it never fires recovery.

#### Upgrading a 1.x recovery handler

A 1.x recovery handler read `_form_values` / `_data_attrs`, names a strict
signature cannot declare. `manage.py check` reports one that still does
(`djust.V031`) and names the new signature; the before-and-after is in
[Migrating to djust 2.0](migrating-to-2.0.md#2-make-event-handlers-strict).

## Slow Clients and Outbound Limits

A client that reads more slowly than the server produces frames (a phone on a
bad network, a busy tab, a flood of broadcasts) does not make the server buffer
without bound. Each connection has an outbound limit,
`LIVEVIEW_CONFIG["max_outbound_buffer_bytes"]` (default `4194304`, 4 MiB; `0`
turns it off). Past it the server ends the connection and the client reconnects
through the recovery path above: a fresh mount, then form recovery and
`dj-auto-recover`. Single frames are never dropped or merged, because the
client would then show a state the server never had.

```python
# settings.py
LIVEVIEW_CONFIG = {
    "max_outbound_buffer_bytes": 8 * 1024 * 1024,  # 8 MiB per connection
}
```

| Transport | What is bounded | Over the limit |
|-----------|-----------------|----------------|
| WebSocket | Frames waiting while another frame of the same socket is still being handed to the ASGI server, in bytes (UTF-8 for text frames), not counting the frame that goes next | The socket is closed with code **1013** ("try again later"); the client reconnects with the usual backoff and remounts |
| SSE | Frames waiting in the session's queue for the stream to write them, in bytes (UTF-8 JSON), not counting the frame the stream takes next | The waiting frames are dropped and the stream ends; `EventSource` reconnects with the same session id and that mount resyncs the page |
| HTTP page POST | Nothing to bound: each response carries the frames of its own event and nothing is held between requests | n/a |
| Streamed HTTP render (`StreamingHttpResponse` of a page) | At most `DJUST_LAZY_CHUNK_QUEUE_MAX` rendered chunks (default 8); a full queue makes the renderer wait for the client (back-pressure) | The renderer waits; nothing is dropped |

What the WebSocket limit covers, exactly:

- **One frame always goes out.** The frame next in line is not counted, so a
  frame larger than the limit is never the reason for a close; the limit bounds
  the backlog behind it, not how large one render may be.
- **Order is kept.** One task per socket sends its frames, in the order they
  were sent. A handler whose frame finds the socket idle waits until that frame
  is sent (and sees a transport error, as before); one that finds a backlog
  hands its frame off and carries on. A cancelled handler does not hold up the
  frames behind its own.
- **The limit is per connection**, so the memory one browser can pin is the
  limit times its open sockets.
- **It measures what the ASGI server has not accepted yet.** uvicorn's
  `websockets` implementations apply back-pressure, so a slow reader shows up
  here. A server that accepts every frame into its own buffer without
  back-pressure (Daphne) holds that backlog itself; bound it there.
- **A close waits for the frames before it.** A `close()` sends the frames
  queued before it first, for up to 5 seconds, then closes anyway and drops
  them. A disconnect drops waiting frames at once.

Over SSE the same limit applies to the session's queue. It sits beside the
frame-count back-pressure for server-driven frames (#3809): at 128 queued
frames a tick is skipped and a `self.server_push()` is deferred and coalesced,
while frames answering an event are never held back. The byte limit is the
backstop for everything else, events included.

The server logs a warning naming the backlog and the limit each time it closes
a socket with 1013 or ends an SSE stream.

**What is coalesced and what needs a resync.** No transport merges or drops
single frames: a frame accepted for a connection reaches it in order, or the
connection ends. The one coalescing step is upstream of the wire, on SSE:
deferred `server_push()` calls (above). Anything a client misses because its
connection ended is recovered by the reconnect's fresh mount, never by
replaying frames.

## SSE Transport

Over SSE (Server-Sent Events), the browser's `EventSource` handles reconnection itself. There is no backoff with jitter, no reconnection banner and no `data-dj-reconnect-attempt` attribute; only the `dj-connected` / `dj-disconnected` body classes and the post-remount form recovery apply.

## Example: Full Reconnection-Resilient Form

```html
{% load live_tags %}
<html>
<head>{% djust_client_config %}</head>
<body>
  <div dj-root>
    <form dj-submit="save_form">
      {% csrf_token %}

      <!-- Auto-recovered on reconnect -->
      <input name="title" dj-change="validate_title" value="{{ title }}" />
      <textarea name="body" dj-input="preview" >{{ body }}</textarea>

      <!-- Not recovered (ephemeral search) -->
      <input name="search" dj-input="filter_tags" dj-no-recover />

      <!-- Custom recovery for rich editor -->
      <div dj-auto-recover="restore_editor" data-doc-id="{{ doc.id }}">
          <div id="rich-editor" dj-update="ignore"></div>
          <input name="cursor" type="hidden" dj-change="sync_cursor" />
      </div>

      <button type="submit" dj-disable-with="Saving...">Save</button>
    </form>
  </div>
</body>
</html>
```

```python
from typing import Any

from djust import LiveView, state
from djust.decorators import event_handler

class EditorView(LiveView):
    template_name = "editor.html"
    title = state("")
    body = state("")

    @event_handler()
    def validate_title(self, value: str):
        self.title = value

    @event_handler()
    def preview(self, value: str):
        self.body = value

    @event_handler(parameter_policy="strict")
    def restore_editor(self, form_values: dict[str, Any], data_attrs: dict[str, str]):
        # Custom recovery: restore editor state from DOM values
        self.doc_id = data_attrs.get("doc-id", "")
        self.cursor_pos = form_values.get("cursor", "")
