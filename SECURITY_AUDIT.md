# djust Security Audit - User Input Hot Spots

**Date**: 2026-02-13
**Context**: After discovering RCE vulnerability in mount handler (Issue #298), conducting comprehensive security review of all user input entry points.

---

## Executive Summary

This document identifies all locations where user-supplied data enters the djust framework and evaluates security controls at each entry point. Every incoming WebSocket message, HTTP request parameter, file upload, and template context is a potential attack vector.

## Critical Finding: Mount Handler RCE (Fixed in commit 07ffd2c)

**Vulnerability**: Prior to fix, `handle_mount()` would import and instantiate ANY Python class specified by the client without type validation.

**Attack**: `{"type": "mount", "view": "os.system"}` → RCE
**Fix**: Added `issubclass(view_class, LiveView)` validation at line 665

---

## Security Hot Spots by Category

### 1. WebSocket Message Handlers (Primary Attack Surface)

All handlers in `python/djust/websocket.py` process untrusted client data:

#### 1.1 `handle_mount()` (Line 605) ⚠️ CRITICAL

**Input**:
- `data.get("view")` - Python module path (e.g., `"myapp.views.MyView"`)
- `data.get("params")` - Dict of mount parameters
- `data.get("url")` - Page URL for request context
- `data.get("client_timezone")` - IANA timezone string
- `data.get("has_prerendered")` - Boolean flag

**Security Controls**:
- ✅ **Type validation** (line 665): `issubclass(view_class, LiveView)` - Prevents mounting arbitrary classes
- ✅ **Module whitelist** (line 627-637): `LIVEVIEW_ALLOWED_MODULES` if configured
- ✅ **Import error handling** (line 640-660): Catches invalid paths
- ✅ **Auth check** (line 794-820): `check_view_auth()` before mount
- ✅ **Timezone validation** (line 685-690): Validates IANA timezone
- ✅ **Rate limiting** (line 552): Global message rate limit

**Potential Issues**:
- ⚠️ **URL injection** (line 755): `data.get("url", "/")` used directly in request path - could contain path traversal (`../../`) or XSS payloads if not sanitized downstream
- ⚠️ **Params dict** (line 618): Arbitrary dict passed to mount() without schema validation - malicious keys could exploit bugs in mount() implementations
- 🔍 **Need to verify**: Does `RequestFactory().get()` sanitize the URL path?

**Test Coverage**:
- ✅ `test_security_mount_validation.py` - Tests type validation (rejects `builtins.dict`, `os.system`, `pathlib.Path`)
- ❌ **Missing**: URL injection tests
- ❌ **Missing**: Malicious params dict tests

---

#### 1.2 `handle_event()` (Line 960) ⚠️ HIGH RISK

**Input**:
- `data.get("event")` - Event handler name (string)
- `data.get("params")` - Dict of event parameters
- `params.get("_args")` - List of positional arguments
- `params.get("view_id")` - Target view ID for embedded views
- `params.get("component_id")` - Target component ID

**Security Controls**:
- ✅ **Event name validation** (via `_validate_event_security()`):
  - Blocks `__dunder__` and `_private` methods
  - Requires `@event_handler` decorator in strict mode
- ✅ **Parameter validation** (line 1018, 1109, 1150): `validate_handler_params()` checks types against signature
- ✅ **Per-handler rate limiting** (line 1011): Via `_ensure_handler_rate_limit()`
- ✅ **Component lookup** (line 1088): Validates component exists before routing

**Potential Issues**:
- ⚠️ **Type coercion** (line 1017, 1108, 1149): `coerce=True` by default - could allow unexpected type conversions leading to logic bugs
  - Example: Attacker sends `{"id": "999 OR 1=1"}` with coercion → might bypass validation if not careful
- ⚠️ **Positional args** (line 981): `params.pop("_args", [])` - arbitrary list merged into params - could override named params
- ⚠️ **Embedded view routing** (line 989-1000): `view_id` lookup - what if view_id is a crafted string attempting injection?
- 🔍 **Need to review**: `validate_handler_params()` implementation for edge cases

**Test Coverage**:
- ✅ `test_event_security.py` - Extensive event handler security tests
- ❌ **Missing**: Type coercion edge case tests (e.g., SQL injection via coerced strings)
- ❌ **Missing**: Positional args override tests

---

#### 1.3 `handle_url_change()` (Line 1843) ⚠️ MODERATE

**Input**:
- `data.get("url")` - New URL from client navigation
- `data.get("params")` - Query parameters as dict

**Security Controls**:
- ❓ **Unknown** - Need to read implementation

**Action Required**: Read and audit `handle_url_change()` implementation

---

#### 1.4 `handle_live_redirect_mount()` (Line 1886) ⚠️ MODERATE

**Input**:
- `data.get("view")` - View path to mount after redirect
- `data.get("params")` - Mount parameters

**Security Controls**:
- ❓ **Unknown** - Likely reuses `handle_mount()` logic

**Action Required**: Read and verify it uses same security checks as `handle_mount()`

---

#### 1.5 `_handle_upload_register()` (Line 1555) ⚠️ HIGH RISK

**Input**:
- `data.get("upload_id")` - Client-supplied upload ID
- `data.get("file_name")` - Client-supplied filename
- `data.get("file_size")` - Client-supplied file size
- `data.get("file_type")` - Client-supplied MIME type

**Security Controls**:
- ❓ **Unknown** - Need to read implementation

**Potential Issues**:
- ⚠️ **Path traversal**: Does `file_name` get sanitized? Could be `../../etc/passwd`
- ⚠️ **File type validation**: Is MIME type trusted? Should verify magic bytes
- ⚠️ **Size limit**: Is `file_size` enforced? Could claim 1KB but send 1GB

**Action Required**: Read and audit upload registration security

---

#### 1.6 `_handle_upload_frame()` (Line 1588) ⚠️ HIGH RISK

**Input**:
- `bytes_data` - Binary file data frames

**Security Controls**:
- ❓ **Unknown** - Need to read implementation

**Potential Issues**:
- ⚠️ **Memory exhaustion**: Are chunks accumulated in memory? DoS risk
- ⚠️ **Zip bombs**: Does decompression happen? Could expand to huge size
- ⚠️ **Malicious content**: Is file content scanned for malware?

**Action Required**: Read and audit upload frame handling

---

#### 1.7 `handle_presence_heartbeat()` (Line 1924) ⚠️ LOW

**Input**:
- `data` - Presence data dict

**Security Controls**:
- ❓ **Unknown** - Need to read implementation

**Action Required**: Quick audit for basic validation

---

#### 1.8 `handle_cursor_move()` (Line 1934) ⚠️ LOW

**Input**:
- `data` - Cursor position data

**Security Controls**:
- ❓ **Unknown** - Need to read implementation

**Potential Issues**:
- ⚠️ **Broadcast amplification**: If cursor positions are broadcast to all users, could be DoS vector

**Action Required**: Quick audit

---

#### 1.9 `handle_request_html()` (Line 1946) ⚠️ MODERATE

**Input**:
- `data` - Request for full HTML refresh

**Security Controls**:
- ❓ **Unknown** - Need to read implementation

**Action Required**: Quick audit

---

### 2. Template Rendering (XSS Attack Surface)

**Location**: Rust crates (`crates/djust_templates/`, `crates/djust_vdom/`)

**Input Sources**:
- View instance attributes (public state)
- Context data from `get_context_data()`
- Template variables (e.g., `{{ user.name }}`)

**Security Controls**:
- ✅ **Auto-escaping**: Rust template engine escapes HTML by default
- ✅ **Safe filter list** (`crates/djust_templates/src/renderer.rs`): `urlize`, `unordered_list` handle their own escaping
- ⚠️ **`safe` filter**: Users can opt-out of escaping with `{{ html|safe }}`

**Potential Issues**:
- ⚠️ **Stored XSS**: If user-controlled data stored in DB and rendered with `|safe`
- ⚠️ **JavaScript context**: Does auto-escaping work in `<script>` tags? Should use `json_script`

**Action Required**:
- Review Rust template engine escaping logic
- Search codebase for `|safe` usage patterns
- Add check for `mark_safe(f'...')` patterns (already in S001 check)

---

### 3. HTTP Request Entry Points

**Location**: `python/djust/routing.py`, `python/djust/views.py`

**Input Sources**:
- URL path parameters (e.g., `/items/<int:id>/`)
- Query parameters (e.g., `?search=term&page=1`)
- POST data (form submissions)
- HTTP headers (e.g., `User-Agent`, `X-Forwarded-For`)

**Security Controls**:
- ✅ **CSRF protection**: Django's CSRF middleware active
- ✅ **SQL injection**: Django ORM parameterizes queries
- ❓ **URL parameter validation**: Need to verify LiveView route registration

**Action Required**:
- Audit `live_session()` URL routing for injection risks
- Review how query params are passed to `mount()`

---

### 4. State Serialization (Code Injection Risk)

**Location**: `python/djust/live_view.py` - `get_state()` method

**Input**: View instance attributes (stored in `__dict__`)

**Security Controls**:
- ⚠️ **V006 check**: Static analysis detects service instances in mount()
- ❌ **No runtime validation**: Service instances silently serialize to strings, causing bugs later

**Vulnerability**:
```python
def mount(self, request, **kwargs):
    self.api_client = SomeService()  # Gets pickled to "<SomeService object at 0x...>"
```

Later: `self.api_client.call()` → `AttributeError: 'str' object has no attribute 'call'`

**Action Required** (from existing plan):
- Add runtime `_is_serializable()` check in `get_state()`
- Raise `TypeError` in DEBUG mode for non-serializable values

---

### 5. Django Admin & Management Commands

**Location**: Various `admin.py` and `management/commands/` files

**Input Sources**:
- Admin form data
- Management command arguments
- Uploaded files via admin

**Security Controls**:
- ✅ **Django admin authentication**: `@admin.register` decorators
- ✅ **Permission checks**: Django's built-in permission system

**Action Required**:
- Audit custom admin actions for CSRF protection
- Review management commands that accept file paths

---

## Global Security Controls (Defense in Depth)

### Rate Limiting

**Location**: `python/djust/rate_limit.py`

**Controls**:
- ✅ **Global rate limit** (line 552 in websocket.py): All message types
- ✅ **Per-handler rate limit**: `@rate_limit` decorator
- ✅ **Per-IP connection limit**: `IPConnectionTracker` (line 426)
- ✅ **Reconnect cooldown**: After rate limit disconnect (line 558)

**Test Coverage**: ✅ Extensive (`test_event_security.py`)

---

### Message Size Limit

**Location**: Line 519-533 in `websocket.py`

**Controls**:
- ✅ **Size check**: `max_message_size` config (default 64KB)
- ✅ **UTF-8 byte counting**: Prevents multi-byte char bypass (line 524-527)

**Test Coverage**: ✅ (`test_event_security.py::TestMessageSizeLimit`)

---

### Error Disclosure Protection

**Location**: `python/djust/websocket.py` - `_safe_error()` function

**Controls**:
- ✅ **DEBUG mode check**: Detailed errors only in DEBUG
- ✅ **Generic production errors**: "Event rejected", "View not found"

**Test Coverage**: ✅ (`test_event_security.py::TestErrorDisclosure`)

---

### Input Sanitization

**Location**: `python/djust/security.py` - `sanitize_for_log()` function

**Controls**:
- ✅ **Log injection prevention**: Sanitizes newlines and control chars from user input before logging

**Test Coverage**: ❓ Need to verify

---

## Security Gaps & Action Items

### Immediate (Critical)

1. ✅ **FIXED**: Mount handler RCE - Added type validation (commit 07ffd2c)
2. ⚠️ **TODO**: Audit `handle_url_change()` for URL injection
3. ⚠️ **TODO**: Audit `_handle_upload_register()` for path traversal
4. ⚠️ **TODO**: Audit `_handle_upload_frame()` for memory exhaustion

### High Priority

5. ⚠️ **TODO**: Review `validate_handler_params()` type coercion edge cases
6. ⚠️ **TODO**: Add runtime state serialization validation (`_is_serializable()`)
7. ⚠️ **TODO**: Search codebase for `|safe` filter usage (potential XSS)
8. ⚠️ **TODO**: Verify Rust template engine handles JavaScript context properly

### Medium Priority

9. ⚠️ **TODO**: Add tests for mount() params dict injection
10. ⚠️ **TODO**: Add tests for event positional args override
11. ⚠️ **TODO**: Review `handle_live_redirect_mount()` security
12. ⚠️ **TODO**: Audit custom admin actions

### Low Priority

13. ⚠️ **TODO**: Review presence/cursor handlers for broadcast DoS
14. ⚠️ **TODO**: Document secure `|safe` usage patterns

---

## Testing Strategy

### Current Coverage

- ✅ **Event handler security**: 600+ lines in `test_event_security.py`
- ✅ **Mount validation**: `test_security_mount_validation.py` (new)
- ✅ **Rate limiting**: Comprehensive
- ✅ **Message size limits**: Covered

### Missing Coverage

- ❌ **URL injection** tests
- ❌ **File upload security** tests
- ❌ **Type coercion edge cases**
- ❌ **Positional args override** tests
- ❌ **Template XSS** tests (Rust side)

---

## Secure Coding Guidelines for Contributors

### Rule 1: Never Trust User Input

**Bad**:
```python
def handle_mount(self, data):
    view_class = getattr(module, data.get("view"))  # ❌ No validation
    instance = view_class()
```

**Good**:
```python
def handle_mount(self, data):
    view_class = getattr(module, data.get("view"))
    if not issubclass(view_class, LiveView):  # ✅ Type validation
        raise SecurityError("Not a LiveView")
    instance = view_class()
```

### Rule 2: Validate, Don't Sanitize

Prefer **whitelisting** (allow only valid input) over **blacklisting** (block known bad input).

**Bad**:
```python
event_name = data.get("event")
if "__" not in event_name:  # ❌ Blacklist - can be bypassed
    handler = getattr(view, event_name)
```

**Good**:
```python
event_name = data.get("event")
if not is_event_handler(handler):  # ✅ Whitelist - explicit opt-in
    raise SecurityError("Not decorated with @event_handler")
```

### Rule 3: Assume Coercion Is Dangerous

Type coercion can enable injection attacks if not carefully controlled.

**Bad**:
```python
user_id = int(params.get("id"))  # ❌ What if id="999 OR 1=1"?
User.objects.filter(id=user_id)
```

**Good**:
```python
user_id = params.get("id")
if not isinstance(user_id, int):  # ✅ Strict type check
    raise ValueError("ID must be integer")
User.objects.filter(id=user_id)
```

### Rule 4: Escape at Render Time, Not Storage

Store data in raw form, escape only when rendering to prevent double-encoding bugs.

**Bad**:
```python
user.name = escape(request.POST.get("name"))  # ❌ Stored escaped
user.save()
# Later: {{ user.name }} renders as &lt;script&gt; (double-escaped)
```

**Good**:
```python
user.name = request.POST.get("name")  # ✅ Store raw
user.save()
# Later: {{ user.name }} auto-escapes at render time
```

### Rule 5: Log Defensively

Never log user input directly - sanitize first to prevent log injection.

**Bad**:
```python
logger.error("Event failed: %s", event_name)  # ❌ Could contain \n\r
```

**Good**:
```python
logger.error("Event failed: %s", sanitize_for_log(event_name))  # ✅
```

---

## Security Review Checklist for PRs

- [ ] Does this PR handle user input? (WebSocket message, HTTP param, file upload, template var)
- [ ] Is the input validated before use?
- [ ] Are types checked explicitly (not coerced blindly)?
- [ ] Are error messages generic in production? (no internal details leaked)
- [ ] Is user input sanitized before logging?
- [ ] Are tests added for malicious input cases?
- [ ] Does this use `mark_safe()` or `|safe`? If yes, is input from trusted source only?
- [ ] Does this change authentication/authorization logic? If yes, are all paths covered?

---

## References

- Django Security Guide: https://docs.djangoproject.com/en/stable/topics/security/
- OWASP Top 10: https://owasp.org/www-project-top-ten/
- CWE Top 25: https://cwe.mitre.org/top25/
- djust Checks: `python/djust/checks.py` (S0xx security checks)
- djust Security Tests: `tests/unit/test_event_security.py`
