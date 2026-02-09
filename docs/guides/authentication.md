# Authentication & Authorization

djust provides opinionated, framework-enforced authentication and authorization for LiveViews. Auth checks run server-side before `mount()` and before individual event handlers — no client-side bypass is possible.

## Quick Start

```python
from djust import LiveView

class DashboardView(LiveView):
    template_name = "dashboard.html"
    login_required = True

    def mount(self, request, **kwargs):
        self.metrics = get_metrics(request.user)
```

That's it. Unauthenticated users are automatically redirected to `settings.LOGIN_URL`.

## View-Level Auth

### Class Attributes (Primary API)

| Attribute | Type | Default | Description |
|-----------|------|---------|-------------|
| `login_required` | `bool` | `None` | `True` = must be authenticated |
| `permission_required` | `str \| list[str]` | `None` | Django permission string(s) |
| `login_url` | `str` | `None` | Override `settings.LOGIN_URL` |

```python
class AdminView(LiveView):
    template_name = "admin/dashboard.html"
    login_required = True
    permission_required = "analytics.view_dashboard"
    login_url = "/admin/login/"

    def mount(self, request, **kwargs):
        self.stats = get_admin_stats()
```

Multiple permissions (all required):

```python
class BillingView(LiveView):
    login_required = True
    permission_required = ["billing.view_invoices", "billing.manage_payments"]
```

### Mixins (Django-Familiar Convenience)

If you prefer Django's mixin pattern:

```python
from djust.auth import LoginRequiredMixin, PermissionRequiredMixin

class DashboardView(LoginRequiredMixin, PermissionRequiredMixin, LiveView):
    template_name = "dashboard.html"
    permission_required = "analytics.view_dashboard"
```

`LoginRequiredMixin` sets `login_required = True`.
`PermissionRequiredMixin` sets `login_required = True` and expects you to set `permission_required`.

### Custom Authorization Logic

Override `check_permissions()` for authorization logic that goes beyond Django's permission system:

```python
class ProjectView(LiveView):
    login_required = True

    def check_permissions(self, request):
        """Return True to allow, raise PermissionDenied or return False to deny."""
        project = Project.objects.get(pk=self.kwargs.get("pk"))
        return project.team.members.filter(user=request.user).exists()
```

The method is only called if the subclass defines it — the base `LiveView` class does not invoke it by default.

### Explicitly Public Views

Set `login_required = False` to explicitly mark a view as public. This suppresses the `djust_audit` warning:

```python
class LandingView(LiveView):
    login_required = False  # Explicitly public

    def mount(self, request, **kwargs):
        self.features = get_public_features()
```

## Handler-Level Permissions

Protect individual event handlers with `@permission_required`:

```python
from djust.decorators import event_handler, permission_required

class ProjectView(LiveView):
    login_required = True

    @event_handler()
    def search(self, query: str = "", **kwargs):
        # Any authenticated user can search
        self.results = Project.objects.filter(name__icontains=query)

    @permission_required("projects.delete_project")
    @event_handler()
    def delete_project(self, project_id: int, **kwargs):
        # Only users with delete permission
        Project.objects.get(pk=project_id).delete()
```

When permission is denied, the client receives a `"Permission denied"` error message. The handler never executes.

## How It Works

### Mount Flow

```
Client sends "mount" message
    → WebSocket consumer creates view instance
    → check_view_auth() runs:
        1. login_required? → check request.user.is_authenticated
        2. permission_required? → check request.user.has_perms()
        3. check_permissions()? → call custom hook (if overridden)
    → If unauthenticated: send {"type": "navigate", "to": "/login/"}
    → If authenticated but lacking perms: close with 4403 (PermissionDenied)
    → If passed: call view.mount(request, **kwargs)
```

### Event Flow

```
Client sends "event" message
    → _validate_event_security() runs:
        1. Event name safety check
        2. Handler exists and is @event_handler
        3. Rate limit check
        4. @permission_required check (if present)
    → If denied: send {"type": "error", "error": "Permission denied"}
    → If passed: call handler(**params)
```

## System Checks

djust registers a Django system check (`djust.S005`) that warns when LiveView subclasses expose state without authentication:

```
$ python manage.py check
System check identified some issues:

WARNINGS:
?: (djust.S005) myapp.views.ContactListView exposes state without authentication.
    HINT: Add login_required = True or permission_required to protect this view,
    or set login_required = False to acknowledge public access.
```

This check runs automatically in CI via `python manage.py check`.

## Audit Integration

The `djust_audit` command shows auth configuration for every view:

```
$ python manage.py djust_audit

  LiveView: crm.views.ContactListView
    Template:   crm/contacts/list.html
    Auth:       login_required, permission: crm.view_contacts
    Exposed state:
      contacts                 (mount)
      search_query             (mount)
    Handlers:
      * search(value: str = "")          @debounce(wait=0.3)
      * delete(contact_id: int)          @permission_required(crm.delete_contact)

  LiveView: public.views.LandingView
    Template:   public/landing.html
    Auth:       (none)                   ⚠ exposes state without auth
    Exposed state:
      features                 (mount)
```

JSON output includes an `"unprotected_with_state"` count in the summary.

## Best Practices

1. **Always set `login_required`** — even `login_required = False` is better than leaving it as `None`, because it shows intent
2. **Use `permission_required` for role-based access** — maps directly to Django's permission system
3. **Use `check_permissions()` for object-level access** — verify the user can access *this specific* resource
4. **Use `@permission_required` on destructive handlers** — even if the view is already protected, adding handler-level permission for delete/update operations provides defense-in-depth
5. **Run `djust_audit` in CI** — catch unprotected views before they ship

## Known Limitations

### Auth is checked at mount time only

View-level auth (`login_required`, `permission_required`, `check_permissions()`) runs once during WebSocket mount. If a user's session expires or permissions are revoked while the WebSocket is open, subsequent events will continue to be processed until the next reconnect.

This is a conscious trade-off shared by all LiveView-style frameworks (Phoenix, Laravel Livewire, etc.) — re-checking auth on every event would add a database query per interaction.

**Mitigations:**
- Use handler-level `@permission_required` for sensitive operations (checked per event)
- WebSocket heartbeats will eventually detect stale sessions
- Critical admin actions should use `check_permissions()` on the handler itself
