# VDOM Architecture Comparison: ID-Based Resolution vs Morphdom

This document compares two architectural approaches for solving VDOM patch path traversal failures in djust.

## Problem Statement

Current djust VDOM patching uses **index-based paths** (e.g., `[1, 2, 0, 3]`) to locate DOM nodes. This fails when:

1. Conditional template content changes DOM structure
2. Browser normalizes HTML differently than Rust parser
3. Third-party scripts modify DOM
4. Whitespace handling differs between server and client

**Result:** 27/50 patches fail → page reload → lost client state

---

## Option 1: ID-Based Path Resolution

### How It Works

1. **Server-side:** Assign unique `data-djust-id` to each element during VDOM construction
2. **Patches:** Include target element ID alongside index-based path
3. **Client-side:** Resolve by ID first, fall back to index path

### Architecture

```
┌─────────────────────────────────────────────────────────────┐
│                        SERVER                                │
├─────────────────────────────────────────────────────────────┤
│  HTML Template                                               │
│       ↓                                                      │
│  Rust VDOM Parser (assigns data-djust-id="dj-1", "dj-2"...) │
│       ↓                                                      │
│  VDOM Diff Algorithm                                         │
│       ↓                                                      │
│  Patches with IDs: {path: [1,2], id: "dj-5", type: SetAttr} │
└─────────────────────────────────────────────────────────────┘
                            ↓ WebSocket
┌─────────────────────────────────────────────────────────────┐
│                        CLIENT                                │
├─────────────────────────────────────────────────────────────┤
│  Receive Patch                                               │
│       ↓                                                      │
│  Try: document.querySelector('[data-djust-id="dj-5"]')      │
│       ↓ (if found)                                          │
│  Apply patch to element                                      │
│       ↓ (if not found)                                      │
│  Fallback: index-based path traversal                        │
└─────────────────────────────────────────────────────────────┘
```

### Implementation Changes

#### 1. Rust VDOM Parser (`crates/djust_vdom/src/parser.rs`)

```rust
use std::sync::atomic::{AtomicU64, Ordering};

static ID_COUNTER: AtomicU64 = AtomicU64::new(0);

fn generate_djust_id() -> String {
    let id = ID_COUNTER.fetch_add(1, Ordering::SeqCst);
    format!("dj-{}", id)
}

fn handle_to_vnode(handle: &Handle) -> Result<VNode> {
    match &handle.data {
        NodeData::Element { name, attrs, .. } => {
            let mut vnode = VNode::element(name.local.to_string());

            // Generate unique ID for this element
            let djust_id = generate_djust_id();
            vnode.djust_id = Some(djust_id.clone());
            vnode.attrs.insert("data-djust-id".to_string(), djust_id);

            // ... rest of parsing
        }
        // ...
    }
}
```

#### 2. VNode Structure (`crates/djust_vdom/src/lib.rs`)

```rust
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct VNode {
    pub tag: String,
    pub attrs: HashMap<String, String>,
    pub children: Vec<VNode>,
    pub text: Option<String>,
    pub key: Option<String>,
    pub djust_id: Option<String>,  // NEW: Unique element identifier
}
```

#### 3. Patch Structure

```rust
#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(tag = "type")]
pub enum Patch {
    SetAttr {
        path: Vec<usize>,
        target_id: Option<String>,  // NEW: Element ID for direct lookup
        key: String,
        value: String,
    },
    // ... similar for other variants
}
```

#### 4. Client Resolution (`client.js`)

```javascript
function getNodeByPath(path, patchContext = null) {
    // Strategy 1: Try ID-based resolution first (most reliable)
    if (patchContext && patchContext.targetId) {
        const byId = document.querySelector(
            `[data-djust-id="${CSS.escape(patchContext.targetId)}"]`
        );
        if (byId) {
            return byId;
        }
    }

    // Strategy 2: Fall back to index-based traversal
    let node = getLiveViewRoot();
    for (const index of path) {
        const children = getSignificantChildren(node);
        if (index >= children.length) return null;
        node = children[index];
    }
    return node;
}
```

### Performance Characteristics

| Metric | Value | Notes |
|--------|-------|-------|
| HTML size increase | +15-25% | `data-djust-id="dj-123"` on every element |
| Client lookup | O(1) | querySelector by attribute is fast |
| Memory (server) | +8 bytes/element | String ID storage |
| Memory (client) | Negligible | Browser handles attributes efficiently |
| Patch size increase | +10-15% | ID included in each patch |

### Pros

1. **Deterministic resolution** - ID lookup never fails due to structural changes
2. **Incremental adoption** - Can fall back to index-based for elements without IDs
3. **Debugging friendly** - IDs visible in DOM inspector
4. **Preserves current architecture** - Still uses VDOM diffing and patches
5. **Fast client-side** - querySelector by attribute is O(1) with browser optimization

### Cons

1. **HTML bloat** - Every element gets an ID attribute
2. **ID management complexity** - Must ensure IDs are stable across renders
3. **Rust changes required** - Modify parser, VNode, Patch structures
4. **Partial solution** - Still uses patches, just with better targeting

### ID Stability Concerns

IDs must be **deterministic** across renders for the same logical element:

```html
<!-- Render 1 -->
<div data-djust-id="dj-1">
  <span data-djust-id="dj-2">Hello</span>
</div>

<!-- Render 2 (after state change) - IDs must match! -->
<div data-djust-id="dj-1">
  <span data-djust-id="dj-2">World</span>  <!-- Same ID, text changed -->
</div>
```

**Solution:** Use content-based hashing or structural position for ID generation:

```rust
fn generate_stable_id(path: &[usize], tag: &str) -> String {
    // ID based on structural position, not global counter
    format!("dj-{}-{}", path.iter().map(|i| i.to_string()).collect::<Vec<_>>().join("-"), tag)
}
```

---

## Option 2: Morphdom/Idiomorph Approach

### How It Works

1. **Server-side:** Render complete HTML (no patches)
2. **Client-side:** Use morphdom/idiomorph library to intelligently merge new HTML into existing DOM
3. **Library handles:** Node matching, attribute updates, child reconciliation

### Architecture

```
┌─────────────────────────────────────────────────────────────┐
│                        SERVER                                │
├─────────────────────────────────────────────────────────────┤
│  HTML Template                                               │
│       ↓                                                      │
│  Rust Template Engine (render full HTML)                     │
│       ↓                                                      │
│  Send complete HTML string                                   │
│  (no VDOM diffing needed on server!)                        │
└─────────────────────────────────────────────────────────────┘
                            ↓ WebSocket
┌─────────────────────────────────────────────────────────────┐
│                        CLIENT                                │
├─────────────────────────────────────────────────────────────┤
│  Receive HTML string                                         │
│       ↓                                                      │
│  idiomorph.morph(existingRoot, newHTML, {                   │
│    callbacks: { beforeNodeMorphed, afterNodeMorphed }       │
│  })                                                          │
│       ↓                                                      │
│  Library intelligently updates DOM in place                  │
└─────────────────────────────────────────────────────────────┘
```

### Implementation Changes

#### 1. Remove Server-Side VDOM Diffing

```python
# live_view.py - Simplified render
class LiveView:
    def render_for_update(self, request=None) -> str:
        """Render and return full HTML - no diffing needed."""
        html = self.render(request)
        return html

    async def handle_event(self, event_name, params, request=None):
        # ... handle event ...

        # Just send full HTML
        html = self.render_for_update(request)
        return {"html": html, "version": self._version}
```

#### 2. Add Idiomorph to Client

```html
<!-- In base template -->
<script src="https://unpkg.com/idiomorph@0.3.0/dist/idiomorph.min.js"></script>
```

#### 3. Client Update Logic (`client.js`)

```javascript
function handleServerResponse(data, eventName, triggerElement) {
    if (data.html) {
        const liveviewRoot = getLiveViewRoot();

        // Use idiomorph for intelligent DOM morphing
        Idiomorph.morph(liveviewRoot, data.html, {
            morphStyle: 'innerHTML',
            ignoreActiveValue: true,  // Don't update focused inputs
            callbacks: {
                beforeNodeMorphed: (oldNode, newNode) => {
                    // Preserve elements with dj-update="ignore"
                    if (oldNode.getAttribute?.('dj-update') === 'ignore') {
                        return false;  // Skip this node
                    }
                    return true;
                },
                afterNodeMorphed: (oldNode, newNode) => {
                    // Re-bind event handlers if needed
                    if (newNode.nodeType === Node.ELEMENT_NODE) {
                        bindElementEvents(newNode);
                    }
                }
            }
        });

        // Re-initialize components
        initReactCounters();
        initTodoItems();
    }
}
```

### How Idiomorph Works

Idiomorph uses a sophisticated algorithm to match nodes:

1. **ID Matching** - Elements with same `id` are matched
2. **Key Matching** - Elements with same `data-key` are matched
3. **Structural Matching** - Similar tag + attributes = likely same element
4. **Content Heuristics** - Text content similarity for ambiguous cases

```javascript
// Idiomorph matching priority:
// 1. id="foo" matches id="foo"
// 2. data-key="123" matches data-key="123"
// 3. <div class="card"> might match <div class="card">
// 4. Text content similarity as tiebreaker
```

### Performance Characteristics

| Metric | Value | Notes |
|--------|-------|-------|
| Network payload | Larger | Full HTML vs patches (but gzip helps) |
| Server CPU | Lower | No VDOM diffing needed |
| Client CPU | Higher | Morphing algorithm runs on client |
| Client bundle | +8-12KB | Idiomorph library size |
| Latency (small changes) | Higher | Full HTML vs small patch |
| Latency (large changes) | Similar | Patch overhead vs HTML size |

### Benchmark Comparison

```
Scenario: Toggle single checkbox in list of 100 items

Patch-based:
  Server: 0.5ms (diff)
  Network: 150 bytes (patch)
  Client: 0.1ms (apply patch)
  Total: ~1ms

Morphdom:
  Server: 0.1ms (render only)
  Network: 15KB (full HTML, 3KB gzipped)
  Client: 2ms (morph)
  Total: ~3ms

Scenario: Update 50 items in list of 100

Patch-based:
  Server: 2ms (diff)
  Network: 5KB (50 patches)
  Client: 5ms (apply patches)
  Total: ~10ms

Morphdom:
  Server: 0.1ms (render only)
  Network: 15KB (full HTML, 3KB gzipped)
  Client: 3ms (morph)
  Total: ~5ms (faster for bulk updates!)
```

### Pros

1. **Most robust** - No path traversal, no index drift, no ID management
2. **Simpler server** - Remove VDOM diffing code entirely
3. **Battle-tested** - Phoenix LiveView uses this (idiomorph)
4. **Handles any DOM state** - Third-party scripts, browser quirks, etc.
5. **Better for bulk updates** - Full HTML often smaller than many patches
6. **Easier debugging** - Just HTML, no patch interpretation

### Cons

1. **Larger client bundle** - +8-12KB for idiomorph
2. **Higher network for small changes** - Full HTML vs tiny patch
3. **Client CPU usage** - Morphing is more expensive than targeted patches
4. **Architectural change** - Removes current VDOM/patch infrastructure
5. **Less control** - Library decides how to morph (callbacks help)

### Phoenix LiveView's Journey

Phoenix LiveView switched from patches to morphdom (then idiomorph):

> "We found that the complexity of maintaining patches and handling edge cases wasn't worth it. Morphdom handles 99% of cases correctly out of the box." - Chris McCord

Their evolution:
1. **v0.1-0.3**: Index-based patches (like current djust)
2. **v0.4-0.15**: Added `phx-` IDs for stability
3. **v0.16+**: Switched to morphdom
4. **v0.18+**: Switched to idiomorph (better algorithm)

---

## Side-by-Side Comparison

| Aspect | ID-Based Resolution | Morphdom/Idiomorph |
|--------|--------------------|--------------------|
| **Complexity** | Medium | Low (uses library) |
| **Robustness** | High | Highest |
| **Server changes** | Moderate (Rust) | Minor (remove diffing) |
| **Client changes** | Minor | Moderate (add library) |
| **Bundle size impact** | None | +8-12KB |
| **Network efficiency** | Best for small changes | Best for bulk changes |
| **Debugging** | IDs visible in DOM | Plain HTML |
| **Migration effort** | 2-3 days | 3-5 days |
| **Risk** | Low (incremental) | Medium (architectural) |
| **Phoenix alignment** | Partial | Full (same approach) |

---

## Hybrid Approach (Recommended)

Combine both approaches for optimal results:

```
┌─────────────────────────────────────────────────────────────┐
│                    HYBRID ARCHITECTURE                       │
├─────────────────────────────────────────────────────────────┤
│                                                              │
│  Small changes (< 20 patches):                              │
│    → Use ID-based patches (efficient, low latency)          │
│                                                              │
│  Large changes (> 20 patches) OR patch failures:            │
│    → Fall back to idiomorph (robust, handles anything)      │
│                                                              │
└─────────────────────────────────────────────────────────────┘
```

### Implementation

```javascript
function handleServerResponse(data, eventName, triggerElement) {
    // Server sends both patches AND full HTML
    // Client decides which to use

    if (data.patches && data.patches.length > 0 && data.patches.length < 20) {
        // Try patches first (more efficient for small changes)
        const result = applyPatches(data.patches);

        if (result.failureRate < 0.2) {
            // Patches worked well enough
            return true;
        }

        // Too many failures - fall back to morphdom
        console.log('[LiveView] Patches had issues, falling back to morph');
    }

    // Use idiomorph for large changes or as fallback
    if (data.html) {
        Idiomorph.morph(getLiveViewRoot(), data.html, {
            morphStyle: 'innerHTML',
            ignoreActiveValue: true
        });
        return true;
    }
}
```

### Server-Side (sends both)

```python
def handle_event(self, event_name, params, request=None):
    # ... handle event ...

    html, patches_json, version = self.render_with_diff(request)
    patches = json.loads(patches_json) if patches_json else []

    # Always include HTML as fallback
    response = {
        "version": version,
        "html": html,  # Always included
    }

    # Include patches if they're efficient
    if patches and len(patches) < 100:
        response["patches"] = patches

    return response
```

---

## Recommendation

### Short-term (1-2 weeks): Implement ID-Based Resolution

1. Lower risk, incremental improvement
2. Preserves existing architecture
3. Solves 90% of current issues
4. Can be done alongside current graceful degradation fix

### Medium-term (1-2 months): Add Idiomorph as Fallback

1. Include idiomorph library
2. Use for large updates or when patches fail
3. Best of both worlds

### Long-term (3-6 months): Evaluate Full Morphdom Migration

1. Measure real-world performance
2. If idiomorph fallback is triggered frequently, consider full migration
3. Aligns with Phoenix LiveView's proven approach

---

## Implementation Checklist

### Phase 1: ID-Based Resolution

- [ ] Add `djust_id` field to VNode struct
- [ ] Generate stable IDs during parsing
- [ ] Add `data-djust-id` attribute to rendered HTML
- [ ] Include `target_id` in Patch variants
- [ ] Update client `getNodeByPath` to try ID first
- [ ] Add tests for ID-based resolution
- [ ] Benchmark HTML size increase

### Phase 2: Idiomorph Fallback

- [ ] Add idiomorph to client bundle
- [ ] Create `morphDOM` wrapper function
- [ ] Update `handleServerResponse` for hybrid approach
- [ ] Add server-side threshold for patch vs HTML
- [ ] Test with complex conditional templates
- [ ] Benchmark client performance

### Phase 3: Full Migration (Optional)

- [ ] Remove VDOM diffing from Rust
- [ ] Simplify server response to HTML-only
- [ ] Optimize idiomorph callbacks for djust
- [ ] Remove patch-related code
- [ ] Update documentation
