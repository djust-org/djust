# Client-Side Features

djust provides several client-side directives for common UI patterns. These attributes can be added to any HTML element to enable reactive behavior without custom JavaScript.

## dj-model — Two-Way Data Binding

Automatically sync form input values with server state:

```html
<!-- Text input -->
<input type="text" dj-model="search_query" placeholder="Search...">

<!-- Select -->
<select dj-model="sort_by">
    <option value="name">Name</option>
    <option value="price">Price</option>
    <option value="date">Date</option>
</select>

<!-- Checkbox -->
<input type="checkbox" dj-model="show_archived">

<!-- Textarea -->
<textarea dj-model="notes"></textarea>
```

On the server, just define the attribute:

```python
class SearchView(LiveView):
    def mount(self, request, **kwargs):
        self.search_query = ""
        self.sort_by = "name"
        self.show_archived = False
```

### Modifiers

| Modifier | Description |
|----------|-------------|
| `dj-model.lazy` | Update on blur instead of every keystroke |
| `dj-model.debounce-500` | Debounce updates by 500ms |
| `dj-model.trim` | Trim whitespace from value |

```html
<!-- Update only on blur -->
<input dj-model.lazy="username">

<!-- Debounce by 300ms -->
<input dj-model.debounce-300="search">

<!-- Trim whitespace and debounce -->
<input dj-model.trim.debounce-500="query">
```

### Allowed Fields

For security, only existing view attributes can be set via `dj-model`. You can also restrict bindable fields:

```python
class MyView(LiveView):
    # Only these fields can be updated via dj-model
    allowed_model_fields = {"search_query", "sort_by", "page"}
```

## dj-confirm — Confirmation Dialogs

Show a browser `confirm()` dialog before sending an event:

```html
<button dj-click="delete_item" dj-confirm="Are you sure you want to delete this?">
    Delete
</button>

<button dj-click="reset_all" dj-confirm="This will reset all settings. Continue?">
    Reset to Defaults
</button>
```

If the user cancels, the event is not sent.

## dj-loading — Loading States

Show/hide elements or modify classes during event processing:

### Show Element While Loading

```html
<button dj-click="save">
    <span dj-loading.show style="display: none">Saving...</span>
    <span dj-loading.hide>Save</span>
</button>
```

### Add Class While Loading

```html
<button dj-click="save" dj-loading.class="opacity-50 cursor-wait">
    Save
</button>
```

### Disable Input While Loading

```html
<button dj-click="submit" dj-loading.disable>
    Submit
</button>

<input dj-input="search" dj-loading.disable>
```

### Remove Element While Loading

```html
<span dj-loading.remove>Click to load</span>
```

### Scope Loading States

Target loading states to specific events:

```html
<!-- Only shows during "save" event -->
<span dj-loading="save" dj-loading.show style="display: none">
    Saving...
</span>

<!-- Only disabled during "delete" event -->
<button dj-click="other_action" dj-loading="delete" dj-loading.disable>
    Other Action
</button>
```

## dj-transition — CSS Animations

Apply CSS transition classes when elements enter or leave the DOM.

### Named Transitions

```html
<div dj-transition="fade">
    Content that fades in/out
</div>
```

Built-in presets:
- `fade` — Opacity transition
- `slide-up` — Slide from below
- `slide-down` — Slide from above
- `scale` — Scale in/out

Add the CSS:

```css
/* Fade */
.fade-enter-from { opacity: 0; }
.fade-enter-to { opacity: 1; transition: opacity 0.3s; }
.fade-leave-from { opacity: 1; }
.fade-leave-to { opacity: 0; transition: opacity 0.3s; }

/* Slide up */
.slide-up-enter-from { opacity: 0; transform: translateY(20px); }
.slide-up-enter-to { opacity: 1; transform: translateY(0); transition: all 0.3s; }
.slide-up-leave-from { opacity: 1; transform: translateY(0); }
.slide-up-leave-to { opacity: 0; transform: translateY(-20px); transition: all 0.3s; }
```

### Explicit Classes

For more control, specify classes directly:

```html
<div dj-transition-enter="opacity-0 -translate-y-4"
     dj-transition-enter-to="opacity-100 translate-y-0 transition-all duration-300"
     dj-transition-leave="opacity-100"
     dj-transition-leave-to="opacity-0 transition-opacity duration-200">
    Animated content
</div>
```

### With Tailwind CSS

```html
<!-- Tailwind transition -->
<div dj-transition-enter="opacity-0 scale-95"
     dj-transition-enter-to="opacity-100 scale-100 transition-all duration-200 ease-out"
     dj-transition-leave="opacity-100 scale-100"
     dj-transition-leave-to="opacity-0 scale-95 transition-all duration-150 ease-in">
    Modal content
</div>
```

## dj-debounce — Debounce Events

Delay event firing until user stops typing/interacting:

```html
<!-- Wait 300ms after last keystroke before firing -->
<input dj-input="search" dj-debounce="300">

<!-- Debounce click events -->
<button dj-click="save" dj-debounce="1000">Save</button>

<!-- Debounce scroll events -->
<div dj-scroll="load_more" dj-debounce="200">...</div>
```

## dj-throttle — Throttle Events

Limit event frequency (fire at most once per interval):

```html
<!-- Fire at most every 100ms -->
<div dj-mousemove="track_cursor" dj-throttle="100">...</div>

<!-- Throttle scroll handler -->
<div dj-scroll="update_position" dj-throttle="50">...</div>
```

### Debounce vs Throttle

- **Debounce**: Wait for pause in activity. Good for search-as-you-type.
- **Throttle**: Fire at regular intervals. Good for scroll/resize/mousemove.

## dj-target — Scoped Updates

Limit DOM updates to a specific region:

```html
<input dj-input="search" dj-target="#search-results">

<div id="search-results">
    {% for item in results %}
        <div>{{ item.name }}</div>
    {% endfor %}
</div>
```

When `dj-target` is set, only the targeted element is re-rendered, reducing patch size.

### Multiple Targets

```html
<button dj-click="update_both" dj-target="#section-a, #section-b">
    Update Both
</button>
```

## dj-optimistic — Optimistic UI Updates

Apply client-side state changes immediately, with automatic rollback on error:

```html
<!-- Toggle boolean -->
<button dj-click="toggle_like" dj-optimistic="liked:!liked">
    {{ 'Unlike' if liked else 'Like' }}
</button>

<!-- Increment counter -->
<button dj-click="increment" dj-optimistic="count:count+1">
    +1 ({{ count }})
</button>

<!-- Decrement -->
<button dj-click="decrement" dj-optimistic="count:count-1">
    -1
</button>
```

If the server returns an error, the optimistic change is rolled back.

## Full Example: Search with Loading States

```python
class SearchView(LiveView):
    template_name = "search.html"

    def mount(self, request, **kwargs):
        self.query = ""
        self.results = []
        self.loading = False

    @event_handler
    def search(self, query="", **kwargs):
        self.query = query
        self.results = Product.objects.filter(name__icontains=query)[:20]
```

```html
<div class="search-container">
    <div class="search-box">
        <input type="text"
               dj-model="query"
               dj-input="search"
               dj-debounce="300"
               dj-loading.class="opacity-50"
               placeholder="Search products...">
        
        <span class="spinner" 
              dj-loading.show 
              style="display: none">
            🔄
        </span>
    </div>

    <div id="results" dj-target>
        {% if query %}
            <p class="result-count">
                {{ results|length }} results for "{{ query }}"
            </p>
        {% endif %}

        {% for product in results %}
            <div class="result-item" dj-transition="fade">
                <h3>{{ product.name }}</h3>
                <p>{{ product.description }}</p>
                <button dj-click="add_to_cart" 
                        dj-value-id="{{ product.id }}"
                        dj-confirm="Add {{ product.name }} to cart?"
                        dj-loading.disable>
                    Add to Cart
                </button>
            </div>
        {% empty %}
            {% if query %}
                <p class="no-results">No products found</p>
            {% endif %}
        {% endfor %}
    </div>
</div>

<style>
.spinner {
    animation: spin 1s linear infinite;
}
@keyframes spin {
    from { transform: rotate(0deg); }
    to { transform: rotate(360deg); }
}
</style>
```

## Full Example: Todo List with Optimistic UI

```python
class TodoView(LiveView):
    template_name = "todos.html"

    def mount(self, request, **kwargs):
        self.todos = list(Todo.objects.filter(user=request.user))

    @event_handler
    def toggle_complete(self, id, **kwargs):
        todo = Todo.objects.get(id=id, user=self.request.user)
        todo.completed = not todo.completed
        todo.save()
        # Reload todos
        self.todos = list(Todo.objects.filter(user=self.request.user))

    @event_handler
    def delete_todo(self, id, **kwargs):
        Todo.objects.filter(id=id, user=self.request.user).delete()
        self.todos = list(Todo.objects.filter(user=self.request.user))
```

```html
<ul class="todo-list">
    {% for todo in todos %}
    <li dj-transition="slide-up">
        <label>
            <input type="checkbox"
                   {% if todo.completed %}checked{% endif %}
                   dj-click="toggle_complete"
                   dj-value-id="{{ todo.id }}">
            <span class="{% if todo.completed %}completed{% endif %}">
                {{ todo.title }}
            </span>
        </label>
        
        <button dj-click="delete_todo"
                dj-value-id="{{ todo.id }}"
                dj-confirm="Delete this todo?"
                dj-loading.class="opacity-50">
            🗑️
        </button>
    </li>
    {% endfor %}
</ul>
```

## Tips

1. **Combine directives**: Most directives can be combined:
   ```html
   <input dj-model="query" dj-input="search" dj-debounce="300" dj-loading.class="loading">
   ```

2. **Use transitions for lists**: Add `dj-transition` to list items for smooth add/remove animations.

3. **Scope loading states**: Use `dj-loading="event_name"` to scope loading indicators to specific events.

4. **Debounce search inputs**: Always debounce search-as-you-type to reduce server load.

5. **Optimistic updates for toggles**: Use `dj-optimistic` for instant feedback on boolean toggles.
