---
title: "on_mount Hooks"
slug: on-mount-hooks
section: guides
order: 8
level: intermediate
description: "Cross-cutting mount logic for authentication, telemetry, tenant resolution, and more -- Phoenix on_mount parity"
---

# on_mount Hooks

on_mount hooks let you inject shared behaviour that runs during every mount (and reconnect) of a LiveView, after authentication but before the view's own `mount()` method. This is the djust equivalent of Phoenix LiveView's `on_mount/1` callback.

## What You Get

- **Cross-cutting concerns** -- Run shared logic (auth, telemetry, tenant resolution) without repeating code in every view
- **Redirect-based halting** -- Return a URL string from any hook to halt the mount and redirect the client
- **Inheritance** -- Hooks walk the MRO parent-first and are automatically deduplicated
- **V009 system check** -- Catches misconfigured `on_mount` attributes at startup

## Basic Usage

### 1. Define a Hook Function

A hook is any callable with the signature `(view, request, **kwargs)`. Return `None` to continue, or a redirect URL string to halt the mount chain.

```python
from djust.hooks import on_mount

@on_mount
def require_verified_email(view, request, **kwargs):
    if not request.user.email_verified:
        return '/verify-email/'
```

The `@on_mount` decorator marks the function so djust recognises it as a hook. It does not change the function's behaviour -- it simply sets an internal marker attribute.

### 2. Attach Hooks to a LiveView

Set the `on_mount` class attribute to a list of hook functions:

```python
from djust import LiveView

class ProfileView(LiveView):
    template_name = 'profile.html'
    on_mount = [require_verified_email]

    def mount(self, request, **kwargs):
        self.user = request.user
```

When `ProfileView` mounts, djust runs `require_verified_email` first. If the user's email is not verified, the client is redirected to `/verify-email/` and `mount()` never executes.

### 3. Multiple Hooks

Hooks run in declaration order. The first hook to return a URL string wins -- subsequent hooks are skipped.

```python
class DashboardView(LiveView):
    template_name = 'dashboard.html'
    on_mount = [require_auth, require_verified_email, track_page_view]
```

## Hook Function Signature

```python
def my_hook(view, request, **kwargs) -> Optional[str]:
    ...
```

| Parameter | Type | Description |
|-----------|------|-------------|
| `view` | `LiveView` instance | The view being mounted. You can read/write state on it. |
| `request` | `HttpRequest` | The Django request object (HTTP on initial mount, synthetic on reconnect). |
| `**kwargs` | `dict` | URL keyword arguments from the route (e.g., `pk` from `path("<int:pk>/", ...)`). |
| **Return** | `None` or `str` | `None` to continue the chain. A URL string to halt and redirect. |

## Halting Mount with a Redirect

When a hook returns a string, djust treats it as a redirect URL. The mount pipeline stops immediately, `mount()` is never called, and the client navigates to the returned URL.

```python
@on_mount
def require_subscription(view, request, **kwargs):
    if not request.user.has_active_subscription:
        return '/pricing/'
    # Return None implicitly to continue
```

This is logged at INFO level:

```
on_mount hook require_subscription halted mount for DashboardView: redirect to /pricing/
```

## Hook Inheritance

When a view inherits from a base that also declares `on_mount`, hooks are collected by walking the MRO in parent-first order. Duplicate hooks (same function object) are automatically removed.

```python
@on_mount
def set_tenant(view, request, **kwargs):
    view._tenant = request.headers.get('X-Tenant-ID')

@on_mount
def require_auth(view, request, **kwargs):
    if not request.user.is_authenticated:
        return '/login/'

class BaseTenantView(LiveView):
    on_mount = [set_tenant, require_auth]

class SettingsView(BaseTenantView):
    on_mount = [require_admin]  # Added on top of parent hooks
```

For `SettingsView`, the effective hook order is:

1. `set_tenant` (from `BaseTenantView`)
2. `require_auth` (from `BaseTenantView`)
3. `require_admin` (from `SettingsView`)

If `SettingsView` also listed `set_tenant`, it would not run twice -- deduplication removes the repeat.

## Common Patterns

### Authentication Guard

```python
@on_mount
def require_auth(view, request, **kwargs):
    if not request.user.is_authenticated:
        return '/login/'
```

### Telemetry / Analytics

```python
import logging

logger = logging.getLogger(__name__)

@on_mount
def track_mount(view, request, **kwargs):
    logger.info(
        "LiveView mount: view=%s user=%s path=%s",
        view.__class__.__name__,
        getattr(request.user, 'pk', 'anonymous'),
        request.path,
    )
```

### Tenant Resolution

```python
@on_mount
def resolve_tenant(view, request, **kwargs):
    tenant_slug = kwargs.get('tenant')
    if not tenant_slug:
        return '/select-tenant/'
    try:
        view._tenant = Tenant.objects.get(slug=tenant_slug)
    except Tenant.DoesNotExist:
        return '/404/'
```

### Feature Flags

```python
@on_mount
def require_feature(view, request, **kwargs):
    feature = getattr(view, '_required_feature', None)
    if feature and not request.user.has_feature(feature):
        return '/upgrade/'
```

```python
class BetaDashboard(LiveView):
    _required_feature = 'beta_dashboard'
    on_mount = [require_auth, require_feature]
```

## V009 System Check

djust validates `on_mount` at startup via Django's system check framework. The check `djust.V009` catches two problems:

1. **`on_mount` is not a list or tuple** -- e.g., accidentally assigning a single function instead of a list.
2. **Non-callable items in the list** -- e.g., a string or `None` slipped in.

```
?: (djust.V009) MyView: 'on_mount' should be a list of hook functions.
    HINT: Set on_mount = [hook1, hook2, ...] on your LiveView class.
```

These checks run automatically with `python manage.py check` and at server startup.

## API Reference

### `@on_mount` decorator

```python
from djust.hooks import on_mount
```

Marks a function as an on_mount hook. Does not alter the function's runtime behaviour.

### `is_on_mount(func) -> bool`

```python
from djust.hooks import is_on_mount
```

Returns `True` if `func` was decorated with `@on_mount`.

### `run_on_mount_hooks(view_instance, request, **kwargs) -> Optional[str]`

```python
from djust.hooks import run_on_mount_hooks
```

Executes all collected hooks for the view's class in MRO order. Returns a redirect URL string if any hook halts, otherwise `None`. This is called internally by the LiveView mount pipeline -- you typically do not call it directly.
