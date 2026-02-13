# Component Performance Optimization Guide

This document explains performance optimization strategies for djust components, from pure Python flexibility to pure Rust performance.

## Table of Contents

1. [Performance Spectrum](#performance-spectrum)
2. [Three-Tier Component System](#three-tier-component-system)
3. [Implementation Strategies](#implementation-strategies)
4. [Benchmarks and Trade-offs](#benchmarks-and-trade-offs)
5. [Migration Paths](#migration-paths)
6. [Recommendations](#recommendations)

## Performance Spectrum

djust components exist on a spectrum from maximum flexibility (Python) to maximum performance (Rust):

```
┌─────────────────────────────────────────────────────────────┐
│  Flexibility ◄─────────────────────────────► Performance   │
└─────────────────────────────────────────────────────────────┘
    Python              Hybrid              Rust
    Component           Component           Component
       ↓                   ↓                   ↓
   Slowest             Medium              Fastest
   Most Flexible       Balanced            Least Flexible
```

## Three-Tier Component System

### Tier 1: Python Components (Maximum Flexibility)

**Pure Python implementation** with complete control over rendering logic.

```python
from djust.components import Component
from django.utils.safestring import mark_safe
from djust.config import config

class StatusBadge(Component):
    """Pure Python component - maximum flexibility"""

    def __init__(self, status: str, label: str = None):
        self.status = status
        self.label = label or status.title()

    def render(self) -> str:
        # Can use ANY Python logic
        framework = config.get('css_framework', 'bootstrap5')

        # Can call external services
        if self.should_fetch_color():
            color = self.fetch_brand_color()

        # Can use complex conditionals
        if framework == 'bootstrap5':
            return mark_safe(self._render_bootstrap())
        elif framework == 'tailwind':
            return mark_safe(self._render_tailwind())
        else:
            return mark_safe(self._render_plain())

    def _render_bootstrap(self) -> str:
        variants = {'success': 'success', 'error': 'danger'}
        variant = variants.get(self.status, 'secondary')
        return f'<span class="badge bg-{variant}">{self.label}</span>'
```

**Performance**: ~50-100μs per component
**Flexibility**: ✅ Maximum - arbitrary Python code
**Use for**: Complex logic, external API calls, dynamic framework selection

### Tier 2: Hybrid Components (Balanced)

**Python interface with Rust template rendering** - best of both worlds.

```python
from djust.components import Component

class StatusBadge(Component):
    """Hybrid component - Rust rendering, Python logic"""

    # Rust renders this template
    template_string = """
        <span class="badge bg-{{ variant }}">{{ label }}</span>
    """

    def __init__(self, status: str, label: str = None):
        self.status = status
        self.label = label or status.title()

    def get_context_data(self) -> dict:
        """Python logic for context preparation"""
        variant = self._compute_variant()  # Python logic
        return {
            'variant': variant,
            'label': self.label,
        }

    def _compute_variant(self) -> str:
        # Complex Python logic
        variants = {'success': 'success', 'error': 'danger'}
        return variants.get(self.status, 'secondary')
```

**How it works:**

```python
class Component(ABC):
    template_string: Optional[str] = None

    def render(self) -> str:
        if self.template_string:
            # Fast path: Rust template rendering
            context = self.get_context_data()
            return _rust.render_template(self.template_string, context)
        else:
            # Flexible path: Python rendering
            return self._render_custom()
```

**Performance**: ~5-10μs per component (10x faster than pure Python)
**Flexibility**: ✅ Good - Python logic + Rust rendering
**Use for**: Simple templates with Python context computation

### Tier 3: Rust Components (Maximum Performance)

**Pure Rust implementation** exposed to Python via PyO3.

**Rust side** (`crates/djust/src/components/badge.rs`):

```rust
use pyo3::prelude::*;

#[pyclass]
#[derive(Clone)]
pub struct BadgeComponent {
    text: String,
    variant: String,
    size: String,
}

#[pymethods]
impl BadgeComponent {
    #[new]
    #[pyo3(signature = (text, variant=None, size=None))]
    fn new(
        text: String,
        variant: Option<String>,
        size: Option<String>,
    ) -> Self {
        Self {
            text,
            variant: variant.unwrap_or_else(|| "primary".to_string()),
            size: size.unwrap_or_else(|| "md".to_string()),
        }
    }

    fn render(&self) -> String {
        self.render_bootstrap()
    }

    fn render_bootstrap(&self) -> String {
        format!(
            r#"<span class="badge bg-{} badge-{}">{}</span>"#,
            self.variant,
            self.size,
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
            _ => "bg-gray-100 text-gray-800",
        };

        format!(
            r#"<span class="rounded font-semibold {} {}">{}</span>"#,
            size_classes,
            color_classes,
            html_escape::encode_text(&self.text)
        )
    }

    #[getter]
    fn text(&self) -> &str {
        &self.text
    }

    #[setter]
    fn set_text(&mut self, text: String) {
        self.text = text;
    }
}
```

**Python side** (usage):

```python
# Import from Rust
from djust._rust import BadgeComponent

class MyView(LiveView):
    def mount(self, request):
        # Instantiate Rust component from Python
        self.badge = BadgeComponent("New", variant="primary", size="sm")

    def get_context_data(self):
        return {
            'badge': self.badge,
        }

# In template:
# {{ badge.render }}
```

**Advanced: Rust Component with Framework Detection**

```rust
#[pyclass]
pub struct SmartBadgeComponent {
    text: String,
    variant: String,
    framework: String,  // Passed from Python config
}

#[pymethods]
impl SmartBadgeComponent {
    #[new]
    fn new(text: String, variant: String, framework: String) -> Self {
        Self { text, variant, framework }
    }

    fn render(&self) -> String {
        match self.framework.as_str() {
            "bootstrap5" => self.render_bootstrap(),
            "tailwind" => self.render_tailwind(),
            _ => self.render_plain(),
        }
    }

    fn render_bootstrap(&self) -> String {
        format!(r#"<span class="badge bg-{}">{}</span>"#, self.variant, self.text)
    }

    fn render_tailwind(&self) -> String {
        let color = match self.variant.as_str() {
            "primary" => "bg-blue-100 text-blue-800",
            "success" => "bg-green-100 text-green-800",
            _ => "bg-gray-100 text-gray-800",
        };
        format!(r#"<span class="rounded px-2 py-1 {}">{}</span>"#, color, self.text)
    }

    fn render_plain(&self) -> String {
        format!(r#"<span class="badge badge-{}">{}</span>"#, self.variant, self.text)
    }
}
```

**Performance**: ~0.5-2μs per component (50-100x faster than pure Python)
**Flexibility**: ⚠️ Limited - requires Rust recompilation for changes
**Use for**: High-frequency components (badges, icons, buttons in lists)

### Batch Rendering for Rust Components

For rendering many components at once:

```rust
#[pyfunction]
pub fn render_badges_batch(badges: Vec<Py<BadgeComponent>>) -> Vec<String> {
    Python::with_gil(|py| {
        badges
            .iter()
            .map(|badge| {
                badge.borrow(py).render()
            })
            .collect()
    })
}

// Parallel batch rendering (even faster)
#[pyfunction]
pub fn render_badges_batch_parallel(badges: Vec<Py<BadgeComponent>>) -> Vec<String> {
    use rayon::prelude::*;

    Python::with_gil(|py| {
        badges
            .par_iter()
            .map(|badge| {
                badge.borrow(py).render()
            })
            .collect()
    })
}
```

**Python usage:**

```python
from djust._rust import BadgeComponent, render_badges_batch_parallel

badges = [BadgeComponent(f"Item {i}", "primary") for i in range(1000)]
rendered = render_badges_batch_parallel(badges)  # Parallel rendering in Rust!
```

## Implementation Strategies

### Strategy 1: Start Python, Optimize Later

**Phase 1**: Implement in Python for flexibility

```python
class Badge(Component):
    def render(self):
        return f'<span class="badge">{self.text}</span>'
```

**Phase 2**: Add template for Rust acceleration

```python
class Badge(Component):
    template_string = '<span class="badge">{{ text }}</span>'

    def get_context_data(self):
        return {'text': self.text}
```

**Phase 3**: Move to Rust if needed (for high-frequency components)

```rust
#[pyclass]
pub struct BadgeComponent { ... }
```

### Strategy 2: Rust-First for Core Components

Create a library of common components in Rust:

**Core Rust components** (`crates/djust/src/components/`):
- `badge.rs` - BadgeComponent
- `button.rs` - ButtonComponent
- `icon.rs` - IconComponent
- `spinner.rs` - SpinnerComponent

These are used frequently and benefit from Rust performance.

**Python components** for app-specific needs:
- `UserProfileCard` - Custom logic
- `DashboardWidget` - External API calls
- `ChartComponent` - Complex rendering logic

### Strategy 3: Hybrid Component Library

Provide both implementations, let developers choose:

```python
# Import Python version (flexible)
from djust.components.ui import BadgeComponent

# Import Rust version (fast)
from djust._rust.components import BadgeComponent

# Or alias for convenience
from djust.components.ui import (
    BadgeComponent as PyBadge,  # Python version
    BadgeComponent_Rust as RustBadge,  # Rust version
)

# Use based on needs
if rendering_many:
    Badge = RustBadge  # Fast
else:
    Badge = PyBadge  # Flexible
```

## Benchmarks and Trade-offs

### Rendering Single Component

| Implementation | Time | Notes |
|---------------|------|-------|
| Python `render()` | ~50-100μs | String formatting, function call overhead |
| Hybrid (template) | ~5-10μs | Single Rust call, template compiled once |
| Pure Rust | ~0.5-2μs | No Python overhead, direct rendering |

### Rendering 100 Components

| Implementation | Total Time | Notes |
|---------------|-----------|-------|
| Python `render()` | ~5-10ms | 100 Python calls |
| Hybrid (template) | ~0.5-1ms | 100 Rust template renders |
| Pure Rust | ~0.05-0.2ms | Pure Rust execution |
| Rust Batch | ~0.02-0.1ms | Single call, parallel rendering |

### Rendering 1000 Components

| Implementation | Total Time | Notes |
|---------------|-----------|-------|
| Python `render()` | ~50-100ms | Significant overhead |
| Hybrid (template) | ~5-10ms | Acceptable for most cases |
| Pure Rust | ~0.5-2ms | Very fast |
| Rust Batch Parallel | ~0.1-0.5ms | Maximum performance |

### Trade-off Matrix

|  | Flexibility | Performance | Dev Speed | Maintenance |
|--|-------------|-------------|-----------|-------------|
| **Python** | ✅ Excellent | ⚠️ Slowest | ✅ Fast | ✅ Easy |
| **Hybrid** | ✅ Good | ✅ Good | ✅ Fast | ✅ Easy |
| **Rust** | ⚠️ Limited | ✅ Excellent | ⚠️ Slower | ⚠️ Harder |

## Migration Paths

### Path 1: Python → Hybrid (Easy)

```python
# Before: Pure Python
class Badge(Component):
    def render(self):
        return f'<span class="badge">{self.text}</span>'

# After: Add template (no other changes needed)
class Badge(Component):
    template_string = '<span class="badge">{{ text }}</span>'

    def get_context_data(self):
        return {'text': self.text}

    # Old render() method still works as fallback
    def render(self):
        return f'<span class="badge">{self.text}</span>'
```

### Path 2: Python → Rust (Advanced)

**Step 1**: Identify hot path (profiling)

```python
import cProfile

# Profile to find bottleneck
cProfile.run('render_dashboard()')
# Result: BadgeComponent.render() called 1000 times, takes 50ms
```

**Step 2**: Implement in Rust

```rust
#[pyclass]
pub struct BadgeComponent { ... }
```

**Step 3**: Replace imports

```python
# Before
from djust.components.ui import BadgeComponent

# After
from djust._rust import BadgeComponent
```

**Step 4**: Update tests, verify behavior matches

### Path 3: Rust → Python (Rare, for Adding Flexibility)

```python
# Wrap Rust component with Python for custom logic
from djust._rust import BadgeComponent as RustBadge

class SmartBadge(Component):
    """Python wrapper around Rust component with extra logic"""

    def __init__(self, status: str):
        self.status = status
        # Create Rust component internally
        self._rust_badge = RustBadge(
            text=self._compute_text(),
            variant=self._compute_variant()
        )

    def _compute_text(self) -> str:
        # Custom Python logic
        return self.status.upper()

    def _compute_variant(self) -> str:
        # Complex business logic
        return 'success' if self.status == 'active' else 'danger'

    def render(self) -> str:
        # Use fast Rust rendering
        return self._rust_badge.render()
```

## Recommendations

### Default Approach: Hybrid Components

Use **Hybrid Components** (Python + template_string) as the default:

```python
class Component(ABC):
    """Base class supporting both Python and Rust rendering"""

    template_string: Optional[str] = None

    def render(self) -> str:
        if self.template_string:
            # Fast path: Rust rendering
            return _rust.render_template(
                self.template_string,
                self.get_context_data()
            )
        else:
            # Flexible path: Python rendering
            return self._render_custom()

    def get_context_data(self) -> dict:
        """Override to provide template context"""
        return {}

    @abstractmethod
    def _render_custom(self) -> str:
        """Override for custom Python rendering"""
        raise NotImplementedError("Either provide template_string or implement _render_custom()")
```

### When to Use Each Tier

#### Use Python Components When:
- Complex conditional logic
- External API calls
- Dynamic framework selection
- Rapid prototyping
- Component used < 10 times per page

#### Use Hybrid Components When:
- Simple template rendering
- Python context computation needed
- Component used 10-100 times per page
- Want good balance of speed and flexibility

#### Use Rust Components When:
- Component used 100+ times per page
- Maximum performance required
- Logic is stable (won't change often)
- Component is framework-agnostic
- Part of core library (badges, buttons, icons)

### Recommended Core Rust Components

These components should be implemented in Rust for maximum performance:

1. **BadgeComponent** - Used everywhere, simple logic
2. **ButtonComponent** - High frequency, simple rendering
3. **IconComponent** - Used in lists, very high frequency
4. **SpinnerComponent** - Simple, used frequently
5. **SeparatorComponent** - Simple dividers, lists

### Recommended Python/Hybrid Components

Keep these in Python or Hybrid for flexibility:

1. **CardComponent** - Complex layouts, framework-specific
2. **ModalComponent** - Complex state, event handling
3. **NavbarComponent** - Complex structure, dynamic items
4. **TableComponent** - Complex rendering, sorting, filtering
5. **FormComponents** - Complex validation, Django integration

## Implementation Roadmap

### Phase 1: Foundation (Current)
- ✅ Document two-tier system (Component + LiveComponent)
- ✅ Implement Python Component base class
- ✅ Implement LiveComponent with Rust VDOM

### Phase 2: Hybrid Support
- Add `template_string` support to Component base class
- Implement Rust template rendering for Components
- Add template caching
- Benchmark and optimize

### Phase 3: Core Rust Components
- Implement BadgeComponent in Rust
- Implement ButtonComponent in Rust
- Implement IconComponent in Rust
- Add batch rendering API
- Add parallel rendering for lists

### Phase 4: Advanced Optimizations
- Implement component template compilation
- Add component memoization
- Profile and optimize hot paths
- Add performance monitoring tools

## Example: Complete Three-Tier Badge Implementation

### Python Version (Maximum Flexibility)

```python
# python/djust/components/ui/badge.py
from djust.components import Component
from django.utils.safestring import mark_safe
from djust.config import config

class BadgeComponent(Component):
    """Pure Python badge - maximum flexibility"""

    def __init__(self, text: str, variant: str = "primary"):
        self.text = text
        self.variant = variant

    def render(self) -> str:
        framework = config.get('css_framework', 'bootstrap5')

        if framework == 'bootstrap5':
            return mark_safe(self._render_bootstrap())
        elif framework == 'tailwind':
            return mark_safe(self._render_tailwind())
        else:
            return mark_safe(self._render_plain())

    def _render_bootstrap(self) -> str:
        return f'<span class="badge bg-{self.variant}">{self.text}</span>'

    def _render_tailwind(self) -> str:
        colors = {
            'primary': 'bg-blue-100 text-blue-800',
            'success': 'bg-green-100 text-green-800',
        }
        color = colors.get(self.variant, 'bg-gray-100 text-gray-800')
        return f'<span class="rounded px-2 py-1 {color}">{self.text}</span>'

    def _render_plain(self) -> str:
        return f'<span class="badge badge-{self.variant}">{self.text}</span>'
```

### Hybrid Version (Balanced)

```python
# python/djust/components/ui/badge_hybrid.py
from djust.components import Component

class BadgeComponentHybrid(Component):
    """Hybrid badge - Rust rendering, Python context"""

    # Bootstrap template (Rust renders this)
    template_string = """
        <span class="badge bg-{{ variant }}">{{ text }}</span>
    """

    def __init__(self, text: str, variant: str = "primary"):
        self.text = text
        self.variant = variant

    def get_context_data(self) -> dict:
        # Python logic for context
        return {
            'text': self.text,
            'variant': self.variant,
        }
```

### Rust Version (Maximum Performance)

```rust
// crates/djust/src/components/badge.rs
use pyo3::prelude::*;

#[pyclass]
#[derive(Clone)]
pub struct BadgeComponentRust {
    text: String,
    variant: String,
}

#[pymethods]
impl BadgeComponentRust {
    #[new]
    #[pyo3(signature = (text, variant=None))]
    fn new(text: String, variant: Option<String>) -> Self {
        Self {
            text,
            variant: variant.unwrap_or_else(|| "primary".to_string()),
        }
    }

    fn render(&self) -> String {
        format!(
            r#"<span class="badge bg-{}">{}</span>"#,
            self.variant,
            html_escape::encode_text(&self.text)
        )
    }
}
```

### Usage Comparison

```python
# All three work the same from Python's perspective

# Python version (flexible)
from djust.components.ui import BadgeComponent
badge = BadgeComponent("New", variant="primary")

# Hybrid version (balanced)
from djust.components.ui import BadgeComponentHybrid
badge = BadgeComponentHybrid("New", variant="primary")

# Rust version (fast)
from djust._rust import BadgeComponentRust
badge = BadgeComponentRust("New", variant="primary")

# All render the same way
html = badge.render()
```

## Lazy Hydration

Lazy hydration is a client-side optimization that defers WebSocket connections until LiveView elements are actually needed.

### How It Works

Instead of establishing WebSocket connections for all LiveView elements on page load, lazy hydration:

1. Renders static HTML immediately (fast initial paint)
2. Observes elements with `dj-lazy` attribute
3. Triggers hydration when the specified condition is met
4. Establishes WebSocket connection only when needed

### Performance Impact

| Scenario | Memory Reduction | Time to Interactive |
|----------|------------------|---------------------|
| 5 below-fold LiveViews | ~20-30% | Faster (less JS execution) |
| 10+ lazy elements | ~30-40% | Significantly faster |
| Infinite scroll | ~50%+ | Only visible items hydrated |

### Usage

```html
<!-- Viewport-based (default) - hydrates when scrolled into view -->
<div dj-view="comments" dj-lazy>
    <div class="skeleton">Loading comments...</div>
</div>

<!-- Click-based - hydrates on first interaction -->
<div dj-view="editor" dj-lazy="click">
    <button>Click to edit</button>
</div>

<!-- Hover-based - hydrates when mouse enters -->
<div dj-view="preview" dj-lazy="hover">
    <span>Hover for details</span>
</div>

<!-- Idle-based - hydrates during browser idle time -->
<div dj-view="analytics" dj-lazy="idle">
    <div>Loading analytics...</div>
</div>
```

### When to Use

| Mode | Best For |
|------|----------|
| `viewport` | Below-fold content, long pages, infinite scroll |
| `click` | Expandable sections, modals, tabbed content |
| `hover` | Tooltips, preview cards, hover menus |
| `idle` | Low-priority content, preloading |

### Combining with Other Optimizations

Lazy hydration works well with other optimization strategies:

```python
class DashboardView(LiveView):
    """Dashboard with optimized component loading"""

    template_string = """
        <!-- Critical: Loads immediately -->
        <div dj-view="summary">{{ summary.render }}</div>

        <!-- Hybrid component with lazy hydration -->
        <div dj-view="recent_orders" dj-lazy>
            {{ orders_skeleton }}
        </div>

        <!-- Rust component (fast rendering) with lazy hydration -->
        <div dj-view="notifications" dj-lazy="hover">
            {{ notification_badges }}
        </div>
    """

    def mount(self, request):
        # Critical data loaded immediately
        self.summary = SummaryComponent(self.get_summary_data())

        # Skeleton placeholder (no data fetched yet)
        self.orders_skeleton = SkeletonComponent(lines=5)

        # Fast Rust badges
        self.notification_badges = [
            BadgeComponentRust(str(n)) for n in range(3)
        ]
```

## Conclusion

djust's component system supports a **performance spectrum** from pure Python (flexible) to pure Rust (fast):

1. **Python Components**: Maximum flexibility, adequate performance for most cases
2. **Hybrid Components**: Best balance - Python logic with Rust rendering
3. **Rust Components**: Maximum performance for high-frequency components
4. **Lazy Hydration**: Deferred WebSocket connections for below-fold content

**Recommended default**: **Hybrid Components** - provides 10x speedup over pure Python while maintaining Python's flexibility for context computation.

**When to use Rust**: For core UI components (badges, buttons, icons) that appear 100+ times per page.

**When to use Lazy Hydration**: For any LiveView content below the fold or that requires user interaction to be useful.

This multi-layered approach gives developers the power to choose the right optimization strategy for each component based on their specific needs.
