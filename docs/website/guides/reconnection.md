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

On reconnect, djust fires the `restore_state` handler with two dict arguments
(they are not flattened into separate kwargs):
- `_form_values`: the container's form field values, keyed by `name`
- `_data_attrs`: the container element's `data-*` attributes, keyed without the
  `data-` prefix (`dj-value-*` attributes are **not** collected)

```python
@event_handler()
def restore_state(self, _form_values=None, _data_attrs=None, **kwargs):
    fv = _form_values or {}
    da = _data_attrs or {}
    self.canvas_id = da.get("canvas-id", "")
    self.brush_size = int(fv.get("brush_size", 5))
    self.color = fv.get("color", "#ff0000")
```

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
    def restore_editor(self, _form_values=None, _data_attrs=None, **kwargs):
        # Custom recovery: restore editor state from DOM values
        self.doc_id = (_data_attrs or {}).get("doc-id", "")
        self.cursor_pos = (_form_values or {}).get("cursor", "")
