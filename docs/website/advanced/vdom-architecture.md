---
title: "VDOM Architecture"
slug: vdom-architecture
section: advanced
order: 2
level: advanced
description: "How djust's Rust-powered Virtual DOM diffing and morphdom-style DOM patching deliver sub-millisecond updates."
---

# VDOM Architecture

djust uses a Rust-powered Virtual DOM to diff server-rendered HTML and send minimal patches to the browser over WebSocket. This architecture delivers sub-millisecond updates while keeping the developer-facing API in pure Python.

## Overview

```
Python LiveView           Rust VDOM (PyO3)           Browser
    |                          |                        |
    |-- render_with_diff() --> |                        |
    |                     parse HTML                    |
    |                     diff(old, new)                |
    |                     emit patches                  |
    |  <-- patches JSON ---   |                        |
    |                          |   --- WS patches --->  |
    |                          |              apply patches
    |                          |              (DOM morph)
```

## Rust Crate Structure

The VDOM lives in `crates/djust_vdom/` and is organized into three modules:

- **`parser.rs`** -- Parses HTML into a `VNode` tree using `html5ever`. Filters out HTML comment nodes and whitespace-only text nodes so the server VDOM matches the browser DOM.
- **`diff.rs`** -- Compares two `VNode` trees and emits a minimal list of `Patch` operations. Supports both indexed (positional) and keyed child diffing.
- **`patch.rs`** -- Applies patches to a `VNode` tree (used server-side in tests). The browser applies patches via JavaScript.

## The VNode Tree

Every element, text node, and attribute is represented as a `VNode`:

```rust
pub struct VNode {
    pub tag: Option<String>,       // "div", "span", etc. (None for text)
    pub text: Option<String>,      // Text content (None for elements)
    pub attrs: HashMap<String, String>,
    pub children: Vec<VNode>,
    pub key: Option<String>,       // For keyed list diffing
    pub djust_id: Option<String>,  // Compact base62 ID for O(1) lookup
}
```

Each element node receives a compact `djust_id` (base62-encoded, e.g. `"1a"`, `"2B"`) during parsing. These IDs are stamped as `data-dj-id` attributes in the HTML sent to the browser, enabling O(1) element lookup during patch application.

## Parsing: HTML to VNode

The parser converts server-rendered HTML into a VNode tree:

```rust
let vdom = parse_html("<div class=\"counter\"><span>0</span></div>");
```

Key behaviors during parsing:

1. **Comment filtering** -- `<!-- ... -->` nodes are skipped entirely, matching browser behavior where comments are not visible to JavaScript DOM traversal.
2. **Whitespace filtering** -- Text nodes containing only whitespace are dropped, preventing path misalignment between server and client.
3. **ID assignment** -- Every element gets a unique `djust_id` via a thread-local counter with base62 encoding.

## The Diff Algorithm

`diff()` compares old and new VNode trees top-down and emits patches:

```rust
let patches: Vec<Patch> = diff(&old_vdom, &new_vdom);
```

### Patch Types

| Patch         | Description                            |
| ------------- | -------------------------------------- |
| `SetText`     | Update a text node's content           |
| `SetAttr`     | Set or update an attribute             |
| `RemoveAttr`  | Remove an attribute                    |
| `Replace`     | Replace an entire node                 |
| `InsertChild` | Insert a new child at an index         |
| `RemoveChild` | Remove a child at an index             |
| `MoveChild`   | Move a child from one index to another |

Every patch carries both a `path` (index-based array) and a `d` (djust_id) field. The client tries ID-based resolution first for O(1) lookup, falling back to path traversal.

### Indexed vs. Keyed Diffing

By default, children are compared by position (indexed diffing). When children have `key` attributes (via `dj-key` in templates), the algorithm uses keyed diffing:

```html
<!-- Keyed list: moves are detected instead of replacements -->
{% for item in items %}
<li dj-key="{{ item.id }}">{{ item.name }}</li>
{% endfor %}
```

Keyed diffing maps old keys to new keys and emits `MoveChild` patches instead of remove-then-insert pairs, preserving DOM state (focus, scroll position, animations) across reorders.

### ID Synchronization

After diffing, `sync_ids()` copies old djust_ids to matched nodes in the new tree. This ensures subsequent diffs use IDs that match what the client currently has in its DOM. Only replaced nodes (tag mismatch) and newly inserted nodes keep their fresh IDs.

## Client-Side Patch Application

Patches are serialized as JSON and sent over WebSocket. The client-side JavaScript applies them in `12-vdom-patch.js`:

```javascript
// ID-based resolution (primary, O(1)):
const node = document.querySelector(`[data-dj-id="${CSS.escape(djustId)}"]`);

// Path-based traversal (fallback):
// Walks childNodes, filtering out comment and whitespace-only text nodes
// to match the server's filtered VNode tree.
```

### Patch Application Order

Child mutations are grouped by parent and applied in a specific order to keep indices stable:

1. **Removes** -- descending index order (highest index first)
2. **Inserts** -- ascending index order (lowest index first)
3. **Moves** -- resolved by `djust_id` of the child being moved

Attribute and text patches are applied last, using ID-based lookup when available.

## The Render-Diff Lifecycle

1. **Mount (GET or WebSocket connect)**: `render_with_diff()` is called to produce initial HTML and establish the VDOM baseline. The baseline is stored server-side.
2. **Event (WebSocket message)**: The handler updates state, then `render_with_diff()` re-renders. Rust diffs the new VNode tree against the stored baseline and emits patches.
3. **Patch delivery**: Patches are serialized to JSON and sent over WebSocket with a monotonically increasing `version` number.
4. **Client application**: The browser applies patches to the live DOM, updating only the changed nodes.
5. **Baseline update**: The new VNode tree becomes the baseline for the next diff.

## Template Preprocessing

Before the Rust VDOM parser sees the template, djust strips HTML comments and normalizes whitespace. This is critical because:

- The Rust parser filters comments and whitespace during parsing.
- The browser DOM includes these nodes.
- Stripping **before** baseline creation ensures server VDOM and client DOM are structurally identical.

```python
# In get_template(), BEFORE Rust VDOM baseline is created:
extracted = self._strip_comments_and_whitespace(extracted)
```

## Performance Characteristics

| Operation           | Typical Time  |
| ------------------- | ------------- |
| HTML parsing (Rust) | 0.1 - 0.5 ms  |
| VDOM diff (Rust)    | 0.05 - 0.2 ms |
| Patch serialization | < 0.1 ms      |
| Client patch apply  | 0.5 - 2 ms    |
| Total round-trip    | 2 - 10 ms     |

Targets for interactive updates:

- **Simple update** (text change): 1-2 patches, < 1 ms client-side
- **Form input**: 1-2 patches, < 1 ms
- **List update**: 5-20 patches, < 5 ms
- **Full refresh**: 50+ patches -- consider optimizing if > 10 ms

## Template Attributes

### `dj-root` — Reactive Boundary

`dj-root` marks the DOM subtree that djust manages. Only HTML inside `dj-root` is diffed and patched. Everything outside (static headers, navbars, footers) is never touched.

```html
<body dj-view="{{ dj_view_id }}">   {# Identifies WebSocket session #}
    <header>Static header — never patched</header>
    <div dj-root>
        {# Everything here is managed by the VDOM #}
        {{ count }}
        <button dj-click="increment">+</button>
    </div>
</body>
```

The VDOM baseline is established for the `dj-root` subtree at mount time. On every event, the Rust engine diffs the new render of this subtree against the stored baseline and emits minimal patches.

### `dj-key` / `data-key` — Stable List Identity

Without a key, children are compared by position (indexed diffing). Insertions and deletions cause cascading replacements for all subsequent siblings.

With `data-key` or `dj-key`, the diff algorithm uses keyed diffing: it maps old keys to new keys and emits `MoveChild` patches instead of remove-then-insert pairs. This preserves DOM state (input focus, scroll position, CSS animations) across reorders.

```html
{# Without key: position-based, produces extra mutations on reorder #}
{% for item in items %}
<div>{{ item.name }}</div>
{% endfor %}

{# With data-key: move-aware, O(n) keyed diff #}
{% for item in items %}
<div data-key="{{ item.id }}">{{ item.name }}</div>
{% endfor %}

{# dj-key is equivalent to data-key #}
{% for item in items %}
<li dj-key="{{ item.id }}">{{ item.name }}</li>
{% endfor %}
```

Keys must be unique within the parent element. Use the item's primary key or a stable string identifier.

### Opting Out of VDOM Patching

For subtrees managed by external JavaScript (chart libraries, rich text editors, map widgets), use `dj-update="ignore"` to prevent djust from patching them:

```html
<div dj-update="ignore" id="my-chart">
    {# Chart.js or similar owns this DOM — djust will not touch it #}
</div>
```

---

## Known Pitfalls

### One-Sided `{% if %}` in Attribute Values

djust's template preprocessor counts `{% if %}` / `{% endif %}` pairs to track div depth during rendering. When `{% if %}` appears inside an HTML **attribute value** without a matching `{% else %}`, the depth counter can fall out of sync, causing VDOM patching misalignment after the first render where the branch evaluates to false.

```html
{# WRONG: one-sided if inside class attribute #}
<div class="card {% if active %}active{% endif %}">
    ...
</div>
```

**Fix:** Use a full `{% if/else %}` pair inside the attribute, or move the conditional outside the tag:

```html
{# CORRECT: full if/else inside attribute #}
<div class="card {% if active %}active{% else %}{% endif %}">

{# ALSO CORRECT: conditional wraps the entire tag #}
{% if active %}
<div class="card active">
{% else %}
<div class="card">
{% endif %}
    ...
</div>
```

This limitation applies only to attribute values. `{% if %}` blocks in element text content work correctly.

### Form Field Value Preservation

djust's VDOM preserves the live value of `<input>`, `<textarea>`, and `<select>` elements during patches. If the user has typed into a field and the server re-renders that field with the same `value=` attribute, the user's draft is kept.

However, if the server sends a **different** `value=`, the server value wins. This is intentional — it allows server-side validation to correct invalid input.

To prevent djust from patching a form field entirely (e.g., a rich text editor), wrap it in `dj-update="ignore"`:

```html
<div dj-update="ignore">
    <textarea id="body"></textarea>
</div>
```

---

## Debugging

Enable VDOM tracing to see every diff decision:

```bash
DJUST_VDOM_TRACE=1 make start
```

This logs node comparisons, attribute changes, child diffing decisions, and generated patches to stderr. Use the Debug Panel's VDOM Patches tab for a visual view of patches applied in the browser.
