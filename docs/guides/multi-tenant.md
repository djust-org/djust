# Multi-Tenant Applications

djust provides comprehensive multi-tenant support for building SaaS applications with complete tenant isolation.

## Overview

Multi-tenant support includes:
- **Automatic Tenant Resolution** - From subdomain, path, headers, or custom logic
- **Data Isolation** - Tenant-scoped queries and state management
- **Flexible Resolution Strategies** - Subdomain, path, header, session, custom, and chained
- **State Backend Isolation** - Tenant-aware Redis and memory backends
- **Template Context** - Automatic tenant injection into templates

## Quick Start

### 1. Configure Tenant Resolution

Resolution is configured through flat keys in `DJUST_CONFIG` (there is no
`DJUST_TENANT_RESOLVER` / `DJUST_TENANT_CONFIG` setting). `TENANT_RESOLVER`
takes a registry **name**, a list of names (chained), or a callable:

```python
# settings.py
DJUST_CONFIG = {
    'TENANT_RESOLVER': 'subdomain',       # 'subdomain' | 'path' | 'header' | 'session' | 'custom'
    'TENANT_MAIN_DOMAIN': 'myapp.com',
    'TENANT_SUBDOMAIN_EXCLUDE': ['www', 'api', 'admin'],
    'TENANT_DEFAULT': 'public',           # used when nothing resolves
}

MIDDLEWARE = [
    # ...
    'djust.tenants.middleware.TenantMiddleware',
]
```

Equivalent shorthand — `DJUST_TENANTS = {'RESOLVER': 'subdomain', 'REQUIRED': True, ...}`
also activates the middleware (`REQUIRED: True` raises 404 when nothing
resolves). With neither key configured, `TenantMiddleware` is a zero-cost
no-op. A dotted class path in `TENANT_RESOLVER` is NOT a valid value — it
logs "Unknown tenant resolver" and falls back to subdomain; use
`'custom'` + `TENANT_CUSTOM_RESOLVER` for callables.


### 2. Scope a view to the tenant
```python
from djust import LiveView
from djust.tenants.mixin import TenantMixin, TenantScopedMixin  # module is `mixin` (singular)

class DashboardView(TenantMixin, LiveView):
    template_name = 'dashboard.html'

    def mount(self, request, **kwargs):
        # self.tenant (a TenantInfo) is resolved automatically
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
        self.projects = self.get_tenant_queryset(Project)
```

## Tenant Resolution Strategies

### Subdomain Resolution

Extract tenant from subdomain:

```python
# acme.myapp.com → tenant_id: "acme"
DJUST_CONFIG = {
    'TENANT_RESOLVER': 'subdomain',
    'TENANT_MAIN_DOMAIN': 'myapp.com',            # optional: pin the domain
    'TENANT_SUBDOMAIN_EXCLUDE': ['www', 'api'],   # ignored subdomains
    'TENANT_DEFAULT': 'public',
}
```

### Path Resolution

Take the tenant from a URL path segment.

```python
# myapp.com/acme/dashboard → tenant_id: "acme"
DJUST_CONFIG = {
    "TENANT_RESOLVER": "path",
    "TENANT_PATH_POSITION": 1,   # 1-based: the first path segment
}
```

```python
# urls.py
urlpatterns = [
    path("<str:tenant_slug>/", include("app.urls")),
]
```

### Header Resolution

Take the tenant from an HTTP request header — the usual choice for APIs.

```python
# X-Tenant-ID: acme → tenant_id: "acme"
DJUST_CONFIG = {
    "TENANT_RESOLVER": "header",
    "TENANT_HEADER": "X-Tenant-ID",   # default
}
```

### Session Resolution

Read the tenant from the session, falling back to a JWT claim and then to
`request.user.tenant_id`.

```python
DJUST_CONFIG = {
    "TENANT_RESOLVER": "session",
    "TENANT_SESSION_KEY": "tenant_id",   # default
    "TENANT_JWT_CLAIM": "tenant_id",     # default; read from user.jwt_payload
}
```

> This resolver **never writes** the session, and a WebSocket handler's
> `request.session[...] = ...` has no built-in save guarantee — Django's
> `SessionMiddleware` only persists on the HTTP response cycle. Treat a session
> write from a WS handler as a best-effort mirror.

### Chained Resolution

Try several strategies in order and take the first that matches. Configure it
by giving `TENANT_RESOLVER` a **list** of registry names rather than one name.

```python
DJUST_CONFIG = {
    "TENANT_RESOLVER": ["header", "subdomain", "session"],
    "TENANT_HEADER": "X-Tenant-ID",
}
```

Unknown names are logged and skipped rather than raising.

### Custom Resolution

Point `TENANT_CUSTOM_RESOLVER` at a callable taking the request and returning a
`TenantInfo` (or a plain `str`, which is wrapped for you).

```python
# myapp/tenants.py
from djust.tenants.resolvers import TenantInfo

def resolve_tenant(request):
    if request.user.is_authenticated:
        org = request.user.organization
        return TenantInfo(tenant_id=org.slug, name=org.name)
    return TenantInfo(tenant_id="public")
```

```python
DJUST_CONFIG = {
    "TENANT_RESOLVER": "custom",
    "TENANT_CUSTOM_RESOLVER": "myapp.tenants.resolve_tenant",
}
```

The registry names are `subdomain` (default), `path`, `header`, `session` and
`custom`; an unrecognised name logs a warning and falls back to `subdomain`.

## Mixins

### TenantMixin

Base mixin for tenant-aware views (`djust/tenants/mixin.py` — there is no
`tenants.mixins` module and no `setup()` hook; resolution happens in the
LiveView lifecycle and `self.tenant` is a property over the resolved
`TenantInfo`):

- `tenant` — property returning the resolved `TenantInfo` (or `None`)
- `set_tenant(tenant_id) -> TenantInfo` — set/switch the tenant explicitly
- `resolve_tenant(request)` — run the configured resolver
- `get_context_data()` — injects the tenant under `TENANT_CONTEXT_NAME`
  (default `tenant`)
- `get_presence_key()` / `get_state_key_prefix()` — automatically tenant-scope
  presence and state keys (`tenant:<id>:…`), so presence and state isolation
  fall out of the mixin

`TenantInfo` exposes `id`, `name`, `settings` (dict), `metadata` (dict), and
`raw` (the original object, e.g. a Django model instance).

### TenantScopedMixin

Extends `TenantMixin` with tenant-scoped model helpers:

- `get_tenant_queryset(model=None) -> QuerySet` — `model` defaults to the
  view's `model` attribute; filtered to the current tenant
- `create_for_tenant(model=None, **kwargs)` — create with the tenant set
- `get_tenant_object(pk, model=None)` — fetch a pk scoped to the current
  tenant (raises DoesNotExist for other tenants — an IDOR guard, not a 404
  helper)

Usage:

```python
class ProjectListView(TenantScopedMixin, LiveView):
    model = Project

    def mount(self, request, **kwargs):
        # Automatically filtered by tenant
        self.projects = self.get_tenant_queryset()

    def get_project(self, project_id):
        # Ensures project belongs to current tenant
        return self.get_tenant_object(project_id)

    def create_project(self, name):
        return self.create_for_tenant(name=name)
```

## State & Presence Isolation

LiveView **state** backends (`DJUST_STATE_BACKEND` env or
`DJUST_CONFIG['STATE_BACKEND']`) accept only `'redis' | 'memory'` or a
`redis://` URL — not dotted class paths. Tenant isolation of state comes from
`TenantMixin.get_state_key_prefix()` (above): on a tenant-resolved view,
state keys are automatically prefixed `tenant:<id>:…`.

For **presence**, djust ships tenant-scoped backends
(`djust/tenants/backends.py`):

```python
from djust.tenants.backends import (
    TenantAwareBackendMixin,      # mixin: prefixes keys tenant:<id>:<key> (private _tenant_key)
    TenantAwareRedisBackend,      # PresenceBackend + tenant scoping
    TenantAwareMemoryBackend,     # PresenceBackend + tenant scoping
    get_tenant_presence_backend,  # (tenant_id: str) -> PresenceBackend
)
```

`TenantAwareBackendMixin` is a mixin (not a standalone backend class), and
these are **presence** backends — pass one via `DJUST_CONFIG['PRESENCE_BACKEND']`
if you want tenant-aware presence globally:

## Presence System Integration

Tenant-aware presence for real-time features:

```python
from djust.tenants.backends import get_tenant_presence_backend

class CollaborationView(TenantMixin, LiveView):
    def mount(self, request, **kwargs):
        # takes the tenant ID string; returns a PresenceBackend with the
        # standard join/leave/list/count/heartbeat API
        self.presence = get_tenant_presence_backend(self.tenant.id)
        self.presence.join(self.get_presence_key(), str(request.user.id), {
            'name': request.user.get_full_name(),
        })

    def leave(self):
        self.presence.leave(self.get_presence_key(), str(self.request.user.id))
```

(There is no `djust.tenants.presence` module and no `track_user`/`untrack_user`
API — presence uses `join`/`leave`/`list`.)

## Template Context

Tenant information is available in templates once you register the context
processor (this step is required, not automatic):

```python
# settings.py
TEMPLATES = [{
    'OPTIONS': {
        'context_processors': [
            # ...
            'djust.tenants.context_processor',
        ],
    },
}]
```

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
        self.users_count = self.get_tenant_queryset(User).count()
        self.projects = self.get_tenant_queryset(Project).order_by('-created_at')[:5]
        self.usage_stats = self.get_usage_stats()

    def get_usage_stats(self):
        return {
            'storage_used': self.get_tenant_queryset(File).aggregate(
                total=Sum('size')
            )['total'] or 0,
            'api_calls': self.get_api_usage(),   # your own method
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
        self.team_members = self.get_tenant_queryset(User)
        self.recent_activity = self.get_tenant_queryset(Activity).order_by('-created_at')[:10]

    def invite_member(self, email):
        if self.has_permission('invite_users'):   # your own method
            invite = TeamInvite.objects.create(
                tenant_id=self.tenant.id,
                email=email,
                invited_by=self.request.user
            )
            self.send_invitation_email(invite)   # your own method
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
        self.projects = self.get_tenant_queryset(Project)
```

3. **Configure resolution:**

```python
# settings.py
DJUST_CONFIG = {
    'TENANT_RESOLVER': 'subdomain',
    'TENANT_MAIN_DOMAIN': 'myapp.com',
}
MIDDLEWARE += ['djust.tenants.middleware.TenantMiddleware']
```

## Testing

There is no `djust.tenants.test` module (`TenantTestCase` /
`override_tenant` do not exist). Test tenant resolution directly with
`RequestFactory` + `TenantInfo`, or through the middleware:

```python
from django.test import RequestFactory, TestCase
from djust.tenants.resolvers import TenantInfo

class ProjectViewTest(TestCase):
    def test_view_with_tenant(self):
        view = DashboardView()
        view.request = RequestFactory().get('/dashboard/')
        # `TenantInfo`'s first argument is POSITIONAL `tenant_id` — there is no
        # `id=` keyword (`tenants/resolvers.py:49`). Assign through the `tenant`
        # property SETTER (`tenants/mixin.py:91`), which marks the tenant
        # resolved so `_ensure_tenant()` will not overwrite it. Setting
        # `request.tenant` does NOT work: `resolve_tenant()` runs the configured
        # resolver and ignores it, and `mount()` never calls `_ensure_tenant()`
        # (only `dispatch`/`get`/`post` do).
        view.tenant = TenantInfo('test', name='Test Org')

        self.assertEqual(view.tenant.id, 'test')

    def test_middleware_sets_tenant(self):
        # with subdomain resolution configured + ALLOWED_HOSTS
        request = RequestFactory().get('/', HTTP_HOST='acme.myapp.com')
        response = TenantMiddleware(lambda r: r)(request)
        self.assertEqual(request.tenant.id, 'acme')
```

- **Database Indexes**: Always index tenant_id fields
- **Query Optimization**: Use select_related/prefetch_related
- **Caching**: Include tenant_id in cache keys
- **Connection Pooling**: Consider per-tenant connection pools

## API Reference

<!-- TODO: Create docs/api/multi-tenant.md with full API documentation -->
