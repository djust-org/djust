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

| Mode       | Behavior                                             |
| ---------- | ---------------------------------------------------- |
| `"strict"` | (default) Only `@event_handler` decorated methods    |
| `"warn"`   | Allows undecorated methods with deprecation warnings |
| `"open"`   | No decorator check (legacy, not recommended)         |

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
    "max_form_fields": 1000,           # fields one form's submitted data may hold
    "max_form_data_size": 2_621_440,   # characters of keys + values (2.5 MB)
    "max_form_members": 100_000,       # keys, values and nested containers
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

`max_message_size` is the largest frame, in bytes, a client may send (`0` = no limit; keep a limit in production, since every frame is held in memory before it is parsed). The server announces it in its `connect` frame, and the client does not send a larger frame: it reports an error with code `message_too_large` (logged, and dispatched as a `djust:error` event whose message starts with `Message too large (N bytes)`) and settles the event's loading state. The server answers a larger frame that arrives anyway with the same error. Send files and other large payloads with [`UploadMixin`](../guides/uploads.md), which splits them into chunks under the limit, rather than raising the limit for every socket.

`max_form_fields`, `max_form_data_size` and `max_form_members` bound what a client may put into a form's submitted data over the socket, with Django's own `DATA_UPLOAD_MAX_NUMBER_FIELDS` and `DATA_UPLOAD_MAX_MEMORY_SIZE` as the defaults of the first two and 100,000 for `max_form_members` (`0` = no limit). Django applies those to an HTTP POST body, but an event is a JSON frame, and `max_message_size` bounds only one frame while a form's data is the sum of many: `FormMixin` keeps `form_data` for the life of the view, adds a client-named key per `validate_field` event and merges every key of a `submit_form` event, and binds all of it to the form on each one. At the default rate limit a client reaches a hundred thousand fields in about seventeen minutes, and each event after that copies and filters all of them. An event that would take the data past a limit is refused whole: nothing of it is stored, the log says so (never the keys), and the view reports it in `form_errors` (`FormMixin`, the admin form view) or in the step's `wizard_step_errors["__all__"]` (`WizardMixin`); a `params_form` bound to a `url_change` whose parameters exceed it is invalid, with the reason in `params_errors["__all__"]`. The fields a form declares are not the client's to add and do not count toward `max_form_fields` (a form with 1,200 fields can still be submitted); what the limit bounds is the keys a client adds beyond them. `max_form_data_size` counts every string, `bytes` and nested list or mapping a value holds, however deeply it is nested. `max_form_members` counts the members themselves (every key, every value, and every list, tuple, set or mapping inside them is one, however long a string is): characters do not bound how long an event takes, because each one hands all the stored data to the template engine, so 2.6 million empty strings fit under the size limit and made every later event take over a second (#3844). The member limit bounds an event's cost rather than removing it: at the cap an event costs about 70 ms of that pass for flat strings or numbers, and roughly 0.2 to 0.4 s for container-heavy data (a list or mapping weighs one member, the same as a leaf, but costs more to hand over; measured on a loaded machine). A wizard keeps one mapping per step: `max_form_fields` is a step's (a step is a form), but the characters and the members are the wizard's total, every step together, so four steps cannot keep four times the limit. `FormSetHelpersMixin.remove_row` and `remove_row()` judge the `-DELETE` key of a client-named row prefix like any other client-named key (only the formset's management keys and, for a row below `TOTAL_FORMS`, the real fields of its form (plus `DELETE`, `ORDER` and `id`, spelled in canonical ASCII digits) are the formset's own; any other key under its prefix, such as `fs-0-anything` or `fs-00-street`, counts toward `max_form_fields`). A field the form adds in its `__init__` counts as one of its fields: the names are read once per formset class from `formset_cls().empty_form.fields` (the unbound form, never the client's data); a form that cannot be built without arguments keeps only its declared fields exempt (#3861). The names are computed once per formset class per process, so a form whose fields depend on state that changes later keeps the first answer. Each field counts as two members (its key and its value), so the default of 100,000 members is 50,000 fields: raise `max_form_fields` above about 50,000 and raise `max_form_members` with it. A value set with `config.set()` or `config.update()` is checked like one in `LIVEVIEW_CONFIG`: anything but a non-negative integer is replaced by the default (and logged), never by "no limit". **Behaviour change:** a client that used to be able to send more than 1,000 undeclared fields over the socket (or more than 2.5 MB of data, or more than 100,000 members of it) is now refused, as it would be over HTTP; raise `max_form_fields` / `max_form_data_size` / `max_form_members` for a form that legitimately needs more, or set them to `0`. Keep all three limits in production: with `0`, `DEBUG=False` logs a warning at startup.

`max_connections_per_ip` counts the sockets one client address holds open on one server process. Behind a load balancer or reverse proxy, set `DJUST_TRUSTED_PROXY_COUNT` to the number of proxies in front of the app so the client address is read from `X-Forwarded-For`; until then every client behind the proxy shares the proxy's address, so with the default of 10 the eleventh socket on a process is refused with close code `4429`, whoever opened it. The refusal log says which limit applied and flags an untrusted `X-Forwarded-For`. Never trust `X-Forwarded-For` without a proxy that sets it. Size the limit for the most tabs one real user or office NAT opens.

After `max_warnings` violations, the connection is closed with code `4429`. A per-IP connection tracker rejects new connections from the same IP during the cooldown period. A handler decorated with `@rate_limit(..., on_exceed="drop")` drops its extra events without counting them as violations; the global per-message limit still applies.

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

djust's client applies these protections itself, at the sinks — there is no
`djustSecurity` global to call. (One was documented here until #2679, but
nothing loaded the file, so it was `undefined` in every browser.)

- DOM updates arrive as VDOM patches, not as `innerHTML` of a server string.
- Keys that could pollute a prototype are refused where untrusted keys are
  written — see `UNSAFE_KEYS` in `static/djust/src/00-namespace.js` and the
  guards in the event-parsing, form-data, draft-manager and VDOM-patch modules.

Writing your own inline JS against untrusted input? Use `textContent` over
`innerHTML`, and a `Map` or `Object.create(null)` over merging attacker-
controlled keys into an object literal.

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

djust's own log calls get this for free: at startup `DjustConfig.ready()` attaches
`DjustLogSanitizerFilter` to the `djust` logger and every `djust.*` logger,
including ones created later, so string arguments in framework log records are
sanitized before any handler sees them. Before 1.2.1 the filter sat on the
`djust` logger alone and never saw records from its child loggers
([#2947](https://github.com/djust-org/djust/issues/2947)). Your application's
loggers are not covered. Call `sanitize_for_log()` there, or add the filter to
your handlers in `LOGGING`:

```python
LOGGING = {
    "version": 1,
    "filters": {"djust_sanitize": {"()": "djust.security.DjustLogSanitizerFilter"}},
    "handlers": {"console": {"class": "logging.StreamHandler", "filters": ["djust_sanitize"]}},
    "root": {"handlers": ["console"]},
}
```

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

Cross-site WebSocket hijacking is blocked by an `Origin` check. Before accepting the handshake, the WebSocket consumer (and `DjustOriginValidator`, which `DjustMiddlewareStack` adds) closes the connection with code 4403 unless the `Origin` is:

- the socket's own origin: the same scheme (`https` for `wss`, or when `SECURE_PROXY_SSL_HEADER` matches), host and port as the handshake's `Host` header, with that host in `ALLOWED_HOSTS`; or
- listed in `DJUST_ALLOWED_ORIGINS` (full origins such as `"https://app.example.com"`, or `"https://*.example.com"` for subdomains). When that setting is unset, `CSRF_TRUSTED_ORIGINS` is used, the list Django's CSRF check already trusts.

Scheme and port count: a page on `http://localhost:9999` cannot open a socket to an app on `http://localhost:8001`. A handshake without an `Origin` header is refused too. Browsers always send one, so only non-browser clients (a native app, a script, a Channels `WebsocketCommunicator` in tests) are affected; set `DJUST_ALLOW_MISSING_ORIGIN = True` to accept them. The SSE event endpoints apply the same rule to a present `Origin`; they accept a missing one because a browser leaves it off a same-origin `GET` such as the `EventSource` request.

If you wrap the router in channels' `AllowedHostsOriginValidator` as well, note that it compares the host alone and always refuses a missing `Origin`, so `DJUST_ALLOW_MISSING_ORIGIN` has no effect behind it; use `djust.routing.DjustOriginValidator` instead. The connection carries the Django session cookie, and view-level authentication (`LoginRequiredMixin`, `login_required`, auth mixins) is enforced when the view mounts.

For any HTTP endpoints in your djust application, standard Django CSRF protection applies. Never use `@csrf_exempt` without documented justification.

A `{% csrf_token %}` form in a LiveView template works whether the page was rendered over HTTP or over the socket (for example after `dj-navigate`). When djust mounts a view over the WebSocket, it binds the browser's CSRF cookie to the rebuilt request the way `CsrfViewMiddleware` would, so the rendered token matches the cookie the browser will POST with. `CSRF_COOKIE_NAME` and `CSRF_USE_SESSIONS` are honoured. Because a socket-rendered page now carries a valid token, the WebSocket `Origin` check above is what keeps it from other sites; keep `ALLOWED_HOSTS` specific.

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

The WebSocket consumer checks `request.user` when the view mounts. If using `PresenceMixin`, the framework gracefully handles missing authentication middleware by checking `hasattr(request, "user")` before accessing `request.user`.

## Multi-Tenant Isolation

When building multi-tenant applications, follow these principles:

### Key Prefixing

All storage keys (cache, state backends, sessions) must include the tenant identifier:

```python
from djust.tenants.mixin import TenantScopedMixin

class TenantDocumentView(TenantScopedMixin, LiveView):
    model = Document

    def mount(self, request, **kwargs):
        # Filtered by tenant_field (default "tenant_id")
        self.documents = self.get_tenant_queryset()
```

`TenantScopedMixin` does not filter querysets automatically, and it does not override `get_queryset()`. A plain `Document.objects.all()` returns every tenant's rows. Always go through `get_tenant_queryset()`, `get_tenant_object(pk)` and `create_for_tenant(**fields)`.

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
| Handler return values | Return values of WebSocket event handlers are discarded — except handlers exposed with `@event_handler(expose_api=True)` and `@server_function` methods, whose return values are sent to the caller |

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
- **Component tags** (`djust.components`) emit no inline event handlers or
  inline scripts: behaviour such as opening a `rich_select` or a `popover`,
  dropping files on a `file_dropzone`, dragging `kanban_board` cards or
  switching `theme_toggle`'s mode runs from delegated listeners in the client
  bundle, and `code_block` highlights through a static script. The `Button`
  component's `onclick=` argument is the exception: it is an inline handler
  you write, and needs `'unsafe-inline'`. `{% colocated_hook %}` carries the request nonce and the
  client registers its body through a script element with that nonce, so it
  needs no `'unsafe-eval'`; without a nonce it falls back to `new Function`.
- **Third-party JS that djust doesn't emit** (Google Analytics, Stripe,
  etc.) is outside djust's scope. Add their domains or hashes to your
  CSP directly.
- **User-generated inline content** (e.g. rich-text editors that allow
  `style=` attributes) is application responsibility, not framework.

## Reporting Vulnerabilities

If you discover a security issue in djust:

1. Do **not** open a public GitHub issue.
2. Email `security@djust.org` with a description, reproduction steps, and potential impact.
3. We will respond within 48 hours.
