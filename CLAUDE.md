# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

djust is a hybrid Python/Rust framework that brings Phoenix LiveView-style reactive server-side rendering to Django. The architecture leverages Rust for performance-critical operations (template rendering, VDOM diffing, HTML parsing) while maintaining a Pythonic API for developers.

### Core Value Proposition
- 10-100x faster template rendering through Rust
- Sub-millisecond VDOM diffing
- Real-time reactive updates over WebSocket
- Zero build step - just ~5KB client JavaScript
- Full Django compatibility

## Build & Development Commands

### Installation & Setup
```bash
# Full install (Python + Rust build)
make install

# Quick Python-only install (no Rust rebuild)
make install-quick

# Build Rust extensions (release mode)
make build

# Build Rust extensions (dev mode, faster compilation)
make dev-build
```

### Development Server
```bash
# Start development server (foreground, with hot reload)
make start

# Start server in background
make start-bg

# Stop server
make stop

# Check server status
make status

# View background server logs
make logs

# Open browser to app
make open
```

**Server Configuration:**
- Default port: 8002 (override with `PORT=8000 make start`)
- Default host: 0.0.0.0 (override with `HOST=127.0.0.1 make start`)
- Server runs from `examples/demo_project/`
- Uses uvicorn ASGI server with auto-reload

### Testing
```bash
# Run all tests (Python + Rust)
make test

# Run Python tests only
make test-python

# Run Rust tests only
make test-rust

# Run linters
make lint

# Format all code
make format

# Run linters and tests
make check
```

### Database (Django)
```bash
# Run migrations
make migrate

# Create new migrations
make migrations

# Reset database (WARNING: destroys data)
make db-reset
```

### Utilities
```bash
# Django shell
make shell

# Show all URL patterns
make urls

# Show project info
make info

# Clean build artifacts
make clean

# Deep clean including venv
make clean-all
```

## Architecture

### High-Level Structure

```
┌─────────────────────────────────────────────┐
│  Browser                                    │
│  ├── client.js (~5KB) - Event & DOM patches│
│  └── WebSocket Connection                   │
└─────────────────────────────────────────────┘
           ↕️ WebSocket (JSON/MessagePack)
┌─────────────────────────────────────────────┐
│  Django + Channels (Python)                 │
│  ├── LiveView Classes                       │
│  ├── Event Handlers                         │
│  └── State Management                       │
└─────────────────────────────────────────────┘
           ↕️ Python/Rust FFI (PyO3)
┌─────────────────────────────────────────────┐
│  Rust Core (Native Speed)                   │
│  ├── Template Engine (<1ms)                │
│  ├── Virtual DOM Diffing (<100μs)          │
│  ├── HTML Parser                            │
│  └── Binary Serialization (MessagePack)    │
└─────────────────────────────────────────────┘
```

### Crate Organization

This is a Cargo workspace with multiple crates:

- **djust_core**: Core types, utilities, serialization, context management
- **djust_templates**: Fast template engine (Django template syntax in Rust)
- **djust_vdom**: Virtual DOM and diffing algorithms for minimal DOM updates
- **djust**: Main PyO3 bindings crate that exposes Rust to Python

The main entry point for Python is `djust._rust` (compiled from `crates/djust/`).

### Python Package Structure

Located in `python/djust/`:

- **live_view.py**: `LiveView` base class for reactive views
- **forms.py**: `FormMixin` for Django Forms integration with real-time validation
- **component.py**: `LiveComponent` base class for reusable components
- **websocket.py**: `LiveViewConsumer` (Channels WebSocket consumer)
- **frameworks.py**: CSS framework integration (Bootstrap, Tailwind, etc.)
- **components.py**: Generic component system with auto-rendering
- **config.py**: Configuration system for framework settings
- **decorators.py**: `@event_handler`, `@reactive` decorators
- **static/djust/**: Client-side JavaScript (~5KB)

### VDOM and Patching

The Rust VDOM implementation (`crates/djust_vdom/`) provides:
- Efficient diffing algorithm that generates minimal patches
- Special handling for form inputs to preserve user state
- DOM path tracking for precise updates
- Support for keyed nodes for list optimization

**Critical behavior**: The VDOM diffing must preserve form field values during updates. See `VDOM_PATCHING_ISSUE.md` for details on form value preservation.

### Forms Integration

Django Forms are deeply integrated via `FormMixin`:
- Real-time field validation on change events
- Automatic error display
- Framework-agnostic rendering (Bootstrap, Tailwind, or plain)
- Preserves user input during VDOM updates

See `PYTHONIC_FORMS_IMPLEMENTATION.md` for implementation details.

## Key Development Patterns

### Creating a LiveView

```python
from djust import LiveView

class MyView(LiveView):
    template_name = 'my_template.html'  # or template_string

    def mount(self, request, **kwargs):
        """Called on initial load"""
        self.count = 0

    def increment(self):
        """Event handler - called from client"""
        self.count += 1

    def get_context_data(self, **kwargs):
        """Override to customize context"""
        return {'count': self.count}
```

### Forms with LiveView

```python
from djust import LiveView
from djust.forms import FormMixin

class MyFormView(FormMixin, LiveView):
    form_class = MyDjangoForm
    template_name = 'form.html'

    def form_valid(self, form):
        form.save()
        self.success_message = "Saved!"

    def form_invalid(self, form):
        self.error_message = "Fix errors"
```

### Template Event Binding

```html
<!-- Click events -->
<button @click="increment">+</button>

<!-- Input events (passes value) -->
<input @input="on_search" type="text" />

<!-- Change events (passes value) -->
<input @change="validate_field" name="email" />

<!-- Form submission (passes form data dict) -->
<form @submit="submit_form">
    <input name="email" />
    <button type="submit">Submit</button>
</form>
```

### WebSocket Setup

The demo project's ASGI configuration is in `examples/demo_project/demo_project/asgi.py`:

```python
from channels.routing import ProtocolTypeRouter, URLRouter
from djust.websocket import LiveViewConsumer

application = ProtocolTypeRouter({
    "http": get_asgi_application(),
    "websocket": AuthMiddlewareStack(
        URLRouter([
            path('ws/live/', LiveViewConsumer.as_asgi()),
        ])
    ),
})
```

## State Management Decorators

djust provides Python-only state management decorators that eliminate the need for custom JavaScript in most use cases. For full API documentation, see `docs/STATE_MANAGEMENT_API.md`.

### Using @cache for Client-Side Caching

The `@cache` decorator enables automatic client-side response caching with TTL and LRU eviction. Perfect for autocomplete, search, and any idempotent read operations.

```python
from djust import LiveView
from djust.decorators import cache, debounce

class ProductSearchView(LiveView):
    template_name = 'search.html'

    def mount(self, request):
        self.results = []

    @debounce(wait=0.5)  # Wait for user to stop typing
    @cache(ttl=300, key_params=["query"])  # Cache for 5 minutes
    def search(self, query: str = "", **kwargs):
        """
        Search handler with debouncing and caching.

        - Debouncing: Waits 500ms after user stops typing
        - Caching: Client caches results for 5 minutes based on query
        - Key params: Only 'query' param is used for cache key
        """
        if query:
            # Expensive database query
            self.results = Product.objects.filter(
                name__icontains=query
            )[:10]
        else:
            self.results = []
```

**Template:**
```html
<div>
    <input @input="search"
           type="text"
           placeholder="Search products..."
           value="{{ query }}" />

    <div class="results">
        {% for product in results %}
            <div class="result-item">{{ product.name }}</div>
        {% endfor %}
    </div>
</div>
```

**How it works:**
1. User types "laptop" → debouncing waits 500ms
2. After 500ms, client checks cache for "laptop"
3. Cache miss → sends request to server
4. Server performs database query
5. Response cached client-side for 5 minutes
6. User types "laptop" again → cache hit, instant result (no server round-trip)

**Performance benefits:**
- First search: 500ms debounce + ~50-200ms server query = ~550-700ms
- Repeated search: <1ms (cache hit, zero latency)
- 87% code reduction vs manual JavaScript caching
- Automatic cache key generation and LRU eviction

For more decorators (@throttle, @optimistic, @client_state, DraftModeMixin), see the [State Management Documentation](#state-management-documentation) section below.

## Component System

djust provides a **two-tier component system** designed for optimal performance and developer experience:

### Component Types

1. **Component (Stateless)**: Simple presentational components
2. **LiveComponent (Stateful)**: Interactive components with state and lifecycle

### When to Use Each Type

| Scenario | Use This | Why |
|----------|----------|-----|
| Badge, button, icon | `Component` | No state needed |
| User profile card | `Component` | Just displays data |
| Todo list with filters | `LiveComponent` | Has state + interaction |
| Data table with sorting | `LiveComponent` | Complex state management |

**Golden Rule**: Start with the simplest pattern (inline template), upgrade to `Component` for reusability, upgrade to `LiveComponent` when you need state and interactivity.

### Creating a Simple Component

```python
from djust.components.base import Component

class StatusBadge(Component):
    """Simple presentational component - no state"""

    def __init__(self, status: str):
        self.status = status

    def render(self) -> str:
        colors = {'active': 'green', 'pending': 'yellow'}
        color = colors.get(self.status, 'gray')
        return f'<span class="badge bg-{color}">{self.status}</span>'

# Usage in view
badge = StatusBadge('active')
# In template: {{ badge.render }}
```

### Creating a LiveComponent

```python
from djust import LiveComponent

class TodoListComponent(LiveComponent):
    """Stateful component with interactivity"""

    template_string = """
        <div class="todo-list">
            <input type="text" @input="on_filter" value="{{ filter }}" />
            {% for item in filtered_items %}
            <div>
                <input type="checkbox" @change="toggle_todo" data-id="{{ item.id }}">
                {{ item.text }}
            </div>
            {% endfor %}
            <p>{{ completed_count }} / {{ total_count }} completed</p>
        </div>
    """

    def mount(self, items=None):
        """Initialize component state"""
        self.items = items or []
        self.filter = ""

    def update(self, items=None, **props):
        """Called when parent updates props"""
        if items is not None:
            self.items = items

    def on_filter(self, value: str = "", **kwargs):
        """Event handler for filter input"""
        self.filter = value

    def toggle_todo(self, id: str = None, **kwargs):
        """Event handler for checkbox"""
        item = next(i for i in self.items if i['id'] == int(id))
        item['completed'] = not item['completed']
        # Notify parent of change
        self.send_parent("todo_toggled", {"id": int(id)})

    def get_context_data(self):
        """Return context for template rendering"""
        filtered = [i for i in self.items if self.filter.lower() in i['text'].lower()]
        return {
            'filter': self.filter,
            'filtered_items': filtered,
            'completed_count': sum(1 for i in self.items if i.get('completed')),
            'total_count': len(self.items),
        }
```

### Component Communication: Props Down, Events Up

LiveComponents communicate with their parent view using a clear pattern:

```python
class DashboardView(LiveView):
    """Parent view coordinates child components"""

    def mount(self, request):
        self.users = User.objects.all()
        self.selected_user = None

        # Create child components (props down)
        self.user_list = UserListComponent(users=self.users)
        self.user_detail = UserDetailComponent(user=None)

    def handle_component_event(self, component_id: str, event: str, data: dict):
        """Handle events from child components (events up)"""
        if event == "user_selected":
            # Update state
            self.selected_user = User.objects.get(id=data['user_id'])
            # Update child component props
            self.user_detail.update(user=self.selected_user)
```

### Component Lifecycle

LiveComponents have three lifecycle methods:

1. **mount(**kwargs)**: Initialize component state (called once on creation)
2. **update(**props)**: React to prop changes from parent
3. **unmount()**: Cleanup when component is destroyed (optional)

```python
class MyComponent(LiveComponent):
    def mount(self, initial_data=None):
        """Called when component is created"""
        self.data = initial_data or []
        self.loading = False

    def update(self, data=None, **props):
        """Called when parent updates props"""
        if data is not None:
            self.data = data

    def unmount(self):
        """Called when component is destroyed"""
        # Optional cleanup
        pass
```

### VDOM and Performance

- **Component**: Zero overhead, just calls `render()` method
- **LiveComponent**:
  - Own VDOM tree (separate from parent)
  - Sub-100μs diffing in Rust
  - Minimal patches sent to client
  - Independent update cycles

**Key insight**: Each LiveComponent has its own VDOM tree, enabling efficient partial updates without re-rendering the entire page.

### Component Implementation Checklist

When implementing components:

1. **Start simple**: Begin with inline template in LiveView
2. **Extract to Component**: When you need reusability
3. **Upgrade to LiveComponent**: When you need state/interactivity
4. **Props down, events up**: Parent coordinates all child components
5. **Use `update()` method**: React to prop changes from parent
6. **Send events with `send_parent()`**: Notify parent of state changes
7. **Test lifecycle**: Verify mount/update/unmount work correctly
8. **Optimize renders**: Use `get_context_data()` for computed values

### Documentation Links

For detailed component documentation:

- **[COMPONENT_UNIFIED_DESIGN.md](COMPONENT_UNIFIED_DESIGN.md)** - ⭐ Unified design with automatic Rust optimization (updated for Phase 4)
- **[LIVECOMPONENT_ARCHITECTURE.md](LIVECOMPONENT_ARCHITECTURE.md)** - Complete architecture guide
- **[API_REFERENCE_COMPONENTS.md](API_REFERENCE_COMPONENTS.md)** - Full API documentation
- **[COMPONENT_BEST_PRACTICES.md](COMPONENT_BEST_PRACTICES.md)** - Best practices and patterns
- **[COMPONENT_MIGRATION_GUIDE.md](COMPONENT_MIGRATION_GUIDE.md)** - Migration guide
- **[COMPONENT_EXAMPLES.md](COMPONENT_EXAMPLES.md)** - Complete working examples (updated for Phase 4)
- **[COMPONENT_PERFORMANCE_OPTIMIZATION.md](COMPONENT_PERFORMANCE_OPTIMIZATION.md)** - Performance optimization strategies
- **[COMPONENTS.md](COMPONENTS.md)** - shadcn-style component philosophy

**Status**: Component system documentation updated and verified for Phase 4 (2025-11-12) with LiveComponent lifecycle, parent-child communication, and automatic registration.

## Building Rust Changes

When making changes to Rust code:

1. **Development builds** (faster compilation):
   ```bash
   make dev-build
   ```

2. **Release builds** (optimized):
   ```bash
   make build
   ```

3. **Run Rust tests**:
   ```bash
   cargo test
   # Or for specific crate:
   cargo test -p djust_vdom
   ```

4. **Lint Rust code**:
   ```bash
   cargo clippy
   cargo fmt
   ```

The Rust code is compiled into a Python extension module (`_rust.cpython-*.so`) via maturin.

## Testing

### Python Tests
Located in `python/djust/` (pytest):
```bash
pytest python/
```

### Rust Tests
Each crate has its own tests:
```bash
cargo test                          # All crates
cargo test -p djust_vdom      # Specific crate
```

Integration tests are in `crates/djust_vdom/tests/`.

### Demo Project
The `examples/demo_project/` serves as both a demo and integration test:
```bash
cd examples/demo_project
python manage.py test
```

## Important Implementation Notes

### VDOM Form Value Preservation
When implementing or modifying VDOM diffing, ensure form field values are preserved during updates. The client must not re-apply patches that would reset user input. See `VDOM_PATCHING_ISSUE.md`.

### Client-Server Communication
- Initial page load: Server renders full HTML
- User interactions: Client sends events via WebSocket
- Server responds: JSON patches describing minimal DOM changes
- Client applies: JavaScript morphs DOM using patches

### Performance Considerations
- Template rendering happens in Rust (sub-millisecond)
- VDOM diffing in Rust generates minimal patches
- Use MessagePack for binary serialization (more efficient than JSON)
- Client JavaScript is intentionally minimal (~5KB)

### State Management
LiveView instances cache state server-side keyed by session ID. The state backend system (see Configuration section) maintains RustLiveView instances for efficient re-rendering. Supports both in-memory (dev) and Redis (production) backends for horizontal scaling.

## Configuration

### Django Settings
```python
INSTALLED_APPS = [
    'channels',              # Required
    'djust',
    # ...
]

ASGI_APPLICATION = 'myproject.asgi.application'

CHANNEL_LAYERS = {
    'default': {
        'BACKEND': 'channels.layers.InMemoryChannelLayer'
    }
}
```

### LiveView Configuration (optional)
```python
LIVEVIEW_CONFIG = {
    # Transport mode
    'use_websocket': True,  # Set to False for HTTP-only mode (no WebSocket dependency)

    # Debug settings
    'debug_vdom': False,  # Enable detailed VDOM patch logging (for troubleshooting)

    # CSS Framework
    'css_framework': 'bootstrap5',  # Options: 'bootstrap5', 'tailwind', None

    # Field rendering options
    'render_labels': True,
    'render_help_text': True,
    'render_errors': True,
    'auto_validate_on_change': True,
}
```

**Debug Mode:**

When debugging VDOM issues, enable `debug_vdom` to see detailed logging:
- Server-side: Patch generation details (logged to stderr)
- Client-side: Patch application, path traversal, and failures (browser console)

```python
# Enable debug mode temporarily
from djust.config import config
config.set('debug_vdom', True)
```

**Client-Side Debug Mode (State Management):**

The client-side JavaScript supports debug logging for state management decorators (@cache, @client_state, etc.). This is controlled by the `djustDebug` global variable:

```javascript
// Enable debug logging (development)
window.djustDebug = true;  // Logs cache hits/misses, state bus events, decorator execution

// Disable debug logging (production - recommended)
window.djustDebug = false;
```

**Production Configuration:**

For production deployments, ensure client-side debug logging is disabled:

```html
<!-- In your base template, before loading djust client.js -->
<script>
    // Disable debug logging in production
    window.djustDebug = false;
</script>
<script src="{% static 'djust/client.js' %}"></script>
```

Or conditionally based on Django settings:

```html
<script>
    window.djustDebug = {% if DEBUG %}true{% else %}false{% endif %};
</script>
<script src="{% static 'djust/client.js' %}"></script>
```

**Debug Output Examples:**

When `djustDebug = true`, you'll see console logs like:
- `[djust:cache] Cache hit for search(query=python) - age: 45s / TTL: 300s`
- `[djust:cache] Cache miss for search(query=django) - fetching from server`
- `[djust:state] Published to bus: temperature=72`
- `[djust:state] Subscriber received update: temperature=72`

### State Backend Configuration

djust provides pluggable state backends for LiveView session storage, enabling horizontal scaling in production:

```python
DJUST_CONFIG = {
    # State backend: 'memory' (dev) or 'redis' (production)
    'STATE_BACKEND': 'memory',  # Default: in-memory for development

    # Redis configuration (only used if STATE_BACKEND='redis')
    'REDIS_URL': 'redis://localhost:6379/0',

    # Session TTL in seconds (default: 3600 = 1 hour)
    'SESSION_TTL': 3600,
}
```

**Backend Options:**

1. **InMemory** (`STATE_BACKEND='memory'`):
   - Fast and simple (default for development)
   - No external dependencies
   - Single-server only (no horizontal scaling)
   - Data lost on server restart

2. **Redis** (`STATE_BACKEND='redis'`):
   - Production-ready horizontal scaling
   - Persistent state survives restarts
   - Requires Redis server and `redis-py` package
   - Native Rust serialization (5-10x faster, 30-40% smaller)

**Production Redis Setup:**

```python
# Install redis-py
pip install redis

# Configure Django settings
DJUST_CONFIG = {
    'STATE_BACKEND': 'redis',
    'REDIS_URL': 'redis://redis.example.com:6379/0',
    'SESSION_TTL': 7200,  # 2 hours
}
```

**Performance Benefits:**

The Redis backend uses native Rust MessagePack serialization:
- **5-10x faster** than Python pickle
- **30-40% smaller** payload size
- **Sub-millisecond** serialization/deserialization
- Full VDOM state preservation for efficient diffing

**Session Management:**

```python
from djust.live_view import cleanup_expired_sessions, get_session_stats

# Cleanup expired sessions (recommended: run periodically via cron/celery)
cleaned = cleanup_expired_sessions(ttl=3600)
print(f"Cleaned {cleaned} expired sessions")

# Get backend statistics
stats = get_session_stats()
print(f"Total sessions: {stats['total_sessions']}")
print(f"Backend: {stats['backend']}")  # 'memory' or 'redis'
```

## Demo Project

The `examples/demo_project/` demonstrates:
- Counter (basic reactivity)
- Forms (with real-time validation)
- Framework comparisons (Bootstrap vs Tailwind vs Plain)

Access after `make start`:
- Home: http://localhost:8002/
- Forms demo: http://localhost:8002/forms/
- Framework comparison: http://localhost:8002/forms/auto/compare/

## Common Issues

### Rust compilation fails
- Ensure Rust 1.70+ installed: `rustc --version`
- Clean and rebuild: `make clean && make build`

### WebSocket connection fails
- Verify Channels installed: `pip list | grep channels`
- Check ASGI configuration in `asgi.py`
- Ensure server running with WebSocket support (uvicorn, daphne)

### Form values reset during updates
- Check VDOM patching logic preserves input values
- See `VDOM_PATCHING_ISSUE.md` for detailed explanation

### Template rendering errors
- Rust template engine supports subset of Django template syntax
- Not all Django filters/tags implemented yet
- Check template compatibility in `crates/djust_templates/`

## Code Review Checklist

When reviewing or writing code:

1. **Rust changes**: Run `cargo clippy` and `cargo fmt`
2. **Python changes**: Run `ruff check` and `ruff format`
3. **Performance**: Run benchmarks if touching rendering/diffing
4. **Forms**: Verify field values preserved during VDOM updates
5. **Tests**: Add tests for new features (Rust + Python)
6. **Documentation**: Update docstrings and comments
7. **Client JS**: Keep minimal - avoid adding dependencies

## Additional Documentation

### General Documentation
- `README.md`: User-facing documentation
- `CONTRIBUTING.md`: Contribution guidelines
- `QUICKSTART.md`: Quick setup guide

### IDE Setup Documentation
- `docs/IDE_SETUP_RUST.md`: ⭐ **Recommended setup for Rust + Python development**
  - IntelliJ IDEA Ultimate recommended (best Rust support + full Python/Django support)
  - Rust plugin installation and configuration
  - Dual IDE setup option (IntelliJ IDEA for Rust, PyCharm for Python)
  - Complete feature comparison and workflow recommendations
- `docs/PYCHARM_SETUP.md`: Complete PyCharm/IntelliJ IDEA configuration guide
- `docs/PYCHARM_QUICK_START.md`: 10-minute essential setup
- **Quick start**: Run `./scripts/open_intellij.sh` to open project in IntelliJ IDEA

### Component System Documentation
- `COMPONENT_UNIFIED_DESIGN.md`: ⭐ Unified design with automatic Rust optimization (read this first!)
- `COMPONENTS.md`: shadcn-style component philosophy and framework adapters
- `LIVECOMPONENT_ARCHITECTURE.md`: Complete architecture guide for two-tier system
- `API_REFERENCE_COMPONENTS.md`: Full API documentation for Component and LiveComponent
- `COMPONENT_BEST_PRACTICES.md`: Best practices, patterns, and anti-patterns
- `COMPONENT_MIGRATION_GUIDE.md`: Step-by-step migration guide
- `COMPONENT_EXAMPLES.md`: Complete working examples (Todo app, User dashboard, etc.)
- `COMPONENT_PERFORMANCE_OPTIMIZATION.md`: Performance optimization strategies (Python → Hybrid → Rust)

### State Management Documentation
- `docs/STATE_MANAGEMENT_API.md`: ⭐ Complete API reference for state management decorators (@debounce, @throttle, @optimistic, @cache, @client_state, DraftModeMixin)
- `docs/STATE_MANAGEMENT_PATTERNS.md`: Common patterns, best practices, and anti-patterns for state management
- `docs/STATE_MANAGEMENT_TUTORIAL.md`: Step-by-step tutorial building a Product Search feature with state management
- `docs/STATE_MANAGEMENT_EXAMPLES.md`: Copy-paste ready examples for e-commerce, chat, dashboards, forms, and more
- `docs/STATE_MANAGEMENT_MIGRATION.md`: Migration guide from manual JavaScript to Python decorators
- `docs/STATE_MANAGEMENT_ARCHITECTURE.md`: Implementation architecture for contributors (backend, frontend, wire protocol)
- `docs/STATE_MANAGEMENT_COMPARISON.md`: Framework comparison with Phoenix LiveView and Laravel Livewire

**Key Concepts:**
- **Python-Only State Management**: Use decorators like `@debounce`, `@optimistic`, `@cache` to eliminate manual JavaScript
- **87% Code Reduction**: State management decorators reduce 889 lines of JavaScript to ~120 lines of Python
- **Zero JavaScript Required**: Common patterns (debouncing, optimistic updates, caching, drafts) work without writing any JS
- **Bundle Size**: 7.1 KB client.js (still smallest: Phoenix ~30KB, Livewire ~50KB)
- **Competitive Advantage**: Python decorators match Phoenix LiveView and Laravel Livewire developer experience

### Implementation Details
- `VDOM_PATCHING_ISSUE.md`: VDOM form value preservation details
- `PYTHONIC_FORMS_IMPLEMENTATION.md`: Forms system implementation details
- `crates/djust_vdom/TESTING.md`: VDOM testing documentation
