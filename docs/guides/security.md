# Security Model

djust's LiveView architecture provides structural security advantages over traditional REST/GraphQL APIs. This guide explains the security model, what data reaches the client, and best practices for keeping your application secure.

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

## LiveView vs. API Security

### What APIs expose

With a REST or GraphQL API, the client receives raw, structured data:

```json
{
  "id": 42,
  "name": "Jane Doe",
  "email": "jane@example.com",
  "ssn": "123-45-6789",
  "internal_score": 87.5
}
```

Every endpoint requires a deliberate serialization decision. Common risks:

- **Over-fetching** — a serializer returns fields you didn't mean to expose
- **Nested leaks** — a related object pulls in unexpected fields (`contact.company.billing_info`)
- **GraphQL introspection** — reveals your entire schema to attackers
- **IDOR** — guessing `/api/contacts/43` to access another user's record
- **Client-side exposure** — raw data lives in the browser (network tab, JS memory, localStorage)

### What djust exposes

The client only ever receives rendered HTML:

```html
<td>Jane Doe</td><td>jane@example.com</td>
```

No serializer to misconfigure. The `ssn` and `internal_score` fields were never sent because the template doesn't render them. There is no network payload to inspect for extra fields — they simply don't exist on the wire.

### Comparison

| Concern | REST/GraphQL API | djust LiveView |
|---------|-----------------|----------------|
| Data format on wire | Raw JSON (structured data) | HTML fragments (rendered output) |
| Default exposure | Everything unless excluded | Nothing unless rendered |
| Serialization bugs | Common (forgotten fields, nested leaks) | Not applicable |
| Client data inspection | Full raw values in DevTools | Only visible HTML |
| Schema discovery | GraphQL introspection, error probing | Not possible |
| Attack surface | Every endpoint + every field | Event handlers only |
| Over-fetching risk | High (especially GraphQL) | None |

### Where they're equal

The LiveView model does **not** eliminate all security concerns:

- **Authorization** — if `mount()` doesn't check permissions, the view renders data the user shouldn't see
- **IDOR** — `ContactDetailView` still needs to verify the user can access that `pk`
- **Business logic bugs** — a handler that performs an unauthorized action is still a bug

Both architectures require proper access control. The difference is that LiveView eliminates the entire class of *data serialization* vulnerabilities.

## Authentication & Authorization

djust provides built-in auth enforcement at two levels: **view-level** (before `mount()`) and **handler-level** (before individual event handlers).

### View-Level Auth (Class Attributes)

```python
class DashboardView(LiveView):
    template_name = "dashboard.html"
    login_required = True                              # Must be authenticated
    permission_required = "analytics.view_dashboard"   # Django permission string
    login_url = "/login/"                              # Override settings.LOGIN_URL

    def mount(self, request, **kwargs):
        self.metrics = get_metrics(request.user)
```

When an unauthenticated user connects, djust sends a `navigate` message redirecting them to the login URL. No `mount()` call happens — the view never initializes.

### View-Level Auth (Mixins)

```python
from djust.auth import LoginRequiredMixin, PermissionRequiredMixin

class DashboardView(LoginRequiredMixin, PermissionRequiredMixin, LiveView):
    template_name = "dashboard.html"
    permission_required = "analytics.view_dashboard"
```

### Custom Authorization Logic

Override `check_permissions()` for complex authorization:

```python
class ProjectView(LiveView):
    login_required = True

    def check_permissions(self, request):
        """Return True to allow, raise PermissionDenied or return False to deny."""
        project = Project.objects.get(pk=self.kwargs.get("pk"))
        return project.team.members.filter(user=request.user).exists()
```

### Handler-Level Permissions

Protect individual event handlers with `@permission_required`:

```python
from djust.decorators import event_handler, permission_required

class ProjectView(LiveView):
    login_required = True

    @permission_required("projects.delete_project")
    @event_handler()
    def delete_project(self, project_id: int, **kwargs):
        Project.objects.get(pk=project_id).delete()
```

### Auth in djust_audit

The `djust_audit` command shows auth configuration for each view:

```
  LiveView: crm.views.ContactListView
    Template:   crm/contacts/list.html
    Auth:       login_required, permission: crm.view_contacts
    ...
```

Views that expose state without auth are flagged with a warning.

See the [Authentication Guide](authentication.md) for complete documentation.

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

## Event Handler Security

Clients can only call methods explicitly marked with `@event_handler`. Three layers of protection are active by default:

1. **Regex guard** — blocks `_private`, `__dunder__`, and malformed event names before `getattr()` runs
2. **Decorator allowlist** — only `@event_handler`-decorated methods are callable (in `strict` mode, the default)
3. **Rate limiting** — per-connection token bucket prevents event flooding

```python
from djust.decorators import event_handler, rate_limit

class MyView(LiveView):
    @rate_limit(rate=5, burst=3)
    @event_handler
    def expensive_search(self, query: str = "", **kwargs):
        """Only this method is callable from the client."""
        self.results = search(query)

    def _internal_helper(self):
        """NOT callable — no decorator, and _ prefix blocked by regex."""
        pass
```

See [SECURITY_GUIDELINES.md](../SECURITY_GUIDELINES.md) for the full event security specification.

## `push_event()`: Explicit Client Communication

`push_event()` is the only mechanism for sending arbitrary data to the client outside of rendered HTML. Since you control the payload, treat it like an API response:

```python
# GOOD — send only what the client needs
self.push_event("notification", {"message": "Contact saved", "type": "success"})

# BAD — leaking internal data
self.push_event("debug", {"contact": model_to_dict(self.contact)})
```

## Best Practices

### Do

- **Check permissions in `mount()`** — verify the user can access the requested resource
- **Use `_` prefix** for internal state that shouldn't reach templates
- **Use `@event_handler`** on every handler — keep `event_security = "strict"` (the default)
- **Use `@rate_limit`** on expensive operations (search, export, bulk actions)
- **Validate handler parameters** — don't trust client-provided values
- **Keep `DEBUG=False` in production** — debug payloads expose variable values

### Don't

- **Don't use `push_event()` to send raw model data** — serialize only what the client needs
- **Don't store secrets in non-private attributes** — anything without `_` prefix enters the template context
- **Don't skip authorization in `mount()`** — LiveView prevents data *serialization* leaks, not *access control* bugs
- **Don't enable `event_security = "open"`** in production — it allows calling any public method via WebSocket

## Auditing Your Application

Use `djust_audit` to review what your application exposes:

```bash
# See all views, handlers, exposed state, and decorators
python manage.py djust_audit

# Machine-readable output for CI
python manage.py djust_audit --json

# Include template variable sub-paths (e.g. contacts → id, name, email)
python manage.py djust_audit --verbose
```

The audit reports every LiveView and LiveComponent with:

- **Exposed state** — every public `self.xxx` attribute set in your methods, which `get_context_data()` will include in the template context. This is the data surface area of each view.
- **Handlers** — event handler signatures, parameter types, and decorator protections
- **Mixins** — optional mixins like `PresenceMixin`, `FormMixin`, etc.
- **Config** — tick intervals, temporary assigns, actor mode

With `--verbose`, exposed state entries show **template variable sub-paths** (e.g., `contacts → id, first_name, email`) so you can see exactly which model fields the template references. This requires the Rust extension.

Example output:

```
  LiveView: crm.views.ContactListView
    Template:   crm/contacts/list.html
    Mixins:     (none)
    Exposed state:
      contacts                 (mount)  → id, first_name, last_name, email
      page                     (mount)
      search_query             (mount)
      total_count              (_refresh_contacts)
    Handlers:
      * search(value: str = "")          @debounce(wait=0.3)
```

## Further Reading

- [Security Guidelines for Contributors](../SECURITY_GUIDELINES.md) — banned patterns, code review checklist, security testing
- [Event Handlers](../EVENT_HANDLERS.md) — `@event_handler` decorator reference
- [Multi-Tenant Security](multi-tenant.md) — tenant isolation patterns
