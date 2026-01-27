# Phase 5: Complete State Management API

**Status**: 🚧 In Progress
**Start Date**: 2025-11-13
**Assigned**: Claude Code

---

## Overview

Phase 5 completes the state management vision by implementing the remaining decorators and features documented in STATE_MANAGEMENT_API.md. This builds on Phases 1-4's foundation to provide a comprehensive Python-only state management system that rivals Phoenix LiveView and Laravel Livewire.

### Goals

1. **@cache Decorator** - Client-side result caching with TTL
2. **@client_state Decorator** - Multi-component state coordination via StateBus
3. **DraftModeMixin** - Draft mode for forms and text editors
4. **@loading Attributes** - Loading states for buttons and forms
5. **Comprehensive Testing** - Unit tests + E2E tests for all features
6. **Demo Applications** - Real-world examples showcasing all features

---

## Current State

### ✅ Already Implemented

**Python Decorators** (`python/djust/decorators.py`)
- ✅ `@debounce(wait)` - Delay handler until user stops interacting
- ✅ `@throttle(interval)` - Limit handler execution frequency
- ✅ `@optimistic` - Optimistic UI updates
- ✅ `@cache(ttl, key_params)` - Python decorator defined (needs JS client)
- ✅ `@client_state(keys)` - Python decorator defined (needs JS client)

**JavaScript Client** (`python/djust/static/djust/decorators.js`)
- ✅ `debounceEvent()` - Debouncing logic
- ✅ `throttleEvent()` - Throttling logic
- ✅ `optimisticEvent()` - Optimistic update logic

**JavaScript Tests** (`tests/js/`)
- ✅ debounce.test.js (21 tests passing)
- ✅ throttle.test.js (28 tests passing)
- ✅ optimistic.test.js (48 tests passing)
- **Total: 97 tests passing**

### ❌ Not Yet Implemented

**JavaScript Client Support**
- [ ] `cacheEvent()` - Client-side caching with TTL
- [ ] `clientStateEvent()` - StateBus integration for multi-component state
- [ ] `loadingAttributes()` - @loading and @loading-text support
- [x] `initDraftMode()` - Draft mode JavaScript logic (DraftManager + field monitoring) ✅

**Python Components**
- [x] `DraftModeMixin` - Draft mode for forms/editors (Python) ✅

**Testing**
- [ ] cache.test.js - Unit tests for caching
- [ ] client-state.test.js - Unit tests for StateBus
- [ ] loading.test.js - Unit tests for loading attributes
- [ ] draft-mode.test.js - Unit tests for draft mode
- [ ] E2E tests (Playwright/Cypress) - Integration tests

**Documentation & Demos**
- [ ] Update STATE_MANAGEMENT_API.md status to "Implemented"
- [ ] Demo applications showing cache, client_state, draft mode
- [ ] Performance benchmarks for each feature

---

## Architecture

### @cache Decorator Flow

```
┌─────────────────────────────────────────────────┐
│  User triggers event                            │
│  Example: Autocomplete search                   │
└────────────┬────────────────────────────────────┘
             │
┌────────────▼────────────────────────────────────┐
│  1. Client checks cache                         │
│     cacheKey = f"{handler}:{params}"           │
│     if (cache.has(cacheKey) && !expired)       │
│       return cached result (no server call)     │
└────────────┬────────────────────────────────────┘
             │ Cache miss or expired
┌────────────▼────────────────────────────────────┐
│  2. Send event to server                        │
│     WebSocket → LiveViewConsumer                │
└────────────┬────────────────────────────────────┘
             │
┌────────────▼────────────────────────────────────┐
│  3. Server handler executes                     │
│     @cache(ttl=60) def search(query): ...      │
│     Returns results                             │
└────────────┬────────────────────────────────────┘
             │
┌────────────▼────────────────────────────────────┐
│  4. Client receives response                    │
│     cache.set(cacheKey, result, ttl)           │
│     Update UI with result                       │
└─────────────────────────────────────────────────┘
```

### @client_state Decorator Flow

```
┌─────────────────────────────────────────────────┐
│  Multiple components share state                │
│  Example: Filter + List + Summary               │
└────────────┬────────────────────────────────────┘
             │
┌────────────▼────────────────────────────────────┐
│  StateBus (client-side)                         │
│  - Stores shared state keys                     │
│  - Notifies all subscribed components           │
│  - Optimistically updates all views             │
└────────────┬────────────────────────────────────┘
             │
┌────────────▼────────────────────────────────────┐
│  Component A updates state                      │
│  @client_state(keys=["filter"])                │
│  def change_filter(value):                     │
│      stateBus.set("filter", value)             │
└────────────┬────────────────────────────────────┘
             │
┌────────────▼────────────────────────────────────┐
│  Component B reacts to state change             │
│  @client_state(keys=["filter"])                │
│  Automatically receives new filter value        │
│  Re-renders with updated data                   │
└─────────────────────────────────────────────────┘
```

### DraftModeMixin Flow

```
┌─────────────────────────────────────────────────┐
│  User edits form/text                           │
│  Example: Blog post editor                      │
└────────────┬────────────────────────────────────┘
             │
┌────────────▼────────────────────────────────────┐
│  1. Local storage sync                          │
│     @input → save to localStorage               │
│     Debounced every 500ms                       │
└────────────┬────────────────────────────────────┘
             │
┌────────────▼────────────────────────────────────┐
│  2. Server sync (optional)                      │
│     Periodically send draft to server           │
│     Server stores in database/cache             │
└────────────┬────────────────────────────────────┘
             │
┌────────────▼────────────────────────────────────┐
│  3. Restore on mount                            │
│     Check localStorage for draft                │
│     Prompt user: "Restore draft?"               │
│     Load draft if confirmed                     │
└─────────────────────────────────────────────────┘
```

---

## Task Breakdown

### Task 1: Implement @cache JavaScript Client (3 hours)

**Location**: `python/djust/static/djust/decorators.js`

**Subtasks**:
- [ ] Add `resultCache` Map for storing cached results
- [ ] Implement `cacheEvent(eventName, eventData, config, sendEvent)`
- [ ] Cache key generation from handler name + params
- [ ] TTL-based expiration checking
- [ ] Integration with event pipeline
- [ ] Add to embedded version in `live_view.py`

**Success Criteria**:
- Cache hits avoid server calls
- TTL expiration works correctly
- Cache keys unique per handler + params
- Memory bounded (LRU eviction)

**Example**:
```javascript
export const resultCache = new Map(); // Map<cacheKey, {result, expiresAt}>

export function cacheEvent(eventName, eventData, config, sendEvent) {
    const {ttl = 60, keyParams = []} = config;

    // Generate cache key from handler + params
    const cacheKey = generateCacheKey(eventName, eventData, keyParams);

    // Check cache
    const cached = resultCache.get(cacheKey);
    if (cached && Date.now() < cached.expiresAt) {
        // Cache hit - no server call
        console.log(`[@cache] Cache hit: ${cacheKey}`);
        return Promise.resolve(cached.result);
    }

    // Cache miss - call server
    console.log(`[@cache] Cache miss: ${cacheKey}`);
    return sendEvent(eventName, eventData).then(result => {
        // Store in cache
        resultCache.set(cacheKey, {
            result,
            expiresAt: Date.now() + (ttl * 1000)
        });
        return result;
    });
}
```

**Testing**:
- [ ] Unit tests for cache hits/misses
- [ ] TTL expiration tests
- [ ] Cache key generation tests
- [ ] Memory management tests

---

### Task 2: Implement @client_state JavaScript Client (4 hours)

**Location**: `python/djust/static/djust/decorators.js`

**Subtasks**:
- [ ] Add `StateBus` class for shared state
- [ ] Implement `clientStateEvent(eventName, eventData, config, sendEvent)`
- [ ] State subscription/notification system
- [ ] Multi-component coordination
- [ ] Integration with event pipeline
- [ ] Add to embedded version in `live_view.py`

**Success Criteria**:
- Multiple components share state
- State updates propagate to all subscribers
- Optimistic updates work
- Server sync maintains consistency

**Example**:
```javascript
export class StateBus {
    constructor() {
        this.state = new Map(); // Map<key, value>
        this.subscribers = new Map(); // Map<key, Set<callback>>
    }

    set(key, value) {
        this.state.set(key, value);
        this.notify(key, value);
    }

    get(key) {
        return this.state.get(key);
    }

    subscribe(key, callback) {
        if (!this.subscribers.has(key)) {
            this.subscribers.set(key, new Set());
        }
        this.subscribers.get(key).add(callback);
    }

    notify(key, value) {
        const callbacks = this.subscribers.get(key) || new Set();
        callbacks.forEach(cb => cb(value));
    }
}

export const stateBus = new StateBus();

export function clientStateEvent(eventName, eventData, config, sendEvent) {
    const {keys = []} = config;

    // Update StateBus with new state
    keys.forEach(key => {
        if (eventData[key] !== undefined) {
            stateBus.set(key, eventData[key]);
        }
    });

    // Send to server for persistence
    return sendEvent(eventName, eventData);
}
```

**Testing**:
- [ ] Unit tests for StateBus set/get/subscribe
- [ ] Multi-component coordination tests
- [ ] State propagation tests
- [ ] Server sync tests

---

### Task 3: Implement DraftModeMixin (Python + JavaScript) (5 hours)

**Location**: `python/djust/mixins.py` (new file)

**Subtasks**:
- [x] Create `DraftModeMixin` Python class ✅
- [x] localStorage integration (JavaScript) ✅
- [x] Auto-save with debouncing (500ms) ✅
- [x] Restore draft on mount ✅
- [ ] User prompt for draft restoration (auto-restores currently)
- [ ] Server-side draft storage (optional - not implemented)
- [x] Add to embedded version in `live_view.py` ✅

**Success Criteria**:
- Drafts saved to localStorage automatically
- User prompted to restore on mount
- Works with forms and text editors
- Optional server sync

**Example (Python)**:
```python
class DraftModeMixin:
    """
    Mixin for draft mode support.

    Automatically saves form/editor state to localStorage
    and prompts user to restore on mount.

    Usage:
        class BlogPostView(DraftModeMixin, LiveView):
            draft_key = "blog_post_draft"  # localStorage key
            draft_fields = ["title", "content"]  # Fields to save
            draft_save_interval = 0.5  # Auto-save every 500ms
    """

    draft_key: str = "draft"
    draft_fields: List[str] = []
    draft_save_interval: float = 0.5

    def mount(self, request, **kwargs):
        super().mount(request, **kwargs)

        # Inject draft mode JavaScript
        self._draft_mode_enabled = True

    def save_draft(self, **draft_data):
        """Save draft to server (optional)."""
        # Store in session/cache/database
        pass

    def load_draft(self):
        """Load draft from server (optional)."""
        # Retrieve from session/cache/database
        pass
```

**Example (JavaScript)**:
```javascript
export class DraftMode {
    constructor(config) {
        this.key = config.draftKey;
        this.fields = config.draftFields;
        this.interval = config.draftSaveInterval * 1000;
        this.debounceTimer = null;
    }

    init() {
        // Check for existing draft
        const draft = this.loadDraft();
        if (draft && this.shouldRestore(draft)) {
            this.restoreDraft(draft);
        }

        // Setup auto-save
        this.fields.forEach(field => {
            const el = document.querySelector(`[name="${field}"]`);
            el?.addEventListener('input', () => this.saveDraft());
        });
    }

    saveDraft() {
        clearTimeout(this.debounceTimer);
        this.debounceTimer = setTimeout(() => {
            const draft = {};
            this.fields.forEach(field => {
                const el = document.querySelector(`[name="${field}"]`);
                if (el) draft[field] = el.value;
            });
            draft.timestamp = Date.now();
            localStorage.setItem(this.key, JSON.stringify(draft));
        }, this.interval);
    }

    loadDraft() {
        const data = localStorage.getItem(this.key);
        return data ? JSON.parse(data) : null;
    }

    shouldRestore(draft) {
        // Prompt user
        const age = Date.now() - draft.timestamp;
        const ageMinutes = Math.floor(age / 60000);
        return confirm(`Restore draft from ${ageMinutes} minutes ago?`);
    }

    restoreDraft(draft) {
        this.fields.forEach(field => {
            const el = document.querySelector(`[name="${field}"]`);
            if (el && draft[field]) {
                el.value = draft[field];
            }
        });
    }

    clearDraft() {
        localStorage.removeItem(this.key);
    }
}
```

**Testing**:
- [ ] localStorage save/load tests
- [ ] Auto-save debouncing tests
- [ ] Restore prompt tests
- [ ] Field restoration tests

---

### Task 4: Implement @loading HTML Attributes (2 hours)

**Location**: `python/djust/static/djust/decorators.js`

**Subtasks**:
- [ ] Parse `@loading` attribute from HTML
- [ ] Parse `@loading-text` attribute
- [ ] Show loading state on event send
- [ ] Restore original state on response
- [ ] Disable buttons during loading
- [ ] Integration with event pipeline
- [ ] Add to embedded version in `live_view.py`

**Success Criteria**:
- Buttons show loading state automatically
- Text changes during loading
- Buttons disabled during loading
- Original state restored on response

**Example (HTML)**:
```html
<button dj-click="submit_form" @loading @loading-text="Saving...">
    Save
</button>

<!-- Becomes during loading: -->
<button dj-click="submit_form" @loading @loading-text="Saving..." disabled>
    Saving...
</button>
```

**Example (JavaScript)**:
```javascript
export function handleLoadingAttributes(element, eventName, isLoading) {
    if (!element.hasAttribute('@loading')) return;

    if (isLoading) {
        // Store original state
        element._originalText = element.textContent;
        element._originalDisabled = element.disabled;

        // Apply loading state
        const loadingText = element.getAttribute('@loading-text');
        if (loadingText) {
            element.textContent = loadingText;
        }
        element.disabled = true;
        element.classList.add('loading');
    } else {
        // Restore original state
        if (element._originalText !== undefined) {
            element.textContent = element._originalText;
        }
        element.disabled = element._originalDisabled || false;
        element.classList.remove('loading');
    }
}
```

**Testing**:
- [ ] Loading state activation tests
- [ ] Text replacement tests
- [ ] Disabled state tests
- [ ] State restoration tests

---

### Task 5: JavaScript Unit Tests (4 hours)

**Location**: `tests/js/`

**Subtasks**:
- [ ] `cache.test.js` - Cache decorator tests (~20 tests)
- [ ] `client-state.test.js` - StateBus tests (~15 tests)
- [ ] `loading.test.js` - Loading attributes tests (~10 tests)
- [ ] `draft-mode.test.js` - Draft mode tests (~15 tests)
- [ ] Update vitest.config.js if needed

**Success Criteria**:
- All tests passing
- Edge cases covered
- Integration with existing tests
- Coverage > 90%

**Example Test**:
```javascript
// cache.test.js
describe('Cache Decorator', () => {
    it('should return cached result on cache hit', async () => {
        const sendFn = vi.fn().mockResolvedValue({results: []});

        // First call - cache miss
        await cacheEvent('search', {query: 'test'}, {ttl: 60}, sendFn);
        expect(sendFn).toHaveBeenCalledTimes(1);

        // Second call - cache hit
        await cacheEvent('search', {query: 'test'}, {ttl: 60}, sendFn);
        expect(sendFn).toHaveBeenCalledTimes(1); // No additional call
    });

    it('should expire cache after TTL', async () => {
        // Test TTL expiration...
    });
});
```

---

### Task 6: E2E Integration Tests (4 hours)

**Tool**: Playwright or Cypress

**Location**: `tests/e2e/` (new directory)

**Subtasks**:
- [ ] Setup Playwright/Cypress
- [ ] Test @cache with autocomplete
- [ ] Test @client_state with multi-component coordination
- [ ] Test DraftModeMixin with form editor
- [ ] Test @loading with button clicks
- [ ] Test all decorators combined

**Success Criteria**:
- Real browser testing
- WebSocket communication verified
- All decorators work end-to-end
- Performance measured

**Example Test**:
```javascript
// cache-autocomplete.spec.js
test('autocomplete uses cache on second search', async ({ page }) => {
    await page.goto('/autocomplete');

    // First search - should call server
    await page.fill('input[name="query"]', 'test');
    await page.waitForResponse('/ws/live');

    // Second search - should use cache (no server call)
    await page.fill('input[name="query"]', '');
    await page.fill('input[name="query"]', 'test');
    // No new network request

    // Verify results shown from cache
    await expect(page.locator('.result')).toHaveCount(5);
});
```

---

### Task 7: Demo Applications (3 hours)

**Location**: `examples/demo_project/demo_app/demos/`

**Subtasks**:
- [ ] Autocomplete with @cache
- [ ] Multi-component dashboard with @client_state
- [ ] Blog editor with DraftModeMixin
- [ ] Form with @loading attributes
- [ ] Combined example showing all features

**Success Criteria**:
- Real-world use cases
- Copy-paste ready code
- Commented and explained
- Performance metrics shown

**Example - Autocomplete**:
```python
class AutocompleteView(LiveView):
    template_name = "autocomplete.html"

    def mount(self, request):
        self.query = ""
        self.results = []

    @debounce(wait=0.3)
    @cache(ttl=300, key_params=["query"])
    def search(self, query: str = "", **kwargs):
        """Cached search with 5 min TTL"""
        self.query = query
        if query:
            self.results = Product.objects.filter(
                name__icontains=query
            )[:10]
        else:
            self.results = []
```

---

## Timeline

| Task | Estimated Time | Status |
|------|---------------|--------|
| 1. @cache JavaScript | 3 hours | ⏳ Not Started |
| 2. @client_state JavaScript | 4 hours | ⏳ Not Started |
| 3. DraftModeMixin | 5 hours | ✅ Complete |
| 4. @loading Attributes | 2 hours | ⏳ Not Started |
| 5. JavaScript Unit Tests | 4 hours | ⏳ Not Started |
| 6. E2E Integration Tests | 4 hours | ⏳ Not Started |
| 7. Demo Applications | 3 hours | ⏳ Not Started |
| **Total** | **25 hours** | **~3-4 days** |

---

## Success Metrics

### Code Metrics
- [ ] All decorators have JavaScript client support
- [ ] Test coverage > 90% for all new code
- [ ] JavaScript bundle size < 10KB (currently ~7KB)
- [ ] Zero TypeScript/linting errors

### Functional Metrics
- [ ] @cache reduces server calls by >80% for repeated queries
- [ ] @client_state enables <100ms cross-component updates
- [x] DraftModeMixin prevents data loss on accidental navigation ✅
- [ ] @loading provides instant user feedback

### Performance Benchmarks
- [ ] Cache lookup < 1ms
- [ ] StateBus propagation < 10ms
- [ ] Draft save (debounced) < 50ms
- [ ] Loading state update < 5ms

---

## Risk Assessment

### Technical Risks

1. **JavaScript Bundle Size**
   - **Risk**: Adding cache, client_state, draft mode increases bundle
   - **Mitigation**: Code splitting, tree shaking, minification
   - **Target**: Keep total <10KB

2. **Memory Leaks**
   - **Risk**: Cache/StateBus accumulate data indefinitely
   - **Mitigation**: LRU eviction, TTL expiration, cleanup on unmount
   - **Monitoring**: Add memory usage tracking

3. **Browser Compatibility**
   - **Risk**: localStorage/Map not available in all browsers
   - **Mitigation**: Feature detection, graceful degradation
   - **Testing**: Test in IE11+, Safari, Firefox, Chrome

### Integration Risks

1. **Decorator Conflicts**
   - **Risk**: Multiple decorators on same handler interfere
   - **Mitigation**: Defined decorator order, pipeline architecture
   - **Documentation**: Clear examples of decorator combinations

2. **StateBus Race Conditions**
   - **Risk**: Multiple components update same state simultaneously
   - **Mitigation**: Queueing, last-write-wins strategy
   - **Testing**: Concurrent update tests

---

## Next Steps After Phase 5

### Phase 6 Options

**A. Production Readiness**
- Performance benchmarks (#53, #46)
- Production deployment guides
- Monitoring and observability
- Error tracking integration

**B. Template Engine Enhancements**
- `{% url %}` tag (#58)
- `{% include %}` tag (#59)
- Advanced filters (#62)
- Template caching (#66)

**C. Developer Experience**
- Live reload improvements
- Debug toolbar
- Error messages
- Developer tools

---

## References

- [STATE_MANAGEMENT_API.md](STATE_MANAGEMENT_API.md) - Complete API reference
- [STATE_MANAGEMENT_PATTERNS.md](STATE_MANAGEMENT_PATTERNS.md) - Usage patterns
- [STATE_MANAGEMENT_EXAMPLES.md](STATE_MANAGEMENT_EXAMPLES.md) - Code examples
- [IMPLEMENTATION_PHASE1.md](IMPLEMENTATION_PHASE1.md) - Template foundation
- [IMPLEMENTATION_PHASE2.md](IMPLEMENTATION_PHASE2.md) - Debounce/throttle
- [IMPLEMENTATION_PHASE3.md](IMPLEMENTATION_PHASE3.md) - Optimistic updates
- [IMPLEMENTATION_PHASE4.md](IMPLEMENTATION_PHASE4.md) - Component system
