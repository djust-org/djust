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
| A0xx | Audit / Static Security Checks | `manage.py check --tag djust` (startup) |
| P0xx | Permissions Document | `manage.py djust_audit --permissions permissions.yaml` |
| L0xx | Live Runtime Probe | `manage.py djust_audit --live <url>` |
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

**Note**: Prior to [#303](https://github.com/djust-org/djust/issues/303), this check incorrectly warned on views with `login_required = False`. The check now correctly distinguishes between intentionally public views (`False`) and views that haven't addressed authentication at all (`None`).

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
<a href="?view=settings" dj-patch="handle_params">Settings</a>
```

In your LiveView, handle the URL parameter:

```python
from djust.decorators import event_handler

class MyView(LiveView):
    def mount(self, request, **kwargs):
        self.current_view = request.GET.get('view', 'dashboard')

    @event_handler()
    def handle_params(self, **kwargs):
        # Called when URL changes via dj-patch
        self.current_view = self.request.GET.get('view', 'dashboard')
```

**Related**: [Navigation Guide](navigation.md)

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

## Audit / Static Security Checks (A0xx)

These checks were added as follow-ups to the 2026-04-10 NYC Claims penetration test. They extend `djust_audit` / `djust_check` with configuration-level security checks that catch misconfigurations Django's own `check --deploy` cannot see. All A0xx checks fire during `manage.py check --tag djust` and appear alongside the C0xx/S0xx findings.

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
from channels.security.websocket import AllowedHostsOriginValidator

"websocket": AllowedHostsOriginValidator(
    AuthMiddlewareStack(URLRouter(websocket_urlpatterns))
),
```

**Prerequisite**: `settings.ALLOWED_HOSTS` must not contain `"*"`.

---

### A010: ALLOWED_HOSTS is only ["*"] in production

**Severity**: Error

**What causes it**: `settings.ALLOWED_HOSTS == ["*"]` with `DEBUG=False`. The wildcard disables Django's Host header defense entirely, and re-opens CSWSH because `AllowedHostsOriginValidator` reads the same setting.

**Fix**: Set `ALLOWED_HOSTS` to the explicit hostnames your app serves:

```python
# settings.py
ALLOWED_HOSTS = ["myapp.example.com", "api.example.com"]
```

---

### A011: ALLOWED_HOSTS mixes "*" with explicit hosts

**Severity**: Error

**What causes it**: `settings.ALLOWED_HOSTS = ["myapp.example.com", "*"]`. Django accepts any Host header as soon as `"*"` is present — the explicit hostname is meaningless once the wildcard is in the list. Authors often mix them thinking they're "also allowing the explicit host for clarity."

**Fix**: Remove `"*"` and keep only the explicit hostnames.

---

### A012: USE_X_FORWARDED_HOST=True + wildcard ALLOWED_HOSTS

**Severity**: Error

**What causes it**: `USE_X_FORWARDED_HOST=True` makes Django trust the `X-Forwarded-Host` header. Combined with wildcard `ALLOWED_HOSTS`, there is no validation of that header — attackers can inject any Host.

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

**What you see**: A lower-privilege user logs in and lands on the same dashboard as a supervisor, immediately exposing any broken RBAC. This is how the NYC Claims pentest team found broken role isolation in minutes.

**Fix**: Subclass `django.contrib.auth.views.LoginView` and override `get_success_url()`:

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

## Permissions Document Findings (P0xx)

These codes come from `manage.py djust_audit --permissions permissions.yaml` and validate the actual code against a committed declarative permissions document. See [Declarative Permissions Document](permissions-document.md) for the full setup guide.

### P001: View declared in permissions.yaml but not found in code

**Severity**: Error

**What causes it**: An entry in `permissions.yaml` points at a dotted view path that no longer exists in the codebase. Probably a stale declaration after a view was removed or renamed.

**Fix**: Remove the stale entry from `permissions.yaml`, or restore the view if it was deleted by mistake.

---

### P002: View found in code but not declared in permissions.yaml

**Severity**: Error (strict mode only)

**What causes it**: A new LiveView was added to the codebase without a corresponding entry in `permissions.yaml`, and the document has `strict: true`.

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

**Severity**: Warning (currently informational)

**What causes it**: `permissions.yaml` declares `object_scoping.fields: [...]` for a view but the best-effort AST check couldn't find references to those fields in `get_object()` or equivalent. This is reserved for a future AST-verified implementation; today it's documentation-only.

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
