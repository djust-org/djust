# Component API Reference

## Table of Contents

1. [Component (Simple/Stateless)](#component-simplestateless)
2. [LiveComponent (Stateful/Reactive)](#livecomponent-statefulreactive)
3. [Component Communication](#component-communication)
4. [Framework Adapters](#framework-adapters)
5. [Type Definitions](#type-definitions)

## Component (Simple/Stateless)

### Overview

`Component` is the base class for stateless components that render HTML based on input parameters. These components have no lifecycle, no state management, and no VDOM tracking.

**Use cases:** Badges, buttons, icons, simple cards, alerts

### Class Definition

```python
from djust.components import Component

class Component(ABC):
    """
    Base class for stateless components.

    Simple components are pure functions that convert props to HTML.
    They are lightweight, have no lifecycle, and are recreated on each render.
    """
```

### Constructor

```python
def __init__(self, **kwargs):
    """
    Initialize component with props.

    Args:
        **kwargs: Component properties (implementation-specific)

    Example:
        badge = BadgeComponent(text="New", variant="primary", size="sm")
    """
```

### Methods

#### `render() -> str`

**Required.** Renders the component to HTML.

```python
@abstractmethod
def render(self) -> str:
    """
    Render component to HTML string.

    Returns:
        HTML string (should be marked as safe for Django templates)

    Example:
        def render(self) -> str:
            from django.utils.safestring import mark_safe
            return mark_safe(f'<span class="badge">{self.text}</span>')
    """
```

**Returns:**
- `str`: HTML markup (marked as safe via `mark_safe()`)

**Notes:**
- Should use `mark_safe()` to prevent double-escaping
- Should implement framework-specific rendering (see Framework Adapters)
- Should handle None/empty values gracefully

### Complete Example

```python
from djust.components import Component
from django.utils.safestring import mark_safe
from djust.config import config

class BadgeComponent(Component):
    """
    Simple badge component.

    Args:
        text: Badge text content
        variant: Color variant (primary, secondary, success, danger, etc.)
        size: Badge size (sm, md, lg)
    """

    def __init__(
        self,
        text: str,
        variant: str = "primary",
        size: str = "md"
    ):
        self.text = text
        self.variant = variant
        self.size = size

    def render(self) -> str:
        """Render badge with framework-specific styling"""
        framework = config.get('css_framework', 'bootstrap5')

        if framework == 'bootstrap5':
            return mark_safe(self._render_bootstrap())
        elif framework == 'tailwind':
            return mark_safe(self._render_tailwind())
        else:
            return mark_safe(self._render_plain())

    def _render_bootstrap(self) -> str:
        """Bootstrap 5 implementation"""
        size_class = 'fs-6' if self.size == 'lg' else ''
        return f'<span class="badge bg-{self.variant} {size_class}">{self.text}</span>'

    def _render_tailwind(self) -> str:
        """Tailwind CSS implementation"""
        colors = {
            'primary': 'bg-blue-500 text-white',
            'secondary': 'bg-gray-500 text-white',
            'success': 'bg-green-500 text-white',
            'danger': 'bg-red-500 text-white',
        }
        size_classes = {
            'sm': 'text-xs px-2 py-0.5',
            'md': 'text-sm px-2.5 py-1',
            'lg': 'text-base px-3 py-1.5',
        }
        color = colors.get(self.variant, colors['primary'])
        size = size_classes.get(self.size, size_classes['md'])

        return f'<span class="inline-block rounded {color} {size}">{self.text}</span>'

    def _render_plain(self) -> str:
        """Plain HTML implementation"""
        return f'<span class="badge badge-{self.variant} badge-{self.size}">{self.text}</span>'
```

### Usage in LiveView

```python
class MyView(LiveView):
    template_string = """
        <h1>Counter: {{ counter }}</h1>
        {{ badge.render }}
    """

    def mount(self, request):
        self.counter = 0

    def get_context_data(self):
        return {
            'counter': self.counter,
            'badge': BadgeComponent(
                text=str(self.counter),
                variant='primary' if self.counter > 0 else 'secondary'
            )
        }
```

**Important:** Simple components are recreated on each render. Don't try to maintain state!

---

## LiveComponent (Stateful/Reactive)

### Overview

`LiveComponent` is the base class for stateful components with their own lifecycle, VDOM tracking, and event handling. These components maintain internal state and can communicate with their parent.

**Use cases:** Tabs, pagination, modals, complex forms, file uploads, charts

### Class Definition

```python
from djust import LiveComponent

class LiveComponent(ABC):
    """
    Base class for stateful, reactive components.

    LiveComponents maintain their own state, handle events, and have
    separate VDOM trees for efficient patching.
    """
```

### Class Attributes

```python
class MyComponent(LiveComponent):
    # Template source (choose one)
    template_name: Optional[str] = None  # Path to template file
    template_string: Optional[str] = None  # Inline template string

    # Component ID (auto-assigned by framework)
    component_id: str  # e.g., "tabs_abc123"
```

### Constructor

```python
def __init__(self, component_id: Optional[str] = None, **kwargs):
    """
    Initialize component (called by framework).

    Args:
        component_id: Unique identifier (auto-generated if not provided)
        **kwargs: Passed to mount() method

    Note:
        Don't override __init__. Use mount() instead.
    """
```

### Lifecycle Methods

#### `mount(**kwargs)`

**Required.** Initialize component state.

```python
@abstractmethod
def mount(self, **kwargs):
    """
    Initialize component state.

    Called once when component is created. Set up initial state here.

    Args:
        **kwargs: Initialization parameters from parent

    Example:
        def mount(self, tabs, active_tab=None):
            self.tabs = tabs
            self.active_tab = active_tab or tabs[0].id
            self.tab_history = []
    """
```

**When called:**
- Once when component is first created
- Before first render

**Use for:**
- Setting initial state variables
- Loading data
- Initializing timers/connections (future)

#### `update(**props)` (Future)

```python
def update(self, **props):
    """
    Update component when parent passes new props.

    Called when parent re-renders with new prop values.

    Args:
        **props: New prop values from parent

    Example:
        def update(self, **props):
            if 'total' in props and props['total'] != self.total:
                self.total = props['total']
                self.recalculate_pages()
    """
```

**When called:**
- When parent changes props passed to component

**Use for:**
- Reacting to prop changes
- Re-calculating derived state

#### `unmount()` (Future)

```python
def unmount(self):
    """
    Cleanup when component is removed.

    Called when component is destroyed.

    Example:
        def unmount(self):
            # Cancel timers
            if self.timer:
                self.timer.cancel()

            # Close connections
            if self.ws_connection:
                self.ws_connection.close()
    """
```

**When called:**
- When component is removed from parent
- When LiveView session ends

**Use for:**
- Canceling timers
- Closing connections
- Releasing resources

### Rendering Methods

#### `get_context_data() -> Dict[str, Any]`

**Required.** Return template context.

```python
@abstractmethod
def get_context_data(self) -> Dict[str, Any]:
    """
    Get context data for template rendering.

    Returns:
        Dictionary of template variables

    Example:
        def get_context_data(self):
            return {
                'tabs': self.tabs,
                'active_tab': self.active_tab,
                'total_tabs': len(self.tabs),
            }
    """
```

**Returns:**
- `Dict[str, Any]`: Context dictionary for template

**Notes:**
- Return only JSON-serializable values
- Components in context will be pre-rendered
- Available in template as `{{ key }}`

#### `render() -> str`

Render component to HTML (called by framework).

```python
def render(self) -> str:
    """
    Render component to HTML.

    Returns:
        HTML string with component boundary marker

    Note:
        Called automatically by framework. Usually don't need to override.
    """
```

**Returns:**
- `str`: HTML markup wrapped in component boundary

**HTML Structure:**
```html
<div data-livecomponent-id="tabs_abc123" data-component="TabsComponent">
    <!-- Your template content here -->
    <ul class="nav">...</ul>
</div>
```

#### `render_with_diff() -> Tuple[str, Optional[str], int]`

Generate VDOM patches (called by framework).

```python
def render_with_diff(self) -> Tuple[str, Optional[str], int]:
    """
    Render and generate VDOM patches.

    Returns:
        Tuple of (html, patches_json, version)
        - html: Full HTML string
        - patches_json: JSON string of patches (or None)
        - version: VDOM version number

    Note:
        Called automatically by framework after event handlers.
    """
```

**Returns:**
- `Tuple[str, Optional[str], int]`:
  - `html`: Full rendered HTML
  - `patches_json`: JSON array of patches (or `None` on first render)
  - `version`: Incrementing version number

### Event Handling

#### Defining Event Handlers

```python
class TabsComponent(LiveComponent):
    def switch_tab(self, tab: str = None, **kwargs):
        """
        Handle tab click event.

        Args:
            tab: Tab ID from data-tab attribute
            **kwargs: Additional event data

        Example template:
            <button @click="switch_tab" data-tab="{{ tab.id }}">
        """
        if tab:
            self.active_tab = tab
            # State changed - framework will call render_with_diff()
```

**Event Handler Signature:**
- Method name matches event name in template (`@click="switch_tab"`)
- Parameters come from `data-*` attributes
- `**kwargs` captures additional data

**Template Binding:**
```html
<!-- Triggers: component.switch_tab(tab="profile") -->
<button @click="switch_tab" data-tab="profile">Profile</button>

<!-- Triggers: component.page_changed(page="2", size="10") -->
<button @click="page_changed" data-page="2" data-size="10">Next</button>
```

### Parent Communication

#### `send_parent(event: str, data: Dict[str, Any])`

Send event to parent LiveView.

```python
def send_parent(self, event: str, data: Dict[str, Any]):
    """
    Send event notification to parent.

    Args:
        event: Event name (handled by parent)
        data: Event payload (JSON-serializable dict)

    Example:
        def switch_tab(self, tab: str = None, **kwargs):
            self.active_tab = tab
            self.send_parent("tab_changed", {
                "tab": tab,
                "timestamp": datetime.now().isoformat()
            })
    """
```

**Parameters:**
- `event` (str): Event name (must match parent handler)
- `data` (Dict[str, Any]): Event payload (JSON-serializable)

**Parent Handler:**
```python
class ParentView(LiveView):
    def handle_component_event(self, component_id: str, event: str, data: Dict[str, Any]):
        """Handle events from child components"""
        if component_id == "tabs" and event == "tab_changed":
            self.on_tab_changed(data["tab"])
```

### Complete Example

```python
from djust import LiveComponent
from typing import List, Dict, Any

class TabsComponent(LiveComponent):
    """
    Tabbed interface with internal state management.

    Args:
        tabs: List of tab dictionaries [{"id": "x", "label": "X", "content": "..."}]
        active_tab: Initially active tab ID
        variant: Visual style ("tabs" or "pills")
    """

    template_string = """
        <div class="tabs-container">
            <!-- Tab buttons -->
            <ul class="nav nav-{{ variant }}">
                {% for tab in tabs %}
                <li class="nav-item">
                    <button
                        class="nav-link {% if tab.id == active_tab %}active{% endif %}"
                        @click="switch_tab"
                        data-tab="{{ tab.id }}"
                        {% if tab.disabled %}disabled{% endif %}>
                        {{ tab.label }}
                        {% if tab.badge %}
                        <span class="badge">{{ tab.badge }}</span>
                        {% endif %}
                    </button>
                </li>
                {% endfor %}
            </ul>

            <!-- Tab content -->
            <div class="tab-content mt-3">
                {% for tab in tabs %}
                {% if tab.id == active_tab %}
                <div class="tab-pane active">
                    {{ tab.content }}
                </div>
                {% endif %}
                {% endfor %}
            </div>
        </div>
    """

    def mount(self, tabs: List[Dict], active_tab: str = None, variant: str = "tabs"):
        """Initialize tabs"""
        self.tabs = tabs
        self.active_tab = active_tab or (tabs[0]["id"] if tabs else None)
        self.variant = variant
        self.tab_history = []  # Track navigation history

    def get_context_data(self) -> Dict[str, Any]:
        """Get template context"""
        return {
            "tabs": self.tabs,
            "active_tab": self.active_tab,
            "variant": self.variant,
        }

    def switch_tab(self, tab: str = None, **kwargs):
        """Handle tab switch"""
        if tab and tab != self.active_tab:
            # Update history
            self.tab_history.append(self.active_tab)

            # Switch tab
            self.active_tab = tab

            # Notify parent
            self.send_parent("tab_changed", {
                "previous_tab": self.tab_history[-1] if self.tab_history else None,
                "current_tab": tab,
                "tab_count": len(self.tabs)
            })

    def activate_tab(self, tab_id: str):
        """Programmatically activate tab (called by parent)"""
        self.switch_tab(tab=tab_id)

    def go_back(self):
        """Navigate to previous tab"""
        if self.tab_history:
            previous = self.tab_history.pop()
            self.active_tab = previous
```

### Usage in LiveView

```python
class DashboardView(LiveView):
    template_string = """
        <h1>Dashboard</h1>

        <!-- Render LiveComponent -->
        <TabsComponent id="profile_tabs" />

        <!-- Or using Django template tag (future) -->
        {% livecomponent "profile_tabs" %}
    """

    def mount(self, request):
        # Create LiveComponent once
        self.profile_tabs = TabsComponent(
            tabs=[
                {"id": "overview", "label": "Overview", "content": "..."},
                {"id": "settings", "label": "Settings", "content": "..."},
                {"id": "security", "label": "Security", "content": "...", "badge": "2"},
            ],
            active_tab="overview"
        )

    def handle_component_event(self, component_id: str, event: str, data: Dict):
        """Handle events from LiveComponents"""
        if component_id == "profile_tabs" and event == "tab_changed":
            # React to tab change
            if data["current_tab"] == "security":
                self.load_security_alerts()

    def load_security_alerts(self):
        """Load data when security tab becomes active"""
        self.security_alerts = fetch_alerts()
```

---

## Component Communication

### Patterns

#### 1. Props Down (Parent → Child)

```python
# Parent
self.tabs = TabsComponent(
    tabs=self.tab_data,  # Pass data down
    active_tab=self.selected_tab
)

# Child receives in mount()
def mount(self, tabs, active_tab):
    self.tabs = tabs
    self.active_tab = active_tab
```

#### 2. Events Up (Child → Parent)

```python
# Child sends event
def switch_tab(self, tab: str = None, **kwargs):
    self.active_tab = tab
    self.send_parent("tab_changed", {"tab": tab})

# Parent handles event
def handle_component_event(self, component_id, event, data):
    if event == "tab_changed":
        self.selected_tab = data["tab"]
```

#### 3. Direct Method Calls (Parent → Child)

```python
# Parent can call child methods directly
self.tabs.activate_tab("settings")
self.pagination.go_to_page(5)
```

### Event Data Structure

**From Child to Parent:**
```python
{
    "component_id": "tabs_abc123",
    "event": "tab_changed",
    "data": {
        "tab": "settings",
        "previous_tab": "overview",
        "timestamp": "2025-01-10T..."
    }
}
```

**From WebSocket:**
```python
{
    "type": "event",
    "target": "tabs_abc123",  # Component ID
    "event": "switch_tab",
    "params": {"tab": "settings"}  # From data-* attributes
}
```

---

## Framework Adapters

### Overview

Both Component and LiveComponent can implement framework-specific rendering:

```python
from djust.config import config

class MyComponent:
    def render(self) -> str:
        framework = config.get('css_framework', 'bootstrap5')

        if framework == 'bootstrap5':
            return self._render_bootstrap()
        elif framework == 'tailwind':
            return self._render_tailwind()
        else:
            return self._render_plain()
```

### Framework Detection

```python
from djust.config import config

# Get configured framework
framework = config.get('css_framework', 'bootstrap5')

# Check framework
if framework == 'bootstrap5':
    # Bootstrap implementation
elif framework == 'tailwind':
    # Tailwind implementation
else:
    # Plain HTML fallback
```

### Configuration

```python
# settings.py
DJUST = {
    'css_framework': 'bootstrap5',  # or 'tailwind', 'plain'
}
```

---

## Type Definitions

### Common Types

```python
from typing import Dict, Any, List, Optional, Tuple, Callable

# Context dictionary
ContextDict = Dict[str, Any]

# Patch data (from VDOM)
PatchData = List[Dict[str, Any]]

# Render result
RenderResult = Tuple[str, Optional[str], int]  # (html, patches_json, version)

# Event data
EventData = Dict[str, Any]

# Component props
Props = Dict[str, Any]
```

### Component Signatures

```python
# Simple Component
class Component(ABC):
    def __init__(self, **kwargs): ...
    @abstractmethod
    def render(self) -> str: ...

# LiveComponent
class LiveComponent(ABC):
    def __init__(self, component_id: Optional[str] = None, **kwargs): ...
    @abstractmethod
    def mount(self, **kwargs): ...
    @abstractmethod
    def get_context_data(self) -> ContextDict: ...
    def render(self) -> str: ...
    def render_with_diff(self) -> RenderResult: ...
    def send_parent(self, event: str, data: EventData): ...
```

---

---

## Lazy Hydration

LiveView elements can defer WebSocket connections until they're needed, reducing initial page load and memory usage.

### Overview

By default, LiveView establishes WebSocket connections immediately when the page loads. For pages with multiple LiveView elements (especially below the fold), this can waste resources for content the user may never see.

Lazy hydration defers the WebSocket connection until:
- The element enters the viewport (`viewport` mode - default)
- The user clicks the element (`click` mode)
- The user hovers over the element (`hover` mode)
- The browser is idle (`idle` mode)

### Usage

Add the `data-live-lazy` attribute to any LiveView element:

```html
<!-- Hydrate when element enters viewport (default) -->
<div data-live-view="my_view" data-live-lazy>
    <!-- Static placeholder content -->
    <div class="skeleton">Loading...</div>
</div>

<!-- Hydrate on first click -->
<div data-live-view="my_view" data-live-lazy="click">
    <button>Click to activate</button>
</div>

<!-- Hydrate on hover -->
<div data-live-view="my_view" data-live-lazy="hover">
    <span>Hover to load details</span>
</div>

<!-- Hydrate when browser is idle -->
<div data-live-view="my_view" data-live-lazy="idle">
    <!-- Low-priority content -->
</div>
```

### Modes

| Mode | Trigger | Use Case |
|------|---------|----------|
| `viewport` | Element enters viewport | Below-fold content, infinite scroll |
| `click` | User clicks element | Interactive widgets, expandable sections |
| `hover` | User hovers over element | Tooltips, preview cards |
| `idle` | Browser idle (requestIdleCallback) | Low-priority background content |

### Python API

```python
# Access lazy hydration state
from djust import LiveView

class MyView(LiveView):
    def mount(self, request, **kwargs):
        # Check if this is a lazy hydration mount
        if kwargs.get('lazy_hydrated'):
            # This mount was triggered by lazy hydration
            self.load_expensive_data()
```

### JavaScript API

```javascript
// Programmatically trigger hydration
window.djust.lazyHydration.hydrateNow(element);

// Check if element is pending hydration
const isPending = window.djust.lazyHydration.isPending(element);

// Force hydration of all pending elements
window.djust.lazyHydration.hydrateAll();
```

### Performance Impact

| Scenario | Memory Reduction | Notes |
|----------|------------------|-------|
| 5 below-fold LiveViews | ~20-30% | Typical dashboard |
| 10+ lazy elements | ~30-40% | Content-heavy pages |
| Infinite scroll | ~50%+ | Only visible items hydrated |

### Best Practices

1. **Use viewport mode for below-fold content**: Most common use case
2. **Use click mode for expandable sections**: Saves resources for collapsed content
3. **Provide meaningful placeholders**: Users should see something useful before hydration
4. **Avoid lazy hydration for critical content**: Above-fold, interactive elements should hydrate immediately

### Example: Dashboard with Lazy Widgets

```html
<!-- Critical widget - loads immediately -->
<div data-live-view="sales_summary">
    {{ sales_data }}
</div>

<!-- Below-fold widgets - lazy loaded -->
<div data-live-view="recent_orders" data-live-lazy>
    <div class="widget-skeleton">
        <div class="skeleton-line"></div>
        <div class="skeleton-line"></div>
    </div>
</div>

<div data-live-view="inventory_alerts" data-live-lazy>
    <div class="widget-skeleton">Loading alerts...</div>
</div>

<!-- Click-to-expand section -->
<details>
    <summary>Advanced Analytics</summary>
    <div data-live-view="analytics_chart" data-live-lazy="click">
        <p>Click to load chart...</p>
    </div>
</details>
```

---

## See Also

- [LiveComponent Architecture](LIVECOMPONENT_ARCHITECTURE.md) - System architecture
- [Best Practices](COMPONENT_BEST_PRACTICES.md) - When to use which type
- [Migration Guide](COMPONENT_MIGRATION_GUIDE.md) - Migrating existing components
- [Examples](COMPONENT_EXAMPLES.md) - Working code examples
- [Performance Optimization](COMPONENT_PERFORMANCE_OPTIMIZATION.md) - Performance tuning guide
