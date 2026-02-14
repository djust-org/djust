# Security Audit Checklist: v{{VERSION}}

This checklist tracks all security review steps required before releasing v{{VERSION}}.
Complete each section and check off items as they are verified.

## 1. Automated Scans

- [ ] **Bandit** (Python security linter) -- no high/critical findings
- [ ] **Safety / pip-audit** -- no known vulnerabilities in Python dependencies
- [ ] **cargo-audit** -- no known vulnerabilities in Rust dependencies
- [ ] **npm audit** -- no high/critical vulnerabilities in JS dependencies
- [ ] **ESLint security plugin** -- no new security warnings
- [ ] **CodeQL** -- no new security alerts (Python + JavaScript)

## 2. Manual Code Review

### 2a. HTML / XSS Prevention
- [ ] No `mark_safe(f'...')` with interpolated user data
- [ ] All user-facing output uses `format_html()` or `escape()`
- [ ] Rust template renderer auto-escapes all variable output
- [ ] `safe` filter usage is justified and documented
- [ ] No raw HTML injection via `dj-html` or similar directives

### 2b. Authentication & Authorization
- [ ] `@permission_required` decorators on all sensitive event handlers
- [ ] `check_view_auth()` enforces access control on LiveView mount
- [ ] WebSocket authentication checks on connect and each message
- [ ] No privilege escalation via parameter manipulation

### 2c. Input Validation
- [ ] Event handler parameters have type annotations and defaults
- [ ] No unsanitized user input in SQL, shell commands, or file paths
- [ ] File upload validation (size, type, name sanitization)
- [ ] URL parameters validated before use

### 2d. CSRF & Session Security
- [ ] No `@csrf_exempt` without documented justification
- [ ] WebSocket origin checking is enabled
- [ ] Session fixation protections in place
- [ ] Cookie security flags (HttpOnly, Secure, SameSite)

### 2e. State Management
- [ ] Private state uses `_underscore` prefix convention
- [ ] No QuerySets or model instances stored as public state
- [ ] JIT serialization does not expose sensitive fields
- [ ] `@client_state` does not store secrets

## 3. Integration & Regression Testing

- [ ] Full test suite passes (`make test`)
- [ ] Security-specific tests pass (`pytest tests/security/ -v`)
- [ ] Rust XSS prevention tests pass (`cargo test -p djust_vdom`)
- [ ] No regressions in form validation or file upload handling
- [ ] WebSocket reconnection does not bypass auth

## 4. Penetration Testing Scenarios

- [ ] **XSS via event handler params** -- inject `<script>` in handler arguments, verify escaping
- [ ] **XSS via template variables** -- set state to HTML payloads, verify Rust renderer escapes
- [ ] **CSRF via WebSocket** -- attempt cross-origin WebSocket connection, verify rejection
- [ ] **Parameter injection** -- send unexpected types/values to event handlers, verify type coercion
- [ ] **State enumeration** -- attempt to read `_private` state from client, verify inaccessible
- [ ] **File upload abuse** -- upload oversized files, executables, path traversal names
- [ ] **URL injection** -- manipulate `dj-navigate` targets with `javascript:` or external URLs

## 5. Configuration Review

- [ ] `DJUST_CONFIG` settings are secure defaults
- [ ] Debug mode is disabled in production configuration
- [ ] WebSocket allowed origins are restrictive
- [ ] Rate limiting is configured for event handlers
- [ ] File upload size limits are set
- [ ] CORS headers are appropriate

## 6. Documentation

- [ ] CHANGELOG.md updated with security-relevant changes
- [ ] SECURITY_GUIDELINES.md reflects current architecture
- [ ] Any new security-sensitive APIs are documented with warnings
- [ ] Migration guide includes security notes (if breaking changes)

## 7. Sign-Off

| Role | Name | Date | Approved |
|------|------|------|----------|
| Lead Developer | | | [ ] |
| Security Reviewer | | | [ ] |
| Release Manager | | | [ ] |

### Release Decision

- [ ] **APPROVED** -- all items checked, no unresolved findings
- [ ] **APPROVED WITH EXCEPTIONS** -- exceptions documented below
- [ ] **BLOCKED** -- unresolved findings require attention before release

#### Exceptions / Notes

<!-- Document any accepted risks, deferred items, or additional context here -->
