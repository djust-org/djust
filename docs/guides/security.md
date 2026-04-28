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
- **Leave `DJUST_EXPOSE_TIMING=False` in production** — VDOM patch timing and performance metadata are useful for local dev and staging profiling, but cross-origin observers can use them to differentiate code paths (DB hit vs cache miss, valid vs invalid CSRF) by comparing handler durations. When `DEBUG=False` the framework hides these fields by default; set `DJUST_EXPOSE_TIMING = True` only in staging environments where you need the timing data and have network-level access control. ([#654](https://github.com/djust-org/djust/issues/654))

### Don't

- **Don't use `push_event()` to send raw model data** — serialize only what the client needs
- **Don't store secrets in non-private attributes** — anything without `_` prefix enters the template context
- **Don't skip authorization in `mount()`** — LiveView prevents data *serialization* leaks, not *access control* bugs
- **Don't enable `event_security = "open"`** in production — it allows calling any public method via WebSocket

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

## Auditing Your Application

Use `djust_audit` to review what your application exposes:

```bash
# See all views, handlers, exposed state, and decorators
python manage.py djust_audit

# Machine-readable output for CI
python manage.py djust_audit --json

# Include template variable sub-paths (e.g. contacts → id, name, email)
python manage.py djust_audit --verbose

# Validate against a committed permissions.yaml (declarative RBAC, #657)
python manage.py djust_audit --permissions permissions.yaml --strict
```

For production apps with role-based access control, also commit a
`permissions.yaml` at your project root that declares the expected auth
configuration for every LiveView, and run `djust_audit --permissions` in CI.
The document is an auditable artifact that catches "forgot the decorator"
and "decorator drift" at PR time instead of in a pentest six months later.
See [Declarative Permissions Document](permissions-document.md) for the full
setup guide.

For the complete `djust_audit` command reference — all four modes, every
CLI flag, CI integration snippets, and exit-code conventions — see the
[`djust_audit` Command Guide](djust-audit.md). For the full list of every
check ID the command can emit (A0xx static, P0xx permissions, L0xx runtime,
plus the C0xx / S0xx / V0xx / T0xx / Q0xx codes from `manage.py check`),
see the [Error Code Reference](error-codes.md).

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

## CSP-Strict Defaults for Framework Code

djust is designed to work under stricter Content Security Policy
deployments — `script-src 'self'` (no `'unsafe-inline'`, no
`'unsafe-eval'`) — without configuration. v1.0 readiness positions
strict-CSP as a design constraint, not an opt-in.

### What this means in practice

Framework code that emits HTML defaults to:

- **External static JS modules** served from
  `python/djust/static/djust/` or
  `python/djust/components/static/djust_components/`. Every behavior
  the framework adds at the client lives in a versioned, addressable
  module. No `eval`, no `Function(string)`.
- **No inline event handlers** — no `onclick=`, `onchange=`,
  `oninput=` in emitted markup. Click/keyboard/input behavior comes
  from delegated listeners installed by the static modules.
- **Auto-bind via marker class or attribute** — when an element opts
  into a framework behavior, it carries a marker (CSS class OR HTML
  attribute) plus the data attributes the behavior reads. Two real
  patterns: CSS class — `tr.data-table-row-clickable` (PR #1170) — and
  attribute — `[dj-track-static]` (`39-dj-track-static.js`),
  `[dj-submit]` on forms (`09-event-binding.js`). The static JS module
  attaches a delegated listener on `document` (or root) and dispatches
  based on `event.target.closest('.<marker-class>')` or
  `event.target.closest('[<marker-attr>]')`. Compose with
  `MutationObserver` for morphdom-managed regions so the binding
  survives VDOM patches.
- **CSP nonce only when genuinely required** — the lazy-fill protocol
  (`<dj-lazy-slot>` activator scripts) is the canonical exception
  where an inline `<script>` is needed because the activator must run
  synchronously inline. When you need an inline script, propagate
  `request.csp_nonce` per
  `python/djust/templatetags/live_tags.py:live_render`'s `lazy=True`
  branch.

### Canonical reference modules

If you're adding a new framework feature that needs client-side
behavior, the cleanest examples to copy are:

- **`python/djust/components/static/djust_components/data-table-row-click.js`**
  + the `tr.data-table-row-clickable` marker class (PR #1170). External
  module + delegated listener + nested-control guard + keyboard
  activation. The "no inline script, ever" example.
- **`python/djust/static/djust/src/50-lazy-fill.js`** + the
  `<dj-lazy-slot>` custom element (PR #1138). The exception case where
  an inline activator script is unavoidable; demonstrates the
  `request.csp_nonce` propagation pattern.
- **`python/djust/static/djust/src/39-dj-track-static.js`** +
  Phoenix-style `phx-track-static` parity attribute. Existing canonical
  pattern for attribute-driven behavior dispatch.

### What CSP headers your deployment should set

For djust apps using only the canonical modules:

```
Content-Security-Policy:
  default-src 'self';
  script-src 'self';
  style-src 'self' 'unsafe-inline';
  img-src 'self' data:;
  connect-src 'self' wss://your-host;
```

For djust apps using the lazy-fill protocol:

```
Content-Security-Policy:
  default-src 'self';
  script-src 'self' 'nonce-<csp_nonce>';
  ...
```

Set `request.csp_nonce` via `django-csp` middleware (pip
`django-csp>=4.0`); djust's lazy-fill template tag reads
`getattr(request, 'csp_nonce', None)` automatically.

Canonicalized in v0.9.1 retro / Action Tracker #183 / GitHub #1175.

## Further Reading

- [Security Guidelines for Contributors](../SECURITY_GUIDELINES.md) — banned patterns, code review checklist, security testing
- [Event Handlers](../EVENT_HANDLERS.md) — `@event_handler` decorator reference
- [Multi-Tenant Security](multi-tenant.md) — tenant isolation patterns
