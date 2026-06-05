---
title: "Multi-Tenant Applications"
slug: multi-tenant
section: guides
order: 7
level: advanced
description: "Build SaaS apps with TenantMixin, flexible resolution strategies, and per-tenant state isolation"
---

# Multi-Tenant Applications

djust provides comprehensive multi-tenant support for building SaaS applications with complete tenant isolation, flexible resolution strategies, and tenant-scoped data access.

## Choosing Your Multi-Tenancy Strategy

djust has **one supported multi-tenancy strategy**: `djust.tenants` (row-level). This guide covers it. If you're starting a new djust application, this is the path.

### `djust.tenants` (row-level) — the supported ASGI-native path

Every tenant-scoped row carries a `tenant_id`. Querysets filter on it. Tenant resolution at request/connection time is a single lookup, cached for the rest of the request. **No schema switching. No `SET search_path`. No per-event Postgres roundtrip.**

This is the recommended and only supported multi-tenancy strategy for djust applications because:

- LiveView's hot path (every WS event re-enters middleware) stays cheap — there's no DB roundtrip on entry.
- Connection-pool pressure scales with **active sessions**, not events × tenants × middleware passes.
- Tenant resolution is cacheable in-process or in Redis without needing to compose with database connection state.
- Single Postgres schema is operationally simpler — one set of migrations, one backup, one set of indexes.
- It integrates cleanly with djust's state backends, presence, and `TenantMixin` / `TenantScopedMixin`.

### `django-tenants` (schema-per-tenant) — DEPRECATED under djust

> **Deprecated.** Integration with the external [django-tenants](https://github.com/django-tenants/django-tenants) library (schema-per-tenant via `SET search_path`) is **deprecated** as a multi-tenancy strategy for djust applications. Existing deploys are not forcibly broken, but new applications should not adopt it, and existing applications should plan a migration to `djust.tenants`.

Why deprecated:

- **It is a known production footgun under ASGI + LiveView.** Every WebSocket event re-enters `TenantMainMiddleware` → `set_tenant()` → `SET search_path`. LiveView amplifies this dramatically: `tick_interval` polling (typically every 1.5–5s), `push_to_view` re-mounts across all connected sessions, presence updates, and `@notify_on_save` listener re-mounts each re-enter the middleware. Without the `TENANT_LIMIT_SET_CALLS` stopgap (below) this exhausts the Postgres connection pool. [Issue #1556](https://github.com/djust-org/djust/issues/1556) was a real production 503 incident on a djust deploy traceable to this shape.
- **Schema-per-tenant isolation is not what most multi-tenant SaaS applications actually need.** `tenant_id` filtering at the row level satisfies the typical "users in tenant A cannot see tenant B's data" requirement, and is the model djust's own mixins, state backends, and presence layer integrate with natively.
- **The migration path is straightforward** for typical applications: add `tenant_id` columns, write a one-time data migration to copy schema-qualified rows into the shared schema with the right `tenant_id`, swap middleware. See the dedicated step-by-step recipe in **[Migrating from django-tenants](migrating-from-django-tenants.md)** (mental-model translation, data migration, code/settings diffs, rollout, and a cross-tenant-leak canary test).

#### Stopgap (until migration): required settings if you remain on django-tenants

If you cannot migrate immediately and are running an existing djust + django-tenants deploy, the stopgap configuration to avoid the connection-storm class of bug is:

```python
# settings.py — stopgap only; migrate to djust.tenants for long-term support

# Skip the redundant SET search_path wire trip when the connection is
# already on the right tenant. WITHOUT THIS your deploy is one
# tick_interval-heavy LiveView away from exhausting Postgres.
TENANT_LIMIT_SET_CALLS = True
```

djust ships a startup system check (`djust.C014`) that warns when `django_tenants` is configured + `ASGI_APPLICATION` is set + `TENANT_LIMIT_SET_CALLS` is unset or `False`. The check's hint and `fix_hint` lead with the migration recommendation; the `TENANT_LIMIT_SET_CALLS = True` setting is described as a stopgap, not a long-term fix.

Additional operational recommendations for the stopgap window:

- Size Postgres `max_connections` for at least `replicas × ASGI_THREAD_LIMIT` plus headroom for Celery/admin/migrations. The default `max_connections = 100` is typically too tight for a 2-replica ASGI deploy.
- Put a **transaction-pooling pgbouncer** in front of Postgres for any multi-replica deployment. ASGI's per-request threadpool model multiplies real Postgres connections beyond what worker-process pooling does under WSGI.
- Plan a migration to `djust.tenants`. The longer the stopgap window, the more risk of regression as djust framework features evolve without active django-tenants integration testing in CI.

The remainder of this guide covers `djust.tenants`.

## What You Get

- **Automatic tenant resolution** -- From subdomain, path, headers, session, or custom logic
- **TenantMixin / TenantScopedMixin** -- Tenant-aware LiveViews with scoped querysets
- **State backend isolation** -- Tenant-aware Redis and memory backends
- **Chained resolution** -- Try multiple strategies with fallback
- **Template context** -- Automatic tenant injection

## Quick Start

### 1. Configure Tenant Resolution

```python
# settings.py
DJUST_CONFIG = {
    'TENANT_RESOLVER': 'subdomain',  # or 'path', 'header', 'session', 'custom'
    'TENANT_MAIN_DOMAIN': 'myapp.com',
    'TENANT_SUBDOMAIN_EXCLUDE': ['www', 'api', 'admin'],
    'TENANT_DEFAULT': 'public',
}
```

### 2. Use TenantMixin in Your Views

```python
from djust import LiveView
from djust.tenants import TenantMixin, TenantScopedMixin

class DashboardView(TenantScopedMixin, LiveView):
    template_name = 'dashboard.html'

    def mount(self, request):
        # self.tenant is automatically available
        self.users_count = self.get_tenant_queryset(User).count()
        self.projects = self.get_tenant_queryset(Project).order_by('-created_at')[:5]
```

### 3. Tenant-Scoped Models

```python
class TenantScopedModel(models.Model):
    tenant_id = models.CharField(max_length=50, db_index=True)

    class Meta:
        abstract = True

class Project(TenantScopedModel):
    name = models.CharField(max_length=100)
    created_at = models.DateTimeField(auto_now_add=True)
```

## Resolution Strategies

### Subdomain

```python
# acme.myapp.com -> tenant_id: "acme"
DJUST_CONFIG = {
    'TENANT_RESOLVER': 'subdomain',
    'TENANT_MAIN_DOMAIN': 'myapp.com',
    'TENANT_SUBDOMAIN_EXCLUDE': ['www', 'api'],
    'TENANT_DEFAULT': 'public',
}
```

### Path

```python
# myapp.com/acme/dashboard -> tenant_id: "acme"
DJUST_CONFIG = {
    'TENANT_RESOLVER': 'path',
    'TENANT_PATH_POSITION': 1,  # 1-based: first path segment after /
    'TENANT_PATH_EXCLUDE': ['admin', 'api', 'static'],
    'TENANT_DEFAULT': 'public',
}
```

### Header

```python
# X-Tenant-ID: acme -> tenant_id: "acme"
DJUST_CONFIG = {
    'TENANT_RESOLVER': 'header',
    'TENANT_HEADER': 'X-Tenant-ID',
    'TENANT_DEFAULT': 'public',
}
```

### Session / JWT

```python
DJUST_CONFIG = {
    'TENANT_RESOLVER': 'session',
    'TENANT_SESSION_KEY': 'tenant_id',  # session key to read
    'TENANT_JWT_CLAIM': 'tenant_id',    # JWT claim, if request.user has jwt_payload
}
```

> The session resolver also falls back to a `user.tenant_id` model attribute when neither
> the session key nor a JWT claim is present.

### Custom

```python
def custom_tenant_resolver(request):
    from djust.tenants.resolvers import TenantInfo
    tenant_id = request.user.organization.slug if request.user.is_authenticated else 'public'
    return TenantInfo(tenant_id=tenant_id, name=tenant_id, settings={'theme': 'blue'})

# settings.py
DJUST_CONFIG = {
    'TENANT_RESOLVER': 'custom',
    'TENANT_CUSTOM_RESOLVER': 'myapp.utils.custom_tenant_resolver',  # dotted path
}
```

### Chained (Fallback)

```python
# Set TENANT_RESOLVER to a LIST of short names; each is tried in order
# and the first successful match wins (chained resolution).
DJUST_CONFIG = {
    'TENANT_RESOLVER': ['header', 'subdomain', 'session'],
    'TENANT_DEFAULT': 'public',
}
```

## Mixins

### TenantMixin

Base mixin that resolves and injects `self.tenant` and adds it to template context.

```python
class MyView(TenantMixin, LiveView):
    def mount(self, request):
        logger.info("Mounted for tenant: %s", self.tenant.name)
```

### TenantScopedMixin

Extends TenantMixin with scoped querysets:

```python
class ProjectListView(TenantScopedMixin, LiveView):
    model = Project  # used as the default for the helpers below

    def mount(self, request):
        self.projects = self.get_tenant_queryset()

    def get_project(self, project_id):
        return self.get_tenant_object(project_id)
```

| Method | Description |
|--------|-------------|
| `get_tenant_queryset(model=None)` | Queryset filtered by current tenant (defaults to `self.model`) |
| `get_tenant_object(pk, model=None)` | Get a single object by `pk`, scoped to the current tenant |
| `create_for_tenant(model=None, **fields)` | Create an instance with `tenant_id` stamped automatically |

The tenant field defaults to `tenant_id`; override it per view with the
`tenant_field` class attribute.

## State Backend Isolation

```python
# settings.py
DJUST_STATE_BACKEND = 'djust.tenants.backends.TenantAwareRedisBackend'
# or
DJUST_STATE_BACKEND = 'djust.tenants.backends.TenantAwareMemoryBackend'
```

## Template Context

Tenant info is automatically available in templates:

```html
<h1>{{ tenant.name }} Dashboard</h1>

{% if tenant.settings.custom_branding %}
<style>
    :root { --primary-color: {{ tenant.settings.primary_color }}; }
</style>
{% endif %}

<span>Plan: {{ tenant.settings.plan_type|default:"Free" }}</span>
```

## Security Considerations

- **Always scope queries** to the current tenant using `TenantScopedMixin` or manual filtering.
- **Index `tenant_id` fields** in the database for query performance.
- **Validate URL access** to prevent cross-tenant data access:

```python
class TenantPermissionMixin:
    def dispatch(self, request, *args, **kwargs):
        if 'tenant_slug' in kwargs:
            if kwargs['tenant_slug'] != request.tenant.id:
                raise PermissionDenied("Access denied")
        return super().dispatch(request, *args, **kwargs)
```

## Testing

```python
from djust.tenants import set_current_tenant
from djust.tenants.resolvers import TenantInfo
from django.test import RequestFactory


def test_tenant_scoped_query(db):
    # Set the thread-local tenant, then assert tenant-scoped filtering.
    set_current_tenant(TenantInfo(tenant_id='acme'))
    try:
        projects = Project.objects.filter(tenant_id='acme')
        assert projects.count() == 2
    finally:
        set_current_tenant(None)  # clear thread-local between tests


def test_view_with_tenant():
    request = RequestFactory().get('/dashboard/')
    request.tenant = TenantInfo(tenant_id='test', name='Test Org')
    view = DashboardView()
    view.setup(request)
    view.tenant = request.tenant  # mixin populates this from request.tenant in dispatch
    assert view.tenant.id == 'test'
```

## Best Practices

- Use `TenantScopedMixin` for all data-accessing views to prevent cross-tenant leakage.
- Include `tenant_id` in cache keys to prevent data bleed through caching.
- Consider separate database connection pools per tenant for heavy workloads.
- Use `select_related` / `prefetch_related` with tenant-scoped querysets for performance.
