# Unified Component Design: Automatic Performance Optimization

This document describes the **unified component design** where a single `Component` class automatically selects the fastest available implementation: Pure Rust → Hybrid → Python.

## Table of Contents

1. [Design Philosophy](#design-philosophy)
2. [Implementation Strategy](#implementation-strategy)
   - Phase 1: Core Rust Components
   - Phase 2: Unified Python Component Class
   - Phase 2.5: LiveComponent - Stateful Components
   - Parent-Child Communication Pattern
   - Phase 3: Python Wrapper Components
3. [Rust Component Library](#rust-component-library)
4. [Unified Component Base Class](#unified-component-base-class)
5. [Usage Examples](#usage-examples)
6. [Migration Path](#migration-path)
7. [Two-Tier Component System](#two-tier-component-system)

## Design Philosophy

### The Problem

We want developers to write simple code:

```python
badge = Badge("New", variant="primary")
html = badge.render()
```

But behind the scenes, we want maximum performance. The developer shouldn't need to know whether `Badge` is implemented in Rust or Python.

### The Solution: Performance Waterfall

The `Component` base class automatically tries implementations in order of speed:

```
1. Pure Rust implementation?     ✅ Use it (fastest: 0.5-2μs)
   ↓ No
2. template_string defined?      ✅ Use Rust rendering (fast: 5-10μs)
   ↓ No
3. render() method overridden?   ✅ Use Python (flexible: 50-100μs)
   ↓ No
4. Error: No implementation found
```

**Developer benefit**: Write code once, get automatic performance optimization.

## Implementation Strategy

### Phase 1: Core Rust Components

Implement high-frequency components in Rust:

**Rust side** (`crates/djust/src/components/`):

```rust
// crates/djust/src/components/mod.rs
pub mod badge;
pub mod button;
pub mod icon;
pub mod spinner;

use pyo3::prelude::*;

pub fn register_components(py: Python, m: &PyModule) -> PyResult<()> {
    m.add_class::<badge::Badge>()?;
    m.add_class::<button::Button>()?;
    m.add_class::<icon::Icon>()?;
    m.add_class::<spinner::Spinner>()?;
    Ok(())
}
```

**Example: Badge in Rust** (`crates/djust/src/components/badge.rs`):

```rust
use pyo3::prelude::*;

#[pyclass(name = "RustBadge")]
#[derive(Clone)]
pub struct Badge {
    text: String,
    variant: String,
    size: String,
    framework: String,
}

#[pymethods]
impl Badge {
    #[new]
    #[pyo3(signature = (text, variant=None, size=None, framework=None))]
    fn new(
        text: String,
        variant: Option<String>,
        size: Option<String>,
        framework: Option<String>,
    ) -> Self {
        Self {
            text,
            variant: variant.unwrap_or_else(|| "primary".to_string()),
            size: size.unwrap_or_else(|| "md".to_string()),
            framework: framework.unwrap_or_else(|| "bootstrap5".to_string()),
        }
    }

    fn render(&self) -> String {
        match self.framework.as_str() {
            "bootstrap5" => self.render_bootstrap(),
            "tailwind" => self.render_tailwind(),
            _ => self.render_plain(),
        }
    }

    fn render_bootstrap(&self) -> String {
        let size_class = match self.size.as_str() {
            "sm" => " badge-sm",
            "lg" => " badge-lg",
            _ => "",
        };

        format!(
            r#"<span class="badge bg-{}{}">{}</span>"#,
            self.variant,
            size_class,
            html_escape::encode_text(&self.text)
        )
    }

    fn render_tailwind(&self) -> String {
        let size_classes = match self.size.as_str() {
            "sm" => "px-2 py-1 text-xs",
            "lg" => "px-3 py-2 text-base",
            _ => "px-2.5 py-1.5 text-sm",
        };

        let color_classes = match self.variant.as_str() {
            "primary" => "bg-blue-100 text-blue-800",
            "success" => "bg-green-100 text-green-800",
            "danger" => "bg-red-100 text-red-800",
            "warning" => "bg-yellow-100 text-yellow-800",
            "info" => "bg-cyan-100 text-cyan-800",
            _ => "bg-gray-100 text-gray-800",
        };

        format!(
            r#"<span class="rounded font-semibold {} {}">{}</span>"#,
            size_classes,
            color_classes,
            html_escape::encode_text(&self.text)
        )
    }

    fn render_plain(&self) -> String {
        format!(
            r#"<span class="badge badge-{} badge-{}">{}</span>"#,
            self.variant,
            self.size,
            html_escape::encode_text(&self.text)
        )
    }

    // Properties for template access
    #[getter]
    fn text(&self) -> &str {
        &self.text
    }

    #[getter]
    fn variant(&self) -> &str {
        &self.variant
    }

    #[getter]
    fn size(&self) -> &str {
        &self.size
    }
}
```

### Phase 2: Unified Python Component Class

**Python side** (`python/djust/components/base.py`):

```python
from abc import ABC
from typing import Optional, Dict, Any
from django.utils.safestring import mark_safe
from djust.config import config

class Component(ABC):
    """
    Unified component base class with automatic performance optimization.

    Performance waterfall:
    1. If Rust implementation exists → use it (fastest)
    2. Elif template_string defined → use Rust rendering (fast)
    3. Elif render() overridden → use Python (flexible)
    4. Else → error

    Examples:
        # Pure Rust (automatic if available)
        badge = Badge("New", variant="primary")

        # Hybrid (template_string)
        class CustomCard(Component):
            template_string = '<div class="card">{{ content }}</div>'
            def get_context_data(self):
                return {'content': self.content}

        # Pure Python (custom logic)
        class DynamicWidget(Component):
            def render(self):
                return self.complex_rendering_logic()
    """

    # Class attribute: optional Rust implementation
    _rust_impl_class: Optional[type] = None

    # Class attribute: optional template string for hybrid rendering
    template_string: Optional[str] = None

    def __init__(self, **kwargs):
        """
        Initialize component.

        If Rust implementation exists, create Rust instance.
        Otherwise, store kwargs for Python/hybrid rendering.
        """
        self._rust_instance = None

        # Try to create Rust instance if implementation exists
        if self._rust_impl_class is not None:
            try:
                # Pass framework from config to Rust
                framework = config.get('css_framework', 'bootstrap5')
                self._rust_instance = self._rust_impl_class(
                    **kwargs,
                    framework=framework
                )
            except Exception as e:
                # Fall back to Python implementation
                print(f"Warning: Failed to create Rust instance: {e}")
                self._rust_instance = None

        # Store kwargs for Python/hybrid rendering
        if self._rust_instance is None:
            for key, value in kwargs.items():
                setattr(self, key, value)

    def render(self) -> str:
        """
        Render component using fastest available method.

        Performance waterfall:
        1. Rust implementation (fastest)
        2. template_string with Rust rendering (fast)
        3. _render_custom() override (flexible)
        """
        # 1. Try pure Rust implementation (fastest)
        if self._rust_instance is not None:
            return mark_safe(self._rust_instance.render())

        # 2. Try hybrid: template_string with Rust rendering (fast)
        if self.template_string is not None:
            from djust._rust import render_template
            context = self.get_context_data()
            return mark_safe(render_template(self.template_string, context))

        # 3. Fall back to custom Python rendering (flexible)
        return mark_safe(self._render_custom())

    def get_context_data(self) -> Dict[str, Any]:
        """
        Override to provide template context for hybrid rendering.

        Returns:
            Dictionary of template variables
        """
        return {}

    def _render_custom(self) -> str:
        """
        Override for custom Python rendering.

        Only called if no Rust implementation and no template_string.

        Returns:
            HTML string
        """
        raise NotImplementedError(
            f"{self.__class__.__name__} must define either:\n"
            f"  - _rust_impl_class (for pure Rust)\n"
            f"  - template_string (for hybrid rendering)\n"
            f"  - _render_custom() method (for custom Python)"
        )

    def __str__(self):
        """Allow {{ component }} in templates to render automatically"""
        return self.render()
```

### Phase 2.5: LiveComponent - Stateful Components

**LiveComponent** extends `Component` to add statefulness and lifecycle management. Use LiveComponent when you need:
- Interactive components with their own state
- Components that respond to user events
- Components that communicate with parent views
- Isolated state management

**Python side** (`python/djust/component.py`):

```python
import uuid
from typing import Dict, Any, Optional, Callable
from djust._rust import RustLiveView

class LiveComponent(Component):
    """
    Stateful component with lifecycle methods and parent-child communication.

    LiveComponent extends Component to provide:
    - **Lifecycle**: mount(), update(), unmount()
    - **State Management**: Isolated component state
    - **Parent Communication**: send_parent() for events up
    - **Props**: Receive updates from parent via update()

    When to use:
    - Todo list with filters
    - User profile editor
    - Data table with sorting
    - Any component with interactive state

    When NOT to use:
    - Simple badges or buttons (use Component)
    - Static content display (use Component)
    - No interactivity needed (use Component)

    Example:
        class TodoListComponent(LiveComponent):
            template_string = '''
                <div class="todo-list">
                    <input type="text" dj-input="on_filter" value="{{ filter }}" />
                    {% for item in filtered_items %}
                    <div>
                        <input type="checkbox" dj-change="toggle_todo" data-id="{{ item.id }}">
                        {{ item.text }}
                    </div>
                    {% endfor %}
                </div>
            '''

            def mount(self, items=None):
                \"\"\"Initialize component state\"\"\"
                self.items = items or []
                self.filter = ""

            def update(self, items=None, **props):
                \"\"\"Called when parent updates props\"\"\"
                if items is not None:
                    self.items = items

            def on_filter(self, value: str = "", **kwargs):
                \"\"\"Event handler for filter input\"\"\"
                self.filter = value

            def toggle_todo(self, id: str = None, **kwargs):
                \"\"\"Event handler for checkbox\"\"\"
                item = next(i for i in self.items if i['id'] == int(id))
                item['completed'] = not item['completed']
                # Notify parent of change
                self.send_parent("todo_toggled", {"id": int(id)})

            def get_context_data(self):
                filtered = [i for i in self.items if self.filter.lower() in i['text'].lower()]
                return {
                    'filter': self.filter,
                    'filtered_items': filtered,
                }
    """

    template_string: str = ""

    def __init__(self, **props):
        """
        Initialize LiveComponent.

        Automatically calls mount() with provided props.
        Generates unique component ID for event routing.
        """
        super().__init__(**props)
        self.component_id = str(uuid.uuid4())
        self._mounted = False
        self._parent_callback: Optional[Callable] = None

        # Call lifecycle method
        self.mount(**props)
        self._mounted = True

    def mount(self, **props):
        """
        Lifecycle: Called when component is first created.

        Override to initialize component state from props.

        Args:
            **props: Initial properties from parent

        Example:
            def mount(self, items=None, user=None):
                self.items = items or []
                self.user = user
                self.selected_id = None
        """
        # Default: set all props as attributes
        for key, value in props.items():
            setattr(self, key, value)

    def update(self, **props):
        """
        Lifecycle: Called when parent updates component props.

        Override to respond to prop changes from parent.

        Args:
            **props: Updated properties from parent

        Example:
            def update(self, items=None, **props):
                if items is not None:
                    self.items = items
                    self.selected_id = None  # Reset selection
        """
        # Default: update attributes
        for key, value in props.items():
            setattr(self, key, value)

    def unmount(self):
        """
        Lifecycle: Called when component is destroyed.

        Override to cleanup resources (timers, connections, etc).

        Example:
            def unmount(self):
                if self.websocket:
                    self.websocket.close()
                super().unmount()
        """
        self._mounted = False
        self._parent_callback = None

    def send_parent(self, event: str, data: Optional[Dict[str, Any]] = None):
        """
        Send event to parent view (events up).

        Use this to notify parent of state changes or user actions.
        Parent receives event via handle_component_event().

        Args:
            event: Event name (e.g., "user_selected", "todo_toggled")
            data: Event data dictionary

        Example:
            # In child component
            def select_user(self, user_id: int):
                self.selected_id = user_id
                self.send_parent("user_selected", {"user_id": user_id})
        """
        if self._parent_callback:
            event_data = {
                "component_id": self.component_id,
                "event": event,
                "data": data or {},
            }
            self._parent_callback(event_data)

    def _set_parent_callback(self, callback: Callable):
        """Internal: Set parent callback for event routing"""
        self._parent_callback = callback

    def render(self) -> str:
        """
        Render component to HTML.

        Wraps output in div with component_id for event routing.
        """
        if not self._mounted:
            raise RuntimeError(
                f"Cannot render unmounted component {self.__class__.__name__}"
            )

        if self._rust_view is None:
            if not self.template_string:
                raise ValueError(
                    f"Component {self.__class__.__name__} must define template_string"
                )
            self._rust_view = RustLiveView(self.template_string)

        context = self.get_context_data()
        self._rust_view.update_state(context)
        html = self._rust_view.render()

        # Wrap in div with component ID for event routing
        return f'<div data-component-id="{self.component_id}">{html}</div>'


# Component vs LiveComponent Decision Tree
"""
Need interactivity? ─┐
                     │
                     ├─ No  → Use Component (stateless)
                     │        - Badge, Button, Icon
                     │        - Display-only cards
                     │        - Simple formatting
                     │
                     └─ Yes → Use LiveComponent (stateful)
                              - Todo lists
                              - Data tables
                              - User editors
                              - Any form with state
"""
```

### Parent-Child Communication Pattern

LiveView coordinates child LiveComponents using **props down, events up** pattern:

**Parent View** (`python/djust/live_view.py`):

```python
class DashboardView(LiveView):
    """
    Parent view coordinates child components.

    Pattern:
    - Props down: Parent updates child via update_component()
    - Events up: Child notifies parent via send_parent()
    """

    def mount(self, request, **kwargs):
        """Initialize parent state and create child components"""
        self.users = [
            {"id": 1, "name": "Alice", "tasks": [...]},
            {"id": 2, "name": "Bob", "tasks": [...]},
        ]
        self.selected_user = None

        # Create child components (props down)
        self.user_list = UserListComponent(users=self.users)
        self.user_detail = UserDetailComponent(user=None)
        self.todo_list = TodoComponent(items=[], user_name="")

    def handle_component_event(self, component_id: str, event: str, data: dict):
        """
        Handle events from child components (events up).

        Automatically called when child calls send_parent().
        """
        if event == "user_selected":
            # Update parent state
            user_id = data["user_id"]
            self.selected_user = next(u for u in self.users if u["id"] == user_id)

            # Update child components (props down)
            self.user_detail.update(user=self.selected_user)
            self.todo_list.update(
                items=self.selected_user["tasks"],
                user_name=self.selected_user["name"]
            )
            self.user_list.update(selected_id=user_id)

        elif event == "todo_toggled":
            # Handle todo toggle event
            todo_id = data["id"]
            # Update state, notify other components, etc.

    def update_component(self, component_id: str, **props):
        """
        Manually update a child component by ID.

        Usually not needed - use component.update() directly.
        """
        component = self._components.get(component_id)
        if component and isinstance(component, LiveComponent):
            component.update(**props)

    def get_context_data(self, **kwargs):
        """
        Provide components to template.

        Components are automatically registered for event handling.
        """
        return {
            'user_list': self.user_list,
            'user_detail': self.user_detail,
            'todo_list': self.todo_list,
            'selected_user': self.selected_user,
        }
```

**Template** (`templates/dashboard.html`):

```html
<div class="dashboard">
    <div class="row">
        <div class="col-md-4">
            <h3>Users</h3>
            {{ user_list.render }}
        </div>

        <div class="col-md-8">
            {% if selected_user %}
                <h3>User Details</h3>
                {{ user_detail.render }}

                <h3>Tasks</h3>
                {{ todo_list.render }}
            {% else %}
                <p>Select a user to view details</p>
            {% endif %}
        </div>
    </div>
</div>
```

**Communication Flow:**

```
1. User clicks in UserListComponent
   ↓
2. UserListComponent calls send_parent("user_selected", {"user_id": 1})
   ↓
3. Parent's handle_component_event() called
   ↓
4. Parent updates state and calls child.update()
   ↓
5. Child components re-render with new props
```

**Key Benefits:**
- **Isolation**: Each component manages its own state
- **Coordination**: Parent orchestrates communication
- **Reusability**: Components don't know about each other
- **Testability**: Components can be tested independently

### Phase 3: Python Wrapper Components

**Python side** (`python/djust/components/ui/badge.py`):

```python
from djust.components.base import Component
from typing import Optional

# Try to import Rust implementation
try:
    from djust._rust import RustBadge
    _RUST_AVAILABLE = True
except ImportError:
    _RUST_AVAILABLE = False
    RustBadge = None


class Badge(Component):
    """
    Badge component with automatic Rust optimization.

    This component automatically uses pure Rust implementation if available,
    otherwise falls back to hybrid rendering.

    Args:
        text: Badge text content
        variant: Color variant (primary, secondary, success, danger, warning, info)
        size: Badge size (sm, md, lg)

    Examples:
        # Simple usage (Rust automatically used if available)
        badge = Badge("New", variant="primary")
        html = badge.render()

        # In template
        {{ badge.render }}  # or just {{ badge }}

        # Multiple badges
        badges = [Badge(f"Item {i}") for i in range(100)]
        # Each renders in ~1μs (Rust) vs ~50μs (Python)
    """

    # Link to Rust implementation if available
    _rust_impl_class = RustBadge if _RUST_AVAILABLE else None

    # Fallback: Hybrid rendering with template_string
    template_string = """
        <span class="badge bg-{{ variant }}{% if size == 'sm' %} badge-sm{% elif size == 'lg' %} badge-lg{% endif %}">
            {{ text }}
        </span>
    """

    def __init__(
        self,
        text: str,
        variant: str = "primary",
        size: str = "md"
    ):
        """
        Initialize badge component.

        Args will be passed to Rust implementation if available,
        otherwise stored for hybrid rendering.
        """
        super().__init__(text=text, variant=variant, size=size)

        # Store for hybrid rendering (if Rust not used)
        self.text = text
        self.variant = variant
        self.size = size

    def get_context_data(self):
        """Context for hybrid rendering (if Rust not available)"""
        return {
            'text': self.text,
            'variant': self.variant,
            'size': self.size,
        }
```

### What This Achieves

From the developer's perspective:

```python
from djust.components.ui import Badge

# Same code, regardless of implementation
badge = Badge("New", variant="primary", size="sm")
html = badge.render()
```

Behind the scenes:
1. **If Rust built**: Uses `RustBadge` (~1μs per render) ⚡
2. **If Rust not built**: Uses `template_string` with Rust template engine (~5μs) ✅
3. **If Rust disabled**: Falls back to Python (custom implementation needed) ⚠️

**Developer experience**: Write once, automatic optimization, graceful degradation.

## Rust Component Library

### Recommended Core Components

Implement these in Rust for maximum performance:

| Component | Frequency | Benefit |
|-----------|-----------|---------|
| **Badge** | Very High | Used in lists, tables, status indicators |
| **Button** | High | Used throughout UI |
| **Icon** | Very High | Used everywhere, often 10-50 per page |
| **Spinner** | Medium | Simple, benefits from Rust |
| **Alert** | Medium | Common UI element |
| **Separator** | High | Used in lists, menus |
| **Tag** | High | Similar to Badge, high frequency |

### Rust Implementation Template

```rust
// crates/djust/src/components/button.rs
use pyo3::prelude::*;

#[pyclass(name = "RustButton")]
#[derive(Clone)]
pub struct Button {
    text: String,
    variant: String,
    size: String,
    disabled: bool,
    framework: String,
}

#[pymethods]
impl Button {
    #[new]
    #[pyo3(signature = (text, variant=None, size=None, disabled=None, framework=None))]
    fn new(
        text: String,
        variant: Option<String>,
        size: Option<String>,
        disabled: Option<bool>,
        framework: Option<String>,
    ) -> Self {
        Self {
            text,
            variant: variant.unwrap_or_else(|| "primary".to_string()),
            size: size.unwrap_or_else(|| "md".to_string()),
            disabled: disabled.unwrap_or(false),
            framework: framework.unwrap_or_else(|| "bootstrap5".to_string()),
        }
    }

    fn render(&self) -> String {
        match self.framework.as_str() {
            "bootstrap5" => self.render_bootstrap(),
            "tailwind" => self.render_tailwind(),
            _ => self.render_plain(),
        }
    }

    fn render_bootstrap(&self) -> String {
        let size_class = match self.size.as_str() {
            "sm" => " btn-sm",
            "lg" => " btn-lg",
            _ => "",
        };

        let disabled_attr = if self.disabled { " disabled" } else { "" };

        format!(
            r#"<button type="button" class="btn btn-{}{}"{}>{}"></button>"#,
            self.variant,
            size_class,
            disabled_attr,
            html_escape::encode_text(&self.text)
        )
    }

    fn render_tailwind(&self) -> String {
        let size_classes = match self.size.as_str() {
            "sm" => "px-3 py-1.5 text-sm",
            "lg" => "px-6 py-3 text-lg",
            _ => "px-4 py-2 text-base",
        };

        let color_classes = match self.variant.as_str() {
            "primary" => "bg-blue-600 hover:bg-blue-700 text-white",
            "secondary" => "bg-gray-600 hover:bg-gray-700 text-white",
            "success" => "bg-green-600 hover:bg-green-700 text-white",
            "danger" => "bg-red-600 hover:bg-red-700 text-white",
            _ => "bg-blue-600 hover:bg-blue-700 text-white",
        };

        let disabled_classes = if self.disabled {
            " opacity-50 cursor-not-allowed"
        } else {
            ""
        };

        format!(
            r#"<button type="button" class="rounded font-medium {} {}{}"{}>{}</button>"#,
            size_classes,
            color_classes,
            disabled_classes,
            if self.disabled { " disabled" } else { "" },
            html_escape::encode_text(&self.text)
        )
    }

    fn render_plain(&self) -> String {
        let disabled_attr = if self.disabled { " disabled" } else { "" };
        format!(
            r#"<button class="button button-{} button-{}"{}>{}"></button>"#,
            self.variant,
            self.size,
            disabled_attr,
            html_escape::encode_text(&self.text)
        )
    }
}
```

### Exposing Rust Components to Python

**Cargo.toml** (`crates/djust/Cargo.toml`):

```toml
[dependencies]
pyo3 = { version = "0.20", features = ["extension-module"] }
html-escape = "0.2"
```

**Main lib.rs** (`crates/djust/src/lib.rs`):

```rust
use pyo3::prelude::*;

mod components;

#[pymodule]
fn _rust(_py: Python, m: &PyModule) -> PyResult<()> {
    // Register core Rust components
    components::register_components(_py, m)?;

    // ... other Rust functionality
    Ok(())
}
```

## Usage Examples

### Example 1: Simple Badge Usage

```python
from djust.components.ui import Badge

# Developer writes simple code
badge = Badge("New", variant="success", size="sm")

# Behind the scenes:
# - If Rust built: RustBadge renders in ~1μs
# - If not: template_string renders in ~5μs
# Developer doesn't need to know or care!

html = badge.render()
# Output: <span class="badge bg-success badge-sm">New</span>
```

### Example 2: Rendering Many Badges

```python
from djust.components.ui import Badge

class ProductListView(LiveView):
    def mount(self, request):
        self.products = Product.objects.all()

    def get_context_data(self):
        return {
            'products': self.products,
            # Create 100 badge components
            'badges': [
                Badge(product.status, variant=self._status_variant(product))
                for product in self.products
            ]
        }

    def _status_variant(self, product):
        return {
            'active': 'success',
            'pending': 'warning',
            'inactive': 'secondary'
        }.get(product.status, 'secondary')
```

**Template:**

```html
<ul>
{% for product in products %}
    <li>
        {{ product.name }}
        {{ badges.forloop.counter0.render }}
    </li>
{% endfor %}
</ul>
```

**Performance:**
- Pure Rust: 100 badges × 1μs = 0.1ms ⚡
- Hybrid: 100 badges × 5μs = 0.5ms ✅
- Pure Python: 100 badges × 50μs = 5ms ⚠️

### Example 3: Custom Component with Hybrid Rendering

```python
from djust.components.base import Component

class UserAvatar(Component):
    """
    Custom component using hybrid rendering.

    No Rust implementation needed - uses template_string for speed.
    """

    template_string = """
        <div class="avatar avatar-{{ size }}">
            {% if image_url %}
                <img src="{{ image_url }}" alt="{{ name }}">
            {% else %}
                <div class="avatar-placeholder">{{ initials }}</div>
            {% endif %}
        </div>
    """

    def __init__(self, name: str, image_url: str = None, size: str = "md"):
        super().__init__(name=name, image_url=image_url, size=size)
        self.name = name
        self.image_url = image_url
        self.size = size

    def get_context_data(self):
        return {
            'name': self.name,
            'image_url': self.image_url,
            'size': self.size,
            'initials': self._get_initials(),
        }

    def _get_initials(self) -> str:
        """Python logic for computing initials"""
        parts = self.name.split()
        return ''.join(p[0].upper() for p in parts[:2])
```

### Example 4: Fully Custom Python Component

```python
from djust.components.base import Component

class DynamicChart(Component):
    """
    Complex component requiring full Python flexibility.

    No Rust implementation, no template - custom render() logic.
    """

    def __init__(self, data: list, chart_type: str = "bar"):
        super().__init__(data=data, chart_type=chart_type)
        self.data = data
        self.chart_type = chart_type

    def _render_custom(self):
        """Full Python control over rendering"""
        # Complex logic: fetch config, process data, etc.
        framework = config.get('css_framework')

        if self.chart_type == "bar":
            return self._render_bar_chart(framework)
        elif self.chart_type == "line":
            return self._render_line_chart(framework)
        else:
            return self._render_pie_chart(framework)

    def _render_bar_chart(self, framework: str):
        # Complex Python logic
        max_value = max(self.data) if self.data else 1
        bars = []

        for value in self.data:
            height = (value / max_value) * 100
            bars.append(f'<div class="bar" style="height: {height}%"></div>')

        return f'<div class="chart chart-bar">{"".join(bars)}</div>'

    # ... more complex rendering methods
```

## Migration Path

### Phase 1: Start with Current Design

Implement `Component` base class with:
- `template_string` support (hybrid)
- `_render_custom()` fallback (Python)

```python
class Component(ABC):
    template_string: Optional[str] = None

    def render(self):
        if self.template_string:
            return render_template(self.template_string, self.get_context_data())
        return self._render_custom()
```

### Phase 2: Add Rust Component Support

Update `Component` to check for Rust implementation:

```python
class Component(ABC):
    _rust_impl_class: Optional[type] = None
    template_string: Optional[str] = None

    def render(self):
        # Try Rust first
        if self._rust_instance:
            return self._rust_instance.render()
        # Fall back to hybrid
        if self.template_string:
            return render_template(self.template_string, self.get_context_data())
        # Fall back to Python
        return self._render_custom()
```

### Phase 3: Implement Core Rust Components

Create Rust implementations:
1. Badge → RustBadge
2. Button → RustButton
3. Icon → RustIcon
4. Spinner → RustSpinner

Link them in Python wrappers:

```python
from djust._rust import RustBadge

class Badge(Component):
    _rust_impl_class = RustBadge
    template_string = "..."  # Fallback
```

### Phase 4: Optimize Hot Paths

Profile application, identify components used 100+ times per page, implement in Rust.

## Summary

The unified design gives you:

1. **Single API**: Developers use `Component` consistently
2. **Automatic Optimization**: Framework chooses fastest implementation
3. **Graceful Degradation**: Falls back if Rust not available
4. **Progressive Enhancement**: Start Python, optimize to Rust later
5. **Maximum Performance**: Core components in Rust (50-100x faster)

**Implementation order:**
1. ✅ Document unified design
2. ✅ Implement `Component` base class with hybrid support
3. ✅ Implement `LiveComponent` with lifecycle and communication (Phase 4)
4. Implement core Rust components (Badge, Button, Icon)
5. Add automatic Rust detection to base class
6. Profile and optimize hot paths

This gives you the best of all worlds: Python's flexibility, Rust's performance, and a seamless developer experience.

---

## Two-Tier Component System

djust provides a **two-tier component system** optimized for both simplicity and power:

### Tier 1: Component (Stateless)

**Use for**: Presentation, display, simple UI elements

**Characteristics**:
- No state management
- No lifecycle methods
- No event handlers
- Pure rendering (Rust → Hybrid → Python)
- Zero overhead

**Examples**:
```python
# Badge component
badge = Badge("New", variant="primary")

# Button component
button = Button("Click me", size="lg")

# Custom display component
class UserAvatar(Component):
    template_string = '''
        <div class="avatar">
            <img src="{{ image_url }}" alt="{{ name }}">
        </div>
    '''
```

### Tier 2: LiveComponent (Stateful)

**Use for**: Interactive components, complex state, parent-child communication

**Characteristics**:
- Full lifecycle (mount/update/unmount)
- Event handlers
- Isolated state management
- Parent-child communication (send_parent)
- Rust-powered rendering

**Examples**:
```python
# Todo list component
class TodoListComponent(LiveComponent):
    template_string = '''...'''

    def mount(self, items=None):
        self.items = items or []
        self.filter = ""

    def update(self, items=None, **props):
        if items is not None:
            self.items = items

    def on_filter(self, value: str = "", **kwargs):
        self.filter = value

    def toggle_todo(self, id: str = None, **kwargs):
        item = next(i for i in self.items if i['id'] == int(id))
        item['completed'] = not item['completed']
        self.send_parent("todo_toggled", {"id": int(id)})
```

### Decision Matrix

| Scenario | Component Type | Reason |
|----------|---------------|--------|
| Display badge/icon/button | `Component` | No state needed |
| Show user avatar | `Component` | Just displays data |
| Format currency | `Component` | Pure presentation |
| Todo list with filters | `LiveComponent` | Has state + interaction |
| User profile editor | `LiveComponent` | Complex state management |
| Data table with sorting | `LiveComponent` | Interactive state |
| Search with debounce | `LiveComponent` | Stateful behavior |
| Dropdown menu | Could be either | Simple → Component, Complex → LiveComponent |

### Performance Characteristics

**Component**:
- Creation: <1μs (no overhead)
- Rendering: 1μs (Rust) → 5μs (Hybrid) → 50μs (Python)
- Memory: Minimal (no state tracking)

**LiveComponent**:
- Creation: ~5μs (lifecycle + ID generation)
- Rendering: 5-10μs (Rust template + wrapping)
- Memory: ~1KB per component (state + VDOM)
- Update: <1ms (isolated VDOM diffing)

### Best Practices

1. **Start Simple**: Begin with `Component`, upgrade to `LiveComponent` when you need state
2. **Composition**: Build complex UIs from simple components
3. **Isolation**: Each `LiveComponent` manages its own state independently
4. **Communication**: Use props down, events up pattern
5. **Performance**: Use `Component` for high-frequency renders (100+ per page)

### Component Lifecycle Summary

```
Component (Stateless):
    __init__() → render() → done

LiveComponent (Stateful):
    __init__() → mount() → [update()* → render()*] → unmount()
                             ↑         ↓
                             └─ events ┘
```

### Real-World Example

```python
class DashboardView(LiveView):
    """Mix Component and LiveComponent for optimal performance"""

    def mount(self, request):
        # Stateless components (fast, simple)
        self.header_badge = Badge("Admin", variant="primary")
        self.status_icon = Icon("check-circle", color="success")

        # Stateful components (interactive)
        self.user_list = UserListComponent(users=User.objects.all())
        self.todo_list = TodoListComponent(items=[])

    def get_context_data(self):
        return {
            # Mix stateless and stateful components
            'header_badge': self.header_badge,  # Fast rendering
            'status_icon': self.status_icon,    # Fast rendering
            'user_list': self.user_list,        # Interactive
            'todo_list': self.todo_list,        # Interactive
        }
```

**Result**: Optimal performance with simple, maintainable code.

---

## Status

**Phase 4 Complete (2025-11-12)**:
- ✅ `Component` base class with performance waterfall
- ✅ `LiveComponent` with full lifecycle
- ✅ Parent-child communication (props down, events up)
- ✅ Automatic component registration
- ✅ State isolation
- ✅ 28 passing tests
- ✅ Complete demo application

**Next Steps**:
- Implement core Rust components (Badge, Button, Icon)
- Add {% component %} template tag
- Optimize VDOM for component trees
- Performance profiling and optimization
