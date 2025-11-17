# How the Automation Knows What to Implement

## TL;DR

**The YAML feature specification file contains all the implementation details, and the script passes this entire file to Claude Code as part of the context.**

---

## Detailed Flow

### 1. You Create a Feature Specification

```yaml
# .claude/features/jit-serialization.yaml
feature: "jit-auto-serialization"
title: "JIT Auto-Serialization for Django ORM"

description: |
  Implement Just-In-Time auto-serialization to automatically optimize
  Django ORM queries based on template variable usage.

phases:
  - name: phase-1-extraction
    title: "Template Variable Extraction"

    description: |
      Extract variable access patterns from Django templates
      (e.g., lease.property.name) using Rust-based parser.

    docs:
      - "docs/templates/ORM_JIT_API.md#phase-1"

    deliverables:
      - "Rust: extract_template_variables() function"
      - "Python: extract_template_variables() binding"
      - "Tests: 50+ comprehensive tests (Rust + Python)"
      - "Benchmarks: Criterion benchmarks"
      - "Docs: API documentation with examples"

    success_criteria:
      - "All tests pass (cargo test, pytest)"
      - "Benchmarks show <5ms for typical templates"
      - "Zero clippy warnings"
      - "Documentation complete with examples"
```

**This YAML file is your specification.** It tells Claude exactly what to implement.

---

### 2. You Run the Script

```bash
./scripts/auto_feature_dev.sh .claude/features/jit-serialization.yaml
```

---

### 3. Script Reads the YAML File

**Code** (`scripts/auto_feature_dev.sh` lines 271-274):
```bash
# Load feature specification
local feature_context=""
if [ -f "$feature_spec" ]; then
    feature_context=$(cat "$feature_spec")  # <-- Reads entire YAML file
fi
```

---

### 4. Script Creates Context File for Claude

**Code** (`scripts/auto_feature_dev.sh` lines 477-479):
```bash
## Feature Specification

$feature_context  # <-- Inserts entire YAML content here
```

**Result** (what Claude receives in `$TEMP_DIR/context.md`):
```markdown
# Feature Development - Multi-Phase Implementation

## Mission: Complete jit-auto-serialization - phase-1-extraction

You are autonomously implementing **phase-1-extraction** for the
**jit-auto-serialization** feature.

## Feature Specification

feature: "jit-auto-serialization"
title: "JIT Auto-Serialization for Django ORM"

description: |
  Implement Just-In-Time auto-serialization to automatically optimize
  Django ORM queries based on template variable usage.

phases:
  - name: phase-1-extraction
    title: "Template Variable Extraction"

    description: |
      Extract variable access patterns from Django templates...

    deliverables:
      - "Rust: extract_template_variables() function"
      - "Python: extract_template_variables() binding"
      - "Tests: 50+ comprehensive tests (Rust + Python)"

    success_criteria:
      - "All tests pass (cargo test, pytest)"
      - "Benchmarks show <5ms for typical templates"

## Your Workflow

### Step 1: Pre-Implementation Planning
- Review the phase specification above
- Identify all files that need changes
- Plan the implementation approach
- Create TODO list using TodoWrite

### Step 2: Implementation
- Implement: extract_template_variables() function (Rust)
- Implement: Python binding
- Write: 50+ tests
- Add: Criterion benchmarks
- Write: API documentation

### Step 3: Quality Gates
- cargo test --all
- pytest
- cargo clippy
- Benchmarks must show <5ms

... [rest of the context]
```

---

### 5. Claude Code Receives This Context

When you run the script, Claude Code CLI is invoked with:
```bash
claude -p "$(cat $TEMP_DIR/context.md)" \
    --allowedTools "Read,Write,Edit,Bash,Grep,Glob,Task,TodoWrite"
```

Claude sees:
- ✅ **What feature**: "jit-auto-serialization"
- ✅ **Current phase**: "phase-1-extraction"
- ✅ **What to implement**: Deliverables list (Rust function, Python binding, tests, etc.)
- ✅ **Definition of done**: Success criteria (tests pass, benchmarks <5ms, etc.)
- ✅ **Where to learn more**: Documentation references
- ✅ **How to validate**: Quality gates (cargo test, pytest, clippy)

---

### 6. Claude Implements Based on the Spec

Claude reads the YAML content and:

1. **Understands the goal**: "Extract template variables"
2. **Knows what to deliver**:
   - Rust function
   - Python binding
   - 50+ tests
   - Benchmarks
   - Documentation
3. **Knows the success criteria**: Tests pass, benchmarks fast, zero warnings
4. **Implements it all**: Following the deliverables list
5. **Validates**: Runs the quality gates
6. **Creates commit**: With descriptive message

---

## Visual Flow Diagram

```
┌─────────────────────────────────────────────┐
│  1. You: Create Feature Spec (YAML)        │
│     - Feature description                   │
│     - Phase details                         │
│     - Deliverables                          │
│     - Success criteria                      │
└─────────────────────────────────────────────┘
                    ↓
┌─────────────────────────────────────────────┐
│  2. You: Run Script                         │
│     ./scripts/auto_feature_dev.sh \         │
│       .claude/features/my-feature.yaml      │
└─────────────────────────────────────────────┘
                    ↓
┌─────────────────────────────────────────────┐
│  3. Script: Read YAML File                  │
│     feature_context=$(cat "$feature_spec")  │
└─────────────────────────────────────────────┘
                    ↓
┌─────────────────────────────────────────────┐
│  4. Script: Create Context for Claude       │
│     ## Feature Specification                │
│     $feature_context  <-- YAML inserted     │
└─────────────────────────────────────────────┘
                    ↓
┌─────────────────────────────────────────────┐
│  5. Script: Invoke Claude Code CLI          │
│     claude -p "$(cat context.md)"           │
└─────────────────────────────────────────────┘
                    ↓
┌─────────────────────────────────────────────┐
│  6. Claude: Read Context                    │
│     - Parse YAML content                    │
│     - Understand what to implement          │
│     - See deliverables list                 │
│     - See success criteria                  │
└─────────────────────────────────────────────┘
                    ↓
┌─────────────────────────────────────────────┐
│  7. Claude: Implement Everything            │
│     - Write Rust code                       │
│     - Write Python bindings                 │
│     - Write 50+ tests                       │
│     - Add benchmarks                        │
│     - Write documentation                   │
│     - Run quality gates                     │
│     - Create commit                         │
└─────────────────────────────────────────────┘
                    ↓
┌─────────────────────────────────────────────┐
│  8. Script: Move to Next Phase              │
│     (if multi-phase feature)                │
└─────────────────────────────────────────────┘
```

---

## Key Insight: YAML = Specification = Instructions

The YAML file serves three purposes:

1. **Documentation**: Records what was planned
2. **Specification**: Defines what to implement
3. **Instructions**: Tells Claude what to do

Think of it like giving Claude a detailed work order:

```yaml
deliverables:
  - "Rust: extract_template_variables() function"
  - "Python: extract_template_variables() binding"
  - "Tests: 50+ comprehensive tests"
```

This tells Claude:
- ✅ Create a Rust function called `extract_template_variables()`
- ✅ Create a Python binding for it
- ✅ Write at least 50 tests

---

## What Claude Knows vs What It Discovers

### From YAML Spec (What to Build):
- Feature goal and description
- Phase objectives
- Deliverables list
- Success criteria
- Quality gates

### From Code Exploration (How to Build):
- Existing code patterns
- Project structure
- Where to put new files
- Integration points
- Testing patterns

### From Documentation (Context):
- API design patterns
- Development process
- Best practices
- Architecture decisions

---

## Example: Minimal vs Detailed Spec

### Minimal Spec (Less Guidance)
```yaml
phases:
  - name: phase-1
    description: "Add template extraction"
```

**Result**: Claude has to figure out:
- What exactly to extract
- How to structure the API
- What tests to write
- What constitutes "done"

### Detailed Spec (Better!)
```yaml
phases:
  - name: phase-1-extraction
    description: |
      Extract variable access patterns from Django templates
      (e.g., lease.property.name) using Rust-based parser.

    deliverables:
      - "Rust: extract_template_variables() function in djust_templates/src/parser.rs"
      - "Python: extract_template_variables() binding in djust_live/src/lib.rs"
      - "Tests: 50+ tests covering edge cases, performance, real-world templates"
      - "Benchmarks: Criterion benchmarks for performance tracking"

    success_criteria:
      - "All tests pass (cargo test, pytest)"
      - "Benchmarks show <5ms for typical templates"
      - "Handles nested variables (user.profile.name)"
      - "Deduplicates variable paths"
```

**Result**: Claude knows exactly:
- ✅ What to build (extract_template_variables function)
- ✅ Where to put it (djust_templates/src/parser.rs)
- ✅ What it should do (extract variable paths, handle nested, deduplicate)
- ✅ How to test it (50+ tests with specific scenarios)
- ✅ What "done" means (tests pass, <5ms performance)

---

## Pro Tips

### 1. Be Specific in Deliverables
```yaml
# ❌ Vague
deliverables:
  - "Add tests"

# ✅ Specific
deliverables:
  - "Tests: 50+ comprehensive tests covering basic, edge cases, performance"
  - "Tests: TestBasicExtraction, TestEdgeCases, TestPerformance classes"
```

### 2. Include Documentation References
```yaml
docs:
  - "docs/templates/ORM_JIT_API.md#phase-1"
  - "DEVELOPMENT_PROCESS.md#testing"
```

Claude will read these docs to understand context.

### 3. Define Success Clearly
```yaml
success_criteria:
  - "All tests pass (cargo test, pytest)"
  - "Benchmarks show <5ms for typical templates"
  - "Zero clippy warnings"
  - "API documentation complete with examples"
```

This tells Claude exactly when the phase is "done."

### 4. Link Phases with Dependencies
```yaml
phases:
  - name: phase-2-optimizer
    dependencies: [phase-1-extraction]  # Claude knows Phase 1 context
```

Claude will understand Phase 1 work when implementing Phase 2.

---

## Summary

**Q: How does the script know what to implement?**

**A: You tell it in the YAML feature specification file.**

The script reads the YAML file and passes it to Claude Code as part of the context. Claude reads the spec and implements exactly what you described in the deliverables, following the success criteria, and using the documentation references for guidance.

**The more detailed your YAML spec, the better the implementation.**

---

**Related Documentation**:
- `.claude/features/example-jit-serialization.yaml` - Complete example
- `scripts/README.md` - Full automation documentation
- `.claude/AUTOMATION_QUICKSTART.md` - Quick start guide
