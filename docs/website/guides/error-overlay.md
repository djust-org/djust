---
title: "Error Overlay (Dev Mode)"
slug: error-overlay
section: guides
order: 36
level: beginner
description: "In-browser error overlay for djust server exceptions — Python tracebacks, hints, and validation details rendered right in the page, Next.js-style"
---

# Error Overlay (Dev Mode)

When a LiveView event handler raises an exception, djust sends an `error` frame to the client. In development (`DEBUG=True`), that frame carries the Python traceback, an optional hint, and any validation context. The error overlay displays all of that in an in-browser panel so you never have to switch back to your terminal to read the stack trace.

The overlay is modeled on the Next.js / Vite dev overlays: full-screen dim, close button, Escape-to-dismiss, and it replaces its contents on each new error rather than stacking.

## How it works

Three pieces cooperate — the first two already ship in djust; the overlay is the new v0.5.1 UI:

1. **Server (`djust.websocket.send_error`)** — In `DEBUG=True`, includes `traceback` (last three frames), `debug_detail` (raw error before sanitization), and `hint` (actionable suggestion when available). In production, these fields are stripped.
2. **Transport (`djust:error` CustomEvent)** — `03-websocket.js` and `03b-sse.js` dispatch this event on the `window` carrying `{error, traceback, event, validation_details}`.
3. **Overlay (`36-error-overlay.js`)** — Listens for `djust:error`, renders a full-screen panel when `window.DEBUG_MODE === true`, no-ops otherwise.

`window.DEBUG_MODE` is set by the `djust_tags` template tag based on Django's `DEBUG` setting, so production deployments automatically get zero overlay code paths.

## What the overlay shows

Given a handler like:

```python
class OrderView(LiveView):
    @event_handler
    def charge(self, amount: int = 0, **kwargs):
        if amount <= 0:
            raise ValueError("amount must be positive")
        self._process_payment(amount)
```

Clicking a button that fires `charge` with `amount=0` pops an overlay containing:

- **Error** — `"amount must be positive"`
- **Triggered by event** — `charge`
- **Traceback** — the last three stack frames from the handler
- **Hint** (when provided by the server) — e.g. "Did you mean `amount >= 0`?"
- **Validation** (when the failure came from a form-validation path) — the per-field errors JSON dump

Close the overlay with the `×` button, Escape, or by clicking the backdrop.

## Dismissing and re-opening

The overlay is non-blocking — the app keeps working behind it. A second error replaces the current overlay rather than stacking a new one, so you always see the latest failure.

For devtools work you can trigger the overlay manually:

```javascript
window.djustErrorOverlay.show({
    error: 'KeyError: foo',
    event: 'save',
    traceback: 'File "views.py", line 42, in save\n    self.data["foo"]\n',
    hint: 'Initialize self.data in mount().',
});
```

And dismiss it with:

```javascript
window.djustErrorOverlay.dismiss();
```

## DEBUG-mode enriched WebSocket errors

When `settings.DEBUG=True`, server-side errors that flow back to the
client over the WebSocket carry three extra fields beyond the
generic message:

| Field | Content |
|---|---|
| `debug_detail` | Unsanitized exception message (e.g. `KeyError: 'foo'`). |
| `traceback` | Last 3 frames of the Python traceback. |
| `hint` | Actionable suggestion when djust recognizes the failure pattern (e.g. "Initialize `self.data` in `mount()`"). |

The overlay reads those fields and renders them. Mount-time class
lookup failures additionally include the list of available LiveView
classes in the response so a typo like `dj-view="MyVeiw"` surfaces
the closest match instead of an opaque 500.

In production (`DEBUG=False`) the framework drops `debug_detail` /
`traceback` / `hint` before serializing the error frame, so even if
the client somehow rendered them, there'd be nothing sensitive to
leak.

## Security

The overlay escapes every field before insertion — `error`, `traceback`, `hint`, and JSON-stringified `validation_details` all pass through HTML entity escaping, so a hostile traceback cannot inject script tags. Production builds (`DEBUG=False`) never render the overlay at all; Django also strips `traceback` / `debug_detail` / `hint` from the error frame in that mode.

If you want to confirm the overlay is off in production, load your app with `DEBUG=False`, raise an exception from a handler, and verify the console still logs the error but no `#djust-error-overlay` element is inserted.

## Related

- [Developer Tools](developer-tools.md) — other dev-mode helpers (djust_doctor, latency simulation, enriched error messages)
- [Testing](testing.md) — write tests that assert on error paths
