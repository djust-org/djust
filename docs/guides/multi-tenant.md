# Multi-Tenant Applications

djust v0.3.0 provides comprehensive multi-tenant support for building SaaS applications with complete tenant isolation.

## Overview

Multi-tenant support includes:
- **Automatic Tenant Resolution** - From subdomain, path, headers, or custom logic
- **Data Isolation** - Tenant-scoped queries and state management
- **Flexible Resolution Strategies** - Subdomain, path, header, session, custom, and chained
- **State Backend Isolation** - Tenant-aware Redis and memory backends
- **Template Context** - Automatic tenant injection into templates

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

### 2. Use TenantMixin in your views

```python
from djust import LiveView
from djust.tenants.mixins import TenantMixin, TenantScopedMixin

class DashboardView(TenantMixin, LiveView):
    template_name = 'dashboard.html'

    def mount(self, request):
        # self.tenant automatically available
        self.stats = self.get_tenant_stats()

    def get_tenant_stats(self):
        # Automatically scoped to current tenant
        return {
            'users': User.objects.filter(tenant=self.tenant.id).count(),
            'projects': Project.objects.filter(tenant=self.tenant.id).count(),
        }
```

### 3. Tenant-scoped models

```python
class TenantScopedModel(models.Model):
    tenant_id = models.CharField(max_length=50, db_index=True)

    class Meta:
        abstract = True

class Project(TenantScopedModel):
    name = models.CharField(max_length=100)
    created_at = models.DateTimeField(auto_now_add=True)

class ProjectView(TenantScopedMixin, LiveView):
    def mount(self, request):
        # Automatically filters by tenant
        self.projects = self.tenant_queryset(Project)
```

## Tenant Resolution Strategies

### Subdomain Resolution

Extract tenant from subdomain:

```python
# acme.myapp.com → tenant_id: "acme"
DJUST_TENANT_RESOLVER = 'djust.tenants.resolvers.SubdomainResolver'
DJUST_TENANT_CONFIG = {
    'subdomain_resolver': {
        'domain': 'myapp.com',
        'exclude_subdomains': ['www', 'api'],
        'default_tenant': 'public'
    }
}
```

### Path Resolution

Extract tenant from URL path:

```python
# myapp.com/acme/dashboard → tenant_id: "acme"
DJUST_TENANT_RESOLVER = 'djust.tenants.resolvers.PathResolver'
DJUST_TENANT_CONFIG = {
    'path_resolver': {
        'position': 0,  # First path segment
        'default_tenant': 'public'
    }
}

# URL patterns
urlpatterns = [
    path('<str:tenant_slug>/', include('app.urls')),
]
```

### Header Resolution

Extract tenant from HTTP headers:

```python
# X-Tenant-ID: acme → tenant_id: "acme"
DJUST_TENANT_RESOLVER = 'djust.tenants.resolvers.HeaderResolver'
DJUST_TENANT_CONFIG = {
    'header_resolver': {
        'header_name': 'X-Tenant-ID',
        'default_tenant': 'public'
    }
}
```

### Session Resolution

Extract tenant from session/JWT:

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

### Custom Resolution

Implement custom tenant logic:

```python
def custom_tenant_resolver(request):
    from djust.tenants.resolvers import TenantInfo

    # Custom logic here
    if request.user.is_authenticated:
        tenant_id = request.user.organization.slug
    else:
        tenant_id = 'public'

    return TenantInfo(
        id=tenant_id,
        name=request.user.organization.name if request.user.is_authenticated else 'Public',
        settings={'theme': 'blue'}
    )

# settings.py
DJUST_TENANT_RESOLVER = 'myapp.utils.custom_tenant_resolver'
```

### Chained Resolution

Try multiple strategies with fallback:

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

Base mixin for tenant-aware views:

```python
class TenantMixin:
    def setup(self, request, *args, **kwargs):
        super().setup(request, *args, **kwargs)
        self.tenant = resolve_tenant(request)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['tenant'] = self.tenant
        return context
```

Usage:

```python
class MyView(TenantMixin, LiveView):
    def mount(self, request):
        # self.tenant available
        logger.info("Mounted for tenant: %s", self.tenant.name)
```

### TenantScopedMixin

Provides tenant-scoped querysets:

```python
class TenantScopedMixin(TenantMixin):
    def tenant_queryset(self, model_class, tenant_field='tenant_id'):
        """Get queryset filtered by current tenant."""

    def tenant_get_object_or_404(self, model_class, **kwargs):
        """Get object scoped to current tenant."""

    def tenant_filter(self, queryset, tenant_field='tenant_id'):
        """Filter existing queryset by tenant."""
```

Usage:

```python
class ProjectListView(TenantScopedMixin, LiveView):
    def mount(self, request):
        # Automatically filtered by tenant
        self.projects = self.tenant_queryset(Project)

    def get_project(self, project_id):
        # Ensures project belongs to current tenant
        return self.tenant_get_object_or_404(Project, id=project_id)
```

## State Backend Isolation

### Tenant-Aware Backends

Configure tenant isolation for LiveView state:

```python
# settings.py
DJUST_STATE_BACKEND = 'djust.tenants.backends.TenantAwareRedisBackend'

# or
DJUST_STATE_BACKEND = 'djust.tenants.backends.TenantAwareMemoryBackend'
```

### Custom Backend

```python
from djust.tenants.backends import TenantAwareBackend

class CustomTenantBackend(TenantAwareBackend):
    def get_tenant_key(self, tenant, key):
        return f"tenant:{tenant.id}:{key}"

    def get(self, tenant, key):
        # Implementation

    def set(self, tenant, key, value, timeout=None):
        # Implementation
```

## Presence System Integration

Tenant-aware presence for real-time features:

```python
# settings.py
DJUST_PRESENCE_BACKEND = 'djust.tenants.presence.TenantPresenceBackend'

# In your view
class CollaborationView(TenantMixin, LiveView):
    def mount(self, request):
        self.presence = get_tenant_presence_backend(self.tenant)
        self.presence.track_user(request.user.id, {
            'name': request.user.name,
            'avatar': request.user.avatar_url
        })

    def handle_disconnect(self):
        self.presence.untrack_user(self.request.user.id)
```

## Template Context

Tenant information is automatically available in templates:

```html
<!-- dashboard.html -->
<h1>{{ tenant.name }} Dashboard</h1>

{% if tenant.settings.custom_branding %}
    <style>
        :root {
            --primary-color: {{ tenant.settings.primary_color }};
        }
    </style>
{% endif %}

<div class="tenant-info">
    <span>Organization: {{ tenant.name }}</span>
    <span>Plan: {{ tenant.settings.plan_type|default:"Free" }}</span>
</div>
```

## Database Patterns

### Shared Database, Separate Schemas

```python
# Use tenant_id foreign key
class Organization(models.Model):
    slug = models.SlugField(unique=True)
    name = models.CharField(max_length=100)

class User(AbstractUser):
    tenant = models.ForeignKey(Organization, on_delete=models.CASCADE)

class Project(models.Model):
    tenant = models.ForeignKey(Organization, on_delete=models.CASCADE)
    name = models.CharField(max_length=100)
```

### Separate Databases

```python
class TenantDatabaseRouter:
    def db_for_read(self, model, **hints):
        if hasattr(model._meta, 'tenant_model'):
            return f"tenant_{get_current_tenant()}"
        return None

# settings.py
DATABASE_ROUTERS = ['myapp.routers.TenantDatabaseRouter']
```

## Security Considerations

### Row Level Security

Ensure all queries are tenant-scoped:

```python
class TenantScopedViewSet(viewsets.ModelViewSet):
    def get_queryset(self):
        return self.queryset.filter(tenant_id=self.request.tenant.id)
```

### URL Access Control

Prevent cross-tenant data access:

```python
class TenantPermissionMixin:
    def dispatch(self, request, *args, **kwargs):
        if 'tenant_slug' in kwargs:
            if kwargs['tenant_slug'] != request.tenant.id:
                raise PermissionDenied("Access denied")
        return super().dispatch(request, *args, **kwargs)
```

## Examples

### SaaS Dashboard

```python
class SaaSDashboard(TenantScopedMixin, LiveView):
    template_name = 'saas/dashboard.html'

    def mount(self, request):
        self.users_count = self.tenant_queryset(User).count()
        self.projects = self.tenant_queryset(Project).order_by('-created_at')[:5]
        self.usage_stats = self.get_usage_stats()

    def get_usage_stats(self):
        return {
            'storage_used': self.tenant_queryset(File).aggregate(
                total=Sum('size')
            )['total'] or 0,
            'api_calls': self.get_api_usage(),
            'plan_limit': self.tenant.settings.get('plan_limit', 1000)
        }
```

### Multi-Tenant E-commerce

```python
class StoreView(TenantMixin, LiveView):
    template_name = 'store/products.html'

    def mount(self, request):
        # Products scoped to store tenant
        self.products = Product.objects.filter(
            store__tenant_id=self.tenant.id,
            is_active=True
        )

    def add_to_cart(self, product_id):
        product = get_object_or_404(
            Product,
            id=product_id,
            store__tenant_id=self.tenant.id
        )
        # Add to cart logic
```

### Team Collaboration

```python
class TeamWorkspaceView(TenantScopedMixin, LiveView):
    template_name = 'workspace.html'

    def mount(self, request):
        self.team_members = self.tenant_queryset(User)
        self.recent_activity = self.tenant_queryset(Activity).order_by('-created_at')[:10]

    def invite_member(self, email):
        if self.has_permission('invite_users'):
            invite = TeamInvite.objects.create(
                tenant_id=self.tenant.id,
                email=email,
                invited_by=self.request.user
            )
            self.send_invitation_email(invite)
```

## Migration Guide

### From Single-Tenant to Multi-Tenant

1. **Add tenant fields to models:**

```python
# Migration
class Migration(migrations.Migration):
    dependencies = [('app', '0001_initial')]

    operations = [
        migrations.AddField('project', 'tenant_id',
                          models.CharField(max_length=50, default='default')),
        migrations.AddIndex('project',
                          models.Index(fields=['tenant_id'])),
    ]
```

2. **Update views:**

```python
# Before
class ProjectView(LiveView):
    def mount(self, request):
        self.projects = Project.objects.all()

# After
class ProjectView(TenantScopedMixin, LiveView):
    def mount(self, request):
        self.projects = self.tenant_queryset(Project)
```

3. **Configure resolution:**

```python
# settings.py
DJUST_TENANT_RESOLVER = 'djust.tenants.resolvers.SubdomainResolver'
```

## Testing

### Test Utilities

```python
from djust.tenants.test import TenantTestCase, override_tenant

class ProjectTestCase(TenantTestCase):
    def test_tenant_scoped_query(self):
        with override_tenant('acme'):
            projects = Project.objects.all()  # Scoped to 'acme'
            self.assertEqual(projects.count(), 2)
```

### Mock Tenant Resolution

```python
from django.test import RequestFactory
from djust.tenants.resolvers import TenantInfo

def test_view_with_tenant():
    request = RequestFactory().get('/dashboard/')
    request.tenant = TenantInfo(id='test', name='Test Org')

    view = DashboardView()
    view.setup(request)
    view.mount(request)

    assert view.tenant.id == 'test'
```

## Performance Considerations

- **Database Indexes**: Always index tenant_id fields
- **Query Optimization**: Use select_related/prefetch_related
- **Caching**: Include tenant_id in cache keys
- **Connection Pooling**: Consider per-tenant connection pools

## API Reference

<!-- TODO: Create docs/api/multi-tenant.md with full API documentation -->
