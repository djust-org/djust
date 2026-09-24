---
title: "Error Code Reference"
slug: error-codes
section: guides
order: 21
level: reference
description: "Every diagnostic code djust emits, with its cause and fix."
---

# Error Code Reference

djust uses structured error codes to help you diagnose problems quickly. This guide covers every diagnostic code djust emits as of 1.2.0rc10, what causes it, and how to fix it.

---

## Error Code Categories

| Prefix | Category | When Checked |
|--------|----------|--------------|
| C0xx, C3xx–C5xx | Configuration (C3xx: VDOM cache, C4xx: Hot View Replacement, C5xx: time travel) | `manage.py check --tag djust` (startup) |
| V0xx | Validation | `manage.py check --tag djust` (startup) |
| S0xx | Security | `manage.py check --tag djust` (startup) |
| T0xx | Templates | `manage.py check --tag djust` (startup) |
| Q0xx | Code Quality | `manage.py check --tag djust` (startup) |
| A0xx | Audit / Static Security Checks | `manage.py check --tag djust` (startup) |
| D0xx | Database notifications | `manage.py check --tag djust` (startup) |
| U0xx | Update notice | `manage.py check --tag djust` (startup, DEBUG only) |
| Y0xx | Accessibility | `manage.py check --tag djust` (startup) |
| `djust.audio.*` | Audio | `manage.py check --tag djust` (startup) |
| `djust_theming.*` | Theming | `manage.py check` (startup, `compatibility` tag) |
| P0xx | Permissions Document | `manage.py djust_audit --permissions permissions.yaml` |
| L0xx | Live Runtime Probe | `manage.py djust_audit --live <url>` |
| X0xx | AST Anti-Pattern Scanner | `manage.py djust_audit --ast` |
| DJE-xxx | Runtime | During WebSocket events and VDOM diffing |

Run all static checks at once:

```bash
python manage.py check --tag djust
```

Run the runtime probe against a deployed environment:

```bash
python manage.py djust_audit --live https://staging.example.com --strict
```

Validate against a committed permissions document:

```bash
python manage.py djust_audit --permissions permissions.yaml --strict
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

**Severity**: Warning (wrong order) / Info (no ASGI server installed)

**What causes it**: Either `daphne` is listed after `django.contrib.staticfiles` in `INSTALLED_APPS` (Warning), or `daphne` is not in `INSTALLED_APPS` and none of daphne, uvicorn or hypercorn is importable (Info: "No ASGI server detected (daphne, uvicorn, or hypercorn)."). Projects that use uvicorn or hypercorn without daphne are not flagged.

**What you see**: With the wrong order, `manage.py runserver` starts the WSGI server instead of the ASGI server, so WebSockets do not work in development.

**Fix**: For the Info case, install an ASGI server; `pip install 'uvicorn[standard]'` is recommended. For the ordering case:

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

In development, pages render without Tailwind utilities until you compile the CSS; `djust_setup_css tailwind --watch` keeps it rebuilt as you edit.

---

### C012: Manual client.js in template

**Severity**: Warning

**What causes it**: A base/layout template manually includes `<script src="{% static 'djust/client.js' %}">`. djust automatically injects its client JS for LiveView pages.

**What you see**: Double-loading of client JavaScript, which can cause race conditions and duplicate WebSocket connections.

**Fix**: Remove the manual `<script>` tag. djust handles script injection automatically.

---

### C013: Stale collectstatic copy of client.min.js

**Severity**: Warning

**What causes it**: `STATIC_ROOT` is set and `STATIC_ROOT/djust/client.min.js` exists, but its content differs from the copy bundled with the installed djust package. Message: "Stale collectstatic copy of client.min.js detected at ... The wheel-bundled copy is different — your browser will load outdated client code."

**Fix**: Run `python manage.py collectstatic --clear --noinput`, then hard-reload the browser. If you serve `client.min.js` from a CDN or a custom build, suppress with `DJUST_CONFIG = {"suppress_checks": ["C013"]}`.

---

### C014: django-tenants under ASGI without TENANT_LIMIT_SET_CALLS

**Severity**: Warning

**What causes it**: django-tenants is configured (`django_tenants` in `INSTALLED_APPS` or `TENANT_MODEL` set), `ASGI_APPLICATION` is set, and `TENANT_LIMIT_SET_CALLS` is unset or `False`. Every WebSocket event then emits a redundant `SET search_path`, which can exhaust the Postgres connection pool under LiveView load. The message also notes that django-tenants is deprecated as a multi-tenancy strategy for djust apps.

**Fix**: Migrate to djust's built-in row-level multi-tenancy (`djust.tenants`); see [Multi-Tenant](multi-tenant.md) and [Migrating from django-tenants](migrating-from-django-tenants.md). As a stopgap, set `TENANT_LIMIT_SET_CALLS = True`. Suppress with `DJUST_CONFIG = {"suppress_checks": ["C014"]}`.

---

### C015: Invalid or unknown DJUST_CONFIG['extensions'] adapter

**Severity**: Error

**What causes it**: `DJUST_CONFIG['extensions']` is not a list ("DJUST_CONFIG['extensions'] must be a list of adapter names, got ..."), or it names an adapter djust doesn't know ("DJUST_CONFIG['extensions'] lists unknown adapter '...'. It will be ignored -- no script is injected and the adapter's hook never mounts.").

**Fix**: Use a list of known adapter names, e.g. `DJUST_CONFIG = {"extensions": ["chart"]}`. The check's hint lists the available adapters.

---

### C016: TEMPLATES backend order

**Severity**: Warning

**What causes it**: One of three `TEMPLATES` shapes:

- a `DjangoTemplates` backend is listed before `DjustTemplateBackend` ("every template the Django engine can find is rendered by Django and never reaches djust");
- there is a `DjustTemplateBackend` entry but no `DjangoTemplates` entry after it ("the admin / admindocs templates cannot render");
- the admin is installed, the `DjustTemplateBackend` entry comes first with `APP_DIRS` on, and its `OPTIONS['context_processors']` lacks `auth`, `messages` or `request` ("the admin index fails (KeyError: 'user') without the auth processor"). The djust engine renders the admin's templates in that shape, and Django's own admin checks only look at `DjangoTemplates` entries.

**Fix**: Put the `DjustTemplateBackend` entry first with `django.template.context_processors.request`, `django.contrib.auth.context_processors.auth` and `django.contrib.messages.context_processors.messages` in its `context_processors`, and add a `django.template.backends.django.DjangoTemplates` entry after it as the fallback for admin and contrib templates (the shape `djust new --with-db` emits). Suppress with `DJUST_CONFIG = {"suppress_checks": ["C016"]}`.

---

### C018: Deprecated LIVEVIEW_CONFIG key

**Severity**: Warning

**What causes it**: `LIVEVIEW_CONFIG` or `DJUST_CONFIG` sets one of `jit_cache_backend`, `jit_cache_dir`, `jit_redis_url`, `debug_components`, `component_wrapper_class` or `component_loading_class`. djust has defaults for these keys but never reads them, so setting one has no effect.

**Fix**: Remove the key. djust 1.3 removes them. Suppress with `DJUST_CONFIG = {"suppress_checks": ["C018"]}`.

---

### C019: Unknown PRESENCE_BACKEND

**Severity**: Warning

**What causes it**: `DJUST_CONFIG['PRESENCE_BACKEND']` is set to a value djust doesn't know ("DJUST_CONFIG['PRESENCE_BACKEND'] is '...', which djust does not know; presence falls back to the in-memory backend (one process only)."). A dotted class path counts as unknown: the setting takes a short name.

**Fix**: Use `'redis'` or `'tenant_redis'` for presence shared between processes, or `'memory'` / `'tenant_memory'` for a single process. Suppress with `DJUST_CONFIG = {"suppress_checks": ["C019"]}`.

---

### C301: Invalid VDOM cache TTL

**Severity**: Error

**What causes it**: `DJUST_VDOM_CACHE_TTL_SECONDS` (or `LIVEVIEW_CONFIG["service_worker"]["vdom_cache_ttl_seconds"]`) is not a positive integer. Message: "DJUST_VDOM_CACHE_TTL_SECONDS must be a positive integer." A TTL <= 0 disables expiry, so the service worker could serve indefinitely stale HTML on back-navigation.

**Fix**: Set a positive number of seconds (the default is 1800).

---

### C302: Invalid VDOM cache size

**Severity**: Error

**What causes it**: `DJUST_VDOM_CACHE_MAX_ENTRIES` (or `LIVEVIEW_CONFIG["service_worker"]["vdom_cache_max_entries"]`) is below 1. Message: "DJUST_VDOM_CACHE_MAX_ENTRIES must be >= 1." A max of 0 evicts every entry on insertion and silently disables the cache.

**Fix**: Set it to 1 or more (the default is 50).

---

### C303: VDOM cache disabled

**Severity**: Info

**What causes it**: `DJUST_VDOM_CACHE_ENABLED` (or `LIVEVIEW_CONFIG["service_worker"]["vdom_cache_enabled"]`) is `False`. Message: "DJUST_VDOM_CACHE_ENABLED is False; VDOM cache disabled." Back-navigation falls through to a fresh mount and render instead of an instant paint.

**Fix**: Nothing is broken. Re-enable the cache, or suppress with `DJUST_CONFIG = {"suppress_checks": ["C303"]}`.

---

### C304: State snapshot with PII-like attribute names

**Severity**: Warning

**What causes it**: A LiveView sets `enable_state_snapshot = True` and declares public class attributes or annotations whose names look sensitive (matching `password`, `token`, `secret`, `api_key`, `pii`, `ssn`, `credit_card`, `bearer`, `private_key`, `auth_header`, `sensitive` or `credential`). Message: "<view>: enable_state_snapshot=True with PII-like attribute names: ...". State snapshots are cached client-side by the service worker, so these values would be stored in browser cache storage.

**Fix**: Make the attributes private (leading `_`), or turn off `enable_state_snapshot` for this view.

---

### C401: Hot View Replacement without watchdog

**Severity**: Warning (DEBUG only)

**What causes it**: Hot View Replacement and hot reload are enabled (the defaults), but the `watchdog` package is not installed. Message: "Hot View Replacement is enabled but watchdog is not installed." Code changes won't hot-swap live view instances.

**Fix**: `pip install watchdog`, or disable HVR. See [Hot View Replacement](hot-view-replacement.md).

---

### C501: Time-travel debugging enabled globally

**Severity**: Info (DEBUG only)

**What causes it**: `LIVEVIEW_CONFIG['time_travel_enabled']` is `True`. Message: "Time-travel debugging is enabled globally (LIVEVIEW_CONFIG['time_travel_enabled']=True)." This is a discoverability notice; individual views still need `time_travel_enabled = True` to allocate a buffer.

**Fix**: None needed. See [Time-Travel Debugging](time-travel-debugging.md).

---

### C502: Invalid time_travel_max_events

**Severity**: Error (DEBUG only)

**What causes it**: `time_travel_max_events` in the djust config is not a positive integer. Message: "time_travel_max_events must be a positive integer (got ...)." The time-travel ring buffer raises `ValueError` on a non-positive cap, which breaks `LiveView.__init__` for any view with `time_travel_enabled = True`.

**Fix**: Set a positive integer (the default is 100).

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

**Abstract base classes**: if the class is an abstract base never mounted directly (no `template_name` by design), mark it explicitly:

```python
class BaseLiveView(LiveView):
    abstract = True   # skips all per-class V0xx/Q0xx checks for this class (not inherited)
    login_required = True
```

**Global suppression** (less preferred — turns the check off everywhere):

```python
# settings.py
DJUST_CONFIG = {"suppress_checks": ["V001"]}  # honored as of #1604
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

**What causes it**: A public method name matches event handler naming patterns (e.g., `on_*`, `toggle_*`, `select_*`, `update_*`, `delete_*`, `create_*`, `add_*`, `remove_*`, `save_*`, `cancel_*`, `submit_*`, `close_*`, `open_*`) but is not decorated with `@event_handler`. `handle_*` methods are not flagged: an undecorated `handle_*` method is the way to write a handler that server push can call and browsers cannot.

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

**Abstract base classes**: mark abstract base views with `abstract = True` to skip V005 (and every other per-class V0xx/Q0xx check) for that specific class:

```python
class BaseLiveView(LiveView):
    abstract = True   # skips all per-class V0xx/Q0xx checks for this class
```

**Global suppression**: `DJUST_CONFIG = {"suppress_checks": ["V005"]}` (honored as of #1604).

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

### V009: Invalid on_mount hooks

**Severity**: Warning

**What causes it**: A LiveView's `on_mount` is not a list or tuple ("<view>: 'on_mount' should be a list of hook functions."), or one of its entries is not callable ("<view>: on_mount[i] is not callable (...).").

**Fix**: Set `on_mount = [hook1, hook2]`, where each entry is a callable hook function.

---

### V010: TutorialMixin after LiveView in the bases

**Severity**: Error

**What causes it**: A class lists `LiveView` before `TutorialMixin` in its bases. Message: "<view>: TutorialMixin must be listed before LiveView in bases." Django's `View.__init__` does not call `super().__init__()`, so mixins listed after `LiveView` are never initialised.

**Fix**: Change `class MyView(LiveView, TutorialMixin)` to `class MyView(TutorialMixin, LiveView)`.

---

### V011: Sticky child snapshots state but its parent doesn't

**Severity**: Warning

**What causes it**: A view embedded as a sticky child sets `enable_state_snapshot = True`, but the parent that embeds it does not. Message: "<child>: used as a sticky child with enable_state_snapshot=True, but embedding parent <parent> does not opt in — the child's state will be silently dropped on reconnect." A sticky child is restored across a WebSocket reconnect only when both the child and its embedding parent opt in.

**Fix**: Set `enable_state_snapshot = True` on the parent as well, or suppress with `DJUST_CONFIG = {"suppress_checks": ["V011"]}`. See [Sticky Child Persistence](sticky-child-persistence.md).

---

### V012: Sticky child template declares its own dj-view

**Severity**: Warning

**What causes it**: A view embedded with `{% live_render ... sticky=True %}` has a template whose root declares `dj-view`. Message: "<view>: sticky child template declares its own 'dj-view' on its root — this nests a duplicate dj-view inside the sticky wrapper and breaks the child's client-side mount (its events won't bind)." The framework emits the wrapper element with the `dj-view` binding itself.

**Fix**: Remove `dj-view` from the sticky child's root element. See [Sticky LiveViews](sticky-liveviews.md).

---

### V013: dispatch()/get()/post() override that won't run over WebSocket

**Severity**: Warning

**What causes it**: A LiveView (or one of its ancestors) overrides `dispatch()`, `get()` or `post()`. Message: "<view>: <ancestor> overrides <methods>, which will NOT run on a WebSocket mount (the WS path calls mount() directly, never dispatch()/get()/post())."

**Fix**: Move the setup into `mount(self, request, **kwargs)` so it runs on every transport. Mark abstract base classes with `abstract = True`, or suppress with `DJUST_CONFIG = {"suppress_checks": ["V013"]}`.

---

### V014: Time travel records PII-like fields

**Severity**: Warning

**What causes it**: A view sets `time_travel_enabled = True`, and its model or form declares fields whose names look like PII that are not listed in `time_travel_excluded_fields`. Message: "<view>: time_travel_enabled = True, and its model/form declares field(s) whose names look like PII and are not in time_travel_excluded_fields: ...". Time-travel snapshots can be exported as a shareable bug-capture blob.

**Fix**: List the sensitive public-state keys in `time_travel_excluded_fields` on the view, or suppress with `DJUST_CONFIG = {"suppress_checks": ["V014"]}` if they never reach the view's public state. See [Bug Capture](bug-capture.md).

---

### V015: djust's own LiveViews blocked by LIVEVIEW_ALLOWED_MODULES

**Severity**: Warning

**What causes it**: `LIVEVIEW_ALLOWED_MODULES` is set, your URLconf routes a LiveView that djust ships (the component gallery, the theme gallery, the admin extension), and the list doesn't admit it. An explicit list replaces the default, which includes `"djust"`, so those pages render but never mount ("View not mounted. Please reload the page."). V005 doesn't cover this case because it skips classes defined in djust.

**Fix**: Add `"djust"` to `LIVEVIEW_ALLOWED_MODULES`, as `djust new` does since 1.2.1. Suppress with `DJUST_CONFIG = {"suppress_checks": ["V015"]}`.

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

**Note**: Prior to [#303](https://github.com/djust-org/djust/issues/303), this check incorrectly warned on views with `login_required = False`. The check now correctly distinguishes between intentionally public views (`False`) and views that haven't addressed authentication at all (`None`).

**Related**: [Authentication Guide](authentication.md)

---

### S006: Tenant isolation is fail-open

**Severity**: Warning

**What causes it**: `DJUST_TENANTS['STRICT_MODE']` is set to `False`. Message: "DJUST_TENANTS['STRICT_MODE'] is set to False — djust tenant isolation is fail-OPEN. Tenant-scoped queries that run without a tenant bound to the current context will return EVERY tenant's rows instead of an empty set, risking cross-tenant data disclosure (especially on the WebSocket/SSE live path)."

**Fix**: Remove `STRICT_MODE` (or set it to `True`) to keep fail-closed isolation. If you must keep it, scope every query explicitly (`Model.objects.unscoped(reason=...)` for deliberate cross-tenant reads). See [Multi-Tenant](multi-tenant.md).

---

### S007: Unsafe rendering of a client-supplied filename

**Severity**: Warning

**What causes it**: A template renders an upload entry's `client_name` with the
`|safe` filter, e.g. `{{ upload_entry.client_name|safe }}`. The `client_name` is
the attacker-controlled original filename of an upload, stored without
sanitisation; `|safe` disables Django's auto-escaping, so a filename containing
`<script>...</script>` renders as live HTML — a **stored XSS** vector.

**Fix**: Remove the `|safe` filter so auto-escaping (the safe default) applies:

```html
<!-- Unsafe: filename is attacker-controlled -->
{{ upload_entry.client_name|safe }}

<!-- Safe: auto-escaping neutralises HTML in the filename -->
{{ upload_entry.client_name }}
```

If the value is genuinely pre-sanitised server-side, escape it explicitly with
`django.utils.html.escape()` before rendering, or suppress the check:

```python
# settings.py — only if client_name is pre-sanitised
DJUST_CONFIG = {"suppress_checks": ["S007"]}  # or "djust.S007"
```

**Detection**: matches `{{ <expr>.client_name|safe }}` (whitespace around `|`
tolerated); word-boundary guards mean `notclient_name` and `client_name_foo` do
not trigger it.

**Related**: [#1821](https://github.com/djust-org/djust/issues/1821), S001 (`mark_safe()` with f-string)

---

### S008: Upload client_name used in a storage path

**Severity**: Warning

**What causes it**: Python code uses an upload entry's `client_name` in a storage path or key. Message: "<file>:<line> -- upload `client_name` used in a storage path/key. `client_name` is the raw attacker-controlled original filename (path/object-key injection: CWE-22 / CWE-73)."

**Fix**: Use `entry.safe_client_name` (basename only, traversal neutralised) for the path or key, and keep `client_name` for display only. Suppress with `DJUST_CONFIG = {"suppress_checks": ["S008"]}` if the value is pre-sanitised. See [Uploads](uploads.md).

---

### S009: View-level auth with ungated public handlers

**Severity**: Warning

**What causes it**: A LiveView declares view-level auth (`login_required`, `permission_required` or a Django auth mixin) and exposes a public `@event_handler` with no per-handler authorization gate. Message: "<file>:<line> -- LiveView '<View>' declares view-level auth but exposes the public @event_handler '<handler>' with no per-handler authorization gate. A user who passes the view's mount auth can call this handler."

**Fix**: If the handler needs finer authorization, add `@permission_required(...)` to it or a `check_permissions()` override that inspects the event. Rename it with a leading underscore if it isn't meant to be client-callable. If view-level auth is sufficient, suppress with `# noqa: S009` on the handler or `DJUST_CONFIG = {"suppress_checks": ["S009"]}`.

---

### S011: Inline script inside a LiveView root without a CSP

**Severity**: Warning

**What causes it**: A template has an executable inline `<script>` inside a `dj-root`/`dj-view` subtree, and no Content-Security-Policy setting is configured. Message: "<file>:<line> -- inline <script> with executable JS inside a LiveView template and no Content-Security-Policy is configured. Inline scripts inside the dj-root are not re-executed after djust morphs the mount HTML (#1848), and a strict CSP would block them."

**Fix**: Move the JS into a static module, or into a base-template block rendered after the `dj-root` closes. If the inline script is intentional, add a CSP nonce or place it outside the root. Suppress with `{# noqa: S011 #}` on the script line or `DJUST_CONFIG = {"suppress_checks": ["S011"]}`.

---

### S012: Auth in dispatch() is not enforced over WebSocket

**Severity**: Error

**What causes it**: A LiveView gates auth with `@method_decorator(..., name="dispatch")`, or overrides `dispatch()` with auth logic. Either is enforced only on the HTTP GET, not on the WebSocket. Message: "<file>:<line> -- LiveView '<View>' gates auth via @method_decorator(..., name='dispatch'); this is NOT enforced over WebSocket (only on the HTTP GET)." (or "... overrides dispatch() with auth logic; ...").

**Fix**: Use djust's `login_required` / `permission_required` class attributes, a `check_permissions()` method, or a Django auth mixin (`LoginRequiredMixin`, `PermissionRequiredMixin`, `UserPassesTestMixin`). These are honored on every transport. See [Authentication](authentication.md).

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

**What causes it**: A template contains djust directives (`dj-click`, `dj-input`, `dj-change`, `dj-submit`, `dj-model`) or a `dj-view` attribute, but no element has the `dj-root` attribute. The check skips templates that use `{% extends %}` since the root may be in a parent template.

**What you see**: Nothing breaks. `dj-root` is auto-inferred from `dj-view` on both the client and the server, so this is informational.

**Fix**: Optionally add `dj-root` next to `dj-view` for clarity, or suppress the check with `DJUST_CONFIG = {"suppress_checks": ["T002"]}`:

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

**What causes it**: A template uses `document.addEventListener('djust:...')` for a djust custom event that is dispatched on `window` (e.g. `djust:push_event`, `djust:before-navigate`, `djust:error`, `djust:shell-swapped`, `djust:vdom-cache-applied`, `djust:upload:*`).

**What you see**: The event listener never fires. No error in the console -- completely silent failure.

> **Exempt (#1809)**: djust dispatches a second family of events on `document` (not `window`): `djust:navigate-start`, `djust:navigate-end`, `djust:hvr-applied`, `djust:layout-changed`, `djust:ws-reconnected`, `djust:time-travel-state`, `djust:time-travel-event`. Listening for those on `document` is **correct** and is no longer flagged by T004.

**Fix**:

```html
<!-- WRONG -- window-dispatched event, never fires on document -->
<script>
document.addEventListener('djust:push_event', (e) => { ... });
</script>

<!-- CORRECT -->
<script>
window.addEventListener('djust:push_event', (e) => { ... });
</script>

<!-- ALSO CORRECT -- navigate-end is dispatched on document, not window -->
<script>
document.addEventListener('djust:navigate-end', (e) => { ... });
</script>
```

**Suppress globally**: `DJUST_CONFIG = {"suppress_checks": ["T004"]}` (works as of #1809) or `SILENCED_SYSTEM_CHECKS = ["djust.T004"]`.

---

### T005: dj-view and dj-root on different elements

**Severity**: Warning

**What causes it**: A template has `dj-view` on one element and `dj-root` on a different element. These attributes must be on the same root element.

**What you see**: VDOM patches may not be applied correctly, or updates fail silently.

**Fix**:

```html
<!-- WRONG -- different elements -->
<div dj-view="myapp.views.MyView">
    <div dj-root>
        <p>Content</p>
    </div>
</div>

<!-- CORRECT -- same element -->
<div dj-view="myapp.views.MyView" dj-root>
    <p>Content</p>
</div>
```

---

### T010: dj-click used for navigation

**Severity**: Warning

**What causes it**: A template element uses `dj-click` together with navigation-related data attributes (`data-view`, `data-tab`, `data-page`, or `data-section`). This pattern suggests you're implementing navigation, which should use `dj-patch` instead for proper URL updates and browser history support.

**What you see**: Navigation works but the browser URL doesn't update, and the back button doesn't work as expected. Users cannot bookmark or share specific views.

**Fix**:

```html
<!-- WRONG -- dj-click for navigation -->
<button dj-click="show_settings" data-view="settings">Settings</button>

<!-- CORRECT -- use dj-patch for navigation -->
<a href="?view=settings" dj-patch>Settings</a>
```

A bare `dj-patch` on an `<a>` patches to its `href`. (A non-empty `dj-patch` value is itself the target URL.)

In your LiveView, read the URL parameter in the `handle_params()` lifecycle hook. It is not an event handler, so don't decorate it with `@event_handler`:

```python
class MyView(LiveView):
    def mount(self, request, **kwargs):
        self.current_view = "dashboard"

    def handle_params(self, params, uri):
        # Called after mount and on every dj-patch / back-forward URL change
        self.current_view = params.get("view", "dashboard")
```

**Related**: [Navigation Guide](navigation.md)

---

### T011: Unsupported template tag

**Severity**: Warning

**What causes it**: A LiveView template uses a Django template tag that the Rust renderer doesn't implement. Message: "<file>:<line> -- unsupported template tag '{% <tag> %}' will be silently ignored by Rust renderer." In 1.2.0rc10 the unsupported-tag set is empty (every tag Django registers is handled), so this check does not currently fire.

**Fix**: Pre-compute the value in your view and pass it as a context variable, or use a supported alternative. Suppress with `{# noqa: T011 #}`.

---

### T012: dj-* directives without dj-view

**Severity**: Warning

**What causes it**: A template uses `dj-*` event directives but has no `dj-view` attribute. Message: "<file> -- template uses dj-* event directives but has no dj-view attribute."

**Fix**: Add `dj-view="yourapp.views.YourView"` to the root element. If the template is an intentional fragment included from a parent LiveView root, add a `{# djust:partial #}` comment, or suppress with `DJUST_CONFIG = {"suppress_checks": ["T012"]}`.

---

### T013: Invalid dj-view value

**Severity**: Warning

**What causes it**: A `dj-view` attribute has an empty or invalid value ("<file>:<line> -- dj-view has empty or invalid value '...'."), or it names a context variable djust never provides, such as `dj-view="{{ view_path }}"` ("... names a context variable djust never provides; it renders as dj-view="" and the page cannot mount.").

**Fix**: Write the literal dotted path, e.g. `dj-view="myapp.views.MyView"`, or put `dj-root` on the element and let djust stamp `dj-view` server-side.

---

### T014: Deprecated data-dj-id attribute

**Severity**: Warning

**What causes it**: A template contains `data-dj-id`. Message: "<file>:<line> -- deprecated 'data-dj-id' attribute (renamed to 'dj-id' in v1.0)."

**Fix**: Replace `data-dj-id` with `dj-id` in hand-authored HTML.

---

### T015: Legacy data-djust-view / data-djust-root attribute

**Severity**: Warning

**What causes it**: A template uses the pre-1.0 root attributes. Message: "<file>:<line> -- legacy '<attr>' attribute detected."

**Fix**: Change `data-djust-view` to `dj-view` and `data-djust-root` to `dj-root`. Suppress with `DJUST_CONFIG = {"suppress_checks": ["T015"]}`.

---

### T016: dj-navigate with no LiveView routes

**Severity**: Warning

**What causes it**: Templates use `dj-navigate`, but no LiveView routes were found in the URLconf, so the client route map is empty. Message: "dj-navigate is used in N location(s) (first: <file>:<line>) but no LiveView routes were found in the URLconf, so the client route map is empty — dj-navigate will silently full-reload instead of navigating over the WebSocket."

**Fix**: Make sure your views subclass `djust.LiveView` and are wired into `urlpatterns`. Suppress with `DJUST_CONFIG = {"suppress_checks": ["T016"]}`. See [Navigation](navigation.md).

---

### T017: dj-view or dj-root on a table-section element

**Severity**: Warning

**What causes it**: `dj-view` or `dj-root` is on a `<tbody>`, `<thead>`, `<tfoot>`, `<tr>`, `<td>`, `<th>`, `<caption>`, `<col>` or `<colgroup>`. Message: "<file>:<line> -- '<attr>' is on a <tag> table-section element, which is foster-parented out of the tree at render time (the rendered output silently drops the table rows, with no error)."

**Fix**: Put the attribute on a wrapping element (the `<table>` or a surrounding `<div>`). Suppress with `DJUST_CONFIG = {"suppress_checks": ["T017"]}`.

---

### T018: Undefined template variable

**Severity**: Warning (undefined variable) / Info (templates skipped)

**What causes it**: A LiveView's template references a variable that is never set via a class attribute, a `self.<name> = ...` assignment, or a literal `get_context_data()` return key, and isn't a framework- or Django-injected name. Message: "<view> -- template references undefined variable '<name>' at line N (<template>) -- it resolves to nothing and renders as empty string, with no error." Views whose templates use `{% extends %}` are skipped, and one Info message reports the count ("T018: skipped N view(s) whose template(s) use {% extends %} ...").

**Fix**: Fix the typo, or, if the variable is set dynamically, silence it with a `{# djust_typecheck: noqa <name> #}` template comment. For extends-based templates, run `manage.py djust_typecheck`. Suppress globally with `DJUST_CONFIG = {"suppress_checks": ["T018"]}`. See [Template type checking](typecheck.md).

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

### Q007: Key in both static_assigns and temporary_assigns

**Severity**: Warning

**What causes it**: A LiveView lists the same keys in `static_assigns` and `temporary_assigns`. Message: "<view>: keys ... appear in both static_assigns and temporary_assigns." A key can't be both static (never re-sent) and temporary (cleared after render).

**Fix**: Remove the key from one of the two lists.

---

### Q010: Navigation state set without patch()

**Severity**: Info

**What causes it**: An event handler sets a navigation-state attribute (`active_view`, `current_tab`, `selected_page`, `current_section`, `active_tab` or `selected_view`) that the view also uses as a URL parameter elsewhere via `self.patch()`, but this handler updates it without `patch()` or `handle_params`. Message: "<file>:<line> -- Event handler '<View>.<handler>()' sets <attr> without using patch(). Consider using dj-patch for URL updates."

**Fix**: Use `dj-patch="?tab=value"` and read the value in `handle_params()` (see T010), or add `# noqa: Q010` on the handler.

---

## Audit / Static Security Checks (A0xx)

These checks were added as follow-ups to the 2026-04-10 a downstream consumer penetration test. They extend `djust_audit` / `djust_check` with configuration-level security checks that catch misconfigurations Django's own `check --deploy` cannot see. All A0xx checks fire during `manage.py check --tag djust` and appear alongside the C0xx/S0xx findings.

### A001: WebSocket router missing AllowedHostsOriginValidator

**Severity**: Error

**What causes it**: The `"websocket"` entry in your `ProtocolTypeRouter` is not wrapped in `channels.security.websocket.AllowedHostsOriginValidator`. Any cross-origin page on the internet can open a WebSocket to your app, mount any LiveView, and dispatch events from a victim's browser (CSWSH — see [#653](https://github.com/djust-org/djust/issues/653)).

**What you see**: No runtime symptom until a pentester or attacker finds it. An `A001` warning from `manage.py check` at startup.

**Fix**:

```python
# asgi.py
from channels.routing import ProtocolTypeRouter, URLRouter
from djust.routing import DjustMiddlewareStack   # Wraps in AllowedHostsOriginValidator by default since 0.4.1

application = ProtocolTypeRouter({
    "http": get_asgi_application(),
    "websocket": DjustMiddlewareStack(
        URLRouter(websocket_urlpatterns)
    ),
})
```

Or wrap manually if you're using `channels.auth.AuthMiddlewareStack`:

```python
from channels.auth import AuthMiddlewareStack
from channels.routing import ProtocolTypeRouter, URLRouter
from channels.security.websocket import AllowedHostsOriginValidator

application = ProtocolTypeRouter({
    "websocket": AllowedHostsOriginValidator(
        AuthMiddlewareStack(URLRouter(websocket_urlpatterns))
    ),
})
```

**Prerequisite**: `settings.ALLOWED_HOSTS` must not contain `"*"`.

---

### A010: ALLOWED_HOSTS is only ["*"] in production

**Severity**: Error

**What causes it**: `settings.ALLOWED_HOSTS == ["*"]` with `DEBUG=False`. The wildcard disables Django's Host header defense entirely, and re-opens CSWSH because `AllowedHostsOriginValidator` reads the same setting.

Not raised when `DEBUG=True`, or when both `SECURE_PROXY_SSL_HEADER` and `DJUST_TRUSTED_PROXIES` are set (you are asserting a trusted L7 proxy).

**Fix**: Set `ALLOWED_HOSTS` to the explicit hostnames your app serves:

```python
# settings.py
ALLOWED_HOSTS = ["myapp.example.com", "api.example.com"]
```

---

### A011: ALLOWED_HOSTS mixes "*" with explicit hosts

**Severity**: Error

**What causes it**: `settings.ALLOWED_HOSTS = ["myapp.example.com", "*"]`. Django accepts any Host header as soon as `"*"` is present — the explicit hostname is meaningless once the wildcard is in the list. Authors often mix them thinking they're "also allowing the explicit host for clarity."

Not raised when `DEBUG=True`, or when both `SECURE_PROXY_SSL_HEADER` and `DJUST_TRUSTED_PROXIES` are set (you are asserting a trusted L7 proxy).

**Fix**: Remove `"*"` and keep only the explicit hostnames.

---

### A012: USE_X_FORWARDED_HOST=True + wildcard ALLOWED_HOSTS

**Severity**: Error

**What causes it**: `USE_X_FORWARDED_HOST=True` makes Django trust the `X-Forwarded-Host` header. Combined with wildcard `ALLOWED_HOSTS`, there is no validation of that header — attackers can inject any Host.

Not raised when `DEBUG=True`, or when both `SECURE_PROXY_SSL_HEADER` and `DJUST_TRUSTED_PROXIES` are set (you are asserting a trusted L7 proxy).

**Fix**: Set `ALLOWED_HOSTS` to explicit hostnames, or set `USE_X_FORWARDED_HOST=False` if your reverse proxy is not configured to set it safely.

---

### A014: SECRET_KEY starts with "django-insecure-" in production

**Severity**: Error

**What causes it**: The Django scaffold default `SECRET_KEY` is a placeholder that starts with `"django-insecure-"`. It's meant to be replaced before deployment. An attacker who knows the value (anyone with access to the source repo) can forge session cookies and password-reset tokens.

**Fix**: Generate a new key and load it from an environment variable:

```python
import os
from django.core.management.utils import get_random_secret_key

SECRET_KEY = os.environ.get("DJANGO_SECRET_KEY") or get_random_secret_key()
```

Then set `DJANGO_SECRET_KEY` in your deployment environment.

---

### A020: LOGIN_REDIRECT_URL hardcoded with multi-group auth

**Severity**: Warning

**What causes it**: `LOGIN_REDIRECT_URL` is a single hardcoded path (e.g. `/dashboard/`) but the project uses a role-based auth model (detected via known packages like `rolepermissions`, `rules`, `guardian`, or via multiple entries in the `Group` table). All roles land on the same page after login — both a UX problem and a strong signal that per-role access control wasn't considered.

**What you see**: A lower-privilege user logs in and lands on the same dashboard as a supervisor, immediately exposing any broken RBAC. This is how a downstream consumer pentest team found broken role isolation in minutes.

**Fix**: Subclass `django.contrib.auth.views.LoginView` and override `get_success_url()`:

<!-- The import is correct Django; the checker's minimal tests.settings
     cannot load django.contrib.auth.views. -->
<!-- doc-snippet-check: skip -->
```python
from django.contrib.auth.views import LoginView
from django.urls import reverse

class RoleAwareLoginView(LoginView):
    def get_success_url(self):
        user = self.request.user
        if user.groups.filter(name="Supervisor").exists():
            return reverse("supervisor-dashboard")
        if user.groups.filter(name="Examiner").exists():
            return reverse("examiner-dashboard")
        return reverse("claimant-dashboard")
```

---

### A030: django.contrib.admin without brute-force protection

**Severity**: Warning

**What causes it**: `django.contrib.admin` is in `INSTALLED_APPS` but no known brute-force protection package is present. The stock Django admin has no built-in rate limiting or lockout.

**Fix**: Install one of the recognized packages:

```bash
pip install django-axes
```

```python
# settings.py
INSTALLED_APPS = [
    # ...
    "django.contrib.admin",
    "axes",
]

MIDDLEWARE = [
    # ...
    "axes.middleware.AxesMiddleware",
]
```

Recognized packages: `axes`, `defender`, `brutebuster`, `ratelimit`, `django_ratelimit`, `django_axes`.

---

### A031: Observability endpoints without LocalhostOnlyObservabilityMiddleware

**Severity**: Warning

**What causes it**: The djust observability URLs (`_djust/observability/`) are wired, but `LocalhostOnlyObservabilityMiddleware` is not in `MIDDLEWARE`. Message: "djust observability endpoints (_djust/observability/) are wired but LocalhostOnlyObservabilityMiddleware is not in MIDDLEWARE." The endpoints also gate themselves to localhost, so this is defense in depth.

**Fix**: Add the middleware (under DEBUG), which rejects non-localhost requests before the view runs.

---

### A070: dj_activity without a name

**Severity**: Warning

**What causes it**: A `{% dj_activity %}` block has no `name` argument. Message: "<file>:<line> -- {% dj_activity %} is missing a 'name' argument." Without a name, `ActivityMixin` cannot route events or track visibility for the region.

**Fix**: Give every block a non-empty name: `{% dj_activity "my-panel" visible=expr %}`. See [Activity](activity.md).

---

### A071: Duplicate dj_activity name

**Severity**: Error

**What causes it**: Two `{% dj_activity %}` blocks in one template share a name. Message: "<file>:<line> -- duplicate {% dj_activity %} name '<name>' (first declared at line N)."

**Fix**: Rename one of the blocks, or split the template if the regions should be tracked independently.

---

### A072: Non-LiveView class in an admin widget slot

**Severity**: Warning

**What causes it**: A djust admin `change_form_widgets` or `change_list_widgets` slot contains a class that isn't a LiveView subclass. Message: "Admin <site> -- <slot> on <model> contains non-LiveView class '<name>'. Widget slots can only embed djust LiveView subclasses."

**Fix**: Make the widget a subclass of `djust.LiveView`, or remove it from the slot. See [Admin Widgets](admin-widgets.md).

---

### A073: Progress actions with multiple ASGI workers

**Severity**: Info

**What causes it**: An admin site uses `@admin_action_with_progress` and `DJUST_ASGI_WORKERS` is greater than 1. The progress widget keeps job state in a process-local dict, so the progress URL must reach the worker that started the job.

**Fix**: Run a single ASGI worker, or enable sticky sessions on your load balancer. Unset `DJUST_ASGI_WORKERS` (or set it to 1) to silence the check. See [Admin Widgets](admin-widgets.md).

---

### A075: live_render with both sticky=True and lazy=True

**Severity**: Warning

**What causes it**: A `{% live_render %}` tag passes both `sticky=True` and `lazy=True`. Message: "<file>:<line> -- {% live_render %} has both sticky=True and lazy=True — these kwargs are mutually exclusive." Sticky reattach needs the slot to exist at mount time, and lazy defers it.

**Fix**: Pick one. Suppress with `DJUST_CONFIG = {"suppress_checks": ["A075"]}` if you have a deliberate reason.

---

### A090: djust_markdown in use

**Severity**: Info

**What causes it**: Templates use `{% djust_markdown %}`. Message: "{% djust_markdown %} is used in N location(s) (first: <file>:<line>) — djust is rendering Markdown server-side via the Rust pulldown-cmark backend with safe-by-default escaping (ENABLE_HTML never set, javascript: URLs neutralised, 10 MiB input cap)."

**Fix**: Informational. Suppress with `DJUST_CONFIG = {"suppress_checks": ["A090"]}`. See [Streaming Markdown](streaming-markdown.md).

---

## Database Notification Checks (D0xx)

### D001: Postgres LISTEN/NOTIFY unavailable

**Severity**: Warning

**What causes it**: The default database uses the PostgreSQL backend with the legacy `psycopg2` driver, and `psycopg` (psycopg3) is missing or older than 3.2. Message: "Postgres LISTEN/NOTIFY (db.notifications) is unavailable because psycopg[binary]>=3.2 is not installed." Apps that use `@notify_on_save` or `db.listen()` will hit a permanent-failure warning at the first NOTIFY attempt.

**Fix**: `pip install 'psycopg[binary]>=3.2'`, or silence with `SILENCED_SYSTEM_CHECKS = ["djust.D001"]` if you don't use `db.notifications`. See [Database Notifications](database-notifications.md).

---

## Update Notice (U0xx)

### U001: Newer djust release or security advisory

**Severity**: Warning (published advisories affect the installed version) / Info (a newer release exists)

**What causes it**: With `DEBUG=True`, the cached update check reports either "SECURITY: djust <version> has N published advisory/advisories (...)" or "djust <latest> is available (you have <installed>)". The system check reads the cache only; it makes no network request.

**Fix**: Upgrade djust. Disable the notice with `DJUST_CONFIG = {"update_check": False}` or the `DJUST_NO_UPDATE_CHECK=1` environment variable. It is also skipped when `CI` is set.

---

## Accessibility Checks (Y0xx)

These scan LiveView templates. All are Warnings, so a false positive never fails `manage.py check`, and each can be suppressed with `DJUST_CONFIG['suppress_checks']`. See [Accessibility](accessibility.md).

### Y001: Control with no accessible name

**Severity**: Warning

**What causes it**: "<file>:<line> -- <tag> has no accessible name (icon-only content and no aria-label)."

**Fix**: Add `aria-label="..."` (or `aria-labelledby` / `title`) so the control's purpose is announced.

---

### Y002: img without alt

**Severity**: Warning

**What causes it**: "<file>:<line> -- <img> tag is missing an 'alt' attribute (WCAG 1.1.1)."

**Fix**: Use `alt="describe the image"` for informative images, or `alt=""` for decorative ones.

---

### Y003: Form control with no label

**Severity**: Warning

**What causes it**: "<file>:<line> -- <tag> form control has no associated label (WCAG 1.3.1)." Applies to `<input>`, `<select>` and `<textarea>`; `<input>` types `hidden`, `submit`, `button`, `reset` and `image` are not flagged.

**Fix**: Associate a label via `<label for="...">`, wrap the control in a `<label>`, or add `aria-label` / `aria-labelledby`. `<label for>` matching is per file, so a label in a different template isn't detected.

---

### Y004: Positive tabindex

**Severity**: Warning

**What causes it**: '<file>:<line> -- positive tabindex="N" overrides natural focus order (WCAG 2.4.3).'

**Fix**: Use `tabindex="0"` to add an element to the natural order, or `tabindex="-1"` to make it focusable only programmatically.

---

## Audio Checks (djust.audio.*)

These run for routed LiveViews that use `AudioMixin`. See [Audio](audio.md).

### djust.audio.E001: audio_banks value is not a SoundBank

**Severity**: Error

**What causes it**: "audio_banks values must be SoundBank instances"

**Fix**: Make every value in the view's `audio_banks` a `SoundBank`.

---

### djust.audio.W001: Declared sound asset not found

**Severity**: Warning

**What causes it**: "Declared sound asset not found: <path>". The staticfiles finders can't locate a sound file declared in a bank.

**Fix**: Add the file to a staticfiles directory before `collectstatic`.

---

## Theming Checks (djust_theming.*)

These are registered by the `djust.theming` app under Django's `compatibility` tag, so they run with a plain `manage.py check`, not with `--tag djust`.

### djust_theming.E001: theme_context processor missing

**Severity**: Error

**What causes it**: "djust.theming.context_processors.theme_context is not in any TEMPLATES backend's context_processors list. Theme template variables (theme_head, theme_switcher, etc.) will not be available."

**Fix**: Add `"djust.theming.context_processors.theme_context"` to `TEMPLATES[0]['OPTIONS']['context_processors']`.

---

### djust_theming.E002: Unknown theme preset

**Severity**: Error

**What causes it**: 'LIVEVIEW_CONFIG["theme"]["preset"] is set to "<name>", which is not a registered theme preset.'

**Fix**: Use one of the preset names listed in the check's hint.

---

### djust_theming.E003: Unknown design system

**Severity**: Error

**What causes it**: 'LIVEVIEW_CONFIG["theme"]["theme"] is set to "<name>", which is not a registered design system.'

**Fix**: Use one of the design system names listed in the check's hint.

---

### djust_theming.E004: Invalid css_prefix

**Severity**: Error

**What causes it**: 'css_prefix "<prefix>" contains invalid characters. Only letters, digits, and hyphens are allowed, and it must start with a letter.'

**Fix**: Use a prefix like `"djt-"` or `"myapp-"`.

---

### djust_theming.W001: Preset fails WCAG AA contrast

**Severity**: Warning

**What causes it**: The active theme preset has a colour pair below the WCAG AA minimum. Message: 'Preset "<name>" <mode> mode: <label> contrast ratio X:1 < Y:1 (WCAG AA)'.

**Fix**: Adjust the named foreground or background colour to reach at least the minimum ratio.

---

### djust_theming.W002: css_prefix without a trailing hyphen

**Severity**: Warning

**What causes it**: 'css_prefix "<prefix>" does not end with "-". Component classes will render as ".<prefix>btn" instead of ".<prefix>-btn".'

**Fix**: Add a trailing `-`, e.g. `"dj-"` instead of `"dj"`.

---

## Permissions Document Findings (P0xx)

These codes come from `manage.py djust_audit --permissions permissions.yaml` and validate the actual code against a committed declarative permissions document. See [Declarative Permissions Document](permissions-document.md) for the full setup guide.

### P001: View declared in permissions.yaml but not found in code

**Severity**: Error

**What causes it**: An entry in `permissions.yaml` points at a dotted view path that no longer exists in the codebase. Probably a stale declaration after a view was removed or renamed.

**Fix**: Remove the stale entry from `permissions.yaml`, or restore the view if it was deleted by mistake.

---

### P002: View found in code but not declared in permissions.yaml

**Severity**: Error (strict mode only)

**What causes it**: A new LiveView was added to the codebase without a corresponding entry in `permissions.yaml`. Raised unless the document sets `strict: false` (strict is the default).

**Fix**: Add the view to `permissions.yaml` with its intended auth config:

```yaml
views:
  myapp.views.NewView:
    public: true  # or: login_required: true, permissions: [...]
```

---

### P003: Document says public but code has auth

**Severity**: Error

**What causes it**: `permissions.yaml` declares `public: true` for a view but the code sets `login_required=True` or `permission_required=[...]`. Either the declaration is out of date or someone added auth to a previously public view without updating the document.

**Fix**: Reconcile the two sources of truth — either remove the `public: true` and declare the actual auth config, or revert the code change if the view really should be public.

---

### P004: Document says auth required but code has none

**Severity**: Error

**What causes it**: `permissions.yaml` declares `login_required: true` or `permissions: [...]` but the code has neither `login_required=True` nor `permission_required`, and there is no custom `check_permissions()` override or dispatch-based auth mixin.

**Fix**: Add the intended auth to the view class:

```python
class MyView(LiveView):
    login_required = True
    permission_required = ["myapp.view_something"]
```

---

### P005: Permission list mismatch

**Severity**: Error

**What causes it**: `permissions.yaml` lists one set of permissions for a view, but the code's `permission_required` attribute has a different set. Order is not significant; the comparison is set-equality.

**Fix**: Either update the document (if the code change was intentional) or revert the code (if the document is the source of truth).

---

### P006: object_scoping field not referenced

**Severity**: Warning (reserved)

**What causes it**: Reserved — not currently emitted. The code is registered for a future check that `object_scoping.fields: [...]` declared for a view are referenced in `get_object()` or equivalent. Today `object_scoping.fields` is documentation-only.

**Fix**: Verify manually that your `get_object()` implementation scopes the query by the declared fields.

---

### P007: Roles declaration (informational)

**Severity**: Info

**What causes it**: `permissions.yaml` lists `roles: [...]` for a view. djust cannot verify Django group membership at static-analysis time, so this is treated as documentation only. The info-level finding is recorded so reviewers can see which roles are expected.

**Fix**: Nothing to fix — the finding is informational. If you don't want to see these in output, filter by severity in CI.

---

## Live Runtime Probe Findings (L0xx)

These codes come from `manage.py djust_audit --live <url>` and reflect actual runtime behavior of a deployed environment. They catch misconfigurations that static analysis cannot see — middleware correctly configured in `settings.py` but the response is stripped by nginx / ingress / proxy, or a firewall allows what settings appear to deny.

### L001: Content-Security-Policy header missing

**Severity**: Error

**What causes it**: The live response has no `Content-Security-Policy` header. Common causes:
1. `csp.middleware.CSPMiddleware` is not in `MIDDLEWARE` in the active settings module.
2. An ingress / reverse proxy is stripping the header on the way out.
3. A response middleware runs after `CSPMiddleware` and removes it.

**Fix**: Verify the header appears in a dev `curl -sI http://localhost:8000/`. If it does, the reverse proxy is the culprit — check your nginx/ingress configuration for `proxy_hide_header` or CloudFront header stripping.

---

### L002: CSP contains 'unsafe-inline'

**Severity**: Warning

**What causes it**: `script-src` or `style-src` in the CSP header includes `'unsafe-inline'`, which negates most of CSP's XSS defense.

**Fix**: Enable nonce-based CSP (see [#655](https://github.com/djust-org/djust/issues/655) for djust's nonce support) and drop `'unsafe-inline'`:

```python
# settings.py
CSP_INCLUDE_NONCE_IN = ("script-src", "script-src-elem", "style-src", "style-src-elem")
CSP_SCRIPT_SRC = ("'self'",)
CSP_STYLE_SRC = ("'self'",)
```

---

### L003: CSP contains 'unsafe-eval'

**Severity**: Warning

**What causes it**: `script-src` includes `'unsafe-eval'`, allowing dynamic code execution via `eval()`, `new Function()`, etc.

**Fix**: Remove `'unsafe-eval'` from `CSP_SCRIPT_SRC`. If a third-party library requires it, isolate that library in an iframe with a separate CSP.

---

### L004: Strict-Transport-Security header missing

**Severity**: Error (on HTTPS URLs only)

**What causes it**: The HTTPS response has no `Strict-Transport-Security` header. HTTP URLs are exempt from this check.

**Fix**:

```python
# settings.py
SECURE_HSTS_SECONDS = 31_536_000  # 1 year
SECURE_HSTS_INCLUDE_SUBDOMAINS = True
SECURE_HSTS_PRELOAD = True
```

---

### L005: HSTS max-age below 1 year

**Severity**: Warning

**What causes it**: `Strict-Transport-Security: max-age=...` is less than `31536000` (1 year). The HSTS preload list requires at least 1 year.

**Fix**: Set `SECURE_HSTS_SECONDS = 31_536_000` (or higher) in `settings.py`.

---

### L006: HSTS missing 'includeSubDomains'

**Severity**: Info

**What causes it**: The HSTS header doesn't include `includeSubDomains`, so subdomains are not protected.

**Fix**: `SECURE_HSTS_INCLUDE_SUBDOMAINS = True` in `settings.py`.

---

### L007: HSTS missing 'preload'

**Severity**: Info

**What causes it**: The HSTS header doesn't include `preload`, so the domain can't be added to the HSTS preload list.

**Fix**: `SECURE_HSTS_PRELOAD = True` in `settings.py`, then submit to [hstspreload.org](https://hstspreload.org/).

---

### L008: X-Frame-Options header missing

**Severity**: Error

**What causes it**: No `X-Frame-Options` header. Your pages can be framed by any other site, enabling clickjacking.

**Fix**:

```python
# settings.py
X_FRAME_OPTIONS = "DENY"  # or "SAMEORIGIN"
```

Make sure `django.middleware.clickjacking.XFrameOptionsMiddleware` is in `MIDDLEWARE`.

---

### L009: X-Content-Type-Options missing or wrong value

**Severity**: Error

**What causes it**: The response has no `X-Content-Type-Options` header, or its value is not `nosniff`. Browsers may MIME-sniff content and render uploaded files as HTML.

**Fix**: `SECURE_CONTENT_TYPE_NOSNIFF = True` in `settings.py` (this is Django's default in modern versions).

---

### L010: Referrer-Policy header missing

**Severity**: Warning

**What causes it**: No `Referrer-Policy` header. The default browser behavior may leak full URLs in the `Referer` header of outgoing requests.

**Fix**: `SECURE_REFERRER_POLICY = "same-origin"` (or `"strict-origin-when-cross-origin"`).

---

### L011: Permissions-Policy header missing

**Severity**: Warning

**What causes it**: No `Permissions-Policy` header. Modern browsers allow the site to restrict access to powerful APIs (camera, microphone, geolocation, payment) — without this header, any same-origin script can request them.

**Fix**: Add a middleware that emits:

```
Permissions-Policy: camera=(), microphone=(), geolocation=(), payment=()
```

---

### L012: Cross-Origin-Opener-Policy missing

**Severity**: Info

**What causes it**: No `Cross-Origin-Opener-Policy` header. This isolates your window from cross-origin windows that open it (important for protecting against Spectre-class attacks).

**Fix**: `SECURE_CROSS_ORIGIN_OPENER_POLICY = "same-origin"` in `settings.py`.

---

### L013: Cross-Origin-Resource-Policy missing

**Severity**: Info

**What causes it**: No `Cross-Origin-Resource-Policy` header on the response. Your resources may be embedded by arbitrary cross-origin pages.

**Fix**: Set via a middleware or reverse proxy header: `Cross-Origin-Resource-Policy: same-site`.

---

### L014: Server header leaks version

**Severity**: Warning

**What causes it**: The `Server` response header contains something that looks like a version string (`nginx/1.25.3`, `Apache/2.4.58`, `Python/3.12`, `uvicorn/0.23`, etc.). Attackers can match the version to known CVEs.

**Fix**: Configure your reverse proxy to suppress the version:

```nginx
# nginx
server_tokens off;
```

---

### L015: X-Powered-By header present

**Severity**: Warning

**What causes it**: The response includes `X-Powered-By`, typically revealing the framework/language. Useful information for attackers; not useful for anything else.

**Fix**: Configure your reverse proxy to strip the header, or remove it at the application layer.

---

### L020: Session cookie missing HttpOnly

**Severity**: Error

**What causes it**: The `sessionid` (or equivalent) cookie doesn't have the `HttpOnly` attribute. JavaScript can read it via `document.cookie`, enabling session theft via XSS.

**Fix**: `SESSION_COOKIE_HTTPONLY = True` (Django's default).

---

### L021: Session cookie missing Secure

**Severity**: Error (on HTTPS URLs only)

**What causes it**: On an HTTPS response, the session cookie doesn't have the `Secure` attribute. The cookie may be sent over plaintext HTTP if the user's connection is downgraded.

**Fix**: `SESSION_COOKIE_SECURE = True` in `settings.py`.

---

### L022: Session cookie missing SameSite

**Severity**: Warning

**What causes it**: The session cookie has no `SameSite` attribute. Modern browsers default to `Lax` in most cases, but explicit declaration is safer.

**Fix**: `SESSION_COOKIE_SAMESITE = "Lax"` (or `"Strict"`).

---

### L023: CSRF cookie missing HttpOnly

**Severity**: Error

**What causes it**: The `csrftoken` cookie doesn't have the `HttpOnly` attribute.

**Fix**: `CSRF_COOKIE_HTTPONLY = True` in `settings.py`.

---

### L024: CSRF cookie missing Secure

**Severity**: Error (on HTTPS URLs only)

**What causes it**: The CSRF cookie doesn't have `Secure` on an HTTPS response.

**Fix**: `CSRF_COOKIE_SECURE = True` in `settings.py`.

---

### L040: /.git/config publicly accessible

**Severity**: Error

**What causes it**: A `GET /.git/config` request returns 2xx. The entire git history of your repository is exposed, including potentially-leaked credentials in commits.

**Fix**: Configure your reverse proxy to deny access to `/.git/`:

```nginx
# nginx
location ~ /\.git { deny all; return 404; }
```

---

### L041: /.env publicly accessible

**Severity**: Error

**What causes it**: A `GET /.env` request returns 2xx. Environment variable files typically contain secrets (database passwords, API keys).

**Fix**: Never serve `.env` files from your web root. Store them outside the static root, and configure your reverse proxy to 404 the path.

---

### L042: Django debug toolbar exposed

**Severity**: Error

**What causes it**: A `GET /__debug__/` request returns 2xx in production. The Django Debug Toolbar is installed and accessible.

**Fix**: Guard the debug toolbar with `DEBUG=True` and `INTERNAL_IPS`:

```python
# settings.py
if DEBUG:
    INSTALLED_APPS += ["debug_toolbar"]
    MIDDLEWARE += ["debug_toolbar.middleware.DebugToolbarMiddleware"]
    INTERNAL_IPS = ["127.0.0.1"]
```

---

### L043: /robots.txt not present

**Severity**: Info

**What causes it**: `/robots.txt` returns 404. Not a security issue — an informational finding suggesting you add one to control crawler behavior explicitly.

**Fix**: Serve a `robots.txt` at the web root if you want to set crawl rules.

---

### L044: /.well-known/security.txt not present (RFC 9116)

**Severity**: Info

**What causes it**: `/.well-known/security.txt` returns 404. RFC 9116 recommends publishing a security contact file for security researchers.

**Fix**: Create `security.txt` at your web root:

```
Contact: security@example.com
Expires: 2027-01-01T00:00:00Z
Preferred-Languages: en
```

---

### L060: WebSocket accepted cross-origin handshake (CSWSH)

**Severity**: Error

**What causes it**: The runtime probe opened a WebSocket handshake with `Origin: https://evil.example` and the server accepted it. This is the runtime confirmation of [#653](https://github.com/djust-org/djust/issues/653) — the static check A001 can see whether the middleware is configured, but only this runtime probe can confirm the handshake is actually rejected end-to-end.

**Fix**: Wrap the WebSocket router in `AllowedHostsOriginValidator` (or use `DjustMiddlewareStack`, which does it by default since 0.4.1). See A001 above.

---

### L061: WebSocket probe skipped (--no-websocket-probe)

**Severity**: Info

**What causes it**: The probe was disabled via `--no-websocket-probe`. Recorded as info so the audit report shows which checks ran and which were skipped.

---

### L062: WebSocket probe skipped (websockets package not installed)

**Severity**: Info

**What causes it**: The `websockets` Python package is not installed, so the CSWSH probe can't run.

**Fix**: `pip install websockets` if you want to enable this check. The rest of the runtime probe still runs without it.

---

### L090: Target URL unreachable

**Severity**: Error

**What causes it**: `urllib` couldn't connect to the target — DNS failure, connection refused, timeout, or similar network error.

**Fix**: Verify the URL is correct, the server is running, and your network can reach it. Check for typos in the hostname or port.

---

### L091: Target URL returned non-2xx status

**Severity**: Error

**What causes it**: The target URL returned an HTTP 4xx/5xx status. The probe still inspects the response headers, but the finding is recorded so the report reflects that the main page wasn't loaded successfully.

**Fix**: Investigate the application error. If the root URL requires auth, pass credentials with `--header 'Authorization: Basic ...'`.

---

## AST Anti-Pattern Scanner Findings (X0xx)

These findings are emitted by `manage.py djust_audit --ast`, which walks your Python source and Django templates looking for eight specific security anti-patterns (X001–X008). Every pattern here was either a live vulnerability or a near-miss in the 2026-04-10 pentest of a downstream consumer. The checks are intentionally narrow: false positives are worse than missed findings for a linter that runs on every push.

Suppress a single finding with `# djust: noqa X001` on the offending line (or `{# djust: noqa X006 #}` inside a template). Bare `# djust: noqa` suppresses every djust.X finding on that line.

Run:

```bash
python manage.py djust_audit --ast                              # scan cwd
python manage.py djust_audit --ast --ast-path src/              # scan a directory
python manage.py djust_audit --ast --json                       # machine-readable
python manage.py djust_audit --ast --strict                     # fail on warnings too
python manage.py djust_audit --ast --ast-exclude vendor legacy/ # skip paths
python manage.py djust_audit --ast --ast-no-templates           # .py only
```

### X001: Possible IDOR — object lookup by URL param without auth scoping

**Severity**: Error

**What causes it**: A view class whose name ends in `DetailView`/`EditView` (or which inherits from `LiveView`, `DetailView`, `UpdateView`, `DeleteView`, `FormView`) calls `Model.objects.get(pk=self.kwargs["pk"])` without a sibling `.filter(owner=request.user)` (or `user=`, `tenant=`, `organization=`, `team=`, `created_by=`, `author=`, `workspace=`) scoping the queryset, and without calling `check_permissions` or `has_perm`.

**What you see**:

```
ERROR [djust.X001] /path/crm/views.py:42:8 Possible IDOR — object lookup by URL param without auth scoping (ContactDetailView.get_object: add .filter(owner=request.user) or override check_permissions() to scope by owner)
```

**Fix**: Scope the queryset by the authenticated user or the tenant the user belongs to:

```python
class ContactDetailView(LiveView):
    def mount(self, request, pk):
        self.contact = Contact.objects.filter(
            owner=request.user
        ).get(pk=pk)
```

Or override `check_permissions()` with an explicit membership check and add `# djust: noqa X001` on the `.get()` call if the scoping is implicit (e.g. the tenant filter sits inside a helper manager).

---

### X002: Event handler mutates state without a permission check

**Severity**: Warning

**What causes it**: A method decorated with `@event_handler` contains a call to `.create()`, `.update()`, `.delete()`, `.bulk_create()`, `.bulk_update()`, or `.save()`, and **neither** of the following is true:

- The enclosing class sets `login_required = True` or `permission_required = "..."` in the class body.
- The handler itself is wrapped in `@permission_required("...")`, `@login_required`, `@user_passes_test(...)`, `@staff_member_required`, or `@superuser_required`.

**What you see**:

```
WARN [djust.X002] /path/projects/views.py:87:4 Event handler mutates state without a permission check (ProjectView.delete_project: add @permission_required('projects.delete_project'), set login_required=True on the view, or override check_permissions())
```

**Fix**: Add class-level auth or a handler decorator:

```python
class ProjectView(LiveView):
    login_required = True
    permission_required = "projects.manage_projects"

    @event_handler
    def delete_project(self, project_id: int = 0, **kwargs):
        Project.objects.filter(pk=project_id).delete()
```

Or the more granular form:

```python
@permission_required("projects.delete_project")
@event_handler
def delete_project(self, project_id: int = 0, **kwargs):
    Project.objects.filter(pk=project_id).delete()
```

---

### X003: SQL string formatting in raw()/extra()/execute() — SQLi risk

**Severity**: Error

**What causes it**: A call to `.raw(...)`, `.extra(...)`, `cursor.execute(...)`, or `cursor.executemany(...)` passes a query built with an f-string, a `.format()` call, or a `"..." % ...` binary-op. Any of those interpolate Python values directly into the SQL string, bypassing Django's ORM parameter binding.

**What you see**:

```
ERROR [djust.X003] /path/reports/queries.py:19:11 SQL string formatting in raw()/extra()/execute() — SQLi risk (.raw(...) with interpolated string)
```

**Fix**: Use parametrised queries — pass the values as a second argument and leave `%s` placeholders in the SQL:

```python
# BAD
Thing.objects.raw(f"SELECT * FROM thing WHERE name = '{name}'")

# GOOD
Thing.objects.raw("SELECT * FROM thing WHERE name = %s", [name])

# GOOD (cursor)
cursor.execute("SELECT * FROM t WHERE name = %s", [name])
```

---

### X004: Open redirect — request data fed to redirect without is_safe_url

**Severity**: Error

**What causes it**: A function contains `HttpResponseRedirect(request.GET[...])` (or `request.POST`, `request.GET.get(...)`, etc.) and the enclosing function does not call `url_has_allowed_host_and_scheme` or `is_safe_url` anywhere inside it. The scanner looks for the guard at the same function scope — moving the check into a helper works, but add `# djust: noqa X004` on the redirect line so reviewers can see the intent.

**What you see**:

```
ERROR [djust.X004] /path/auth/views.py:55:15 Open redirect — request data fed to redirect without is_safe_url (post_login: wrap the target in url_has_allowed_host_and_scheme() before redirecting)
```

**Fix**: Validate the URL before redirecting:

```python
from django.utils.http import url_has_allowed_host_and_scheme

def post_login(request):
    target = request.GET.get("next", "/")
    if not url_has_allowed_host_and_scheme(
        target, allowed_hosts={request.get_host()}, require_https=request.is_secure()
    ):
        target = "/"
    return HttpResponseRedirect(target)
```

---

### X005: mark_safe() with interpolated value — XSS risk

**Severity**: Error

**What causes it**: `django.utils.safestring.mark_safe(...)` or `SafeString(...)` is called with an argument that is an f-string, a `.format()` call, or a `"..." % ...` binary-op. Whatever gets interpolated becomes trusted HTML — if any piece of it is user-controlled, that's stored XSS.

**What you see**:

```
ERROR [djust.X005] /path/views.py:72:15 mark_safe() with interpolated value — XSS risk (mark_safe(...) wraps an interpolated string)
```

**Fix**: Use `django.utils.html.format_html()` instead — it behaves like `.format()` but escapes every substitution:

```python
from django.utils.html import format_html, escape

# BAD
return mark_safe(f"<b>{name}</b>")

# GOOD
return format_html("<b>{}</b>", name)

# ALSO GOOD
return mark_safe(f"<b>{escape(name)}</b>")
```

---

### X006: Template uses |safe on a view variable

**Severity**: Warning

**What causes it**: A `.html` template contains `{{ variable|safe }}`. The scanner flags every instance as "worth reviewing" — sometimes `|safe` is correct (rendering stored HTML that the author wrote in a CMS that already sanitizes it), but it is also the most common XSS foot-gun in Django apps.

**What you see**:

```
WARN [djust.X006] /path/templates/blog/post.html:14:4 Template uses |safe on a view variable ({{ body|safe }})
```

**Fix**: Remove `|safe` if the value is user-controlled. If the value is a short snippet you control, wrap it in `format_html()` on the view side and pass a `SafeString` instead. Suppress the finding with `{# djust: noqa X006 #}` on the same line if you have verified the HTML is already sanitised by bleach / nh3.

---

### X007: Template uses {% autoescape off %}

**Severity**: Warning

**What causes it**: A `.html` template opens an `{% autoescape off %}` block. That disables Django's default auto-escaping for every `{{ var }}` inside the block — one forgotten `|escape` turns into stored XSS.

**What you see**:

```
WARN [djust.X007] /path/templates/emails/body.html:3:0 Template uses {% autoescape off %}
```

**Fix**: Remove the `{% autoescape off %}` block. If you really need unescaped output (rendering pre-escaped content from a trusted source), prefer an explicit `{{ var|safe }}` on the one variable that needs it — then X006 documents the exception — instead of a block that silently bypasses escaping for everything inside. Suppress with `{# djust: noqa X007 #}` on the block opener line if the exception is intentional.

---

### X008: Detail view matches IDOR shape — missing object-permission lifecycle override

**Severity**: Warning

**What causes it**: A detail-shaped view class has `permission_required`, binds a URL-kwarg id to `self` in `mount()`, and has event handlers that read it, but neither it nor a base class in the same module overrides `has_object_permission()` or `check_permissions()`. View-level permissions don't check that the user may access this particular object.

**Fix**: Override `has_object_permission()` (or `check_permissions()`) to check access to the object. See [Authorization](authorization.md) for the pattern. Suppress with `# djust: noqa X008` if access is scoped elsewhere.

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

```html
<!-- WRONG: duplicate IDs possible if items have same id -->
{% for item in items %}
<li data-key="{{ item.name }}">{{ item.name }}</li>
{% endfor %}

<!-- CORRECT: use unique identifier -->
{% for item in items %}
<li data-key="{{ item.id }}">{{ item.name }}</li>
{% endfor %}
```

---

### DJE-052: dj-virtual children fell back to index diffing

**Severity**: Warning

**What causes it**: A `dj-virtual` container's children could not be diffed by key (for example, some children lack keys, or keys are duplicated), so the VDOM fell back to index-addressed diffing. Index-addressed ops cannot address items outside the client's visible window.

**What you see**: A server log warning: `DJE-052: a [dj-virtual] container's children ... — falling back to index-addressed diffing, which cannot address items outside the client's visible window. Give every child a unique dj-key.`

**Fix**: Give every child of a `dj-virtual` container a unique `dj-key`:

```html
<div dj-virtual="items" dj-virtual-item-height="48"
     style="height: 600px; overflow: auto;">
    {% for item in items %}
    <div dj-key="{{ item.id }}">{{ item.name }}</div>
    {% endfor %}
</div>
```

**Related**: [Large Lists](large-lists.md), [List Reordering Performance](keyed-lists-performance.md)

---

### DJE-053: Fell back to full HTML update

**Severity**: Warning

**What causes it**: An event triggered a re-render, but the VDOM diff returned no patches (or the view forced a full HTML update), after the view already had a rendered baseline. djust then sends the whole view HTML instead of patches. This often means the modified state is rendered **outside** the `dj-root` element (e.g., in `base.html`).

**What you see**: In server logs:

```
WARNING [djust] Event 'toggle_sidebar' on DashboardView fell back to full HTML update (DJE-053). Template: myapp/dashboard.html. VDOM diff returned no patches — this may cause event listeners and DOM state to be lost. Debugging steps: ...
```

djust falls back to replacing the whole view HTML. The page updates, but client-side DOM state and listeners inside the root can be lost.

**Common causes**:

1. **State rendered outside the VDOM root**: The `{% if show_panel %}` block is in `base.html` while `dj-root` is in the child template.
2. **Root not where you expect**: `dj-root` is auto-inferred from `dj-view`, so check which element carries `dj-view`; state rendered outside it is not diffed.
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

If there is no WebSocket connection, check C001-C005. If events are sent but no patches come back, check the server log for DJE-053.

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
- [Security Guide](../advanced/security.md) -- Authentication and authorization
- [Best Practices](BEST_PRACTICES.md) -- State management patterns
