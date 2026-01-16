# Template Resolution in djust

## Overview

djust uses Rust for template inheritance resolution to achieve blazing-fast rendering. It's critical that the Rust template loader resolves templates in the **exact same order** as Django to ensure consistent behavior.

## Django's Template Search Order

Django searches for templates in this order:

1. **DIRS** from each `TEMPLATES` config (in the order they appear in settings)
2. **APP_DIRS** (if `APP_DIRS=True`) - searches `<app>/templates/` directories in app registration order

Example:
```python
# settings.py
TEMPLATES = [
    {
        'BACKEND': 'django.template.backends.django.DjangoTemplates',
        'DIRS': [BASE_DIR / 'templates'],  # Searched FIRST
        'APP_DIRS': True,                   # Searched SECOND
    }
]
```

**Result**: `project/templates/base.html` is found BEFORE `demo_app/templates/base.html`

## Implementation in djust

The `LiveView._load_template()` method builds the template directory list in the exact same order:

```python
# Step 1: Add DIRS from all TEMPLATES configs
for template_config in settings.TEMPLATES:
    if "DIRS" in template_config:
        template_dirs.extend(template_config["DIRS"])

# Step 2: Add app template directories
for template_config in settings.TEMPLATES:
    if template_config.get("APP_DIRS", False):
        for app_config in apps.get_app_configs():
            templates_dir = Path(app_config.path) / "templates"
            if templates_dir.exists():
                template_dirs.append(str(templates_dir))
```

This list is then passed to Rust's `resolve_template_inheritance()` function.

## Runtime Verification

djust includes runtime verification to catch template resolution mismatches:

```python
# Get the actual path Django resolved
django_resolved_path = template.origin.name

# Check if Rust would find the same template
rust_would_find = None
for template_dir in template_dirs_str:
    candidate = os.path.join(template_dir, self.template_name)
    if os.path.exists(candidate):
        rust_would_find = os.path.abspath(candidate)
        break

# Warn if there's a mismatch
if rust_would_find != django_resolved_path:
    print(f"[WARNING] Template resolution mismatch!")
```

If you see this warning, it indicates a bug in the template directory ordering logic.

## Common Pitfalls

### Multiple `base.html` Files

If you have multiple `base.html` files in different locations:

```
project/
├── templates/
│   └── base.html          # Found FIRST (DIRS)
└── demo_app/
    └── templates/
        └── base.html      # Found SECOND (APP_DIRS)
```

Django (and djust) will use `project/templates/base.html` because it comes first in the search order.

**Solution**: Ensure ALL versions of a template have the same blocks defined, or consolidate to a single version.

### Template Inheritance Blocks

When a child template extends a parent:

```html
<!-- counter.html -->
{% extends "base.html" %}

{% block dependencies_css %}{{ dependencies_css|safe }}{% endblock %}
```

The parent template (`base.html`) **MUST** have the `dependencies_css` block defined:

```html
<!-- base.html -->
<head>
    {% block dependencies_css %}{% endblock %}
</head>
```

If the parent doesn't have the block, the child's override will be silently ignored!

## Testing

Run the template resolution tests to verify correct behavior:

```bash
pytest python/djust/tests/test_template_resolution.py -v
```

These tests verify:
1. Template directory ordering matches Django
2. DIRS is searched before APP_DIRS
3. Duplicate templates use the first found (regression test)

## Debugging

To debug template resolution issues:

1. **Check Django's resolution**:
   ```python
   from django.template import loader
   template = loader.get_template('base.html')
   print(template.origin.name)  # Shows which file Django found
   ```

2. **Check the template directory list**:
   Add debug logging in `LiveView._load_template()`:
   ```python
   print(f"Template dirs in order: {template_dirs_str}")
   ```

3. **Look for warnings**:
   The runtime verification will print warnings if Rust and Django disagree

## Best Practices

1. **Avoid duplicate templates**: Use unique names or consolidate into a single template
2. **Define all blocks in parent**: Ensure parent templates have all blocks that children might override
3. **Test template inheritance**: Run `pytest` to verify template resolution works correctly
4. **Document template locations**: Comment in settings.py which templates are in DIRS vs APP_DIRS

## Related Files

- `python/djust/live_view.py` - Template loading logic (line ~210)
- `crates/djust_templates/src/inheritance.rs` - Rust template inheritance resolver
- `python/djust/tests/test_template_resolution.py` - Template resolution tests
