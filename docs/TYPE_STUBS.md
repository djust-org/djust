# Type Stubs for Rust Extension Module

## Overview

The `python/djust/_rust.pyi` file provides type information for djust's Rust extension module. This enables IDE autocomplete, type checking with mypy/pyright, and better API discoverability.

## What Are Type Stubs?

Type stub files (`.pyi`) are Python files containing type hints for modules that don't have inline type annotations. They're especially useful for:

1. **C/C++/Rust extension modules** - Where type information isn't available at runtime
2. **IDE autocomplete** - Provides function signatures and parameter names
3. **Static type checking** - Enables mypy/pyright to catch errors before runtime
4. **Documentation** - Serves as API reference with docstrings

## Benefits for djust

### 1. Catch Typos at Lint Time

**Without stubs:**
```python
from djust._rust import render_template

# Typo won't be caught until runtime
html = render_tempalte("<h1>Test</h1>", {})  # ❌ NameError at runtime
```

**With stubs:**
```python
from djust._rust import render_template

# Typo caught by mypy/pyright immediately
html = render_tempalte("<h1>Test</h1>", {})  # ❌ Error at lint time
# Error: Cannot find reference 'render_tempalte' in '_rust.pyi'
```

### 2. IDE Autocomplete

When you type `render_template(`, your IDE shows:

```python
render_template(template_source: str, context: Dict[str, Any]) -> str
```

### 3. Type Safety

```python
# Type checker verifies argument types
html = render_template("<h1>Test</h1>", {})  # ✅ OK
html = render_template(123, {})  # ❌ Error: int is not str
html = render_template("<h1>Test</h1>")  # ❌ Error: missing argument
```

### 4. Better Documentation

Docstrings in the stub file provide inline documentation:

```python
def render_template(template_source: str, context: Dict[str, Any]) -> str:
    """
    Render a template string with the given context.

    Fast Rust-based template rendering using the djust template engine.

    Args:
        template_source: The template source string to render
        context: Template context variables as a dictionary

    Returns:
        The rendered HTML string
    """
```

## What's Covered

djust provides two stub files for comprehensive type checking:

### `_rust.pyi` - Rust Extension Module

Provides type information for Rust-exported functions and classes:

#### Core Rendering Functions
- `render_template()` - Fast template rendering
- `render_template_with_dirs()` - Template rendering with {% include %} support
- `diff_html()` - HTML diffing for VDOM patches
- `resolve_template_inheritance()` - Resolve {% extends %} and {% block %}

#### Serialization Functions
- `fast_json_dumps()` - Rust-based JSON serialization
- `extract_template_variables()` - JIT variable extraction
- `serialize_queryset()` - Django QuerySet serialization
- `serialize_context()` - Template context serialization
- `serialize_models_fast()` - Fast model serialization

#### Template Tag Registry
- `register_tag_handler()` - Register custom template tags
- `has_tag_handler()` - Check if tag is registered
- `get_registered_tags()` - List registered tags
- `unregister_tag_handler()` - Remove tag handler
- `clear_tag_handlers()` - Clear all handlers

#### Actor System
- `SessionActorHandle` - Async session state management
- `SupervisorStatsPy` - Actor supervisor statistics
- `create_session_actor()` - Create/retrieve session actor
- `get_actor_stats()` - Get supervisor metrics

#### LiveView Backend
- `RustLiveView` - Rust-backed LiveView component

#### UI Components (15 components)
- `RustButton`, `RustAlert`, `RustCard`, `RustModal`, etc.

### `live_view.pyi` - LiveView Class Methods

Provides type information for LiveView instance methods from mixins:

#### Navigation (NavigationMixin)
- `live_patch()` - Update URL without remounting
- `live_redirect()` - Navigate to different view over WebSocket
- `handle_params()` - Handle URL parameter changes

#### Push Events (PushEventMixin)
- `push_event()` - Push server-to-client events

#### Streams (StreamsMixin)
- `stream()` - Initialize memory-efficient stream
- `stream_insert()` - Insert item into stream
- `stream_delete()` - Delete item from stream
- `stream_reset()` - Reset stream contents

#### Core Lifecycle
- `mount()` - Initialize view state
- `get_context_data()` - Get template context
- `handle_tick()` - Periodic updates
- `get_state()` - Get serializable state

**Benefit**: Catches typos like `live_navigate` (nonexistent) at lint time instead of runtime.

## Using Type Stubs

### With mypy

```bash
# Type check your code
mypy your_app/

# Type check a single file
mypy your_app/views.py
```

### With pyright/pylance (VS Code)

Add to `pyproject.toml`:

```toml
[tool.pyright]
typeCheckingMode = "basic"  # or "strict"
```

### With IDE (PyCharm, VS Code)

The stubs are automatically discovered by IDEs that support PEP 561. Just import and use - autocomplete and type hints will work automatically.

## Example Usage

See `examples/stub_demo.py` for a complete demonstration:

```bash
# Run the demo
python examples/stub_demo.py

# Type check the demo
mypy examples/stub_demo.py
```

## Development

### Updating Stubs

#### When Rust functions are added/modified

If modifying `crates/djust/src/lib.rs`:

1. Update `python/djust/_rust.pyi` with new signatures
2. Run tests: `pytest python/tests/test_type_stubs.py`
3. Verify coverage: `pytest python/tests/test_type_checking_integration.py`

#### When LiveView methods are added/modified

If modifying mixin methods in `python/djust/mixins/`:

1. Update `python/djust/live_view.pyi` with new signatures
2. Run tests: `pytest python/tests/test_liveview_type_stubs.py`
3. Verify all stubs: `pytest python/tests/test_type*.py`

### Testing Stubs

```bash
# Test stub file structure
pytest python/tests/test_type_stubs.py -v

# Test type checking integration
pytest python/tests/test_type_checking_integration.py -v

# Test everything
pytest python/tests/test_type*.py -v
```

## References

- [PEP 484](https://peps.python.org/pep-0484/) - Type Hints
- [PEP 561](https://peps.python.org/pep-0561/) - Distributing and Packaging Type Information
- [mypy documentation](https://mypy.readthedocs.io/)
- [pyright documentation](https://github.com/microsoft/pyright)

## Troubleshooting

### Stubs not detected by mypy

Ensure the stub file is in the correct location:
```
python/djust/_rust.pyi  # Next to _rust.so extension module
```

### IDE not showing autocomplete

Try restarting your language server or IDE. For VS Code:
1. Open command palette (Cmd+Shift+P)
2. Run "Python: Restart Language Server"

### Type errors in working code

The stubs may not perfectly match runtime behavior in all cases. If you encounter false positives, please file an issue with details.
