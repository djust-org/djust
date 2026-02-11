---
title: "Debug Panel"
slug: debug-panel
section: advanced
order: 3
level: advanced
description: "Built-in developer debug panel for real-time event inspection, VDOM patch monitoring, and state debugging."
---

# Debug Panel

The djust Developer Debug Panel provides real-time introspection into your LiveView application. Monitor events, inspect VDOM patches, view state variables, and debug WebSocket messages -- all from a panel docked to the bottom of your page.

## Enabling the Debug Panel

The panel is automatically enabled when Django's `DEBUG` mode is active:

```python
# settings.py
DEBUG = True   # Debug panel appears
DEBUG = False  # Debug panel hidden (zero overhead in production)
```

The panel only appears when `DEBUG = True`. Never deploy to production with DEBUG enabled, as it exposes internal application structure, variable values, and event logs.

## Opening the Panel

- **Keyboard shortcut**: `Ctrl+Shift+D` (Windows/Linux) or `Cmd+Shift+D` (Mac)
- **Floating button**: Click the bug icon in the bottom-right corner

The panel docks to the bottom of the viewport at 350px height, with a vertical tab sidebar on the left.

## Event Handlers Tab

Lists all event handlers registered in the current LiveView, including:

- **Handler name** (e.g., `search`, `filter_by_status`)
- **Description** from docstrings or `@event_handler(description=...)`
- **Parameters** with name, type, required/optional, and defaults
- **Decorators** applied (e.g., `@debounce`, `@optimistic`)

```
search
  Search properties with debouncing

  Parameters:
    value: str = ""
    **kwargs

  Decorators: @debounce
```

Use this tab to discover available events, verify parameter names for templates, and confirm decorators are applied.

## Event History Tab

Real-time log of every event triggered during your session:

- **Event name** and **timestamp**
- **Duration** in milliseconds
- **Parameters** as expandable JSON
- **Status** -- green for success, red for errors

```
search | 45.2ms | 4:32:15 PM   [Click to expand]

  Parameters:
  {
    "value": "luxury apartment",
    "_skipDecorators": false
  }
```

### Key Features

- **Circular buffer**: Retains the last 50 events (configurable)
- **Filter by name**: Substring search to find specific events
- **Filter by status**: Show all, errors only, or successes only
- **Copy JSON**: Copy event data for use in unit tests
- **Export all**: Download entire event history as JSON
- **Replay**: Re-trigger any event with its original parameters; inline status feedback (pending/success/error) auto-clears after 2 seconds

## VDOM Patches Tab

Visualizes Virtual DOM patch operations for debugging rendering and performance:

- **Patch count** and **timestamp** per update
- **Application time** (sub-millisecond precision)
- **Patch operations**: SetAttr, SetText, Replace, InsertChild, RemoveChild, MoveChild

```
3 patches | 0.85ms | 4:32:15 PM   [Click to expand]

  [
    {"type": "SetText", "path": [0,1,2], "text": "5 properties"},
    {"type": "SetAttr", "path": [0,3,0], "key": "value", "value": "luxury apartment"}
  ]

  Applied in 0.85ms
```

### Performance Targets

| Update type   | Patches | Time    |
|---------------|---------|---------|
| Form input    | 1-2     | < 1 ms  |
| Typical update| 1-5     | < 2 ms  |
| List update   | 5-20    | < 5 ms  |
| Full refresh  | 50+     | Optimize if > 10 ms |

## Variables Tab

Shows current public instance variables and their values:

```
properties
  QuerySet
  <QuerySet [<Property: Luxury Apartment>, <Property: Beach House>]>

search_query
  str
  "luxury apartment"
```

Only public variables (not starting with `_`) are shown, following the JIT Serialization convention.

## Network Tab

Inspect WebSocket messages in real time:

- **Directional color coding**: Amber for sent messages, cyan for received
- **Expandable payloads**: Click any message to see full pretty-printed JSON
- **Copy to clipboard**: Copy raw JSON with visual feedback
- **Connection stats**: Messages sent/received, bytes transferred, reconnections, uptime

## Live Debug Updates

When `DEBUG=True`, every WebSocket event response includes a `_debug` payload with:

- Updated **variables** (view state after the event)
- **Handlers** metadata
- **Patches** applied
- **Performance** metrics (render time, diff time)
- **Event name** that triggered the response

This keeps all debug tabs live after each interaction, not just from initial page load data.

## Common Workflows

### Debugging Parameter Mismatches

1. Trigger an event from the UI
2. Open **Event History** tab and expand the event
3. Compare sent parameters with expected parameters in **Event Handlers** tab

```
Event History shows:
  search { "query": "test" }        # Wrong parameter name

Event Handlers shows:
  search(value: str = "")           # Expects "value"
```

### Performance Optimization

1. Open **VDOM Patches** tab
2. Trigger an update
3. Check patch count and duration
4. If > 20 patches or > 5 ms, refactor the handler:

```python
# Before: 52 patches, 8.2ms
def filter_items(self, value: str = ""):
    self.filter_value = value
    self.items = self._get_filtered()
    self.count = len(self.items)
    self.message = f"{self.count} items"

# After: 3 patches, 1.1ms
def filter_items(self, value: str = ""):
    self.filter_value = value
    self._refresh_items()

def get_context_data(self, **kwargs):
    self.items = self._items
    context = super().get_context_data(**kwargs)
    context['count'] = len(self.items)
    return context
```

### Creating Test Cases from Events

1. Interact with the app
2. Open **Event History** tab
3. Click **Copy JSON** on the event you want to test
4. Paste into your test file:

```python
def test_save_property(self):
    view = PropertyListView()
    view.mount(self.request)
    view.save_property(
        name="Luxury Apartment",
        address="123 Main St",
        price=500000.0,
    )
    assert Property.objects.filter(name="Luxury Apartment").exists()
```

## Troubleshooting

### Panel Not Appearing

1. Verify `DEBUG = True` in `settings.py`
2. Refresh the page after enabling DEBUG
3. Check browser console for JavaScript errors
4. Verify `window.DJUST_DEBUG_INFO` exists in the console

### Event History Not Logging

1. Check browser console for errors
2. Verify events appear in the Network tab
3. Confirm `window.djustDebugPanel` is initialized

### Reducing Panel Overhead

```javascript
// Reduce history size (default: 50)
window.djustDebugPanel.maxHistory = 20;

// Or clear history manually via the "Clear" buttons
```

Close the panel when not actively debugging. Panel state (open/closed, active tab) is remembered per view class.
