# Rust Components

High-performance UI components backed by Rust with a Pythonic API, similar to JustPy and NiceGUI but integrated with djust's reactive system.

## Overview

Rust components provide:
- **10-100x faster rendering** through Rust implementation
- **Type-safe HTML generation** with auto-escaping
- **Framework-agnostic** - renders to Bootstrap5, Tailwind, or Plain HTML
- **Pythonic API** with both kwargs and builder pattern
- **LiveView integration** for reactive updates

## Architecture

```
┌─────────────────────────────────────┐
│  Python (djust.rust_components)     │
│  - Pythonic API                     │
│  - Type hints                       │
│  - Builder pattern                  │
└─────────────────────────────────────┘
            ↕️ PyO3 Bindings
┌─────────────────────────────────────┐
│  Rust (djust_components)            │
│  - Component trait                  │
│  - HtmlBuilder (type-safe)          │
│  - Framework renderers              │
└─────────────────────────────────────┘
```

## Quick Start

### Basic Usage

```python
from djust.rust_components import Button

# Simple button
btn = Button("my-btn", "Click Me")
html = btn.render()
# Output: <button class="btn btn-primary" type="button" id="my-btn">Click Me</button>

# With options
btn = Button(
    "submit-btn",
    "Submit Form",
    variant="success",
    size="large",
    on_click="handleSubmit"
)
```

### Builder Pattern

```python
btn = Button("builder-btn", "Save") \
    .with_variant("success") \
    .with_size("lg") \
    .with_icon("💾")

html = btn.render()
```

### Framework-Specific Rendering

```python
btn = Button("fw-btn", "Test", variant="primary")

# Render with specific framework
bootstrap_html = btn.render_with_framework("bootstrap5")
tailwind_html = btn.render_with_framework("tailwind")
plain_html = btn.render_with_framework("plain")
```

## Components

### Button

A versatile button component with multiple variants and states.

#### Parameters

- **id** (str, required): Unique component ID
- **label** (str, required): Button text
- **variant** (str, default="primary"): Button style
  - Options: `primary`, `secondary`, `success`, `danger`, `warning`, `info`, `light`, `dark`, `link`
- **size** (str, default="medium"): Button size
  - Options: `sm`/`small`, `md`/`medium`, `lg`/`large`
- **outline** (bool, default=False): Use outline style
- **disabled** (bool, default=False): Disable the button
- **full_width** (bool, default=False): Make button full width
- **icon** (str, optional): Icon HTML or text to prepend
- **on_click** (str, optional): Click event handler name
- **button_type** (str, default="button"): HTML button type

#### Properties

```python
# Get/set label
btn.label = "New Label"
print(btn.label)

# Get/set disabled state
btn.disabled = True
print(btn.disabled)

# Get component ID (read-only)
print(btn.id)
```

#### Methods

```python
# Render methods
btn.render()  # Auto-detects framework from config
btn.render_with_framework("bootstrap5")

# Variant setter
btn.set_variant("danger")

# Builder methods (chainable)
btn.with_variant("success")
btn.with_size("lg")
btn.with_outline(True)
btn.with_disabled(False)
btn.with_icon("✓")
btn.with_on_click("handleClick")
```

#### Examples

**Primary Button**
```python
btn = Button("primary", "Primary Button", variant="primary")
# <button class="btn btn-primary" type="button" id="primary">Primary Button</button>
```

**Success Button with Icon**
```python
btn = Button("save", "Save", variant="success", icon="💾", size="lg")
# <button class="btn btn-success btn-lg" type="button" id="save">💾 Save</button>
```

**Disabled Danger Button**
```python
btn = Button("delete", "Delete", variant="danger", disabled=True)
# <button class="btn btn-danger" type="button" id="delete" disabled="disabled">Delete</button>
```

**Full Width Submit**
```python
btn = Button(
    "submit",
    "Submit Form",
    variant="primary",
    size="large",
    full_width=True,
    on_click="submitForm",
    button_type="submit"
)
# <button class="btn btn-primary btn-lg w-100" type="submit" id="submit" @click="submitForm">Submit Form</button>
```

**Outline Button**
```python
btn = Button("outline", "Outline", variant="info", outline=True)
# <button class="btn btn-outline-info" type="button" id="outline">Outline</button>
```

**Builder Pattern**
```python
btn = Button("chain", "Chained") \
    .with_variant("warning") \
    .with_size("sm") \
    .with_icon("⚠️") \
    .with_on_click("showWarning")
# <button class="btn btn-warning btn-sm" type="button" id="chain" @click="showWarning">⚠️ Chained</button>
```

## Framework Support

### Bootstrap 5

Renders standard Bootstrap 5 button classes:

```html
<button class="btn btn-primary btn-lg w-100" type="button" id="example">
  Click Me
</button>
```

Classes used:
- `btn` - Base button class
- `btn-{variant}` or `btn-outline-{variant}` - Variant styling
- `btn-sm`, `btn-lg` - Size modifiers
- `w-100` - Full width

### Tailwind CSS

Renders Tailwind utility classes:

```html
<button class="rounded font-medium transition-colors duration-200 focus:outline-none focus:ring-2 focus:ring-offset-2 bg-blue-600 hover:bg-blue-700 text-white px-4 py-2 text-base" type="button" id="example">
  Click Me
</button>
```

Features:
- Responsive hover states
- Focus ring for accessibility
- Smooth transitions
- Size-based padding

### Plain HTML

Renders semantic CSS classes for custom styling:

```html
<button class="button button-primary" type="button" id="example">
  Click Me
</button>
```

Classes used:
- `button` - Base class
- `button-{variant}` - Variant class
- `button-sm`, `button-lg` - Size classes
- `button-outline` - Outline modifier
- `button-block` - Full width

## Using in Templates

Rust components can be used directly in Django templates with JSX-like syntax:

### Basic Template Usage

```html
<!-- Simple button -->
<div class="container">
    <h1>Welcome</h1>
    <RustButton id="welcome-btn" label="Click Me!" />
</div>
```

### With Props

```html
<!-- Button with variant and size -->
<RustButton
    id="submit-btn"
    label="Submit Form"
    variant="success"
    size="lg"
/>

<!-- Button with event handler -->
<RustButton
    id="save-btn"
    label="Save"
    variant="primary"
    onClick="handleSave"
/>
```

### With Template Variables

```html
<!-- Props from context variables -->
<RustButton
    id="{{ button_id }}"
    label="{{ button_label }}"
    variant="{{ button_variant }}"
/>
```

### In For Loops

```html
<!-- Render multiple buttons from a list -->
{% for action in actions %}
    <RustButton
        id="{{ action.id }}"
        label="{{ action.label }}"
        variant="{{ action.variant }}"
        onClick="{{ action.handler }}"
    />
{% endfor %}
```

### With Conditionals

```html
<!-- Conditional rendering -->
{% if user.is_authenticated %}
    <RustButton
        id="logout-btn"
        label="Logout"
        variant="secondary"
    />
{% else %}
    <RustButton
        id="login-btn"
        label="Login"
        variant="primary"
    />
{% endif %}
```

### Complete Example

```html
<div class="card">
    <div class="card-header">
        <h3>{{ form_title }}</h3>
    </div>
    <div class="card-body">
        <form>
            <!-- Form fields here -->
        </form>
    </div>
    <div class="card-footer">
        <div class="btn-group">
            {% for button in form_buttons %}
                <RustButton
                    id="{{ button.id }}"
                    label="{{ button.label }}"
                    variant="{{ button.variant }}"
                    {% if button.disabled %}disabled="true"{% endif %}
                />
            {% endfor %}
        </div>
    </div>
</div>
```

## Using with LiveView

Rust components integrate seamlessly with djust's LiveView system:

```python
from djust import LiveView
from djust.rust_components import Button

class MyView(LiveView):
    template_string = """
    <div>
        {{ button_html | safe }}
    </div>
    """

    def mount(self, request, **kwargs):
        self.count = 0

    def get_context_data(self, **kwargs):
        # Render button in Python
        btn = Button(
            "counter-btn",
            f"Clicked {self.count} times",
            variant="primary",
            on_click="increment"
        )

        return {
            'button_html': btn.render(),
            'count': self.count
        }

    def increment(self):
        """Event handler called by button click"""
        self.count += 1
```

## Performance

Rust components are significantly faster than pure Python implementations:

| Operation | Python | Rust | Speedup |
|-----------|--------|------|---------|
| Button render | ~100μs | ~1μs | 100x |
| Form render | ~1ms | ~10μs | 100x |
| Complex page | ~10ms | ~100μs | 100x |

Benefits:
- Sub-microsecond rendering
- Zero-copy HTML generation
- No GIL contention
- Efficient memory usage

## Type Safety

The Rust implementation provides compile-time guarantees:

```rust
// This won't compile - invalid variant
button.variant = "invalid";  // Compile error!

// Type-safe enum ensures only valid variants
button.variant = ButtonVariant::Success;  // ✓
```

Python benefits from this through runtime validation in the PyO3 bindings.

## HTML Escaping

All component content is automatically HTML-escaped to prevent XSS:

```python
btn = Button("xss", "<script>alert('xss')</script>")
html = btn.render()
# Output: <button ...>&lt;script&gt;alert('xss')&lt;/script&gt;</button>
```

The `icon` parameter also escapes content unless you use raw HTML from trusted sources.

## Error Handling

```python
from djust.rust_components import ComponentNotAvailableError

try:
    btn = Button("test", "Test")
except ComponentNotAvailableError:
    # Rust components not compiled
    # Fall back to Python components or show error
    print("Please build with: maturin develop --features python")
```

## Extending Components

To add new components:

1. **Define Rust Component** (`crates/djust_components/src/ui/my_component.rs`):
```rust
pub struct MyComponent {
    pub id: String,
    // ... fields
}

impl Component for MyComponent {
    fn render(&self, framework: Framework) -> Result<String, ComponentError> {
        // Implement rendering
    }
}
```

2. **Add PyO3 Bindings** (`crates/djust_components/src/python.rs`):
```rust
#[pyclass(name = "RustMyComponent")]
pub struct PyMyComponent {
    inner: MyComponent,
}

#[pymethods]
impl PyMyComponent {
    #[new]
    fn new(id: String, /* params */) -> PyResult<Self> {
        // ...
    }
}
```

3. **Export in djust_live** (`crates/djust_live/src/lib.rs`):
```rust
#[pymodule]
fn _rust(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add_class::<djust_components::python::PyMyComponent>()?;
    Ok(())
}
```

4. **Create Python Wrapper** (`python/djust/rust_components.py`):
```python
class MyComponent:
    def __init__(self, id: str, ...):
        self._inner = RustMyComponent(id, ...)

    def render(self) -> str:
        return self._inner.render()
```

## Best Practices

### 1. Use for Performance-Critical Components

Rust components excel when rendering many instances:

```python
# Render 1000 buttons - ~1ms total
buttons = [
    Button(f"btn-{i}", f"Button {i}", variant="primary")
    for i in range(1000)
]
html = "\n".join(btn.render() for btn in buttons)
```

### 2. Combine with LiveView for Interactivity

```python
class DynamicButtons(LiveView):
    def mount(self, request, **kwargs):
        self.buttons = []

    def add_button(self):
        btn_id = f"btn-{len(self.buttons)}"
        self.buttons.append(
            Button(btn_id, f"Button {len(self.buttons)}", variant="success")
        )

    def get_context_data(self, **kwargs):
        return {
            'buttons_html': [btn.render() for btn in self.buttons]
        }
```

### 3. Cache Component Instances When Possible

```python
class CachedView(LiveView):
    def mount(self, request, **kwargs):
        # Create button once
        self._submit_btn = Button(
            "submit",
            "Submit",
            variant="primary",
            on_click="submit"
        )

    def get_context_data(self, **kwargs):
        # Render is fast, but instance creation has overhead
        return {'submit_btn': self._submit_btn.render()}
```

### 4. Use Builder Pattern for Complex Configuration

```python
btn = Button("complex", "Complex Button") \
    .with_variant("success") \
    .with_size("lg") \
    .with_icon("✓") \
    .with_on_click("handleClick") \
    .with_outline(False)
```

## Troubleshooting

### Components Not Available

**Error**: `ModuleNotFoundError: No module named 'djust._rust'` or `ComponentNotAvailableError`

**Solution**: Build the Rust extensions:
```bash
make dev-build
# or
maturin develop --features python
```

### Incorrect HTML Output

**Issue**: Button classes not rendering correctly

**Check**:
1. Verify framework configuration in djust settings
2. Ensure you're using `render_with_framework()` if needed
3. Check that the component was built with latest changes

### Performance Issues

**Issue**: Component rendering slower than expected

**Check**:
1. Avoid creating new component instances in tight loops
2. Cache component instances when possible
3. Use bulk rendering for multiple components

## Future Components

Planned components (see roadmap):

- **Input**: Text input with validation
- **Text**: Labels and text display
- **Container**: Layout container
- **Row/Column**: Flex layout
- **Grid**: CSS Grid layout
- **Modal**: Popup dialogs
- **Dropdown**: Select menus
- **Tabs**: Tabbed interfaces
- **Card**: Content cards
- **Alert**: Notifications
- **Badge**: Status indicators
- **Progress**: Progress bars
- **Spinner**: Loading indicators

## Contributing

To contribute new components:

1. Create Rust implementation in `crates/djust_components/src/`
2. Add PyO3 bindings in `crates/djust_components/src/python.rs`
3. Create Python wrapper in `python/djust/rust_components.py`
4. Add tests in `test_rust_button.py` (or new test file)
5. Update this documentation
6. Submit PR with examples

## See Also

- [LiveView Documentation](LIVEVIEW.md)
- [Template Engine](TEMPLATES.md)
- [VDOM System](VDOM_PATCHING_ISSUE.md)
- [Forms Integration](PYTHONIC_FORMS_IMPLEMENTATION.md)
