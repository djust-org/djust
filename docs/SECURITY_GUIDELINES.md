# Security Guidelines for djust Contributors

This document outlines security best practices for contributing to djust. Following these guidelines helps prevent common vulnerabilities and ensures the framework remains secure for users.

## Table of Contents

1. [Required Security Utilities](#required-security-utilities)
2. [Template Auto-Escaping](#template-auto-escaping)
3. [Authentication & Authorization](#authentication--authorization)
4. [Banned Patterns](#banned-patterns)
5. [Code Review Checklist](#code-review-checklist)
6. [Common Vulnerabilities](#common-vulnerabilities)
7. [Multi-Tenant Security](#multi-tenant-security)
8. [PWA / Offline Sync Security](#pwa--offline-sync-security)
9. [Security Testing](#security-testing)
10. [Pre-Release Security Audit](#pre-release-security-audit)
11. [Security Hot Spot Files](#security-hot-spot-files)

---

## Required Security Utilities

djust provides security utilities in `djust.security` that **must** be used when handling untrusted input.

### Python Security Module

```python
from djust.security import (
    safe_setattr,       # Use instead of setattr() with untrusted keys
    sanitize_for_log,   # Use before logging user input
    create_safe_error_response,  # Use for WebSocket/HTTP error responses
)
```

### 1. Attribute Setting from Untrusted Input

**Always use `safe_setattr()` instead of raw `setattr()` when attribute names come from untrusted sources:**

```python
# BAD - vulnerable to prototype pollution
for key, value in user_params.items():
    setattr(obj, key, value)

# GOOD - blocks dangerous attributes (__class__, __proto__, etc.)
from djust.security import safe_setattr

for key, value in user_params.items():
    safe_setattr(obj, key, value)
```

**When to use:**
- Restoring state from sessions
- Processing URL parameters
- Deserializing user-provided data
- Setting component properties from props

### 2. Logging User Input

**Always use `sanitize_for_log()` before logging user-controlled data:**

```python
# BAD - vulnerable to log injection
logger.info(f"User searched for: {user_query}")

# GOOD - strips control characters and truncates
from djust.security import sanitize_for_log

logger.info(f"User searched for: {sanitize_for_log(user_query)}")
```

**What it prevents:**
- ANSI escape sequence injection (terminal manipulation)
- Log line injection (forged log entries)
- Log flooding (extremely long strings)

### 3. Error Responses

**Always use `create_safe_error_response()` for generating error responses:**

```python
# BAD - leaks stack traces and params in production
await self.send_json({
    "type": "error",
    "error": str(e),
    "traceback": traceback.format_exc(),
    "params": params,  # NEVER include params!
})

# GOOD - DEBUG-aware, no information leakage
from djust.security import create_safe_error_response

response = create_safe_error_response(
    exception=e,
    error_type="event",
    event_name=event_name,
)
await self.send_json(response)
```

**Security properties:**
- Stack traces only in DEBUG mode
- Never includes user parameters
- Generic messages in production

### 4. WebSocket Event Security

djust provides three layers of protection for WebSocket event dispatch. These are enabled by default — no configuration needed.

#### Layer 1: Event Name Guard

A regex pattern filter runs before `getattr()` to block dangerous method names:

```python
# Only allows: lowercase letters, digits, underscores, starting with a letter
# Blocks: _private, __dunder__, CamelCase, dots, dashes, spaces, empty strings
from djust.security import is_safe_event_name

is_safe_event_name("increment")    # True
is_safe_event_name("__class__")    # False
is_safe_event_name("_private")     # False
```

#### Layer 2: `@event_handler` Decorator Allowlist

Only methods explicitly marked as event handlers are callable via WebSocket. This is controlled by the `event_security` setting (default: `"strict"`):

```python
from djust import LiveView
from djust.decorators import event_handler

class MyView(LiveView):
    @event_handler
    def increment(self):
        """This IS callable via WebSocket"""
        self.count += 1

    def internal_helper(self):
        """This is NOT callable via WebSocket (not decorated)"""
        pass
```

**Modes:**
- `"strict"` (default) — only `@event_handler` decorated methods are callable
- `"warn"` — allows undecorated methods but logs deprecation warnings
- `"open"` — no decorator check (legacy behavior, not recommended)

#### Layer 3: Server-Side Rate Limiting

Per-connection token bucket rate limiting prevents event flooding:

```python
# In settings.py
LIVEVIEW_CONFIG = {
    "rate_limit": {
        "rate": 100,
        "burst": 20,
        "max_warnings": 3,
        "max_connections_per_ip": 10,   # Max concurrent WebSocket connections per IP
        "reconnect_cooldown": 5,        # Seconds before a rate-limited IP can reconnect
    },
    "max_message_size": 65536,  # 64KB
}
```

For expensive handlers, use the `@rate_limit` decorator:

```python
from djust.decorators import event_handler, rate_limit

class MyView(LiveView):
    @rate_limit(rate=5, burst=3)
    @event_handler
    def expensive_operation(self, **kwargs):
        """Limited to 5/sec sustained, 3 burst"""
        ...
```

Violations trigger warnings. After `max_warnings` violations, the connection is closed with code `4429`.

#### Per-IP Connection Limits

A process-level `IPConnectionTracker` prevents a single IP from opening excessive WebSocket connections. When the limit is reached, new connections are immediately rejected with close code `4429`. After a rate-limit disconnect, the IP enters a cooldown period during which all reconnection attempts are rejected.

The tracker supports `X-Forwarded-For` headers for deployments behind reverse proxies. Note that this is per-process; multi-process deployments would need an external store (e.g., Redis) for cross-process tracking.

### JavaScript Security Utilities

```javascript
// Available globally as djustSecurity
djustSecurity.safeSetInnerHTML(element, htmlString);  // Safe innerHTML
djustSecurity.safeObjectAssign(target, source);       // Safe object merge
djustSecurity.sanitizeForLog(value);                  // Safe logging
```

---

## Template Auto-Escaping

djust's Rust template engine **auto-escapes all variables by default**, matching Django's behavior. This is the primary defense against XSS.

### How It Works

Every `{{ variable }}` expression is HTML-escaped before rendering:

| Input | Output |
|-------|--------|
| `<script>alert(1)</script>` | `&lt;script&gt;alert(1)&lt;/script&gt;` |
| `<img onerror=alert(1)>` | `&lt;img onerror=alert(1)&gt;` |
| `"onclick=evil()"` | `&quot;onclick=evil()&quot;` |

### SafeString Propagation

Django's `mark_safe()` values are automatically detected and skip escaping:

- **Component HTML**: `Component.render()` returns `SafeString` — auto-detected in `_sync_state_to_rust()` and `render_full_template()`
- **CSRF input**: `csrf_input` is marked safe so the hidden form field renders correctly
- **Custom safe values**: Any context value that is a `SafeString` instance is auto-detected before serialization

The detection happens in three places:
1. `DjustTemplate.render()` — the template backend path (in `rendering.py`)
2. `_sync_state_to_rust()` — the WebSocket render path (in `rust_bridge.py`)
3. `render_full_template()` — the initial GET render path (in `template.py`)

### The `|safe` Filter

Use `{{ variable|safe }}` to skip escaping for a specific variable in a template:

```html
{# Escaped (default — safe) #}
<p>{{ user_input }}</p>

{# Unescaped (explicit opt-in — use with caution) #}
<div>{{ pre_rendered_html|safe }}</div>
```

**Rule**: Never use `|safe` on user-controlled values. Only use it for HTML you've generated server-side (e.g., pre-rendered components, markdown-to-HTML output).

### What's NOT Escaped

- Values marked with `mark_safe()` in Python
- Values using the `|safe` filter in templates
- `csrf_input` (contains raw `<input>` HTML)
- Component `.render()` output (pre-rendered HTML)

---

## Authentication & Authorization

djust provides framework-enforced authentication and authorization for LiveViews. Auth checks run server-side before `mount()` and before individual event handlers — no client-side bypass is possible.

### View-Level Auth

Use class attributes to enforce auth on the entire view:

```python
from djust import LiveView

class DashboardView(LiveView):
    template_name = "dashboard.html"
    login_required = True                              # Must be authenticated
    permission_required = "analytics.view_dashboard"   # Django permission string
    login_url = "/login/"                              # Override settings.LOGIN_URL
```

Or use Django-familiar mixins:

```python
from djust.auth import LoginRequiredMixin, PermissionRequiredMixin

class DashboardView(LoginRequiredMixin, PermissionRequiredMixin, LiveView):
    template_name = "dashboard.html"
    permission_required = "analytics.view_dashboard"
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

### Custom Auth Logic

Override `check_permissions()` for object-level authorization:

```python
class ProjectView(LiveView):
    login_required = True

    def check_permissions(self, request):
        project = Project.objects.get(pk=self.kwargs.get("pk"))
        return project.team.members.filter(user=request.user).exists()
```

### Auth Audit & System Checks

- **`djust_audit` command** shows auth configuration for every view
- **`djust.S005` system check** warns when views expose state without authentication
- Set `login_required = False` to explicitly mark public views and suppress warnings

For the full guide, see **[Authentication & Authorization Guide](guides/authentication.md)**.

---

## Banned Patterns

The following patterns are **prohibited** in djust code:

### Python

| Banned Pattern | Replacement | Reason |
|----------------|-------------|--------|
| `setattr(obj, untrusted_key, value)` | `safe_setattr(obj, key, value)` | Prototype pollution |
| `traceback.format_exc()` in responses | `create_safe_error_response()` | Information disclosure |
| `f"...{user_input}..."` in logs | `f"...{sanitize_for_log(user_input)}..."` | Log injection |
| `params` in error responses | Remove entirely | Data leakage |
| `eval(user_input)` | Never use eval | Code injection |
| `exec(user_input)` | Never use exec | Code injection |
| `getattr(obj, user_input)` without guard | `is_safe_event_name()` + `@event_handler` check | Arbitrary method invocation |
| Event handler without `@event_handler` | Add `@event_handler` decorator | Unrestricted WebSocket access |
| `mark_safe(f'...')` with user values | `format_html()` or `escape()` | XSS via template tags |
| `@csrf_exempt` without justification | Token-based auth or standard CSRF | CSRF bypass |

### JavaScript

| Banned Pattern | Replacement | Reason |
|----------------|-------------|--------|
| `element.innerHTML = untrusted` | `djustSecurity.safeSetInnerHTML()` | XSS |
| `Object.assign(target, untrusted)` | `djustSecurity.safeObjectAssign()` | Prototype pollution |
| Direct console.log of user data | `djustSecurity.sanitizeForLog()` | Log injection |
| `new Function(userCode)` | Never use | Code injection |
| `console.log`/`console.error` in production JS | `_log()` helper or `window.djust.reportError()` | Debug info leakage |

---

## Code Review Checklist

When reviewing PRs, check for these security issues:

### Input Handling
- [ ] All `setattr()` calls with dynamic keys use `safe_setattr()`
- [ ] No direct attribute access like `obj.__class__` from user input
- [ ] URL parameters are validated before use
- [ ] Form inputs are sanitized server-side

### Authentication & Authorization
- [ ] Views with exposed state have `login_required = True` or `login_required = False` (explicit)
- [ ] Destructive handlers use `@permission_required` for defense-in-depth
- [ ] `check_permissions()` overrides validate object-level access
- [ ] `djust_audit` shows no unprotected views with exposed state

### WebSocket Event Security
- [ ] All event handlers decorated with `@event_handler`
- [ ] No `getattr()` on user-controlled names without `is_safe_event_name()` guard
- [ ] `event_security` setting is `"strict"` (default) in production
- [ ] Expensive handlers have `@rate_limit` decorator
- [ ] All event handlers use `@event_handler` decorator

### Error Handling
- [ ] No stack traces exposed in non-DEBUG responses
- [ ] No user parameters included in error responses
- [ ] Generic error messages used in production
- [ ] Exceptions logged with `exc_info=True` (not in response)

### Logging
- [ ] User input sanitized with `sanitize_for_log()`
- [ ] Sensitive data (passwords, tokens) never logged
- [ ] No raw exception messages with user data

### Template Tags
- [ ] Template tags use `format_html()` or `escape()`, never `mark_safe(f'...')`
- [ ] New endpoints have CSRF protection (no unjustified `@csrf_exempt`)
- [ ] No `|safe` filter used on user-controlled template variables
- [ ] Components pass through SafeString detection (pre-rendered HTML not double-escaped)

### JavaScript
- [ ] innerHTML assignments reviewed for XSS
- [ ] Object merges use `safeObjectAssign()`
- [ ] No `eval()`, `new Function()`, or similar
- [ ] No `console.log`/`console.error` in production JS — use `_log()` helper

### Dependencies
- [ ] No known vulnerable dependencies
- [ ] `cargo audit` passes
- [ ] `npm audit` passes
- [ ] `safety check` passes

---

## Common Vulnerabilities

### 1. Prototype Pollution (Python)

**What it is:** Setting dangerous attributes like `__class__`, `__proto__`, or `__init__` via untrusted input.

**Impact:** Code execution, authentication bypass, denial of service.

**Prevention:**
```python
from djust.security import safe_setattr

# This blocks __class__, __proto__, etc.
safe_setattr(obj, user_key, user_value)
```

### 2. Stack Trace Exposure

**What it is:** Returning full stack traces in production error responses.

**Impact:** Information disclosure (file paths, code structure, dependencies).

**Prevention:**
```python
from djust.security import create_safe_error_response

# Automatically handles DEBUG mode
response = create_safe_error_response(e, error_type="event")
```

### 3. Log Injection

**What it is:** User input containing newlines or ANSI codes that manipulate logs.

**Impact:** Forged log entries, hidden attacks, terminal manipulation.

**Prevention:**
```python
from djust.security import sanitize_for_log

logger.info(f"Query: {sanitize_for_log(user_query)}")
```

### 4. XSS (Cross-Site Scripting)

**What it is:** Injecting malicious scripts via user input that's rendered in HTML.

**Impact:** Session hijacking, data theft, defacement.

**Prevention:**
- Server: djust's Rust template engine auto-escapes all `{{ variable }}` output by default (HTML entities)
- Server: `SafeString` values (from `mark_safe()`, component `.render()`) are auto-detected and skip escaping
- Server: Template tags use `format_html()` or `escape()`, never `mark_safe(f'...')`
- Client: Use `djustSecurity.safeSetInnerHTML()` for dynamic content
- CSP headers for defense in depth

### 5. Arbitrary Method Invocation via WebSocket

**What it is:** A client sends a crafted event name (e.g., `__init__`, `delete`, `mount`) to invoke unintended methods via `getattr()`.

**Impact:** Calling internal framework methods, Django ORM methods, or private helpers.

**Prevention:**
```python
# All three layers are automatic when event_security = "strict" (default):
# 1. Regex guard blocks _private, __dunder__, malformed names
# 2. @event_handler decorator allowlist blocks unmarked methods
# 3. Rate limiter prevents flooding

# Just decorate your handlers:
from djust.decorators import event_handler

@event_handler
def my_handler(self, **kwargs):
    pass
```

### 6. Parameter Reflection

**What it is:** Including user parameters in error responses.

**Impact:** Sensitive data leakage, debugging info exposure.

**Prevention:**
```python
# NEVER include params in error responses
# Use the debug panel for inspection instead
response = create_safe_error_response(e)  # No params!
```

### 7. Template Tag XSS

**What it is:** Using `mark_safe()` with f-string interpolation of user-controlled values (e.g., URLs, class names, CSS selectors) in template tags.

**Impact:** Script injection via manipulated template tag parameters.

**Prevention:**
```python
# BAD - user-controlled URL injected into mark_safe f-string
from django.utils.safestring import mark_safe

def pwa_head(manifest_url, theme_color):
    return mark_safe(f'<link rel="manifest" href="{manifest_url}">'
                     f'<meta name="theme-color" content="{theme_color}">')

# GOOD - format_html auto-escapes interpolated values
from django.utils.html import format_html

def pwa_head(manifest_url, theme_color):
    return format_html(
        '<link rel="manifest" href="{}">'
        '<meta name="theme-color" content="{}">',
        manifest_url, theme_color
    )
```

Use `escape()` for values embedded in JavaScript strings:
```python
from django.utils.html import escape

# Safe for embedding in JS string contexts
js_selector = escape(user_selector)
```

---

## Multi-Tenant Security

When building multi-tenant features, follow these principles to prevent cross-tenant data leakage:

### Tenant Isolation Principles

1. **Key Prefixing** — All storage keys (cache, state backends, session data) must be prefixed with the tenant identifier. Never use global keys that could collide across tenants.

2. **QuerySet Scoping** — All database queries in tenant-aware views must be filtered by the current tenant. Use `TenantScopedMixin` to enforce this automatically.

3. **Tenant-Aware Backends** — Storage backends (Redis, memory, file) must isolate data per tenant. All keys should include the tenant ID prefix.

4. **Context Isolation** — Template context processors in multi-tenant mode must only inject data for the current tenant.

### Code Example: TenantScopedMixin

```python
from djust.tenant.mixins import TenantScopedMixin

class TenantDocumentView(TenantScopedMixin, LiveView):
    """Queries are automatically filtered by current tenant."""

    def get_queryset(self):
        # TenantScopedMixin auto-filters: .filter(tenant=self.tenant)
        return Document.objects.all()
```

### Cross-Tenant Prevention Checklist

- [ ] All cache/state keys include tenant prefix
- [ ] Database queries scoped to current tenant
- [ ] File uploads stored in tenant-specific paths
- [ ] Background tasks carry tenant context
- [ ] Admin views respect tenant boundaries

---

## PWA / Offline Sync Security

The PWA sync system allows offline actions to be replayed when connectivity is restored. This creates a trust boundary — all data from the client must be treated as untrusted.

### Sync Endpoint Requirements

1. **Authentication Required** — The sync endpoint must require authentication. Never use `@csrf_exempt` without equivalent protection (e.g., token-based auth).

2. **Payload Validation** — Validate the sync payload structure before processing:
   - Type-check the top-level payload (must be a dict)
   - Type-check the actions list (must be a list)
   - Enforce an action count limit (default: 100) to prevent abuse
   - Validate each action's fields against a whitelist

3. **Safe Field Extraction** — Never pass client data as arbitrary `**kwargs`. Extract only known fields:

```python
# BAD - arbitrary kwargs from client data
def process_action(action_data):
    MyModel.objects.create(**action_data)

# GOOD - explicit field extraction with validation
ALLOWED_FIELDS = frozenset({"title", "content", "status"})

def process_action(action_data):
    if not isinstance(action_data, dict):
        logger.warning("Invalid action data type: %s", type(action_data).__name__)
        return

    safe_fields = {k: v for k, v in action_data.items() if k in ALLOWED_FIELDS}
    MyModel.objects.create(**safe_fields)
```

4. **Offline Data Validation** — Data collected offline may be stale or manipulated. Validate timestamps, check for conflicts, and verify permissions before applying synced changes.

---

## Security Testing

### Automated Scanning

djust uses several automated security tools:

1. **Bandit** - Python security linter
   ```bash
   bandit -r python/djust/ -ll -ii
   ```

2. **Safety** - Python dependency vulnerabilities
   ```bash
   pip freeze | safety check --stdin
   ```

3. **cargo-audit** - Rust dependency vulnerabilities
   ```bash
   cargo audit
   ```

4. **npm audit** - JavaScript dependency vulnerabilities
   ```bash
   npm audit --audit-level=high
   ```

5. **Semgrep** - Custom security rules
   ```bash
   semgrep --config .semgrep/
   ```

### Pre-commit Hooks

Security checks run automatically on commit:
- Bandit (Python security)
- detect-secrets (credential detection)
- cargo-audit (Rust dependencies)
- detect-private-key (key file detection)

### Manual Testing

For security-sensitive changes:

1. **Prototype Pollution Test:**
   ```python
   # Should fail silently or raise
   safe_setattr(obj, "__class__", MaliciousClass)
   assert obj.__class__ != MaliciousClass
   ```

2. **Error Response Test:**
   ```python
   # In DEBUG=False, should not contain traceback
   response = create_safe_error_response(ValueError("test"))
   assert "traceback" not in response
   assert "test" not in response["error"]
   ```

3. **Log Sanitization Test:**
   ```python
   # Should strip ANSI and control characters
   result = sanitize_for_log("\x1b[31mred\x1b[0m\ninjected")
   assert "\x1b" not in result
   assert "\n" not in result
   ```

### Automated Fuzz Testing

djust provides `LiveViewSmokeTest`, a test mixin that auto-discovers LiveView subclasses and fuzzes their event handlers with XSS payloads and type-confusion values:

```python
from django.test import TestCase
from djust.testing import LiveViewSmokeTest

class TestAllViews(TestCase, LiveViewSmokeTest):
    app_label = "myapp"     # only test views in this app (optional)
    max_queries = 20        # fail if render exceeds this many queries
    fuzz = True             # fuzz event handlers (default: True)
```

**What it tests:**
- `test_smoke_render` — every view mounts and renders without exceptions
- `test_smoke_queries` — every render stays within the query count threshold
- `test_fuzz_xss` — XSS payloads in handler params don't appear unescaped in rendered HTML
- `test_fuzz_no_unhandled_crash` — wrong-type params don't cause unhandled exceptions

**XSS payloads tested include:**
- `<script>alert("xss")</script>`
- `<img src=x onerror=alert(1)>`
- `"><svg onload=alert(1)>`
- SQL injection strings
- Template injection strings

**Type-confusion payloads include:**
- `None`, wrong types (`str` for `int`, `int` for `bool`, etc.)
- Boundary values (`2^31`, `float("inf")`, empty strings)
- Oversized strings (`"x" * 10000`)

Run fuzz tests alongside your regular test suite:

```bash
pytest -k "TestAllViews" -v
```

---

## Pre-Release Security Audit

Every release of djust must pass a security audit before publication. This process is enforced by the `pre-release-security-audit.yml` GitHub Actions workflow, which runs automatically on release branches and tags.

### Audit Stages

The pre-release audit consists of five stages, each of which must pass before proceeding:

#### Stage 1: Automated Scanning

Automated tools run against the full codebase:

| Tool | Scope | What It Checks |
|------|-------|----------------|
| Bandit | Python | Common security anti-patterns (eval, exec, hardcoded passwords) |
| Safety | Python dependencies | Known CVEs in installed packages |
| cargo audit | Rust dependencies | Known CVEs in Cargo crate dependencies |
| npm audit | JavaScript dependencies | Known CVEs in npm packages |
| Semgrep | Cross-language | Custom rules in `.semgrep/` (XSS patterns, banned APIs) |
| detect-secrets | All files | Accidentally committed credentials or API keys |

These scans must produce zero high-severity findings. Medium-severity findings require documented justification to proceed.

#### Stage 2: Manual Code Review of Hot Spots

All [security hot spot files](#security-hot-spot-files) must be manually reviewed by a maintainer, regardless of whether they changed in the release. The reviewer verifies:

- No regressions in input validation or escaping
- Auth checks are enforced at both view and handler levels
- Rate limiting configuration is appropriate
- Error responses do not leak internal details

#### Stage 3: Integration Security Testing

Run the dedicated security test suites:

```bash
# Parameter injection tests
pytest python/tests/test_security.py -v

# Mount validation tests
pytest python/tests/test_security_mount_validation.py -v

# Fuzz testing across all views
pytest -k "LiveViewSmokeTest" -v

# Rust XSS prevention tests
cargo test -p djust_templates -- xss
```

All tests must pass with zero failures.

#### Stage 4: Configuration Review

Verify production-safe defaults:

- [ ] `event_security` defaults to `"strict"`
- [ ] `DEBUG` is not hardcoded to `True` anywhere
- [ ] Rate limit defaults are reasonable (100 req/s sustained, 20 burst)
- [ ] `max_message_size` is set (default: 64KB)
- [ ] `max_connections_per_ip` is set (default: 10)
- [ ] CSRF protection is enabled on all non-WebSocket endpoints
- [ ] Template auto-escaping is enabled by default in the Rust engine

#### Stage 5: Sign-Off

The release requires sign-off from at least one maintainer who has:

1. Reviewed the automated scan results
2. Completed the manual hot spot review
3. Verified all security tests pass
4. Confirmed no open security issues affect the release

Sign-off is recorded in the GitHub release notes and the security audit issue created by the workflow.

### Audit Issue Template

Each pre-release audit creates a GitHub issue from `.github/SECURITY_AUDIT_TEMPLATE.md`. This issue tracks completion of all audit stages and serves as a permanent record. The issue must be closed (all items checked) before the release is published.

### Skipping the Audit

The audit **cannot** be skipped. Documentation-only releases (changes limited to `.md` files with no code changes) still run automated scans but may skip the manual review stages with maintainer approval noted in the audit issue.

---

## Security Hot Spot Files

Certain files in the codebase have elevated security impact. Changes to these files require additional review scrutiny and trigger the `security-review` label on pull requests.

### Hot Spot File List

| File | Security Concern |
|------|-----------------|
| `python/djust/websocket.py` | WebSocket event dispatch, message handling, connection lifecycle |
| `python/djust/security.py` | Core security utilities (safe_setattr, sanitize_for_log, error responses) |
| `python/djust/auth.py` | Authentication and authorization enforcement |
| `python/djust/decorators.py` | @event_handler allowlist, @permission_required, @rate_limit |
| `python/djust/live_view.py` | State management, context exposure, mount lifecycle |
| `python/djust/uploads.py` | File upload handling (binary WebSocket frames) |
| `python/djust/config.py` | Security-related configuration defaults |
| `python/djust/routing.py` | URL routing and session management |
| `python/djust/templatetags/` | Template tags that may use mark_safe() |
| `python/djust/tenants/` | Multi-tenant isolation boundaries |
| `crates/djust_templates/src/renderer.rs` | Template rendering, auto-escaping, safe filter handling |
| `crates/djust_templates/src/filters.rs` | Filter implementations (safe_output_filters list) |
| `crates/djust_vdom/` | VDOM diffing (attribute injection vectors) |
| `python/djust/static/djust/` | Client-side JavaScript (XSS surface) |

### Review Requirements for Hot Spot Changes

When a PR modifies any hot spot file:

1. **Automatic labeling** -- The PR workflow adds a `security-review` label and posts a reminder comment listing which hot spot files were changed.

2. **Mandatory security reviewer** -- At least one reviewer with security expertise must approve the PR. Standard code review approval alone is not sufficient.

3. **Expanded testing** -- The PR must include or reference:
   - Targeted tests for the specific security property affected
   - Fuzz test results (`LiveViewSmokeTest`) if the change affects rendering or event handling
   - Manual verification that banned patterns were not introduced

4. **Documentation** -- If the change alters security behavior (e.g., new escaping rules, changed auth flow), update this document and the [Code Review Checklist](#code-review-checklist).

### Adding New Hot Spot Files

When introducing a new file that handles untrusted input, authentication, or security-sensitive operations:

1. Add it to the hot spot list above
2. Update `.github/workflows/` to include the new path in the detection pattern
3. Add a comment in the PR explaining why the file is security-sensitive

---

## Reporting Security Issues

If you discover a security vulnerability in djust:

1. **Do NOT** open a public GitHub issue
2. Email: security@djust.org
3. Include:
   - Description of the vulnerability
   - Steps to reproduce
   - Potential impact
   - Suggested fix (if any)

We will respond within 48 hours and work with you on a fix.

---

## Resources

- [OWASP Top 10](https://owasp.org/www-project-top-ten/)
- [Django Security Documentation](https://docs.djangoproject.com/en/stable/topics/security/)
- [Python Security Best Practices](https://python-security.readthedocs.io/)
- [Rust Security Guidelines](https://anssi-fr.github.io/rust-guide/)
