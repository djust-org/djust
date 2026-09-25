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
| C013 | Config | Warning | Stale collectstatic copy of client.min.js |
| C014 | Config | Warning | Multi-tenant ASGI without TENANT_LIMIT_SET_CALLS |
| C015 | Config | Error | Unknown adapter name in DJUST_CONFIG['extensions'] |
| C016 | Config | Warning | DjangoTemplates listed before DjustTemplateBackend, no DjangoTemplates fallback for admin, or a djust-first entry lacking the admin's context processors |
| C018 | Config | Warning | Deprecated LIVEVIEW_CONFIG key set that djust never reads (removed in 1.3) |
| C019 | Config | Warning | Unknown DJUST_CONFIG['PRESENCE_BACKEND'] value (presence falls back to in-process memory) |
| C020 | Config | Error | `DJUST_SERVER_STATE_MAX_AGE` is not an integer from 1 to 86400 |
| C021 | Config | Error | `LIVEVIEW_CONFIG['event_parameter_policy']` is not `'legacy'` or `'strict'` (ADR-036) |
| V001 | LiveView | Warning | LiveView missing template_name attribute |
| V002 | LiveView | Info | LiveView missing mount() method |
| V003 | LiveView | Error | mount() has wrong signature |
| V004 | LiveView | Info | Public method looks like event handler but missing @event_handler |
| V005 | LiveView | Warning | Module not in LIVEVIEW_ALLOWED_MODULES |
| V006 | LiveView | Warning | Service instance assigned in mount() — high-confidence subset of V008 |
| V007 | LiveView | Warning | Event handler missing **kwargs (legacy-policy handlers only) |
| V008 | LiveView | Info | Non-primitive type assigned in mount() — broader, lower-confidence (skips V006 patterns) |
| V012 | LiveView | Warning | Sticky child template declares its own dj-view (nested duplicate binding) |
| V013 | LiveView | Warning | HTTP-only dispatch()/get()/post() override never runs on a WebSocket mount |
| V014 | LiveView | Warning | Time-travel-enabled view has PII-looking model/form fields not in `time_travel_excluded_fields` |
| V015 | LiveView | Warning | LIVEVIEW_ALLOWED_MODULES rejects a djust LiveView the URLconf routes (add `"djust"`) |
| V016 | LiveView | Error | Strict-policy handler declaration that strict dispatch rejects (ADR-036) |
| V017 | LiveView | Error | Async strict event handler on an actor view (`use_actors = True`) |
| V018 | LiveView | Warning | `@event_handler(params=[...])` disagrees with a strict handler's signature |
| V019 | LiveView | Warning | A `dj-auto-recover` handler declares `parameter_policy="strict"`; recovery always runs under legacy policy |
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
| T018 | Templates | Warning | Template references a variable that resolves nowhere (renders blank, no error) |
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

### C020 — Invalid `DJUST_SERVER_STATE_MAX_AGE`
- **Severity**: Error
- **Method**: Settings inspection
- **What it detects**: `DJUST_SERVER_STATE_MAX_AGE` is set but is not an `int` from 1 to 86400. The setting is the restore lifetime, in seconds, of ADR-038 explicit server-state envelopes (default 3600). With an invalid value, explicit views fail closed: they cannot load or save server state.
- **Suppression**: `DJUST_CONFIG = {"suppress_checks": ["C020"]}` or `SILENCED_SYSTEM_CHECKS = ["djust.C020"]` (the runtime still fails closed)
- **False positives**: None

### C021 — Invalid `event_parameter_policy`
- **Severity**: Error
- **Method**: Settings inspection, through the resolver dispatch uses (`djust.validation.get_project_parameter_policy`)
- **What it detects**: `LIVEVIEW_CONFIG['event_parameter_policy']` (or the same key in `DJUST_CONFIG`) is set to something other than `'legacy'` or `'strict'`. Every handler without its own `parameter_policy` inherits the value, and dispatch rejects each of their events while it is invalid. An absent key is the `'legacy'` default and never reports. The ADR-036 strict policy is opt-in; legacy remains the default.
- **Suppression**: `DJUST_CONFIG = {"suppress_checks": ["C021"]}` or `SILENCED_SYSTEM_CHECKS = ["djust.C021"]` (the runtime still rejects the events)
- **False positives**: None

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
- **Method**: Class inspection (method name heuristic — `on_*`, `toggle_*`, `submit_*` and similar prefixes)
- **What it detects**: Public methods whose names match the event-handler naming pattern but lack the `@event_handler` decorator
- **Suppression** (any of):
  - `abstract = True` class attribute on an abstract base
  - `DJUST_CONFIG = {"suppress_checks": ["V004"]}` — global (fixed in #1607)
  - `SILENCED_SYSTEM_CHECKS = ["djust.V004"]`
- **Not flagged**: `handle_*` methods. `server_push` may call an undecorated `handle_*` method, and leaving it undecorated is how a handler is made callable by server push but not by browsers — adding `@event_handler` or a `_` prefix would break it (#3002). The framework's own lifecycle names (`mount`, `handle_params`, `handle_info`, `handle_tick`, …) were already exempt.

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
- **Not reported for strict-policy handlers**: under ADR-036's strict policy the closed signature is the handler's parameter contract, so adding `**kwargs` would open it. V016 checks those declarations instead.
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
- **What it detects**: A sticky-child view (`sticky = True`, embedded via `{% live_render ... sticky=True %}`) whose own template root carries a `dj-view` attribute (on any element, not only a `<div>`, since #2892). The `live_render` wrapper already emits `<div dj-view dj-sticky-view="<id>" ...>`, so the child's own `dj-view` nests a **duplicate** binding inside the wrapper — the child's client-side mount breaks and its events silently don't bind.
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

### V014 — Time-travel-enabled view exposes PII-looking fields
- **Severity**: Warning
- **Method**: Runtime (walks every registered `LiveView` that sets `time_travel_enabled = True`)
- **What it detects**: The view records `state_before` / `state_after` for every event, and `encode_view_state()` turns those snapshots into a **shareable** `djbug1.` blob — a bug capture exists to leave the machine it was captured on. If the view's model or form declares a field whose name looks like PII (`password`, `passwd`, `ssn`, `credit_card`, `tax_id`, `email`, `phone`) and that name is not in `time_travel_excluded_fields`, a password or an SSN is one paste away from a bug tracker.
- **Fix**: declare the sensitive keys — `time_travel_excluded_fields = ["password", "ssn"]`. `encode_view_state()` applies them before any caller-supplied `scrub`, so the redaction does not depend on every call site remembering one. The names are matched against **top-level public-state keys**, so list the attribute names the view assigns (often the same as the field names, but not necessarily).
- **False positives**: three gates keep this quiet, in order of how much work each does. (1) **`time_travel_enabled`** — a deliberate dev-only opt-in almost no view sets, so a project not using the feature never sees V014 at all; the demo project declares several PII-carrying forms and V014 is silent on it as shipped. (2) **Token matching, not substring matching** — `telephone_pole` does not contain the *token* `phone`. (3) **Field type** — `email` is on almost every user model, and so is `email_verified`; a `BooleanField`/`DateField`/`DateTimeField`/relation/auto-pk field is skipped whatever it is called, which is what keeps `email_notifications`, `phone_confirmed_at` and `password_changed_at` quiet.
- **Not `DEBUG`-gated**: the runtime surfaces (`BugCapture.encode`, the replay route) are, because they *do* something. A system check only tells you something, and `manage.py check --deploy` on the way to production is exactly when you want to hear that a shipped view records a password field.
- **Suppression**: `DJUST_CONFIG = {'suppress_checks': ['V014']}` or `SILENCED_SYSTEM_CHECKS = ["djust.V014"]`
- Added in the unreleased line (#1561)

### V016 — Strict-policy handler declaration that strict dispatch rejects
- **Severity**: Error
- **Method**: Runtime (walks user `LiveView` and `LiveComponent` subclasses; compiles each strict `@event_handler` / `@server_function` contract with the same cached resolver dispatch uses, without constructing a view or running a handler)
- **What it detects**, for handlers whose resolved `parameter_policy` is `'strict'` (declared on the decorator or inherited from `event_parameter_policy`):
  - an annotation that cannot be resolved: a misspelled name, a name imported only under `if TYPE_CHECKING:`, or a name that exists only in another class. Deferred annotations (`from __future__ import annotations`, quoted forward references) resolve against the defining class body first, then the module, as eager evaluation would. Classes defined inside a function body are not reachable by qualified name, so their class-body names cannot be resolved;
  - an unsupported type or shape (`dict`, unions other than `Optional[T]`, bare `list`, `Annotated`, `set`, ...). Supported: `str`, `int`, `float`, `bool`, `Decimal`, `UUID`, `date`, `Optional[T]`, `list[T]` and explicit `Any`;
  - a named parameter without an annotation (use `Any` for unchecked input; unannotated `*args` / `**kwargs` are an intentional open contract);
  - a keyword-capable parameter named `view_id` or `component_id`, or starting with `_`. Transports strip those routing keys before validation, so the parameter could never receive an application value (ADR-036 D5). Positional-only parameters may use any name;
  - a handler whose own `parameter_policy` metadata is not `'legacy'` or `'strict'`;
  - an ADR-034 output-subscription callback (staged) whose payload annotation the strict contract does not support. The source `component` is framework-supplied and not checked as a payload parameter.
- **Reporting**: one message per declaration, under the declaring class when it is itself checked, otherwise under its first user with `(declared as ...)`. Handlers that inherit an invalid project policy are covered by C021 instead.
- **Legacy handlers**: never reported. Legacy remains the default.
- **Suppression**: `DJUST_CONFIG = {"suppress_checks": ["V016"]}` or `SILENCED_SYSTEM_CHECKS = ["djust.V016"]` (dispatch still rejects the events)
- **False positives**: None: the check and dispatch share one compiled contract.

### V017 — Async strict event handler on an actor view
- **Severity**: Error
- **Method**: Runtime (user `LiveView` subclasses with `use_actors = True`)
- **What it detects**: a strict-policy `async def` event handler on an actor view. Actor dispatch rejects strict async handlers before invoking them.
- **Limitation**: components hosted by an actor view are not inspected: the host is not known statically.
- **Suppression**: `DJUST_CONFIG = {"suppress_checks": ["V017"]}` or `SILENCED_SYSTEM_CHECKS = ["djust.V017"]`

### V018 — `params=` disagrees with a strict handler's signature
- **Severity**: Warning
- **Method**: Runtime (decorator metadata compared with the compiled strict contract)
- **What it detects**: `@event_handler(params=[...])` names a different set of parameters from the strict handler's signature. Under the strict policy the signature is the contract; the explicit list is ignored by validation and misleads tooling that reads it.
- **Suppression**: `DJUST_CONFIG = {"suppress_checks": ["V018"]}` or `SILENCED_SYSTEM_CHECKS = ["djust.V018"]`

### V019 — Strict declaration on a `dj-auto-recover` handler
- **Severity**: Warning
- **Method**: Runtime (literal `dj-auto-recover="name"` in the view's own `template` / `template_name` source; dispatch additionally uses the HTML each render produced)
- **What it detects**: a handler that a `dj-auto-recover` binding targets and that declares `parameter_policy="strict"`. Recovery handlers receive the `_form_values` / `_data_attrs` dictionaries, so dispatch always runs them under the legacy policy (ADR-036 decision R1), whatever the declaration or project policy. Recovery targets are not otherwise checked by V016.
- **Limitation**: this startup check sees only the view's own template source. A binding in an included or parent template, or with a dynamic value, is not reported here, but dispatch still treats the handler as legacy once a render contains it.
- **Suppression**: `DJUST_CONFIG = {"suppress_checks": ["V019"]}` or `SILENCED_SYSTEM_CHECKS = ["djust.V019"]`

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
- **Currently flagged tags**: none — every Django built-in tag is implemented (`{% ifchanged %}` was the last, #2650). The set is derived from the engine's generated support lists (`docs/TEMPLATE_BACKEND.md`) and pinned by test, so this check goes live again only if a future Django tag is not yet implemented
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

### T018 — Undefined template variable reference (#2824)
- **Severity**: Warning
- **Method**: AST + regex (per-LiveView static analysis, not a per-file scan)
- **What it detects**: A template `{{ variable }}` / `{% if variable %}` /
  `{% for x in variable %}` head reference that resolves to nothing —
  Django (and the Rust engine) render it as an empty string with **no error
  and no warning**, so a typo'd or never-set context name is invisible to
  both the test suite and this check family until now. T018 compares each
  LiveView's template variable references against its statically-derivable
  context: public class attributes, `self.x = ...` assignments anywhere in
  the class (mount, event handlers, mixins), literal `get_context_data()`
  dict-return keys, `{% for %}`-declared loop vars in the same template, and
  the framework/Django-injected names (`csrf_token`, `request`, `user`,
  `messages`, `forloop`, …). Covers both `template_name` (file) and inline
  `template = "..."` views. Delegates its extraction to the same helpers
  `manage.py djust_typecheck` has shipped since v0.5.1 (#849), via a shared
  `_check_view_source()` extraction point. Shared EXTRACTION means the two
  entry points never disagree about what a given template means — but their
  COVERAGE differs by design (#2833): this check additionally covers inline
  `template = "..."` views and additionally skips `{% extends %}` templates
  (below), while `manage.py djust_typecheck` covers `{% extends %}`
  `template_name` templates but not inline ones. Run both for full coverage;
  neither is a superset of the other.
- **Fix**: Set the missing name via `self.x = ...` in `mount()`, return it
  from `get_context_data()`, or fix the typo in the template.
- **Suppression**: `DJUST_CONFIG = {"suppress_checks": ["T018"]}` project-wide,
  or `{# djust_typecheck: noqa name #}` (or bare `{# djust_typecheck: noqa #}`)
  in the template for a single name/template — the same pragma
  `djust_typecheck` already honors, not a second competing convention.
- **False positives avoided**: a dotted attribute tail (`row.options`)
  resolves on the root name only; `{{ value|default:"x" }}` resolves on
  `value` alone; framework-injected names and template-declared loop vars are
  never flagged; a view whose `get_context_data()` does anything this check
  can't statically follow (anything beyond a literal dict return or a bare
  `super().get_context_data(...)` delegation) is skipped **entirely**, never
  partially trusted.
- **Known limitation (v1)**: templates using `{% extends %}` are **skipped
  entirely** — a `{% block %}` override's variable references are
  template-inheritance context this check has no way to see (it only reads
  the child template's own source). This trades some false negatives for
  zero false positives on inheritance-based templates, per the issue's own
  guidance that an advisory check with a documented gap is safer than a
  noisy one. The skip is **reported, not silent** (#2833): a run that
  skipped one or more views emits one Info-level `djust.T018` message with
  the skipped count, so `All djust checks passed!` is falsifiable — "passed
  with 2 views skipped" is distinguishable from "examined everything and
  found nothing".
- **Scope**: Static check only; abstract base LiveViews (`abstract = True`)
  are skipped, matching the other V/T checks' convention.

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
