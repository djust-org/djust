# LiveComponent Architecture

## Table of Contents

1. [Overview](#overview)
2. [The Two-Tier Component System](#the-two-tier-component-system)
3. [Why Two Types of Components?](#why-two-types-of-components)
4. [Component Hierarchy](#component-hierarchy)
5. [VDOM Patching with Components](#vdom-patching-with-components)
6. [Component Lifecycle](#component-lifecycle)
7. [Parent-Child Communication](#parent-child-communication)
8. [Performance Characteristics](#performance-characteristics)
9. [Implementation Details](#implementation-details)

## Overview

djust implements a **two-tier component system** inspired by Phoenix LiveView, combining the simplicity of stateless components with the power of stateful, reactive LiveComponents.

```
┌─────────────────────────────────────────────────────────┐
│                      LiveView (Parent)                  │
│  ┌──────────────────────────────────────────────────┐  │
│  │  State: counter, items, show_alert                │  │
│  │  VDOM Tree #1 (parent content)                    │  │
│  └──────────────────────────────────────────────────┘  │
│                                                          │
│  ┌────────────┐  ┌────────────┐  ┌──────────────────┐ │
│  │  Simple    │  │  Simple    │  │  LiveComponent   │ │
│  │  Component │  │  Component │  │  (Stateful)      │ │
│  │  (Button)  │  │  (Badge)   │  │  ┌────────────┐  │ │
│  │            │  │            │  │  │ Own State  │  │ │
│  │  Renders   │  │  Renders   │  │  │ VDOM #2    │  │ │
│  │  HTML      │  │  HTML      │  │  └────────────┘  │ │
│  └────────────┘  └────────────┘  └──────────────────┘ │
└─────────────────────────────────────────────────────────┘
```

## The Two-Tier Component System

### Component (Simple/Stateless)

**Purpose:** Render HTML based on input parameters, no state, no lifecycle.

```python
from djust.components import Component

class BadgeComponent(Component):
    """Simple component - just returns HTML"""

    def __init__(self, text: str, variant: str = "primary"):
        self.text = text
        self.variant = variant

    def render(self) -> str:
        return f'<span class="badge bg-{self.variant}">{self.text}</span>'
```

**Characteristics:**
- ✅ Lightweight (no VDOM tracking)
- ✅ Fast rendering
- ✅ No memory overhead
- ✅ No event handlers
- ✅ Recreated on each parent render
- ❌ Cannot have internal state
- ❌ Cannot handle events directly

### LiveComponent (Stateful/Reactive)

**Purpose:** Complex widgets with internal state, lifecycle, and event handling.

```python
from djust import LiveComponent

class TabsComponent(LiveComponent):
    """Stateful component - manages own state"""

    template_string = """
        <ul class="nav">
            {% for tab in tabs %}
            <li class="{% if tab.id == active_tab %}active{% endif %}"
                @click="switch_tab" data-tab="{{ tab.id }}">
                {{ tab.label }}
            </li>
            {% endfor %}
        </ul>
    """

    def mount(self, tabs, active_tab=None):
        """Initialize component state"""
        self.tabs = tabs
        self.active_tab = active_tab or tabs[0].id

    def switch_tab(self, tab: str = None, **kwargs):
        """Handle tab switching"""
        if tab:
            self.active_tab = tab
            # Notify parent
            self.send_parent("tab_changed", {"tab": tab})

    def get_context_data(self):
        return {
            "tabs": self.tabs,
            "active_tab": self.active_tab
        }
```

**Characteristics:**
- ✅ Has internal state
- ✅ Own VDOM tree (efficient patches)
- ✅ Event handlers
- ✅ Lifecycle methods (mount, update, unmount)
- ✅ Can communicate with parent
- ✅ Persistent across renders
- ⚠️ Higher memory overhead
- ⚠️ More complex API

## Why Two Types of Components?

### The Problem with One-Size-Fits-All

If **everything** is a LiveComponent:
- ❌ Memory overhead for simple badges/buttons
- ❌ Unnecessary VDOM tracking
- ❌ Complex API for simple use cases
- ❌ Slower simple renders

If **nothing** is a LiveComponent:
- ❌ Parent bloats with all child logic
- ❌ No component reusability
- ❌ Poor encapsulation
- ❌ Can't have self-contained complex widgets

### The Solution: Use the Right Tool

**Simple Component:** "This button always looks the same given these props"

```python
# Created on each render, no state
badge = BadgeComponent(text=str(counter), variant="primary")
```

**LiveComponent:** "This pagination widget manages its own page state and tells parent when pages change"

```python
# Created once in mount(), persists across renders
self.pagination = PaginationComponent(total=100, per_page=10)
```

## Component Hierarchy

### Full System Diagram

```
┌──────────────────────────────────────────────────────────────┐
│                   Browser (Client)                            │
│  ┌────────────────────────────────────────────────────────┐  │
│  │  DOM Tree                                              │  │
│  │    <div data-liveview-root>                           │  │
│  │      <h1>Counter: 5</h1>                              │  │
│  │      <span class="badge">5</span>  ← Simple Component │  │
│  │      <div data-livecomponent-id="tabs_abc123">        │  │
│  │        <ul class="nav">...</ul>    ← LiveComponent    │  │
│  │      </div>                                            │  │
│  │    </div>                                              │  │
│  └────────────────────────────────────────────────────────┘  │
└───────────────────────────────────────────────────────────────┘
         ↕ WebSocket (patches)
┌──────────────────────────────────────────────────────────────┐
│                   Server (Python/Rust)                        │
│  ┌────────────────────────────────────────────────────────┐  │
│  │  LiveView Instance                                     │  │
│  │    state: {counter: 5, items: [...]}                  │  │
│  │    _rust_view: RustLiveView                           │  │
│  │      └─ VDOM Tree #1 (parent content)                 │  │
│  │                                                         │  │
│  │    components:                                         │  │
│  │      badge: BadgeComponent(text="5")  ← Stateless     │  │
│  │      tabs: TabsComponent              ← Stateful       │  │
│  │        └─ _rust_view: RustLiveView                    │  │
│  │            └─ VDOM Tree #2 (tabs content)             │  │
│  └────────────────────────────────────────────────────────┘  │
└──────────────────────────────────────────────────────────────┘
```

### State Ownership

```python
class DashboardView(LiveView):
    def mount(self, request):
        # Parent owns state
        self.counter = 0
        self.items = []

        # Simple component - recreated with new value on each render
        # (no state ownership)

        # Stateful component - owns its state
        self.tabs = TabsComponent(tabs=[...])  # tabs.active_tab is owned by component
        self.pagination = PaginationComponent(total=100)  # pagination.current_page owned by component
```

## VDOM Patching with Components

### How VDOM Tracking Works

#### Simple Components: No VDOM Tracking

Simple components are rendered inline into the parent's HTML:

```python
# Parent template
template_string = """
    <h1>Counter: {{ counter }}</h1>
    {{ badge.render }}  <!-- Inline: <span class="badge">5</span> -->
"""
```

The badge HTML becomes part of the **parent's VDOM tree**:

```
Parent VDOM:
  Element(div)
    ├─ Element(h1)
    │   └─ Text("Counter: 5")
    └─ Element(span, class="badge")  ← Badge is part of parent tree
        └─ Text("5")
```

When `counter` changes, the **parent's VDOM** generates patches for everything, including the badge.

#### LiveComponents: Separate VDOM Tree

LiveComponents maintain their own VDOM tree:

```python
# Parent template
template_string = """
    <h1>Counter: {{ counter }}</h1>
    <LiveComponent id="tabs" />  <!-- Component boundary -->
"""
```

This creates **separate VDOM trees**:

```
Parent VDOM Tree #1:
  Element(div, data-liveview-root)
    ├─ Element(h1)
    │   └─ Text("Counter: 5")
    └─ Element(div, data-livecomponent-id="tabs")  ← Boundary marker
        └─ [Component content tracked separately]

Tabs Component VDOM Tree #2:
  Element(ul, class="nav")
    ├─ Element(li, class="active")
    │   └─ Text("Tab 1")
    └─ Element(li)
        └─ Text("Tab 2")
```

### Patch Targeting

When a LiveComponent's state changes, **only that component generates patches**:

```javascript
// Parent counter changed
{
    "type": "patch",
    "target": "root",  // Apply to parent's DOM
    "patches": [
        {"op": "replace", "path": [0, 0], "value": "Counter: 6"}
    ]
}

// Tab clicked in LiveComponent
{
    "type": "patch",
    "target": "tabs_abc123",  // Apply to component's DOM subtree
    "patches": [
        {"op": "set_class", "path": [0, 0], "value": ""},
        {"op": "set_class", "path": [0, 1], "value": "active"}
    ]
}
```

### Performance Benefit

**Without LiveComponents (all in parent):**
- User clicks tab
- Parent re-renders entire template (navbar, counter, tabs, footer, everything)
- VDOM diffs entire page (~1000 nodes)
- Generates patches for tab + any structural changes
- Payload: ~500 bytes of patches

**With LiveComponents:**
- User clicks tab
- **Only TabsComponent** re-renders (just tabs, ~10 nodes)
- VDOM diffs only tabs subtree
- Generates minimal patches (2 class changes)
- Payload: ~80 bytes of patches

**Result: 6x smaller payload, 100x faster diffing**

## Component Lifecycle

### Simple Component Lifecycle

Simple components have **no lifecycle** - they're pure functions:

```python
# Render cycle 1
badge = BadgeComponent(text="5", variant="primary")
html = badge.render()  # Creates <span...>5</span>

# Render cycle 2 (counter changed)
badge = BadgeComponent(text="6", variant="primary")  # NEW INSTANCE
html = badge.render()  # Creates <span...>6</span>

# Previous badge instance is garbage collected
```

### LiveComponent Lifecycle

LiveComponents have a full lifecycle:

```
┌──────────────┐
│  __init__()  │  ← Component instantiated
└──────┬───────┘
       ↓
┌──────────────┐
│   mount()    │  ← Initialize state
└──────┬───────┘
       ↓
┌──────────────┐
│   render()   │  ← Generate initial HTML
└──────┬───────┘
       ↓
╔══════════════╗
║   MOUNTED    ║  ← Component active, handling events
╚══════╤═══════╝
       ↓ (event triggered)
┌──────────────┐
│ handle_xxx() │  ← Event handler updates state
└──────┬───────┘
       ↓
┌───────────────────┐
│ render_with_diff()│  ← Generate patches
└──────┬────────────┘
       ↓
╔══════════════╗
║   MOUNTED    ║  ← Back to active state
╚══════╤═══════╝
       ↓ (component removed)
┌──────────────┐
│  unmount()   │  ← Cleanup (future)
└──────────────┘
```

**Lifecycle Methods:**

```python
class PaginationComponent(LiveComponent):
    def mount(self, total, per_page=10):
        """Called once when component is created"""
        self.total = total
        self.per_page = per_page
        self.current_page = 1
        # Can perform initialization, load data, etc.

    def update(self, **props):
        """Called when parent passes new props (future)"""
        if 'total' in props:
            self.total = props['total']
            # Re-calculate page count if total changed

    def unmount(self):
        """Called when component is removed (future)"""
        # Cleanup: cancel timers, close connections, etc.
```

## Parent-Child Communication

### Props Down, Events Up

This is the **Phoenix LiveView pattern**:

```
    ┌─────────────────┐
    │   Parent View   │
    │                 │
    │  state: {...}   │
    └────────┬────────┘
             │ Props ↓
    ┌────────▼────────┐
    │  LiveComponent  │
    │                 │
    │  state: {...}   │
    └────────┬────────┘
             │ Events ↑
    ┌────────▼────────┐
    │   Parent View   │
    │  (handles event)│
    └─────────────────┘
```

### Passing Props to LiveComponent

```python
class DashboardView(LiveView):
    template_string = """
        <h1>User: {{ username }}</h1>

        <!-- Pass props to component -->
        <TabsComponent
            id="profile_tabs"
            :tabs="tab_data"
            :active="selected_tab" />
    """

    def mount(self, request):
        self.username = request.user.username
        self.selected_tab = "profile"
        self.tab_data = [
            {"id": "profile", "label": "Profile"},
            {"id": "settings", "label": "Settings"},
        ]

        # Component receives these as mount() parameters
        self.profile_tabs = TabsComponent(
            tabs=self.tab_data,
            active=self.selected_tab
        )
```

### Sending Events from LiveComponent

```python
class TabsComponent(LiveComponent):
    def switch_tab(self, tab: str = None, **kwargs):
        """User clicked a tab"""
        if tab:
            # Update own state
            self.active_tab = tab

            # Notify parent
            self.send_parent("tab_changed", {
                "tab": tab,
                "timestamp": datetime.now().isoformat()
            })
```

### Handling Events in Parent

```python
class DashboardView(LiveView):
    def handle_component_event(self, component_id: str, event: str, data: dict):
        """Central handler for component events"""

        if component_id == "profile_tabs" and event == "tab_changed":
            # Parent can coordinate based on tab change
            self.selected_tab = data["tab"]

            if data["tab"] == "settings":
                # Load settings data
                self.load_settings()

        elif component_id == "pagination" and event == "page_changed":
            # Load new page of data
            self.current_page = data["page"]
            self.reload_data()
```

### Direct Method Calls (Alternative Pattern)

For simpler cases, parent can directly call component methods:

```python
class DashboardView(LiveView):
    def next_tab(self):
        """Parent-initiated action"""
        # Direct method call on component
        self.profile_tabs.activate_next_tab()
```

## Performance Characteristics

### Memory Footprint

| Component Type | Memory Overhead | Per Component | 100 Components |
|---------------|-----------------|---------------|----------------|
| Simple Component | Minimal | ~1 KB | ~100 KB |
| LiveComponent | Moderate | ~5-10 KB | ~500-1000 KB |

**Recommendation:** Use simple components for numerous small widgets (badges, buttons, icons).

### Rendering Performance

| Scenario | Simple Component | LiveComponent |
|----------|------------------|---------------|
| Initial render | ~0.1 ms | ~0.5 ms |
| Update (parent changed) | ~0.1 ms (re-rendered) | ~0.01 ms (skipped) |
| Update (self changed) | N/A | ~0.2 ms (isolated) |
| VDOM diff | Included in parent | Isolated (~10x faster) |

### Network Payload

| Change Type | Without LiveComponents | With LiveComponents |
|-------------|----------------------|---------------------|
| Counter changes | ~200 bytes | ~80 bytes (same) |
| Tab switches | ~500 bytes (parent + tabs) | ~80 bytes (tabs only) |
| Pagination | ~600 bytes (parent + pagination) | ~120 bytes (pagination only) |
| Complex form update | ~2 KB (entire form) | ~200 bytes (one field) |

**Result:** LiveComponents reduce network traffic by **60-90%** for localized updates.

## Implementation Details

### Session Management

Each LiveComponent gets its own session storage:

```python
# Redis/Cache structure
{
    "liveview:session_abc": {
        "counter": 5,
        "items": [...],
        "components": {
            "tabs_123": {
                "component_class": "TabsComponent",
                "state": {
                    "active_tab": "profile",
                    "tabs": [...]
                }
            }
        }
    }
}
```

### WebSocket Routing

```python
# Client sends event
{
    "type": "event",
    "event": "switch_tab",
    "target": "tabs_123",  // Component ID
    "params": {"tab": "settings"}
}

# Server routes to component
component = session.components["tabs_123"]
component.switch_tab(tab="settings")

# Component generates patches
patches = component.render_with_diff()

# Server sends response
{
    "type": "patch",
    "target": "tabs_123",
    "patches": [...]
}
```

### VDOM Tree Management

```python
class LiveView:
    def __init__(self):
        self._rust_view = RustLiveView(template)  # Parent VDOM
        self._components = {}  # LiveComponent instances

    def render_with_diff(self):
        # Render parent template
        html, patches, version = self._rust_view.render_with_diff()

        # Render each LiveComponent
        for component_id, component in self._components.items():
            comp_html, comp_patches, comp_version = component.render_with_diff()

            # Merge patches with scoped paths
            patches.extend(self._scope_patches(comp_patches, component_id))

        return html, patches, version
```

### Component Registration

```python
# Automatic registration on mount
class DashboardView(LiveView):
    def mount(self, request):
        # Creating a LiveComponent automatically registers it
        self.tabs = TabsComponent(...)
        # Framework detects "tabs" is a LiveComponent and:
        # 1. Assigns component_id = "tabs"
        # 2. Registers in self._components["tabs"]
        # 3. Sets up event routing
```

## Summary

The two-tier component system provides:

**✅ Simplicity where it matters** - Simple components for simple cases
**✅ Power where you need it** - LiveComponents for complex widgets
**✅ Efficient VDOM patching** - Isolated component trees
**✅ Small network payloads** - Only changed components send patches
**✅ Clean architecture** - Clear separation of concerns
**✅ Phoenix-proven pattern** - Battle-tested in production

**Next Steps:**
- [API Reference](API_REFERENCE_COMPONENTS.md) - Detailed API documentation
- [Best Practices](COMPONENT_BEST_PRACTICES.md) - When to use which type
- [Migration Guide](COMPONENT_MIGRATION_GUIDE.md) - How to migrate existing code
- [Examples](COMPONENT_EXAMPLES.md) - Working code examples
