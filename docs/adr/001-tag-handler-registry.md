# ADR-001: Tag Handler Registry for Django Template Tags

**Status**: Accepted
**Date**: 2026-01-25
**Deciders**: Project maintainers
**Related Issues**: [#37](https://github.com/johnrtipton/djust/issues/37)

## Context

The djust Rust template engine currently handles template tags via a hardcoded match statement in `parser.rs`. When it encounters an unknown tag like `{% url %}`, it silently treats it as a comment, resulting in empty output.

Django-specific tags (`{% url %}`, `{% static %}`, `{% csrf_token %}`) require access to Django's runtime (URL resolver, static file handling) which isn't available in Rust. The current workaround requires users to pass pre-resolved URLs via context, which is error-prone and breaks when URLs are used inside loops.

### Problem Statement

```django
{% for post in posts %}
  <a href="{% url 'post_detail' post.slug %}">{{ post.title }}</a>
{% endfor %}
```

This common pattern fails because:
1. The `{% url %}` tag is unknown to Rust → treated as comment → empty output
2. Even with pre-processing, loop variables like `post.slug` aren't resolved until render time
3. A two-pass marker system was attempted but adds complexity and fragility

## Decision Drivers

- **Correctness**: `{% url %}` must work inside loops with dynamic variables
- **Performance**: Minimal overhead for built-in tags (if, for, block)
- **Extensibility**: Users should be able to register custom template tags
- **Maintainability**: Solution should be clean and not require complex multi-pass processing

## Considered Options

### Option A: Pre/Post Processing with Markers

Continue current approach of pre-processing templates to insert markers, letting Rust render, then post-processing to resolve markers.

**Pros**: No Rust changes needed
**Cons**: Complex, fragile, requires regex parsing, two extra passes

### Option B: Rust Registry with Python Callbacks

Implement a tag handler registry in Rust that can call Python handlers via PyO3 for unknown tags.

**Pros**: Single pass, clean architecture, extensible
**Cons**: GIL acquisition overhead (~15-50µs per custom tag)

### Option C: Full Python Fallback

If any unknown tag is encountered, fall back to Django's template engine entirely.

**Pros**: Full compatibility
**Cons**: Loses all Rust performance benefits, all-or-nothing approach

## Decision

**Chosen Option: B - Rust Registry with Python Callbacks**

Implement a Tag Handler Registry pattern:

1. Built-in tags (`{% if %}`, `{% for %}`, etc.) remain as native Rust match arms → zero overhead
2. Unknown tags check a Python callback registry
3. If handler found → create `Node::CustomTag` → render via Python callback
4. If no handler → treat as comment (current behavior)

### Architecture

```
Template: {% url 'post' post.slug %}
    ↓ Rust parser encounters "url" tag
    ↓ Not in built-in match → check Python registry
    ↓ Found UrlTagHandler → create Node::CustomTag
    ↓ Rust renderer hits Node::CustomTag
    ↓ Acquires GIL, calls Python handler with args + context
    ↓ Handler calls Django's reverse()
    ↓ Returns "/posts/my-slug/"
Final HTML with correct URL
```

### Performance Impact

| Operation | Overhead |
|-----------|----------|
| GIL acquisition | ~1-5µs |
| Python function call | ~10-20µs |
| Type conversion (args) | ~5-20µs |
| **Total per custom tag** | **~15-50µs** |

For typical templates (50 tags, 5 custom): ~75-250µs added → 5-25% overhead on 1-5ms render time. This is acceptable.

## Implementation Plan

### Phase 1: Rust Infrastructure

1. **New file**: `crates/djust_templates/src/registry.rs`
   - `TAG_HANDLERS: Mutex<HashMap<String, Py<PyAny>>>`
   - `register_tag_handler(name, handler)` PyO3 function
   - `get_handler(name)` lookup function

2. **Modify**: `crates/djust_templates/src/parser.rs`
   - Add `Node::CustomTag { name, args, body }` variant
   - In tag match default case: check registry before creating `Node::Comment`

3. **Modify**: `crates/djust_templates/src/renderer.rs`
   - Add match arm for `Node::CustomTag`
   - Acquire GIL, call Python handler, return result

### Phase 2: Python Handlers

1. **New file**: `python/djust/template_tags/__init__.py`
   - `TagHandler` base class with `render(args, body, context)` method
   - `@register(name)` decorator

2. **New file**: `python/djust/template_tags/url.py`
   - `UrlTagHandler` implementing Django URL resolution
   - Supports positional args, kwargs, and context variable resolution

### Phase 3: Integration

1. **Modify**: `python/djust/apps.py`
   - Register built-in handlers on app ready

2. **Remove**: `python/djust/url_resolver.py`
   - Delete the marker-based workaround

### Phase 4: Testing

1. **New file**: `tests/unit/test_tag_registry.py`
   - Handler registration tests
   - URL tag with various argument patterns
   - Loop variable resolution
   - Performance benchmarks

## Consequences

### Positive

- **Clean architecture**: Single-pass rendering, no markers or regex
- **Extensibility**: Easy to add `{% static %}`, `{% csrf_token %}`, custom tags
- **Correctness**: Loop variables work naturally
- **Performance**: Built-in tags unaffected, custom tags have acceptable overhead

### Negative

- **GIL contention**: Python callbacks require GIL, may affect concurrent rendering
- **Complexity**: Registry adds abstraction layer
- **Testing**: Need to test Python/Rust boundary carefully

### Neutral

- **Migration**: Existing workarounds (passing URLs via context) continue to work

## Verification

1. All existing tests pass
2. `{% url 'name' arg %}` works with static arguments
3. `{% url 'name' var.attr %}` works with context variables
4. `{% for item in items %}{% url 'name' item.id %}{% endfor %}` works in loops
5. Custom tag registration works via Python API
6. Performance overhead is <50µs per custom tag call

## References

- [Django Template Tags](https://docs.djangoproject.com/en/4.2/howto/custom-template-tags/)
- [PyO3 GIL Management](https://pyo3.rs/v0.22/python-from-rust)
- [Phoenix LiveView](https://hexdocs.pm/phoenix_live_view/) (architectural inspiration)
