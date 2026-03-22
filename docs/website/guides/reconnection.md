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
5. Fires `dj-auto-recover` handlers for custom state restoration

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

Style with CSS:

```css
.dj-reconnecting-banner {
    /* Override default amber banner */
    background: #dc2626;
    color: white;
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
<div dj-auto-recover="restore_editor_state" dj-value-editor-id="main">
    <!-- Fields here are NOT auto-recovered -->
    <textarea name="content" dj-change="update_content"></textarea>
    <input name="cursor_pos" type="hidden" dj-change="update_cursor" />
</div>
```

### Custom Recovery with `dj-auto-recover`

For views with complex state that cannot be inferred from form values alone (canvas state, editor cursors, drag positions), use `dj-auto-recover`:

```html
<div dj-auto-recover="restore_state" dj-value-canvas-id="main">
    <input name="brush_size" value="5" />
    <input name="color" value="#ff0000" />
</div>
```

On reconnect, djust fires the `restore_state` handler with:
- All form field values from the container (serialized)
- All `data-*` attributes from the container element

```python
@event_handler()
def restore_state(self, canvas_id="", brush_size="5", color="#ff0000", **kwargs):
    self.canvas_id = canvas_id
    self.brush_size = int(brush_size)
    self.color = color
```

## SSE Transport

Form recovery and backoff with jitter work identically over the SSE (Server-Sent Events) transport. The reconnection UI, banner, and data attributes behave the same way regardless of transport.

## Example: Full Reconnection-Resilient Form

```html
{% load djust_tags %}
<html>
<head>{% djust_scripts %}</head>
<body dj-view="{{ dj_view_id }}">
  <div dj-root>
    <form dj-submit="save_form">
      {% csrf_token %}

      <!-- Auto-recovered on reconnect -->
      <input name="title" dj-change="validate_title" value="{{ title }}" />
      <textarea name="body" dj-input="preview" >{{ body }}</textarea>

      <!-- Not recovered (ephemeral search) -->
      <input name="search" dj-input="filter_tags" dj-no-recover />

      <!-- Custom recovery for rich editor -->
      <div dj-auto-recover="restore_editor" dj-value-doc-id="{{ doc.id }}">
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
from djust import LiveView
from djust.decorators import event_handler

class EditorView(LiveView):
    template_name = "editor.html"

    def mount(self, request, **kwargs):
        self.title = ""
        self.body = ""

    @event_handler()
    def validate_title(self, value="", **kwargs):
        self.title = value

    @event_handler()
    def preview(self, value="", **kwargs):
        self.body = value

    @event_handler()
    def restore_editor(self, doc_id="", cursor="", **kwargs):
        # Custom recovery: restore editor state from DOM values
        self.doc_id = doc_id
        self.cursor_pos = cursor
