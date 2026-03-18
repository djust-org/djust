---
title: "Flash Messages"
slug: flash-messages
section: guides
order: 7
level: beginner
description: "Show transient success, error, and info notifications with put_flash -- Phoenix-style flash messages for LiveView"
---

# Flash Messages

djust provides Phoenix-style flash messages for showing transient notifications (success, error, info, warning) to users. Messages are set in Python, delivered over the WebSocket, and rendered client-side with auto-dismiss and removal animations.

## What You Get

- **`put_flash(level, message)`** -- Queue a flash message from any event handler
- **`clear_flash(level=None)`** -- Dismiss flash messages programmatically
- **`{% dj_flash %}`** -- Template tag that renders the flash container
- **Auto-dismiss** -- Messages disappear after a configurable timeout
- **Per-level CSS classes** -- Style each level (`info`, `success`, `warning`, `error`) independently
- **ARIA attributes** -- Accessible by default with `role="alert"` and `aria-live="polite"`

## Quick Start

### 1. Add the Flash Container to Your Template

```html
{% load djust_flash %}

<div dj-view="my-view" dj-root>
    {% dj_flash %}

    <button dj-click="save">Save</button>
</div>
```

### 2. Call `put_flash()` in Your View

```python
from djust import LiveView
from djust.decorators import event_handler


class ItemView(LiveView):
    template_name = "item.html"

    def mount(self, request, **kwargs):
        self.name = ""

    @event_handler()
    def save(self, **kwargs):
        if not self.name:
            self.put_flash("error", "Name is required.")
            return
        save_item(self.name)
        self.put_flash("success", "Item saved!")
```

That is the entire setup. No extra configuration, no middleware, no message framework.

## Flash Levels

Any string is accepted as a level -- it becomes a CSS class `dj-flash-{level}`. The four conventional levels are:

| Level | Use for | CSS class |
|-------|---------|-----------|
| `info` | Neutral information | `dj-flash-info` |
| `success` | Completed actions | `dj-flash-success` |
| `warning` | Non-blocking cautions | `dj-flash-warning` |
| `error` | Failed actions, validation errors | `dj-flash-error` |

```python
self.put_flash("info", "Your session expires in 5 minutes.")
self.put_flash("success", "Changes saved successfully.")
self.put_flash("warning", "This action cannot be undone.")
self.put_flash("error", "Failed to connect to the database.")
```

## Template Setup

### Basic Container

```html
{% load djust_flash %}
{% dj_flash %}
```

This renders an empty `<div id="dj-flash-container">` that the client JS populates when flash messages arrive. The container uses `dj-update="ignore"` internally so VDOM patches do not clobber active flash messages.

### Custom Auto-Dismiss Timeout

The default auto-dismiss is 5000ms (5 seconds). Override it per-container:

```html
<!-- Dismiss after 8 seconds -->
{% dj_flash auto_dismiss=8000 %}

<!-- Never auto-dismiss (user must close manually or call clear_flash) -->
{% dj_flash auto_dismiss=0 %}
```

### Position Hint

Add a position CSS class to the container for absolute/fixed positioning:

```html
{% dj_flash position="top-right" %}
```

This adds the class `dj-flash-top-right` to the container. You provide the CSS:

```css
.dj-flash-top-right {
    position: fixed;
    top: 1rem;
    right: 1rem;
    z-index: 1050;
}
```

## Clearing Flash Messages

### Clear All

```python
@event_handler()
def reset(self, **kwargs):
    self.clear_flash()  # removes all visible flash messages
```

### Clear by Level

```python
@event_handler()
def dismiss_errors(self, **kwargs):
    self.clear_flash("error")  # removes only error-level messages
```

## CSS Styling

Flash messages receive these CSS classes and attributes:

```html
<!-- Individual flash message -->
<div class="dj-flash dj-flash-success" role="alert" data-dj-flash-level="success">
    Item saved!
</div>

<!-- During removal animation -->
<div class="dj-flash dj-flash-success dj-flash-removing" ...>
    Item saved!
</div>
```

### Minimal Starter CSS

```css
.dj-flash-container {
    position: fixed;
    top: 1rem;
    right: 1rem;
    z-index: 1050;
    display: flex;
    flex-direction: column;
    gap: 0.5rem;
    max-width: 400px;
}

.dj-flash {
    padding: 0.75rem 1rem;
    border-radius: 0.375rem;
    color: #fff;
    font-size: 0.875rem;
    box-shadow: 0 4px 6px rgba(0, 0, 0, 0.1);
    transition: opacity 0.3s ease, transform 0.3s ease;
}

.dj-flash-info    { background-color: #3b82f6; }
.dj-flash-success { background-color: #22c55e; }
.dj-flash-warning { background-color: #f59e0b; color: #1a1a1a; }
.dj-flash-error   { background-color: #ef4444; }

.dj-flash-removing {
    opacity: 0;
    transform: translateX(100%);
}
```

### Bootstrap Integration

If you use Bootstrap, map flash levels to Bootstrap alert classes with a small CSS override:

```css
.dj-flash         { @extend .alert; margin-bottom: 0.5rem; }
.dj-flash-info    { @extend .alert-info; }
.dj-flash-success { @extend .alert-success; }
.dj-flash-warning { @extend .alert-warning; }
.dj-flash-error   { @extend .alert-danger; }
```

### Tailwind Integration

Use `@apply` in your CSS:

```css
.dj-flash         { @apply p-3 rounded-lg text-sm shadow-md; }
.dj-flash-info    { @apply bg-blue-500 text-white; }
.dj-flash-success { @apply bg-green-500 text-white; }
.dj-flash-warning { @apply bg-amber-500 text-gray-900; }
.dj-flash-error   { @apply bg-red-500 text-white; }
.dj-flash-removing { @apply opacity-0 translate-x-full; }
```

## Flash Survival Semantics

Flash messages interact with djust's navigation system:

| Navigation type | Flash behavior |
|----------------|----------------|
| **`live_patch()`** | Flash messages survive -- container is not cleared |
| **`live_redirect()`** | Flash messages are cleared -- new view mounts fresh |
| **Full page reload** | Flash messages are cleared -- client state resets |

This matches Phoenix LiveView's behavior. If you need a flash message to survive a redirect, set it on the destination view's `mount()`:

```python
class DestinationView(LiveView):
    def mount(self, request, **kwargs):
        if request.session.pop("_flash_success", None):
            self.put_flash("success", "Welcome!")
```

## Common Patterns

### Flash After Form Submission

```python
@event_handler()
def submit_form(self, **kwargs):
    form = MyForm(self._form_data)
    if form.is_valid():
        form.save()
        self.put_flash("success", "Form submitted successfully.")
        self.clear_form()
    else:
        self.put_flash("error", "Please fix the errors below.")
```

### Flash with Background Work

Flash messages work with `start_async()` and `@background`:

```python
@event_handler()
def generate_report(self, **kwargs):
    self.generating = True
    self.put_flash("info", "Generating report...")
    self.start_async(self._do_generate)

def _do_generate(self):
    try:
        self.report = build_report()
        self.put_flash("success", "Report ready!")
    except Exception as e:
        self.put_flash("error", f"Report failed: {e}")
    finally:
        self.generating = False
```

### Multiple Flash Messages

You can queue multiple flash messages in a single handler -- they are all delivered after the response:

```python
@event_handler()
def bulk_import(self, **kwargs):
    results = import_records(self._data)
    self.put_flash("success", f"{results.created} records created.")
    if results.skipped:
        self.put_flash("warning", f"{results.skipped} duplicates skipped.")
    if results.errors:
        self.put_flash("error", f"{results.errors} records failed.")
```

## JavaScript API

The flash system is exposed on `window.djust.flash` for advanced use cases:

```javascript
// Show a flash message from client-side code
window.djust.flash.show("info", "Client-side notification");

// Clear all flash messages
window.djust.flash.clear();

// Clear only error messages
window.djust.flash.clear("error");

// Dismiss a specific flash element
window.djust.flash.dismiss(element);
```

## How It Works Under the Hood

1. `put_flash()` appends a command to an internal `_pending_flash` queue on the view instance
2. After each WebSocket response (event, tick, server push), the consumer calls `_drain_flash()` to collect pending commands
3. Each command is sent as a separate `{"type": "flash", "action": "put", "level": "...", "message": "..."}` WebSocket message
4. The client JS (`23-flash.js`) receives the message and inserts a `<div>` into `#dj-flash-container`
5. After the auto-dismiss timeout, the element gets the `dj-flash-removing` class (for CSS transitions) and is removed from the DOM 300ms later

## See Also

- [Loading States & Background Work](loading-states.md) -- Show spinners during long operations
- [Navigation](navigation.md) -- `live_patch` and `live_redirect` behavior
- [Streaming](streaming.md) -- Real-time partial DOM updates
