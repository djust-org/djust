# Client-Side Features

djust provides a rich set of template directives for building interactive UIs without writing JavaScript. These directives handle common patterns like data binding, loading states, confirmations, transitions, and more.

## dj-model — Two-Way Data Binding

Automatically sync form input values with server-side view attributes.

### Basic Usage

```html
<input type="text" dj-model="search_query" />
<textarea dj-model="description"></textarea>
<select dj-model="category">
    <option value="all">All</option>
    <option value="electronics">Electronics</option>
</select>
<input type="checkbox" dj-model="is_active" />
```

```python
class SearchView(LiveView):
    search_query = ""
    description = ""
    category = "all"
    is_active = False

    @event_handler
    def update_model(self, field, value, **kwargs):
        """Auto-called by dj-model"""
        setattr(self, field, value)
```

### Modifiers

| Modifier | Usage | Description |
|----------|-------|-------------|
| `.lazy` | `dj-model.lazy="field"` | Sync on blur instead of every keystroke |
| `.debounce-N` | `dj-model.debounce-300="field"` | Debounce by N milliseconds |
| `.trim` | `dj-model.trim="field"` | Trim whitespace |

```html
<!-- Sync on blur (not every keystroke) -->
<input dj-model.lazy="email" />

<!-- Debounce 500ms -->
<input dj-model.debounce-500="search_query" />

<!-- Combined -->
<textarea dj-model.lazy.trim="bio"></textarea>
```

## dj-confirm — Confirmation Dialogs

Show a browser `confirm()` dialog before sending an event. If cancelled, the event is not sent.

```html
<button dj-click="delete_item" 
        dj-confirm="Are you sure you want to delete this item?">
    Delete
</button>

<button dj-click="clear_all" 
        dj-confirm="This will remove all items. Continue?">
    Clear All
</button>
```

Works with `dj-click` only (not `dj-change`, `dj-input`).

## dj-loading — Loading States

Manage UI state while events are processing.

### Modifiers

| Modifier | Usage | Description |
|----------|-------|-------------|
| `.disable` | `dj-loading.disable` | Disable element while loading |
| `.class="..."` | `dj-loading.class="opacity-50"` | Add CSS classes while loading |
| `.show` | `dj-loading.show` | Show element only while loading |
| `.hide` / `.remove` | `dj-loading.hide` | Hide element while loading |

### Examples

```html
<!-- Disable button while processing -->
<button dj-click="save" dj-loading.disable>
    Save
</button>

<!-- Add opacity while loading -->
<div dj-loading.class="opacity-50 pointer-events-none">
    Content area
</div>

<!-- Show spinner only while loading -->
<span dj-loading.show style="display: none;">
    ⏳ Saving...
</span>

<!-- Hide content while loading -->
<div dj-loading.hide>
    Ready content
</div>

<!-- Custom display value when showing -->
<div dj-loading.show="flex" style="display: none;">
    <span>Loading...</span>
</div>
```

### Complete Example

```html
<form dj-submit="submit_form">
    <input name="email" dj-loading.disable />
    
    <button type="submit" dj-loading.disable>
        <span dj-loading.hide>Submit</span>
        <span dj-loading.show style="display: none;">Submitting...</span>
    </button>
</form>

<div dj-loading.class="blur-sm">
    Form content that blurs while loading
</div>
```

## dj-transition — CSS Transitions

Apply CSS transition classes when elements are added or removed from the DOM.

### Named Transitions

```html
<div dj-transition="fade">Content</div>
```

Applies classes:
- **Enter:** `fade-enter-from` → `fade-enter-to`
- **Leave:** `fade-leave-from` → `fade-leave-to`

### Built-in Presets

| Name | Effect |
|------|--------|
| `fade` | Opacity 0 → 1 |
| `slide-up` | Translate Y + fade |
| `slide-down` | Translate Y + fade |
| `scale` | Scale 0.95 → 1 + fade |

### Custom Classes

```html
<div dj-transition-enter="opacity-0 scale-95" 
     dj-transition-enter-to="opacity-100 scale-100"
     dj-transition-leave="opacity-100 scale-100" 
     dj-transition-leave-to="opacity-0 scale-95">
    Content
</div>
```

### Example CSS

```css
/* Fade transition */
.fade-enter-from,
.fade-leave-to {
    opacity: 0;
}
.fade-enter-to,
.fade-leave-from {
    opacity: 1;
}
.fade-enter-from,
.fade-enter-to,
.fade-leave-from,
.fade-leave-to {
    transition: opacity 0.3s ease;
}

/* Slide up transition */
.slide-up-enter-from {
    opacity: 0;
    transform: translateY(10px);
}
.slide-up-enter-to {
    opacity: 1;
    transform: translateY(0);
}
```

## dj-debounce — Debounce Events

Delay event sending until user stops interacting. Works with any event type.

```html
<!-- Debounce text input -->
<input dj-input="search" dj-debounce="300" />

<!-- Debounce clicks -->
<button dj-click="save" dj-debounce="1000">Save</button>

<!-- Debounce keydown -->
<input dj-keydown="validate" dj-debounce="500" />
```

The event is sent after `N` milliseconds of inactivity. Each new trigger resets the timer.

## dj-throttle — Throttle Events

Limit event frequency. Useful for scroll/resize handlers.

```html
<!-- Throttle scroll events -->
<div dj-scroll="handle_scroll" dj-throttle="100">
    Scrollable content
</div>

<!-- Throttle rapid clicks -->
<button dj-click="increment" dj-throttle="200">+1</button>
```

Events fire at most once per `N` milliseconds.

## dj-target — Scoped Updates

Specify which part of the DOM should be updated, reducing patch size for large pages.

### On Event Triggers

```html
<!-- Only update #search-results when searching -->
<input dj-input="search" dj-target="#search-results" />

<!-- Only update .comments section -->
<button dj-click="add_comment" dj-target=".comments">
    Add Comment
</button>
```

### On Containers

```html
<!-- Mark as update target -->
<div id="search-results" dj-target>
    {% for item in results %}
        <div>{{ item.name }}</div>
    {% endfor %}
</div>
```

### Benefits

- Smaller WebSocket payloads
- Faster VDOM diffing
- More predictable updates

## Complete Example

```html
<!-- Search with debounce, loading states, and scoped updates -->
<div class="search-page">
    <div class="search-bar">
        <input type="text" 
               dj-model="query"
               dj-input="search"
               dj-debounce="300"
               dj-target="#results"
               dj-loading.class="bg-gray-100"
               placeholder="Search..." />
        
        <span dj-loading.show style="display: none;">
            🔍 Searching...
        </span>
    </div>

    <div id="results" dj-target>
        {% if results %}
            {% for item in results %}
                <div class="result" dj-transition="fade">
                    <h3>{{ item.title }}</h3>
                    <p>{{ item.description }}</p>
                    <button dj-click="delete_item" 
                            dj-value-id="{{ item.id }}"
                            dj-confirm="Delete '{{ item.title }}'?"
                            dj-loading.disable>
                        Delete
                    </button>
                </div>
            {% endfor %}
        {% else %}
            <p class="empty" dj-transition="fade">No results found</p>
        {% endif %}
    </div>
</div>
```

```python
class SearchView(LiveView):
    template_name = "search.html"
    
    def mount(self, request, **kwargs):
        self.query = ""
        self.results = []

    @event_handler
    def search(self, **kwargs):
        if len(self.query) >= 2:
            self.results = Item.objects.filter(
                title__icontains=self.query
            )[:20]
        else:
            self.results = []

    @event_handler
    def delete_item(self, id, **kwargs):
        Item.objects.filter(id=id).delete()
        self.search()  # Refresh results
```

## dj-optimistic — Optimistic UI Updates

Apply client-side state updates immediately before server confirmation, with automatic rollback on errors.

```html
<!-- Toggle boolean -->
<button dj-click="toggle_like" dj-optimistic="liked:!liked">
    {{ liked|yesno:"❤️,🤍" }}
</button>

<!-- Increment counter -->
<button dj-click="increment" dj-optimistic="count:count+1">
    +1 ({{ count }})
</button>

<!-- Add class -->
<span dj-click="mark_read" dj-optimistic="class:read">
    {{ message }}
</span>
```

### Syntax

| Pattern | Description |
|---------|-------------|
| `field:value` | Set field to literal value |
| `field:!field` | Toggle boolean field |
| `class:classname` | Add CSS class |

Features:
- Immediate DOM updates for better perceived performance
- Automatic rollback if server returns error or different state
- Works with `dj-target` for scoped updates

## Directive Reference

| Directive | Description |
|-----------|-------------|
| `dj-model="field"` | Two-way data binding |
| `dj-model.lazy="field"` | Sync on blur |
| `dj-model.debounce-N="field"` | Debounced sync |
| `dj-confirm="message"` | Confirmation dialog |
| `dj-loading.disable` | Disable while loading |
| `dj-loading.class="cls"` | Add class while loading |
| `dj-loading.show` | Show while loading |
| `dj-loading.hide` | Hide while loading |
| `dj-transition="name"` | Named CSS transition |
| `dj-transition-enter="cls"` | Enter from classes |
| `dj-transition-enter-to="cls"` | Enter to classes |
| `dj-transition-leave="cls"` | Leave from classes |
| `dj-transition-leave-to="cls"` | Leave to classes |
| `dj-debounce="N"` | Debounce event by N ms |
| `dj-throttle="N"` | Throttle event to N ms |
| `dj-target="selector"` | Scope updates to selector |
| `dj-optimistic="field:value"` | Optimistic update |
