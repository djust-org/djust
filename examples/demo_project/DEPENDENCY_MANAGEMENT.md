# Automatic Dependency Management System

A declarative, automatic system for managing JavaScript and CSS dependencies in djust components.

## Table of Contents

- [Problem Statement](#problem-statement)
- [Solution Overview](#solution-overview)
- [How It Works](#how-it-works)
- [Quick Start](#quick-start)
- [Architecture](#architecture)
- [Usage Guide](#usage-guide)
- [Extending the System](#extending-the-system)
- [LiveView Integration](#liveview-integration)
- [API Reference](#api-reference)
- [Examples](#examples)

## Problem Statement

### Before: Manual Dependency Management ❌

When using components that require external JavaScript or CSS libraries (like syntax highlighting, charts, date pickers), developers had to:

1. **Remember** which dependencies each component needs
2. **Manually include** `<link>` and `<script>` tags in templates
3. **Copy/paste** initialization code in every template
4. **Risk duplicates** when multiple components need the same library
5. **Maintain** 40+ lines of boilerplate per template

**Example (OLD approach):**

```html
<!-- counter.html - BEFORE -->
{% block syntax_highlighting %}
<link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/highlight.js/11.9.0/styles/atom-one-dark.min.css">
{% endblock %}

{% block extra_js %}
<script src="https://cdnjs.cloudflare.com/ajax/libs/highlight.js/11.9.0/highlight.min.js"></script>
<script>
    // 40+ lines of initialization code
    function highlightCodeBlocks() {
        if (typeof hljs !== 'undefined') {
            document.querySelectorAll('pre code:not(.hljs)').forEach(function(block) {
                hljs.highlightElement(block);
            });
        }
    }

    window.addEventListener('load', function() {
        highlightCodeBlocks();

        // MutationObserver for LiveView updates...
        const observer = new MutationObserver(/* ... */);
        // ... more boilerplate ...
    });
</script>
{% endblock %}
```

**Problems:**
- 🔴 Developer must remember to include this setup
- 🔴 Easy to forget or make mistakes
- 🔴 Code duplication across templates
- 🔴 Hard to maintain (40+ lines per template)
- 🔴 No automatic deduplication

## Solution Overview

### After: Automatic Dependency Management ✅

**In components (declare once):**
```python
class CodeBlock(Component):
    """Syntax-highlighted code block"""
    requires_dependencies = ['highlight.js']  # ← Declarative!
```

**In views (automatic collection):**
```python
def get_context_data(self, **kwargs):
    context = super().get_context_data(**kwargs)
    context.update(demo_page_context(
        code_examples=[...]  # Uses CodeBlock internally
    ))
    # Dependencies automatically collected and pre-rendered!
    return context
```

**In templates (automatic inclusion):**
```html
<!-- counter.html - AFTER -->
{% block extra_css %}{{ dependencies_css|safe }}{% endblock %}
{% block extra_js %}{{ dependencies_js|safe }}{% endblock %}

<!-- That's it! No manual setup needed. -->
```

**Benefits:**
- ✅ **Declarative**: Components declare their needs
- ✅ **Automatic**: Zero manual setup required
- ✅ **DRY**: No code duplication
- ✅ **Safe**: Automatic deduplication (no double-includes)
- ✅ **Extensible**: Easy to add new libraries
- ✅ **LiveView-compatible**: Works with Rust template rendering

## How It Works

### 1. Components Declare Dependencies

Components specify what they need using a class attribute:

```python
class CodeBlock(Component):
    requires_dependencies = ['highlight.js']

class ChartComponent(Component):
    requires_dependencies = ['chart.js']

class DatePicker(Component):
    requires_dependencies = ['flatpickr']
```

### 2. Registry Stores Available Dependencies

All available dependencies are registered in a central registry:

```python
# demo_app/components/dependencies.py
DEPENDENCY_REGISTRY = {
    'highlight.js': Dependency(
        name='highlight.js',
        css='https://cdnjs.cloudflare.com/ajax/libs/highlight.js/11.9.0/styles/{theme}.min.css',
        js='https://cdnjs.cloudflare.com/ajax/libs/highlight.js/11.9.0/highlight.min.js',
        init_js="""
            (function() {
                // Initialization code
            })();
        """
    ),
}
```

### 3. DependencyManager Collects Requirements

The view creates a `DependencyManager` that scans the context:

```python
from demo_app.components.dependencies import DependencyManager

def get_context_data(self, **kwargs):
    context = super().get_context_data(**kwargs)

    # Add components to context
    context['code_block'] = CodeBlock(...)
    context['chart'] = ChartComponent(...)

    # DependencyManager scans and collects
    deps = DependencyManager()
    deps.collect_from_context(context)  # Finds all components
    # Now deps knows: ['highlight.js', 'chart.js']

    # Pre-render for templates
    context['dependencies_css'] = deps.render_css()
    context['dependencies_js'] = deps.render_js()

    return context
```

### 4. Templates Receive Pre-rendered HTML

Templates override blocks to include dependencies:

```html
{% extends "base.html" %}

{% block extra_css %}
{{ dependencies_css|safe }}
<!-- Renders: <link rel="stylesheet" href="...highlight.css"> -->
{% endblock %}

{% block extra_js %}
{{ dependencies_js|safe }}
<!-- Renders: <script src="...highlight.js"></script>
            <script>/* init code */</script> -->
{% endblock %}
```

### 5. Automatic Deduplication

If multiple components require the same dependency, it's only included once:

```python
context['code1'] = CodeBlock(...)  # requires highlight.js
context['code2'] = CodeBlock(...)  # requires highlight.js
context['code3'] = CodeBlock(...)  # requires highlight.js

deps.collect_from_context(context)
# Result: highlight.js included only ONCE
```

## Quick Start

### Step 1: Create a Component with Dependencies

```python
# demo_app/components/ui.py
from djust.components.base import Component

class CodeBlock(Component):
    # Declare what this component needs
    requires_dependencies = ['highlight.js']

    def __init__(self, code, language="python"):
        self.code = code
        self.language = language

    def render(self):
        return f'<pre><code class="language-{self.language}">{self.code}</code></pre>'
```

### Step 2: Use Component in View

```python
# demo_app/views/demos.py
from demo_app.components import C, demo_page_context

class MyDemoView(LiveView):
    template_name = "my_demo.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)

        # Use helper (handles dependencies automatically)
        context.update(demo_page_context(
            title="My Demo",
            code_examples=[
                {"code": "print('hello')", "language": "python"}
            ]
        ))
        # Dependencies automatically collected!

        return context
```

### Step 3: Override Template Blocks

```html
<!-- templates/my_demo.html -->
{% extends "base.html" %}

{% block extra_css %}{{ dependencies_css|safe }}{% endblock %}
{% block extra_js %}{{ dependencies_js|safe }}{% endblock %}

{% block content %}
    {{ code|safe }}
{% endblock %}
```

**That's it!** The syntax highlighting will work automatically.

## Architecture

### File Structure

```
demo_app/
├── components/
│   ├── __init__.py
│   ├── dependencies.py      # ← Dependency system
│   │   ├── Dependency (dataclass)
│   │   ├── DEPENDENCY_REGISTRY (dict)
│   │   └── DependencyManager (class)
│   ├── ui.py                # Components declare dependencies
│   └── builders.py          # Helpers use DependencyManager
├── views/
│   └── demos.py             # Views pre-render dependencies
└── templates/
    ├── base.html            # Defines blocks
    └── demos/
        └── counter.html     # Overrides blocks
```

### Class Diagram

```
┌─────────────────────────────────────────────────────────┐
│ Component (djust.components.base)                       │
│ ├── requires_dependencies: List[str] = []               │
│ └── render() → str                                       │
└─────────────────────────────────────────────────────────┘
                     ▲
                     │ inherits
                     │
┌─────────────────────────────────────────────────────────┐
│ CodeBlock (demo_app.components.ui)                      │
│ ├── requires_dependencies = ['highlight.js']            │
│ └── render() → str                                       │
└─────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────┐
│ Dependency (dataclass)                                   │
│ ├── name: str                                            │
│ ├── css: Optional[str]                                   │
│ ├── js: Optional[str]                                    │
│ └── init_js: Optional[str]                               │
└─────────────────────────────────────────────────────────┘
                     ▲
                     │ stored in
                     │
┌─────────────────────────────────────────────────────────┐
│ DEPENDENCY_REGISTRY: Dict[str, Dependency]              │
│ ├── 'highlight.js' → Dependency(...)                    │
│ ├── 'bootstrap-tooltips' → Dependency(...)              │
│ └── 'chart.js' → Dependency(...) # example              │
└─────────────────────────────────────────────────────────┘
                     ▲
                     │ uses
                     │
┌─────────────────────────────────────────────────────────┐
│ DependencyManager                                        │
│ ├── _required: Set[str]                                 │
│ ├── require(*names)                                      │
│ ├── collect_from_context(context)                       │
│ ├── render_css() → str                                   │
│ └── render_js() → str                                    │
└─────────────────────────────────────────────────────────┘
```

### Data Flow

```
1. Component Declaration
   ┌──────────────────────────┐
   │ CodeBlock                │
   │ requires_dependencies =  │
   │   ['highlight.js']       │
   └──────────────────────────┘
              ↓

2. View Context Building
   ┌──────────────────────────┐
   │ get_context_data()       │
   │ context['code'] =        │
   │   CodeBlock(...)         │
   └──────────────────────────┘
              ↓

3. Dependency Collection
   ┌──────────────────────────┐
   │ DependencyManager        │
   │ .collect_from_context()  │
   │ → finds CodeBlock        │
   │ → reads requires_deps    │
   │ → adds 'highlight.js'    │
   └──────────────────────────┘
              ↓

4. Dependency Rendering
   ┌──────────────────────────┐
   │ deps.render_css()        │
   │ → <link href="...">      │
   │ deps.render_js()         │
   │ → <script src="...">     │
   │ → <script>init</script>  │
   └──────────────────────────┘
              ↓

5. Template Rendering
   ┌──────────────────────────┐
   │ {{ dependencies_css }}   │
   │ {{ dependencies_js }}    │
   └──────────────────────────┘
```

## Usage Guide

### Using `demo_page_context()` Helper

The easiest way to use the dependency system:

```python
from demo_app.components import demo_page_context

def get_context_data(self, **kwargs):
    context = super().get_context_data(**kwargs)

    context.update(demo_page_context(
        title="Counter Demo",
        subtitle="Real-time reactive counter",
        icon="⚡",
        code_examples=[
            {
                "code": "print('hello')",
                "language": "python",
                "filename": "demo.py"
            }
        ],
        features=[
            ("🔌", "WebSocket", "Persistent connection"),
            ("⚡", "Fast", "Sub-millisecond updates"),
        ],
        highlight_theme="atom-one-dark"  # Optional theme
    ))

    return context
```

**What happens behind the scenes:**
1. `demo_page_context()` creates `CodeBlock` components for `code_examples`
2. Creates a `DependencyManager` instance
3. Calls `deps.collect_from_context(result)` to scan all components
4. Pre-renders `dependencies_css` and `dependencies_js`
5. Returns dict with all components + pre-rendered dependencies

### Manual Dependency Management

For more control, use `DependencyManager` directly:

```python
from demo_app.components.dependencies import DependencyManager

def get_context_data(self, **kwargs):
    context = super().get_context_data(**kwargs)

    # Create components
    context['chart'] = ChartComponent(...)
    context['datepicker'] = DatePicker(...)
    context['code'] = CodeBlock(...)

    # Manually create dependency manager
    deps = DependencyManager()

    # Collect from context
    deps.collect_from_context(context)

    # Or manually require dependencies
    # deps.require('highlight.js', 'chart.js')

    # Configure (optional)
    deps.configure(theme='github-dark')

    # Pre-render for templates
    context['dependencies_css'] = deps.render_css()
    context['dependencies_js'] = deps.render_js()

    return context
```

### Template Integration

Your template must override the `extra_css` and `extra_js` blocks:

```html
{% extends "base.html" %}

{% block title %}My Demo{% endblock %}

<!-- Include CSS dependencies -->
{% block extra_css %}
{{ dependencies_css|safe }}
{% endblock %}

<!-- Your content -->
{% block content %}
    <div data-djust-root>
        {{ code|safe }}
        {{ chart|safe }}
    </div>
{% endblock %}

<!-- Include JS dependencies -->
{% block extra_js %}
{{ dependencies_js|safe }}
{% endblock %}
```

**Important:** Use the `|safe` filter to prevent HTML escaping.

## Extending the System

### Adding a New Dependency

To add a new library to the registry:

```python
# demo_app/components/dependencies.py

DEPENDENCY_REGISTRY: Dict[str, Dependency] = {
    # Existing dependencies...
    'highlight.js': Dependency(...),

    # Add a new one:
    'chart.js': Dependency(
        name='chart.js',
        js='https://cdn.jsdelivr.net/npm/chart.js@4.4.0/dist/chart.umd.min.js',
        # CSS is optional
        css=None,
        # Initialization is optional
        init_js="""
            console.log('Chart.js loaded');
        """
    ),

    'flatpickr': Dependency(
        name='flatpickr',
        css='https://cdn.jsdelivr.net/npm/flatpickr/dist/flatpickr.min.css',
        js='https://cdn.jsdelivr.net/npm/flatpickr',
        init_js="""
            document.addEventListener('DOMContentLoaded', function() {
                flatpickr('.datepicker', {
                    dateFormat: 'Y-m-d',
                });
            });
        """
    ),
}
```

### Creating a Component with Dependencies

```python
# demo_app/components/ui.py

class ChartComponent(Component):
    """Component that requires Chart.js"""

    # Declare dependency
    requires_dependencies = ['chart.js']

    def __init__(self, data, chart_type='line'):
        self.data = data
        self.chart_type = chart_type

    def render(self):
        import json
        return f'''
            <canvas id="myChart"></canvas>
            <script>
                const ctx = document.getElementById('myChart');
                new Chart(ctx, {{
                    type: '{self.chart_type}',
                    data: {json.dumps(self.data)}
                }});
            </script>
        '''
```

### Components with Multiple Dependencies

A component can require multiple dependencies:

```python
class RichTextEditor(Component):
    """Rich text editor with syntax highlighting"""

    requires_dependencies = [
        'quill',
        'highlight.js'
    ]

    def render(self):
        return '''
            <div id="editor"></div>
            <script>
                var quill = new Quill('#editor', {
                    modules: {
                        syntax: true,
                    }
                });
            </script>
        '''
```

### Template Variables in Dependencies

Dependencies support template variables like `{theme}`:

```python
'highlight.js': Dependency(
    name='highlight.js',
    css='https://cdnjs.cloudflare.com/ajax/libs/highlight.js/11.9.0/styles/{theme}.min.css',
    # ...
),
```

Configure the theme:

```python
# In view
deps = DependencyManager()
deps.configure(theme='github-dark')  # or 'atom-one-dark', 'monokai', etc.

# Or in demo_page_context
context.update(demo_page_context(
    highlight_theme='github-dark'
))
```

## LiveView Integration

### Why Pre-rendering is Necessary

LiveView uses **Rust for template rendering**, not Django's template engine. This has important implications:

**Django Template Features NOT Available in Rust Templates:**
- ❌ Calling Python methods: `{{ obj.method() }}`
- ❌ Django context processors (auto-injected variables)
- ❌ Complex Python objects in context

**Solution:** Pre-render dependencies as strings in the view:

```python
def get_context_data(self, **kwargs):
    context = super().get_context_data(**kwargs)

    # Collect dependencies
    deps = DependencyManager()
    deps.collect_from_context(context)

    # Pre-render to strings (Rust templates can handle strings)
    context['dependencies_css'] = deps.render_css()  # Returns HTML string
    context['dependencies_js'] = deps.render_js()    # Returns HTML string

    return context
```

### Template Block Pattern

**Why we use `{% block extra_css %}` instead of direct variables:**

Django template inheritance works differently with LiveView. Variables in the base template (outside blocks) don't have access to child view context.

**❌ This doesn't work:**
```html
<!-- base.html -->
<head>
    {{ dependencies_css|safe }}  <!-- Empty! No access to child context -->
</head>
```

**✅ This works:**
```html
<!-- base.html -->
<head>
    {% block extra_css %}{% endblock %}  <!-- Block can be overridden -->
</head>

<!-- child.html -->
{% block extra_css %}
{{ dependencies_css|safe }}  <!-- Has access to view context! -->
{% endblock %}
```

## API Reference

### `Dependency` (Dataclass)

Represents a single JavaScript or CSS dependency.

```python
@dataclass
class Dependency:
    name: str                    # Unique identifier
    css: Optional[str] = None    # CSS file URL
    js: Optional[str] = None     # JavaScript file URL
    init_js: Optional[str] = None  # Initialization code
```

**Example:**
```python
Dependency(
    name='highlight.js',
    css='https://.../highlight.min.css',
    js='https://.../highlight.min.js',
    init_js='console.log("loaded");'
)
```

### `DependencyManager` (Class)

Manages dependencies for a page render.

#### `__init__()`

Creates a new dependency manager.

```python
deps = DependencyManager()
```

#### `require(*dep_names: str)`

Manually mark dependencies as required.

```python
deps.require('highlight.js', 'chart.js')
```

**Raises:** `KeyError` if dependency not in registry.

#### `configure(**kwargs)`

Set configuration options (e.g., theme).

```python
deps.configure(theme='github-dark')
```

#### `collect_from_context(context: Dict[str, Any])`

Automatically scan context for Component instances and collect their dependencies.

```python
context = {
    'code': CodeBlock(...),      # requires highlight.js
    'chart': ChartComponent(...), # requires chart.js
}
deps.collect_from_context(context)
# Now deps has: ['highlight.js', 'chart.js']
```

#### `render_css() → str`

Render all CSS `<link>` tags for required dependencies.

```python
css_html = deps.render_css()
# Returns: '<link rel="stylesheet" href="...">\n<link rel="stylesheet" href="...">'
```

#### `render_js() → str`

Render all JavaScript `<script>` tags for required dependencies.

```python
js_html = deps.render_js()
# Returns: '<script src="..."></script>\n<script>/* init */</script>'
```

### Component Attribute

#### `requires_dependencies: List[str]`

Class attribute declaring dependencies.

```python
class MyComponent(Component):
    requires_dependencies = ['highlight.js', 'chart.js']
```

## Examples

### Example 1: Simple Code Block

```python
# Component
class CodeBlock(Component):
    requires_dependencies = ['highlight.js']

    def __init__(self, code, language='python'):
        self.code = code
        self.language = language

    def render(self):
        from html import escape
        return f'<pre><code class="language-{self.language}">{escape(self.code)}</code></pre>'

# View
class MyView(LiveView):
    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)

        context['code'] = CodeBlock("print('hello')", language='python')

        deps = DependencyManager()
        deps.collect_from_context(context)
        context['dependencies_css'] = deps.render_css()
        context['dependencies_js'] = deps.render_js()

        # Auto-render components
        for key, value in list(context.items()):
            if hasattr(value, 'render') and callable(value.render):
                context[key] = value.render()

        return context
```

```html
<!-- Template -->
{% extends "base.html" %}

{% block extra_css %}{{ dependencies_css|safe }}{% endblock %}
{% block extra_js %}{{ dependencies_js|safe }}{% endblock %}

{% block content %}
    {{ code|safe }}
{% endblock %}
```

### Example 2: Multiple Components

```python
class DashboardView(LiveView):
    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)

        # Multiple components with different dependencies
        context['code'] = CodeBlock(...)       # needs highlight.js
        context['chart'] = ChartComponent(...) # needs chart.js
        context['picker'] = DatePicker(...)    # needs flatpickr

        # Collect all dependencies automatically
        deps = DependencyManager()
        deps.collect_from_context(context)
        # Result: ['highlight.js', 'chart.js', 'flatpickr']

        context['dependencies_css'] = deps.render_css()
        context['dependencies_js'] = deps.render_js()

        return context
```

### Example 3: Using `demo_page_context()` Helper

```python
class CounterView(LiveView):
    template_name = "demos/counter.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)

        # One-liner handles everything!
        context.update(demo_page_context(
            title="Counter Demo",
            subtitle="Real-time reactive counter",
            icon="⚡",
            code_examples=[
                {"code": "class CounterView(LiveView): ...", "language": "python"},
                {"code": "<button @click='increment'>+</button>", "language": "html"}
            ],
            features=[
                ("🔌", "WebSocket", "Persistent connection"),
            ],
            highlight_theme="atom-one-dark"
        ))
        # Dependencies automatically collected and pre-rendered!

        return context
```

### Example 4: Custom Theme Configuration

```python
def get_context_data(self, **kwargs):
    context = super().get_context_data(**kwargs)

    context['code'] = CodeBlock(...)

    deps = DependencyManager()
    deps.collect_from_context(context)

    # Configure theme based on user preference
    user_theme = self.request.session.get('code_theme', 'atom-one-dark')
    deps.configure(theme=user_theme)

    context['dependencies_css'] = deps.render_css()
    context['dependencies_js'] = deps.render_js()

    return context
```

## Best Practices

### ✅ DO:

- Declare dependencies at the component class level
- Use `demo_page_context()` for standard demo pages
- Pre-render dependencies in `get_context_data()`
- Use template blocks (`extra_css`, `extra_js`) for inclusion
- Add new dependencies to the registry
- Use `|safe` filter in templates

### ❌ DON'T:

- Don't include dependencies manually in templates
- Don't forget to override template blocks
- Don't put dependency objects in context (use pre-rendered strings)
- Don't duplicate dependency definitions
- Don't use method calls in Rust templates

## Troubleshooting

### Dependency not rendering

**Problem:** Dependencies not appearing in HTML.

**Solution:** Check that template overrides the correct blocks:
```html
{% block extra_css %}{{ dependencies_css|safe }}{% endblock %}
{% block extra_js %}{{ dependencies_js|safe }}{% endblock %}
```

### KeyError: Unknown dependency

**Problem:** `KeyError: Unknown dependency 'foo'`

**Solution:** Add the dependency to `DEPENDENCY_REGISTRY`:
```python
DEPENDENCY_REGISTRY['foo'] = Dependency(
    name='foo',
    js='https://...'
)
```

### JavaScript syntax errors

**Problem:** JavaScript has `{{` instead of `{`.

**Solution:** Use single braces in `init_js` strings (not f-strings):
```python
init_js="""
function myFunc() {  // ← Single braces
    console.log('ok');
}
"""
```

### Duplicate includes

**Problem:** Same dependency included multiple times.

**Solution:** This shouldn't happen. `DependencyManager` automatically deduplicates. If you see duplicates, check that you're not manually including dependencies in templates.

## Performance Considerations

- **No runtime overhead**: Dependencies collected once per page render
- **Deduplication**: O(1) lookups using `Set`
- **Pre-rendering**: Happens in view, not during template rendering
- **Caching**: Consider caching rendered dependency strings for identical dependency sets

## Migration from Manual Setup

### Step 1: Identify Dependencies

List all external libraries your templates currently use:
- highlight.js
- Chart.js
- Flatpickr
- Bootstrap tooltips
- etc.

### Step 2: Add to Registry

```python
# demo_app/components/dependencies.py
DEPENDENCY_REGISTRY['my-lib'] = Dependency(
    name='my-lib',
    css='https://...',
    js='https://...',
    init_js='...'
)
```

### Step 3: Update Components

```python
class MyComponent(Component):
    requires_dependencies = ['my-lib']
```

### Step 4: Update Views

```python
def get_context_data(self, **kwargs):
    context = super().get_context_data(**kwargs)
    # ... create components ...

    deps = DependencyManager()
    deps.collect_from_context(context)
    context['dependencies_css'] = deps.render_css()
    context['dependencies_js'] = deps.render_js()

    return context
```

### Step 5: Update Templates

Remove manual `<link>` and `<script>` tags, replace with:
```html
{% block extra_css %}{{ dependencies_css|safe }}{% endblock %}
{% block extra_js %}{{ dependencies_js|safe }}{% endblock %}
```

## Summary

The automatic dependency management system:

1. **Declarative**: Components declare needs via `requires_dependencies`
2. **Automatic**: `DependencyManager` scans and collects automatically
3. **Centralized**: `DEPENDENCY_REGISTRY` stores all available dependencies
4. **DRY**: Zero code duplication across templates
5. **Safe**: Automatic deduplication prevents double-includes
6. **Extensible**: Easy to add new libraries
7. **LiveView-compatible**: Pre-rendered strings work with Rust templates

**Result:** 100% elimination of manual dependency setup!
