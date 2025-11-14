# PR #83: @loading HTML Attribute (Phase 5) - Complete Implementation

## Overview

This PR implements Phoenix LiveView-style `@loading` HTML attributes for djust, enabling declarative loading indicators without writing JavaScript. This completes Phase 5 of the state management system.

## Features Implemented ✅

### 1. Core @loading Attributes

Four loading modifiers with full scoping support:

```html
<!-- Disable button during operation -->
<button @click="save" @loading.disable>Save</button>

<!-- Add CSS class during loading -->
<button @click="save" @loading.class="opacity-25">Save</button>

<!-- Show spinner during loading -->
<div @loading.show style="display: none;">Loading...</div>

<!-- Hide content during loading -->
<div @loading.hide>Content</div>

<!-- Combine multiple modifiers -->
<button @loading.disable @loading.class="opacity-25">Save</button>
```

### 2. Scoped Loading State (No Cross-Contamination)

**Problem Solved**: Multiple buttons with same event handler no longer interfere with each other.

**Solution**: Loading states scoped to trigger element + explicit grouping containers:

```html
<!-- ✅ Independent buttons -->
<div class="card-body">
    <button @click="save" @loading.disable>Save A</button>
    <button @click="save" @loading.disable>Save B</button>
</div>
<!-- Clicking Save A only affects Save A -->

<!-- ✅ Grouped elements -->
<div class="d-flex gap-2">
    <button @click="save">Save</button>
    <div @loading.show>Spinner</div>
</div>
<!-- Clicking Save affects both button and spinner -->
```

### 3. Configurable Grouping Classes (NEW!)

**Framework Flexibility**: No longer hardcoded to Bootstrap!

```python
# Django settings.py
LIVEVIEW_CONFIG = {
    'loading_grouping_classes': [
        'd-flex',           # Bootstrap
        'flex',             # Tailwind
        'my-custom-group',  # Custom
    ],
}
```

**Benefits**:
- Works with Bootstrap, Tailwind, or custom frameworks
- No code changes needed
- Backward compatible

### 4. Debug Logging

Enable detailed logging for troubleshooting:

```html
<script>
    window.djustDebug = true;  // Shows registration, state changes
</script>
```

**Output**:
```javascript
[Loading] Registered modifiers for "save": [{type: 'disable'}, {type: 'class', value: 'opacity-25'}]
[Loading] Started: save <button>
[Loading] Applied disable to element
[Loading] Applied class "opacity-25" to element
[Loading] Stopped: save <button>
```

### 5. Comprehensive Test Suite

- **Automated Test Page**: `/tests/loading/` with 6+ interactive tests
- **Playwright E2E Test**: `test_loading_attribute.py` verifies actual browser behavior
- **Unit Tests**: 10 E2E tests in `test_phase5_decorators.py`

### 6. Complete Documentation

- **API Reference**: `docs/STATE_MANAGEMENT_API.md` - Complete @loading docs with scoping rules
- **Patterns**: `docs/STATE_MANAGEMENT_PATTERNS.md` - Best practices and examples
- **Future Work**: `docs/LOADING_ATTRIBUTE_IMPROVEMENTS.md` - Roadmap for enhancements
- **Test Guide**: Updated `docs/TESTING_PAGES.md`

## Key Improvements Over Initial Implementation

### Visual Differentiation
Changed from `opacity-50` → `opacity-25` for more dramatic loading effect:
- **Before**: opacity-50 (0.5) looked too similar to Bootstrap disabled (0.65)
- **After**: opacity-25 (0.25) provides clear visual feedback

### Code Quality
- Added comprehensive inline documentation (25+ line comment blocks)
- Explained sibling event detection algorithm
- Documented scoping rules with examples
- Created future improvements roadmap

### Test Coverage
- Automated tests run on page load
- Manual Playwright test for CI verification
- Test index page lists all available tests

## Technical Implementation

### Architecture

```
┌─────────────────────────────────────────────┐
│  Template (HTML)                            │
│  <button @loading.disable>Save</button>     │
└─────────────────────────────────────────────┘
           ↓ Registration (bindLiveViewEvents)
┌─────────────────────────────────────────────┐
│  LoadingManager (JavaScript)                │
│  - Tracks elements by event name            │
│  - Preserves original state                 │
│  - Scopes to trigger + grouped siblings     │
└─────────────────────────────────────────────┘
           ↓ Event triggers
┌─────────────────────────────────────────────┐
│  startLoading(eventName, triggerElement)    │
│  - Applies modifiers (disable, class, etc.) │
│  - Only to related elements                 │
└─────────────────────────────────────────────┘
           ↓ Server response
┌─────────────────────────────────────────────┐
│  stopLoading(eventName, triggerElement)     │
│  - Restores original state                  │
│  - Removes classes, re-enables elements     │
└─────────────────────────────────────────────┘
```

### Key Components

1. **LoadingManager Class** (`live_view.py:3326-3520`)
   - `register()` - Register elements with @loading attributes
   - `startLoading()` - Apply loading state
   - `stopLoading()` - Restore original state
   - `isRelatedElement()` - Determine scoping

2. **Element Registration** (`live_view.py:2403-2460`)
   - Strategy 1: Element has its own event handler
   - Strategy 2: Sibling has event handler (for spinners)

3. **Config Integration** (`config.py:90-97`, `live_view.py:1416-1429`)
   - Python config → JavaScript via `window.DJUST_LOADING_GROUPING_CLASSES`
   - JSON serialization for array passing

## Breaking Changes

None! Fully backward compatible.

## Migration Guide

No migration needed. This is a new feature, all existing code continues to work.

## Testing

```bash
# Start development server
make start

# Run automated tests (browser)
open http://localhost:8002/tests/loading/

# Run Playwright E2E test
python test_loading_attribute.py

# Run Python unit tests
pytest tests/e2e/test_phase5_decorators.py -k loading
```

## Documentation Updates

- ✅ STATE_MANAGEMENT_API.md - Complete @loading reference
- ✅ STATE_MANAGEMENT_PATTERNS.md - Updated all examples
- ✅ LOADING_ATTRIBUTE_IMPROVEMENTS.md - Future enhancements
- ✅ test_index.py - Added @loading to test catalog
- ✅ TESTING_PAGES.md - Already comprehensive

## Future Improvements (Tracked in LOADING_ATTRIBUTE_IMPROVEMENTS.md)

### HIGH Priority (Next PR)
- **CI E2E Tests**: Add Playwright to GitHub Actions
- **Loading Delays**: `@loading.delay="200"` to prevent spinner flash

### MEDIUM Priority
- **Performance Optimization**: Event indexing for O(1) lookup
- **Custom Event Names**: `@loading.show="save_article"` explicit targeting

### LOW Priority
- **TypeScript Definitions**: Better IDE support
- **Animation Support**: `@loading.animate="fade-in"`

## Performance

- **Registration**: O(n) on page load (acceptable)
- **State Changes**: O(n) per event (acceptable for most pages)
- **Memory**: ~40 bytes per registered element
- **Bundle Size**: +~2 KB to client.js (now 7.1 KB total)

## Commits in this PR

1. **68cf83b** - feat(phase5): Add @loading HTML attribute support
2. **99ff4f8** - test: Add automated test page for @loading attributes
3. **7f9e782** - fix(phase5): Integrate LoadingManager with event handling
4. **9f14f0a** - fix(phase5): Support @loading on sibling elements
5. **66b4ce7** - fix(@loading): Restrict loading state to explicit grouping containers
6. **5774489** - feat(@loading): Add debug logging and improve visual differentiation
7. **5ca8f01** - docs(@loading): Document scoping behavior and debug mode
8. **447b05b** - docs(@loading): Update patterns to use implemented attributes
9. **c95d6c6** - fix(tests): Add missing RequestFactory import
10. **edff6c9** - docs(@loading): Address code review feedback
11. **c637786** - feat(@loading): Make grouping classes configurable
12. **a92b38e** - docs(@loading): Mark configurable grouping classes as implemented
13. **1c50acf** - fix(js): Restore missing closing brace for isRelatedElement method

## Reviewers

Please check:
- [ ] Automated test page works at `/tests/loading/`
- [ ] Buttons operate independently (no cross-contamination)
- [ ] Spinners show/hide correctly
- [ ] Debug logging works (`window.djustDebug = true`)
- [ ] Documentation is clear and complete
- [ ] Custom grouping classes can be configured

## Related Issues

- Closes #xx (if applicable)
- Part of Phase 5 State Management milestone

---

**Ready to merge**: All tests passing, documentation complete, backward compatible.
