# Loading States & Background Work

## Loading Directives

```html
<!-- Disable button during event -->
<button dj-click="save" dj-loading.disable>Save</button>

<!-- Show spinner during event -->
<div dj-loading.show dj-loading.for="save" style="display:none">Saving...</div>

<!-- Hide element during event -->
<span dj-loading.hide dj-loading.for="save">Ready</span>

<!-- Add CSS class during event -->
<div dj-loading.class="opacity-50" dj-loading.for="save">Content</div>

<!-- Show with specific display value -->
<div dj-loading.show="flex" dj-loading.for="save" style="display:none">...</div>
```

### Scoping Rules
- **Implicit**: `dj-loading.*` on an element with `dj-click="event"` scopes to that event
- **Explicit**: `dj-loading.for="event_name"` scopes to any named event (works on any element)

### CSS
- Trigger element gets `djust-loading` class during loading
- `<body>` gets `djust-global-loading`

## AsyncWorkMixin (Background Work)

```python
from djust import LiveView
from djust.mixins.async_work import AsyncWorkMixin
from djust.decorators import event_handler

class MyView(AsyncWorkMixin, LiveView):
    def mount(self, request, **kwargs):
        self.loading = False
        self.result = ""

    @event_handler()
    def generate(self, **kwargs):
        self.loading = True              # Sent to client immediately
        self.start_async(self._do_work)  # Runs after response sent

    def _do_work(self):
        """Runs in background thread. View re-renders when done."""
        try:
            self.result = slow_api_call()
        except Exception as e:
            self.error = str(e)
        self.loading = False
```

### How async_pending Works
1. Handler sets state + calls `start_async(callback)`
2. Server sends patches with `async_pending: true` — client keeps loading active
3. Background callback runs in thread
4. When done, server re-renders + sends final patches (no `async_pending`) — loading stops

### start_async Signature
```python
self.start_async(callback, *args, **kwargs)
```
- `callback`: Method on the view (receives `self` implicitly)
- `*args, **kwargs`: Forwarded to callback

### Error Handling
Always catch exceptions in the callback — uncaught exceptions are logged but leave the client in loading state:
```python
def _do_work(self):
    try:
        self.result = risky_call()
    except Exception as e:
        self.error = str(e)
    finally:
        self.loading = False
```

## Complete Example

```python
class ReportView(AsyncWorkMixin, LiveView):
    template_name = "report.html"

    def mount(self, request, **kwargs):
        self.generating = False
        self.report = ""

    @event_handler()
    def generate_report(self, **kwargs):
        self.generating = True
        self.start_async(self._generate)

    def _generate(self):
        self.report = call_api()
        self.generating = False
```

```html
<button dj-click="generate_report" dj-loading.disable>Generate</button>
<div dj-loading.show dj-loading.for="generate_report" style="display:none">
    Generating...
</div>
{% if report %}
<div>{{ report }}</div>
{% endif %}
```

## @background Decorator

Simpler syntax — entire handler runs in background:

```python
from djust.decorators import background

class ContentView(LiveView):
    @event_handler
    @background
    def generate_content(self, prompt: str = "", **kwargs):
        self.generating = True
        self.content = call_llm(prompt)
        self.generating = False
```

**vs start_async:**
- `@background`: entire handler runs in background
- `start_async()`: update state first, then start background work

Can combine with other decorators:
```python
@event_handler
@debounce(wait=0.5)
@background
def auto_save(self, **kwargs):
    self.save_draft()
```

Task name is function name; cancel via `self.cancel_async("generate_content")`.

## Configuration

```python
# settings.py
LIVEVIEW_CONFIG = {
    'loading_grouping_classes': ['d-flex', 'flex'],  # Container scoping
}
```

## Programmatic API (JS)
```javascript
window.djust.globalLoadingManager.startLoading('event_name');
window.djust.globalLoadingManager.stopLoading('event_name');
window.djust.globalLoadingManager.pendingEvents.has('event_name');
```
