# Pull Request Review Checklist

This document outlines the mandatory checks that must be evaluated when reviewing any Pull Request to djust.

## 🔍 Pre-Review Quick Checks

- [ ] **PR Title** follows format: `[type]: brief description` (e.g., `feat:`, `fix:`, `docs:`, `refactor:`)
- [ ] **PR Description** includes purpose, changes, and testing approach
- [ ] **Breaking Changes** are clearly identified and documented
- [ ] **Linked Issues** are referenced (if applicable)
- [ ] **Target Branch** is correct (typically `main` for releases)
- [ ] **All new files are tracked** — `git status` shows no untracked files that should be part of the PR. Tests must not depend on files absent from the diff

## 🧪 Testing Requirements

### Test Coverage

- [ ] **New features have tests** - All new functionality must include unit/integration tests
- [ ] **Bug fixes have regression tests** - Prevent the same issue from recurring
- [ ] **Test coverage maintained** - No significant decrease in overall test coverage
- [ ] **Edge cases covered** - Tests include error conditions and boundary cases
- [ ] **Existing tests pass** - All pre-existing tests continue to pass
- [ ] **Integration tests pass** - End-to-end verification that features work together, not just unit tests in isolation

### Test Quality

- [ ] **Tests are deterministic** - No flaky or randomly failing tests
- [ ] **Test names are descriptive** - Clear what is being tested and expected outcome
- [ ] **Test isolation** - Tests don't depend on other tests or external state
- [ ] **Test-implementation alignment** - Tests actually import and exercise the code in the PR. No references to phantom modules, renamed tags, or APIs that don't exist in the diff
- [ ] **Import names match shipped code** - Template tag library names, module paths, and class names used in tests match the actual files in the PR diff
- [ ] **Performance tests** for features affecting rendering/VDOM (if applicable)

## 💻 Code Quality

### Architecture & Design

- [ ] **Follows djust patterns** - Consistent with existing codebase architecture
- [ ] **Single responsibility** - Functions and classes have clear, focused purposes
- [ ] **Appropriate abstractions** - No over-engineering or under-engineering
- [ ] **No code duplication** - Shared logic is properly abstracted
- [ ] **Separation of concerns** - UI, business logic, and data access properly separated
- [ ] **No placeholder/stub implementations** - No hardcoded `return True`, simulated APIs, or fake data in shipped code. If a feature isn't ready, mark it clearly as `NotImplementedError` or exclude it from the PR
- [ ] **No simulated/stub code** - Comments containing "simulate", "would be implemented", "in a real implementation", or "for now" indicate incomplete code that should not ship

### Python Code Standards

- [ ] **Type hints** are present for all public APIs and complex functions
- [ ] **Docstrings** follow Django/djust conventions for public methods/classes
- [ ] **Exception handling** uses existing djust logging system (see below)
- [ ] **Django best practices** - Proper use of querysets, managers, etc.
- [ ] **Security considerations** - No SQL injection, XSS, or other vulnerabilities

### Rust Code Standards (if applicable)

- [ ] **cargo fmt** has been run - Code is properly formatted
- [ ] **cargo clippy** warnings addressed - No lint warnings
- [ ] **Error handling** uses appropriate Result types
- [ ] **Memory safety** - No unsafe code without justification
- [ ] **Performance implications** considered for VDOM-affecting changes

### JavaScript Code Standards

- [ ] **ESLint rules** pass - Code follows project style guide
- [ ] **No console.log** in production code without debug guard - All `console.log` calls must be wrapped in `if (globalThis.djustDebug)` guards. Unguarded logging is auto-rejected
- [ ] **Generated JS follows same rules** - Python code that generates JavaScript strings (e.g., service worker generators, template tags) must also avoid `console.log` and use proper escaping
- [ ] **New JS feature files have tests** - Each new file in `static/djust/src/` must have a corresponding test file in `tests/js/`
- [ ] **Browser compatibility** maintained for supported versions
- [ ] **Security considerations** - No XSS vulnerabilities, proper sanitization

## 🚨 Exception Handling Requirements

**MANDATORY: All exceptions must use the existing djust logging system**

### Python Exception Handling

- [ ] **Use djust logger** instead of print statements or bare exceptions:

  ```python
  import logging
  logger = logging.getLogger(__name__)
  ```

# Correct

try:
    risky_operation()
except ValueError as e:
    logger.error("Operation failed: %s", str(e), exc_info=True)
    raise

# Incorrect - Don't do this

except ValueError as e:
    print(f"Error: {e}")  # ❌ No print statements
    pass  # ❌ No silent failures

```
- [ ] **Use `%s` formatting in logger calls, not f-strings** - Django convention; avoids formatting strings that may never be emitted:
```python
# Correct
logger.error("Operation failed for key %s: %s", key, err, exc_info=True)

# Incorrect - f-string is evaluated even when log level is disabled
logger.error(f"Operation failed for key {key}: {err}")
```

- [ ] **Appropriate log levels** used:

  - `logger.error()` - For errors that affect functionality
  - `logger.warning()` - For recoverable issues
  - `logger.info()` - For important operational events
  - `logger.debug()` - For detailed troubleshooting info

- [ ] **Include context** in log messages - User ID, tenant, operation details

- [ ] **Use exc_info=True** for exceptions to capture stack traces

- [ ] **No silent failures** - All exceptions are logged or re-raised appropriately

### JavaScript Exception Handling

- [ ] **Use djust debug system** for client-side errors:

  ```javascript
  // Correct - Use djust error reporting
  window.djust.reportError('Operation failed', error);
  ```

// Incorrect
console.error(error); // ❌ Won't be captured in production

```
## 📖 Documentation Requirements

### Code Documentation
- [ ] **Public APIs documented** - All public methods/classes have docstrings
- [ ] **Complex logic explained** - Non-obvious code has inline comments
- [ ] **Type annotations** serve as documentation for function signatures
- [ ] **Examples provided** for new features or complex APIs

### User Documentation
- [ ] **New features documented** - User-facing changes have guide updates
- [ ] **Breaking changes documented** - Migration path provided
- [ ] **CHANGELOG.md updated** - All changes properly categorized (mandatory for `feat:` and `fix:` PRs)
- [ ] **API references updated** - If public API changes
- [ ] **No internal tracking documents** - Completion checklists, internal status docs (e.g., `*_COMPLETE.md`), and private planning docs must not be committed to the public repo

### Developer Documentation
- [ ] **Architecture decisions documented** - Design rationale explained
- [ ] **Performance implications noted** - If changes affect performance
- [ ] **Security considerations documented** - For security-sensitive changes

## 🔒 Security Review

### Input Validation
- [ ] **User input validated** - All user data is properly sanitized/validated
- [ ] **SQL injection prevention** - Proper use of parameterized queries
- [ ] **XSS prevention** - Template output is properly escaped
- [ ] **No `|safe` on user input** - The `|safe` filter must only be used on server-generated HTML, never on user-controlled values
- [ ] **Fuzz tests pass** - `LiveViewSmokeTest` mixin covers XSS and crash testing for new views
- [ ] **Template tags escape user input** - Any use of `mark_safe()` must escape interpolated values with `django.utils.html.escape()` or `django.utils.html.format_html()`. Never inject raw parameters into HTML or JavaScript strings
- [ ] **JS string context uses `json.dumps()`** - Values embedded in JavaScript strings use `json.dumps()` for escaping, not HTML `escape()` which is insufficient for JS contexts
- [ ] **CSRF protection** maintained - No bypass of Django's CSRF protection
- [ ] **No unjustified `@csrf_exempt`** - New endpoints must not use `@csrf_exempt` without documented justification. Service worker / API endpoints should use token-based auth instead

### Access Control
- [ ] **Authentication required** where appropriate
- [ ] **Authorization enforced** - Users can only access allowed resources
- [ ] **Tenant isolation maintained** - Multi-tenant security not compromised
- [ ] **Rate limiting** considered for new endpoints

### Data Protection
- [ ] **Sensitive data handling** - No passwords/keys in logs or responses
- [ ] **Privacy considerations** - PII handling follows regulations
- [ ] **Secure defaults** - New features are secure by default

### Security Hot Spot Changes

If the PR modifies any file listed in [Security Hot Spot Files](SECURITY_GUIDELINES.md#security-hot-spot-files), the following additional requirements apply:

- [ ] **Hot spot files identified** - PR description lists which hot spot files are changed and why
- [ ] **Security reviewer assigned** - At least one reviewer with security expertise must approve (standard code review alone is not sufficient)
- [ ] **Targeted security tests included** - New or updated tests that exercise the specific security property affected by the change
- [ ] **Fuzz tests pass** - `LiveViewSmokeTest` runs clean if the change affects rendering, event dispatch, or state management
- [ ] **Banned patterns verified** - Confirmed no [banned patterns](SECURITY_GUIDELINES.md#banned-patterns) were introduced
- [ ] **Security documentation updated** - If the change alters security behavior (escaping rules, auth flow, rate limiting), `SECURITY_GUIDELINES.md` is updated

**Hot spot files include:** `websocket.py`, `security.py`, `auth.py`, `decorators.py`, `live_view.py`, `uploads.py`, `config.py`, `routing.py`, `templatetags/`, `tenants/`, `renderer.rs`, `filters.rs`, `djust_vdom/`, `static/djust/`. See [SECURITY_GUIDELINES.md](SECURITY_GUIDELINES.md#security-hot-spot-files) for the full list and rationale.

## ⚡ Performance Review

### Template & VDOM
- [ ] **Template efficiency** - No unnecessary loops or complex operations in templates
- [ ] **VDOM impact** considered - Changes to rendering pipeline are analyzed
- [ ] **Memory usage** - No memory leaks or excessive allocations
- [ ] **Caching appropriate** - Expensive operations are cached when possible

### Database
- [ ] **Query efficiency** - No N+1 queries or unnecessary database hits
- [ ] **Indexes considered** - New filters/ordering have appropriate indexes
- [ ] **Migration safety** - Database migrations are backwards compatible
- [ ] **Connection pooling** respected - No connection leaks

### Client-Side
- [ ] **JavaScript bundle size** - New JS doesn't significantly increase bundle
- [ ] **WebSocket usage** - Efficient use of WebSocket messages
- [ ] **DOM manipulation** - Client-side changes are efficient

## 🌐 Compatibility & Integration

### Browser Support
- [ ] **Supported browsers tested** - Works in Chrome, Firefox, Safari, Edge
- [ ] **Mobile compatibility** - Responsive design maintained
- [ ] **Accessibility** - Screen readers and keyboard navigation work

### Django Integration
- [ ] **Django version compatibility** - Works with supported Django versions
- [ ] **Third-party compatibility** - No conflicts with common Django packages
- [ ] **Settings configuration** - New settings are documented and have sensible defaults

### Deployment
- [ ] **Production readiness** - Code works in production environment
- [ ] **Environment variables** - All required configuration documented
- [ ] **Static files** - Proper handling of CSS/JS assets

## 🔄 Release Considerations

### Versioning
- [ ] **Semantic versioning** followed - Breaking changes increment major version
- [ ] **Feature completeness** - Features are complete, not partial implementations
- [ ] **Backwards compatibility** maintained unless major version change

### Migration Path
- [ ] **Upgrade instructions** provided for breaking changes
- [ ] **Deprecation warnings** for features being removed
- [ ] **Feature flags** used for gradual rollout (if appropriate)

## 📝 Review Process

### Initial Review
1. **Automated checks pass** - CI/CD pipeline is green
2. **Manual testing performed** - Reviewer has tested the changes locally
3. **Code walkthrough** - Complex changes are understood by reviewer
4. **Documentation reviewed** - All docs are accurate and complete

### Approval Criteria
- [ ] **All checklist items pass** - No unresolved issues remain
- [ ] **No unaddressed feedback** - All review comments are resolved
- [ ] **Test suite passes** - Full test suite runs without failures
- [ ] **Performance regression** - No significant performance degradation

### Final Steps
- [ ] **Squash commits** if needed - Clean commit history for main branch
- [ ] **Update branch** - Latest main branch merged if needed
- [ ] **Final CI check** - All automated tests pass one final time
- [ ] **CLAUDE.md updates suggested** - If the PR introduces new patterns, modules, security rules, or conventions that should be reflected in `CLAUDE.md`, suggest specific changes in the review feedback

## 🚫 Common Rejection Reasons

**Auto-reject if:**
- Uses `print()` statements instead of logging system
- Uses f-string formatting in logger calls instead of `%s` style
- `console.log` in production JS without `if (globalThis.djustDebug)` guard
- Introduces silent exception handling (`except: pass`)
- No tests for new functionality (Python or JavaScript)
- New JS feature files in `static/djust/src/` without corresponding test files in `tests/js/`
- Tests reference modules/APIs that don't exist in the PR
- Breaks existing functionality
- Security vulnerabilities identified (XSS, CSRF bypass, injection)
- `mark_safe()` used with unescaped interpolated values
- `|safe` template filter used on user-controlled variables (bypasses Rust auto-escaping)
- `@csrf_exempt` on endpoints without documented justification
- No documentation for user-facing changes
- CHANGELOG.md not updated for `feat:` or `fix:` PRs
- Internal tracking documents (e.g., `*_COMPLETE.md`) included in public repo
- Placeholder/stub implementations shipped as production code (e.g., `return True`, hardcoded fake data)
- Untracked files that tests depend on are missing from the PR
- Comments indicate incomplete code ("simulate", "would be implemented", "for now")
- Changes to [security hot spot files](SECURITY_GUIDELINES.md#security-hot-spot-files) without a security-qualified reviewer approval
- Hot spot file changes without targeted security tests covering the affected property

## 📋 Reviewer Responsibility

As a reviewer, you must:
- [ ] **Test the changes** locally in multiple scenarios
- [ ] **Verify all checklist items** - Don't approve incomplete reviews
- [ ] **Provide constructive feedback** - Help improve code quality
- [ ] **Ask questions** if anything is unclear
- [ ] **Consider long-term maintainability** - Not just immediate functionality

## 📞 When to Escalate

Escalate to project maintainers when:
- **Architecture changes** affect multiple components
- **Performance implications** are significant
- **Security concerns** are complex
- **Breaking changes** affect many users
- **Disagreement** between reviewer and author cannot be resolved

## 📁 Review Feedback

All PR review feedback must be saved to `pr/feedback/` for traceability:

- **File naming**: `pr-{number}-{short-description}.md` (e.g., `pr-235-v0.3.0-phoenix-rising.md`)
- **Contents**: Checklist evaluation, issues found, verdict (APPROVED / CHANGES REQUESTED), any checklist improvement suggestions, and any suggested updates to `CLAUDE.md` (e.g., new rules, project structure changes, updated patterns)
- **When**: After completing a review, before or alongside posting comments on the PR itself

This ensures review decisions are durable, searchable, and not lost in GitHub comment threads.

---

**Remember:** This checklist ensures djust maintains high quality, security, and performance standards. Every item serves a purpose in creating reliable, maintainable software.
```
