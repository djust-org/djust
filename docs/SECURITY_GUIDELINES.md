# Security Guidelines for djust Contributors

This document outlines security best practices for contributing to djust. Following these guidelines helps prevent common vulnerabilities and ensures the framework remains secure for users.

## Table of Contents

1. [Required Security Utilities](#required-security-utilities)
2. [Banned Patterns](#banned-patterns)
3. [Code Review Checklist](#code-review-checklist)
4. [Common Vulnerabilities](#common-vulnerabilities)
5. [Security Testing](#security-testing)

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

### JavaScript Security Utilities

```javascript
// Available globally as djustSecurity
djustSecurity.safeSetInnerHTML(element, htmlString);  // Safe innerHTML
djustSecurity.safeObjectAssign(target, source);       // Safe object merge
djustSecurity.sanitizeForLog(value);                  // Safe logging
```

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

### JavaScript

| Banned Pattern | Replacement | Reason |
|----------------|-------------|--------|
| `element.innerHTML = untrusted` | `djustSecurity.safeSetInnerHTML()` | XSS |
| `Object.assign(target, untrusted)` | `djustSecurity.safeObjectAssign()` | Prototype pollution |
| Direct console.log of user data | `djustSecurity.sanitizeForLog()` | Log injection |
| `new Function(userCode)` | Never use | Code injection |

---

## Code Review Checklist

When reviewing PRs, check for these security issues:

### Input Handling
- [ ] All `setattr()` calls with dynamic keys use `safe_setattr()`
- [ ] No direct attribute access like `obj.__class__` from user input
- [ ] URL parameters are validated before use
- [ ] Form inputs are sanitized server-side

### Error Handling
- [ ] No stack traces exposed in non-DEBUG responses
- [ ] No user parameters included in error responses
- [ ] Generic error messages used in production
- [ ] Exceptions logged with `exc_info=True` (not in response)

### Logging
- [ ] User input sanitized with `sanitize_for_log()`
- [ ] Sensitive data (passwords, tokens) never logged
- [ ] No raw exception messages with user data

### JavaScript
- [ ] innerHTML assignments reviewed for XSS
- [ ] Object merges use `safeObjectAssign()`
- [ ] No `eval()`, `new Function()`, or similar

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
- Server: Use Django's auto-escaping in templates
- Client: Use `djustSecurity.safeSetInnerHTML()` for dynamic content
- CSP headers for defense in depth

### 5. Parameter Reflection

**What it is:** Including user parameters in error responses.

**Impact:** Sensitive data leakage, debugging info exposure.

**Prevention:**
```python
# NEVER include params in error responses
# Use the debug panel for inspection instead
response = create_safe_error_response(e)  # No params!
```

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
