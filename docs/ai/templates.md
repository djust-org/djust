# Template Directives

djust templates are standard Django templates with `dj-*` attributes for reactivity.

```html
<!-- Click events -->
<button dj-click="increment">+1</button>
<button dj-click="delete" data-item-id="{{ item.id }}">Delete</button>

<!-- Text input (fires on every keystroke) -->
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
- `dj-input` sends `value` parameter to handler on each keystroke
- `dj-change` sends `value` on blur or select change
- `dj-click` sends `data-*` attributes as handler params (kebab-case -> snake_case)
- `dj-submit` sends all named form fields as handler kwargs
- `data-key` on list items enables efficient keyed diffing (add it when items reorder)
- `{% csrf_token %}` required in all `dj-submit` forms
