# Template Directives

djust templates are standard Django templates with `dj-*` attributes for reactivity.

What a template can read (explicit exposure, the default): fields declared with
`state()` (unless `render=False`), what `get_context_data()` returns (which wins
over a declared field of the same name), framework providers and Django context
processors. Any other attribute on `self` renders empty. Rendering is not a
browser grant: `client=True` and `persist=` stay separate.

```python
from djust import LiveView, event_handler, state

class CounterView(LiveView):
    template = "<div dj-root><p>{{ count }}</p><button dj-click='inc'>+</button></div>"
    count = state(0)  # renders as {{ count }}; no get_context_data() needed

    @event_handler()
    def inc(self):
        self.count += 1
```

```html
<!-- Click events -->
<button dj-click="increment">+1</button>
<button dj-click="delete" data-item-id="{{ item.id }}">Delete</button>

<!-- Text input (input event, debounced 300 ms by default) -->
<input type="text" dj-input="search" value="{{ query }}" />

<!-- Change (fires on blur / select change) -->
<select dj-change="filter_status">
    <option value="all" {% if status == "all" %}selected{% endif %}>All</option>
    <option value="active" {% if status == "active" %}selected{% endif %}>Active</option>
</select>

<!-- Form submission -->
<form dj-submit="save_form">
    {% csrf_token %}
    <input name="title" type="text" value="{{ title }}" />
    <input name="email" type="email" />
    <button type="submit">Save</button>
</form>

<!-- Keyboard events -->
<input dj-keydown.enter="submit" dj-keydown.escape="cancel" />

<!-- Hooks (JS lifecycle: mounted/updated/destroyed) -->
<div dj-hook="chart" id="my-chart"></div>

<!-- Skip re-rendering a subtree -->
<div dj-update="ignore">User-managed DOM</div>

<!-- Keyed lists for optimal diffing -->
{% for item in items %}
<div data-key="{{ item.id }}">{{ item.name }}</div>
{% endfor %}
```

Notes:
- `dj-input` sends `value` parameter to handler on the `input` event; text fields are debounced 300 ms by default (`dj-debounce` / `dj-throttle` override, `dj-debounce="0"` sends every keystroke)
- `dj-change` sends `value` on blur or select change
- `dj-click` sends `data-*` attributes as handler params (kebab-case -> snake_case)
- `dj-submit` sends all named form fields as handler kwargs
- `data-key` on list items enables efficient keyed diffing (add it when items reorder)
- `{% csrf_token %}` required in all `dj-submit` forms
