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
