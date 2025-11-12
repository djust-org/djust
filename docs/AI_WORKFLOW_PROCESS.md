# AI Workflow Process

This document captures the workflow process used when working with AI assistants (Claude Code) on the djust project. It serves as a guide for maintaining consistency, quality, and efficiency in AI-assisted development.

## Overview

Our AI workflow follows an iterative, test-driven approach with strong emphasis on documentation, planning, and quality improvements. The process is designed to produce production-ready code while maintaining high standards for testing and documentation.

---

## Workflow Phases

### 1. **Planning & Task Breakdown**

**Purpose**: Break down complex tasks into manageable, trackable units of work.

**Process**:
1. **Create tracking document** (e.g., `IMPLEMENTATION_PHASE1.md`)
   - List all tasks with time estimates
   - Define success criteria
   - Specify deliverables
   - Include test requirements

2. **Use TodoWrite tool** for real-time tracking
   - Create todos at start of session
   - Mark as `in_progress` before starting work
   - Mark as `completed` immediately after finishing (don't batch)
   - Keep ONE task `in_progress` at a time

3. **Time estimates**
   - Provide realistic estimates for each task
   - Track actual vs estimated time
   - Learn from variances for future planning

**Example from Phase 1:**
```markdown
### Task Breakdown

1. Update @debounce and @throttle decorators (30 min)
2. Add @optimistic decorator (15 min)
3. Add @cache decorator (20 min)
4. Add @client_state decorator (20 min)
5. Implement metadata extraction (30 min)
6. Update render() method (45 min)
7. Add comprehensive tests (1.5 hrs)
8. Run and verify (30 min)

**Total Estimated**: 4.5 hours
**Actual**: 2 hours (56% under budget)
```

---

### 2. **Implementation**

**Purpose**: Write clean, well-tested code that meets requirements.

**Process**:

1. **Start with tests** (TDD approach)
   - Write tests first when possible
   - Define expected behavior clearly
   - Ensure tests are comprehensive

2. **Write implementation**
   - Follow existing code patterns
   - Add type hints (Python)
   - Include docstrings with examples
   - Add inline comments for complex logic

3. **Run tests frequently**
   - Run tests after each significant change
   - Verify 100% pass rate before moving on
   - Add tests for edge cases discovered during implementation

4. **Update tracking**
   - Mark tasks completed as you finish them
   - Update time estimates
   - Note any blockers or challenges

**Code Quality Standards**:
- ✅ Type hints on all function signatures
- ✅ Docstrings with examples
- ✅ Inline comments for non-obvious logic
- ✅ Follow PEP 8 (Python) / project style guide
- ✅ No TODOs or FIXMEs in committed code
- ✅ All tests passing

---

### 3. **Testing**

**Purpose**: Ensure code works correctly and prevent regressions.

**Testing Strategy**:

1. **Unit Tests**
   - Test individual functions/methods
   - Cover happy path and edge cases
   - Test error conditions
   - Aim for 100% coverage of new code

2. **Integration Tests**
   - Test interactions between components
   - Verify end-to-end workflows
   - Test with realistic data

3. **Test Organization**
   - Group tests by functionality (TestDecoratorMetadata, TestMetadataExtraction, etc.)
   - Use descriptive test names (`test_debounce_metadata`, not `test1`)
   - Include docstrings explaining what's being tested

**Example from Phase 1:**
```python
class TestDecoratorMetadata:
    """Test decorator metadata attachment."""

    def test_debounce_metadata(self):
        """Test @debounce attaches correct metadata."""
        @debounce(wait=0.5)
        def handler(self, **kwargs):
            pass

        assert hasattr(handler, '_djust_decorators')
        assert 'debounce' in handler._djust_decorators
        assert handler._djust_decorators['debounce'] == {
            'wait': 0.5,
            'max_wait': None
        }
```

**Test Requirements**:
- ✅ All tests pass (100%)
- ✅ No warnings (or document why warnings are acceptable)
- ✅ Fast execution (< 1 second for unit tests)
- ✅ Deterministic (no flaky tests)
- ✅ Independent (tests don't depend on each other)

---

### 4. **Code Review & Iteration**

**Purpose**: Improve code quality through feedback and iteration.

**Review Process**:

1. **Initial Implementation Complete**
   - All tests passing
   - Documentation complete
   - Ready for review

2. **Review Feedback**
   - Address all feedback items
   - Categorize as "blockers" vs "nice-to-have"
   - Create plan for addressing each item

3. **Iterative Improvements**
   - Implement improvements
   - Add tests for new functionality
   - Update documentation
   - Re-run all tests

**Example from Phase 1:**

**Review Feedback Received:**
1. Caching metadata extraction (Performance)
2. Adding type hints (Code Quality)
3. Adding debug logging (Debuggability)

**Response:**
- Implemented all three in ~30 minutes
- Added test for caching behavior
- Updated tracking document
- All tests still passing (25/25)

---

### 5. **Documentation**

**Purpose**: Make code understandable and maintainable.

**Documentation Types**:

1. **Code Documentation**
   - Docstrings for all public functions/classes
   - Inline comments for complex logic
   - Type hints for all parameters and return values

2. **User Documentation**
   - README updates for new features
   - Tutorial/guide additions
   - API reference updates

3. **Implementation Documentation**
   - Tracking documents (e.g., IMPLEMENTATION_PHASE1.md)
   - Architecture documents
   - Migration guides

4. **Process Documentation**
   - This document (AI_WORKFLOW_PROCESS.md)
   - DEFINITION_OF_DONE.md
   - Decision records

**Documentation Standards**:
- ✅ Examples for all new features
- ✅ Clear, concise language
- ✅ Up-to-date with code changes
- ✅ Properly formatted (Markdown, etc.)
- ✅ Cross-references where helpful

---

### 6. **Commit & PR Management**

**Purpose**: Maintain clean git history and clear communication.

**Commit Guidelines**:

1. **Commit Message Format**
   ```
   <type>(<scope>): <subject>

   <body>

   🤖 Generated with [Claude Code](https://claude.com/claude-code)

   Co-Authored-By: Claude <noreply@anthropic.com>
   ```

2. **Commit Types**
   - `feat`: New feature
   - `fix`: Bug fix
   - `docs`: Documentation only
   - `refactor`: Code refactoring
   - `test`: Adding tests
   - `perf`: Performance improvement

3. **Commit Scope**
   - Component name (e.g., `decorators`, `live_view`)
   - Phase name (e.g., `phase1`)
   - Feature name (e.g., `metadata-extraction`)

4. **Commit Frequency**
   - Commit logical units of work
   - Don't batch unrelated changes
   - Each commit should be reviewable independently

**PR Guidelines**:

1. **PR Title**
   - Descriptive and concise
   - Start with type (feat, fix, docs, etc.)
   - Example: `feat: State Management Documentation Suite + Phase 1 Implementation`

2. **PR Description**
   - Clear summary of changes
   - Link to related issues/PRs
   - Include test results
   - List commits
   - Show before/after examples

3. **PR Size**
   - Prefer smaller, focused PRs when possible
   - Large PRs should be well-documented
   - Consider breaking into multiple PRs if too large

---

## Best Practices

### General Principles

1. **Test-Driven Development**
   - Write tests first when possible
   - Aim for 100% coverage of new code
   - All tests must pass before committing

2. **Incremental Progress**
   - Make small, verifiable changes
   - Commit frequently
   - Mark tasks completed as you finish them

3. **Quality Over Speed**
   - Don't rush to meet estimates
   - Take time to do it right
   - Refactor as you go

4. **Documentation is Code**
   - Document as you build
   - Keep docs in sync with code
   - Examples are essential

5. **Communicate Clearly**
   - Update tracking documents
   - Write clear commit messages
   - Explain decisions in code comments

### AI-Specific Practices

1. **Context Management**
   - Reference relevant files/sections
   - Provide enough context for AI to understand
   - Use tracking documents to maintain state

2. **Verification**
   - Always verify AI-generated code
   - Run tests after each change
   - Check for logic errors

3. **Iterative Refinement**
   - Start with working code
   - Iterate to improve quality
   - Address feedback promptly

---

## Example Workflow: Phase 1 Implementation

This is a real example from our Phase 1 state management implementation:

### 1. Planning (15 min)
- Created `IMPLEMENTATION_PHASE1.md` tracking document
- Broke down tasks with time estimates
- Defined success criteria
- Listed test requirements

### 2. Implementation (2 hrs)
- Updated @debounce and @throttle decorators
- Added @optimistic, @cache, @client_state decorators
- Implemented metadata extraction in LiveView
- Updated render() method to inject metadata
- Wrote 24 comprehensive tests

### 3. Testing (included in implementation)
- All 24 tests passing
- 100% pass rate
- Test results: `24 passed, 3 warnings in 0.32s`

### 4. Review & Iteration (30 min)
- Received 3 "nice-to-have" suggestions
- Implemented metadata caching
- Added comprehensive type hints
- Added debug logging
- Added 1 more test (total: 25)
- All tests still passing

### 5. Documentation (included throughout)
- Updated IMPLEMENTATION_PHASE1.md with completion status
- Added post-completion improvements section
- Documented all changes in commit messages

### 6. PR Creation
- Created comprehensive PR description
- Listed all changes, improvements, test results
- Linked to related PRs
- Included example code

**Total Time**: 2.5 hours (2 hrs + 0.5 hrs improvements)
**Estimated**: 4.5 hours
**Result**: 56% under budget, 100% tests passing

---

## Common Patterns

### Pattern: Iterative Improvement

**Scenario**: After initial implementation, receive feedback for improvements.

**Process**:
1. Categorize feedback (blocker vs nice-to-have)
2. Create plan for addressing each item
3. Implement improvements one at a time
4. Add tests for new functionality
5. Update documentation
6. Verify all tests still pass

**Example**: Phase 1 post-completion improvements
- Metadata caching (performance)
- Type hints (code quality)
- Debug logging (debuggability)

### Pattern: Test-Driven Feature Addition

**Scenario**: Adding a new feature with clear requirements.

**Process**:
1. Write tests defining expected behavior
2. Run tests (they should fail)
3. Implement feature
4. Run tests (they should pass)
5. Refactor if needed
6. Document the feature

**Example**: Adding `@cache` decorator
1. Wrote `test_cache_metadata()` and `test_cache_defaults()`
2. Tests failed (decorator didn't exist)
3. Implemented `cache()` decorator
4. Tests passed
5. Added docstring with examples

### Pattern: Documentation-First Design

**Scenario**: Designing a new API or feature.

**Process**:
1. Write documentation showing how feature will be used
2. Get feedback on API design
3. Refine documentation based on feedback
4. Implement to match documentation
5. Verify examples in docs actually work

**Example**: State Management documentation suite
1. Wrote extensive examples in STATE_MANAGEMENT_EXAMPLES.md
2. Showed how decorators would be used
3. Implemented decorators to match documentation
4. Verified all examples work

---

## Tools & Automation

### Required Tools

1. **TodoWrite** - Task tracking during sessions
2. **Pytest** - Python testing
3. **Git** - Version control
4. **GitHub CLI** (`gh`) - PR management

### Automation Scripts

- `make test` - Run all tests
- `make test-python` - Run Python tests only
- `make lint` - Run linters
- `make format` - Format code

---

## Metrics & Success Criteria

### Code Quality Metrics

- ✅ Test coverage: 100% of new code
- ✅ Test pass rate: 100%
- ✅ Type hint coverage: 100% of public APIs
- ✅ Documentation coverage: All public APIs documented

### Process Metrics

- 📊 Actual vs estimated time
- 📊 Bugs found in review vs production
- 📊 Number of iterations needed
- 📊 Time to merge after PR creation

### Example from Phase 1

**Code Quality**:
- Test coverage: 100% (25/25 tests)
- Type hints: 100% (all decorators and methods)
- Documentation: 100% (all features documented)

**Process**:
- Actual vs estimated: 2.5 hrs vs 4.5 hrs (56% under budget)
- Iterations: 1 (improvements after initial review)
- Bugs found: 0
- Tests passing: 100%

---

## Continuous Improvement

This document should be updated as we:
- Discover new patterns
- Refine existing processes
- Learn from mistakes
- Find more efficient approaches

Each PR should consider whether workflow improvements should be documented here.

---

## Related Documents

- [DEFINITION_OF_DONE.md](DEFINITION_OF_DONE.md) - Checklist for PR completion
- [IMPLEMENTATION_PHASE1.md](IMPLEMENTATION_PHASE1.md) - Example tracking document
- [CLAUDE.md](../CLAUDE.md) - Project-specific guidance for AI

---

**Last Updated**: 2025-01-12
**Version**: 1.0
