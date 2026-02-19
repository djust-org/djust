# Error Code Reference

djust uses structured error codes to help you diagnose problems quickly. This guide covers every error code, what causes it, and how to fix it.

---

## Error Code Categories

| Prefix | Category | When Checked |
|--------|----------|--------------|
| C0xx | Configuration | `manage.py check --tag djust` (startup) |
| V0xx | Validation | `manage.py check --tag djust` (startup) |
| S0xx | Security | `manage.py check --tag djust` (startup) |
| T0xx | Templates | `manage.py check --tag djust` (startup) |
| Q0xx | Code Quality | `manage.py check --tag djust` (startup) |
| DJE-xxx | Runtime | During WebSocket events and VDOM diffing |

Run all static checks at once:

```bash
python manage.py check --tag djust
```

---

## Configuration Errors (C0xx)

### C001: ASGI_APPLICATION not set

**Severity**: Error

**What causes it**: Your Django settings file is missing the `ASGI_APPLICATION` setting. djust requires ASGI for WebSocket support.

**What you see**: LiveView pages load as static HTML. No WebSocket connection is established.

**Fix**:

```python
# settings.py
ASGI_APPLICATION = "myproject.asgi.application"
```

---

### C002: CHANNEL_LAYERS not configured

**Severity**: Error

**What causes it**: Django Channels is not configured. djust uses Channels for WebSocket communication.

**What you see**: WebSocket connections fail. Browser console shows WebSocket connection errors.

**Fix**:

```python
# settings.py (development)
CHANNEL_LAYERS = {
    "default": {
        "BACKEND": "channels.layers.InMemoryChannelLayer",
    }
}

# settings.py (production -- use Redis)
CHANNEL_LAYERS = {
    "default": {
        "BACKEND": "channels_redis.core.RedisChannelLayer",
        "CONFIG": {
            "hosts": [("127.0.0.1", 6379)],
        },
    }
}
```

---

### C003: daphne ordering in INSTALLED_APPS

**Severity**: Warning (wrong order) / Info (missing)

**What causes it**: `daphne` is listed after `django.contrib.staticfiles` in `INSTALLED_APPS`, or is missing entirely.

**What you see**: `manage.py runserver` starts the WSGI server instead of the ASGI server, so WebSockets do not work in development.

**Fix**:

```python
# settings.py
INSTALLED_APPS = [
    "daphne",                          # Must be before staticfiles
    "django.contrib.staticfiles",
    "djust",
    # ...
]
```

---

### C004: djust not in INSTALLED_APPS

**Severity**: Error

**What causes it**: The `djust` app is not in your `INSTALLED_APPS`.

**What you see**: Template tags, static files, and system checks are not available.

**Fix**:

```python
# settings.py
INSTALLED_APPS = [
    # ...
    "djust",
    # ...
]
```

---

### C005: WebSocket routes missing AuthMiddlewareStack

**Severity**: Warning

**What causes it**: Your ASGI routing does not wrap the WebSocket URL router with `AuthMiddlewareStack` or `DjustMiddlewareStack`.

**What you see**: `request.session` and `request.user` are unavailable inside `mount()`. Authentication-related features silently fail.

**Fix**:

```python
# asgi.py
from channels.auth import AuthMiddlewareStack
from channels.routing import ProtocolTypeRouter, URLRouter

application = ProtocolTypeRouter({
    "http": get_asgi_application(),
    "websocket": AuthMiddlewareStack(   # Wrap with this
        URLRouter(websocket_urlpatterns)
    ),
})
```

---

### C006: daphne without WhiteNoise

**Severity**: Warning

**What causes it**: You are using daphne (ASGI server) but WhiteNoise is not in your middleware. Daphne does not serve static files by default.

**What you see**: 404 errors for djust's client JavaScript and CSS files. The page loads but is not interactive.

**Fix**:

```python
# settings.py
MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "whitenoise.middleware.WhiteNoiseMiddleware",   # Add after SecurityMiddleware
    # ...
]
```

Then run `python manage.py collectstatic`.

---

### C010: Tailwind CDN in production

**Severity**: Warning

**What causes it**: A base/layout template includes the Tailwind CSS CDN script in a non-DEBUG environment.

**What you see**: Slow page loads and console warnings about Tailwind CDN usage in production.

**Fix**: Compile Tailwind CSS instead of using the CDN:

```bash
python manage.py djust_setup_css tailwind
# Or manually:
tailwindcss -i static/css/input.css -o static/css/output.css --minify
```

---

### C011: Missing compiled CSS

**Severity**: Warning (production) / Info (development)

**What causes it**: Tailwind is configured (tailwind.config.js exists or input.css references Tailwind) but the compiled output.css is missing.

**Fix**: Compile the CSS:

```bash
python manage.py djust_setup_css tailwind
```

In development, djust falls back to the Tailwind CDN automatically.

---

### C012: Manual client.js in template

**Severity**: Warning

**What causes it**: A base/layout template manually includes `<script src="{% static 'djust/client.js' %}">`. djust automatically injects its client JS for LiveView pages.

**What you see**: Double-loading of client JavaScript, which can cause race conditions and duplicate WebSocket connections.

**Fix**: Remove the manual `<script>` tag. djust handles script injection automatically.

---

## Validation Errors (V0xx)

### V001: Missing template_name

**Severity**: Warning

**What causes it**: A LiveView subclass does not define `template_name` (and no parent class defines it either).

**Fix**:

```python
class MyView(LiveView):
    template_name = "myapp/my_template.html"  # Add this
```

---

### V002: Missing mount() method

**Severity**: Info

**What causes it**: A LiveView subclass does not define a `mount()` method. While not strictly required, `mount()` is where you initialize state.

**Fix**:

```python
class MyView(LiveView):
    template_name = "my_template.html"

    def mount(self, request, **kwargs):
        self.count = 0
```

---

### V003: Wrong mount() signature

**Severity**: Error

**What causes it**: The `mount()` method does not accept `request` as its first positional argument.

**Fix**:

```python
# WRONG
def mount(self):
    pass

# WRONG
def mount(self, **kwargs):
    pass

# CORRECT
def mount(self, request, **kwargs):
    self.count = 0
```

---

### V004: Missing @event_handler decorator

**Severity**: Info

**What causes it**: A public method name matches event handler naming patterns (e.g., `handle_*`, `on_*`, `toggle_*`, `select_*`, `update_*`, `delete_*`, `create_*`, `add_*`, `remove_*`, `save_*`, `cancel_*`, `submit_*`, `close_*`, `open_*`) but is not decorated with `@event_handler`.

Without the decorator, the method cannot be called from templates via `dj-click` or other directives.

**Fix**:

```python
from djust.decorators import event_handler

class MyView(LiveView):
    @event_handler()
    def toggle_sidebar(self, **kwargs):
        self.sidebar_open = not self.sidebar_open
```

If the method is intentionally private (not callable from templates), prefix it with `_`:

```python
def _toggle_internal_flag(self):
    self._flag = not self._flag
```

---

### V005: Module not in LIVEVIEW_ALLOWED_MODULES

**Severity**: Warning

**What causes it**: You have set `LIVEVIEW_ALLOWED_MODULES` in settings and a LiveView's module is not in the list. WebSocket mount will silently fail for this view.

**Fix**:

```python
# settings.py
LIVEVIEW_ALLOWED_MODULES = [
    "myapp.views",
    "otherapp.views",  # Add the missing module
]
```

Or remove `LIVEVIEW_ALLOWED_MODULES` entirely to allow all modules.

---

### V006: Service instance in mount()

**Severity**: Warning

**What causes it**: AST analysis detected an assignment in `mount()` that instantiates a class whose name contains keywords like "Service", "Client", "Session", "API", or "Connection". These are typically not JSON-serializable.

**What you see**: The view works initially, but after a WebSocket reconnection or page refresh, you get `AttributeError` when trying to call methods on what is now a string representation of the object.

**Fix**: Use the helper method pattern:

```python
# WRONG
class MyView(LiveView):
    def mount(self, request, **kwargs):
        self.s3 = boto3.client("s3")  # Not serializable

# CORRECT
class MyView(LiveView):
    def _get_s3(self):
        return boto3.client("s3")

    def mount(self, request, **kwargs):
        s3 = self._get_s3()  # Temporary variable, not stored in state
        # Use s3 here to fetch data
```

Or suppress if you know the object is serializable:

```python
self.api_client = MySerializableClient()  # noqa: V006
```

**Related**: [Working with External Services](services.md)

---

### V007: Event handler missing **kwargs

**Severity**: Warning

**What causes it**: An `@event_handler` decorated method does not include `**kwargs` in its signature. Event handlers receive all event parameters from the client, and without `**kwargs`, extra parameters will cause errors.

**Fix**:

```python
# WRONG - will fail if client sends unexpected parameters
@event_handler()
def search(self, query: str = ""):
    self.results = search(query)

# CORRECT
@event_handler()
def search(self, query: str = "", **kwargs):
    self.results = search(query)
```

---

### V008: Non-primitive type in mount()

**Severity**: Info

**What causes it**: AST analysis detected an assignment in `mount()` that instantiates a non-primitive type (not `list`, `dict`, `set`, `tuple`, `str`, `int`, `float`, `bool`). This may indicate a non-serializable object being stored in LiveView state.

**What you see**: Similar to V006, but catches a broader range of types. You may see runtime warnings about non-serializable values, or `AttributeError` after deserialization.

**Fix**: If the type is not JSON-serializable, use a private variable or helper method:

```python
# WRONG
class MyView(LiveView):
    def mount(self, request, **kwargs):
        self.processor = DataProcessor()  # May not be serializable

# CORRECT - private variable
class MyView(LiveView):
    def mount(self, request, **kwargs):
        self._processor = DataProcessor()  # Private, not serialized

# OR - if it IS serializable, suppress the check
class MyView(LiveView):
    def mount(self, request, **kwargs):
        self.config = MySerializableConfig()  # noqa: V008
```

V008 is broader than V006 and will flag any custom class instantiation, not just service-like names. This helps catch subtle serialization bugs early.

**Related**: [Working with External Services](services.md)

---

## Security Errors (S0xx)

### S001: mark_safe() with f-string

**Severity**: Error

**What causes it**: Using `mark_safe(f"...")` with interpolated values. This is a cross-site scripting (XSS) vulnerability.

**Fix**:

```python
# WRONG -- XSS vulnerability
from django.utils.safestring import mark_safe
html = mark_safe(f"<b>{user_input}</b>")

# CORRECT
from django.utils.html import format_html
html = format_html("<b>{}</b>", user_input)
```

---

### S002: @csrf_exempt without justification

**Severity**: Warning

**What causes it**: A view function uses `@csrf_exempt` without a docstring explaining why CSRF protection is disabled.

**Fix**: Add a docstring mentioning "csrf" to acknowledge the exemption:

```python
@csrf_exempt
def webhook_endpoint(request):
    """CSRF exempt: external webhook callback from payment provider."""
    pass
```

Or suppress the check on a specific line:

```python
@csrf_exempt  # noqa: S002
def my_view(request):
    pass
```

---

### S003: Bare except: pass

**Severity**: Warning

**What causes it**: A bare `except: pass` block that swallows all exceptions silently.

**Fix**:

```python
# WRONG
try:
    do_something()
except:
    pass

# CORRECT -- catch specific exception and log
try:
    do_something()
except ValueError as e:
    logger.warning("Failed: %s", e)
```

---

### S004: DEBUG=True with non-localhost ALLOWED_HOSTS

**Severity**: Warning

**What causes it**: `DEBUG = True` but `ALLOWED_HOSTS` includes non-local addresses. This exposes detailed error pages to external users.

**Fix**: Set `DEBUG = False` in production, or restrict `ALLOWED_HOSTS` to local addresses during development.

---

### S005: State exposed without authentication

**Severity**: Warning

**What causes it**: A LiveView exposes public state variables but has no authentication configured (no `login_required`, `permission_required`, or `check_permissions` override).

**Fix**:

```python
class AdminDashboardView(LiveView):
    login_required = True  # Require login
    # or
    permission_required = "myapp.view_dashboard"  # Require specific permission

    template_name = "dashboard.html"
```

If the view is intentionally public, acknowledge it explicitly:

```python
class PublicCounterView(LiveView):
    login_required = False  # Explicitly public
```

**Related**: [Authentication Guide](authentication.md)

---

## Template Errors (T0xx)

### T001: Deprecated @event syntax

**Severity**: Warning

**What causes it**: A template uses the old `@click`, `@input`, `@change`, or `@submit` syntax instead of the current `dj-click`, `dj-input`, `dj-change`, `dj-submit`.

**Fix**:

```html
<!-- WRONG (deprecated) -->
<button @click="increment">+1</button>

<!-- CORRECT -->
<button dj-click="increment">+1</button>
```

---

### T002: Missing dj-root

**Severity**: Info

**What causes it**: A template contains djust directives (`dj-click`, `dj-input`, `dj-change`, `dj-submit`, `dj-model`) but no element has the `dj-root` attribute. The check skips templates that use `{% extends %}` since the root may be in a parent template.

**What you see**: Events fire but the DOM never updates. Server logs show DJE-053 warnings.

**Fix**: Add `dj-root` to the root element of your LiveView template:

```html
<div dj-view="myapp.views.MyView" dj-root>
    <!-- content -->
</div>
```

**Related**: [Template Requirements Guide](template-requirements.md)

---

### T003: Wrapper template using {% include %} instead of {{ liveview_content|safe }}

**Severity**: Info

**What causes it**: A wrapper template that references LiveView content uses `{% include %}` tags instead of the `{{ liveview_content|safe }}` variable.

**Fix**: Use `{{ liveview_content|safe }}` in wrapper templates to render LiveView content.

---

### T004: document.addEventListener for djust events

**Severity**: Warning

**What causes it**: A template uses `document.addEventListener('djust:...')` for djust custom events. All `djust:*` events are dispatched on `window`, not `document`.

**What you see**: The event listener never fires. No error in the console -- completely silent failure.

**Fix**:

```html
<!-- WRONG -- event never fires -->
<script>
document.addEventListener('djust:push_event', (e) => { ... });
</script>

<!-- CORRECT -->
<script>
window.addEventListener('djust:push_event', (e) => { ... });
</script>
```

---

## Code Quality (Q0xx)

### Q001: print() statement

**Severity**: Info

**What causes it**: A `print()` call was found in production code.

**Fix**: Use the logging module instead:

```python
import logging
logger = logging.getLogger(__name__)

# WRONG
print(f"Processing {item}")

# CORRECT
logger.info("Processing %s", item)
```

---

### Q002: f-string in logger call

**Severity**: Warning

**What causes it**: A logger call uses an f-string instead of `%s`-style formatting. f-strings are evaluated eagerly, even when the log level is disabled.

**Fix**:

```python
# WRONG -- f-string evaluated even if DEBUG is off
logger.debug(f"Processing {expensive_repr(item)}")

# CORRECT -- lazy evaluation
logger.debug("Processing %s", item)
```

---

### Q003: console.log without djustDebug guard

**Severity**: Info

**What causes it**: A JavaScript file contains `console.log` without a `djustDebug` check. djust's client JS should only log when debugging is explicitly enabled.

**Fix**:

```javascript
// WRONG
console.log("Connected to", url);

// CORRECT
if (globalThis.djustDebug) {
    console.log("Connected to", url);
}
```

---

## Runtime Errors (DJE-xxx)

These errors appear in server logs during WebSocket communication and VDOM diffing. They are not caught by `manage.py check` -- they only occur at runtime.

### DJE-050: Mixed keyed/unkeyed children

**Severity**: Warning (performance)

**What causes it**: A parent element has some children with `data-key` attributes and some without. This forces the VDOM to use a less efficient diffing strategy.

**What you see**: A trace-level warning in server logs (visible with `DJUST_VDOM_TRACE=1`). The page still works but updates may be slower than necessary for large lists.

**Fix**: Either add `data-key` to all sibling elements or remove keys from all of them:

```html
<!-- WRONG: mixed keyed/unkeyed -->
<ul>
    <li>Static header</li>
    {% for item in items %}
    <li data-key="{{ item.id }}">{{ item.name }}</li>
    {% endfor %}
</ul>

<!-- CORRECT: all keyed (move static content outside the list) -->
<p>Static header</p>
<ul>
    {% for item in items %}
    <li data-key="{{ item.id }}">{{ item.name }}</li>
    {% endfor %}
</ul>
```

---

### DJE-051: Duplicate keys

**Severity**: Warning

**What causes it**: Two or more sibling elements share the same `data-key` value. The VDOM engine cannot distinguish between them.

**What you see**: Trace-level warning in server logs. Elements may be updated incorrectly or not at all.

**Fix**: Ensure every `data-key` within the same parent is unique:

```python
# WRONG: duplicate IDs possible if items have same id
{% for item in items %}
<li data-key="{{ item.name }}">{{ item.name }}</li>
{% endfor %}

# CORRECT: use unique identifier
{% for item in items %}
<li data-key="{{ item.id }}">{{ item.name }}</li>
{% endfor %}
```

---

### DJE-052: Unkeyed list performance warning

**Severity**: Warning (performance)

**What causes it**: A large unkeyed list (10+ children) produced patches for more than half its children. This usually means the list was reordered, and without keys the VDOM had to patch every element individually.

**What you see**: Trace-level warning. The page still works but updates may be visually janky or slow for large lists.

**Fix**: Add `data-key` to list items:

```html
<ul>
    {% for item in items %}
    <li data-key="{{ item.id }}">{{ item.name }}</li>
    {% endfor %}
</ul>
```

**Related**: [List Reordering Performance](LIST_REORDERING_PERFORMANCE.md)

---

### DJE-053: No DOM changes

**Severity**: Warning

**What causes it**: An event handler modified state and triggered a re-render, but the VDOM diff produced zero patches. This usually means the modified state is rendered **outside** the `dj-root` element (e.g., in `base.html`).

**What you see**: In server logs:

```
WARNING [djust] Event 'toggle_sidebar' on DashboardView produced no DOM changes (DJE-053).
The modified state may be outside <div dj-root>.
```

The page does not update even though the event handler ran and changed state.

**Common causes**:

1. **State rendered outside the VDOM root**: The `{% if show_panel %}` block is in `base.html` while `dj-root` is in the child template.
2. **Missing `dj-root`**: The attribute is not on any element, so the VDOM has no root to diff against.
3. **Event handler changes state that does not affect the template**: The handler updates a variable that is not used in the template.

**Fix**:

1. Move the affected template code inside the `dj-root` element
2. Use `push_event` for UI state changes that live outside the VDOM root
3. Check that `dj-root` is present on the root element

**Related**: [Template Requirements Guide](template-requirements.md)

---

## Debugging Steps

When you encounter an error, follow this workflow:

### 1. Run system checks

```bash
python manage.py check --tag djust
```

This catches configuration, validation, security, template, and code quality issues at once.

### 2. Enable VDOM tracing for runtime issues

```bash
DJUST_VDOM_TRACE=1 python manage.py runserver
```

This produces detailed output for every VDOM diff, including tree structures, patch lists, and performance data. Look for DJE-xxx codes in the output.

### 3. Check browser DevTools

Open the browser's Network tab and filter by "WS" (WebSocket). You should see:

- A WebSocket connection to your server
- Messages flowing back and forth when you click buttons

If there is no WebSocket connection, check C001-C005. If events are sent but no patches come back, check DJE-053 and T002.

### 4. Check server logs

djust logs warnings for common issues. Look for lines starting with `[djust]`:

```bash
python manage.py runserver 2>&1 | grep "\[djust\]"
```

### 5. Run the security audit

For a comprehensive review of authentication and exposed state:

```bash
python manage.py djust_audit
```

---

## Suppressing Checks

You can suppress specific checks on individual lines using `# noqa`:

```python
# Suppress a specific check
mark_safe(f"<b>{trusted_value}</b>")  # noqa: S001

# Suppress all checks on a line
print("Debug output")  # noqa

# Suppress multiple specific checks
some_code()  # noqa: Q001,S003
```

---

## See Also

- [Template Requirements](template-requirements.md) -- Required template attributes
- [Working with External Services](services.md) -- Avoiding serialization errors
- [Security Guide](security.md) -- Authentication and authorization
- [Best Practices](BEST_PRACTICES.md) -- State management patterns
