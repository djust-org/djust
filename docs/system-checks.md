# djust System Checks Reference

Quick reference for all 42 djust system checks — IDs, severities, suppression patterns, and known false positive conditions.

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
| C010 | Config | Warning | Tailwind CDN in production templates |
| C011 | Config | Info/Warning | Missing compiled Tailwind output.css |
| C012 | Config | Warning | Manual client.js script tag in base template |
| V001 | LiveView | Warning | LiveView missing template_name attribute |
| V002 | LiveView | Info | LiveView missing mount() method |
| V003 | LiveView | Error | mount() has wrong signature |
| V004 | LiveView | Info | Public method looks like event handler but missing @event_handler |
| V005 | LiveView | Warning | Module not in LIVEVIEW_ALLOWED_MODULES |
| V006 | LiveView | Warning | Service instance assigned in mount() — high-confidence subset of V008 |
| V007 | LiveView | Warning | Event handler missing **kwargs |
| V008 | LiveView | Info | Non-primitive type assigned in mount() — broader, lower-confidence (skips V006 patterns) |
| V012 | LiveView | Warning | Sticky child template declares its own dj-view (nested duplicate binding) |
| V013 | LiveView | Warning | HTTP-only dispatch()/get()/post() override never runs on a WebSocket mount |
| S001 | Security | Error | mark_safe() with f-string (XSS risk) |
| S002 | Security | Warning | @csrf_exempt without justification comment |
| S003 | Security | Warning | Bare except: pass swallows all exceptions |
| S004 | Security | Warning | DEBUG=True with non-localhost ALLOWED_HOSTS |
| S005 | Security | Warning | LiveView exposes state without authentication |
| S007 | Security | Warning | `client_name\|safe` renders an unsanitised upload filename (stored XSS) |
| S009 | Security | Warning | View-auth'd LiveView exposes a public `@event_handler` with no per-handler gate |
| S011 | Security | Warning | Inline executable `<script>` inside a `dj-root` with no CSP configured (#1848) |
| S012 | Security | Error | LiveView gates auth via `@method_decorator(..., name="dispatch")` or an overridden `dispatch()` — not enforced over WebSocket (#14; reallocated from a duplicate S004, #2070) |
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
| T015 | Templates | Warning | Legacy data-djust-root / data-djust-view root attributes |
| T017 | Templates | Warning | dj-view / dj-root on a table-section element (foster-parented to silent garbage) |
| Q001 | Quality | Info | print() statement found |
| Q002 | Quality | Warning | f-string in logger call |
| Q003 | Quality | Info | console.log without djustDebug guard |
| Q007 | Quality | Warning | Overlapping static_assigns and temporary_assigns |
| Q010 | Quality | Info | Event handler sets nav state without patch() (heuristic) |
| Y001 | Accessibility | Warning | Interactive element (icon-only `<button>`/`<a>`) missing an accessible name |
| Y002 | Accessibility | Warning | `<img>` tag missing an `alt` attribute (WCAG 1.1.1) |
| Y003 | Accessibility | Warning | Form control (`<input>`/`<select>`/`<textarea>`) with no associated label (WCAG 1.3.1 / 3.3.2) |
| Y004 | Accessibility | Warning | Positive `tabindex` value — a focus-order anti-pattern (WCAG 2.4.3) |

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

### Abstract base LiveView classes

A common pattern is to define an abstract base view that subclasses extend for shared boilerplate — common mount logic, authorisation rules, helper methods. The base typically has no `template_name` and is never mounted directly. Mark such classes with `abstract = True`:

```python
from djust import LiveView

class BaseLiveView(LiveView):
    """Abstract base — provides shared mount + auth boilerplate."""
    abstract = True   # skip V001 / V005 / V002 / V003 / V004 / V007 / Q007
    login_required = True

    def mount(self, request, **kwargs):
        # Shared mount steps for all child views
        ...

class DashboardView(BaseLiveView):
    template_name = "dashboard.html"
    # Concrete view — V001/V005 still apply normally
```

Semantics (mirrors Django's `Meta.abstract` for models):
- `abstract = True` skips the per-class V/Q system checks for that class only.
- The marker is **not** inherited. Subclasses are validated as concrete unless they also redeclare `abstract = True`.
- Setting `abstract = False` explicitly is the same as not setting it — the class is concrete.

Added in v1.0.0 (#1605). The older mechanism (`SILENCED_SYSTEM_CHECKS` / `DJUST_CONFIG['suppress_checks']`) still works and is the right choice if you want to suppress a check **globally** across all classes rather than mark a specific class as abstract.

### V001 — LiveView missing template_name
- **Severity**: Warning
- **Method**: Runtime (class inspection)
- **What it detects**: A LiveView subclass has no `template_name` attribute
- **Suppression** (any of):
  - `abstract = True` class attribute on the LiveView subclass — preferred for abstract base classes (skips all per-class V/Q checks for that class only; not inherited). See [Abstract base LiveView classes](#abstract-base-liveview-classes) below.
  - `DJUST_CONFIG = {"suppress_checks": ["V001"]}` — global, matches the C003-style mechanism (fixed in #1604)
  - `SILENCED_SYSTEM_CHECKS = ["djust.V001"]` — Django's own global suppression
  - `# noqa: V001` on the class — per-class inline
- **False positives**: Abstract base classes that are never rendered directly — use `abstract = True` (the per-class opt-out is intent-revealing and doesn't require modifying settings)

### V002 — LiveView missing mount() method
- **Severity**: Info
- **Method**: Runtime (class inspection)
- **What it detects**: A LiveView subclass does not define `mount()`
- **Suppression** (any of):
  - `abstract = True` class attribute on the LiveView subclass (skips all per-class checks)
  - `DJUST_CONFIG = {"suppress_checks": ["V002"]}` — global (fixed in #1607)
  - `SILENCED_SYSTEM_CHECKS = ["djust.V002"]`
- **False positives**: Views that require no state initialisation

### V003 — mount() has wrong signature
- **Severity**: Error
- **Method**: Runtime (introspection)
- **What it detects**: `mount()` signature does not include `request` as the second positional parameter; the valid signature is `mount(self, request, **kwargs)`
- **Suppression** (any of):
  - Fix the signature (the real fix)
  - `abstract = True` class attribute on an abstract base
  - `DJUST_CONFIG = {"suppress_checks": ["V003"]}` — global (fixed in #1607)
  - `SILENCED_SYSTEM_CHECKS = ["djust.V003"]`
- **False positives**: None

### V004 — Public method looks like event handler but missing @event_handler
- **Severity**: Info
- **Method**: AST (method name heuristic — `handle_*` prefix and similar)
- **What it detects**: Public methods whose names match the event-handler naming pattern but lack the `@event_handler` decorator
- **Suppression** (any of):
  - `abstract = True` class attribute on an abstract base
  - `DJUST_CONFIG = {"suppress_checks": ["V004"]}` — global (fixed in #1607)
  - `SILENCED_SYSTEM_CHECKS = ["djust.V004"]`
  - `# noqa: V004` on the method
- **False positives**: `handle_params()`, `handle_disconnect()`, `handle_connect()`, and `handle_event()` are djust lifecycle methods, not event handlers — the `handle_*` heuristic fires on them incorrectly

### V005 — Module not in LIVEVIEW_ALLOWED_MODULES
- **Severity**: Warning
- **Method**: Runtime (settings inspection)
- **What it detects**: A LiveView class is discovered in a module not listed in `LIVEVIEW_ALLOWED_MODULES`
- **Suppression** (any of):
  - Add the module to `LIVEVIEW_ALLOWED_MODULES` (the real fix)
  - `abstract = True` class attribute on an abstract base — see [Abstract base LiveView classes](#abstract-base-liveview-classes) below
  - `DJUST_CONFIG = {"suppress_checks": ["V005"]}` — global (fixed in #1604)
  - `SILENCED_SYSTEM_CHECKS = ["djust.V005"]`
- **False positives**: Newly created view modules not yet added to the allowlist; abstract base classes that are never mounted directly

### V006 — Service instance assigned in mount() (AST)
- **Severity**: Warning
- **Method**: AST (inspects assignments in `mount()`)
- **What it detects**: `self.X = SomeService()` pattern — service objects are not JSON-serialisable and cannot survive WebSocket reconnects. Matches class names containing "Service", "Client", "Session", "API", or "Connection" (case-insensitive)
- **Relationship to V008**: V006 is the high-confidence subset of V008. Both detect non-serialisable state in `mount()`, but V006 fires at **Warning** level for well-known service patterns while V008 fires at **Info** level for everything else. They are deliberately non-overlapping — V008 skips any assignment already caught by V006
- **Suppression**: `# noqa: V006` inline on the assignment
- **False positives**: Objects whose class name contains "Service", "Client", "Session", "API", or "Connection" but are actually lightweight and serialisable

### V007 — Event handler missing **kwargs
- **Severity**: Warning
- **Method**: AST (inspects `@event_handler` decorated methods)
- **What it detects**: An event handler method does not accept `**kwargs`, which causes a `TypeError` when djust passes extra keyword arguments
- **Suppression** (any of):
  - Fix the signature (the real fix)
  - `abstract = True` class attribute on an abstract base
  - `DJUST_CONFIG = {"suppress_checks": ["V007"]}` — global (fixed in #1607)
  - `SILENCED_SYSTEM_CHECKS = ["djust.V007"]`
  - `# noqa: V007` inline
- **False positives**: None

### V008 — Non-primitive type assigned in mount() (AST)
- **Severity**: Info
- **Method**: AST (inspects the RHS expression of assignments in `mount()`)
- **What it detects**: `self.X = some_function()` where the assigned value appears to be non-primitive (not a literal int/str/bool/None/list/dict) and is not already caught by V006
- **Relationship to V006**: V008 is the broader, lower-confidence counterpart to V006. V006 covers high-confidence service patterns at **Warning** level; V008 catches all remaining non-primitive assignments at **Info** level. V008 explicitly skips assignments matching V006's keyword patterns to avoid duplicate messages
- **Suppression**: `# noqa: V008` inline on the assignment
- **False positives**: Functions with primitive return-type annotations (e.g. `-> str`, `-> int`) are excluded since PR #398. Other functions returning serialisable types (e.g. dataclasses) may still trigger V008 — suppress with `# noqa: V008`

### V012 — Sticky child declares its own `dj-view`
- **Severity**: Warning
- **Method**: Runtime (walks `LiveView` subclasses with `sticky = True`, scans each one's template root)
- **What it detects**: A sticky-child view (`sticky = True`, embedded via `{% live_render ... sticky=True %}`) whose own template root carries a `dj-view` attribute. The `live_render` wrapper already emits `<div dj-view dj-sticky-view="<id>" ...>`, so the child's own `dj-view` nests a **duplicate** binding inside the wrapper — the child's client-side mount breaks and its events silently don't bind.
- **Why it's subtle**: normal page views *require* `dj-view="<path>"` on their root to be mountable, so authors (and code-generating agents) reasonably add it everywhere — including sticky children, where it's wrong.
- **Fix**: remove `dj-view` from the sticky child's root element; the wrapper provides it. See the [sticky LiveViews guide](website/guides/sticky-liveviews.md#v012-system-check).
- **False positives**: none on normal page views — only `sticky = True` views are inspected. A `dj-view` appearing only inside a `{% comment %}` block (e.g. documenting the wrapper) is ignored (comments are stripped before scanning).
- **Suppression**: `DJUST_CONFIG = {'suppress_checks': ['V012']}`
- Added in v1.0.5-3 (#1803)

### V013 — HTTP-only dispatch()/get()/post() override never runs on a WebSocket mount
- **Severity**: Warning
- **Method**: Runtime (walks every registered `LiveView`'s `__mro__`)
- **What it detects**: An ancestor class in a `LiveView`'s MRO — the view class itself, or an earlier mixin — defines `dispatch()`, `get()`, or `post()` in its own `__dict__`. The WebSocket mount path calls `view_instance.mount(request, **kwargs)` directly; it never calls `dispatch()`/`get()`/`post()`. Any setup logic hooked there (tenant resolution, rate limiting, custom auth, request-scoped state) silently never runs for a WS-mounted view. Downstream symptom pattern: `self._tenant = None` in handlers, empty querysets, writes that no-op.
- **Fix**: move the setup logic into `mount(self, request, **kwargs)` so it runs on every transport. For auth/tenant-family logic specifically, see `djust.auth.core.run_pre_mount_auth` — the canonical pre-mount hook every live mount path (WebSocket, SSE, runtime) already calls.
- **False positives**: none expected on ordinary user mixins. Ancestors in `django.*` (e.g. `django.views.generic.base.View`, and `django.contrib.auth.mixins.{AccessMixin,LoginRequiredMixin,PermissionRequiredMixin,UserPassesTestMixin}` — already enforced on the WS/SSE mount path via `isinstance()` in `djust.auth.core._check_django_access_mixins`) and `djust.*` (djust's own mixins, e.g. `djust.tenants.mixin.TenantMixin`, which is independently reconciled with the WS path via `run_pre_mount_auth`'s `_ensure_tenant()` hook) are excluded.
- **Suppression**: `abstract = True` on the LiveView class, `DJUST_CONFIG = {'suppress_checks': ['V013']}`, or `SILENCED_SYSTEM_CHECKS = ["djust.V013"]`
- Added in v1.1.0 (#2059)

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
- **Note (#2070)**: Before this fix, `S004` was ALSO used by a second,
  unrelated check ("LiveView gates auth via dispatch()", see `S012`
  below) — suppressing `djust.S004` silently suppressed both. That check
  has been reallocated to `djust.S012`; `S004` now refers exclusively to
  this DEBUG/ALLOWED_HOSTS check.

### S005 — LiveView exposes state without authentication
- **Severity**: Warning
- **Method**: AST/Runtime (inspects LiveView for auth checks in `mount()`)
- **What it detects**: LiveView `mount()` does not check `request.user.is_authenticated`
- **Suppression**: `SILENCED_SYSTEM_CHECKS = ["djust.S005"]`
- **False positives**: Intentionally public views (anonymous landing pages, public dashboards)

### S007 — `client_name|safe` renders an unsanitised upload filename
- **Severity**: Warning
- **Method**: Template scan (regex over template files)
- **What it detects**: `{{ <expr>.client_name|safe }}` (whitespace around `|` is
  tolerated). An upload entry's `client_name` is the attacker-controlled
  original filename, stored without sanitisation. `|safe` disables Django's
  auto-escaping, so a `<script>`-bearing filename renders as live HTML —
  a stored-XSS vector.
- **Fix**: Remove `|safe` (auto-escaping is the safe default), or sanitise the
  value first with `django.utils.html.escape()`.
- **Suppression**: `DJUST_CONFIG = {'suppress_checks': ['S007']}` (or
  `'djust.S007'`) — only when the rendered value is pre-sanitised.
- **False positives**: A `client_name` value that has already been sanitised
  server-side before reaching the template.

### S009 — Event handler needs per-handler authorization
- **Severity**: Warning
- **Method**: AST (LiveView class + decorator walk)
- **What it detects**: A LiveView that declares **view-level** authorization
  (a truthy `login_required` / `permission_required` class attribute, a
  `check_permissions()` override, a Django/djust `AccessMixin`-family base such
  as `LoginRequiredMixin` / `PermissionRequiredMixin`, or an auth-gated
  `dispatch`) **and** exposes a **public** `@event_handler` / `@action` method
  with **no per-handler gate** (`@permission_required`) and no class-level
  `check_handler_permission()` override. View-level auth only runs at mount; a
  user who passes it can call any public handler. If a sensitive handler needs
  finer authorization, the mount gate alone is not enough.
- **Fix**: Add `@permission_required("app.perm")` above `@event_handler` on the
  sensitive handler, add a `check_handler_permission()` override that inspects
  the event, or rename the method with a leading `_` if it is not a
  client-callable handler.
- **Suppression**: `# noqa: S009` on the handler (its `def` or its decorator
  line), or `DJUST_CONFIG = {'suppress_checks': ['S009']}` when the view-level
  auth is sufficient for every handler.
- **False positives**: Conservative by design — private (`_`-prefixed) handlers
  and read-only-looking handlers (`load_` / `get_` / `list_` / `search_` / …)
  are exempt, and a falsy `login_required = False` does not count as view auth.

### S011 — Inline `<script>` inside a `dj-root` without a CSP
- **Severity**: Warning
- **Method**: Template scan (regex + dj-root subtree balancing)
- **What it detects**: An inline executable `<script>` placed **inside a real
  `dj-root` / `dj-view` subtree** when no Content-Security-Policy is configured
  (no django-csp middleware, no `CONTENT_SECURITY_POLICY` / `CSP_*` /
  `SECURE_CSP*` setting). This is the #1848 class: morphdom does **not**
  re-execute `<script>` tags it inserts/re-creates, so an inline script inside
  the dj-root silently never runs after the WS-mount morph — and a strict CSP
  would block it regardless.
- **Fix**: Move page JS into a static module (served from `static/`, registered
  on `DOMContentLoaded` + a `MutationObserver` for morph-managed regions), or
  into a base-template block rendered **after** the dj-root `</div>` (e.g.
  `{% block extra_scripts %}`). If the inline script is intentional, add a CSP
  nonce (`nonce="{{ request.csp_nonce }}"` with django-csp) or place it outside
  the dj-root.
- **Suppression**: `{# noqa: S011 #}` on the script line, or
  `DJUST_CONFIG = {'suppress_checks': ['S011']}`.
- **False positives**: Low by construction — external `<script src>`,
  nonce-bearing scripts, and data blocks (`type="application/json"` /
  `"text/template"` / …) are skipped, `<pre>`/`<code>` example markup is
  ignored, and a script **after** the dj-root closes (the recommended pattern)
  is not flagged.

> **Note**: `S010` (rate-limit-presence) is intentionally not shipped as a
> default-on check — it is advisory/opt-in only (high false-positive risk).

### S012 — LiveView gates auth via dispatch() (reallocated from S004, #2070)
- **Severity**: Error
- **Method**: AST (LiveView class + decorator/method walk)
- **What it detects**: A LiveView subclass whose authorization is applied via
  `@method_decorator(<auth>, name="dispatch")`, or via an overridden
  `dispatch()` method that performs auth itself (e.g.
  `if not request.user.is_authenticated: raise PermissionDenied()`). The
  WS/SSE mount path authorizes through `check_view_auth` and never calls
  `dispatch()`, so either pattern is enforced on the initial HTTP GET but
  silently bypassed over WebSocket (finding #14). Django auth **mixins**
  (`LoginRequiredMixin` / `PermissionRequiredMixin` / `UserPassesTestMixin`)
  are auto-honored by `check_view_auth` and do NOT trigger this check —
  only the decorated/overridden-`dispatch` forms are un-portable.
- **Fix**: Replace the `@method_decorator(..., name="dispatch")` (or the
  dispatch()-level auth) with djust's `login_required = True` /
  `permission_required = ...` class attributes, a `check_permissions(self,
  request)` method (honored on every transport), or subclass a Django auth
  mixin.
- **Suppression**: `# noqa: S012` on the decorator or the `def dispatch`
  line (this check, like `S001`-`S003` in the same module, is AST/inline-noqa
  only — it does not consult `DJUST_CONFIG['suppress_checks']`), or
  `SILENCED_SYSTEM_CHECKS = ["djust.S012"]` in settings (honored by
  `python manage.py check`; note `python manage.py djust_check` does not
  currently filter on Django's `is_silenced()`).
- **False positives**: A plain Django `View` (not a `LiveView` subclass)
  with a decorated `dispatch()` is correct HTTP-only usage and is not
  flagged; a `dispatch()` override that does no auth work is not flagged.
- **Migration note**: This check was originally shipped as `djust.S004`
  (PR #154, finding #14) and collided with the pre-existing `djust.S004`
  ("DEBUG=True with non-localhost ALLOWED_HOSTS", configuration.py) —
  suppressing one silently suppressed both. #2070 reallocated this check to
  `S012`; `S004` now refers ONLY to the DEBUG/ALLOWED_HOSTS check. If you
  suppressed `djust.S004` (via `# noqa: S004` or `SILENCED_SYSTEM_CHECKS`)
  specifically to silence the dispatch-auth warning, update the suppression
  to `djust.S012`.

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
- **What it detects**: `document.addEventListener("djust:..."` for a djust event that is dispatched on `window` (e.g. `djust:push_event`, `djust:before-navigate`, `djust:error`, `djust:shell-swapped`, `djust:vdom-cache-applied`, `djust:upload:*`) — those listeners belong on `window`, not `document`
- **Exempt (#1809)**: The djust events that the client dispatches on `document` are **not** flagged — `djust:navigate-start`, `djust:navigate-end`, `djust:hvr-applied`, `djust:layout-changed`, `djust:ws-reconnected`, `djust:time-travel-state`, `djust:time-travel-event`. Listening for these on `document` is correct.
- **Suppression**: `SILENCED_SYSTEM_CHECKS = ["djust.T004"]` or `DJUST_CONFIG = {"suppress_checks": ["T004"]}` (the `suppress_checks` form was a no-op before #1809)
- **False positives**: None for the window-dispatched events; the document-dispatched family above is exempt as of #1809

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

### T015 — Legacy data-djust-root / data-djust-view root attributes
- **Severity**: Warning
- **Method**: Regex (template scan)
- **What it detects**: The pre-1.0 root attributes `data-djust-root` and
  `data-djust-view`, renamed in djust 1.0 to `dj-root` / `dj-view` (the
  `data-` prefix is no longer required). The generic T012 ("dj-* directives
  but no dj-view") doesn't recognise that a view IS declared when it uses the
  deprecated spelling, so the path from symptom (the LiveView never connects
  over WebSocket) to fix is non-obvious — T015 names the rename explicitly.
- **Suppression**: Fix the templates (`data-djust-view` → `dj-view`,
  `data-djust-root` → `dj-root`); or
  `DJUST_CONFIG = {"suppress_checks": ["T015"]}`
- **False positives**: None; the match is scoped to exactly `data-djust-root`
  / `data-djust-view`, so other `data-djust-*` attributes
  (`data-djust-embedded`, `data-djust-activity`, `data-djust-view-model`, …)
  are never flagged.
- **Scope**: Static check only — djust 1.0's runtime does not accept the
  legacy attributes; the template must be migrated to the `dj-` spelling.

### T017 — dj-view / dj-root on a table-section element
- **Severity**: Warning
- **Method**: Regex (template scan)
- **What it detects**: A `dj-view` or `dj-root` attribute placed on an HTML
  table-section element (`<tbody>`, `<thead>`, `<tfoot>`, `<tr>`, `<td>`,
  `<th>`, `<caption>`, `<col>`, `<colgroup>`). Such a view renders to **silent
  garbage**: html5ever foster-parents the table elements out of the tree at
  render time, so `<tbody dj-view="…">{% for %}<tr>…{% endfor %}</tbody>`
  renders as `<html><head></head><body>text</body></html>` (all rows dropped)
  with **no error** (#1837).
- **Fix**: Put `dj-view` / `dj-root` on a wrapping element (the `<table>` or a
  surrounding `<div>`); a table-section element is foster-parented at render
  time and cannot be a standalone parse root.
- **Suppression**: Fix the templates; or
  `DJUST_CONFIG = {"suppress_checks": ["T017"]}`
- **False positives**: None expected; the match is scoped to the same tag
  (`[^>]*?` stops at the table-section tag's own `>`), so a `<div dj-view>`
  wrapping a `<table><tbody>` is never flagged, and a word-boundary on the tag
  name rejects `<trx` / `<tablefoo`. `<table dj-view>` (the recommended wrap
  target) is intentionally not flagged.
- **Scope**: Static check only — it does not change html5ever's HTML5-spec
  foster-parenting; it warns at startup so the silent failure is caught before
  a request hits.

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
- **Suppression** (any of):
  - Fix the overlap (the real fix; this almost always indicates a logic error)
  - `abstract = True` class attribute on an abstract base
  - `DJUST_CONFIG = {"suppress_checks": ["Q007"]}` — global (fixed in #1607)
  - `SILENCED_SYSTEM_CHECKS = ["djust.Q007"]`
- **False positives**: None; overlapping keys indicate a logic error

### Q010 — Event handler sets nav state without patch()
- **Severity**: Info
- **Method**: AST (low-confidence heuristic)
- **What it detects**: An event handler assigns `self.<name>` where `<name>` looks like navigation state (tab, view, page, step, etc.) without calling `self.patch()` to update the URL
- **Suppression**: `SILENCED_SYSTEM_CHECKS = ["djust.Q010"]` or `# noqa: Q010` inline
- **False positives**: Low-confidence heuristic — fires on any handler that sets state variables with navigation-sounding names, even when those variables are not URL parameters

---

## Accessibility Checks (Y)

The `Y` category (mnemonic: a11**Y**) scans project template files for
ARIA/WCAG accessibility defects. The regex heuristics are deliberately
conservative so they carry near-zero false positives; the category is
extensible — each check is a single-function-body addition. Templates that show
literal HTML inside `{% verbatim %}` blocks are skipped to avoid false
positives. See the [Accessibility guide](website/guides/accessibility.md) for
the full narrative.

### Y001 — interactive element missing an accessible name
- **Severity**: Warning
- **Method**: Regex (template file scan)
- **What it detects**: An interactive `<button>` or `<a href>` whose visible content is icon-only (an HTML entity, `<svg>`, or an `<i>`/`<span>` icon wrapper) and which has no `aria-label`, `aria-labelledby`, or `title` — a screen-reader user hears nothing for such a control
- **Suppression**: `DJUST_CONFIG['suppress_checks'] = ['Y001']` or `SILENCED_SYSTEM_CHECKS = ["djust.Y001"]`
- **False positives**: Near zero — inner content containing `{{ variable }}` / `{% tag %}` is conservatively treated as "may resolve to a label" and not flagged; a bare `<a>` without `href` is treated as an anchor target, not a control

### Y002 — `<img>` missing an `alt` attribute
- **Severity**: Warning
- **Method**: Regex (template file scan)
- **What it detects**: An `<img>` tag with no `alt` attribute at all (WCAG 1.1.1, Level A)
- **Suppression**: `DJUST_CONFIG['suppress_checks'] = ['Y002']` or `SILENCED_SYSTEM_CHECKS = ["djust.Y002"]`
- **False positives**: Near zero — `alt=""` (the WCAG-correct marker for a decorative image) is not flagged, and an `<img>` whose attributes are injected dynamically (`{% ... %}` / `{{ ... }}`) is treated as "alt may be present" and not flagged

### Y003 — form control missing an associated label
- **Severity**: Warning
- **Method**: Regex (template file scan)
- **What it detects**: An `<input>`, `<select>`, or `<textarea>` form control with no associated label (WCAG 1.3.1 / 3.3.2, Level A). A control is considered labelled if it has a `<label for>` referencing its `id`, a wrapping `<label>`, an `aria-label`, or an `aria-labelledby` — a screen-reader user cannot identify an unlabelled field
- **Suppression**: `DJUST_CONFIG['suppress_checks'] = ['Y003']` or `SILENCED_SYSTEM_CHECKS = ["djust.Y003"]`
- **False positives**: Near zero — hidden/submit/button/reset/image `<input>` types are skipped (they need no label); a control whose attributes are injected dynamically (`{% ... %}` / `{{ ... }}`) is treated conservatively as "label may be present" and not flagged; `data-type` attributes are not mistaken for the input `type`

### Y004 — positive `tabindex` value
- **Severity**: Warning
- **Method**: Regex (template file scan)
- **What it detects**: A `tabindex` attribute with a positive value (`tabindex="1"`+), which overrides the natural DOM focus order (WCAG 2.4.3, Level A). `tabindex="0"` (focusable in natural order) and `tabindex="-1"` (focusable only programmatically) are valid and not flagged
- **Suppression**: `DJUST_CONFIG['suppress_checks'] = ['Y004']` or `SILENCED_SYSTEM_CHECKS = ["djust.Y004"]`
- **False positives**: Near zero — `tabindex="0"` / `tabindex="-1"` are valid and not flagged; an interpolated value (`tabindex="{{ ... }}"` / `{% ... %}`) is treated conservatively and not flagged; a `data-tabindex` attribute is not mistaken for `tabindex`

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
