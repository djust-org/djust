# Developer Debug Panel User Guide

The djust Developer Debug Panel provides real-time introspection and debugging capabilities for LiveView applications. This interactive tool helps you discover event handlers, monitor events, inspect VDOM patches, and debug state issues.

## Table of Contents

- [Enabling the Debug Panel](#enabling-the-debug-panel)
- [Opening the Debug Panel](#opening-the-debug-panel)
- [Features Overview](#features-overview)
  - [Event Handlers Tab](#event-handlers-tab)
  - [Event History Tab](#event-history-tab)
  - [VDOM Patches Tab](#vdom-patches-tab)
  - [Variables Tab](#variables-tab)
- [Common Workflows](#common-workflows)
- [Performance Tips](#performance-tips)
- [Troubleshooting](#troubleshooting)

## Enabling the Debug Panel

The debug panel is **automatically enabled** when Django's `DEBUG` mode is active.

**In `settings.py`:**
```python
DEBUG = True  # Debug panel will appear
```

**Production:**
```python
DEBUG = False  # Debug panel hidden (zero performance impact)
```

### Security Note

The debug panel only appears when `DEBUG = True`. Never deploy to production with DEBUG mode enabled, as this:
- Exposes internal application structure
- Shows variable values and state
- Logs all events and parameters
- Increases memory usage

## Opening the Debug Panel

### Keyboard Shortcut (Recommended)

Press **`Ctrl+Shift+D`** (Windows/Linux) or **`Cmd+Shift+D`** (Mac)

### Floating Button

Click the **🐞** button in the bottom-right corner of the page.

### Panel Position

The panel appears as a bottom dock that slides up from the bottom of the screen:
- **Height**: 350px (customizable via CSS)
- **Width**: Full screen width
- **Position**: Fixed at bottom, overlays page content
- **Sidebar**: Left-side vertical tab navigation

## Features Overview

### Event Handlers Tab

Shows all event handlers registered in the current LiveView, with comprehensive signature information.

![Event Handlers Tab](https://via.placeholder.com/800x400?text=Event+Handlers+Tab)

#### What You See

For each handler:
- **Handler name**: Method name (e.g., `search`, `filter_by_status`)
- **Description**: From docstring or `@event_handler(description=...)`
- **Parameters**: Name, type, required/optional, default values
- **Decorators**: Applied decorators (e.g., `@debounce`, `@optimistic`)

#### Example Display

```
search
  Search properties with debouncing

  Parameters:
    value: str = ""
    **kwargs

  Decorators: @debounce
```

#### Use Cases

1. **Discover available events**: See what handlers exist and what they do
2. **Check parameter names**: Copy exact parameter names for templates
3. **Understand signatures**: See required vs optional parameters
4. **Verify decorators**: Confirm debouncing, caching, etc. are applied

### Event History Tab

Real-time log of all events triggered in the current LiveView session.

![Event History Tab](https://via.placeholder.com/800x400?text=Event+History+Tab)

#### What You See

For each event:
- **Event name**: Handler that was called
- **Timestamp**: When the event occurred
- **Duration**: How long the event took (milliseconds)
- **Parameters**: JSON object with all parameters sent
- **Status**: Success (green) or error (red)
- **Error details**: Validation errors or exceptions

#### Example Display

```
search • 45.2ms • 4:32:15 PM   [Click to expand]

  Parameters:
  {
    "value": "luxury apartment",
    "_skipDecorators": false
  }

  [Copy JSON]
```

#### Use Cases

1. **Debug parameter issues**: See exactly what parameters were sent
2. **Performance monitoring**: Track event execution times
3. **Validate event flow**: Confirm events fire in correct order
4. **Create test cases**: Copy event JSON for unit tests
5. **Track validation errors**: See why parameters were rejected

#### Features

- **Last 50 events**: Automatically circular buffer (configurable)
- **Collapsible**: Click event header to expand/collapse details
- **Copy JSON**: Copy event data for testing
- **Export all**: Download entire event history as JSON
- **Color coding**: Green for success, red for errors

### VDOM Patches Tab

Shows virtual DOM patch operations for debugging rendering and performance issues.

![VDOM Patches Tab](https://via.placeholder.com/800x400?text=VDOM+Patches+Tab)

#### What You See

For each patch set:
- **Patch count**: Number of DOM operations
- **Timestamp**: When patches were applied
- **Duration**: How long patch application took (sub-millisecond)
- **Patch operations**: SetAttr, SetText, Replace, InsertChild, RemoveChild, MoveChild

#### Example Display

```
3 patches • 0.85ms • 4:32:15 PM   [Click to expand]

  [
    {
      "type": "SetText",
      "path": [0, 1, 2],
      "text": "5 properties"
    },
    {
      "type": "SetAttr",
      "path": [0, 3, 0],
      "key": "value",
      "value": "luxury apartment"
    }
  ]

  Applied in 0.85ms
```

#### Use Cases

1. **Performance tuning**: Track patch count (fewer = faster)
2. **Debug rendering**: See exactly what DOM changes occur
3. **Optimize handlers**: Reduce unnecessary state changes
4. **Verify VDOM**: Confirm patches match expected changes
5. **Investigate issues**: Debug when DOM doesn't update as expected

#### Performance Targets

- **Typical update**: 1-5 patches, <2ms application time
- **Form input**: 1-2 patches, <1ms
- **List update**: 5-20 patches, <5ms
- **Full refresh**: 50+ patches, consider optimization if >10ms

### Variables Tab

Shows current public instance variables and their values.

![Variables Tab](https://via.placeholder.com/800x400?text=Variables+Tab)

#### What You See

For each variable:
- **Variable name**: Instance variable name
- **Type**: Python type
- **Value**: Current value (truncated at 100 chars)

#### Example Display

```
properties
  QuerySet
  <QuerySet [<Property: Luxury Apartment>, <Property: Beach House>]>

search_query
  str
  "luxury apartment"

filter_status
  str
  "all"
```

#### Use Cases

1. **Inspect state**: See current values of all public variables
2. **Debug state issues**: Verify variables have expected values
3. **Understand context**: See what's available in templates
4. **Track state changes**: Refresh tab to see updated values

#### Note on Private Variables

Only **public variables** (not starting with `_`) are shown. This follows the JIT Serialization Pattern convention. See `docs/EVENT_HANDLERS.md#public-vs-private-variables`.

## Common Workflows

### Workflow 1: Discovering Available Events

**Goal**: Find out what events are available and how to call them

**Steps**:
1. Open debug panel (`Ctrl+Shift+D`)
2. Click **Event Handlers** tab
3. Browse available handlers
4. Note parameter names and types
5. Use in template with `dj-click="handler_name"` or `dj-input="handler_name"`

**Example**:
```
Debug Panel shows:
  filter_by_status(value: str = "all")

Template:
  <select dj-change="filter_by_status">
    <option value="all">All</option>
    <option value="active">Active</option>
  </select>
```

### Workflow 2: Debugging Parameter Mismatch

**Goal**: Figure out why handler isn't receiving correct parameters

**Steps**:
1. Trigger event from UI (e.g., type in search box)
2. Open debug panel
3. Go to **Event History** tab
4. Click the event to expand
5. Check **Parameters** section
6. Go to **Event Handlers** tab
7. Compare sent params with expected params

**Common Issue**:
```
Event History shows:
  search { "query": "test" }   ❌ Wrong parameter name

Event Handlers shows:
  search(value: str = "")      ✅ Expects "value"

Solution: Use dj-input="search" with parameter name "value"
```

### Workflow 3: Performance Optimization

**Goal**: Reduce VDOM patch count to improve rendering performance

**Steps**:
1. Open debug panel
2. Go to **VDOM Patches** tab
3. Trigger an update (e.g., filter items)
4. Check patch count and duration
5. If >20 patches or >5ms, optimize handler

**Optimization strategies**:
- Avoid setting multiple variables (batch updates)
- Use private variables for intermediate state
- Only assign to public variables in `get_context_data()`
- Consider `@optimistic` for instant UI updates

**Before optimization**:
```python
def filter_items(self, value: str = ""):
    self.filter_value = value           # Patch 1
    self.items = self._get_filtered()   # Patches 2-50
    self.count = len(self.items)        # Patch 51
    self.message = f"{self.count} items" # Patch 52
```
Result: 52 patches, 8.2ms

**After optimization**:
```python
def filter_items(self, value: str = ""):
    self.filter_value = value
    self._refresh_items()  # Build in private variable

def get_context_data(self, **kwargs):
    self.items = self._items  # Single assignment triggers JIT
    context = super().get_context_data(**kwargs)
    context['count'] = len(self.items)
    return context
```
Result: 3 patches, 1.1ms

### Workflow 4: Creating Test Cases from Events

**Goal**: Generate test data from real interactions

**Steps**:
1. Interact with the app normally
2. Open debug panel
3. Go to **Event History** tab
4. Find the event you want to test
5. Click **Copy JSON** button
6. Paste into test file

**Example**:
```python
# From debug panel:
{
    "name": "save_property",
    "params": {
        "name": "Luxury Apartment",
        "address": "123 Main St",
        "price": 500000.0
    }
}

# Test case:
def test_save_property(self):
    view = PropertyListView()
    view.mount(self.request)

    view.save_property(
        name="Luxury Apartment",
        address="123 Main St",
        price=500000.0
    )

    assert Property.objects.filter(name="Luxury Apartment").exists()
```

### Workflow 5: Monitoring Real-time Updates

**Goal**: Watch events and patches as they happen during development

**Steps**:
1. Open debug panel
2. Position browser window to see both app and panel
3. Click **Event History** tab
4. Interact with app
5. Watch events appear in real-time
6. Check for validation errors (red items)

**Pro tip**: Use dual monitors - app on one, debug panel on the other.

## Performance Tips

### Minimize Impact

- **Close when not needed**: Panel uses ~10KB memory per 50 events
- **Clear history**: Click "Clear" button in Event History/VDOM Patches tabs
- **Limit history**: Configure `maxHistory` in client.js (default: 50)

### Optimize Rendering

- **Target**: <5 patches per update, <2ms application time
- **Red flag**: >20 patches or >5ms indicates optimization needed
- **Strategy**: Use JIT Serialization Pattern to batch updates

### Export for Analysis

Export event history for offline analysis:
```javascript
// In debug panel Event History tab
Click "Export All Events" button

// Saves to: djust-events-[timestamp].json
{
    "exported_at": "2025-11-18T16:30:00.000Z",
    "view_class": "PropertyListView",
    "event_count": 23,
    "events": [...]
}
```

## Troubleshooting

### Debug Panel Not Appearing

**Symptom**: No floating button, no panel

**Checklist**:
1. ✅ Is `DEBUG = True` in settings.py?
2. ✅ Did you refresh the page after enabling DEBUG?
3. ✅ Check browser console for JavaScript errors
4. ✅ Verify `window.DJUST_DEBUG_INFO` exists in console

**Solution**:
```javascript
// In browser console:
console.log(window.DJUST_DEBUG_INFO);

// Should show:
{
    "view_class": "MyView",
    "handlers": {...},
    "variables": {...}
}
```

### Event Handler Tab Empty

**Symptom**: "No event handlers found" message

**Cause**: No handlers decorated with `@event_handler()`

**Solution**: Add decorator to event handlers:
```python
from djust.decorators import event_handler

@event_handler()  # Add this!
def my_handler(self, value: str = ""):
    pass
```

### Event History Not Logging Events

**Symptom**: Events fire but don't appear in history

**Checklist**:
1. ✅ Check browser console for errors
2. ✅ Verify events appear in network tab
3. ✅ Check if event has `_skipDecorators: true` (internal events)

**Debug**:
```javascript
// Check if hooking worked:
console.log(window.djustDebugPanel);

// Should show DjustDebugPanel instance with:
// - eventHistory: []
// - patchHistory: []
```

### VDOM Patches Not Showing

**Symptom**: Patches tab always says "No patches captured yet"

**Cause**: `applyPatches` function missing or hook failed

**Solution**: Check console for warnings:
```
[djust] applyPatches not found - patch logging disabled
```

If you see this, the applyPatches function needs to be restored (it may have been accidentally removed during refactoring).

### Panel Keyboard Shortcut Not Working

**Symptom**: `Ctrl+Shift+D` doesn't toggle panel

**Checklist**:
1. ✅ Try clicking the 🐞 button instead
2. ✅ Check if another extension is using the shortcut
3. ✅ Try `Cmd+Shift+D` on Mac
4. ✅ Check browser console for errors

**Alternative**: Always use the floating button.

### Performance Issues

**Symptom**: Panel slows down the app

**Causes**:
- Too many events logged (>100)
- Large patch history (>100)
- Very large parameter objects

**Solutions**:
```javascript
// 1. Clear history regularly (click "Clear" buttons)

// 2. Reduce maxHistory in client.js:
window.djustDebugPanel.maxHistory = 20;  // Default: 50

// 3. Close panel when not actively debugging
```

## Advanced Features

### Filtering Event History (Future)

Planned feature to filter events by:
- Handler name
- Success/error status
- Time range
- Parameter values

### Event Replay (Future)

Planned feature to replay events for debugging:
1. Select event from history
2. Click "Replay"
3. Event resent with same parameters

### Network Tab (Future)

Planned feature to inspect WebSocket messages:
- See raw messages sent/received
- Monitor connection status
- Debug protocol issues

## See Also

- **[Event Handler Best Practices](EVENT_HANDLERS.md)** - Writing effective handlers
- **[JIT Serialization Pattern](JIT_SERIALIZATION_PATTERN.md)** - Performance optimization
- **[State Management API](STATE_MANAGEMENT_API.md)** - Decorators reference
