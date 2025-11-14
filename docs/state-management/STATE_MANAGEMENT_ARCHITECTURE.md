# State Management Architecture

**Status**: Specification
**Target**: djust 0.4.0
**Implementation Timeline**: 10-12 weeks

This document describes the architecture for Python-only state management decorators in djust. It serves as the implementation specification for contributors.

## Table of Contents

- [Overview](#overview)
- [Design Principles](#design-principles)
- [High-Level Architecture](#high-level-architecture)
- [Backend Architecture](#backend-architecture)
- [Frontend Architecture](#frontend-architecture)
- [Wire Protocol](#wire-protocol)
- [Component Architecture](#component-architecture)
- [Performance Optimizations](#performance-optimizations)
- [Testing Strategy](#testing-strategy)
- [Implementation Phases](#implementation-phases)

## Overview

### Problem Statement

Current djust state management requires manual JavaScript for common patterns:

```javascript
// Current approach: 889 lines of manual JavaScript
let debounceTimer = null;
const searchInput = document.getElementById('search');
searchInput.addEventListener('input', (e) => {
  clearTimeout(debounceTimer);
  debounceTimer = setTimeout(() => {
    sendEvent('search', { query: e.target.value });
  }, 500);
});
```

### Solution

Python-only decorators that eliminate JavaScript:

```python
# New approach: Pure Python
from djust.decorators import debounce

class SearchView(LiveView):
    @debounce(wait=0.5)
    def search(self, query: str = "", **kwargs):
        self.results = Product.objects.filter(name__icontains=query)
```

### Goals

1. **Zero JavaScript**: Developers write only Python
2. **Automatic Optimization**: Debouncing, throttling, caching happen automatically
3. **Instant Feedback**: Optimistic updates before server validation
4. **Coordinated State**: Components share state via StateBus
5. **Persistent Drafts**: Forms auto-save to localStorage
6. **Bundle Size**: Keep client.js under 10KB (currently 5KB → 7.1KB)

## Design Principles

### 1. Python-First API

```python
# ✅ Good: Declarative Python decorator
@debounce(wait=0.5)
def search(self, query: str = "", **kwargs):
    pass

# ❌ Bad: Imperative JavaScript required
def search(self, query: str = "", **kwargs):
    # Developer writes debouncing logic in JS
    pass
```

### 2. Convention Over Configuration

```python
# ✅ Good: Sensible defaults
@debounce()  # Defaults to 300ms
def search(self, query: str = "", **kwargs):
    pass

# ✅ Good: Explicit when needed
@debounce(wait=0.5, max_wait=2.0)
def search(self, query: str = "", **kwargs):
    pass
```

### 3. Composability

```python
# ✅ Good: Decorators compose cleanly
@debounce(wait=0.5)
@optimistic
@cache(ttl=60)
def search(self, query: str = "", **kwargs):
    pass
```

### 4. Progressive Enhancement

```python
# ✅ Good: Works without decorators
def search(self, query: str = "", **kwargs):
    self.results = Product.objects.filter(name__icontains=query)

# ✅ Better: Add debouncing
@debounce(wait=0.5)
def search(self, query: str = "", **kwargs):
    self.results = Product.objects.filter(name__icontains=query)

# ✅ Best: Add optimistic updates
@debounce(wait=0.5)
@optimistic
def search(self, query: str = "", **kwargs):
    self.results = Product.objects.filter(name__icontains=query)
```

## High-Level Architecture

### System Overview

```
┌─────────────────────────────────────────────────────────────┐
│ Browser                                                     │
│                                                             │
│  ┌──────────────────────────────────────────────────────┐  │
│  │ User Interaction                                     │  │
│  │  - Click button                                      │  │
│  │  - Type in input                                     │  │
│  │  - Submit form                                       │  │
│  └──────────────────────────────────────────────────────┘  │
│                          ↓                                  │
│  ┌──────────────────────────────────────────────────────┐  │
│  │ Event Handlers (client.js)                          │  │
│  │  - handleEvent()                                     │  │
│  │  - Extract event data                                │  │
│  │  - Check handler metadata                            │  │
│  └──────────────────────────────────────────────────────┘  │
│                          ↓                                  │
│  ┌──────────────────────────────────────────────────────┐  │
│  │ Decorator Interceptors                               │  │
│  │  ┌────────────┐  ┌────────────┐  ┌────────────┐     │  │
│  │  │ @optimistic│  │ @debounce  │  │ @cache     │     │  │
│  │  │ Update DOM │  │ Delay send │  │ Return     │     │  │
│  │  │ instantly  │  │ 500ms      │  │ cached     │     │  │
│  │  └────────────┘  └────────────┘  └────────────┘     │  │
│  │  ┌────────────┐  ┌────────────┐                      │  │
│  │  │ @throttle  │  │ @client_   │                      │  │
│  │  │ Limit rate │  │  state     │                      │  │
│  │  │ 1/sec      │  │ StateBus   │                      │  │
│  │  └────────────┘  └────────────┘                      │  │
│  └──────────────────────────────────────────────────────┘  │
│                          ↓                                  │
│  ┌──────────────────────────────────────────────────────┐  │
│  │ WebSocket Send                                       │  │
│  │  { event: "search", data: { query: "laptop" } }      │  │
│  └──────────────────────────────────────────────────────┘  │
└─────────────────────────────────────────────────────────────┘
                             ↓ WebSocket
┌─────────────────────────────────────────────────────────────┐
│ Django + Channels (Python)                                  │
│                                                             │
│  ┌──────────────────────────────────────────────────────┐  │
│  │ LiveViewConsumer                                     │  │
│  │  - Receive WebSocket message                         │  │
│  │  - Extract event name and data                       │  │
│  │  - Route to handler                                  │  │
│  └──────────────────────────────────────────────────────┘  │
│                          ↓                                  │
│  ┌──────────────────────────────────────────────────────┐  │
│  │ LiveView Event Handler                               │  │
│  │                                                       │  │
│  │  @debounce(wait=0.5)                                 │  │
│  │  @optimistic                                          │  │
│  │  def search(self, query: str = "", **kwargs):        │  │
│  │      self.results = Product.objects.filter(...)      │  │
│  │                                                       │  │
│  │  Decorator metadata:                                 │  │
│  │  {                                                    │  │
│  │    "debounce": {"wait": 0.5, "max_wait": null},      │  │
│  │    "optimistic": true                                 │  │
│  │  }                                                    │  │
│  └──────────────────────────────────────────────────────┘  │
│                          ↓                                  │
│  ┌──────────────────────────────────────────────────────┐  │
│  │ Render Template (get_context_data)                   │  │
│  │  - Build context dict                                │  │
│  │  - Pass to Rust template engine                      │  │
│  └──────────────────────────────────────────────────────┘  │
└─────────────────────────────────────────────────────────────┘
                             ↓ PyO3 FFI
┌─────────────────────────────────────────────────────────────┐
│ Rust Core                                                   │
│                                                             │
│  ┌──────────────────────────────────────────────────────┐  │
│  │ Template Engine (djust_templates)                    │  │
│  │  - Parse template (<1ms)                             │  │
│  │  - Render with context                               │  │
│  │  - Generate HTML string                              │  │
│  └──────────────────────────────────────────────────────┘  │
│                          ↓                                  │
│  ┌──────────────────────────────────────────────────────┐  │
│  │ VDOM Diffing (djust_vdom)                            │  │
│  │  - Old VDOM tree                                     │  │
│  │  - New VDOM tree                                     │  │
│  │  - Generate minimal patches (<100μs)                 │  │
│  │  - Preserve form values                              │  │
│  └──────────────────────────────────────────────────────┘  │
│                          ↓                                  │
│  ┌──────────────────────────────────────────────────────┐  │
│  │ Patch Serialization                                  │  │
│  │  [                                                    │  │
│  │    {                                                  │  │
│  │      "type": "ReplaceText",                           │  │
│  │      "path": [0, 2, 1],                               │  │
│  │      "text": "Found 42 products"                      │  │
│  │    }                                                  │  │
│  │  ]                                                    │  │
│  └──────────────────────────────────────────────────────┘  │
└─────────────────────────────────────────────────────────────┘
                             ↓ PyO3 FFI
┌─────────────────────────────────────────────────────────────┐
│ Django + Channels (Python)                                  │
│                                                             │
│  ┌──────────────────────────────────────────────────────┐  │
│  │ Send Response                                        │  │
│  │  {                                                    │  │
│  │    "patches": [...],                                  │  │
│  │    "handlers": {                                      │  │
│  │      "search": {                                      │  │
│  │        "debounce": {"wait": 0.5},                     │  │
│  │        "optimistic": true                             │  │
│  │      }                                                 │  │
│  │    }                                                   │  │
│  │  }                                                     │  │
│  └──────────────────────────────────────────────────────┘  │
└─────────────────────────────────────────────────────────────┘
                             ↓ WebSocket
┌─────────────────────────────────────────────────────────────┐
│ Browser                                                     │
│                                                             │
│  ┌──────────────────────────────────────────────────────┐  │
│  │ Apply Patches (morphdom)                             │  │
│  │  - Update DOM nodes                                  │  │
│  │  - Preserve form values                              │  │
│  │  - Maintain focus                                    │  │
│  └──────────────────────────────────────────────────────┘  │
│                          ↓                                  │
│  ┌──────────────────────────────────────────────────────┐  │
│  │ Update Handler Metadata                              │  │
│  │  window.handlerMetadata["search"] = {                │  │
│  │    debounce: { wait: 500, maxWait: null },           │  │
│  │    optimistic: true                                   │  │
│  │  };                                                   │  │
│  └──────────────────────────────────────────────────────┘  │
└─────────────────────────────────────────────────────────────┘
```

### Data Flow

**Initial Page Load:**

1. Browser requests `/search/`
2. Django renders full HTML with initial state
3. Client.js connects WebSocket
4. Server sends handler metadata in initial response
5. Client stores metadata for event interception

**User Interaction:**

1. User types "laptop" in search box
2. Client intercepts `@input="search"` event
3. Client checks `window.handlerMetadata["search"]`
4. Client applies decorators:
   - `@optimistic`: Update DOM instantly (no server round-trip)
   - `@debounce(500ms)`: Delay WebSocket send by 500ms
5. After 500ms silence, send WebSocket message
6. Server processes search, renders template, diffs VDOM
7. Server sends patches + updated metadata
8. Client applies patches to DOM

## Backend Architecture

### Python Layer (`python/djust/`)

#### 1. Decorator Implementation (`decorators.py`)

```python
# python/djust/decorators.py
from functools import wraps
from typing import Callable, Optional, List

def debounce(wait: float = 0.3, max_wait: Optional[float] = None):
    """
    Debounce event handler execution.

    Implementation:
    1. Attach metadata to handler function
    2. Metadata extracted during mount/render
    3. Sent to client in handler metadata
    4. Client intercepts events and debounces
    """
    def decorator(func: Callable) -> Callable:
        @wraps(func)
        def wrapper(*args, **kwargs):
            return func(*args, **kwargs)

        # Attach metadata
        if not hasattr(wrapper, '_djust_decorators'):
            wrapper._djust_decorators = {}

        wrapper._djust_decorators['debounce'] = {
            'wait': wait,
            'max_wait': max_wait
        }

        return wrapper
    return decorator

def throttle(interval: float = 0.3, leading: bool = True, trailing: bool = True):
    """Throttle event handler execution."""
    def decorator(func: Callable) -> Callable:
        @wraps(func)
        def wrapper(*args, **kwargs):
            return func(*args, **kwargs)

        if not hasattr(wrapper, '_djust_decorators'):
            wrapper._djust_decorators = {}

        wrapper._djust_decorators['throttle'] = {
            'interval': interval,
            'leading': leading,
            'trailing': trailing
        }

        return wrapper
    return decorator

def optimistic(func: Callable) -> Callable:
    """Apply optimistic updates before server validation."""
    @wraps(func)
    def wrapper(*args, **kwargs):
        return func(*args, **kwargs)

    if not hasattr(wrapper, '_djust_decorators'):
        wrapper._djust_decorators = {}

    wrapper._djust_decorators['optimistic'] = True

    return wrapper

def client_state(keys: List[str]):
    """Share state via client-side StateBus."""
    def decorator(func: Callable) -> Callable:
        @wraps(func)
        def wrapper(*args, **kwargs):
            return func(*args, **kwargs)

        if not hasattr(wrapper, '_djust_decorators'):
            wrapper._djust_decorators = {}

        wrapper._djust_decorators['client_state'] = {
            'keys': keys
        }

        return wrapper
    return decorator

def cache(ttl: int = 60, key_params: Optional[List[str]] = None):
    """Cache handler responses client-side."""
    def decorator(func: Callable) -> Callable:
        @wraps(func)
        def wrapper(*args, **kwargs):
            return func(*args, **kwargs)

        if not hasattr(wrapper, '_djust_decorators'):
            wrapper._djust_decorators = {}

        wrapper._djust_decorators['cache'] = {
            'ttl': ttl,
            'key_params': key_params or []
        }

        return wrapper
    return decorator
```

#### 2. LiveView Integration (`live_view.py`)

```python
# python/djust/live_view.py

class LiveView:
    def _extract_handler_metadata(self) -> dict:
        """
        Extract decorator metadata from all event handlers.

        Returns:
            {
                "search": {
                    "debounce": {"wait": 0.5, "max_wait": null},
                    "optimistic": true
                },
                "update_slider": {
                    "throttle": {"interval": 0.1, "leading": true, "trailing": true}
                }
            }
        """
        metadata = {}

        # Iterate all methods
        for name in dir(self):
            if name.startswith('_'):
                continue

            method = getattr(self, name)
            if not callable(method):
                continue

            # Check for decorator metadata
            if hasattr(method, '_djust_decorators'):
                metadata[name] = method._djust_decorators

        return metadata

    def render(self) -> str:
        """
        Render LiveView and include handler metadata.

        Returns HTML with embedded metadata:
        <script>
        window.handlerMetadata = {
            "search": {"debounce": {"wait": 0.5}},
            ...
        };
        </script>
        """
        # Get context
        context = self.get_context_data()

        # Render template via Rust
        html = self._rust_view.render(self.template_name, context)

        # Extract handler metadata
        metadata = self._extract_handler_metadata()

        # Inject metadata script
        if metadata:
            script = f"""
<script>
window.handlerMetadata = {json.dumps(metadata)};
</script>
"""
            # Insert before </body>
            html = html.replace('</body>', f'{script}</body>')

        return html

    def handle_event(self, event: str, data: dict) -> dict:
        """
        Handle WebSocket event.

        Returns:
            {
                "patches": [...],
                "handlers": {...}  # Updated metadata
            }
        """
        # Call handler
        handler = getattr(self, event)
        handler(**data)

        # Re-render
        new_html = self.render()

        # Diff VDOM
        patches = self._rust_view.diff(new_html)

        # Extract metadata (may have changed)
        metadata = self._extract_handler_metadata()

        return {
            'patches': patches,
            'handlers': metadata
        }
```

#### 3. DraftMode Mixin (`mixins.py`)

```python
# python/djust/mixins.py

class DraftModeMixin:
    """
    Auto-save form drafts to localStorage.

    Usage:
        class ContactFormView(DraftModeMixin, FormMixin, LiveView):
            form_class = ContactForm
            draft_key = "contact_form"  # localStorage key
            draft_ttl = 3600  # 1 hour
    """
    draft_key: str = "form_draft"
    draft_ttl: int = 3600  # seconds

    def _extract_handler_metadata(self) -> dict:
        """Add draft_mode metadata to form handlers."""
        metadata = super()._extract_handler_metadata()

        # Add draft_mode metadata
        metadata['_draft_mode'] = {
            'enabled': True,
            'key': self.draft_key,
            'ttl': self.draft_ttl
        }

        return metadata
```

### Rust Layer (`crates/djust/`)

No changes required to Rust core for decorator implementation. All logic happens in:
1. Python decorators (metadata attachment)
2. Python LiveView (metadata extraction)
3. JavaScript client (metadata consumption)

Rust VDOM diffing remains unchanged - continues to generate minimal patches.

## Frontend Architecture

### Client.js Module Structure

```javascript
// python/djust/static/djust/client.js

// ============================================================================
// Global State
// ============================================================================

// Handler metadata storage
window.handlerMetadata = window.handlerMetadata || {};

// Debounce timers
const debounceTimers = new Map(); // Map<handlerName, timerId>

// Throttle state
const throttleState = new Map(); // Map<handlerName, {lastCall, timeoutId}>

// StateBus for @client_state
const stateBus = new Map(); // Map<key, value>
const stateBusSubscribers = new Map(); // Map<key, Set<handlerName>>

// Cache for @cache
const responseCache = new Map(); // Map<cacheKey, {response, expires}>

// DraftMode storage
const draftTimers = new Map(); // Map<formKey, timerId>

// ============================================================================
// Core Event Handling
// ============================================================================

function handleEvent(eventName, eventData = {}) {
  const metadata = window.handlerMetadata[eventName];

  // Check cache first
  if (metadata?.cache) {
    const cached = checkCache(eventName, eventData, metadata.cache);
    if (cached) {
      applyPatches(cached.patches);
      return;
    }
  }

  // Apply optimistic updates
  if (metadata?.optimistic) {
    applyOptimisticUpdate(eventName, eventData);
  }

  // Apply debounce
  if (metadata?.debounce) {
    debounceEvent(eventName, eventData, metadata.debounce);
    return;
  }

  // Apply throttle
  if (metadata?.throttle) {
    throttleEvent(eventName, eventData, metadata.throttle);
    return;
  }

  // Send immediately
  sendEvent(eventName, eventData);
}

function sendEvent(eventName, eventData) {
  // Update StateBus if @client_state
  const metadata = window.handlerMetadata[eventName];
  if (metadata?.client_state) {
    updateStateBus(metadata.client_state.keys, eventData);
  }

  // Send WebSocket message
  if (socket.readyState === WebSocket.OPEN) {
    socket.send(JSON.stringify({
      event: eventName,
      data: eventData
    }));
  }
}

// ============================================================================
// Debounce Implementation
// ============================================================================

function debounceEvent(eventName, eventData, config) {
  const { wait, max_wait } = config;

  // Clear existing timer
  if (debounceTimers.has(eventName)) {
    clearTimeout(debounceTimers.get(eventName));
  }

  // Set new timer
  const timerId = setTimeout(() => {
    sendEvent(eventName, eventData);
    debounceTimers.delete(eventName);
  }, wait * 1000);

  debounceTimers.set(eventName, timerId);

  // TODO: Implement max_wait
}

// ============================================================================
// Throttle Implementation
// ============================================================================

function throttleEvent(eventName, eventData, config) {
  const { interval, leading, trailing } = config;
  const now = Date.now();

  if (!throttleState.has(eventName)) {
    // First call
    if (leading) {
      sendEvent(eventName, eventData);
    }

    throttleState.set(eventName, {
      lastCall: now,
      timeoutId: null
    });

    if (trailing) {
      const timerId = setTimeout(() => {
        sendEvent(eventName, eventData);
        throttleState.delete(eventName);
      }, interval * 1000);

      throttleState.get(eventName).timeoutId = timerId;
    }

    return;
  }

  const state = throttleState.get(eventName);
  const elapsed = now - state.lastCall;

  if (elapsed >= interval * 1000) {
    // Enough time passed
    sendEvent(eventName, eventData);
    state.lastCall = now;
  } else if (trailing) {
    // Schedule trailing call
    if (state.timeoutId) {
      clearTimeout(state.timeoutId);
    }

    state.timeoutId = setTimeout(() => {
      sendEvent(eventName, eventData);
      throttleState.delete(eventName);
    }, (interval * 1000) - elapsed);
  }
}

// ============================================================================
// Optimistic Updates
// ============================================================================

function applyOptimisticUpdate(eventName, eventData) {
  // Apply instant DOM updates based on event data
  // This is heuristic-based - may not always be accurate

  // Example: Update input value immediately
  if (eventData.value !== undefined) {
    const input = document.querySelector(`[data-event="${eventName}"]`);
    if (input && input.tagName === 'INPUT') {
      input.value = eventData.value;
    }
  }

  // Example: Update checkbox state
  if (eventData.checked !== undefined) {
    const checkbox = document.querySelector(`[data-event="${eventName}"]`);
    if (checkbox && checkbox.type === 'checkbox') {
      checkbox.checked = eventData.checked;
    }
  }

  // Server will send corrective patches if needed
}

// ============================================================================
// Client State (StateBus)
// ============================================================================

function updateStateBus(keys, eventData) {
  keys.forEach(key => {
    if (eventData[key] !== undefined) {
      stateBus.set(key, eventData[key]);

      // Notify subscribers
      if (stateBusSubscribers.has(key)) {
        stateBusSubscribers.get(key).forEach(handlerName => {
          // Trigger subscribed handler
          handleEvent(handlerName, { [key]: eventData[key] });
        });
      }
    }
  });
}

function subscribeStateBus(key, handlerName) {
  if (!stateBusSubscribers.has(key)) {
    stateBusSubscribers.set(key, new Set());
  }
  stateBusSubscribers.get(key).add(handlerName);
}

// ============================================================================
// Response Caching
// ============================================================================

function checkCache(eventName, eventData, config) {
  const { ttl, key_params } = config;

  // Build cache key
  let cacheKey = eventName;
  if (key_params.length > 0) {
    const keyValues = key_params.map(p => eventData[p] || '').join(':');
    cacheKey = `${eventName}:${keyValues}`;
  }

  // Check cache
  if (responseCache.has(cacheKey)) {
    const cached = responseCache.get(cacheKey);
    if (Date.now() < cached.expires) {
      console.log(`[Cache] Hit: ${cacheKey}`);
      return cached;
    } else {
      console.log(`[Cache] Expired: ${cacheKey}`);
      responseCache.delete(cacheKey);
    }
  }

  return null;
}

function cacheResponse(eventName, eventData, response, ttl) {
  const metadata = window.handlerMetadata[eventName];
  if (!metadata?.cache) return;

  const { key_params } = metadata.cache;

  // Build cache key
  let cacheKey = eventName;
  if (key_params.length > 0) {
    const keyValues = key_params.map(p => eventData[p] || '').join(':');
    cacheKey = `${eventName}:${keyValues}`;
  }

  // Store in cache
  responseCache.set(cacheKey, {
    response: response,
    expires: Date.now() + (ttl * 1000)
  });

  console.log(`[Cache] Stored: ${cacheKey} (TTL: ${ttl}s)`);
}

// ============================================================================
// Draft Mode
// ============================================================================

function initDraftMode(formElement, config) {
  const { key, ttl } = config;

  // Load draft from localStorage
  const draft = localStorage.getItem(key);
  if (draft) {
    try {
      const data = JSON.parse(draft);
      const age = Date.now() - data.timestamp;

      if (age < ttl * 1000) {
        // Restore draft
        Object.entries(data.values).forEach(([name, value]) => {
          const input = formElement.querySelector(`[name="${name}"]`);
          if (input) {
            input.value = value;
          }
        });

        console.log(`[DraftMode] Restored draft: ${key} (${Math.round(age/1000)}s old)`);
      } else {
        // Expired
        localStorage.removeItem(key);
        console.log(`[DraftMode] Draft expired: ${key}`);
      }
    } catch (e) {
      console.error('[DraftMode] Failed to parse draft:', e);
    }
  }

  // Auto-save on input
  formElement.addEventListener('input', (e) => {
    saveDraft(formElement, key);
  });

  // Clear draft on successful submit
  formElement.addEventListener('submit', (e) => {
    localStorage.removeItem(key);
    console.log(`[DraftMode] Cleared draft: ${key}`);
  });
}

function saveDraft(formElement, key) {
  // Debounce saves (1 second)
  if (draftTimers.has(key)) {
    clearTimeout(draftTimers.get(key));
  }

  const timerId = setTimeout(() => {
    const formData = new FormData(formElement);
    const values = {};

    formData.forEach((value, name) => {
      values[name] = value;
    });

    const draft = {
      timestamp: Date.now(),
      values: values
    };

    localStorage.setItem(key, JSON.stringify(draft));
    console.log(`[DraftMode] Saved draft: ${key}`);

    draftTimers.delete(key);
  }, 1000);

  draftTimers.set(key, timerId);
}

// ============================================================================
// Loading States
// ============================================================================

function showLoadingState(element) {
  // Add loading class
  element.classList.add('loading');

  // Replace text if @loading-text
  if (element.hasAttribute('@loading-text')) {
    element.dataset.originalText = element.textContent;
    element.textContent = element.getAttribute('@loading-text');
  }

  // Disable element
  element.disabled = true;
}

function hideLoadingState(element) {
  element.classList.remove('loading');

  // Restore original text
  if (element.dataset.originalText) {
    element.textContent = element.dataset.originalText;
    delete element.dataset.originalText;
  }

  element.disabled = false;
}

// ============================================================================
// Initialization
// ============================================================================

document.addEventListener('DOMContentLoaded', () => {
  // Initialize DraftMode for forms
  if (window.handlerMetadata._draft_mode?.enabled) {
    const config = window.handlerMetadata._draft_mode;
    const forms = document.querySelectorAll('form');
    forms.forEach(form => {
      initDraftMode(form, config);
    });
  }

  // Set up event listeners
  document.addEventListener('click', (e) => {
    const target = e.target.closest('[\\@click]');
    if (target) {
      const eventName = target.getAttribute('@click');
      handleEvent(eventName, {});
    }
  });

  document.addEventListener('input', (e) => {
    const target = e.target;
    if (target.hasAttribute('@input')) {
      const eventName = target.getAttribute('@input');
      handleEvent(eventName, { value: target.value });
    }
  });

  // ... more event listeners
});

// Expose for manual event triggering
window.sendEvent = handleEvent;
```

### Bundle Size Breakdown

```
Current client.js: 5.0 KB (gzipped)

After state management features:
├── Core (existing):              3.0 KB
├── Debounce/Throttle:           +0.8 KB
├── Optimistic updates:          +0.5 KB
├── StateBus:                    +0.6 KB
├── Response caching:            +0.7 KB
├── DraftMode:                   +0.9 KB
└── Loading states:              +0.3 KB
                                 ─────────
Total:                            7.1 KB (gzipped)

Still smaller than:
- Phoenix LiveView: ~30 KB
- Laravel Livewire: ~50 KB
```

## Wire Protocol

### Current Protocol

**Client → Server:**
```json
{
  "event": "search",
  "data": {
    "query": "laptop"
  }
}
```

**Server → Client:**
```json
{
  "patches": [
    {
      "type": "ReplaceText",
      "path": [0, 2, 1],
      "text": "Found 42 products"
    }
  ]
}
```

### Enhanced Protocol (with metadata)

**Server → Client (initial render):**
```json
{
  "html": "<html>...",
  "handlers": {
    "search": {
      "debounce": {
        "wait": 0.5,
        "max_wait": null
      },
      "optimistic": true,
      "cache": {
        "ttl": 60,
        "key_params": ["query"]
      }
    },
    "update_slider": {
      "throttle": {
        "interval": 0.1,
        "leading": true,
        "trailing": true
      }
    }
  },
  "draft_mode": {
    "enabled": true,
    "key": "contact_form",
    "ttl": 3600
  }
}
```

**Server → Client (event response):**
```json
{
  "patches": [
    {
      "type": "ReplaceText",
      "path": [0, 2, 1],
      "text": "Found 42 products"
    }
  ],
  "handlers": {
    "search": {
      "debounce": {"wait": 0.5},
      "cache": {"ttl": 60, "key_params": ["query"]}
    }
  }
}
```

**Metadata Updates:**
- Metadata sent on initial render (embedded in HTML script tag)
- Metadata updated on every event response (if changed)
- Client merges new metadata with existing

**Backward Compatibility:**
- If `handlers` key missing, client treats as non-decorated
- Old clients ignore `handlers` key
- Graceful degradation

## Component Architecture

### Overview

State management decorators are implemented as **interceptors** in the client-side event pipeline:

```
User Event → handleEvent() → Interceptors → sendEvent() → WebSocket
                               ↓
                          ┌────────────┐
                          │ @cache?    │ → Return cached
                          └────────────┘
                               ↓
                          ┌────────────┐
                          │ @optimistic│ → Update DOM
                          └────────────┘
                               ↓
                          ┌────────────┐
                          │ @debounce? │ → Delay send
                          └────────────┘
                               ↓
                          ┌────────────┐
                          │ @throttle? │ → Limit rate
                          └────────────┘
```

### Component Responsibilities

#### 1. Debounce Manager

**File**: `client.js` (debounceEvent function)
**Responsibility**: Delay handler execution until user stops interacting

**State:**
```javascript
const debounceTimers = new Map(); // Map<handlerName, timerId>
```

**Algorithm:**
1. On event: Clear existing timer (if any)
2. Start new timer for `wait` milliseconds
3. If timer expires: Send event
4. If user triggers again: Reset timer (restart)

**Edge Cases:**
- Multiple events for same handler: Only latest survives
- `max_wait`: Force send after max_wait even if user still interacting
- Component unmount: Clear all timers

#### 2. Throttle Manager

**File**: `client.js` (throttleEvent function)
**Responsibility**: Limit handler execution frequency

**State:**
```javascript
const throttleState = new Map();
// Map<handlerName, {lastCall: timestamp, timeoutId: number}>
```

**Algorithm:**
1. First call: Send immediately if `leading=true`
2. Subsequent calls within interval: Ignore or schedule trailing
3. After interval: Reset state

**Edge Cases:**
- `leading=false`: Skip first call
- `trailing=false`: Skip last call
- Both false: No calls (invalid)

#### 3. StateBus

**File**: `client.js` (updateStateBus, subscribeStateBus)
**Responsibility**: Client-side pub/sub for component coordination

**State:**
```javascript
const stateBus = new Map(); // Map<key, value>
const stateBusSubscribers = new Map(); // Map<key, Set<handlerName>>
```

**Algorithm:**
1. Handler publishes: `@client_state(keys=["filter"])`
2. On event: Update stateBus with new values
3. Notify all subscribers with key changes
4. Subscribers trigger their handlers

**Example:**
```python
# ComponentA publishes filter
@client_state(keys=["filter"])
def update_filter(self, filter: str = "", **kwargs):
    self.filter = filter

# ComponentB subscribes to filter
@client_state(keys=["filter"])  # Auto-subscribes
def on_filter_change(self, filter: str = "", **kwargs):
    self.filtered_items = [i for i in self.items if filter in i['name']]
```

#### 4. Response Cache

**File**: `client.js` (checkCache, cacheResponse)
**Responsibility**: Cache handler responses to avoid redundant server requests

**State:**
```javascript
const responseCache = new Map();
// Map<cacheKey, {response: object, expires: timestamp}>
```

**Algorithm:**
1. Before send: Check cache with key
2. If hit and not expired: Return cached response
3. After server response: Store in cache with TTL
4. On expiry: Remove from cache

**Cache Key Generation:**
```javascript
// @cache(ttl=60, key_params=["query", "page"])
// Event: search(query="laptop", page=2)
// Key: "search:laptop:2"

function buildCacheKey(eventName, eventData, keyParams) {
  if (keyParams.length === 0) return eventName;
  const values = keyParams.map(p => eventData[p] || '').join(':');
  return `${eventName}:${values}`;
}
```

#### 5. DraftMode

**File**: `client.js` (initDraftMode, saveDraft)
**Responsibility**: Auto-save form drafts to localStorage

**State:**
```javascript
const draftTimers = new Map(); // Map<formKey, timerId>
```

**Algorithm:**
1. On page load: Check localStorage for draft
2. If found and not expired: Restore form values
3. On form input: Debounced save to localStorage (1 second)
4. On successful submit: Clear draft

**localStorage Format:**
```json
{
  "contact_form": {
    "timestamp": 1699564800000,
    "values": {
      "name": "John Doe",
      "email": "john@example.com",
      "message": "Hello..."
    }
  }
}
```

## Performance Optimizations

### 1. Minimize Metadata Size

**Problem**: Sending full metadata on every response increases payload size

**Solution**: Only send metadata delta (what changed)

```python
class LiveView:
    def __init__(self):
        self._last_metadata = {}

    def handle_event(self, event: str, data: dict) -> dict:
        # ... handle event

        # Get current metadata
        metadata = self._extract_handler_metadata()

        # Calculate delta
        delta = {}
        for handler, meta in metadata.items():
            if handler not in self._last_metadata or self._last_metadata[handler] != meta:
                delta[handler] = meta

        self._last_metadata = metadata

        return {
            'patches': patches,
            'handlers': delta  # Only what changed
        }
```

### 2. Batch WebSocket Messages

**Problem**: Rapid events flood WebSocket with messages

**Solution**: Batch events within 16ms (one frame)

```javascript
let eventQueue = [];
let batchTimer = null;

function handleEvent(eventName, eventData) {
  // Add to queue
  eventQueue.push({ event: eventName, data: eventData });

  // Schedule batch send
  if (!batchTimer) {
    batchTimer = setTimeout(() => {
      sendBatch(eventQueue);
      eventQueue = [];
      batchTimer = null;
    }, 16); // One frame
  }
}

function sendBatch(events) {
  socket.send(JSON.stringify({
    type: 'batch',
    events: events
  }));
}
```

### 3. Lazy Load Decorators

**Problem**: Loading all decorator code increases bundle size

**Solution**: Load decorators on demand

```javascript
// Core client.js (always loaded)
async function handleEvent(eventName, eventData) {
  const metadata = window.handlerMetadata[eventName];

  // Lazy load debounce module
  if (metadata?.debounce && !window.djustDebounce) {
    const module = await import('./decorators/debounce.js');
    window.djustDebounce = module.debounce;
  }

  if (window.djustDebounce) {
    window.djustDebounce(eventName, eventData, metadata.debounce);
    return;
  }

  sendEvent(eventName, eventData);
}
```

**Bundle Size:**
- Core: 3.0 KB (always loaded)
- Decorators: 4.1 KB (loaded on demand)
- Total: 7.1 KB (but split for faster initial load)

### 4. Rust VDOM Optimizations

**Current**: VDOM diffing in Rust (~100μs)
**No changes needed**: Already optimized

## Testing Strategy

### Python Tests

#### 1. Decorator Metadata Extraction

```python
# tests/test_decorators.py
import pytest
from djust import LiveView
from djust.decorators import debounce, throttle, optimistic

def test_debounce_metadata():
    class TestView(LiveView):
        @debounce(wait=0.5)
        def search(self, query: str = "", **kwargs):
            pass

    view = TestView()
    metadata = view._extract_handler_metadata()

    assert 'search' in metadata
    assert metadata['search']['debounce'] == {'wait': 0.5, 'max_wait': None}

def test_multiple_decorators():
    class TestView(LiveView):
        @debounce(wait=0.5)
        @optimistic
        def search(self, query: str = "", **kwargs):
            pass

    view = TestView()
    metadata = view._extract_handler_metadata()

    assert metadata['search']['debounce'] == {'wait': 0.5, 'max_wait': None}
    assert metadata['search']['optimistic'] is True
```

#### 2. Metadata Rendering

```python
# tests/test_live_view.py
def test_metadata_injection():
    class TestView(LiveView):
        template_string = "<div>{{ count }}</div>"

        @debounce(wait=0.5)
        def increment(self, **kwargs):
            self.count += 1

    view = TestView()
    html = view.render()

    assert 'window.handlerMetadata' in html
    assert '"increment"' in html
    assert '"debounce"' in html
```

### JavaScript Tests

#### 1. Debounce Behavior

```javascript
// tests/client.test.js
describe('Debounce', () => {
  beforeEach(() => {
    jest.useFakeTimers();
    debounceTimers.clear();
  });

  test('delays event send', () => {
    const sendSpy = jest.spyOn(window, 'sendEvent');

    window.handlerMetadata['search'] = {
      debounce: { wait: 0.5, max_wait: null }
    };

    handleEvent('search', { query: 'a' });
    expect(sendSpy).not.toHaveBeenCalled();

    jest.advanceTimersByTime(500);
    expect(sendSpy).toHaveBeenCalledWith('search', { query: 'a' });
  });

  test('resets timer on subsequent events', () => {
    const sendSpy = jest.spyOn(window, 'sendEvent');

    window.handlerMetadata['search'] = {
      debounce: { wait: 0.5 }
    };

    handleEvent('search', { query: 'a' });
    jest.advanceTimersByTime(300);

    handleEvent('search', { query: 'ab' }); // Reset timer
    jest.advanceTimersByTime(300);

    expect(sendSpy).not.toHaveBeenCalled(); // Not yet

    jest.advanceTimersByTime(200);
    expect(sendSpy).toHaveBeenCalledWith('search', { query: 'ab' });
  });
});
```

#### 2. Throttle Behavior

```javascript
describe('Throttle', () => {
  test('limits event frequency', () => {
    const sendSpy = jest.spyOn(window, 'sendEvent');

    window.handlerMetadata['scroll'] = {
      throttle: { interval: 1.0, leading: true, trailing: false }
    };

    handleEvent('scroll', { y: 100 });
    expect(sendSpy).toHaveBeenCalledTimes(1); // Leading

    handleEvent('scroll', { y: 200 });
    handleEvent('scroll', { y: 300 });
    expect(sendSpy).toHaveBeenCalledTimes(1); // Still throttled

    jest.advanceTimersByTime(1000);

    handleEvent('scroll', { y: 400 });
    expect(sendSpy).toHaveBeenCalledTimes(2); // After interval
  });
});
```

#### 3. Cache Behavior

```javascript
describe('Cache', () => {
  test('returns cached response', () => {
    const sendSpy = jest.spyOn(window, 'sendEvent');

    window.handlerMetadata['search'] = {
      cache: { ttl: 60, key_params: ['query'] }
    };

    // First call: miss
    handleEvent('search', { query: 'laptop' });
    expect(sendSpy).toHaveBeenCalledTimes(1);

    // Cache response
    cacheResponse('search', { query: 'laptop' }, { patches: [] }, 60);

    // Second call: hit
    handleEvent('search', { query: 'laptop' });
    expect(sendSpy).toHaveBeenCalledTimes(1); // Not called again
  });

  test('cache expires after TTL', () => {
    jest.useFakeTimers();

    window.handlerMetadata['search'] = {
      cache: { ttl: 60, key_params: ['query'] }
    };

    cacheResponse('search', { query: 'laptop' }, { patches: [] }, 60);

    jest.advanceTimersByTime(61000); // 61 seconds

    const cached = checkCache('search', { query: 'laptop' }, { ttl: 60, key_params: ['query'] });
    expect(cached).toBeNull();
  });
});
```

### Integration Tests

```python
# tests/integration/test_state_management.py
import pytest
from channels.testing import WebsocketCommunicator
from myproject.asgi import application

@pytest.mark.asyncio
async def test_debounced_search():
    """Test that @debounce metadata is sent to client."""
    communicator = WebsocketCommunicator(application, "/ws/live/")
    connected, subprotocol = await communicator.connect()
    assert connected

    # Initial render should include metadata
    response = await communicator.receive_json_from()
    assert 'handlers' in response
    assert 'search' in response['handlers']
    assert response['handlers']['search']['debounce'] == {'wait': 0.5, 'max_wait': None}

    await communicator.disconnect()
```

## Implementation Phases

### Phase 1: Core Infrastructure (Week 1-2)

**Goal**: Metadata extraction and transmission

**Tasks**:
1. Implement decorator functions (debounce, throttle, optimistic, etc.)
2. Implement `_extract_handler_metadata()` in LiveView
3. Modify `render()` to inject metadata script
4. Update WebSocket response to include metadata
5. Write Python tests for metadata extraction

**Deliverables**:
- ✅ Decorators attach metadata to functions
- ✅ Metadata extracted and sent to client
- ✅ Tests pass

**Risk**: Low

### Phase 2: Client-Side Debounce/Throttle (Week 3-4)

**Goal**: Implement debounce and throttle interceptors

**Tasks**:
1. Implement `debounceEvent()` function
2. Implement `throttleEvent()` function
3. Integrate into `handleEvent()` pipeline
4. Add debug logging
5. Write JavaScript tests

**Deliverables**:
- ✅ @debounce works client-side
- ✅ @throttle works client-side
- ✅ Tests pass

**Risk**: Low (well-understood patterns)

### Phase 3: Optimistic Updates (Week 5-6)

**Goal**: Instant UI updates before server validation

**Tasks**:
1. Implement `applyOptimisticUpdate()` heuristics
2. Handle server corrections (revert on error)
3. Add conflict resolution
4. Write tests

**Deliverables**:
- ✅ @optimistic applies instant updates
- ✅ Server corrections work
- ✅ Tests pass

**Risk**: Medium (heuristics may not cover all cases)

### Phase 4: Client State & Caching (Week 7-8)

**Goal**: StateBus and response caching

**Tasks**:
1. Implement StateBus (pub/sub)
2. Implement response cache
3. Add cache invalidation
4. Write tests

**Deliverables**:
- ✅ @client_state coordinates components
- ✅ @cache reduces server requests
- ✅ Tests pass

**Risk**: Medium (cache invalidation is hard)

### Phase 5: DraftMode & Loading States (Week 9-10)

**Goal**: Form drafts and loading indicators

**Tasks**:
1. Implement DraftMode mixin
2. Implement localStorage persistence
3. Implement @loading attributes
4. Write tests

**Deliverables**:
- ✅ DraftModeMixin auto-saves forms
- ✅ @loading shows/hides indicators
- ✅ Tests pass

**Risk**: Low

### Phase 6: Integration & Documentation (Week 11-12)

**Goal**: End-to-end testing and docs

**Tasks**:
1. Build demo apps using all decorators
2. Integration testing
3. Performance benchmarking
4. Update documentation
5. Migration guide

**Deliverables**:
- ✅ Demo apps showcase features
- ✅ Performance meets targets
- ✅ Documentation complete

**Risk**: Low

## Success Metrics

### Performance Targets

| Metric | Current | Target | Status |
|--------|---------|--------|--------|
| Bundle size | 5.0 KB | < 10 KB | ✅ 7.1 KB |
| Template render | < 1ms | < 1ms | ✅ Unchanged |
| VDOM diff | < 100μs | < 100μs | ✅ Unchanged |
| WebSocket latency | ~50ms | ~50ms | ✅ Unchanged |

### Code Reduction

| Pattern | Before (JS) | After (Python) | Reduction |
|---------|-------------|----------------|-----------|
| Debounced search | 35 lines | 0 lines | 100% |
| Optimistic updates | 45 lines | 0 lines | 100% |
| StateBus | 85 lines | ~10 lines | 88% |
| Form drafts | 65 lines | 0 lines | 100% |
| Response caching | 55 lines | 0 lines | 100% |

**Total**: 889 lines JS → ~120 lines Python = **87% reduction**

### Developer Experience

- ✅ Zero JavaScript required for common patterns
- ✅ Declarative Python decorators
- ✅ Automatic optimization
- ✅ Backward compatible
- ✅ Graceful degradation

## Conclusion

This architecture delivers Python-only state management that:

1. **Eliminates JavaScript**: 87% code reduction
2. **Maintains Performance**: 5KB → 7.1KB bundle (still smallest)
3. **Stays Pythonic**: Declarative decorators, not imperative JS
4. **Enables Patterns**: Debounce, throttle, optimistic, state sharing, caching, drafts
5. **Competes with Phoenix**: Matches LiveView/Livewire DX

**Next Steps**:
1. Get community feedback on this specification
2. Begin Phase 1 implementation
3. Iterate based on real-world usage

**Estimated Timeline**: 10-12 weeks to production-ready

---

**Questions? Feedback?**
Open an issue or discussion on GitHub.
