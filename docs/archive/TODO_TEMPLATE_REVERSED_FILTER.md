# TODO: Add `reversed` Filter to Rust Template Engine

**Status**: Tracked in GitHub Issue [#48](https://github.com/johnrtipton/djust/issues/48)

**Priority**: Medium
**Component**: `djust_templates` (Rust)
**Estimated Effort**: 2-3 hours
**Created**: 2025-11-12

## Problem

The Rust template engine (`crates/djust_templates`) currently doesn't support the `reversed` filter for `{% for %}` loops. This is a common Django template feature that's expected by developers.

### Current Behavior

```django
{% for item in items reversed %}
  <!-- Loop body is SKIPPED - no error, just silent failure -->
  <li>{{ item }}</li>
{% endfor %}
```

Result: Empty output (loop body never executes)

### Workaround (Current)

Reverse the list in Python before passing to template:

```python
def get_context_data(self):
    context = super().get_context_data()
    context['items'] = list(reversed(context['items']))
    return context
```

```django
{% for item in items %}
  <li>{{ item }}</li>
{% endfor %}
```

## Desired Behavior

The `reversed` filter should work as in Django templates:

```django
{% for item in items reversed %}
  <li>{{ item }}</li>
{% endfor %}
```

Should produce the same output as:

```python
for item in reversed(items):
    ...
```

## Implementation

### Location

`crates/djust_templates/src/parser.rs` and `crates/djust_templates/src/runtime.rs`

### Changes Needed

1. **Parser**: Update `ForNode` to detect and store the `reversed` flag
   - Modify `parse_for_tag()` to check for `reversed` keyword after loop variable
   - Store `reversed: bool` in `ForNode` struct

2. **Runtime**: Apply reversal during loop execution
   - In `ForNode::render()`, reverse the iterator if flag is set
   - Use `Vec::reverse()` or `Iterator::rev()` depending on collection type

### Example Implementation (Pseudo-code)

```rust
// parser.rs
struct ForNode {
    loop_var: String,
    iterable: Expression,
    reversed: bool,  // NEW
    body: Vec<Node>,
}

impl ForNode {
    fn parse(tokens: &mut TokenStream) -> Result<Self> {
        // ... parse "for item in items" ...

        // Check for "reversed" keyword
        let reversed = if tokens.peek() == Some("reversed") {
            tokens.consume();
            true
        } else {
            false
        };

        // ... parse body ...
    }
}

// runtime.rs
impl ForNode {
    fn render(&self, context: &Context) -> Result<String> {
        let items = self.iterable.evaluate(context)?;

        let iter: Box<dyn Iterator<Item=Value>> = if self.reversed {
            Box::new(items.into_vec()?.into_iter().rev())
        } else {
            Box::new(items.into_iter())
        };

        // ... loop rendering ...
    }
}
```

## Testing

### Unit Tests

Add to `crates/djust_templates/tests/`:

```rust
#[test]
fn test_for_reversed() {
    let template = r#"
        {% for item in items reversed %}
        {{ item }}
        {% endfor %}
    "#;

    let context = json!({
        "items": [1, 2, 3]
    });

    let output = render(template, context).unwrap();
    assert_eq!(output.trim(), "3 2 1");
}

#[test]
fn test_for_reversed_with_dict() {
    let template = r#"
        {% for key, value in items.items reversed %}
        {{ key }}={{ value }}
        {% endfor %}
    "#;

    // Test with dictionary iteration
}
```

### Integration Tests

Update `examples/demo_project/demo_app/templates/demos/component_demo.html`:

```django
<!-- Change from: -->
{% for entry in event_log %}

<!-- To: -->
{% for entry in event_log reversed %}
```

And remove the Python workaround in `get_context_data()`.

## Django Compatibility

Ensure behavior matches Django's `reversed`:
- Works with lists, tuples, and querysets
- Works with dictionary iteration (`items.items reversed`)
- Does NOT work with generators (should raise error)

## Documentation

Update:
- `docs/TEMPLATE_SYNTAX.md` - Add `reversed` to filter documentation
- `CHANGELOG.md` - Note the new feature
- `CLAUDE.md` - Remove workaround note

## Related Issues

- Component demo event log (fixed with workaround in commit `3892776`)
- General Django template compatibility

## References

- [Django template for tag documentation](https://docs.djangoproject.com/en/stable/ref/templates/builtins/#for)
- Current workaround: `examples/demo_project/demo_app/views/component_demo.py:336`
