# djust Naming Convention Proposal (v1.0)

**Status**: DRAFT - For Discussion
**Created**: February 13, 2026
**Proposed for**: djust 1.0

---

## Executive Summary

**Current Problem**: Inconsistent naming (some attributes are `dj-*`, others are `data-djust-*`)
**Proposed Solution**: All framework attributes use `dj-*` prefix with smart defaults

---

## Design Principles

From the djust manifesto:

1. **Complexity Is the Enemy** → Simplest possible API
2. **Developer First** → What developers naturally expect
3. **AI-Ready by Design** → Clear, consistent, predictable patterns

**Key Decision**: Optimize for **developer experience** over **HTML5 validator compliance**

---

## Proposed Naming Convention

### Rule 1: All Framework Attributes Use `dj-*` Prefix

**Consistency**: One namespace, one pattern, easy to remember.

```html
<!-- View mounting -->
<div dj-view="ProductListView">

<!-- Event handling -->
<button dj-click="save">
<input dj-input="search">
<form dj-submit="save_form">

<!-- Data binding -->
<input dj-model="search_query">
<input dj-model.debounce-500="search_query">

<!-- Loading states -->
<button dj-loading.disable>
<div dj-loading.show>

<!-- Update strategies -->
<div dj-update="append">
<div dj-update="prepend">

<!-- Confirmation dialogs -->
<button dj-click="delete" dj-confirm="Are you sure?">
```

**No exceptions** - if it's djust functionality, it's `dj-*`.

### Rule 2: Smart Defaults (Convention Over Configuration)

**Default behavior**: `dj-view` element is automatically the VDOM root.

```html
<!-- 99% case - just this: -->
<div dj-view="MyView">
  <h1>{{ title }}</h1>
  <p>{{ content }}</p>
</div>
```

**No `dj-root` needed** - the framework infers it.

### Rule 3: Explicit Overrides When Needed

**Optional `dj-root`**: Only use when you need a different root element.

```html
<!-- 1% edge case - explicit root: -->
<div dj-view="MyView">
  <header class="view-metadata">
    <!-- This is OUTSIDE the update boundary -->
    <span>Connected to MyView</span>
  </header>

  <div dj-root>
    <!-- Only THIS gets VDOM updates -->
    <h1>{{ title }}</h1>
    <p>{{ content }}</p>
  </div>
</div>
```

**When to use explicit `dj-root`**:
- Wrapper elements with static metadata
- Multiple update boundaries (very rare)
- Performance optimization (skip diffing wrapper)

**99% of developers will never need this.**

### Rule 4: HTML5-Compliant Alternative (Optional)

For developers who care about HTML5 validation, support `data-dj-*` as an alias:

```html
<!-- Recommended (short): -->
<div dj-view="MyView">
  <button dj-click="save">

<!-- HTML5-compliant (verbose): -->
<div data-dj-view="MyView">
  <button data-dj-click="save">
```

**Both work identically** - framework treats them the same.

**Recommendation**: Don't document `data-dj-*` prominently. Mention it only in FAQ:
> "If your team requires HTML5 validation, you can prefix all `dj-*` attributes with `data-` (e.g., `data-dj-view`). This is optional and rarely needed."

---

## Complete Attribute Reference

### View Mounting

| Attribute | Required | Default | Example |
|-----------|----------|---------|---------|
| `dj-view` | ✅ Yes | - | `dj-view="ProductList"` |
| `dj-root` | ❌ Optional | Element with `dj-view` | `dj-root` (no value) |

### Event Handlers

| Attribute | Purpose | Example |
|-----------|---------|---------|
| `dj-click` | Click events | `dj-click="save"` |
| `dj-input` | Input events (every keystroke) | `dj-input="search"` |
| `dj-change` | Change events | `dj-change="filter_category"` |
| `dj-submit` | Form submission | `dj-submit="save_form"` |
| `dj-keydown` | Keyboard events | `dj-keydown="handle_key"` |
| `dj-focus` | Focus events | `dj-focus="on_focus"` |
| `dj-blur` | Blur events | `dj-blur="on_blur"` |

### Data Binding

| Attribute | Purpose | Example |
|-----------|---------|---------|
| `dj-model` | Two-way binding | `dj-model="search_query"` |
| `dj-model.lazy` | Sync on blur | `dj-model.lazy="email"` |
| `dj-model.debounce-N` | Debounced sync | `dj-model.debounce-500="search"` |

### Loading States

| Attribute | Purpose | Example |
|-----------|---------|---------|
| `dj-loading.disable` | Disable during load | `<button dj-loading.disable>` |
| `dj-loading.show` | Show during load | `<div dj-loading.show>` |
| `dj-loading.hide` | Hide during load | `<div dj-loading.hide>` |
| `dj-loading.class` | Add class during load | `dj-loading.class="opacity-50"` |

### Update Strategies

| Attribute | Purpose | Example |
|-----------|---------|---------|
| `dj-update="replace"` | Replace content (default) | - |
| `dj-update="append"` | Append new items | For chat, logs |
| `dj-update="prepend"` | Prepend new items | For feeds |

### Other Directives

| Attribute | Purpose | Example |
|-----------|---------|---------|
| `dj-confirm` | Confirmation dialog | `dj-confirm="Delete this?"` |
| `data-key` | List item identity | `data-key="{{ item.id }}"` |

**Note**: `data-key` keeps `data-` prefix because it's a **data attribute** (holds data), not a djust directive.

---

## Comparison: Current vs. Proposed

### Current (v0.3)

```html
<!-- Inconsistent - two naming patterns: -->
<div dj-view="ProductList" dj-root>
  <button dj-click="add_to_cart">Add to Cart</button>
  <input dj-model="search_query">
</div>
```

**Problems**:
- ❌ Two prefixes: `data-djust-*` and `dj-*`
- ❌ Dual-attribute requirement confusing
- ❌ Verbose: 18 chars for `dj-view="`
- ❌ Causes common mistakes (T002, T005 errors)

### Proposed (v1.0)

```html
<!-- Consistent - one pattern: -->
<div dj-view="ProductList">
  <button dj-click="add_to_cart">Add to Cart</button>
  <input dj-model="search_query">
</div>
```

**Benefits**:
- ✅ One prefix: `dj-*` for everything
- ✅ Single attribute (auto-inferred root)
- ✅ Concise: 7 chars for `dj-view="`
- ✅ Eliminates T002, T005 checks (not needed)
- ✅ What developers naturally expect

---

## HTML5 Validity: Why We Don't Care

### Modern Frameworks Ignore HTML5 Validation

| Framework | Attributes | HTML5 Valid? | Market Share |
|-----------|------------|--------------|--------------|
| Vue 3 | `v-if`, `v-for`, `v-model` | ❌ No | ~40% |
| Alpine.js | `x-data`, `x-show`, `x-on` | ❌ No | Growing |
| htmx | `hx-get`, `hx-post`, `hx-swap` | ❌ No | Growing |
| Svelte | `bind:`, `on:` | ❌ No | ~5% |
| **djust (current)** | **`data-djust-*`** | **✅ Yes** | **New** |

**Observation**: djust is the **only modern framework** prioritizing validator compliance over developer experience.

### Why Validators Don't Matter

1. **Browsers don't care** - Custom attributes work perfectly
2. **Developers disable warnings** - Too noisy for modern development
3. **Build tools ignore them** - Webpack, Vite don't validate HTML
4. **Users never see validators** - Only developer tools
5. **DX > compliance** - Developer productivity matters more

### The `data-` Tax

**Cost of `data-` prefix**:
- **61% more typing**: `dj-view` (7 chars) → `dj-view` (18 chars)
- **Cognitive load**: Why is this different from `dj-click`?
- **Inconsistency**: Mixed naming patterns confuse developers
- **Documentation overhead**: Need to explain two systems

**Benefit**:
- **HTML validator doesn't complain** (but nobody runs validators anyway)

**Verdict**: Cost >> Benefit

---

## Migration Path

### Phase 1: djust 0.4 (Deprecation)

**Support both syntaxes**, prefer new:

```python
# In client.js and Python code:
view_name = (
    element.getAttribute('dj-view') or
    element.getAttribute('data-dj-view') or
    element.getAttribute('dj-view')  # Legacy
)

if element.hasAttribute('dj-view'):
    console.warn('dj-view is deprecated, use dj-view')
```

**Auto-infer root**:
```python
# If no explicit root, use dj-view element
root = element.querySelector('[dj-root]') or element
```

### Phase 2: djust 1.0 (Breaking Change)

**Only `dj-*` supported**:
- `dj-view` (required)
- `dj-root` (optional, auto-inferred)
- `data-dj-view` (works as alias, undocumented)
- `dj-view` (raises clear error)

**Migration guide**:
```bash
# Simple find/replace:
find . -name "*.html" -exec sed -i '' 's/dj-view=/dj-view=/g' {} \;
find . -name "*.html" -exec sed -i '' 's/dj-root/dj-root/g' {} \;
```

**Error messages**:
```
DJE-001: Detected legacy attribute 'dj-view'

Migration required for djust 1.0:
  Replace: <div dj-view="MyView" dj-root>
  With:    <div dj-view="MyView">

The dj-root attribute is now auto-inferred.
See: https://djust.org/docs/migration/v1.0
```

---

## Edge Cases & Advanced Usage

### Nested Views

```html
<!-- Auto-inferred roots: -->
<div dj-view="OuterView">
  <h1>Outer View</h1>

  <div dj-view="InnerView">
    <h2>Inner View</h2>
  </div>
</div>
```

Each `dj-view` automatically becomes its own root.

### Multiple LiveViews on Same Page

```html
<!-- Sidebar -->
<aside dj-view="NavMenu">
  <ul>...</ul>
</aside>

<!-- Main content -->
<main dj-view="ProductList">
  <div>...</div>
</main>

<!-- Chat widget -->
<div dj-view="ChatWidget">
  <div>...</div>
</div>
```

Each has independent root, no conflicts.

### Explicit Root for Performance

```html
<!-- Optimization: skip diffing the wrapper -->
<div dj-view="HeavyView" class="view-wrapper-with-complex-css">
  <header class="static-header">
    <!-- Complex animations, never changes -->
    <div class="logo-animation"></div>
  </header>

  <div dj-root>
    <!-- Only diff this part -->
    <p>{{ dynamic_content }}</p>
  </div>
</div>
```

**Rare**: Only use if profiling shows wrapper diffing is expensive.

### Template Inheritance

```html
<!-- base.html -->
<html>
<body>
  {% block content %}{% endblock %}
</body>
</html>

<!-- my_view.html -->
{% extends "base.html" %}
{% block content %}
<div dj-view="{{ view_name }}">
  <h1>{{ title }}</h1>
</div>
{% endblock %}
```

Works naturally - no special handling needed.

---

## FAQ

### Q: Will `data-dj-*` still work?

**A**: Yes, as an undocumented alias. Framework treats `dj-view` and `data-dj-view` identically. Use if your team requires HTML5 validation, but it's not recommended.

### Q: What if I need `dj-root` on a different element?

**A**: Just add it:
```html
<div dj-view="MyView">
  <header>Static metadata</header>
  <div dj-root>Updates here</div>
</div>
```

### Q: Can I have multiple `dj-root` elements?

**A**: No. One root per view. If you need multiple update zones, use separate views or LiveComponents.

### Q: What about `data-key`? Should that be `dj-key`?

**A**: No. `data-key` holds **data** (the item ID), it's not a djust **directive**. Keeping `data-key` is correct per HTML5 semantics.

### Q: Will this break my existing templates?

**A**: Not until djust 1.0. The 0.4 release will support both syntaxes with deprecation warnings.

### Q: What if HTML validators complain?

**A**: Disable the rule. Add to your `.htmlhintrc`:
```json
{
  "attr-lowercase": false,
  "attr-no-duplication": true
}
```

Or use `data-dj-*` aliases (works, but verbose).

---

## Implementation Checklist

### Client JS (`static/djust/client.js`)
- [ ] Detect `dj-view` attribute (primary)
- [ ] Support `data-dj-view` as alias (undocumented)
- [ ] Warn if `dj-view` used (deprecated)
- [ ] Auto-infer root from `dj-view` element
- [ ] Support explicit `dj-root` if present
- [ ] Remove `dj-root` support (breaking)

### Python (`python/djust/`)
- [ ] Update template tag library
- [ ] Update checks (remove T002, T005)
- [ ] Update error messages
- [ ] Update test templates

### Documentation
- [ ] Update all guides
- [ ] Update quick start
- [ ] Update best practices
- [ ] Add migration guide
- [ ] Update error code reference

### Tests
- [ ] Test `dj-view` attribute detection
- [ ] Test `data-dj-view` alias
- [ ] Test auto-inferred root
- [ ] Test explicit `dj-root` override
- [ ] Test nested views
- [ ] Test multiple views per page

---

## Decision Record

**Proposed**: February 13, 2026
**Status**: DRAFT - Seeking feedback

**Key Decision**: Prioritize developer experience over HTML5 validation compliance.

**Rationale**:
1. All modern frameworks use short attribute names
2. HTML validators are not used in modern development
3. Consistency reduces cognitive load
4. Auto-inferred root eliminates common mistakes
5. Aligns with djust manifesto ("Complexity is the enemy")

**Trade-offs**:
- ✅ Simpler, cleaner API
- ✅ Consistent naming
- ✅ Fewer mistakes
- ❌ HTML validators warn (but nobody cares)
- ❌ Breaking change (but easy migration)

**Recommendation**: **APPROVE** for djust 1.0

---

## Appendix: Alternative Considered

### Alternative A: Keep Current (`data-djust-*`)

```html
<div dj-view="MyView" dj-root>
```

**Rejected**: Verbose, inconsistent with `dj-*` directives.

### Alternative B: All Long Form (`data-dj-*`)

```html
<div data-dj-view="MyView">
  <button data-dj-click="save">
  <input data-dj-model="query">
```

**Rejected**: Even more verbose, nobody does this.

### Alternative C: Different Prefix (`djust-*`)

```html
<div djust-view="MyView">
  <button djust-click="save">
```

**Rejected**: Longer than `dj-*`, no benefit.

### Alternative D: No Prefix (Custom Elements)

```html
<djust-view name="MyView">
  <button @click="save">
```

**Rejected**: Requires custom elements API, more complex to implement, breaks template inheritance.

---

## Summary

**Proposed Convention**:
1. **All attributes**: `dj-*` prefix
2. **View mounting**: `dj-view="ClassName"` (auto-inferred root)
3. **Optional root**: `dj-root` (only when needed)
4. **HTML5 alias**: `data-dj-*` (undocumented, works if needed)

**Migration**: Simple find/replace in djust 1.0

**Result**: Consistent, simple, predictable naming that aligns with modern frameworks and djust's manifesto.

---

**Feedback welcome!** Discuss at: https://github.com/djust-org/djust/discussions/XXX
