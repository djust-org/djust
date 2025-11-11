# Unified Component Design: Automatic Performance Optimization

This document describes the **unified component design** where a single `Component` class automatically selects the fastest available implementation: Pure Rust → Hybrid → Python.

## Table of Contents

1. [Design Philosophy](#design-philosophy)
2. [Implementation Strategy](#implementation-strategy)
3. [Rust Component Library](#rust-component-library)
4. [Unified Component Base Class](#unified-component-base-class)
5. [Usage Examples](#usage-examples)
6. [Migration Path](#migration-path)

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
2. Implement `Component` base class with hybrid support
3. Implement core Rust components (Badge, Button, Icon)
4. Add automatic Rust detection to base class
5. Profile and optimize hot paths

This gives you the best of all worlds: Python's flexibility, Rust's performance, and a seamless developer experience.
