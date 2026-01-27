# Reusable UI Components

This module provides stateless presentational components that make it easy to build consistent, beautiful demo pages with minimal HTML.

## Key Concept: Pre-Rendering for LiveView

**Important**: For LiveView compatibility, components must be **rendered to HTML strings** in `get_context_data()`, not passed as objects:

```python
# ✅ CORRECT - Render to HTML string
context['hero'] = HeroSection(title="Demo", icon="⚡").render()

# ❌ WRONG - LiveView can't serialize component objects
context['hero'] = HeroSection(title="Demo", icon="⚡")
```

## Available Components

### CodeBlock

Syntax-highlighted code block with optional filename header.

```python
from demo_app.components import CodeBlock

code = CodeBlock(
    code='''def hello():
    print("world")''',
    language="python",
    filename="example.py"  # Optional
).render()
```

**Features:**
- Preserves newlines and indentation
- Syntax highlighting via highlight.js
- Optional header with filename and language badge
- Supports any language supported by highlight.js

### HeroSection

Large hero section with icon, title, and subtitle.

```python
from demo_app.components import HeroSection

hero = HeroSection(
    title="Counter Demo",
    subtitle="Real-time reactive counter",
    icon="⚡",
    padding="4rem 0 2rem"  # Optional
).render()
```

### Button

Modern button with icon support and event binding.

```python
from demo_app.components import Button

btn = Button(
    text="Increment",
    event="increment",
    variant="primary",  # or "secondary"
    icon_svg='<path d="..."></path>',  # Optional SVG path
    extra_attrs='style="color: red;"'  # Optional
).render()
```

**Variants:**
- `primary`: Blue accent button
- `secondary`: Outlined button

### Card

Card container with optional header.

```python
from demo_app.components import Card

card = Card(
    title="Live Demo",
    subtitle="Click buttons to see updates",
    content="<p>Your content here</p>",
    header_color="rgba(59, 130, 246, 0.03)"  # Optional
).render()
```

### FeatureCard & FeatureGrid

Feature cards with icons and descriptions, arranged in a grid.

```python
from demo_app.components import FeatureCard, FeatureGrid

features = FeatureGrid([
    FeatureCard(
        icon="🔌",
        title="WebSocket Connection",
        description="Persistent connection"
    ),
    FeatureCard(
        icon="⚡",
        title="Fast Updates",
        description="Sub-millisecond VDOM"
    ),
], columns=3).render()
```

### Section

Section wrapper with optional title and subtitle.

```python
from demo_app.components import Section

section = Section(
    title="Features",
    subtitle="Everything you need",
    content="<div>Section content</div>",
    padding="4rem 0",
    max_width="1200px"
).render()
```

### BackButton

Navigation back button with customizable text and link.

```python
from demo_app.components import BackButton

back = BackButton(
    href="/demos/",
    text="Back to Demos"
).render()
```

## Complete Example

Here's how to use components in a LiveView:

```python
# views.py
from djust import LiveView
from demo_app.components import (
    HeroSection, CodeBlock, Button,
    FeatureCard, FeatureGrid, BackButton
)

class MyDemoView(LiveView):
    template_name = "my_demo.html"

    def mount(self, request, **kwargs):
        self.count = 0

    def increment(self):
        self.count += 1

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)

        # IMPORTANT: Call .render() on all components
        context['hero'] = HeroSection(
            title="My Demo",
            subtitle="A cool demo",
            icon="🚀"
        ).render()

        context['increment_btn'] = Button(
            text="Increment",
            event="increment",
            variant="primary"
        ).render()

        context['code'] = CodeBlock(
            code="def demo():\n    pass",
            language="python"
        ).render()

        context['features'] = FeatureGrid([
            FeatureCard("⚡", "Fast", "Blazing fast"),
            FeatureCard("🔒", "Secure", "Battle tested"),
        ]).render()

        context['back'] = BackButton().render()

        return context
```

```html
<!-- my_demo.html -->
{% extends "base.html" %}

{% block content %}
<div data-djust-root>
    <!-- Use |safe filter to render HTML -->
    {{ hero|safe }}

    <section class="section-modern">
        <div class="container">
            {{ increment_btn|safe }}
            <p>Count: {{ count }}</p>
            {{ code|safe }}
            {{ features|safe }}
            {{ back|safe }}
        </div>
    </section>
</div>
{% endblock %}
```

## Benefits

✅ **Consistency**: All demos use the same styled components
✅ **Less HTML**: Reduce template code by 50-70%
✅ **Maintainable**: Update styles in one place
✅ **Type-safe**: Component parameters are documented
✅ **Fast**: Stateless components have zero overhead

## Adding New Components

To add a new component:

1. Create a class that inherits from `Component`
2. Implement `__init__()` to accept parameters
3. Implement `render()` to return HTML string
4. Export from `__init__.py`
5. Document in this README

Example:

```python
# components/ui.py
class Alert(Component):
    def __init__(self, message: str, variant: str = "info"):
        self.message = message
        self.variant = variant

    def render(self) -> str:
        return f'''
            <div class="alert alert-{self.variant}">
                {self.message}
            </div>
        '''
```
