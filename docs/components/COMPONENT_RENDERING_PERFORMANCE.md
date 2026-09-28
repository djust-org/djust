# Component Rendering Performance Analysis

> **Historical benchmark record:** these measurements are from an old local
> run and are not a current performance guarantee. The README benchmark covers
> template rendering, not component rendering. Raw HTML f-string examples have
> been replaced with escaped formatting; those replacements were not measured.

## Executive Summary

**Template caching is working perfectly.** The "slowness" we observed in Hybrid rendering is NOT due to parsing overhead (that's cached!), but due to the runtime cost of template evaluation flexibility.

## Performance Hierarchy (With Caching)

```
Pure Rust PyO3:        0.3 μs   ← Direct HTML generation
Python f-strings:      0.9 μs   ← Compiled bytecode
Hybrid (cached):      14.5 μs   ← AST evaluation + FFI
Hybrid (uncached):   193.0 μs   ← Parse + AST evaluation
```

## Template Caching Effectiveness

### Simple Template
```python
template = '<span class="badge bg-{{ variant }}">{{ text }}</span>'
```

| Render | Time | Speedup |
|--------|------|---------|
| 1st (cache miss) | 288 μs | - |
| 2nd (cache hit) | 3.6 μs | **80x faster** |
| Average (cached) | 1.6 μs | **180x faster** |

**Conclusion**: Parsing overhead **eliminated** by caching.

### Complex Template (with loops)
```python
template = '''
{% for item in items %}
    <div>{{ item.name }}</div>
{% endfor %}
'''
```

| Render | Time | Speedup |
|--------|------|---------|
| 1st (cache miss) | 193 μs | - |
| 2nd (cache hit) | 23 μs | **8.3x faster** |
| Average (cached) | 15 μs | **12.9x faster** |

**Conclusion**: Parsing overhead (**170μs, 88% of first render**) eliminated by caching.

## Overhead Breakdown

### Where Does the 14μs Come From?

```
Python f-string:              0.9 μs  ← Baseline
────────────────────────────────────
Hybrid Rust template (cached): 14.5 μs

Breakdown:
  FFI boundary crossing:        ~1 μs  (Python → Rust → Python)
  Context::from_dict():         ~2 μs  (dict → Rust types)
  Template AST evaluation:     ~12 μs  (loops, variables, filters)
────────────────────────────────────
Total overhead:               13.6 μs
```

### Why Is Template Evaluation Slower Than f-strings?

**Python f-strings:**
```python
format_html('<span class="badge bg-{}">{}</span>', variant, text)
# Compiled to bytecode at parse time
# Runtime: Django escapes variable values before returning safe HTML
```

**Rust Template (cached):**
```python
render_template('<span class="badge bg-{{ variant }}">{{ text }}</span>', context)
# Template AST cached
# Runtime: Walk AST, evaluate variables, build string
```

The difference:
- **f-strings**: Bytecode compiled, direct substitution
- **Templates**: AST walking, dynamic evaluation, flexible (filters, loops, etc.)

## Performance Comparison by Use Case

### Use Case 1: Simple Badge Component

```python
from django.utils.html import format_html

# Pure Rust PyO3
# Rust component availability depends on the built extension; historical timing only.

# Python safe HTML formatting
format_html('<span class="badge bg-primary">{}</span>', 'Hello')  # historical measurement only

# Hybrid template rendering is covered by the current Component API guide.
```

The previous winner ratios describe only the historical benchmark setup.

### Use Case 2: Complex List with Loop

```python
from django.utils.html import format_html, format_html_join

items = [{'name': f'Item {i}'} for i in range(10)]

result = format_html(
    '<div>{}</div>',
    format_html_join('', '<div>{}</div>', ((item['name'],) for item in items)),
)
```

The former comparison used raw f-string interpolation and is not a safe or
current performance comparison. Use `format_html()` or a Django template.

Do not infer comparative throughput from this historical example.

### Use Case 3: Large Dataset (100 items)

```python
from django.utils.html import format_html, format_html_join

items = list(range(100))

# Escaped HTML construction
result = format_html('<ul>{}</ul>', format_html_join('\n', '<li>{}</li>', ((i,) for i in items)))  # historical measurement only

```

The old hybrid-template timing belongs to the historical benchmark; use a
configured Django template engine for a direct comparison.

### Use Case 4: Template with Filters

```python
from datetime import datetime
from django.utils.html import format_html

from django.template import Context, Engine

# A Django template supports filters.
Engine().from_string(
    '<div>{{ date|date:"Y-m-d" }}</div>',
).render(Context({'date': datetime.now()}))

# Python equivalent
format_html('<div>{}</div>', datetime.now().strftime("%Y-%m-%d"))  # historical measurement only
```

**Historical result:** the original benchmark compared these approaches on
one machine and is not a current guarantee.

**But**: Templates provide filter reusability and designer-friendly syntax.

## Throughput Analysis

| Method | Time/Render | Renders/Second |
|--------|-------------|----------------|
| Pure Rust PyO3 | 0.3 μs | **3.3 million** |
| Python f-string | 0.9 μs | **1.1 million** |
| Hybrid (simple, cached) | 1.6 μs | **625,000** |
| Hybrid (complex, cached) | 14.5 μs | **68,000** |
| Hybrid (complex, uncached) | 193 μs | **5,000** |

These historical measurements are not current performance guarantees.

A typical web request budget is often **100-500ms**; benchmark your own
application and runtime before drawing a budget conclusion.

## When to Use Each Approach

### Use Pure Rust PyO3 When:
✅ Component structure is **fixed**
✅ Need **maximum throughput** (millions of renders/sec)
✅ Building a **library component** (Badge, Button, Icon)
✅ Performance-critical path

**Example**: `RustBadge("New", "danger").render()` → 0.3μs

### Use Python f-strings When:
✅ Component is **application-specific**
✅ Logic is **simple and inline**
✅ Need **maximum flexibility**
✅ Developers > Designers

**Example**: `format_html('<div class="badge">{}</div>', text)` escapes dynamic text.

### Use Hybrid (template) When:
✅ Need **template reusability**
✅ Want **designer-friendly** syntax
✅ Need **filters** (|date, |upper, etc.)
✅ Complex **nested logic** (loops in loops)
✅ Template **inheritance** (`{% extends %}`)

**Example**: Use `template` when you need Django template syntax and autoescaping.

## Optimization Strategies

### 1. **Template Caching** ✅ IMPLEMENTED

**Status**: Already working perfectly!

```rust
// crates/djust_live/src/lib.rs
static TEMPLATE_CACHE: Lazy<DashMap<String, Arc<Template>>> = Lazy::new(|| DashMap::new());

fn render_template(template_source: String, context: HashMap<String, Value>) -> PyResult<String> {
    let template_arc = if let Some(cached) = TEMPLATE_CACHE.get(&template_source) {
        cached.clone()  // ← Cache hit!
    } else {
        let template = Template::new(&template_source)?;
        let arc = Arc::new(template);
        TEMPLATE_CACHE.insert(template_source.clone(), arc.clone());
        arc
    }
    // ...
}
```

**Impact**: Eliminates 88% of first render time (170μs → 0μs parsing overhead)

### 2. **Component-Level Rust Implementation** ✅ IMPLEMENTED

**Status**: RustBadge, RustButton already use this.

```rust
// Pure Rust - no template parsing, no AST evaluation
pub fn render(&self) -> String {
    format!(r#"<span class="badge bg-{}">{}</span>"#, self.variant, self.text)
}
```

**Impact**: 50x faster than cached templates (0.3μs vs 15μs)

### 3. **Reduce Context Creation Overhead** ⚠️ LIMITED GAINS

Current implementation already uses `AHashMap` (faster than `HashMap`).

Potential optimization: Reuse Context objects instead of creating new ones.

```python
# Current (creates new context each time)
render_template(template, {'text': 'Hello', 'variant': 'primary'})  # ~2μs context creation

# Optimized (reuse context)
ctx = Context.new()
ctx.set('text', 'Hello')
ctx.set('variant', 'primary')
template.render(ctx)  # ~0μs context creation
```

**Impact**: Could save ~2μs per render. **But**: Adds API complexity, not worth it for most use cases.

### 4. **Template Compilation (JIT)** ❌ VERY COMPLEX

Convert template AST → native code at runtime.

**Example**: Template with loop
```
{% for item in items %}
    <div>{{ item }}</div>
{% endfor %}
```

Could compile to:
```rust
fn render_compiled(items: &[Value]) -> String {
    let mut html = String::from("<div>");
    for item in items {
        html.push_str("<div>");
        html.push_str(&item.to_string());
        html.push_str("</div>");
    }
    html.push_str("</div>");
    html
}
```

**Impact**: Could match Python f-string performance (~1μs).

**Downside**: Extremely complex, security concerns (code generation), maintenance burden.

**Verdict**: **Not worth it** for the use cases we're targeting.

## Recommendations

### For djust Component Library

| Component Type | Recommended Approach | Reasoning |
|----------------|---------------------|-----------|
| **Library components** (Badge, Button, Icon) | Pure Rust PyO3 | Fixed structure, maximum performance (0.3μs) |
| **Layout components** (Card, Container) | Hybrid template | Flexible slots, designer-friendly (5-10μs) |
| **Complex interactive** (DataTable, Autocomplete) | Python with helpers | Business logic complexity, 10-50μs acceptable |

### For Application Developers

1. **Start with Python f-strings** - simplest, fastest for simple cases
2. **Upgrade to Hybrid** when you need filters, loops, or template inheritance
3. **Use Pure Rust** for library components you install from djust

### Performance Guidelines

- **< 1μs**: Pure Rust or Python f-strings
- **1-20μs**: Hybrid templates (cached) - excellent for web apps
- **20-200μs**: Hybrid templates (uncached) - fine for low-traffic routes
- **> 200μs**: Consider Python generators or pagination

**Rule of thumb**: If your component renders in under **100μs**, performance is not a concern. Focus on maintainability and developer experience.

## Conclusion

### Key Findings

1. ✅ **Template caching works perfectly** - eliminates 88% of parsing overhead
2. ✅ **All three approaches are production-ready** - even "slowest" is imperceptibly fast
3. ✅ **Python f-strings are surprisingly fast** - often beat cached templates
4. ✅ **Pure Rust is king** - but requires compilation and less flexibility

### The Real Insight

The performance difference between methods (0.3μs vs 14μs) is **academically interesting** but **practically irrelevant** for web applications.

**What matters**:
- Developer experience
- Code maintainability
- Team expertise
- Design flexibility

Choose based on your needs, not micro-benchmarks. All approaches are blazing fast.

### Final Recommendation

**Use the Component base class with automatic performance waterfall:**

```python
from django.utils.html import format_html
class Badge(Component):
    _rust_impl_class = RustBadge  # If available: 0.3μs
    template = '...'        # Fallback: 1.6μs (cached)

    def _render_custom(self):      # Last resort: 0.9μs
        return format_html('<span>...</span>')
```

This gives you:
- **Best performance** when Rust is available
- **Good performance** with cached templates
- **Excellent fallback** with Python

You get the best of all worlds without having to choose!
