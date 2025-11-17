# Development Process

**Project**: djust (Python/Rust Hybrid Framework)
**Last Updated**: 2025-11-16
**Status**: Living Document

## Table of Contents

1. [Overview](#overview)
2. [Pre-Implementation Planning](#pre-implementation-planning)
3. [Phase Implementation Process](#phase-implementation-process)
4. [Quality Assurance Checklist](#quality-assurance-checklist)
5. [PR Review & Iteration](#pr-review--iteration)
6. [Merge & Cleanup](#merge--cleanup)
7. [Common Pitfalls](#common-pitfalls)
8. [Language-Specific Guidelines](#language-specific-guidelines)
9. [Quick Reference Checklists](#quick-reference-checklists)

---

## Overview

This document outlines the development process for implementing features in djust, a Python/Rust hybrid framework. The process emphasizes:

- **Phased implementation** for manageable complexity
- **Test-first mindset** for quality assurance
- **Comprehensive documentation** for maintainability
- **Pre-commit hygiene** for clean commits
- **Security and best practices** at every step

### Process Philosophy

- **Build incrementally**: Small, focused phases are easier to review and debug
- **Test continuously**: Don't wait until the end - test as you build
- **Document as you go**: Documentation written during implementation is more accurate
- **Fail fast**: Run pre-commit checks before every commit, not just before PR
- **Self-review first**: Review your own changes before asking others

---

## Pre-Implementation Planning

### 1. Feature Analysis

Before writing any code:

```markdown
□ Read and understand the feature request
□ Identify affected components (Python, Rust, templates, docs)
□ Check for existing related code
□ Review relevant architecture documentation
□ Identify potential security implications
□ List dependencies and constraints
```

### 2. Break Into Phases

For complex features, break into logical phases:

**Example: JIT Auto-Serialization (6 phases)**
1. Template variable extraction (Foundation)
2. Query optimizer (Core logic)
3. Serializer code generation (Code generation)
4. LiveView integration (Integration)
5. Caching infrastructure (Performance)
6. Testing & documentation (Quality)

**Phase Criteria:**
- ✅ Each phase delivers working, testable functionality
- ✅ Each phase can be reviewed independently
- ✅ Each phase is ~2-5 days of work (not months)
- ✅ Dependencies are clear (Phase N depends on Phase N-1)

### 3. Create Master Plan Document

For multi-phase features, create comprehensive planning docs:

```bash
docs/features/FEATURE_NAME_ARCHITECTURE.md  # System design
docs/features/FEATURE_NAME_IMPLEMENTATION.md  # Implementation plan
docs/features/FEATURE_NAME_API.md  # API documentation
docs/features/FEATURE_NAME_PERFORMANCE.md  # Performance requirements
```

**Example from JIT Auto-Serialization:**
- `ORM_JIT_ARCHITECTURE.md` (32KB) - Technical design
- `ORM_JIT_IMPLEMENTATION.md` (50KB) - Phase-by-phase plan
- `ORM_JIT_API.md` (28KB) - API documentation
- `ORM_JIT_PERFORMANCE.md` (25KB) - Benchmarks and metrics

### 4. Set Success Criteria

Define "done" before you start:

```markdown
□ Functionality works as designed
□ All tests pass (unit + integration + edge cases)
□ Performance meets requirements (benchmarks if applicable)
□ Documentation complete (API + usage + debugging)
□ Security review passed
□ Code review feedback addressed
□ Pre-commit hooks pass
□ No accidental commits (__pycache__, build artifacts, etc.)
```

---

## Phase Implementation Process

### Step 1: Branch Setup

```bash
# Update main first
git checkout main
git pull origin main

# Create feature branch
git checkout -b feature/descriptive-name

# Example branch names:
# feature/jit-auto-serialization
# feature/websocket-reconnect
# fix/vdom-input-preservation
# docs/api-reference-update
```

**Branch Naming Convention:**
- `feature/` - New functionality
- `fix/` - Bug fixes
- `docs/` - Documentation only
- `refactor/` - Code reorganization
- `perf/` - Performance improvements
- `test/` - Test additions/fixes

### Step 2: Setup Progress Tracking

Use TodoWrite to track phase tasks:

```python
# Example from Phase 1
todos = [
    {"content": "Implement Rust template variable extraction", "status": "in_progress"},
    {"content": "Add PyO3 Python binding", "status": "pending"},
    {"content": "Write comprehensive tests", "status": "pending"},
    {"content": "Add performance benchmarks", "status": "pending"},
    {"content": "Update documentation", "status": "pending"},
]
```

**Update throughout implementation:**
- Mark tasks as `in_progress` when starting
- Mark as `completed` immediately after finishing
- Add new tasks as you discover them
- Keep only ONE task `in_progress` at a time

### Step 3: Implementation (Test-Driven)

**⚠️ CRITICAL: Test as you build, not after!**

```markdown
1. Write skeleton/interface
2. Write basic tests
3. Implement core logic
4. Run tests (should pass)
5. Add edge case tests
6. Fix issues found
7. Repeat for next component
```

**Example Flow (from our session):**
```bash
# 1. Implement core function
vim crates/djust_templates/src/parser.rs
cargo test -p djust_templates  # Run tests frequently

# 2. Add Python binding
vim crates/djust_live/src/lib.rs
make dev-build  # Build after each change

# 3. Test immediately
.venv/bin/python -c "from djust._rust import extract_template_variables; print(...)"

# 4. Write comprehensive tests
vim python/tests/test_template_variable_extraction.py
pytest python/tests/  # Verify tests pass

# 5. Add benchmarks
vim crates/djust_templates/benches/variable_extraction.rs
cargo bench --bench variable_extraction
```

### Step 4: Pre-Commit Checks (Before EVERY Commit)

**Run these BEFORE staging files:**

```bash
# Rust checks
cargo fmt --all
cargo clippy --all-targets --all-features -- -D warnings
cargo test --all

# Python checks (if Python files changed)
ruff check python/
ruff format python/

# Run full pre-commit suite
pre-commit run --all-files
```

**If ANY check fails:**
- ❌ DO NOT commit
- ✅ Fix the issues
- ✅ Re-run checks
- ✅ Only commit when all pass

### Step 5: Self-Review Changes

**Before committing, review your own changes:**

```bash
# See what you're about to commit
git status
git diff

# Check for unwanted files
git status --short | grep -E "(__pycache__|\.pyc|\.DS_Store|\.env|\.log|target/)"
```

**Red Flags:**
- ❌ `__pycache__/` directories
- ❌ `.pyc` files
- ❌ `.DS_Store` (macOS)
- ❌ `.env` or secrets files
- ❌ `target/` directory (Rust build)
- ❌ Build artifacts
- ❌ Test databases
- ❌ Log files

**If found:**
```bash
# Remove from staging
git reset HEAD <unwanted-file>

# Or remove from tracking entirely
git rm -r --cached <unwanted-path>

# Add to .gitignore if not already there
echo "__pycache__/" >> .gitignore
```

### Step 6: Commit with Meaningful Messages

**Commit Message Format:**

```
<type>(<scope>): <subject>

<body>

<footer>
```

**Types:**
- `feat`: New feature
- `fix`: Bug fix
- `docs`: Documentation only
- `test`: Adding/updating tests
- `refactor`: Code restructuring
- `perf`: Performance improvement
- `chore`: Maintenance (dependencies, cleanup)

**Example:**
```
feat(jit): Phase 1 - Add Rust template variable extraction

Implemented extract_template_variables function that:
- Parses Django templates via lexer/parser
- Extracts variable access patterns (e.g., user.profile.name)
- Handles all template syntax (for/if/with/block tags)
- Returns deduplicated, sorted paths

Performance: <5ms for typical templates
Tests: 26 Rust tests added (all passing)
```

**Bad Examples:**
```
fix stuff                              ❌ Too vague
WIP                                    ❌ Not descriptive
updated files                          ❌ What changed?
asdf                                   ❌ Meaningless
```

### Step 7: Comprehensive Testing

**Test Pyramid:**
```
       /\
      /  \  Edge Cases (19 tests)
     /____\
    /      \  Integration (34 tests)
   /________\
  /          \  Unit Tests (104 tests)
 /____________\
```

**Testing Checklist:**

```markdown
□ Unit tests for each function/method
□ Integration tests for component interaction
□ Edge cases (empty input, malformed, boundary conditions)
□ Performance benchmarks (if applicable)
□ Error handling tests
□ Regression tests (for bugs found)
```

**Example from Phase 1:**
```bash
# Rust tests (104 total)
cargo test -p djust_templates --lib

# Python tests (53 total)
pytest python/tests/test_template_variable_extraction.py
pytest python/tests/test_template_edge_cases.py

# Benchmarks
cargo bench --bench variable_extraction

# Test summary
✅ 104 Rust tests pass
✅ 53 Python tests pass
✅ All benchmarks run successfully
```

### Step 8: Documentation (During Implementation)

**Document as you code, not after:**

```markdown
□ Add docstrings to functions (with examples)
□ Update API documentation
□ Add usage examples to docs/
□ Document known limitations
□ Add debug/troubleshooting guide
□ Update README if needed
□ Add migration notes (if breaking changes)
```

**Documentation Locations:**
```
crates/*/src/*.rs          # Rust docstrings (///)
python/djust/*.py          # Python docstrings (""")
docs/                      # User-facing documentation
README.md                  # Quick start + overview
CONTRIBUTING.md            # Developer guide
```

**Example Docstring (Rust):**
```rust
/// Extract all variable paths from a Django template for JIT serialization.
///
/// # Behavior
///
/// - **Empty templates**: Returns empty HashMap
/// - **Malformed templates**: Returns error
/// - **Duplicates**: Automatically deduplicated and sorted
///
/// # Performance
///
/// Typically <5ms for standard templates. See benchmarks for details.
///
/// # Example
///
/// ```rust
/// let vars = extract_template_variables("{{ user.email }}")?;
/// assert_eq!(vars.get("user").unwrap(), &vec!["email".to_string()]);
/// ```
```

### Step 9: Create Pull Request

**Before pushing:**

```bash
# Final checks
cargo test --all
pytest python/tests/
pre-commit run --all-files

# Review ALL changed files
git status
git diff main...HEAD

# Check for unwanted files (IMPORTANT!)
git status --short | grep -vE "^[MADRCU] "

# Push to remote
git push origin feature/branch-name
```

**PR Description Template:**

```markdown
## Summary

[Brief description of what this PR does]

## What's New

- Feature 1
- Feature 2
- Feature 3

## Implementation Details

[Technical details, design decisions, tradeoffs]

## Testing

- ✅ X Rust tests (all passing)
- ✅ Y Python tests (all passing)
- ✅ Benchmarks: [performance metrics]

## Documentation

- Updated: [list files]
- Added: [list files]

## Breaking Changes

[None / List any breaking changes]

## Checklist

- [ ] All tests pass
- [ ] Pre-commit hooks pass
- [ ] Documentation updated
- [ ] No accidental commits
- [ ] Benchmarks run (if applicable)
- [ ] Security implications reviewed
```

**After creating PR:**
```bash
# Save PR for review (your custom command?)
/review-save PR#
```

---

## Quality Assurance Checklist

### Code Quality

```markdown
□ Follows Rust style guide (rustfmt)
□ Follows Python style guide (PEP 8, ruff)
□ No compiler warnings (Rust: clippy -D warnings)
□ No linter warnings (Python: ruff check)
□ Functions are documented with docstrings
□ Complex logic has explanatory comments
□ No commented-out code (unless with TODO + explanation)
□ No debug print statements (use logging)
□ Error messages are helpful
□ Variable names are descriptive
```

### Testing Quality

```markdown
□ Test coverage for all new code
□ Edge cases covered (empty, null, boundary)
□ Error conditions tested
□ Integration tests for cross-component interaction
□ Performance tests (if performance-critical)
□ Tests are independent (don't depend on order)
□ Tests are deterministic (no random failures)
□ Test names clearly describe what they test
```

### Security

```markdown
□ No hardcoded secrets (API keys, passwords)
□ Input validation for user-provided data
□ SQL injection prevention (parameterized queries)
□ XSS prevention (template escaping)
□ CSRF protection (for forms)
□ Dependencies reviewed for vulnerabilities
□ No sensitive data in logs
□ Error messages don't leak sensitive info
```

### Performance

```markdown
□ No N+1 queries (use select_related/prefetch_related)
□ Database queries are optimized
□ Large datasets handled efficiently
□ No memory leaks
□ Benchmarks show acceptable performance
□ No blocking operations in async code
```

### Documentation Quality

```markdown
□ API documentation complete
□ Usage examples provided
□ Known limitations documented
□ Migration guide (if breaking changes)
□ Debug/troubleshooting section
□ Inline code comments for complex logic
□ README updated (if needed)
```

---

## PR Review & Iteration

### Receiving Feedback

**When you receive PR review comments:**

1. **Read all comments fully** before responding
2. **Categorize by priority**:
   - 🔴 **Blocking**: Must fix before merge (security, bugs, breaking changes)
   - 🟡 **Important**: Should address (design issues, missing tests)
   - 🟢 **Nice-to-have**: Can defer (polish, optimizations)

3. **Create action plan**:
   ```markdown
   ## Review Response Plan

   ### Blocking Issues (must fix)
   - [ ] Security: Input validation missing
   - [ ] Bug: Edge case causes panic

   ### Important (should fix)
   - [ ] Add tests for edge cases
   - [ ] Improve error messages

   ### Nice-to-have (can defer)
   - [ ] Extract magic strings to constants
   - [ ] Refactor test helpers
   ```

4. **Respond to each comment**:
   - ✅ Acknowledge the feedback
   - ✅ Explain your fix OR reasoning
   - ✅ Link to commit if fixed
   - ✅ Ask clarifying questions if unclear

### Addressing Comments

**Example from our session:**

**Comment 1: "Missing documentation for graceful fallback"**
```markdown
Response: "Good catch! Added comprehensive docstrings documenting:
- Empty template → returns empty dict
- Malformed template → raises ValueError
- Duplicate paths → auto-deduplicated

See commit 7b92bbf"
```

**Comment 2: "Expression parsing may fail on string literals with dots"**
```markdown
Response: "Added 19 edge case tests to verify behavior:
- 17/19 pass as expected
- 2 known limitations documented (string literals in if conditions)
- Impact: Low - false positives are harmless
- Will fix in Phase 2 with full expression grammar

See commits cf8165a (tests) and 3f4badb (docs)"
```

**Comment 3: "Add performance benchmarks"**
```markdown
Response: "Added criterion benchmarks covering:
- Simple/nested variables (257ns - 2.7µs)
- Template tags, real-world templates
- Large templates (10-200 iterations)
- All results meet <5ms requirement

See commit cf8165a"
```

### Pushing Updates

```bash
# Make changes based on feedback
vim file.rs

# Run tests
cargo test

# Pre-commit checks
cargo fmt --all
cargo clippy --all-targets --all-features -- -D warnings
pre-commit run --all-files

# Commit with descriptive message
git add -A
git commit -m "docs: Address PR review comments

1. Added comprehensive docstrings
2. Added 19 edge case tests
3. Added performance benchmarks
..."

# Push updates
git push origin feature/branch-name

# Respond to comments (your custom command?)
/respond PR#
```

---

## Merge & Cleanup

### Pre-Merge Final Checks

```bash
# Ensure branch is up-to-date with main
git fetch origin
git rebase origin/main  # Or merge if preferred

# Final test run
cargo test --all
pytest python/tests/
pre-commit run --all-files

# Final review of changes
git log main..HEAD --oneline
git diff main...HEAD --stat

# Check for unwanted files one last time
git diff main...HEAD --name-only | grep -E "(__pycache__|\.pyc|\.DS_Store)"
```

**If unwanted files found:**
```bash
git rm -r --cached <path>
git commit -m "chore: Remove <unwanted> from tracking"
git push origin feature/branch-name
```

### Merge Process

```bash
# After PR approval, merge via GitHub UI or:
gh pr merge <PR#> --squash  # Squash commits
# OR
gh pr merge <PR#> --merge   # Keep commit history

# Delete remote branch (optional)
git push origin --delete feature/branch-name

# Update local main
git checkout main
git pull origin main

# Delete local feature branch
git branch -d feature/branch-name

# Run cleanup command (your custom command?)
/compact
```

### Post-Merge

```markdown
□ Verify CI/CD passes on main
□ Update project board/issue tracker
□ Notify stakeholders (if applicable)
□ Monitor for any issues in production
□ Update changelog (if maintained)
```

---

## Common Pitfalls

### ❌ Don't Do This

1. **Committing without pre-commit checks**
   ```bash
   git commit -m "fix stuff"  # ❌ No checks run
   ```
   **Instead:**
   ```bash
   cargo fmt && cargo clippy
   git commit -m "fix: Correct template parsing logic"
   ```

2. **Writing tests after PR creation**
   ```
   Day 1: Write code, create PR
   Day 2: Reviewer: "Where are the tests?"
   Day 3: Add tests  # ❌ Should have been Day 1
   ```
   **Instead:**
   - Write tests during implementation
   - Create PR only when tests pass

3. **Committing __pycache__ or build artifacts**
   ```bash
   git add -A  # ❌ Adds everything including cache
   git commit
   ```
   **Instead:**
   ```bash
   git status  # Review what's being added
   git add specific-files  # Be selective
   ```

4. **Vague commit messages**
   ```bash
   git commit -m "updates"  # ❌ What changed?
   git commit -m "WIP"      # ❌ Not descriptive
   ```
   **Instead:**
   ```bash
   git commit -m "feat(parser): Add support for nested template blocks"
   ```

5. **Skipping documentation**
   ```python
   def extract_vars(tmpl):  # ❌ No docstring
       return parse(tmpl)
   ```
   **Instead:**
   ```python
   def extract_vars(tmpl: str) -> Dict[str, List[str]]:
       """Extract variable paths from Django template.

       Args:
           tmpl: Template source string

       Returns:
           Dict mapping variable names to attribute paths

       Example:
           >>> extract_vars("{{ user.email }}")
           {'user': ['email']}
       """
       return parse(tmpl)
   ```

6. **Not testing edge cases**
   ```python
   def test_basic():  # ❌ Only happy path
       result = func("normal input")
       assert result == expected
   ```
   **Instead:**
   ```python
   def test_basic():
       result = func("normal input")
       assert result == expected

   def test_empty_input():
       result = func("")
       assert result == {}

   def test_malformed_input():
       with pytest.raises(ValueError):
           func("{% if x")
   ```

7. **Letting tests fail and moving on**
   ```bash
   cargo test  # 2 tests fail
   # "I'll fix those later"  # ❌ Fix now!
   ```
   **Instead:**
   - Stop immediately when tests fail
   - Fix before continuing
   - Tests should always be green

### ✅ Do This

1. **Test continuously**
   ```bash
   # After each change
   cargo test -p djust_templates
   pytest python/tests/test_my_feature.py
   ```

2. **Commit frequently with good messages**
   ```bash
   git commit -m "feat(parser): Add variable extraction"
   git commit -m "test: Add edge case tests for parser"
   git commit -m "docs: Add parser API documentation"
   ```

3. **Review your own changes first**
   ```bash
   git diff
   git diff --staged
   # Read every line before committing
   ```

4. **Run pre-commit before every commit**
   ```bash
   pre-commit run --all-files
   # Fix any issues
   git commit
   ```

5. **Document as you code**
   ```rust
   /// (Write docstring immediately when creating function)
   pub fn extract_template_variables(...) -> Result<...> {
       // Implementation
   }
   ```

---

## Language-Specific Guidelines

### Rust

**Code Style:**
```bash
# Format code
cargo fmt --all

# Lint code
cargo clippy --all-targets --all-features -- -D warnings

# Fix lints automatically
cargo clippy --fix --allow-dirty --allow-staged
```

**Testing:**
```bash
# Run tests for specific crate
cargo test -p djust_templates

# Run tests with output
cargo test -- --nocapture

# Run specific test
cargo test test_extract_simple_variable

# Run benchmarks
cargo bench --bench variable_extraction
```

**Documentation:**
```rust
/// Function summary (one line).
///
/// Longer description explaining behavior, edge cases, performance.
///
/// # Arguments
///
/// * `template` - Template source string
///
/// # Returns
///
/// HashMap mapping variable names to paths
///
/// # Errors
///
/// Returns `Err` if template cannot be parsed
///
/// # Example
///
/// ```rust
/// let vars = extract_template_variables("{{ user.name }}")?;
/// assert!(vars.contains_key("user"));
/// ```
pub fn extract_template_variables(template: &str) -> Result<HashMap<String, Vec<String>>> {
    // Implementation
}
```

**Common Issues:**
- Unused imports: `cargo clippy` will catch
- Uninlined format args: Use `format!("{var}")` not `format!("{}", var)`
- Missing error handling: Always propagate errors with `?`

### Python

**Code Style:**
```bash
# Format code
ruff format python/

# Lint code
ruff check python/

# Fix lints automatically
ruff check python/ --fix
```

**Testing:**
```bash
# Run all tests
pytest python/tests/

# Run specific test file
pytest python/tests/test_template_extraction.py

# Run with verbose output
pytest python/tests/ -v

# Run with coverage
pytest python/tests/ --cov=djust --cov-report=html
```

**Documentation:**
```python
def extract_template_variables(template: str) -> dict[str, list[str]]:
    """Extract variable paths from Django template.

    Parses the template and returns a mapping of root variable names
    to their access paths. Used for JIT auto-serialization.

    Args:
        template: Template source string

    Returns:
        Dictionary mapping variable names to lists of attribute paths.
        Root variables map to empty lists.

    Raises:
        ValueError: If template cannot be parsed (malformed syntax)

    Example:
        >>> extract_template_variables("{{ user.email }}")
        {'user': ['email']}

        >>> extract_template_variables("")
        {}

    Note:
        - Automatically deduplicates and sorts paths
        - Handles for/if/with/block tags
        - Performance: <5ms for typical templates
    """
    # Implementation
```

**Common Issues:**
- Type hints: Always add for function signatures
- Docstrings: Required for public functions
- f-strings: Use `f"{var}"` not `"{}".format(var)`

---

## Quick Reference Checklists

### Pre-Commit Checklist

```markdown
□ Run cargo fmt --all
□ Run cargo clippy -- -D warnings
□ Run cargo test --all
□ Run ruff check + format (if Python changed)
□ Run pytest (if Python tests changed)
□ Review git status (no unwanted files)
□ Review git diff (all changes intentional)
□ Pre-commit hooks pass
□ Commit message is descriptive
```

### Pre-Push Checklist

```markdown
□ All tests pass (Rust + Python)
□ All pre-commit hooks pass
□ Documentation updated
□ No __pycache__ or build artifacts
□ No secrets or .env files
□ Commit history is clean
□ Branch is up-to-date with main
```

### Pre-PR Checklist

```markdown
□ All tests pass
□ Comprehensive test coverage (unit + integration + edge cases)
□ Benchmarks run (if applicable)
□ Documentation complete (API + usage + debugging)
□ Known limitations documented
□ Pre-commit hooks pass
□ Self-review completed
□ No unwanted files committed
□ PR description is comprehensive
```

### PR Review Response Checklist

```markdown
□ Read all comments thoroughly
□ Categorize by priority (blocking/important/nice-to-have)
□ Create action plan
□ Address blocking issues first
□ Add tests for reported edge cases
□ Update documentation based on feedback
□ Run all checks before pushing updates
□ Respond to each comment with explanation
□ Link commits that fix issues
```

---

## Process Improvement

This process is a **living document**. After each major feature:

```markdown
1. What went well?
2. What could be improved?
3. What mistakes were made?
4. What should be added to this document?
```

**Update this document** with lessons learned!

### Recent Improvements (2025-11-16)

Based on Phase 1 implementation:

- ✅ Added "check for unwanted files" step (learned from __pycache__ incident)
- ✅ Added "test during implementation" emphasis (not after PR)
- ✅ Added "documentation during coding" (not after PR)
- ✅ Added comprehensive pre-commit hygiene steps
- ✅ Added PR review categorization (blocking/important/nice-to-have)

---

## Contact & Questions

If you have questions about this process:

1. Check this document first
2. Review recent PRs for examples
3. Ask in team chat/discussion

**This process will evolve** - contribute improvements as you learn!
