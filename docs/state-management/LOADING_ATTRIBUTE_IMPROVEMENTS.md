# @loading Attribute - Future Improvements

This document tracks potential enhancements to the @loading attribute system (Phase 5).

## Current Implementation

The @loading attribute system provides Phoenix LiveView-style loading indicators with:
- `dj-loading.disable` - Disable element during loading
- `dj-loading.class="class-name"` - Add CSS class during loading
- `dj-loading.show` - Show element during loading
- `dj-loading.hide` - Hide element during loading

**Scoping**: Loading states are scoped to prevent cross-button contamination using grouping containers (`d-flex`, `btn-group`, etc.).

## Implemented Improvements ✅

### 1. ~~Grouping Classes Hardcoded for Bootstrap~~ → NOW CONFIGURABLE!

**Status**: ✅ **IMPLEMENTED** (commit c637786)

**Solution**: Grouping classes are now configurable via `LIVEVIEW_CONFIG`:

```python
# Django settings.py
LIVEVIEW_CONFIG = {
    'loading_grouping_classes': [
        'd-flex',           # Bootstrap flex container
        'flex',             # Tailwind flex
        'my-custom-group',  # Your custom class
    ],
}
```

**How it works**:
- Config passed from Python → JavaScript via `window.DJUST_LOADING_GROUPING_CLASSES`
- Falls back to Bootstrap defaults if not configured
- Maintains backward compatibility

**Benefits**:
- ✅ Works with any CSS framework (Bootstrap, Tailwind, custom)
- ✅ No code changes needed for different frameworks
- ✅ Backward compatible with existing code

## Known Limitations

### 2. E2E Test Completeness

**Issue**: Current E2E tests only verify HTML rendering, not actual client-side behavior.

**What's tested**:
- ✅ Decorator metadata in rendered HTML
- ✅ Attribute presence in DOM

**What's NOT tested in CI**:
- ❌ Actual disable/enable behavior in browser
- ❌ Class addition/removal
- ❌ Show/hide visibility changes
- ❌ WebSocket integration

**Workaround**: Manual Playwright test exists at `test_loading_attribute.py` but runs outside CI.

**Future Enhancement**: Add Playwright tests to CI/CD pipeline with headless browser.

## Nice-to-Have Enhancements

### 1. Custom Event Names

Allow explicit event name targeting for more flexible grouping:

```html
<!-- Current: Relies on parent container grouping -->
<div class="d-flex">
    <button dj-click="save_article">Save</button>
    <div dj-loading.show>Saving...</div>
</div>

<!-- Future: Explicit event targeting -->
<button dj-click="save_article">Save</button>
<div dj-loading.show="save_article">Saving...</div>
```

**Benefits**:
- More flexible element placement
- No requirement for grouping containers
- Clearer intent in templates

**Complexity**: Medium - requires parsing event names from attributes

### 2. Loading Delays

Prevent flashing spinners on fast operations (Phoenix LiveView feature):

```html
<div dj-loading.show @loading.delay="200">Loading...</div>
```

**Behavior**:
- If operation completes in < 200ms, spinner never shows
- Prevents visual jank on fast network requests

**Benefits**:
- Better perceived performance
- Less visual noise

**Complexity**: Low - add setTimeout logic to LoadingManager

### 3. Performance Optimization

**Current**: Iterates through all registered elements on every event:
```javascript
this.loadingElements.forEach((config, element) => {
    if (config.eventName === eventName) {
        // Apply state...
    }
});
```

**Issue**: O(n) lookup on every event for pages with many loading elements.

**Future**: Index elements by event name:
```javascript
this.elementsByEvent = {
    'save_article': [element1, element2],
    'delete_item': [element3]
};
```

**Benefits**:
- O(1) lookup instead of O(n)
- Faster for pages with 100+ loading elements

**Complexity**: Low - add Map-based indexing

### 4. TypeScript Definitions

Add TypeScript definitions for LoadingManager API:

```typescript
interface LoadingManager {
    register(element: HTMLElement, eventName: string): void;
    startLoading(eventName: string, triggerElement?: HTMLElement): void;
    stopLoading(eventName: string, triggerElement?: HTMLElement): void;
    isLoading(eventName: string): boolean;
    clear(): void;
}

declare global {
    const globalLoadingManager: LoadingManager;
}
```

**Benefits**:
- Better IDE autocomplete
- Type safety for custom JavaScript
- Better developer experience

**Complexity**: Low - add `.d.ts` file

### 5. Loading States API

Expose programmatic control for advanced use cases:

```javascript
// Manual control
globalLoadingManager.startLoading('save_article');
// ... do work ...
globalLoadingManager.stopLoading('save_article');

// Check state
if (globalLoadingManager.isLoading('save_article')) {
    // ...
}
```

**Current**: Already exists! Just needs documentation.

### 6. Animation Support

Add CSS animation support for smoother transitions:

```html
<div dj-loading.show @loading.animate="fade-in">Loading...</div>
```

**Behavior**:
- Applies animation class during show/hide
- Uses CSS transitions for smooth appearance

**Benefits**:
- More polished UX
- Matches modern web app expectations

**Complexity**: Medium - coordinate with CSS animation lifecycle

## Implementation Priority

### High Priority
1. ~~**Configurable grouping classes**~~ - ✅ **DONE** - Addresses framework flexibility
2. **CI E2E tests** - Improves reliability (requires GitHub Actions setup)

### Medium Priority
3. **Loading delays** - Common UX improvement
4. **Performance optimization** - Matters at scale

### Low Priority
5. **TypeScript definitions** - Nice developer experience
6. **Custom event names** - Advanced use case
7. **Animation support** - Polish

## Contributing

Want to implement one of these? See [CONTRIBUTING.md](../CONTRIBUTING.md) for guidelines.

Each enhancement should:
1. Add tests (unit + E2E)
2. Update documentation
3. Maintain backward compatibility
4. Follow existing code style

## Questions?

Open an issue on GitHub or ask in the community Discord.
