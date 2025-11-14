# Template Inheritance Design - Rust VDOM Renderer

## Overview

Add `{% extends %}` and `{% block %}` support to the Rust template renderer, eliminating the need for dual Django/Rust template systems while maintaining DRY code organization.

## Goals

1. **Single Template System**: All templates use Rust renderer with Django-like syntax
2. **Whole Page VDOM**: Entire merged template becomes one VDOM tree
3. **Update Anywhere**: Any `{{ var }}` in any block can be updated via patches
4. **Familiar Syntax**: Django developers feel at home
5. **Code Reuse**: Base templates for shared layouts (nav, footer, head, etc.)

## Syntax

### Base Template
```html
<!-- templates/base.html -->
<!DOCTYPE html>
<html>
<head>
  <title>{% block title %}My App{% endblock %}</title>
  {% block head %}
    <link href="/static/bootstrap.css">
  {% endblock %}
</head>
<body>
  <nav>
    <span>Cart: {{ cart_count }}</span>
    <span>Notifications: {{ notification_count }}</span>
  </nav>

  {% block content %}
    <p>Default content</p>
  {% endblock %}

  <footer>{{ footer_text }}</footer>
</body>
</html>
```

### Child Template
```html
<!-- templates/products.html -->
{% extends "base.html" %}

{% block title %}Products - {{ site_name }}{% endblock %}

{% block content %}
  <h1>{{ page_title }}</h1>
  <div class="products">
    {{ product_list }}
  </div>
{% endblock %}
```

### Merged Result
```html
<!DOCTYPE html>
<html>
<head>
  <title>Products - {{ site_name }}</title>
  <link href="/static/bootstrap.css">
</head>
<body>
  <nav>
    <span>Cart: {{ cart_count }}</span>
    <span>Notifications: {{ notification_count }}</span>
  </nav>

  <h1>{{ page_title }}</h1>
  <div class="products">
    {{ product_list }}
  </div>

  <footer>{{ footer_text }}</footer>
</body>
</html>
```

All variables are reactive - entire page is one VDOM.

## Architecture

### Phase 1: Template Resolution (Parse Time)

```rust
pub struct TemplateInheritance {
    base_path: Option<String>,           // {% extends "base.html" %}
    blocks: HashMap<String, BlockNode>,  // {% block name %}...{% endblock %}
}

pub fn resolve_inheritance(template_path: &str) -> String {
    let template = load_template(template_path);
    let parsed = parse_template_tags(&template);

    if let Some(base_path) = parsed.extends {
        // Load base template
        let base_template = load_template(&base_path);
        let base_parsed = parse_template_tags(&base_template);

        // Merge blocks: child blocks override base blocks
        let merged_blocks = merge_blocks(&base_parsed.blocks, &parsed.blocks);

        // Replace {% block %} tags in base with merged content
        replace_blocks_in_template(&base_template, &merged_blocks)
    } else {
        template
    }
}
```

### Phase 2: VDOM Construction (Same as Current)

```rust
pub fn render_with_diff(merged_template: &str, state: &State) -> (String, Option<String>, u64) {
    // Build VDOM from entire merged template
    let vdom = parse_html_to_vdom(merged_template);

    // Diff against previous VDOM
    let patches = diff_vdom(&previous_vdom, &vdom);

    // Render HTML with variable substitution
    let html = render_vdom(&vdom, state);

    (html, patches, version)
}
```

## Implementation Plan

### 1. Parser Enhancements (`crates/djust_vdom/src/parser.rs`)

```rust
#[derive(Debug, Clone)]
pub enum TemplateTag {
    Extends(String),              // {% extends "base.html" %}
    Block(String, Vec<Node>),     // {% block name %}...{% endblock %}
    Variable(String),             // {{ var }}
}

pub struct TemplateParser {
    // Existing fields...
    extends: Option<String>,
    blocks: HashMap<String, Vec<Node>>,
}

impl TemplateParser {
    pub fn parse_extends(&mut self, input: &str) -> Option<String> {
        // {% extends "base.html" %}
        // Extract template path
    }

    pub fn parse_block(&mut self, input: &str) -> (String, Vec<Node>) {
        // {% block content %}...{% endblock %}
        // Extract block name and parse inner content
    }
}
```

### 2. Template Loader

```rust
pub struct TemplateLoader {
    template_dirs: Vec<PathBuf>,
    cache: HashMap<String, String>,
}

impl TemplateLoader {
    pub fn load(&self, path: &str) -> Result<String, TemplateError> {
        // Check cache
        if let Some(cached) = self.cache.get(path) {
            return Ok(cached.clone());
        }

        // Search template directories
        for dir in &self.template_dirs {
            let full_path = dir.join(path);
            if full_path.exists() {
                let content = fs::read_to_string(full_path)?;
                return Ok(content);
            }
        }

        Err(TemplateError::NotFound(path.to_string()))
    }
}
```

### 3. Block Merging

```rust
pub fn merge_blocks(
    base_blocks: &HashMap<String, Vec<Node>>,
    child_blocks: &HashMap<String, Vec<Node>>,
) -> HashMap<String, Vec<Node>> {
    let mut merged = base_blocks.clone();

    // Child blocks override base blocks
    for (name, nodes) in child_blocks {
        merged.insert(name.clone(), nodes.clone());
    }

    merged
}
```

### 4. Python API Update

```python
# python/djust/live_view.py

class LiveView(View):
    template_name = None      # Path to template (can use {% extends %})
    template_string = None    # Inline template string
    template_dirs = []        # Additional template directories

    def get_template(self) -> str:
        """
        Get the final merged template.
        Template inheritance is resolved in Rust.
        """
        if self.template_string:
            return self.template_string
        elif self.template_name:
            # Rust will handle {% extends %} resolution
            return self.template_name
        else:
            raise ValueError("Must specify template_name or template_string")
```

## Migration Path

### Phase 1: Implement in Rust (Non-breaking)

- Add template inheritance to Rust renderer
- Keep existing dual-renderer support
- New views can use `{% extends %}` in Rust templates
- Old views continue working

### Phase 2: Migrate Examples

```python
# OLD (dual renderer)
class KitchenSinkView(LiveView):
    template_name = "kitchen_sink_content.html"
    wrapper_template = "kitchen_sink_page.html"

# NEW (single renderer with inheritance)
class KitchenSinkView(LiveView):
    template_name = "kitchen_sink.html"
```

```html
<!-- templates/kitchen_sink.html -->
{% extends "base.html" %}

{% block title %}Kitchen Sink{% endblock %}

{% block content %}
  <h1>{{ page_title }}</h1>
  <div class="components">
    {{ components }}
  </div>
{% endblock %}
```

### Phase 3: Deprecate wrapper_template

- Remove wrapper injection logic from `live_view.py`
- Update documentation
- Clean up dual-renderer examples

## Benefits

### Before (Dual Renderer)
```python
# Two template systems to learn
# Complex wrapper injection logic
# Can only update content inside LiveView wrapper
# Confusing mental model

template_name = "content.html"        # Rust template
wrapper_template = "page.html"        # Django template
```

### After (Template Inheritance)
```python
# One template system
# Simple inheritance (Django-like)
# Update anything anywhere
# Clear mental model

template_name = "products.html"
# {% extends "base.html" %} inside template
```

## Example: Cart Updates

With template inheritance, updating the cart in navbar is trivial:

```python
class ProductListView(LiveView):
    template_name = "products.html"  # {% extends "base.html" %}

    def mount(self, request):
        self.cart_count = 0
        self.products = get_products()

    def add_to_cart(self, product_id):
        add_product_to_cart(product_id)
        self.cart_count += 1  # Updates {{ cart_count }} in base template nav
```

VDOM patch will update both:
- Cart count in navbar (from base template)
- Product list (from child template)

## Testing Strategy

### Unit Tests
```rust
#[test]
fn test_parse_extends() {
    let template = r#"{% extends "base.html" %}"#;
    let parser = TemplateParser::new(template);
    assert_eq!(parser.extends, Some("base.html".to_string()));
}

#[test]
fn test_parse_block() {
    let template = r#"{% block content %}<h1>Hello</h1>{% endblock %}"#;
    let parser = TemplateParser::new(template);
    assert!(parser.blocks.contains_key("content"));
}

#[test]
fn test_merge_blocks() {
    let base = "{% block title %}Base{% endblock %}";
    let child = "{% extends 'base.html' %}{% block title %}Child{% endblock %}";
    let merged = resolve_inheritance(child);
    assert!(merged.contains("Child"));
}
```

### Integration Tests
```python
def test_template_inheritance_rendering():
    view = ProductListView()
    view.mount(request)
    html = view.render(request)

    # Should include base template elements
    assert '<nav>' in html
    assert 'Cart: 0' in html

    # Should include child template elements
    assert '<h1>Products</h1>' in html
```

## Performance Considerations

1. **Template Caching**: Cache merged templates (already done)
2. **Lazy Loading**: Only load base templates when needed
3. **VDOM Size**: Whole page VDOM is larger but:
   - Still much smaller than full HTML (~10-50KB)
   - Diffing is O(n) and very fast
   - Patches are tiny (~100-500 bytes)

## Open Questions

1. **Nested Inheritance**: Support `{% extends %}` chains? (A extends B extends C)
   - **Decision**: Start with one level, add multi-level later if needed

2. **Block Composition**: Support `{{ block.super }}` for extending parent blocks?
   - **Decision**: Add in Phase 2 if users request it

3. **Template Discovery**: Search multiple directories like Django?
   - **Decision**: Yes, use `TEMPLATES` dirs from Django settings

## Success Criteria

- ✅ Single template system (no dual renderer)
- ✅ `{% extends %}` and `{% block %}` work correctly
- ✅ All existing functionality preserved (VDOM patches, event handlers)
- ✅ Can update any variable anywhere in merged template
- ✅ Examples migrated to new pattern
- ✅ Documentation updated
- ✅ All tests passing

## Timeline

- **Week 1**: Rust parser enhancements, basic inheritance
- **Week 2**: Template loader, block merging, Python API
- **Week 3**: Migrate examples, comprehensive testing
- **Week 4**: Documentation, deprecate wrapper pattern

## Related Issues

This addresses several pain points:
- Dual template system confusion
- Limited update scope (only content wrapper)
- Complex wrapper injection logic
- Steeper learning curve for new users
