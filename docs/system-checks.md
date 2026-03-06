# djust System Checks Reference

Quick reference for all 37 djust system checks — IDs, severities, suppression patterns, and known false positive conditions.

Run checks with: `python manage.py check --deploy` or `python manage.py djust_check`

---

## Quick-Reference Table

| ID | Category | Severity | Summary |
|----|----------|----------|---------|
| C001 | Config | Error | ASGI_APPLICATION not set |
| C002 | Config | Error | CHANNEL_LAYERS not configured |
| C003 | Config | Warning/Info | daphne ordering in INSTALLED_APPS |
| C004 | Config | Error | 'djust' not in INSTALLED_APPS |
| C005 | Config | Warning | WebSocket routes missing AuthMiddlewareStack |
| C006 | Config | Warning | daphne without WhiteNoise (static files 404) |
| C010 | Config | Warning | Tailwind CDN in production templates |
| C011 | Config | Info/Warning | Missing compiled Tailwind output.css |
| C012 | Config | Warning | Manual client.js script tag in base template |
| V001 | LiveView | Warning | LiveView missing template_name attribute |
| V002 | LiveView | Info | LiveView missing mount() method |
| V003 | LiveView | Error | mount() has wrong signature |
| V004 | LiveView | Info | Public method looks like event handler but missing @event_handler |
| V005 | LiveView | Warning | Module not in LIVEVIEW_ALLOWED_MODULES |
| V006 | LiveView | Warning | Service instance assigned in mount() (AST) |
| V007 | LiveView | Warning | Event handler missing **kwargs |
| V008 | LiveView | Info | Non-primitive type assigned in mount() (AST) |
| S001 | Security | Error | mark_safe() with f-string (XSS risk) |
| S002 | Security | Warning | @csrf_exempt without justification comment |
| S003 | Security | Warning | Bare except: pass swallows all exceptions |
| S004 | Security | Warning | DEBUG=True with non-localhost ALLOWED_HOSTS |
| S005 | Security | Warning | LiveView exposes state without authentication |
| T001 | Templates | Warning | Deprecated @click/@input syntax |
| T002 | Templates | Info | LiveView template missing dj-root |
| T003 | Templates | Info | wrapper_template uses {% include %} instead of liveview_content |
| T004 | Templates | Warning | document.addEventListener for djust events (use window) |
| T005 | Templates | Warning | dj-view and dj-root on different elements |
| T010 | Templates | Warning | dj-click used for navigation instead of dj-patch |
| T011 | Templates | Warning | Unsupported Django template tags (silently ignored by Rust renderer) |
| T012 | Templates | Warning | Template with dj-* directives but no dj-view |
| T013 | Templates | Warning | dj-view with empty or dynamic value |
| T014 | Templates | Warning | Deprecated data-dj-id attribute |
| Q001 | Quality | Info | print() statement found |
| Q002 | Quality | Warning | f-string in logger call |
| Q003 | Quality | Info | console.log without djustDebug guard |
| Q007 | Quality | Warning | Overlapping static_assigns and temporary_assigns |
| Q010 | Quality | Info | Event handler sets nav state without patch() (heuristic) |

---

## Suppression Patterns

**Global suppression** (in `settings.py`):
```python
SILENCED_SYSTEM_CHECKS = ["djust.S005", "djust.V001"]
```

**Inline suppression** (Python — on the specific line):
```python
self.route_map = get_route_map_script()  # noqa: V008
```

**Inline suppression** (Django templates):
```html
{# noqa: T011 #}
```

**Inline suppression** (JavaScript):
```js
console.log("debug info"); // noqa: Q003
```

---

## Configuration Checks (C)

### C001 — ASGI_APPLICATION not set
- **Severity**: Error
- **Method**: Runtime (settings inspection)
- **What it detects**: `settings.ASGI_APPLICATION` is missing or None
- **Suppression**: `SILENCED_SYSTEM_CHECKS = ["djust.C001"]`
- **False positives**: None known; required for WebSocket support

### C002 — CHANNEL_LAYERS not configured
- **Severity**: Error
- **Method**: Runtime (settings inspection)
- **What it detects**: `settings.CHANNEL_LAYERS` is empty or missing
- **Suppression**: `SILENCED_SYSTEM_CHECKS = ["djust.C002"]`
- **False positives**: None known; required for live updates

### C003 — daphne ordering in INSTALLED_APPS
- **Severity**: Warning/Info
- **Method**: Runtime (settings inspection)
- **What it detects**: `daphne` is not listed before `django.contrib.staticfiles` in `INSTALLED_APPS`
- **Suppression**: `SILENCED_SYSTEM_CHECKS = ["djust.C003"]`
- **False positives**: Projects not using daphne as the ASGI server (e.g. uvicorn)

### C004 — 'djust' not in INSTALLED_APPS
- **Severity**: Error
- **Method**: Runtime (settings inspection)
- **What it detects**: `djust` is absent from `INSTALLED_APPS`
- **Suppression**: Cannot be suppressed meaningfully — fix the config
- **False positives**: None

### C005 — WebSocket routes missing AuthMiddlewareStack
- **Severity**: Warning
- **Method**: AST (routing config inspection)
- **What it detects**: WebSocket URL patterns not wrapped in `AuthMiddlewareStack`
- **Suppression**: `SILENCED_SYSTEM_CHECKS = ["djust.C005"]`
- **False positives**: Intentionally public WebSocket endpoints (e.g. anonymous chat)

### C006 — daphne without WhiteNoise
- **Severity**: Warning
- **Method**: Runtime (settings inspection)
- **What it detects**: daphne is in `INSTALLED_APPS` but WhiteNoise middleware is absent; static files will return 404 in production
- **Suppression**: `SILENCED_SYSTEM_CHECKS = ["djust.C006"]`
- **False positives**: Projects serving static files via a CDN or a reverse proxy such as nginx

### C010 — Tailwind CDN in production templates
- **Severity**: Warning
- **Method**: Regex (template file scan)
- **What it detects**: Tailwind CDN `<script>` tag in templates (slow; not suitable for production)
- **Suppression**: `SILENCED_SYSTEM_CHECKS = ["djust.C010"]`
- **False positives**: None; CDN use in production is always a concern

### C011 — Missing compiled Tailwind output.css
- **Severity**: Info/Warning
- **Method**: Filesystem check
- **What it detects**: `STATICFILES_DIRS` references a Tailwind `output.css` that does not exist on disk
- **Suppression**: `SILENCED_SYSTEM_CHECKS = ["djust.C011"]`
- **False positives**: CI environments before asset compilation; fresh checkouts before running the Tailwind build

### C012 — Manual client.js script tag
- **Severity**: Warning
- **Method**: Regex (template file scan)
- **What it detects**: Hardcoded `<script src="...client.js">` tag; djust injects this automatically via middleware
- **Suppression**: `SILENCED_SYSTEM_CHECKS = ["djust.C012"]`
- **False positives**: None; the manual tag is always redundant when djust middleware is active

---

## LiveView Checks (V)

### V001 — LiveView missing template_name
- **Severity**: Warning
- **Method**: Runtime (class inspection)
- **What it detects**: A LiveView subclass has no `template_name` attribute
- **Suppression**: `SILENCED_SYSTEM_CHECKS = ["djust.V001"]` or `# noqa: V001` on the class
- **False positives**: Abstract base classes that are never rendered directly

### V002 — LiveView missing mount() method
- **Severity**: Info
- **Method**: Runtime (class inspection)
- **What it detects**: A LiveView subclass does not define `mount()`
- **Suppression**: `SILENCED_SYSTEM_CHECKS = ["djust.V002"]`
- **False positives**: Views that require no state initialisation

### V003 — mount() has wrong signature
- **Severity**: Error
- **Method**: Runtime (introspection)
- **What it detects**: `mount()` signature does not include `request` as the second positional parameter; the valid signature is `mount(self, request, **kwargs)`
- **Suppression**: Fix the signature rather than suppress
- **False positives**: None

### V004 — Public method looks like event handler but missing @event_handler
- **Severity**: Info
- **Method**: AST (method name heuristic — `handle_*` prefix and similar)
- **What it detects**: Public methods whose names match the event-handler naming pattern but lack the `@event_handler` decorator
- **Suppression**: `SILENCED_SYSTEM_CHECKS = ["djust.V004"]` (global) or `# noqa: V004` on the method
- **False positives**: `handle_params()`, `handle_disconnect()`, `handle_connect()`, and `handle_event()` are djust lifecycle methods, not event handlers — the `handle_*` heuristic fires on them incorrectly

### V005 — Module not in LIVEVIEW_ALLOWED_MODULES
- **Severity**: Warning
- **Method**: Runtime (settings inspection)
- **What it detects**: A LiveView class is discovered in a module not listed in `LIVEVIEW_ALLOWED_MODULES`
- **Suppression**: Add the module to `LIVEVIEW_ALLOWED_MODULES`, or `SILENCED_SYSTEM_CHECKS = ["djust.V005"]`
- **False positives**: Newly created view modules not yet added to the allowlist

### V006 — Service instance assigned in mount() (AST)
- **Severity**: Warning
- **Method**: AST (inspects assignments in `mount()`)
- **What it detects**: `self.X = SomeService()` pattern — service objects are not JSON-serialisable and cannot survive WebSocket reconnects
- **Suppression**: `# noqa: V006` inline on the assignment
- **False positives**: Objects whose class name contains "Service", "Client", "Session", "API", or "Connection" but are actually lightweight and serialisable

### V007 — Event handler missing **kwargs
- **Severity**: Warning
- **Method**: AST (inspects `@event_handler` decorated methods)
- **What it detects**: An event handler method does not accept `**kwargs`, which causes a `TypeError` when djust passes extra keyword arguments
- **Suppression**: `# noqa: V007` inline or fix the signature
- **False positives**: None

### V008 — Non-primitive type assigned in mount() (AST)
- **Severity**: Info
- **Method**: AST (inspects the RHS expression of assignments in `mount()` — reads the call expression, **not** the return type annotation)
- **What it detects**: `self.X = some_function()` where the assigned value appears to be non-primitive (not a literal int/str/bool/None/list/dict)
- **Suppression**: `# noqa: V008` inline on the assignment
- **False positives**: `self.x = get_some_string()` triggers V008 even when the function returns a `str` — the AST check reads the function name, not its `-> str` return type annotation. Use `# noqa: V008` on such assignments.

---

## Security Checks (S)

### S001 — mark_safe() with f-string (XSS risk)
- **Severity**: Error
- **Method**: AST
- **What it detects**: `mark_safe(f"...")` — interpolated content bypasses Django's HTML escaping
- **Suppression**: Do not suppress; fix the code. Use `format_html()` or pre-escape variables with `escape()` then concatenate.
- **False positives**: None (the pattern is always dangerous)

### S002 — @csrf_exempt without justification comment
- **Severity**: Warning
- **Method**: AST
- **What it detects**: `@csrf_exempt` decorator without an adjacent comment explaining the justification
- **Suppression**: Add a comment, or `SILENCED_SYSTEM_CHECKS = ["djust.S002"]`
- **False positives**: Legitimate exemptions (webhooks, public APIs) that already have comments alongside the decorator

### S003 — Bare except: pass
- **Severity**: Warning
- **Method**: AST
- **What it detects**: `except: pass` — silently swallows all exceptions including `KeyboardInterrupt` and `SystemExit`
- **Suppression**: `# noqa: S003` inline or `SILENCED_SYSTEM_CHECKS = ["djust.S003"]`
- **False positives**: Intentional fire-and-forget patterns in non-critical paths

### S004 — DEBUG=True with non-localhost ALLOWED_HOSTS
- **Severity**: Warning
- **Method**: Runtime (settings inspection)
- **What it detects**: `DEBUG=True` and `ALLOWED_HOSTS` contains entries other than `localhost` / `127.0.0.1`
- **Suppression**: `SILENCED_SYSTEM_CHECKS = ["djust.S004"]`
- **False positives**: Local dev environments using custom hostnames (e.g. `myapp.local`)

### S005 — LiveView exposes state without authentication
- **Severity**: Warning
- **Method**: AST/Runtime (inspects LiveView for auth checks in `mount()`)
- **What it detects**: LiveView `mount()` does not check `request.user.is_authenticated`
- **Suppression**: `SILENCED_SYSTEM_CHECKS = ["djust.S005"]`
- **False positives**: Intentionally public views (anonymous landing pages, public dashboards)

---

## Template Checks (T)

### T001 — Deprecated @click/@input syntax
- **Severity**: Warning
- **Method**: Regex (template scan)
- **What it detects**: Old `@click="..."` / `@input="..."` attribute syntax, replaced by `dj-click` / `dj-input`
- **Suppression**: `SILENCED_SYSTEM_CHECKS = ["djust.T001"]`
- **False positives**: None; the old syntax is deprecated

### T002 — LiveView template missing dj-root
- **Severity**: Info
- **Method**: Regex (template scan)
- **What it detects**: A LiveView template has no element with `dj-root`
- **Suppression**: `SILENCED_SYSTEM_CHECKS = ["djust.T002"]`
- **False positives**: Partial templates and base templates that intentionally omit `dj-root` (djust can infer the root automatically)

### T003 — wrapper_template uses {% include %}
- **Severity**: Info
- **Method**: Regex (template scan)
- **What it detects**: A `wrapper_template` uses Django `{% include %}` instead of `{% liveview_content %}`
- **Suppression**: `SILENCED_SYSTEM_CHECKS = ["djust.T003"]`
- **False positives**: Base templates that use `{% include %}` for SVG sprites, modals, or other non-LiveView partials — the heuristic can fire incorrectly on these

### T004 — document.addEventListener for djust events (use window)
- **Severity**: Warning
- **Method**: Regex (template/JS scan)
- **What it detects**: `document.addEventListener("djust:..."` — djust events are dispatched on `window`, not `document`
- **Suppression**: `# noqa: T004` or `SILENCED_SYSTEM_CHECKS = ["djust.T004"]`
- **False positives**: None; djust events always bubble to `window`

### T005 — dj-view and dj-root on different elements
- **Severity**: Warning
- **Method**: Regex (template scan)
- **What it detects**: `dj-view` and `dj-root` appear in the same template but on different elements
- **Suppression**: `SILENCED_SYSTEM_CHECKS = ["djust.T005"]`
- **False positives**: Intentional split layouts (uncommon)

### T010 — dj-click used for navigation
- **Severity**: Warning
- **Method**: Regex/AST (heuristic: dj-click handlers that call redirect or navigate)
- **What it detects**: `dj-click` triggers navigation; prefer `dj-patch` for URL state updates
- **Suppression**: `SILENCED_SYSTEM_CHECKS = ["djust.T010"]`
- **False positives**: Click handlers that navigate as a side effect of other work (e.g. save-and-redirect)

### T011 — Unsupported Django template tags
- **Severity**: Warning
- **Method**: Regex (template scan)
- **What it detects**: Django template tags not yet implemented in the Rust renderer — these are **silently ignored** at render time, producing no output
- **Currently flagged tags**: `{% ifchanged %}`, `{% regroup %}`, `{% resetcycle %}`, `{% lorem %}`, `{% debug %}`, `{% filter %}`, `{% autoescape %}`
- **Suppression**: `SILENCED_SYSTEM_CHECKS = ["djust.T011"]` or `{# noqa: T011 #}` in the template
- **False positives**: Base templates processed by Django's Python renderer rather than the Rust renderer
- **Note**: `{% extends %}` and `{% block %}` are **fully supported** by the Rust renderer since template inheritance was implemented; T011 does not flag them

### T012 — Template with dj-* directives but no dj-view
- **Severity**: Warning
- **Method**: Regex (template scan)
- **What it detects**: Template uses `dj-*` attributes but has no `dj-view` attribute to bind to a LiveView
- **Suppression**: `SILENCED_SYSTEM_CHECKS = ["djust.T012"]`
- **False positives**: Partial templates that intentionally omit `dj-view` because the parent/wrapper template provides it

### T013 — dj-view with empty or dynamic value
- **Severity**: Warning
- **Method**: Regex (template scan)
- **What it detects**: `dj-view=""` (empty) or `dj-view="{{ ... }}"` (Django template variable) — a static dotted-path string is expected
- **Suppression**: `SILENCED_SYSTEM_CHECKS = ["djust.T013"]`
- **False positives**: Base templates that use `dj-view="{{ view_path }}"` to inject the view path from context are valid — suppress this check if that pattern is intentional

### T014 — Deprecated data-dj-id attribute
- **Severity**: Warning
- **Method**: Regex (template scan)
- **What it detects**: Old `data-dj-id` attribute syntax, replaced by `dj-id`
- **Suppression**: Fix the templates; or `SILENCED_SYSTEM_CHECKS = ["djust.T014"]`
- **False positives**: None; the old attribute name is deprecated

---

## Code Quality Checks (Q)

### Q001 — print() statement found
- **Severity**: Info
- **Method**: AST
- **What it detects**: `print(...)` calls in Python files; use the `logging` module instead
- **Suppression**: `# noqa: Q001` inline or `SILENCED_SYSTEM_CHECKS = ["djust.Q001"]`
- **False positives**: CLI management commands and scripts where `print()` output is intentional

### Q002 — f-string in logger call
- **Severity**: Warning
- **Method**: AST
- **What it detects**: `logger.info(f"...")` — prefer `%`-style formatting or positional args so the string is not evaluated when the log level is disabled
- **Suppression**: `# noqa: Q002` inline or `SILENCED_SYSTEM_CHECKS = ["djust.Q002"]`
- **False positives**: Performance is rarely an issue in practice; this is primarily a style concern

### Q003 — console.log without djustDebug guard
- **Severity**: Info
- **Method**: Regex (JS/template scan)
- **What it detects**: `console.log(...)` not wrapped in a `djustDebug` guard
- **Correct guard**: `if (globalThis.djustDebug) { console.log(...); }`
- **Suppression**: `// noqa: Q003` inline or `SILENCED_SYSTEM_CHECKS = ["djust.Q003"]`
- **False positives**: Intentional debug logging that you intend to remove before merging

### Q007 — Overlapping static_assigns and temporary_assigns
- **Severity**: Warning
- **Method**: Runtime (class inspection)
- **What it detects**: A key appears in both `static_assigns` and `temporary_assigns` on the same LiveView
- **Suppression**: Fix the overlap; or `SILENCED_SYSTEM_CHECKS = ["djust.Q007"]`
- **False positives**: None; overlapping keys indicate a logic error

### Q010 — Event handler sets nav state without patch()
- **Severity**: Info
- **Method**: AST (low-confidence heuristic)
- **What it detects**: An event handler assigns `self.<name>` where `<name>` looks like navigation state (tab, view, page, step, etc.) without calling `self.patch()` to update the URL
- **Suppression**: `SILENCED_SYSTEM_CHECKS = ["djust.Q010"]` or `# noqa: Q010` inline
- **False positives**: Low-confidence heuristic — fires on any handler that sets state variables with navigation-sounding names, even when those variables are not URL parameters

---

## Suppression Examples

### Silence a single check globally

```python
# settings.py
SILENCED_SYSTEM_CHECKS = [
    "djust.S005",  # Intentionally public view — no auth required
    "djust.V001",  # Abstract base class, never rendered directly
    "djust.T012",  # Partial templates intentionally omit dj-view
]
```

### Suppress one specific line (Python)

```python
def mount(self, request, **kwargs):
    self.route_map = get_route_map_script()  # noqa: V008  (returns str)
    self.client = MyLightweightClient()      # noqa: V006  (not a service)
```

### Suppress in a Django template

```html
{% load my_custom_tags %}
{# noqa: T011 #}
```

### Suppress in JavaScript

```js
// noqa: Q003
console.log("permanent debug output intentional here");
```
