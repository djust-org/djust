# djust Security Audit - Key Findings

**Date**: 2026-02-13
**Auditor**: Claude Code
**Trigger**: RCE vulnerability discovered in mount handler (commit 07ffd2c)

---

## Summary

Comprehensive security review of all user input entry points in djust framework. Audited 9 WebSocket message handlers, template rendering, HTTP entry points, state serialization, and file uploads.

**Result**: 1 critical vulnerability fixed (mount RCE), 3 moderate risks identified, strong defense-in-depth controls in place.

---

## Fixed Vulnerabilities

### ✅ CRITICAL: Mount Handler RCE (Fixed)

**Issue**: Prior to fix, `handle_mount()` would import and instantiate ANY Python class without validation.

**Attack**:
```json
{"type": "mount", "view": "os.system"}
{"type": "mount", "view": "builtins.dict"}
```

**Fix** (commit 07ffd2c):
```python
# Line 665 in websocket.py
if not (isinstance(view_class, type) and issubclass(view_class, LiveView)):
    logger.error(error_msg)
    await self.send_error(_safe_error(error_msg, "Invalid view class"))
    return
```

**Test Coverage**: `test_security_mount_validation.py` (4 tests)

---

## Moderate Risk Findings

### ⚠️ 1. URL Injection in Mount Handler (Line 755)

**Issue**: Client-supplied URL used directly in request context without validation.

**Code**:
```python
page_url = data.get("url", "/")  # Untrusted input
path_with_query = f"{page_url}?{query_string}" if query_string else page_url
request = factory.get(path_with_query)  # Used without validation
```

**Attack Scenarios**:
- Path traversal: `../../admin/`
- CRLF injection: `foo\r\nHost: evil.com`
- If URL reflected in logs/responses: XSS via `javascript:alert(1)`

**Recommendation**:
```python
from urllib.parse import urlparse

page_url = data.get("url", "/")
parsed = urlparse(page_url)
if not parsed.path.startswith("/") or "\n" in page_url or "\r" in page_url:
    await self.send_error("Invalid URL")
    return
```

**Priority**: Medium - Django's `RequestFactory` may already sanitize, needs verification

---

### ⚠️ 2. Type Coercion in Event Parameters (Multiple locations)

**Issue**: Event handler parameters are coerced by default, which can enable injection attacks.

**Code**:
```python
# Line 1018, 1109, 1150 in websocket.py
coerce = get_handler_coerce_setting(handler)  # True by default
validation = validate_handler_params(..., coerce=coerce)
```

**Attack Scenario**:
```python
# Handler expects integer:
def delete_item(self, item_id: int, **kwargs):
    Item.objects.filter(id=item_id).delete()

# Attacker sends:
{"event": "delete_item", "params": {"item_id": "999 OR 1=1"}}

# If coerce naively converts to int, could bypass validation
```

**Recommendation**:
- Review `validate_handler_params()` coercion logic for edge cases
- Document that developers should use strict type checks when dealing with security-sensitive parameters
- Consider adding `@strict_types` decorator to disable coercion for sensitive handlers

**Priority**: Medium - Depends on `validate_handler_params()` implementation

---

### ⚠️ 3. Filename Stored XSS in File Uploads (Line 409)

**Issue**: Client-supplied filename stored without sanitization. If displayed in HTML later without escaping, causes stored XSS.

**Code**:
```python
# uploads.py line 409
entry = UploadEntry(
    client_name=client_name,  # Stored as-is
    ...
)

# Later, if developer does:
# <p>Uploaded: {{ entry.client_name|safe }}</p>  ← XSS
```

**Attack**:
```json
{
  "type": "upload_register",
  "client_name": "<img src=x onerror=alert(1)>.jpg"
}
```

**Mitigation Already in Place**:
- Rust template engine auto-escapes by default (XSS prevented unless `|safe` used)
- `tempfile.mkstemp()` uses only the extension, not full filename (path traversal prevented)

**Recommendation**:
- Add check S007: Warn about `{{ upload_entry.client_name|safe }}` patterns
- Document safe upload filename display patterns

**Priority**: Low - Auto-escaping prevents this by default

---

## Strong Controls Found (Defense in Depth)

### ✅ Event Handler Security

**Protection Layers**:
1. **Event name validation**: Blocks `__dunder__` and `_private` methods
2. **@event_handler whitelist**: Strict mode requires decorator (default)
3. **Parameter validation**: Type-checked against handler signature
4. **Per-handler rate limiting**: `@rate_limit` decorator
5. **Global rate limiting**: All message types (100/sec default)

**Test Coverage**: 600+ lines in `test_event_security.py`

---

### ✅ File Upload Security

**Protection Layers**:
1. **Extension whitelist**: `accept='.jpg,.png'` parameter
2. **MIME type validation**: Client-supplied type checked against magic bytes
3. **Magic byte validation**: Checks file header matches claimed type
4. **Size limits**: Both registration and chunk accumulation
5. **Max entries limit**: Prevents disk space exhaustion
6. **Temp file isolation**: Uses `tempfile.mkstemp()` with unique names
7. **Path traversal prevention**: Only extension used for suffix, not full filename

**Code**:
```python
# uploads.py line 76-104
def validate_magic_bytes(data: bytes, expected_mime: str) -> bool:
    signatures = MAGIC_BYTES.get(expected_mime)
    for magic, offset in signatures:
        if data[offset : offset + len(magic)] == magic:
            return True
    return False
```

---

### ✅ Message Size Limits

**Protection**: `max_message_size` config (64KB default)

**UTF-8 bypass prevention**:
```python
# Line 524-527 in websocket.py
char_len = len(text_data)
# Only skip encode when even worst-case (4 bytes/char) is under limit
raw_size = char_len if char_len * 4 <= max_msg_size else len(text_data.encode("utf-8"))
```

Prevents multi-byte character bypass (e.g., 1000 emoji chars = 4000 bytes)

---

### ✅ Error Disclosure Protection

**Production**: Generic error messages ("Event rejected", "View not found")
**DEBUG**: Detailed error messages with stack traces

```python
def _safe_error(detailed_msg: str, generic_msg: str = "Event rejected") -> str:
    from django.conf import settings
    return detailed_msg if settings.DEBUG else generic_msg
```

---

### ✅ Per-IP Connection Limits

**Protection**: `max_connections_per_ip` (default 10)
**Cooldown**: 5 seconds after rate limit disconnect

Prevents single IP from exhausting server resources.

---

## Test Coverage Gaps

### Missing Tests

1. ❌ URL injection in mount handler
2. ❌ Type coercion edge cases (SQL injection via coerced params)
3. ❌ Positional args override (e.g., `_args` overriding named params)
4. ❌ Malicious mount params dict (e.g., `{"__class__": ...}`)
5. ❌ File upload with path traversal filename (already prevented, but not tested)

### Recommendation

Add integration tests for the above scenarios to `tests/unit/test_security_mount_validation.py` or new `test_security_parameters.py`.

---

## Action Items

### High Priority

1. ✅ **DONE**: Fix mount handler RCE (commit 07ffd2c)
2. ⚠️ **TODO**: Verify `RequestFactory().get()` sanitizes URL paths (or add validation)
3. ⚠️ **TODO**: Review `validate_handler_params()` coercion for SQL/command injection risks
4. ⚠️ **TODO**: Add tests for URL injection, type coercion edge cases

### Medium Priority

5. Document secure patterns for handling coerced parameters
6. Add `@strict_types` decorator option to disable coercion
7. Add check S007: Warn about `{{ upload.client_name|safe }}`
8. Review all handlers in `websocket.py` (url_change, live_redirect_mount, etc.)

### Low Priority

9. Review Rust template engine JavaScript context escaping
10. Audit custom Django admin actions for CSRF
11. Document when to use `|safe` filter securely

---

## Conclusion

The djust framework has **strong security controls** with defense-in-depth:

✅ **Event security**: Comprehensive (rate limiting, validation, whitelist)
✅ **File uploads**: Robust (magic bytes, size limits, temp isolation)
✅ **Rate limiting**: Global + per-handler + per-IP
✅ **Error handling**: Safe disclosure (generic in prod, detailed in debug)

⚠️ **Mount RCE**: FIXED (commit 07ffd2c)
⚠️ **Moderate risks**: 3 identified, need follow-up

**Overall Risk**: **Low** (after mount RCE fix)

The mount RCE was a critical gap in our test coverage (no WebSocket integration tests for mount validation). Going forward, all user input entry points should have integration tests with malicious payloads.

---

## References

- Full audit: `SECURITY_AUDIT.md`
- Django Security: https://docs.djangoproject.com/en/stable/topics/security/
- OWASP Top 10: https://owasp.org/www-project-top-ten/
- djust Security Tests: `tests/unit/test_event_security.py`
- Mount Security Tests: `python/djust/tests/test_security_mount_validation.py`
