# Runtime Layout Switching

Change the surrounding layout (nav, sidebar, footer, wrapper markup) during a LiveView session without a full page reload. Inner state — form values, scroll position, focused elements, third-party widget instances — is fully preserved.

Phoenix 1.1 added this; djust's equivalent is `self.set_layout(path)`.

## Quick start

```python
from djust import LiveView
from djust.decorators import event_handler

class EditorView(LiveView):
    template_name = "editor/page.html"

    def mount(self, request, **kwargs):
        self.fullscreen = False

    @event_handler
    def enter_fullscreen(self, **kwargs):
        self.fullscreen = True
        self.set_layout("layouts/fullscreen.html")

    @event_handler
    def exit_fullscreen(self, **kwargs):
        self.fullscreen = False
        self.set_layout("layouts/app.html")
```

Any template that renders through Django's template loader works — `set_layout` just queues the path. The layout is rendered with the view's `get_context_data()` so template variables resolve the same way they do for the initial page render.

## How it works

1. `self.set_layout(path)` stores the path on the view (overwrites any prior pending layout — last write wins).
2. After the event handler returns, the WebSocket consumer drains the pending path, renders the template with the view's current context, and emits a `{"type": "layout", "path": ..., "html": ...}` frame alongside the usual VDOM patches.
3. The client parses the new HTML, finds the `[data-djust-root]` (or `[dj-root]`) placeholder inside it, and **physically moves the live root element** from the current body into the new layout. The document `<body>` is then replaced with the reconstructed body.

Because the live `[dj-root]` element is the **same DOM node** before and after the swap, everything attached to it survives:

- `<input>` values currently being typed
- Scroll position inside scrollable containers
- Focused element (and cursor position)
- `dj-hook` instances and their internal state
- Third-party JS libraries (charts, maps, editors) that stored references to nodes inside the root

## Listening for the swap

A `djust:layout-changed` CustomEvent fires on `document` after every swap:

```js
document.addEventListener('djust:layout-changed', (e) => {
    console.log('layout is now', e.detail.path);
    // Re-initialize anything that depends on the new layout's outer markup,
    // e.g. a sidebar menu collapse handler.
});
```

## Known limitations

- **`<head>` is not merged.** If your new layout needs different stylesheets or scripts, add them to the original layout's `<head>` at mount time. The swap only replaces `<body>`.
- **Outer-layout event listeners** (e.g. a click handler on a `<nav>` that the initial layout owned) are **lost** — the old `<body>` is replaced wholesale. Put such listeners on the document / use event delegation.
- **`set_layout` is imperative.** No declarative `layout_template = "..."` class attribute — that's a separate future feature.
- **One layout per render cycle.** Calling `set_layout` twice in the same handler keeps only the last call (intentional — the client only applies one swap).

## Use cases

| Pattern | Mount with | On event |
|---|---|---|
| Admin ↔ public toggle | `layouts/public.html` | `self.set_layout("layouts/admin.html")` |
| Fullscreen editor | `layouts/app.html` | `self.set_layout("layouts/fullscreen.html")` |
| Onboarding → app | `layouts/onboarding.html` | `self.set_layout("layouts/app.html")` on completion |
| Focus mode for reading | `layouts/default.html` | `self.set_layout("layouts/reader.html")` |

## Errors

- **Template not found** — logged as a warning (`djust.websocket`), swap is skipped. The WebSocket stays alive; the view continues normally with the current layout.
- **Template rendering error** — same handling: logged with traceback, swap skipped.
- **No `[dj-root]` in the incoming HTML** — client refuses the swap and logs a console warning. This usually means the new layout template doesn't include a `<div dj-root></div>` placeholder — add one.

## See also

- [LiveView lifecycle](../core-concepts/liveview.md) — for how `get_context_data` is called
- [Navigation](navigation.md) — for routing between different LiveViews (as opposed to swapping layout within one)
