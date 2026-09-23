---
title: "Error Overlay (Dev Mode)"
slug: error-overlay
section: guides
order: 36
level: beginner
description: "In-browser error overlay for djust server exceptions — Python tracebacks and validation details rendered right in the page, Next.js-style"
---

# Error Overlay (Dev Mode)

When a LiveView event handler raises an exception, djust sends an `error` frame to the client. In development (`DEBUG=True`), that frame carries the Python traceback and any validation context. The error overlay displays all of that in an in-browser panel so you never have to switch back to your terminal to read the stack trace.

The overlay is modeled on the Next.js / Vite dev overlays: full-screen dim, close button, Escape-to-dismiss, and it replaces its contents on each new error rather than stacking.

## How it works

Three pieces cooperate:

1. **Server** — Event-handler exceptions are turned into an error frame by the runtime's safe-error path. In `DEBUG=True` the frame carries a prefixed message (`Error in View.handler(): ExcType: message`) and the full Python `traceback`. Other server errors go through `djust.websocket.send_error`, which in `DEBUG=True` adds `traceback` (last three frames only). In production, the traceback is omitted.
2. **Transport (`djust:error` CustomEvent)** — `03-websocket.js` dispatches this event on the `window` carrying `{error, traceback, event, validation_details}`; `03b-sse.js` dispatches `{error, traceback}`.
3. **Overlay (`36-error-overlay.js`)** — Listens for `djust:error`, renders a full-screen panel when `window.DEBUG_MODE === true`, no-ops otherwise.

`window.DEBUG_MODE` is written into the config `<script>` that djust injects into every LiveView page, based on Django's `DEBUG` setting, so production deployments automatically get zero overlay code paths.

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

- **Error** — `Error in OrderView.charge(): ValueError: amount must be positive`
- **Triggered by event** — `charge`
- **Traceback** — the full Python traceback (DEBUG only)
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
client over the WebSocket can carry extra fields beyond the generic
message:

| Field | Content |
|---|---|
| `traceback` | Event-handler exceptions: the full Python traceback. Errors sent through `send_error`: the last 3 frames. |
| `debug_detail` | `send_error` only: the unsanitized message, when a caller passes it. |
| `hint` | `send_error` only: an actionable suggestion, when a caller passes it. |

The overlay renders `error`, `event`, `traceback` and
`validation_details`. `hint` and `debug_detail` are rendered only
when you pass them to `djustErrorOverlay.show()` yourself: the
transports don't forward them to the `djust:error` event, and no
framework call site passes them to `send_error` at present.

Mount-time class lookup failures report
`Class <Name> not found in module <module>` in DEBUG (`View not found`
in production). A did-you-mean suggestion exists only for unknown
event handler names.

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
