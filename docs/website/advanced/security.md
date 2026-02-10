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

| Mode       | Behavior                                         |
|------------|--------------------------------------------------|
| `"strict"` | (default) Only `@event_handler` decorated methods |
| `"warn"`   | Allows undecorated methods with deprecation warnings |
| `"open"`   | No decorator check (legacy, not recommended)     |

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

djust provides JavaScript security utilities:

```javascript
// Safe innerHTML (strips dangerous content)
djustSecurity.safeSetInnerHTML(element, htmlString);

// Safe object merge (blocks __proto__, constructor)
djustSecurity.safeObjectAssign(target, source);

// Safe logging (strips control characters)
djustSecurity.sanitizeForLog(value);
```

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
from djust.tenant.mixins import TenantScopedMixin

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

## Reporting Vulnerabilities

If you discover a security issue in djust:

1. Do **not** open a public GitHub issue.
2. Email `security@djust.org` with a description, reproduction steps, and potential impact.
3. We will respond within 48 hours.
