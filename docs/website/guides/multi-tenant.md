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
- **The migration path is straightforward** for typical applications: add `tenant_id` columns, write a one-time data migration to copy schema-qualified rows into the shared schema with the right `tenant_id`, swap middleware. A dedicated migration guide is tracked as a v1.1.0 documentation item (see [#1559](https://github.com/djust-org/djust/issues/1559) — placeholder until the issue is filed).

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
DJUST_TENANT_RESOLVER = 'djust.tenants.resolvers.SubdomainResolver'

DJUST_TENANT_CONFIG = {
    'default_tenant': 'public',
    'subdomain_resolver': {
        'domain': 'myapp.com',
        'exclude_subdomains': ['www', 'api', 'admin']
    }
}
```

### 2. Use TenantMixin in Your Views

```python
from djust import LiveView
from djust.tenants.mixins import TenantMixin, TenantScopedMixin

class DashboardView(TenantScopedMixin, LiveView):
    template_name = 'dashboard.html'

    def mount(self, request):
        # self.tenant is automatically available
        self.users_count = self.tenant_queryset(User).count()
        self.projects = self.tenant_queryset(Project).order_by('-created_at')[:5]
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
DJUST_TENANT_RESOLVER = 'djust.tenants.resolvers.SubdomainResolver'
DJUST_TENANT_CONFIG = {
    'subdomain_resolver': {
        'domain': 'myapp.com',
        'exclude_subdomains': ['www', 'api'],
        'default_tenant': 'public'
    }
}
```

### Path

```python
# myapp.com/acme/dashboard -> tenant_id: "acme"
DJUST_TENANT_RESOLVER = 'djust.tenants.resolvers.PathResolver'
DJUST_TENANT_CONFIG = {
    'path_resolver': {
        'position': 0,  # First path segment
        'default_tenant': 'public'
    }
}
```

### Header

```python
# X-Tenant-ID: acme -> tenant_id: "acme"
DJUST_TENANT_RESOLVER = 'djust.tenants.resolvers.HeaderResolver'
DJUST_TENANT_CONFIG = {
    'header_resolver': {
        'header_name': 'X-Tenant-ID',
        'default_tenant': 'public'
    }
}
```

### Session / JWT

```python
DJUST_TENANT_RESOLVER = 'djust.tenants.resolvers.SessionResolver'
DJUST_TENANT_CONFIG = {
    'session_resolver': {
        'session_key': 'tenant_id',
        'jwt_claim': 'tenant',
        'user_attribute': 'tenant_id'
    }
}
```

### Custom

```python
def custom_tenant_resolver(request):
    from djust.tenants.resolvers import TenantInfo
    tenant_id = request.user.organization.slug if request.user.is_authenticated else 'public'
    return TenantInfo(id=tenant_id, name=tenant_id, settings={'theme': 'blue'})

# settings.py
DJUST_TENANT_RESOLVER = 'myapp.utils.custom_tenant_resolver'
```

### Chained (Fallback)

```python
DJUST_TENANT_RESOLVER = 'djust.tenants.resolvers.ChainedResolver'
DJUST_TENANT_CONFIG = {
    'chained_resolver': {
        'resolvers': [
            'djust.tenants.resolvers.HeaderResolver',
            'djust.tenants.resolvers.SubdomainResolver',
            'djust.tenants.resolvers.SessionResolver'
        ],
        'default_tenant': 'public'
    }
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
    def mount(self, request):
        self.projects = self.tenant_queryset(Project)

    def get_project(self, project_id):
        return self.tenant_get_object_or_404(Project, id=project_id)
```

| Method | Description |
|--------|-------------|
| `tenant_queryset(model_class, tenant_field='tenant_id')` | Queryset filtered by current tenant |
| `tenant_get_object_or_404(model_class, **kwargs)` | Get object scoped to current tenant |
| `tenant_filter(queryset, tenant_field='tenant_id')` | Filter existing queryset by tenant |

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
from djust.tenants.test import TenantTestCase, override_tenant
from djust.tenants.resolvers import TenantInfo
from django.test import RequestFactory

class ProjectTestCase(TenantTestCase):
    def test_tenant_scoped_query(self):
        with override_tenant('acme'):
            projects = Project.objects.all()
            self.assertEqual(projects.count(), 2)

def test_view_with_tenant():
    request = RequestFactory().get('/dashboard/')
    request.tenant = TenantInfo(id='test', name='Test Org')
    view = DashboardView()
    view.setup(request)
    view.mount(request)
    assert view.tenant.id == 'test'
```

## Best Practices

- Use `TenantScopedMixin` for all data-accessing views to prevent cross-tenant leakage.
- Include `tenant_id` in cache keys to prevent data bleed through caching.
- Consider separate database connection pools per tenant for heavy workloads.
- Use `select_related` / `prefetch_related` with tenant-scoped querysets for performance.
