# djust Framework Improvements

## Implemented in this branch

### 1. `dj-hook` — Client-Side JS Hooks (Phoenix-style)

**What:** Register named JavaScript hooks that fire lifecycle callbacks when elements are mounted, updated, or destroyed. Like Phoenix LiveView's `phx-hook`.

**Usage:**
```html
<div dj-hook="Chart" data-values="{{ chart_data|json }}">
  <canvas></canvas>
</div>
```

```javascript
djust.hooks.Chart = {
  mounted() {
    // this.el = the DOM element
    // this.pushEvent(name, params) sends event to server
    this.chart = new Chart(this.el.querySelector('canvas'), {
      data: JSON.parse(this.el.dataset.values)
    });
  },
  updated() {
    this.chart.update(JSON.parse(this.el.dataset.values));
  },
  destroyed() {
    this.chart.destroy();
  }
};
```

**Lifecycle callbacks:**
- `mounted()` — element inserted into DOM
- `updated()` — element's attributes changed via VDOM patch
- `destroyed()` — element removed from DOM
- `disconnected()` — WebSocket lost
- `reconnected()` — WebSocket restored

**Properties available in hooks:**
- `this.el` — the DOM element
- `this.pushEvent(name, params)` — send event to server
- `this.handleEvent(name, callback)` — listen for server-pushed events

### 2. `dj-model` — Two-Way Data Binding

**What:** Automatic two-way binding between form inputs and server state. Combines `dj-input` debouncing with automatic `name`→`value` synchronization.

**Usage:**
```html
<input type="text" dj-model="search_query" placeholder="Search...">
<select dj-model="sort_by">
  <option value="name">Name</option>
  <option value="date">Date</option>
</select>
<textarea dj-model="message"></textarea>
```

On the server, the view receives `update_model` events automatically:
```python
class MyView(LiveView):
    search_query = ""
    sort_by = "name"

    @event_handler
    def update_model(self, field, value):
        setattr(self, field, value)
```

**Options:**
- `dj-model.lazy` — only sync on blur (not every keystroke)
- `dj-model.debounce-500` — custom debounce (ms)
- `dj-model.trim` — trim whitespace

### 3. Error Overlay (Development Mode)

**What:** A rich error overlay (like Next.js/Vite) that displays server errors with full tracebacks, event context, and suggested fixes. Replaces the console-only error logging.

**Features:**
- Full-screen overlay with syntax-highlighted Python traceback
- Shows which event triggered the error
- Validation error details with field-level info
- Dismiss with Escape or click
- Stack frames with file paths
- Copy traceback to clipboard button

---

## Proposed (Not Yet Implemented)

### Core Features
- `dj-submit` form handling improvements (nested forms, file inputs)
- Upload support (file uploads via WebSocket with progress)
- Dead view detection improvements (exponential backoff, offline indicator)
- Connection multiplexing (multiple views on one WebSocket)

### Performance
- Template fragment caching (only re-render changed sections)
- Lazy loading / virtual scrolling for large lists
- Batch multiple events into single round-trip
- VDOM diff streaming (send patches as they're computed)

### Developer Experience
- Component scaffolding CLI (`djust create component MyComponent`)
- TypeScript types for client-side JS API
- Storybook-like component preview mode
- Network inspector tab in debug panel

### Testing
- LiveView test client (mount + send events + assert state)
- Snapshot testing for VDOM diffs
- Performance benchmarking suite with CI integration
