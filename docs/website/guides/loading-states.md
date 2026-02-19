---
title: "Loading States & Background Work"
slug: loading-states
section: guides
order: 5
level: intermediate
description: "Show spinners, disable buttons, and run slow work in the background with immediate UI feedback"
---

# Loading States & Background Work

djust provides two complementary systems for responsive UIs during slow operations:

1. **Loading directives** (`dj-loading.*`) -- Client-side attributes that show/hide/disable elements while an event is in flight
2. **AsyncWorkMixin** (`start_async()`) -- Server-side mixin that flushes UI state immediately, then runs slow work in a background thread

Together they let you show a spinner instantly, run a 10-second API call, and update the UI when it finishes -- all without writing any JavaScript.

## Loading Directives

### Basic Usage

Add `dj-loading.*` attributes to any element that has a `dj-click`, `dj-submit`, or other event attribute. The loading state activates when the event fires and deactivates when the server responds.

```html
<button dj-click="save" dj-loading.disable>
    Save
</button>
```

### Available Modifiers

| Attribute | Effect | Example |
|-----------|--------|---------|
| `dj-loading.disable` | Sets `disabled=true` during loading | `<button dj-click="save" dj-loading.disable>` |
| `dj-loading.show` | Shows the element during loading (hidden otherwise) | `<div dj-loading.show style="display:none">Saving...</div>` |
| `dj-loading.show="flex"` | Shows with specific display value | `<div dj-loading.show="flex" style="display:none">` |
| `dj-loading.hide` | Hides the element during loading | `<span dj-loading.hide>Ready</span>` |
| `dj-loading.class="name"` | Adds a CSS class during loading | `<div dj-loading.class="opacity-50">` |

### Scoping with `dj-loading.for`

By default, loading modifiers are scoped to the event on the same element. Use `dj-loading.for` to tie any element's loading state to a specific event name, regardless of where that element is in the DOM.

```html
<!-- Button triggers the event -->
<button dj-click="generate_report">Generate</button>

<!-- Spinner anywhere in the page, tied to the same event -->
<div dj-loading.show dj-loading.for="generate_report" style="display:none">
    <span class="spinner"></span> Generating report...
</div>

<!-- Disable another button while report generates -->
<button dj-click="export" dj-loading.disable dj-loading.for="generate_report">
    Export
</button>
```

This is useful when:
- The loading indicator is far from the trigger button in the DOM
- Multiple elements should react to the same event
- You want to disable unrelated buttons during a long operation

### CSS Classes

Every trigger element automatically gets the `djust-loading` class during loading. The `<body>` also gets `djust-global-loading`. Use these for custom CSS:

```css
.djust-loading {
    cursor: wait;
    opacity: 0.7;
}

.djust-global-loading .sidebar {
    pointer-events: none;
}
```

### Configuring Grouping Classes

Loading state scoping uses container CSS classes to group related elements. Configure which classes act as grouping containers:

```python
# settings.py
LIVEVIEW_CONFIG = {
    'loading_grouping_classes': [
        'd-flex',           # Bootstrap
        'flex',             # Tailwind
        'my-custom-group',  # Your own
    ],
}
```

## Background Work with `start_async()`

### The Problem

When an event handler does slow work (API calls, AI generation, file processing), the user sees nothing until the server responds -- which could be 5-30 seconds.

### The Solution

`AsyncWorkMixin` lets you split the work into two phases:

1. **Immediate response** -- Update state (e.g., `self.generating = True`), flush to client
2. **Background work** -- Run the slow operation in a thread; when done, re-render automatically

```python
from djust import LiveView
from djust.mixins.async_work import AsyncWorkMixin
from djust.decorators import event_handler


class ReportView(AsyncWorkMixin, LiveView):
    template_name = "report.html"

    def mount(self, request, **kwargs):
        self.generating = False
        self.report_html = ""
        self.error = ""

    @event_handler()
    def generate_report(self, **kwargs):
        self.generating = True        # Shows spinner immediately
        self.error = ""
        self.start_async(self._do_generate)

    def _do_generate(self):
        """Runs in a background thread after the client sees the spinner."""
        try:
            self.report_html = call_slow_api()  # 10 seconds
        except Exception as e:
            self.error = str(e)
        self.generating = False
        # View automatically re-renders when this returns
```

### Template

```html
<button dj-click="generate_report"
        dj-loading.disable
        dj-loading.for="generate_report">
    Generate Report
</button>

<!-- Spinner: visible during both the initial response AND background work -->
<div dj-loading.show dj-loading.for="generate_report"
     style="display:none">
    <span class="spinner-border spinner-border-sm"></span>
    Generating...
</div>

{% if report_html %}
<div class="report">
    {{ report_html|safe }}
</div>
{% endif %}

{% if error %}
<div class="alert alert-danger">{{ error }}</div>
{% endif %}
```

### How It Works Under the Hood

1. User clicks "Generate Report"
2. `generate_report()` sets `self.generating = True` and calls `self.start_async(self._do_generate)`
3. The WebSocket consumer sends VDOM patches immediately (spinner appears)
4. The response includes `async_pending: true`, telling the client to **keep loading state active**
5. The consumer spawns `_do_generate()` in an asyncio background task
6. When `_do_generate()` returns, the view re-renders and sends updated patches
7. This final response does NOT have `async_pending`, so loading state stops (spinner disappears)

### Passing Arguments

`start_async()` forwards positional and keyword arguments to the callback:

```python
@event_handler()
def start_export(self, **kwargs):
    fmt = kwargs.get("format", "csv")
    self.exporting = True
    self.start_async(self._run_export, format=fmt)

def _run_export(self, format="csv"):
    self.data = expensive_export(format)
    self.exporting = False
```

### Error Handling

If the background callback raises an exception:
- The exception is logged (not sent to the client)
- The loading state on the client will remain active indefinitely

To handle errors gracefully, catch exceptions in your callback and set error state:

```python
def _do_work(self):
    try:
        self.result = risky_operation()
    except Exception as e:
        self.error = f"Operation failed: {e}"
    finally:
        self.loading = False  # Always clear loading state
```

### The `@background` Decorator

For simpler syntax, use the `@background` decorator to automatically run the entire handler in the background:

```python
from djust import LiveView
from djust.decorators import event_handler, background


class ContentView(LiveView):
    template_name = "content.html"

    def mount(self, request, **kwargs):
        self.generating = False
        self.content = ""
        self.error = ""

    @event_handler
    @background
    def generate_content(self, prompt: str = "", **kwargs):
        """Entire method runs in background thread."""
        self.generating = True
        try:
            self.content = call_llm(prompt)  # Slow operation
        except Exception as e:
            self.error = str(e)
        finally:
            self.generating = False
```

The `@background` decorator:
- Automatically wraps the handler to call `start_async()` internally
- Uses the function name as the task name (for cancellation/tracking)
- Can be combined with other decorators like `@debounce`:

```python
@event_handler
@debounce(wait=0.5)
@background
def auto_save(self, **kwargs):
    # Debounced and runs in background
    self.save_draft()
```

**When to use `@background` vs `start_async()`:**

- Use `@background` when the **entire handler** should run in the background
- Use `start_async()` when you need to update state **before** starting background work, or when you need multiple concurrent async tasks with different names

### Task Naming and Cancellation

Both `start_async()` and `@background` support named tasks for tracking and cancellation:

```python
@event_handler
def start_export(self, **kwargs):
    self.exporting = True
    self.start_async(self._run_export, format="csv", name="export")

def _run_export(self, format="csv"):
    self.data = expensive_export(format)
    self.exporting = False

@event_handler
def cancel_export(self, **kwargs):
    self.cancel_async("export")  # Cancel the named task
    self.exporting = False
    self.status = "Cancelled"
```

With `@background`, the task name is automatically set to the handler's function name:

```python
@event_handler
@background
def generate_report(self, **kwargs):
    # Task name is "generate_report"
    ...

@event_handler
def cancel_report(self, **kwargs):
    self.cancel_async("generate_report")
```

### Handling Completion or Errors

Implement `handle_async_result()` to receive notifications when async tasks complete or fail:

```python
def handle_async_result(self, name: str, result=None, error=None):
    """Called when any async task completes."""
    if error:
        self.error_message = f"Task {name} failed: {error}"
        self.loading = False
    elif name == "export":
        self.status = "Export complete"
```

This method is optional -- if not implemented, errors are logged and the view re-renders normally when the task completes.

## Combining Both Systems

The loading directives and `start_async()` are designed to work together. The key is the `async_pending` flag:

| Phase | `dj-loading.*` active? | Why |
|-------|----------------------|-----|
| Event sent to server | Yes | Client starts loading on event fire |
| Server responds with patches + `async_pending: true` | Yes | Client keeps loading active |
| Background work completes, server sends final patches | No | No `async_pending` flag, client stops loading |

Without `start_async()`, loading states end as soon as the server responds. With `start_async()`, they persist through the entire background operation.

## Common Patterns

### Disable Multiple Buttons

```html
<button dj-click="save" dj-loading.disable>Save</button>
<button dj-click="cancel" dj-loading.disable dj-loading.for="save">Cancel</button>
```

### Swap Button Text

```html
<button dj-click="deploy">
    <span dj-loading.hide dj-loading.for="deploy">Deploy</span>
    <span dj-loading.show dj-loading.for="deploy" style="display:none">
        Deploying...
    </span>
</button>
```

### Full-Page Overlay

```html
<div dj-loading.show dj-loading.for="generate"
     style="display:none; position:fixed; inset:0; background:rgba(0,0,0,0.3); z-index:999">
    <div style="position:absolute; top:50%; left:50%; transform:translate(-50%,-50%)">
        Loading...
    </div>
</div>
```

### Progress with Streaming

For operations where you want incremental progress (not just a spinner), combine `start_async()` with [StreamingMixin](streaming.md):

```python
class ImportView(AsyncWorkMixin, StreamingMixin, LiveView):
    @event_handler()
    def start_import(self, **kwargs):
        self.importing = True
        self.start_async(self._do_import)

    def _do_import(self):
        for i, row in enumerate(large_dataset):
            process(row)
            if i % 100 == 0:
                self.stream_text("progress", f"{i} rows processed...")
        self.importing = False
```

## Programmatic API

The loading manager is exposed globally for advanced use cases:

```javascript
// Start/stop loading manually
window.djust.globalLoadingManager.startLoading('my_event');
window.djust.globalLoadingManager.stopLoading('my_event');

// Check if an event is currently loading
window.djust.globalLoadingManager.pendingEvents.has('my_event');
```
