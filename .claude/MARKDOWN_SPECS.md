# Using Markdown Documentation as Feature Specifications

## TL;DR

**You can use your existing markdown documentation directly!**

```bash
# Use your markdown docs as the specification
./scripts/auto_feature_dev.sh docs/templates/ORM_JIT_IMPLEMENTATION.md
```

The script automatically:
- Detects markdown format (`.md` extension)
- Extracts phases from `## Phase N:` headers
- Passes entire markdown content to Claude
- Claude implements based on your documentation

---

## Why Markdown Support?

**Problem**: You already have comprehensive markdown documentation. Why rewrite it in YAML?

**Solution**: Use markdown docs directly as feature specifications.

### Benefits

1. **Documentation = Specification**: One source of truth
2. **Natural Format**: Markdown is more readable than YAML
3. **Code Examples**: Include code snippets inline
4. **Rich Formatting**: Bold, lists, tables, code blocks
5. **Existing Docs**: Reuse what you already have

---

## Markdown Format Requirements

### Minimal Format

The script looks for phase headers:

```markdown
## Phase 1: Title Here

Content describing phase 1...

## Phase 2: Another Title

Content describing phase 2...
```

That's it! Everything else is optional but recommended.

### Recommended Format

For better results, include these sections per phase:

```markdown
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

```rust
pub fn extract_template_variables(template: &str) -> HashMap<String, Vec<String>> {
    // Implementation here...
}
\`\`\`

### Acceptance Criteria

- [x] `extract_template_variables()` returns correct variable paths
- [x] Handles nested attributes
- [x] Performance < 5ms for typical templates
```

### What Claude Extracts

| Markdown Element | What Claude Uses |
|------------------|------------------|
| `## Phase N:` | Phase identification |
| **Duration** | Time estimation |
| **Dependencies** | Phase ordering |
| Goals | High-level understanding |
| Deliverables | What to implement |
| Code examples | Implementation guidance |
| File paths | Where to put code |
| Acceptance Criteria | Definition of done |

---

## Example: Using ORM_JIT_IMPLEMENTATION.md

Your existing file (`docs/templates/ORM_JIT_IMPLEMENTATION.md`) is **already perfect**!

### What the Script Detects

```bash
$ ./scripts/auto_feature_dev.sh docs/templates/ORM_JIT_IMPLEMENTATION.md
```

**Output**:
```
Parsing feature specification: docs/templates/ORM_JIT_IMPLEMENTATION.md
  Found Phase 1: Rust Template Variable Extraction
  Found Phase 2: Query Optimizer
  Found Phase 3: Serializer Code Generation
  Found Phase 4: LiveView Integration
  Found Phase 5: Caching Infrastructure
  Found Phase 6: Testing & Documentation
✓ Found 6 phase(s): phase-1 phase-2 phase-3 phase-4 phase-5 phase-6 (format: markdown)
```

### What Claude Receives

The **entire markdown file** is passed to Claude as context:

```markdown
# Feature Specification

[ENTIRE CONTENT OF ORM_JIT_IMPLEMENTATION.md]

## Your Mission

You are implementing **Phase 1: Rust Template Variable Extraction**.

From the specification above, you need to:
- Read "Phase 1: Rust Template Variable Extraction" section
- Implement all deliverables listed
- Follow the code examples provided
- Meet all acceptance criteria
```

Claude reads:
- ✅ **Goals**: Understands what to achieve
- ✅ **Deliverables**: Knows what to create
- ✅ **Code examples**: Follows your patterns
- ✅ **File paths**: Puts code in right place
- ✅ **Acceptance criteria**: Knows when done

---

## Comparison: YAML vs Markdown

### YAML Format

```yaml
feature: "jit-auto-serialization"
phases:
  - name: phase-1
    title: "Template Variable Extraction"
    description: |
      Extract variable paths from Django templates
    deliverables:
      - "Rust: extract_template_variables() function"
      - "Tests: 50+ comprehensive tests"
    success_criteria:
      - "All tests pass"
```

**Pros**: Structured, easy to parse programmatically
**Cons**: Less readable, no code examples, requires rewriting docs

### Markdown Format

```markdown
## Phase 1: Template Variable Extraction

Extract variable paths from Django templates.

### Deliverables

- [ ] `extract_template_variables()` function in Rust
- [ ] Tests: 50+ comprehensive tests

**Implementation**:

\`\`\`rust
pub fn extract_template_variables(template: &str) -> HashMap<String, Vec<String>> {
    let mut variables: HashMap<String, Vec<String>> = HashMap::new();
    // Your implementation here...
    variables
}
\`\`\`

### Acceptance Criteria

- [x] All tests pass
- [x] Performance < 5ms
```

**Pros**: Readable, code examples, rich formatting, reuse docs
**Cons**: Less structured for parsing (but good enough!)

### Recommendation

**Use Markdown** if you:
- Already have markdown documentation
- Want readable specifications
- Need code examples inline
- Prefer documentation-first approach

**Use YAML** if you:
- Need programmatic access to spec structure
- Want machine-parseable format
- Prefer structured data format

**Hybrid Approach**: YAML can reference markdown sections:

```yaml
feature: "jit-auto-serialization"
phases:
  - name: phase-1
    title: "Template Variable Extraction"
    docs:
      - "docs/templates/ORM_JIT_IMPLEMENTATION.md#phase-1"
```

---

## How to Convert Existing Docs

### Already Have Markdown? You're Done!

If your docs have `## Phase N:` headers, just use them:

```bash
./scripts/auto_feature_dev.sh docs/YOUR_DOC.md
```

### Need to Add Phase Headers?

Add phase headers to existing documentation:

```markdown
<!-- Before -->
# My Feature

This feature does X, Y, Z.

Implementation details...


<!-- After -->
# My Feature

This feature does X, Y, Z.

## Phase 1: Core Implementation

Implementation details for phase 1...

## Phase 2: Testing

Testing details for phase 2...
```

That's it!

---

## Usage Examples

### Example 1: Implement All Phases

```bash
# Execute all 6 phases from markdown
./scripts/auto_feature_dev.sh docs/templates/ORM_JIT_IMPLEMENTATION.md
```

**What happens**:
1. Parses markdown, finds 6 phases
2. For Phase 1:
   - Creates context with full markdown
   - Claude reads "Phase 1" section
   - Implements deliverables
   - Creates commit
3. Repeats for Phases 2-6
4. Creates final PR with all 6 commits

### Example 2: Implement Single Phase

```bash
# Only implement Phase 2
./scripts/auto_feature_dev.sh --single phase-2 docs/templates/ORM_JIT_IMPLEMENTATION.md
```

**What happens**:
- Reads markdown
- Executes only Phase 2
- Creates commit for Phase 2
- Stops (doesn't continue to Phase 3)

### Example 3: Preview Context

```bash
# See what Claude will receive
./scripts/auto_feature_dev.sh --dry-run docs/templates/ORM_JIT_IMPLEMENTATION.md
```

**Output**:
- Shows full context file
- No execution
- Review before running

---

## Best Practices

### 1. Structure Your Markdown

```markdown
## Phase N: Descriptive Title

**Duration**: X days
**Dependencies**: [list phases this depends on]

### Goals
[What this phase achieves]

### Deliverables
- [ ] Specific item 1
- [ ] Specific item 2

### Implementation
[Code examples, file paths, instructions]

### Acceptance Criteria
- [x] Criterion 1
- [x] Criterion 2
```

### 2. Be Specific in Deliverables

```markdown
<!-- ❌ Vague -->
- [ ] Add tests

<!-- ✅ Specific -->
- [ ] Tests: 50+ comprehensive tests in python/tests/test_variable_extraction.py
- [ ] Tests: TestBasicExtraction, TestEdgeCases, TestPerformance classes
- [ ] Benchmarks: Criterion benchmarks showing <5ms performance
```

### 3. Include Code Examples

Claude uses these as implementation guidance:

```markdown
### Step 1.1: Implement Function

**File**: `crates/djust_templates/src/parser.rs`

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

    variables
}
\`\`\`
```

Claude will:
- Create the file `crates/djust_templates/src/parser.rs`
- Implement `extract_template_variables()` function
- Follow the structure shown in your example
- Use similar patterns (tokenize, match, etc.)

### 4. Document Dependencies

```markdown
## Phase 2: Query Optimizer

**Duration**: 3 days
**Dependencies**: None

<!-- This phase can start immediately -->
```

```markdown
## Phase 4: LiveView Integration

**Duration**: 2 days
**Dependencies**: Phase 1, Phase 2, Phase 3

<!-- This phase requires phases 1-3 to complete first -->
```

The script will execute phases in correct order based on dependencies.

### 5. Include Acceptance Criteria

```markdown
### Acceptance Criteria

- [x] `extract_template_variables()` returns correct variable paths
- [x] Handles nested attributes (e.g., `lease.tenant.user.email`)
- [x] Handles template filters (e.g., `|date:"M d"`)
- [x] Deduplicates paths
- [x] Performance < 5ms for typical templates
- [x] All unit tests pass
```

Claude uses these to know when the phase is complete.

---

## Troubleshooting

### "No phases found in feature specification"

**Problem**: Script couldn't find phase headers

**Solution**: Ensure headers match pattern:
```markdown
## Phase 1: Title Here
## Phase 2: Another Title
```

Not:
```markdown
# Phase 1  (single #)
### Phase 1  (triple ###)
Phase 1:  (no ##)
```

### "Phase not found: phase-3"

**Problem**: Requested phase doesn't exist in markdown

**Solution**: Check phase numbers in markdown:
```bash
grep "^## Phase" docs/YOUR_DOC.md
```

### "Template content too long"

**Problem**: Markdown file is very large (>50KB)

**Solution**: Split into multiple files or use YAML with docs references:
```yaml
phases:
  - name: phase-1
    docs: ["docs/phase1.md"]
  - name: phase-2
    docs: ["docs/phase2.md"]
```

---

## FAQ

### Q: Can I mix markdown and YAML?

**A**: Yes! YAML can reference markdown documentation:

```yaml
feature: "my-feature"
phases:
  - name: phase-1
    title: "Implementation"
    docs:
      - "docs/implementation.md#phase-1"
    deliverables:
      - "See docs for details"
```

The script passes both YAML and linked markdown to Claude.

### Q: Do I need all the sections (Goals, Deliverables, etc.)?

**A**: No, only `## Phase N:` headers are required. But more detail = better implementation.

### Q: Can phase numbers skip?

**A**: Yes! These all work:
- `## Phase 1:`, `## Phase 2:`, `## Phase 3:`
- `## Phase 1:`, `## Phase 3:`, `## Phase 5:`
- `## Phase 10:`, `## Phase 20:`

### Q: What if I have existing markdown without phase headers?

**A**: Add phase headers to structure it:

```markdown
# Existing Doc

Introduction...

## Phase 1: Core Implementation  <!-- ADD THIS -->

Existing content about core implementation...

## Phase 2: Testing  <!-- ADD THIS -->

Existing content about testing...
```

---

## Summary

**Markdown support makes automation effortless:**

1. ✅ **Use existing docs** - No YAML rewrite needed
2. ✅ **Natural format** - Markdown is readable
3. ✅ **Code examples** - Show Claude how to implement
4. ✅ **One source of truth** - Documentation = Specification
5. ✅ **Rich formatting** - Bold, lists, code blocks

**Just add phase headers and run:**

```bash
./scripts/auto_feature_dev.sh docs/YOUR_FEATURE.md
```

**Your existing `docs/templates/ORM_JIT_IMPLEMENTATION.md` is perfect as-is!**

---

**Related Documentation**:
- `.claude/HOW_IT_WORKS.md` - How the automation works
- `.claude/YAML_TO_CODE.md` - YAML specification format
- `scripts/README.md` - Complete automation guide
