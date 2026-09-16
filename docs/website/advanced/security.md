---
title: "Security"
slug: security
section: advanced
order: 4
level: advanced
description: "Security best practices for djust applications: WebSocket event safety, XSS prevention, rate limiting, and multi-tenant isolation."
---

# Security

djust provides multiple layers of security by default. This guide covers the protections built into the framework and the practices you should follow when building applications.

## WebSocket Event Security

djust enforces three layers of protection on every WebSocket event dispatch. All are enabled by default with no configuration needed.

### Layer 1: Event Name Guard

A regex filter runs before `getattr()` to block dangerous method names:

```python
from djust.security import is_safe_event_name

is_safe_event_name("increment")    # True
is_safe_event_name("__class__")    # False
is_safe_event_name("_private")     # False
```

Only lowercase letters, digits, and underscores are allowed, and the name must start with a letter.

### Layer 2: @event_handler Allowlist

Only methods explicitly decorated with `@event_handler` are callable via WebSocket:

```python
from djust import LiveView
from djust.decorators import event_handler

class MyView(LiveView):
    @event_handler
    def increment(self):
        """Callable via WebSocket."""
        self.count += 1

    def internal_helper(self):
        """NOT callable via WebSocket (not decorated)."""
        pass
```

The `event_security` setting controls enforcement:

| Mode       | Behavior                                             |
| ---------- | ---------------------------------------------------- |
| `"strict"` | (default) Only `@event_handler` decorated methods    |
| `"warn"`   | Allows undecorated methods with deprecation warnings |
| `"open"`   | No decorator check (legacy, not recommended)         |

### Layer 3: Rate Limiting

Per-connection token bucket rate limiting prevents event flooding:

```python
# settings.py
LIVEVIEW_CONFIG = {
    "rate_limit": {
        "rate": 100,                    # events/second sustained
        "burst": 20,                    # burst allowance
        "max_warnings": 3,             # warnings before disconnect
        "max_connections_per_ip": 10,  # concurrent WS connections per IP
        "reconnect_cooldown": 5,       # seconds before rate-limited IP can reconnect
    },
    "max_message_size": 65536,  # 64KB
}
```

For expensive handlers, apply per-handler limits:

```python
from djust.decorators import event_handler, rate_limit

class MyView(LiveView):
    @rate_limit(rate=5, burst=3)
    @event_handler
    def expensive_operation(self, **kwargs):
        """Limited to 5/sec sustained, 3 burst."""
        ...
```

After `max_warnings` violations, the connection is closed with code `4429`. A per-IP connection tracker rejects new connections from the same IP during the cooldown period.

## XSS Prevention

### Template Escaping

Django's auto-escaping is active by default in djust templates. User input rendered with `{{ variable }}` is automatically escaped.

For template tags that produce HTML, always use `format_html()`:

```python
# WRONG -- vulnerable to XSS
from django.utils.safestring import mark_safe

def my_tag(user_url):
    return mark_safe(f'<a href="{user_url}">Link</a>')

# CORRECT -- format_html auto-escapes interpolated values
from django.utils.html import format_html

def my_tag(user_url):
    return format_html('<a href="{}">Link</a>', user_url)
```

For JavaScript string contexts, use `json.dumps()` (not `escape()`):

```python
import json

js_value = json.dumps(user_input)  # Handles \, newlines, quotes
```

### Client-Side Safety

djust's client applies these protections itself, at the sinks — there is no
`djustSecurity` global to call. (One was documented here until #2679, but
nothing loaded the file, so it was `undefined` in every browser.)

- DOM updates arrive as VDOM patches, not as `innerHTML` of a server string.
- Keys that could pollute a prototype are refused where untrusted keys are
  written — see `UNSAFE_KEYS` in `static/djust/src/00-namespace.js` and the
  guards in the event-parsing, form-data, draft-manager and VDOM-patch modules.

Writing your own inline JS against untrusted input? Use `textContent` over
`innerHTML`, and a `Map` or `Object.create(null)` over merging attacker-
controlled keys into an object literal.

## Python Security Utilities

### Safe Attribute Setting

Always use `safe_setattr()` when attribute names come from untrusted input:

```python
from djust.security import safe_setattr

# Blocks __class__, __proto__, __init__, etc.
for key, value in user_params.items():
    safe_setattr(obj, key, value)
```

### Safe Logging

Sanitize user input before logging to prevent log injection:

```python
from djust.security import sanitize_for_log

logger.info("User searched for: %s", sanitize_for_log(user_query))
```

This strips ANSI escape sequences, newlines (prevents forged log entries), and truncates long strings.

### Safe Error Responses

Use `create_safe_error_response()` to avoid leaking stack traces in production:

```python
from djust.security import create_safe_error_response

response = create_safe_error_response(
    exception=e,
    error_type="event",
    event_name=event_name,
)
await self.send_json(response)
```

Stack traces are included only when `DEBUG = True`. User parameters are never included.

## CSRF Protection

djust LiveViews use WebSocket connections authenticated by the Django session. The initial HTTP handshake carries the session cookie, and the WebSocket consumer verifies the session before accepting the connection.

For any HTTP endpoints in your djust application, standard Django CSRF protection applies. Never use `@csrf_exempt` without documented justification.

## Authentication Enforcement

Protect LiveViews that require authentication:

```python
from django.contrib.auth.mixins import LoginRequiredMixin
from djust import LiveView

class DashboardView(LoginRequiredMixin, LiveView):
    template_name = "dashboard.html"

    def mount(self, request, **kwargs):
        self.user = request.user
```

The WebSocket consumer checks `request.user` during connection. If using `PresenceMixin`, the framework gracefully handles missing authentication middleware by checking `hasattr(request, "user")` before accessing `request.user`.

## Multi-Tenant Isolation

When building multi-tenant applications, follow these principles:

### Key Prefixing

All storage keys (cache, state backends, sessions) must include the tenant identifier:

```python
from djust.tenants.mixin import TenantScopedMixin

class TenantDocumentView(TenantScopedMixin, LiveView):
    def get_queryset(self):
        # TenantScopedMixin auto-filters: .filter(tenant=self.tenant)
        return Document.objects.all()
```

### Isolation Checklist

- All cache/state keys include tenant prefix
- Database queries scoped to current tenant
- File uploads stored in tenant-specific paths
- Background tasks carry tenant context
- Admin views respect tenant boundaries

## Security Scanning

djust runs these tools automatically via pre-commit hooks:

```bash
# Python security linter
bandit -r python/djust/ -ll -ii

# Rust dependency audit
cargo audit

# JavaScript dependency audit
npm audit --audit-level=high

# Credential detection
detect-secrets scan
```

## How Data Flows in djust

When a user interacts with a LiveView, here's what happens:

```
1. Client sends event:     {"event": "search", "params": {"value": "foo"}}
2. Server calls handler:   self.query = params["value"]
3. Server builds context:  get_context_data() → {"query": "foo", "results": [...]}
4. Server renders template: template + context → HTML
5. Server diffs HTML:       old VDOM vs new VDOM → patches
6. Client receives:         [{"type": "replace", "path": "/1/3", "value": "<td>...</td>"}]
```

**The context dict never leaves the server.** Only rendered HTML fragments (VDOM patches) are sent over the WebSocket. The client cannot request raw state, inspect variable values, or access fields that aren't in the template.

## What Reaches the Client

### Always sent (by design)

| Channel | What's sent | Notes |
|---------|------------|-------|
| VDOM patches | HTML fragment diffs | Only structural changes to the rendered template |
| Full HTML update | Complete rendered HTML | Fallback when VDOM diffing isn't possible |
| `push_event()` payload | Developer-specified data | You control what goes in the payload |

### Never sent (in production)

| Data | Why it's safe |
|------|--------------|
| Context dict | Used server-side for rendering only, never serialized to the client |
| Private attributes (`_name`) | Excluded from `get_context_data()` by convention |
| Unreferenced model fields | JIT serialization only processes fields the template actually uses |
| Handler return values | Return values are discarded; only the re-rendered HTML matters |

### DEBUG mode only

When `DEBUG=True`, djust injects debug information into the page:

```javascript
window.DJUST_DEBUG_INFO = {
  "variables": {"count": {"type": "int", "value": "42"}},
  "handlers": {"increment": {"params": [...]}},
  ...
}
```

This exposes variable names, types, and truncated `repr()` values. **Never run `DEBUG=True` in production.**

## JIT Serialization: Privacy by Default

djust's JIT (Just-In-Time) serialization inspects your template and only serializes the model fields that the template actually references:

```python
class ContactDetailView(LiveView):
    template_name = "contact_detail.html"

    def mount(self, request, pk):
        self.contact = Contact.objects.get(pk=pk)
```

If `contact_detail.html` contains:

```html
<h1>{{ contact.name }}</h1>
<p>{{ contact.email }}</p>
```

Then only `name` and `email` are serialized for the VDOM. Fields like `ssn`, `salary`, or `internal_notes` are never processed — even if they exist on the model. This provides defense-in-depth: even if you accidentally include a full model object in context, only the fields the template renders will appear in the HTML sent to the client.

## Private State

Use the `_` prefix convention to keep state completely out of the template context:

```python
class DashboardView(LiveView):
    template_name = "dashboard.html"

    def mount(self, request):
        self.metrics = calculate_metrics()       # Available in template
        self._api_key = get_api_key()            # Hidden from context
        self._internal_cache = {}                # Hidden from context
```

`get_context_data()` automatically excludes attributes starting with `_`. These values exist only on the server, are never serialized, and cannot be reached through any client-side mechanism.

## Content Security Policy (CSP) with nonces

djust emits a small number of inline `<script>` and `<style>` tags during
render — the handler metadata bootstrap, the `live_session` route map, and
the PWA template tags (`djust_sw_register`, `djust_offline_indicator`,
`djust_offline_styles`). Until #655 these required `'unsafe-inline'` in
`CSP_SCRIPT_SRC` and `CSP_STYLE_SRC`, which negates most of CSP's XSS
defense. As of v0.4.1 every djust-emitted inline tag picks up
`request.csp_nonce` when one is available and renders a `nonce="..."`
attribute, so apps can switch to strict nonce-based CSP.

### Setup

1. Install [django-csp](https://django-csp.readthedocs.io/) 4.0 or later:
   ```bash
   pip install 'django-csp>=4.0'
   ```

2. Add `csp.middleware.CSPMiddleware` to `MIDDLEWARE` in `settings.py`.

3. Configure `CSP_INCLUDE_NONCE_IN` to cover the directives djust uses for
   inline content:
   ```python
   # settings.py
   CSP_INCLUDE_NONCE_IN = (
       "script-src",
       "script-src-elem",
       "style-src",
       "style-src-elem",
   )

   # Drop 'unsafe-inline' — nonces cover what djust emits:
   CSP_SCRIPT_SRC = ("'self'",)
   CSP_SCRIPT_SRC_ELEM = ("'self'",)
   CSP_STYLE_SRC = ("'self'",)
   CSP_STYLE_SRC_ELEM = ("'self'",)
   ```

4. Make sure you're rendering templates with a `RequestContext` (Django's
   default via `render()` / `TemplateResponse` — no changes needed unless
   you construct a bare `Context` manually). The djust PWA template tags
   and the handler metadata injector both read `request.csp_nonce` from
   the active request, which django-csp populates automatically.

### Caveats

- **VDOM patches** (the incremental updates sent over the WebSocket after
  initial page load) do not currently carry nonces. If your app injects
  fresh `<script>` or `<style>` blocks via VDOM patches — uncommon, since
  most dynamic content is attribute and text updates — those will still
  need `'unsafe-inline'`. This is tracked as a follow-up to #655.
- **Third-party JS that djust doesn't emit** (Google Analytics, Stripe,
  etc.) is outside djust's scope. Add their domains or hashes to your
  CSP directly.
- **User-generated inline content** (e.g. rich-text editors that allow
  `style=` attributes) is application responsibility, not framework.

## Reporting Vulnerabilities

If you discover a security issue in djust:

1. Do **not** open a public GitHub issue.
2. Email `security@djust.org` with a description, reproduction steps, and potential impact.
3. We will respond within 48 hours.
