# Phase 3: Optimistic Updates + Phase 2 Critical Fix

## Summary

This PR implements Phase 3 (Optimistic Updates) and fixes a **critical Phase 2 bug** where `@debounce` and `@throttle` decorators were completely non-functional.

## What's Included

### ✅ Phase 3: Optimistic Updates (`@optimistic` decorator)
- Instant UI feedback before server validation
- Automatic revert on server errors
- Works with checkboxes, inputs, selects, and buttons
- Demo views showing optimistic todo toggles and counter buttons

### 🐛 Critical Phase 2 Fix: Decorator Support in Embedded JS
- **Discovered**: Phase 2 decorators (`@debounce`, `@throttle`) were only implemented in external `client.js`, which was never loaded
- **Fixed**: Ported all Phase 2 decorator support to embedded JavaScript in `live_view.py`
- **Impact**: `@debounce` and `@throttle` now actually work for the first time

### 🐛 Critical Bug Fixes (6 major bugs fixed)
1. **Empty patches array handling**: Server sent `{"patches": []}` without html fallback
2. **JSON double-parsing**: `applyPatches()` was parsing already-parsed data
3. **Optimistic state clearing**: Button stayed disabled due to timing issue
4. **State reset bug**: `mount()` called on POST requests, destroying restored state
5. **Patches as string**: Server sent JSON string instead of array
6. **Unused file cleanup**: Removed `client.js` that was never loaded

---

## Detailed Changes

### Phase 3: Optimistic Updates

**New Decorator:**
```python
from djust.decorators import optimistic

class MyView(LiveView):
    @optimistic
    def toggle_item(self, item_id: int, checked: bool = False, **kwargs):
        item = self.items.get(id=item_id)
        item.completed = checked
        item.save()
```

**Client-Side Features:**
- Optimistic state tracking (Maps for updates and pending events)
- Heuristic-based updates (checkboxes, inputs, selects, buttons)
- Loading indicators (`.optimistic-pending` CSS class)
- Error handling with shake animation (`.optimistic-error`)
- Server-wins conflict resolution
- Automatic cleanup on WebSocket disconnect

**Demo Views:**
- `/demos/optimistic-todo/` - Todo list with instant checkbox toggles
- `/demos/optimistic-counter/` - Counter with loading button states

**What Works:**
- ✅ Checkboxes toggle instantly
- ✅ Form inputs update instantly
- ✅ Buttons show loading state
- ✅ Server errors revert optimistic updates with animation
- ✅ Works in both HTTP and WebSocket modes

---

### Phase 2 Critical Fix: Embedded JavaScript

**Problem Discovered:**

djust uses **embedded JavaScript** in `live_view.py` (not external `client.js`). When Phase 2 was implemented in PR #40, decorators were only added to `client.js`, which is never loaded by the browser.

**Result**: `@debounce` and `@throttle` decorators did nothing - events were sent immediately.

**Fix:**

Ported complete Phase 2 implementation to embedded JavaScript:
- `debounceTimers` and `throttleState` Maps
- `debounceEvent()` with `max_wait` support
- `throttleEvent()` with `leading`/`trailing` options
- `sendEventImmediate()` for decorator bypass
- Pipeline integration into `handleEvent()`
- WebSocket disconnect cleanup

**Impact:**
- ✅ `@debounce(wait=0.5, max_wait=2.0)` now delays events until user stops typing
- ✅ `@throttle(interval=0.1)` now limits event frequency
- ✅ Demos at `/demos/debounce/` and `/demos/throttle/` now work correctly

---

## Critical Bug Fixes

### Bug 1: Empty Patches Array Without HTML Fallback

**Symptom**: Counter increments once then stops. Button disables but never re-enables.

**Root Cause**:
```python
# Server sends empty array
if patch_count <= PATCH_THRESHOLD:  # 0 <= 100 → True
    return JsonResponse({"patches": [], "version": 3})  # No html field!
```

**Client receives**:
```javascript
if (data.patches && ...) { }       // false - empty array
else if (data.html) { }            // false - field doesn't exist!
// Neither branch executes → no DOM update
```

**Fix**: Only send patches if array is non-empty; send HTML fallback for empty patches.

---

### Bug 2: JSON Double-Parsing

**Symptom**: `TypeError: "[object Object]" is not valid JSON`

**Root Cause**:
```javascript
const data = await response.json();  // Parses JSON → object
const patches = data.patches;        // Already an array
applyPatches(patches);               // Tries to JSON.parse(array) → error
```

**Fix**: Removed `JSON.parse()` from `applyPatches()` since data is already parsed.

---

### Bug 3: Optimistic State Clearing Timing

**Symptom**: Button stays disabled after first click.

**Root Cause**:
```javascript
applyPatches(data.patches);          // Replaces button element in DOM
clearOptimisticState(eventName);     // Tries to restore state on STALE element
// Button reference is detached from DOM → state never restored
```

**Fix**: Call `clearOptimisticState()` BEFORE applying patches while element reference is valid.

---

### Bug 4: mount() Resetting State on POST

**Symptom**: Counter increments once (5→6) then gets stuck at 6.

**Root Cause**:
```python
# POST handler
saved_state = request.session.get(view_key, {})  # count=6
for key, value in saved_state.items():
    setattr(self, key, value)                    # self.count = 6

self.mount(request, **kwargs)                    # self.count = 5 ❌
self.increment()                                 # self.count = 6
request.session[view_key] = {"count": 6}        # Always saves 6
```

**Fix**: Only call `mount()` when no saved state exists (new session).

---

### Bug 5: Patches Sent as JSON String

**Symptom**: Client can't distinguish empty patches from non-empty.

**Root Cause**:
```python
return JsonResponse({"patches": patches_json, ...})  # String: "[]" or "[{...}]"
```

**Client**:
```javascript
if (data.patches) {  // "[]" is truthy!
    applyPatches(data.patches);  // Expects array, gets string
}
```

**Fix**: Send parsed array instead of JSON string.

---

### Bug 6: Unused External client.js

**Problem**: External `python/djust/static/djust/client.js` was never loaded but contained all Phase 2 logic.

**Fix**: Deleted unused file after porting to embedded JS.

---

## Testing

**Automated Test**:
```bash
bash test_counter_curl.sh
```

**Manual Testing**:
- ✅ Counter increments/decrements correctly
- ✅ Button shows loading state
- ✅ State persists across requests
- ✅ Fresh page load resets to initial state
- ✅ Optimistic todo checkboxes toggle instantly
- ✅ Debounce demo delays search events
- ✅ Throttle demo limits scroll events

---

## Files Changed

**Core Framework:**
- `python/djust/live_view.py` - Embedded JS with Phase 2 + Phase 3 decorators, bug fixes
- `python/djust/decorators.py` - Added `@optimistic` decorator
- `python/djust/static/djust/client.js` - DELETED (unused file)

**Demo Views:**
- `examples/demo_project/demo_app/views/optimistic_counter_demo.py` - NEW
- `examples/demo_project/demo_app/views/optimistic_todo_demo.py` - NEW
- `examples/demo_project/demo_app/urls.py` - Added routes for optimistic demos

**Documentation:**
- `docs/IMPLEMENTATION_PHASE3.md` - NEW (950 lines tracking document)
- `test_counter_curl.sh` - NEW (automated test)
- `python/tests/test_optimistic_updates.py` - NEW (test suite)

---

## Migration Notes

**No Breaking Changes**:
- All changes are additive or bug fixes
- Existing code continues to work
- Phase 2 demos that were broken now work correctly

**New Feature**:
```python
from djust.decorators import optimistic

@optimistic
def my_handler(self, **kwargs):
    # Updates happen instantly on client
    # Server validates and corrects if needed
    pass
```

---

## Performance

**Bundle Size**: No significant change (optimistic JS is small, deleted unused client.js)

**Latency**:
- Optimistic updates: **0ms perceived latency** (instant UI feedback)
- Debounce: Configurable delay (default 300ms)
- Throttle: Configurable interval (default 100ms)

---

## Known Limitations

**Optimistic Updates:**
- Only works for self-contained elements (checkbox toggles itself)
- Does NOT work for buttons that update other elements (e.g., counter display)
- This is intentional - keeps framework generic and predictable

**Example**:
```python
# ✅ Works - checkbox updates itself
@optimistic
def toggle_todo(self, checked: bool, **kwargs):
    self.completed = checked

# ❌ Doesn't optimistically update counter display
@optimistic
def increment(self):
    self.count += 1  # Button shows loading, but counter waits for server
```

---

## Next Steps

**Phase 4**: Component System
- LiveComponent with isolated state
- Parent-child communication
- Nested component updates

**Future Enhancements**:
- JavaScript unit tests for decorators (deferred to issue #41)
- More comprehensive integration tests
- Performance benchmarks

---

## Commits Summary

**Phase 3 Implementation:**
- `8a3b99f` - Add Phase 3 tracking document
- `540a834` - Implement @optimistic in client.js (later moved to embedded)
- `5e0ed75` - Add optimistic demo views
- `86388d6` - Add optimistic to embedded JavaScript (correct location)

**Critical Bug Fixes:**
- `f2f9f69` - Port Phase 2 decorators to embedded JS, delete unused client.js
- `5701e63` - Fix optimistic state clearing timing
- `cbac64d` - Fix patches sent as JSON string
- `8d57a5c` - Fix JSON double-parsing
- `eb22791` - Fix empty patches HTML fallback
- `73cff6c` - Fix mount() resetting state on POST

**Polish:**
- `55ef4f0` - Add console logging for debugging
- `d80350d` - Remove artificial network delays from demo

---

## Review Checklist

- [x] All tests pass (automated curl test)
- [x] Manual testing completed (counter, todo, debounce, throttle)
- [x] No breaking changes
- [x] Documentation added (IMPLEMENTATION_PHASE3.md)
- [x] Code follows existing patterns
- [x] Phase 2 bug fixed (critical)
- [x] Phase 3 fully implemented
- [x] Demo views work correctly
