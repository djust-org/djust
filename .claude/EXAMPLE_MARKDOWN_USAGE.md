# Example: Using ORM_JIT_IMPLEMENTATION.md

## Your Markdown Documentation is Ready!

Your file `docs/templates/ORM_JIT_IMPLEMENTATION.md` has perfect structure:

```markdown
## Phase 1: Rust Template Variable Extraction
**Duration**: 2 days
**Dependencies**: None

### Goals
1. Parse Django template syntax
2. Return structured data
3. Expose function to Python

### Deliverables
- [ ] `extract_template_variables()` function in Rust
- [ ] PyO3 Python binding
- [ ] Unit tests for parser
...

## Phase 2: Query Optimizer
...

## Phase 3: Serializer Code Generation
...

## Phase 4: LiveView Integration
...

## Phase 5: Caching Infrastructure
...

## Phase 6: Testing & Documentation
...
```

## How to Use It

### Option 1: Implement All 6 Phases

```bash
# Execute all phases automatically
./scripts/auto_feature_dev.sh docs/templates/ORM_JIT_IMPLEMENTATION.md
```

**What happens**:
1. **Branch created**: `feature/orm-jit-implementation-20251116`
2. **Phase 1** (2 days of work):
   - Claude reads Phase 1 section from markdown
   - Implements `extract_template_variables()` in Rust
   - Creates Python PyO3 binding
   - Writes 50+ tests
   - Adds Criterion benchmarks
   - Creates commit: `feat(phase-1): Rust template variable extraction`
3. **Phase 2** (3 days of work):
   - Reads Phase 2 section
   - Implements query optimizer
   - Creates tests
   - Creates commit: `feat(phase-2): Query optimizer`
4. **Phases 3-6**: Continue similarly
5. **PR created**: With all 6 commits

**Total time**: ~14 days of work done in 3-6 hours (autonomous)

### Option 2: Implement One Phase at a Time

```bash
# Just do Phase 1
./scripts/auto_feature_dev.sh --single phase-1 docs/templates/ORM_JIT_IMPLEMENTATION.md

# Review Phase 1 implementation, then continue
./scripts/auto_feature_dev.sh --single phase-2 docs/templates/ORM_JIT_IMPLEMENTATION.md

# And so on...
```

**Benefits**:
- Review each phase before continuing
- Test each phase independently
- Iterate if needed

### Option 3: Preview What Claude Will Receive

```bash
# See the context without executing
./scripts/auto_feature_dev.sh --dry-run docs/templates/ORM_JIT_IMPLEMENTATION.md
```

**Output**: Shows full context file that Claude will receive

---

## What Claude Will See (Phase 1)

```markdown
# Feature Development - Multi-Phase Implementation

## Mission: Complete ORM JIT Auto-Serialization - Phase 1

You are implementing **Phase 1: Rust Template Variable Extraction**.

## Feature Specification

[ENTIRE ORM_JIT_IMPLEMENTATION.MD CONTENT HERE]

# djust ORM JIT Auto-Serialization - Implementation Plan

**Status**: Ready for Implementation
...

## Phase 1: Rust Template Variable Extraction

**Duration**: 2 days
**Dependencies**: None

### Goals

1. Parse Django template syntax to extract variable paths
2. Return structured data mapping variable names to attribute paths
3. Expose function to Python via PyO3

### Deliverables

- [ ] `extract_template_variables()` function in Rust
- [ ] PyO3 Python binding
- [ ] Unit tests for parser
- [ ] Benchmark: <5ms for typical templates

### Step 1.1: Implement Variable Extraction in Rust

**File**: `crates/djust_templates/src/parser.rs`

**Add new function**:

\`\`\`rust
pub fn extract_template_variables(template: &str) -> HashMap<String, Vec<String>> {
    let mut variables: HashMap<String, Vec<String>> = HashMap::new();

    // Tokenize the template
    let tokens = crate::lexer::tokenize(template)?;

    for token in tokens {
        match token {
            Token::Variable { name, filters: _ } => {
                extract_from_variable(&name, &mut variables);
            }
            _ => {}
        }
    }

    // Deduplicate paths for each variable
    for paths in variables.values_mut() {
        paths.sort();
        paths.dedup();
    }

    variables
}
\`\`\`

[... rest of implementation details ...]

### Acceptance Criteria

- [x] `extract_template_variables()` returns correct variable paths
- [x] Handles nested attributes (e.g., `lease.tenant.user.email`)
- [x] Handles template filters (e.g., `|date:"M d"`)
- [x] Performance < 5ms for typical templates
- [x] All unit tests pass

## Your Autonomous Workflow

### Step 1: Pre-Implementation Planning
- Review Phase 1 specification above
- Identify files to create/modify
- Create TODO list

### Step 2: Implementation
- Create `crates/djust_templates/src/parser.rs`
- Implement `extract_template_variables()` function
- Follow code examples from spec
- Write tests DURING implementation

### Step 3: Quality Gates
- cargo fmt --all
- cargo clippy --all-targets
- cargo test -p djust_templates
- pytest python/tests/

[... rest of workflow ...]

Begin implementation!
```

---

## What Makes This Work

Claude reads your markdown and:

1. **Understands Goals**: "Parse Django template syntax"
2. **Knows What to Create**: "extract_template_variables() function in Rust"
3. **Knows Where**: "crates/djust_templates/src/parser.rs"
4. **Sees Example Code**: Follows your implementation pattern
5. **Knows Success Criteria**: "Performance < 5ms"
6. **Creates Tests**: "50+ comprehensive tests"

**Result**: Complete implementation matching your specification.

---

## Why This is Powerful

### Before Automation

**Manual Process** (14 days of work):
1. Week 1: Implement Phases 1-2 (5 days)
2. Week 2: Implement Phases 3-4 (6 days)
3. Week 3: Implement Phases 5-6 (3 days)

**Your time**: 14 days full-time coding

### With Automation

**Automated Process** (same 14 days of work, done in hours):

```bash
./scripts/auto_feature_dev.sh docs/templates/ORM_JIT_IMPLEMENTATION.md
```

**Claude's time**: 3-6 hours (runs autonomously)
**Your time**: 30 minutes (review PR, merge)

**Savings**: 13.5 days!

---

## Your Next Steps

### 1. Preview (Recommended First Step)

```bash
./scripts/auto_feature_dev.sh --dry-run docs/templates/ORM_JIT_IMPLEMENTATION.md
```

Review the context file to see what Claude will receive.

### 2. Test with Single Phase

```bash
# Just try Phase 1
./scripts/auto_feature_dev.sh --single phase-1 docs/templates/ORM_JIT_IMPLEMENTATION.md
```

This lets you:
- See how it works on one phase
- Review the implementation
- Verify quality before continuing

### 3. Run Full Implementation

If Phase 1 looks good:

```bash
./scripts/auto_feature_dev.sh docs/templates/ORM_JIT_IMPLEMENTATION.md
```

Let it run all 6 phases, then review the final PR.

---

## Customization Options

### Custom Branch Name

```bash
./scripts/auto_feature_dev.sh --branch jit-v2 docs/templates/ORM_JIT_IMPLEMENTATION.md
```

### Skip to Specific Phase

If you already did Phase 1-2 manually:

```bash
./scripts/auto_feature_dev.sh --single phase-3 docs/templates/ORM_JIT_IMPLEMENTATION.md
```

### Modify Documentation

Want to change Phase 1 deliverables? Just edit the markdown:

```markdown
## Phase 1: Rust Template Variable Extraction

### Deliverables
- [ ] `extract_template_variables()` function in Rust
- [ ] PyO3 Python binding
- [ ] **NEW: Property-based tests using proptest**  <!-- Add this -->
- [ ] Unit tests for parser
```

Then run again - Claude will include the new deliverable.

---

## Summary

**Your ORM_JIT_IMPLEMENTATION.md is perfect as-is!**

Just run:
```bash
./scripts/auto_feature_dev.sh docs/templates/ORM_JIT_IMPLEMENTATION.md
```

And get:
- ✅ All 6 phases implemented
- ✅ 6 commits (one per phase)
- ✅ Comprehensive tests
- ✅ Documentation updated
- ✅ All quality gates passed
- ✅ PR ready for review

**14 days of work → Done in 3-6 hours** ⚡

---

**Related Documentation**:
- `.claude/MARKDOWN_SPECS.md` - Complete markdown specification guide
- `.claude/HOW_IT_WORKS.md` - How the automation works
- `scripts/README.md` - Full automation documentation
