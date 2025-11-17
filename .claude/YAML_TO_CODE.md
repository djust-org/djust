# YAML Specification → Code Implementation

This document shows **exactly** how your YAML specification maps to what Claude implements.

## Example: YAML Spec → Actual Code

### You Write This YAML:

```yaml
# .claude/features/jit-serialization.yaml
feature: "jit-auto-serialization"

phases:
  - name: phase-1-extraction
    title: "Template Variable Extraction"

    description: |
      Extract variable access patterns from Django templates
      (e.g., lease.property.name) using Rust-based parser.

    deliverables:
      - "Rust: extract_template_variables() function in djust_templates/src/parser.rs"
      - "Python: extract_template_variables() binding in djust_live/src/lib.rs"
      - "Tests: 50+ comprehensive tests (Rust + Python)"
      - "Tests: TestBasicExtraction, TestEdgeCases, TestPerformance"
      - "Benchmarks: Criterion benchmarks in benches/variable_extraction.rs"
      - "Docs: Add usage examples to docs/templates/ORM_JIT_API.md"

    success_criteria:
      - "All tests pass (cargo test, pytest)"
      - "Benchmarks show <5ms for typical templates"
      - "Zero clippy warnings"
      - "Handles nested variables (user.profile.name)"
      - "Deduplicates variable paths"

    quality_gates:
      - "cargo fmt --all"
      - "cargo clippy --all-targets -- -D warnings"
      - "cargo test -p djust_templates"
      - "pytest python/tests/test_template_variable_extraction.py"
      - "cargo bench --bench variable_extraction"
```

---

### Claude Implements This:

#### 1. Rust Function (from deliverable #1)

**File**: `djust_templates/src/parser.rs`

```rust
/// Extract all variable paths from a Django template for JIT serialization.
///
/// # Behavior
/// - Returns HashMap with variable names as keys, paths as values
/// - Handles nested variables (user.profile.name)
/// - Deduplicates variable paths
///
/// # Example
/// ```
/// let template = "{{ user.profile.name }} {{ user.email }}";
/// let vars = extract_template_variables(template)?;
/// // Returns: {"user": ["profile.name", "email"]}
/// ```
pub fn extract_template_variables(
    template: &str,
) -> Result<HashMap<String, Vec<String>>> {
    // Implementation based on description and success criteria
    let tokens = crate::lexer::tokenize(template)?;
    let nodes = parse(&tokens)?;

    let mut variables: HashMap<String, Vec<String>> = HashMap::new();
    extract_from_nodes(&nodes, &mut variables);

    // Deduplicates (from success criteria)
    for paths in variables.values_mut() {
        paths.sort();
        paths.dedup();
    }

    Ok(variables)
}
```

**Why this way?**
- ✅ Deliverable said: "in djust_templates/src/parser.rs"
- ✅ Success criteria said: "Handles nested variables"
- ✅ Success criteria said: "Deduplicates variable paths"

---

#### 2. Python Binding (from deliverable #2)

**File**: `djust_live/src/lib.rs`

```rust
/// Extract template variables for JIT auto-serialization.
///
/// # Example
/// ```python
/// from djust._rust import extract_template_variables
///
/// template = "{{ lease.property.name }}"
/// vars = extract_template_variables(template)
/// # Returns: {"lease": ["property.name"]}
/// ```
#[pyfunction(name = "extract_template_variables")]
fn extract_template_variables_py(py: Python, template: String) -> PyResult<PyObject> {
    let vars_map = djust_templates::extract_template_variables(&template)
        .map_err(|e| PyErr::new::<PyValueError, _>(format!("Parsing error: {e}")))?;

    // Convert to Python dict
    let py_dict = PyDict::new_bound(py);
    for (key, paths) in vars_map {
        let py_list = PyList::new_bound(py, paths.iter().map(|s| s.as_str()));
        py_dict.set_item(key, py_list)?;
    }

    Ok(py_dict.into())
}
```

**Why this way?**
- ✅ Deliverable said: "in djust_live/src/lib.rs"
- ✅ Includes Python usage example (from docs requirement)

---

#### 3. Comprehensive Tests (from deliverable #3)

**File**: `python/tests/test_template_variable_extraction.py`

```python
"""Tests for template variable extraction."""
import pytest
from djust._rust import extract_template_variables

class TestBasicExtraction:  # From deliverable: TestBasicExtraction
    def test_single_variable(self):
        result = extract_template_variables("{{ name }}")
        assert result == {"name": []}

    def test_nested_variable(self):  # From success criteria
        result = extract_template_variables("{{ user.profile.name }}")
        assert result == {"user": ["profile.name"]}

class TestEdgeCases:  # From deliverable: TestEdgeCases
    def test_empty_template(self):
        result = extract_template_variables("")
        assert result == {}

    def test_deeply_nested(self):
        result = extract_template_variables("{{ a.b.c.d.e }}")
        assert result == {"a": ["b.c.d.e"]}

class TestPerformance:  # From deliverable: TestPerformance
    def test_large_template_performance(self):
        import time
        template = "{{ var }}" * 1000
        start = time.time()
        extract_template_variables(template)
        duration = time.time() - start
        assert duration < 0.005  # From success criteria: <5ms
```

**Why 50+ tests?**
- ✅ Deliverable specified: "50+ comprehensive tests"
- ✅ Named classes as specified: TestBasicExtraction, TestEdgeCases, TestPerformance
- ✅ Tests success criteria: nested variables, performance <5ms

---

#### 4. Benchmarks (from deliverable #5)

**File**: `benches/variable_extraction.rs`

```rust
use criterion::{black_box, criterion_group, criterion_main, Criterion};
use djust_templates::extract_template_variables;

fn bench_simple_variables(c: &mut Criterion) {
    c.bench_function("single_var", |b| {
        b.iter(|| extract_template_variables(black_box("{{ name }}")));
    });

    c.bench_function("nested_3_levels", |b| {
        b.iter(|| extract_template_variables(black_box("{{ user.profile.name }}")));
    });
}

fn bench_real_world(c: &mut Criterion) {
    let template = r#"
        {% for lease in expiring_soon %}
            {{ lease.property.name }}
            {{ lease.tenant.user.email }}
        {% endfor %}
    "#;

    c.bench_function("rental_dashboard", |b| {
        b.iter(|| extract_template_variables(black_box(template)));
    });
}

criterion_group!(benches, bench_simple_variables, bench_real_world);
criterion_main!(benches);
```

**Why criterion?**
- ✅ Deliverable said: "Criterion benchmarks"
- ✅ Success criteria: "Benchmarks show <5ms" (criterion measures this)

---

#### 5. Documentation (from deliverable #6)

**File**: `docs/templates/ORM_JIT_API.md`

```markdown
## Phase 1: Template Variable Extraction

### Python API

```python
from djust._rust import extract_template_variables

# Extract variables from template
template = """
    {{ lease.property.name }}
    {{ lease.tenant.user.email }}
"""

vars = extract_template_variables(template)
# Returns: {
#   "lease": ["property.name", "tenant.user.email"]
# }
```

### Usage Example

```python
# Identify what to serialize
template = open('templates/dashboard.html').read()
vars = extract_template_variables(template)

# Use for query optimization
if "lease" in vars:
    paths = vars["lease"]
    queryset = queryset.select_related(*[p.replace('.', '__') for p in paths])
```

### Performance

- Typical templates: <1ms
- Complex templates: <5ms
- Handles deeply nested paths efficiently
```

**Why this format?**
- ✅ Deliverable said: "Add usage examples to docs/templates/ORM_JIT_API.md"
- ✅ Includes Python examples (usage-focused)
- ✅ Documents performance (success criteria: <5ms)

---

#### 6. Quality Gates Passed

```bash
# From quality_gates in YAML:
✓ cargo fmt --all                    # No formatting issues
✓ cargo clippy --all-targets         # Zero warnings (success criteria)
✓ cargo test -p djust_templates      # All tests pass (success criteria)
✓ pytest python/tests/...            # All tests pass (success criteria)
✓ cargo bench --bench variable_extraction  # Performance verified (<5ms)
```

---

## Mapping Summary

| YAML Field | What Claude Does |
|------------|------------------|
| `feature` | Uses as commit prefix, PR title |
| `description` | Understands the goal, context |
| `deliverables[0]` | Creates Rust function in specified file |
| `deliverables[1]` | Creates Python binding in specified file |
| `deliverables[2]` | Writes 50+ tests |
| `deliverables[3]` | Creates TestBasicExtraction, TestEdgeCases classes |
| `deliverables[4]` | Creates Criterion benchmark file |
| `deliverables[5]` | Adds usage examples to docs |
| `success_criteria[0]` | Ensures all tests pass |
| `success_criteria[1]` | Verifies benchmarks show <5ms |
| `success_criteria[2]` | Runs clippy to ensure zero warnings |
| `success_criteria[3]` | Implements nested variable handling |
| `success_criteria[4]` | Implements deduplication logic |
| `quality_gates[*]` | Runs each command before committing |

---

## The More Specific, The Better

### ❌ Vague Deliverable
```yaml
deliverables:
  - "Add tests"
```

**Result**: Claude writes basic tests, maybe 5-10 tests, unclear structure.

### ✅ Specific Deliverable
```yaml
deliverables:
  - "Tests: 50+ comprehensive tests (Rust + Python)"
  - "Tests: TestBasicExtraction (5 tests)"
  - "Tests: TestEdgeCases (10 tests covering empty, malformed, deeply nested)"
  - "Tests: TestPerformance (benchmark that asserts <5ms for typical templates)"
  - "Tests: TestRealWorld (3 tests with actual template patterns)"
```

**Result**: Claude writes exactly this structure:
- ✅ 50+ tests total
- ✅ TestBasicExtraction class with 5 tests
- ✅ TestEdgeCases class with 10 tests (empty, malformed, nested)
- ✅ TestPerformance class with <5ms assertion
- ✅ TestRealWorld class with 3 real patterns

---

## Pro Tips for Writing Specs

### 1. Specify File Locations
```yaml
# ❌ Vague
- "Add Rust function"

# ✅ Specific
- "Rust: extract_template_variables() in djust_templates/src/parser.rs"
```

### 2. Include Examples in Description
```yaml
description: |
  Extract variable access patterns from Django templates.

  Examples:
  - "{{ user.name }}" → {"user": ["name"]}
  - "{{ lease.property.name }}" → {"lease": ["property.name"]}
```

Claude will use these examples to understand the expected behavior.

### 3. Link to Existing Code Patterns
```yaml
description: |
  Implement query optimizer similar to existing template renderer
  in djust_templates/src/renderer.rs but for ORM queries.
```

Claude will read that file and follow the same patterns.

### 4. Define Edge Cases in Success Criteria
```yaml
success_criteria:
  - "Handles empty templates gracefully"
  - "Handles malformed templates with clear error messages"
  - "Handles deeply nested paths (10+ levels)"
  - "Handles duplicate variables correctly"
```

Claude will write tests for each of these.

---

## Summary

**Your YAML specification is a contract.**

Everything in the YAML becomes code:
- `deliverables` → Files created, functions written
- `success_criteria` → Tests written, validation performed
- `quality_gates` → Commands run
- `description` → Implementation guidance
- `docs` → Documentation Claude reads for context

**The better your spec, the better the implementation.**

---

**See Also**:
- `.claude/HOW_IT_WORKS.md` - Complete flow explanation
- `.claude/features/example-jit-serialization.yaml` - Full example
- `scripts/README.md` - Automation documentation
