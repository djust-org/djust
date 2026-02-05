# Pull Request Review Checklist

This document outlines the mandatory checks that must be evaluated when reviewing any Pull Request to djust.

## 🔍 Pre-Review Quick Checks

- [ ] **PR Title** follows format: `[type]: brief description` (e.g., `feat:`, `fix:`, `docs:`, `refactor:`)
- [ ] **PR Description** includes purpose, changes, and testing approach
- [ ] **Breaking Changes** are clearly identified and documented
- [ ] **Linked Issues** are referenced (if applicable)
- [ ] **Target Branch** is correct (typically `main` for releases)

## 🧪 Testing Requirements

### Test Coverage
- [ ] **New features have tests** - All new functionality must include unit/integration tests
- [ ] **Bug fixes have regression tests** - Prevent the same issue from recurring
- [ ] **Test coverage maintained** - No significant decrease in overall test coverage
- [ ] **Edge cases covered** - Tests include error conditions and boundary cases
- [ ] **Existing tests pass** - All pre-existing tests continue to pass

### Test Quality
- [ ] **Tests are deterministic** - No flaky or randomly failing tests
- [ ] **Test names are descriptive** - Clear what is being tested and expected outcome
- [ ] **Test isolation** - Tests don't depend on other tests or external state
- [ ] **Performance tests** for features affecting rendering/VDOM (if applicable)

## 💻 Code Quality

### Architecture & Design
- [ ] **Follows djust patterns** - Consistent with existing codebase architecture
- [ ] **Single responsibility** - Functions and classes have clear, focused purposes
- [ ] **Appropriate abstractions** - No over-engineering or under-engineering
- [ ] **No code duplication** - Shared logic is properly abstracted
- [ ] **Separation of concerns** - UI, business logic, and data access properly separated

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
- [ ] **No console.log** in production code - Use proper logging/debug tools
- [ ] **Browser compatibility** maintained for supported versions
- [ ] **Security considerations** - No XSS vulnerabilities, proper sanitization

## 🚨 Exception Handling Requirements

**MANDATORY: All exceptions must use the existing djust logging system**

### Python Exception Handling
- [ ] **Use djust logger** instead of print statements or bare exceptions:
```python
import logging
logger = logging.getLogger(__name__)

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
- [ ] **CHANGELOG.md updated** - All changes properly categorized
- [ ] **API references updated** - If public API changes

### Developer Documentation
- [ ] **Architecture decisions documented** - Design rationale explained
- [ ] **Performance implications noted** - If changes affect performance
- [ ] **Security considerations documented** - For security-sensitive changes

## 🔒 Security Review

### Input Validation
- [ ] **User input validated** - All user data is properly sanitized/validated
- [ ] **SQL injection prevention** - Proper use of parameterized queries
- [ ] **XSS prevention** - Template output is properly escaped
- [ ] **CSRF protection** maintained - No bypass of Django's CSRF protection

### Access Control
- [ ] **Authentication required** where appropriate
- [ ] **Authorization enforced** - Users can only access allowed resources
- [ ] **Tenant isolation maintained** - Multi-tenant security not compromised
- [ ] **Rate limiting** considered for new endpoints

### Data Protection
- [ ] **Sensitive data handling** - No passwords/keys in logs or responses
- [ ] **Privacy considerations** - PII handling follows regulations
- [ ] **Secure defaults** - New features are secure by default

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

## 🚫 Common Rejection Reasons

**Auto-reject if:**
- Uses `print()` statements instead of logging system
- Introduces silent exception handling (`except: pass`)
- No tests for new functionality
- Breaks existing functionality
- Security vulnerabilities identified
- No documentation for user-facing changes

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

---

**Remember:** This checklist ensures djust maintains high quality, security, and performance standards. Every item serves a purpose in creating reliable, maintainable software.