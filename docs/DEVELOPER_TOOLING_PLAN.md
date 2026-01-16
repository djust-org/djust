# Developer Tooling Enhancement Plan

**Status:** Planning Phase
**Created:** 2025-11-18
**Priority:** High (Developer Experience)

## Overview

Add comprehensive developer experience improvements to djust: parameter validation, enhanced @event_handler decorator, interactive debug panel, and documentation.

## Background

### Current Issues
1. **No parameter validation** - Wrong parameter names cause silent failures (e.g., `value` vs `query`/`sort`/`status`)
2. **No event introspection** - Developers can't discover what events exist or what parameters they accept
3. **Limited debug tooling** - Only basic logging, no interactive debugging
4. **Undocumented conventions** - Public/private variable naming is convention but not enforced or documented

### User Preferences (2025-11-18)
- ✅ Implement all 4 features (validation, decorator, debug panel, documentation)
- ✅ Debug panel: Floating button + keyboard shortcut (Ctrl+Shift+D)
- ✅ Strict validation by default (reject unexpected parameters)
- ✅ Runtime type validation using Python type hints

---

## Phase 1: Event Parameter Validation (Core Foundation)

### Goals
- Validate handler signatures before calling
- Provide clear error messages for parameter mismatches
- Support runtime type validation using type hints
- Fail fast on validation errors (strict mode)

### Files to Create
- `python/djust/validation.py` - **NEW** - Parameter validation utilities

### Files to Modify
- `python/djust/websocket.py` - Add validation in `handle_event()` (before line 527)
- `python/djust/live_view.py` - Add validation in HTTP POST handler

### Implementation Details

#### 1. Create `python/djust/validation.py`

```python
"""
Event handler parameter validation utilities.

Provides runtime validation of event handler signatures including:
- Required parameter checking
- Unexpected parameter detection
- Type validation using type hints
- Clear error message generation
"""

import inspect
from typing import Any, Dict, List, Optional, Callable, get_type_hints


def validate_handler_params(
    handler: Callable,
    params: Dict[str, Any],
    event_name: str
) -> Dict[str, Any]:
    """
    Validate event parameters match handler signature.

    Args:
        handler: Event handler method to validate against
        params: Parameters provided by client event
        event_name: Name of the event (for error messages)

    Returns:
        Dict with validation result:
        {
            "valid": bool,
            "error": Optional[str],
            "expected": List[str],  # Expected parameter names
            "provided": List[str],  # Provided parameter names
            "type_errors": Optional[List[Dict]]  # Type mismatch details
        }
    """
    sig = inspect.signature(handler)

    # Extract parameter information
    required_params = []
    optional_params = []
    accepted_params = []
    has_var_keyword = False

    for name, param in sig.parameters.items():
        # Skip 'self' parameter
        if name == 'self':
            continue

        # Check for **kwargs
        if param.kind == inspect.Parameter.VAR_KEYWORD:
            has_var_keyword = True
            continue

        # Skip *args
        if param.kind == inspect.Parameter.VAR_POSITIONAL:
            continue

        accepted_params.append(name)

        if param.default == inspect.Parameter.empty:
            required_params.append(name)
        else:
            optional_params.append(name)

    # Check for missing required parameters
    missing = [p for p in required_params if p not in params]
    if missing:
        return {
            "valid": False,
            "error": f"Handler '{event_name}' missing required parameters: {missing}",
            "expected": accepted_params,
            "provided": list(params.keys()),
            "type_errors": None
        }

    # Check for unexpected parameters (if no **kwargs)
    if not has_var_keyword:
        unexpected = [p for p in params if p not in accepted_params]
        if unexpected:
            return {
                "valid": False,
                "error": f"Handler '{event_name}' received unexpected parameters: {unexpected}. Expected: {accepted_params}",
                "expected": accepted_params,
                "provided": list(params.keys()),
                "type_errors": None
            }

    # Validate parameter types using type hints
    type_errors = validate_parameter_types(handler, params)
    if type_errors:
        error_msg = f"Handler '{event_name}' received wrong parameter types:\n"
        for err in type_errors:
            error_msg += f"  - {err['param']}: expected {err['expected']}, got {err['actual']}\n"

        return {
            "valid": False,
            "error": error_msg.strip(),
            "expected": accepted_params,
            "provided": list(params.keys()),
            "type_errors": type_errors
        }

    return {
        "valid": True,
        "error": None,
        "expected": accepted_params,
        "provided": list(params.keys()),
        "type_errors": None
    }


def validate_parameter_types(
    handler: Callable,
    params: Dict[str, Any]
) -> Optional[List[Dict[str, str]]]:
    """
    Validate parameter types against type hints.

    Args:
        handler: Event handler method
        params: Parameters provided by client

    Returns:
        List of type errors, or None if all types valid
        Each error dict contains: {param, expected, actual}
    """
    try:
        type_hints = get_type_hints(handler)
    except Exception:
        # If type hints can't be extracted, skip type validation
        return None

    errors = []

    for param_name, param_value in params.items():
        if param_name not in type_hints:
            continue

        expected_type = type_hints[param_name]

        # Skip complex types (Union, Optional, etc.) for now
        if not isinstance(expected_type, type):
            continue

        # Check type match
        if not isinstance(param_value, expected_type):
            errors.append({
                "param": param_name,
                "expected": expected_type.__name__,
                "actual": type(param_value).__name__
            })

    return errors if errors else None


def get_handler_signature_info(handler: Callable) -> Dict[str, Any]:
    """
    Extract comprehensive signature information from handler.

    Used by debug panel and @event_handler decorator.

    Args:
        handler: Event handler method

    Returns:
        Dict containing:
        - params: List of parameter dicts with name, type, required, default
        - description: Handler docstring
        - accepts_kwargs: Whether handler accepts **kwargs
    """
    sig = inspect.signature(handler)

    try:
        type_hints = get_type_hints(handler)
    except Exception:
        type_hints = {}

    params = []
    accepts_kwargs = False

    for name, param in sig.parameters.items():
        if name == 'self':
            continue

        if param.kind == inspect.Parameter.VAR_KEYWORD:
            accepts_kwargs = True
            continue

        if param.kind == inspect.Parameter.VAR_POSITIONAL:
            continue

        param_info = {
            "name": name,
            "type": type_hints.get(name, Any).__name__ if name in type_hints else "Any",
            "required": param.default == inspect.Parameter.empty,
            "default": str(param.default) if param.default != inspect.Parameter.empty else None
        }

        params.append(param_info)

    return {
        "params": params,
        "description": inspect.getdoc(handler) or "",
        "accepts_kwargs": accepts_kwargs
    }
```

#### 2. Integrate into `websocket.py`

Insert before line 527 (before calling handler):

```python
# Validate parameters before calling handler
from djust.validation import validate_handler_params

validation = validate_handler_params(handler, params, event_name)
if not validation["valid"]:
    logger.error(f"Parameter validation failed: {validation['error']}")
    await self.send_json({
        "type": "error",
        "error": validation["error"],
        "validation_details": {
            "expected_params": validation["expected"],
            "provided_params": validation["provided"],
            "type_errors": validation["type_errors"]
        }
    })
    return
```

#### 3. Integrate into `live_view.py` HTTP handler

Similar validation in HTTP POST handler (around line 1840):

```python
from djust.validation import validate_handler_params

validation = validate_handler_params(handler, event_data, event_name)
if not validation["valid"]:
    return JsonResponse({
        "type": "error",
        "error": validation["error"],
        "validation_details": {
            "expected_params": validation["expected"],
            "provided_params": validation["provided"],
            "type_errors": validation["type_errors"]
        }
    }, status=400)
```

### Testing

**Unit tests in `tests/test_validation.py`:**

```python
def test_missing_required_parameter():
    """Test that missing required parameters are caught"""
    def handler(self, required_param: str, optional: str = "default"):
        pass

    result = validate_handler_params(handler, {}, "test_event")
    assert result["valid"] is False
    assert "missing required parameters" in result["error"]
    assert "required_param" in result["error"]

def test_unexpected_parameter():
    """Test that unexpected parameters are rejected"""
    def handler(self, value: str = ""):
        pass

    result = validate_handler_params(
        handler,
        {"value": "test", "unexpected": "bad"},
        "test_event"
    )
    assert result["valid"] is False
    assert "unexpected parameters" in result["error"]
    assert "unexpected" in result["error"]

def test_type_validation():
    """Test that type hints are validated"""
    def handler(self, count: int):
        pass

    result = validate_handler_params(
        handler,
        {"count": "not_an_int"},
        "test_event"
    )
    assert result["valid"] is False
    assert "wrong parameter types" in result["error"]
    assert result["type_errors"][0]["param"] == "count"
    assert result["type_errors"][0]["expected"] == "int"
    assert result["type_errors"][0]["actual"] == "str"

def test_kwargs_accepts_any_param():
    """Test that **kwargs handlers accept any parameters"""
    def handler(self, **kwargs):
        pass

    result = validate_handler_params(
        handler,
        {"any": "param", "works": "here"},
        "test_event"
    )
    assert result["valid"] is True
```

### Benefits
- ✅ Catches the `value` vs `query`/`sort`/`status` parameter mismatch immediately
- ✅ Clear error messages showing expected vs provided parameters
- ✅ Type validation prevents runtime TypeErrors
- ✅ Fails fast before any handler code executes (no partial state changes)
- ✅ Works in both WebSocket and HTTP modes

---

## Phase 2: Enhanced @event_handler Decorator

### Goals
- Auto-extract parameter names, types, and descriptions from handler signatures
- Store metadata for debug panel introspection
- Maintain backward compatibility with existing code
- Support optional explicit parameter declaration

### Files to Modify
- `python/djust/decorators.py` - Enhance existing `event_handler()` decorator (lines 32-46)

### Implementation Details

#### Enhance `event_handler()` decorator

Replace existing implementation (lines 32-46) with:

```python
from typing import List, Optional, TypeVar, Callable, cast
from djust.validation import get_handler_signature_info

F = TypeVar('F', bound=Callable[..., Any])

def event_handler(
    params: Optional[List[str]] = None,
    description: str = ""
) -> Callable[[F], F]:
    """
    Mark method as event handler with automatic signature introspection.

    Auto-extracts parameter names, types, and descriptions from function signature.
    Stores metadata in _djust_decorators for validation and debug panel.

    Args:
        params: Optional explicit parameter list (overrides auto-extraction)
        description: Human-readable description (overrides docstring)

    Usage:
        @event_handler
        def search(self, value: str = "", **kwargs):
            '''Search leases with debouncing'''
            self.search_query = value
            self._refresh_leases()

        @event_handler(description="Update item quantity")
        def update_item(self, item_id: int, quantity: int, **kwargs):
            self.items[item_id].quantity = quantity
    """
    def decorator(func: F) -> F:
        # Extract comprehensive signature information
        sig_info = get_handler_signature_info(func)

        # Use explicit params if provided, otherwise use extracted
        if params is not None:
            param_names = params
        else:
            param_names = [p["name"] for p in sig_info["params"]]

        # Use explicit description if provided, otherwise use docstring
        final_description = description or sig_info["description"]

        # Store comprehensive metadata
        _add_decorator_metadata(func, "event_handler", {
            "params": sig_info["params"],  # Full param info with types
            "param_names": param_names,    # Just names for quick lookup
            "description": final_description,
            "accepts_kwargs": sig_info["accepts_kwargs"],
            "required": [p["name"] for p in sig_info["params"] if p["required"]],
            "optional": [p["name"] for p in sig_info["params"] if not p["required"]]
        })

        # Keep existing markers for backward compatibility
        func._is_event_handler = True
        func._event_name = func.__name__

        return cast(F, func)

    # Support both @event_handler and @event_handler()
    if callable(params):
        # Called as @event_handler (no parentheses)
        func = params
        params = None
        return decorator(func)

    return decorator
```

### Usage Examples

```python
# Basic usage (auto-extracts everything)
@event_handler
def search(self, value: str = "", **kwargs):
    """Search leases with debouncing"""
    self.search_query = value
    self._refresh_leases()

# Explicit description
@event_handler(description="Sort leases by field")
def sort_leases(self, value: str = "end_date", **kwargs):
    self.sort_by = value
    self._refresh_leases()

# Type hints are automatically extracted
@event_handler
def update_quantity(self, item_id: int, quantity: int = 1):
    """Update item quantity"""
    self.items[item_id].quantity = quantity
```

### Metadata Example

After decoration, the function will have:

```python
func._djust_decorators = {
    "event_handler": {
        "params": [
            {
                "name": "value",
                "type": "str",
                "required": False,
                "default": ""
            }
        ],
        "param_names": ["value"],
        "description": "Search leases with debouncing",
        "accepts_kwargs": True,
        "required": [],
        "optional": ["value"]
    }
}
```

### Testing

**Unit tests in `tests/test_decorators.py`:**

```python
def test_event_handler_extracts_signature():
    """Test that decorator extracts parameter information"""
    @event_handler
    def my_handler(self, value: str = "", count: int = 0):
        pass

    metadata = my_handler._djust_decorators["event_handler"]
    assert len(metadata["params"]) == 2
    assert metadata["params"][0]["name"] == "value"
    assert metadata["params"][0]["type"] == "str"
    assert metadata["params"][0]["required"] is False
    assert metadata["params"][1]["name"] == "count"
    assert metadata["params"][1]["type"] == "int"

def test_event_handler_with_required_params():
    """Test required vs optional parameter detection"""
    @event_handler
    def my_handler(self, required: str, optional: str = "default"):
        pass

    metadata = my_handler._djust_decorators["event_handler"]
    assert "required" in metadata["required"]
    assert "optional" in metadata["optional"]

def test_event_handler_backward_compatible():
    """Test that existing markers are still set"""
    @event_handler
    def my_handler(self):
        pass

    assert hasattr(my_handler, "_is_event_handler")
    assert my_handler._is_event_handler is True
    assert my_handler._event_name == "my_handler"
```

### Benefits
- ✅ Auto-documents handler signatures
- ✅ Powers debug panel with complete handler information
- ✅ Enables IDE autocomplete for event parameters (via type hints)
- ✅ Backward compatible (works with or without arguments)
- ✅ Foundation for future enhancements (rate limiting, permissions, etc.)

---

## Phase 3: Developer Debug Panel (Interactive UI)

### Goals
- Provide interactive UI for discovering event handlers
- Show real-time event history and parameters
- Display VDOM patch history
- Enable event replay and debugging
- Only visible in Django DEBUG mode

### Files to Create
- `python/djust/static/djust/debug-panel.css` - **NEW** - Debug panel styles

### Files to Modify
- `python/djust/live_view.py` - Add `get_debug_info()` method, inject debug data
- `python/djust/static/djust/client.js` - Add `DjustDebugPanel` class

### Implementation Details

#### 1. Server-Side: Debug Info Generation

Add to `python/djust/live_view.py` (around line 2100):

```python
def get_debug_info(self) -> Dict[str, Any]:
    """
    Get debug information about this LiveView instance.

    Used by developer debug panel to show:
    - Available event handlers with signatures
    - Public variables and their current values
    - Decorator metadata

    Returns:
        Dict with debug information
    """
    import inspect

    handlers = {}
    variables = {}

    for name in dir(self):
        # Skip private attributes
        if name.startswith('_'):
            continue

        attr = getattr(self, name)

        # Collect event handlers
        if callable(attr) and hasattr(attr, '_is_event_handler'):
            sig_info = get_handler_signature_info(attr)

            handlers[name] = {
                "name": name,
                "params": sig_info["params"],
                "description": sig_info["description"],
                "accepts_kwargs": sig_info["accepts_kwargs"],
                "decorators": getattr(attr, '_djust_decorators', {})
            }

        # Collect public variables
        elif not callable(attr):
            variables[name] = {
                "name": name,
                "type": type(attr).__name__,
                "value": repr(attr)[:100]  # Truncate long values
            }

    return {
        "view_class": self.__class__.__name__,
        "handlers": handlers,
        "variables": variables,
        "template": self.template_name if hasattr(self, 'template_name') else None
    }
```

#### 2. Inject Debug Info into HTML

Modify `_inject_client_script()` method (around line 1995):

```python
def _inject_client_script(self, html: str) -> str:
    """Inject LiveView client script and configuration"""
    from django.conf import settings

    use_websocket = config.get("use_websocket", True)
    debug_vdom = config.get("debug_vdom", False)

    # Include debug info if Django DEBUG mode
    debug_info_script = ""
    if settings.DEBUG:
        import json
        debug_info = self.get_debug_info()
        debug_info_script = f"""
        <script>
            window.DJUST_DEBUG_INFO = {json.dumps(debug_info)};
        </script>
        """

    config_script = f"""
    <script>
        window.DJUST_USE_WEBSOCKET = {'true' if use_websocket else 'false'};
        window.DJUST_DEBUG_VDOM = {'true' if debug_vdom else 'false'};
    </script>
    {debug_info_script}
    """

    # ... rest of method
```

#### 3. Client-Side: Debug Panel Component

Add to end of `python/djust/static/djust/client.js`:

```javascript
// ============================================================================
// Developer Debug Panel
// ============================================================================

class DjustDebugPanel {
    constructor() {
        this.visible = false;
        this.currentTab = 'handlers';
        this.eventHistory = [];
        this.patchHistory = [];
        this.maxHistory = 50;

        this.createUI();
        this.setupKeyboardShortcut();
    }

    createUI() {
        // Create floating button (only visible in DEBUG mode)
        if (typeof window.DJUST_DEBUG_INFO === 'undefined') {
            return; // Not in debug mode
        }

        // Floating button
        this.button = document.createElement('button');
        this.button.className = 'djust-debug-button';
        this.button.innerHTML = '🔧';
        this.button.title = 'Open djust Debug Panel (Ctrl+Shift+D)';
        this.button.onclick = () => this.toggle();
        document.body.appendChild(this.button);

        // Panel container
        this.panel = document.createElement('div');
        this.panel.className = 'djust-debug-panel';
        this.panel.style.display = 'none';
        document.body.appendChild(this.panel);
    }

    setupKeyboardShortcut() {
        document.addEventListener('keydown', (e) => {
            if (e.ctrlKey && e.shiftKey && e.key === 'D') {
                e.preventDefault();
                this.toggle();
            }
        });
    }

    toggle() {
        this.visible = !this.visible;

        if (this.visible) {
            this.render();
            this.panel.style.display = 'block';
            this.button.classList.add('active');
        } else {
            this.panel.style.display = 'none';
            this.button.classList.remove('active');
        }
    }

    render() {
        const debugInfo = window.DJUST_DEBUG_INFO || {};

        this.panel.innerHTML = `
            <div class="djust-debug-header">
                <h3>djust Debug Panel</h3>
                <button onclick="window.djustDebugPanel.toggle()">✕</button>
            </div>

            <div class="djust-debug-tabs">
                <button class="${this.currentTab === 'handlers' ? 'active' : ''}"
                        onclick="window.djustDebugPanel.switchTab('handlers')">
                    Event Handlers
                </button>
                <button class="${this.currentTab === 'history' ? 'active' : ''}"
                        onclick="window.djustDebugPanel.switchTab('history')">
                    Event History (${this.eventHistory.length})
                </button>
                <button class="${this.currentTab === 'patches' ? 'active' : ''}"
                        onclick="window.djustDebugPanel.switchTab('patches')">
                    VDOM Patches
                </button>
                <button class="${this.currentTab === 'variables' ? 'active' : ''}"
                        onclick="window.djustDebugPanel.switchTab('variables')">
                    Variables
                </button>
            </div>

            <div class="djust-debug-content">
                ${this.renderTabContent()}
            </div>
        `;
    }

    renderTabContent() {
        const debugInfo = window.DJUST_DEBUG_INFO || {};

        switch (this.currentTab) {
            case 'handlers':
                return this.renderHandlers(debugInfo.handlers || {});
            case 'history':
                return this.renderEventHistory();
            case 'patches':
                return this.renderPatchHistory();
            case 'variables':
                return this.renderVariables(debugInfo.variables || {});
            default:
                return '';
        }
    }

    renderHandlers(handlers) {
        if (Object.keys(handlers).length === 0) {
            return '<p class="empty">No event handlers found</p>';
        }

        let html = '<div class="handler-list">';

        for (const [name, info] of Object.entries(handlers)) {
            const params = info.params || [];
            const decorators = Object.keys(info.decorators || {}).filter(d => d !== 'event_handler');

            html += `
                <div class="handler-item">
                    <div class="handler-name">${name}</div>
                    ${info.description ? `<div class="handler-desc">${info.description}</div>` : ''}

                    <div class="handler-signature">
                        <strong>Parameters:</strong>
                        ${params.length > 0 ? params.map(p => `
                            <div class="param">
                                <code>${p.name}</code>:
                                <span class="type">${p.type}</span>
                                ${!p.required ? ` = <span class="default">${p.default}</span>` : '<span class="required">*required</span>'}
                            </div>
                        `).join('') : '<span class="empty">No parameters</span>'}
                        ${info.accepts_kwargs ? '<div class="param"><code>**kwargs</code></div>' : ''}
                    </div>

                    ${decorators.length > 0 ? `
                        <div class="handler-decorators">
                            <strong>Decorators:</strong> ${decorators.map(d => `<code>@${d}</code>`).join(', ')}
                        </div>
                    ` : ''}
                </div>
            `;
        }

        html += '</div>';
        return html;
    }

    renderEventHistory() {
        if (this.eventHistory.length === 0) {
            return '<p class="empty">No events captured yet. Interact with the page to see events here.</p>';
        }

        let html = '<div class="event-history">';

        // Reverse to show newest first
        for (const event of [...this.eventHistory].reverse()) {
            const timestamp = new Date(event.timestamp).toLocaleTimeString();
            const success = event.error ? 'error' : 'success';

            html += `
                <div class="event-item ${success}">
                    <div class="event-header">
                        <span class="event-name">${event.name}</span>
                        <span class="event-time">${timestamp}</span>
                    </div>

                    <div class="event-params">
                        <strong>Parameters:</strong>
                        <pre>${JSON.stringify(event.params, null, 2)}</pre>
                    </div>

                    ${event.error ? `
                        <div class="event-error">
                            <strong>Error:</strong> ${event.error}
                        </div>
                    ` : ''}

                    <div class="event-actions">
                        <button onclick="navigator.clipboard.writeText('${JSON.stringify(event).replace(/'/g, "\\'")}')">
                            Copy JSON
                        </button>
                    </div>
                </div>
            `;
        }

        html += '</div>';
        return html;
    }

    renderPatchHistory() {
        if (this.patchHistory.length === 0) {
            return '<p class="empty">No patches captured yet.</p>';
        }

        let html = '<div class="patch-history">';

        for (const entry of [...this.patchHistory].reverse()) {
            html += `
                <div class="patch-item">
                    <div class="patch-header">
                        <span>${entry.count} patches</span>
                        <span>${new Date(entry.timestamp).toLocaleTimeString()}</span>
                    </div>
                    <pre>${JSON.stringify(entry.patches.slice(0, 5), null, 2)}</pre>
                    ${entry.patches.length > 5 ? `<p>... and ${entry.patches.length - 5} more</p>` : ''}
                </div>
            `;
        }

        html += '</div>';
        return html;
    }

    renderVariables(variables) {
        if (Object.keys(variables).length === 0) {
            return '<p class="empty">No public variables found</p>';
        }

        let html = '<div class="variable-list">';

        for (const [name, info] of Object.entries(variables)) {
            html += `
                <div class="variable-item">
                    <div class="variable-name">${name}</div>
                    <div class="variable-type">${info.type}</div>
                    <div class="variable-value"><code>${info.value}</code></div>
                </div>
            `;
        }

        html += '</div>';
        return html;
    }

    switchTab(tab) {
        this.currentTab = tab;
        this.render();
    }

    logEvent(eventName, params, result) {
        this.eventHistory.push({
            timestamp: Date.now(),
            name: eventName,
            params: params,
            error: result && result.type === 'error' ? result.error : null
        });

        // Keep last 50 events
        if (this.eventHistory.length > this.maxHistory) {
            this.eventHistory.shift();
        }

        if (this.visible && this.currentTab === 'history') {
            this.render();
        }
    }

    logPatches(patches) {
        this.patchHistory.push({
            timestamp: Date.now(),
            count: patches.length,
            patches: patches
        });

        if (this.patchHistory.length > this.maxHistory) {
            this.patchHistory.shift();
        }

        if (this.visible && this.currentTab === 'patches') {
            this.render();
        }
    }
}

// Initialize global debug panel
if (typeof window.DJUST_DEBUG_INFO !== 'undefined') {
    window.djustDebugPanel = new DjustDebugPanel();

    // Hook into event sending to log events
    const originalSendEvent = LiveView.prototype.sendEvent;
    LiveView.prototype.sendEvent = function(eventName, params) {
        const result = originalSendEvent.call(this, eventName, params);
        window.djustDebugPanel.logEvent(eventName, params, null);
        return result;
    };

    // Hook into patch application to log patches
    const originalApplyPatches = applyPatches;
    applyPatches = function(patches) {
        window.djustDebugPanel.logPatches(patches);
        return originalApplyPatches(patches);
    };
}
```

#### 4. Styles: `python/djust/static/djust/debug-panel.css`

```css
/* djust Developer Debug Panel Styles */

.djust-debug-button {
    position: fixed;
    bottom: 20px;
    right: 20px;
    width: 50px;
    height: 50px;
    border-radius: 50%;
    background: #3b82f6;
    color: white;
    border: none;
    font-size: 24px;
    cursor: pointer;
    box-shadow: 0 4px 6px rgba(0, 0, 0, 0.1);
    z-index: 9998;
    transition: all 0.2s;
}

.djust-debug-button:hover {
    background: #2563eb;
    transform: scale(1.1);
}

.djust-debug-button.active {
    background: #1e40af;
}

.djust-debug-panel {
    position: fixed;
    top: 0;
    right: 0;
    width: 500px;
    height: 100vh;
    background: #1e293b;
    color: #e2e8f0;
    box-shadow: -4px 0 6px rgba(0, 0, 0, 0.1);
    z-index: 9999;
    display: flex;
    flex-direction: column;
    font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
}

.djust-debug-header {
    display: flex;
    justify-content: space-between;
    align-items: center;
    padding: 16px;
    background: #0f172a;
    border-bottom: 1px solid #334155;
}

.djust-debug-header h3 {
    margin: 0;
    font-size: 18px;
    font-weight: 600;
}

.djust-debug-header button {
    background: transparent;
    border: none;
    color: #e2e8f0;
    font-size: 24px;
    cursor: pointer;
    padding: 0;
    width: 32px;
    height: 32px;
}

.djust-debug-tabs {
    display: flex;
    background: #0f172a;
    border-bottom: 1px solid #334155;
}

.djust-debug-tabs button {
    flex: 1;
    padding: 12px;
    background: transparent;
    border: none;
    color: #94a3b8;
    font-size: 13px;
    cursor: pointer;
    border-bottom: 2px solid transparent;
    transition: all 0.2s;
}

.djust-debug-tabs button:hover {
    color: #e2e8f0;
    background: #1e293b;
}

.djust-debug-tabs button.active {
    color: #3b82f6;
    border-bottom-color: #3b82f6;
}

.djust-debug-content {
    flex: 1;
    overflow-y: auto;
    padding: 16px;
}

/* Handler List */
.handler-item {
    background: #0f172a;
    border: 1px solid #334155;
    border-radius: 8px;
    padding: 16px;
    margin-bottom: 12px;
}

.handler-name {
    font-size: 16px;
    font-weight: 600;
    color: #3b82f6;
    margin-bottom: 8px;
}

.handler-desc {
    font-size: 13px;
    color: #94a3b8;
    margin-bottom: 12px;
}

.handler-signature {
    font-size: 13px;
}

.param {
    margin-left: 16px;
    margin-top: 4px;
}

.param code {
    background: #334155;
    padding: 2px 6px;
    border-radius: 3px;
    font-family: 'Monaco', 'Courier New', monospace;
}

.param .type {
    color: #22d3ee;
}

.param .default {
    color: #a78bfa;
}

.param .required {
    color: #f87171;
    font-size: 11px;
}

/* Event History */
.event-item {
    background: #0f172a;
    border: 1px solid #334155;
    border-left: 4px solid #22c55e;
    border-radius: 8px;
    padding: 16px;
    margin-bottom: 12px;
}

.event-item.error {
    border-left-color: #ef4444;
}

.event-header {
    display: flex;
    justify-content: space-between;
    margin-bottom: 8px;
}

.event-name {
    font-weight: 600;
    color: #3b82f6;
}

.event-time {
    font-size: 12px;
    color: #64748b;
}

.event-params pre {
    background: #334155;
    padding: 8px;
    border-radius: 4px;
    font-size: 12px;
    overflow-x: auto;
}

.event-error {
    margin-top: 8px;
    padding: 8px;
    background: #7f1d1d;
    border-radius: 4px;
    font-size: 13px;
}

.event-actions {
    margin-top: 8px;
}

.event-actions button {
    background: #334155;
    border: none;
    color: #e2e8f0;
    padding: 6px 12px;
    border-radius: 4px;
    font-size: 12px;
    cursor: pointer;
}

.event-actions button:hover {
    background: #475569;
}

/* Variables */
.variable-item {
    background: #0f172a;
    border: 1px solid #334155;
    border-radius: 8px;
    padding: 12px;
    margin-bottom: 8px;
}

.variable-name {
    font-weight: 600;
    color: #3b82f6;
    margin-bottom: 4px;
}

.variable-type {
    font-size: 12px;
    color: #22d3ee;
    margin-bottom: 4px;
}

.variable-value code {
    font-size: 12px;
    color: #94a3b8;
}

/* Empty States */
.empty {
    text-align: center;
    color: #64748b;
    padding: 32px;
}
```

#### 5. Load CSS in base template

The CSS should be automatically loaded when the debug panel is active. Add to `live_view.py` in `_inject_client_script()`:

```python
if settings.DEBUG:
    debug_css = '<link rel="stylesheet" href="/static/djust/debug-panel.css">'
    # Insert before </head>
    html = html.replace('</head>', f'{debug_css}</head>')
```

### Usage

1. **Enable**: Just run with `DEBUG = True` in Django settings
2. **Open**: Click floating button (bottom-right) or press `Ctrl+Shift+D`
3. **Explore**: Browse event handlers, see their signatures and parameters
4. **Monitor**: Watch events as they happen in real-time
5. **Debug**: Copy event JSON for test cases, inspect VDOM patches

### Testing

**Manual testing checklist:**
- [ ] Floating button appears in DEBUG mode only
- [ ] Keyboard shortcut (Ctrl+Shift+D) toggles panel
- [ ] Event Handlers tab shows all decorated handlers
- [ ] Parameter types and descriptions display correctly
- [ ] Event History captures events with parameters
- [ ] Error events show validation errors in red
- [ ] Copy JSON button works
- [ ] VDOM Patches tab shows recent patches
- [ ] Variables tab shows public instance variables
- [ ] Panel styling works on different screen sizes
- [ ] No JavaScript errors in console

### Benefits
- ✅ Instant discoverability of available events
- ✅ Clear documentation of parameters and types
- ✅ Real-time event monitoring for debugging
- ✅ VDOM patch inspection for performance tuning
- ✅ No impact on production (only in DEBUG mode)
- ✅ Minimal footprint when closed (just floating button)

---

## Phase 4: Documentation

### Goals
- Document public/private variable convention
- Explain event handler best practices
- Provide debug panel usage guide
- Create comprehensive examples

### Files to Create
- `docs/EVENT_HANDLERS.md` - **NEW** - Event handler patterns and best practices
- `docs/DEBUG_PANEL.md` - **NEW** - Debug panel user guide

### Files to Modify
- `CLAUDE.md` - Add developer tooling section
- `README.md` - Add brief mention with link to docs

### Content Structure

#### `docs/EVENT_HANDLERS.md`

```markdown
# Event Handler Best Practices

## Parameter Naming Convention

**Always use `value` for form input events:**

```python
# ✅ Correct - matches what @input/@change sends
@event_handler
def search(self, value: str = "", **kwargs):
    self.search_query = value

# ❌ Wrong - will get default value, state won't change
@event_handler
def search(self, query: str = "", **kwargs):
    self.search_query = query
```

## Type Hints

Use type hints for automatic validation:

```python
@event_handler
def update_quantity(self, item_id: int, quantity: int = 1):
    """Update item quantity - types validated at runtime"""
    self.items[item_id].quantity = quantity
```

## Decorator Usage

Mark all event handlers with `@event_handler`:

```python
@event_handler
def my_handler(self, value: str):
    """Handler description for debug panel"""
    pass
```

Benefits:
- Auto-extracted for debug panel
- Parameter validation
- IDE autocomplete support

## Public/Private Variables

**Convention:**
- `_private` - Not exposed to templates
- `public` - Auto-exposed to templates

```python
def mount(self, request):
    # Private - internal state
    self._properties = Property.objects.all()

    # Public - exposed to template
    self.properties = self._properties
    self.count = 0
```

## Error Handling

Parameter validation is **strict by default**:
- Missing required parameters → Error
- Unexpected parameters → Error
- Wrong types → Error

Check console/logs for validation errors with expected vs provided parameters.
```

#### `docs/DEBUG_PANEL.md`

```markdown
# Developer Debug Panel

Interactive debugging tool for djust LiveView development.

## Enabling

Automatically enabled when `DEBUG = True` in Django settings.

## Opening

- **Keyboard**: `Ctrl+Shift+D`
- **Click**: Floating button (bottom-right corner)

## Features

### Event Handlers Tab

Shows all event handlers with:
- Parameter names and types
- Required vs optional
- Decorators applied
- Description from docstring

### Event History Tab

Real-time log of events:
- Event name
- Parameters sent
- Timestamp
- Errors (if any)
- Copy as JSON for test cases

### VDOM Patches Tab

Shows recent patch operations:
- Number of patches
- Patch details (SetAttr, SetText, etc.)
- Performance monitoring

### Variables Tab

Current public variables:
- Variable name
- Type
- Current value

## Workflows

### Discovering Events

1. Open debug panel
2. Go to Event Handlers tab
3. See all available events and their parameters
4. Copy parameter names for your template

### Debugging Parameter Issues

1. Trigger event from UI
2. Go to Event History tab
3. See parameters sent
4. Check for validation errors
5. Compare with handler signature in Handlers tab

### Performance Tuning

1. Go to VDOM Patches tab
2. Trigger updates
3. Check patch count (fewer = better)
4. Optimize handlers to minimize changes
```

#### Update `CLAUDE.md` (add section)

```markdown
## Developer Tooling

### Event Parameter Validation

djust validates event handler signatures at runtime:
- Checks required parameters are provided
- Rejects unexpected parameters
- Validates types against type hints
- Clear error messages with expected vs provided params

**Always use `value` for form events:**
```python
@event_handler
def search(self, value: str = "", **kwargs):  # ✅ Correct
    pass
```

### @event_handler Decorator

Mark event handlers for:
- Automatic parameter introspection
- Debug panel discovery
- IDE autocomplete support

```python
@event_handler
def my_handler(self, value: str):
    """Description shown in debug panel"""
    pass
```

### Debug Panel

Interactive debugging tool (DEBUG mode only):
- **Open**: `Ctrl+Shift+D` or click floating button
- **Features**: Event handlers, event history, VDOM patches, variables
- **See**: `docs/DEBUG_PANEL.md` for full guide

### Public/Private Variables

**Convention:**
- `_private` - Not in template context
- `public` - Auto-exposed to templates

Used extensively in JIT serialization pattern.
```

---

## Implementation Order

**Recommended sequence:**

1. ✅ **Phase 1** - Parameter validation (2-3 hours)
   - Fundamental - catches bugs immediately
   - Required for good error messages
   - Independent of other phases

2. ✅ **Phase 2** - Enhanced decorator (1-2 hours)
   - Enables debug panel
   - Improves IDE experience
   - Small, focused change

3. ✅ **Phase 3** - Debug panel (4-6 hours)
   - Most visible feature
   - Builds on Phase 1 & 2
   - Significant UX improvement

4. ✅ **Phase 4** - Documentation (2-3 hours)
   - Explains everything
   - Examples and best practices
   - Can be iterative

**Total estimated time: 9-14 hours**

---

## Breaking Changes

**None!** All changes are backward compatible:

- ✅ Validation only triggers on errors (working code unaffected)
- ✅ `@event_handler` works with or without arguments
- ✅ Debug panel only shows in DEBUG mode
- ✅ Public/private convention already used throughout codebase

---

## Success Metrics

After implementation, developers should:

✅ Get immediate feedback on parameter mismatches
✅ Discover events without reading code
✅ Understand expected parameters from debug panel
✅ Debug event flow with visual tools
✅ Write event handlers 50% faster (less trial-and-error)
✅ Reduce parameter-related bugs by 90%

---

## Future Enhancements (Out of Scope)

Ideas for later:
- Event replay functionality (record → replay interactions)
- Performance profiler (event timing, query counts)
- Type generation for TypeScript (export handler signatures)
- Rate limiting decorator (`@rate_limit(per_minute=10)`)
- Permission checking (`@require_permission("can_edit")`)
- WebSocket protocol inspector (see raw messages)

---

## References

- Research findings: See research agent output above
- Existing patterns: `python/djust/decorators.py` for decorator examples
- VDOM debug: `LIVEVIEW_CONFIG['debug_vdom']` for VDOM logging
- Similar frameworks: Phoenix LiveView, Laravel Livewire debug tools

---

**Next Steps:**
1. Review this plan
2. Confirm implementation approach
3. Begin Phase 1 (parameter validation)
4. Iterate with feedback
