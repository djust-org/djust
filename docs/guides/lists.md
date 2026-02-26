# Rendering Dynamic Lists

This guide covers how to render and update dynamic lists efficiently in djust, including
keyed diffing with `dj-key` for identity-stable DOM updates.

## Basic List Rendering

Render a list the same way you would in any Django template:

```html
<ul>
  {% for item in items %}
    <li>{{ item.name }}</li>
  {% endfor %}
</ul>
```

When `items` changes, djust re-renders the template and sends a minimal VDOM patch to the
client. Without keying, elements are matched by their position in the list. This is fine
for simple, static-order lists.

## Keyed Diffing with `dj-key`

For lists where items can be reordered, inserted in the middle, or removed, use `dj-key`
to tell the VDOM differ which element represents which logical item. This preserves element
state (focus, animations, input values) across updates.

```html
<ul>
  {% for item in items %}
    <li dj-key="{{ item.pk }}">{{ item.name }}</li>
  {% endfor %}
</ul>
```

`dj-key` values must be unique among siblings. Use the item's primary key or any stable
string identifier.

### When to use `dj-key`

Use `dj-key` when:

- Items can be reordered (e.g. drag-and-drop, sorted feeds)
- Items are inserted or removed from arbitrary positions
- Each item has interactive state you want preserved (focused inputs, expanded details, etc.)
- The list can grow large (keyed lookup is O(1) vs O(n) positional matching)

You can skip `dj-key` for simple append-only lists or lists whose order never changes.

### `data-key` is also accepted

The legacy `data-key` attribute works identically to `dj-key` and is retained for
compatibility:

```html
<li data-key="{{ item.pk }}">{{ item.name }}</li>
```

Both `dj-key` and `data-key` are explicit opt-ins. The element's `id=` attribute is
**not** used as a diff key (see [MIGRATION.md](../../MIGRATION.md) for the v1.0 change).

## Efficient Appending with `dj-update`

For append-only lists such as chat messages or activity feeds, combine `dj-key` with
`dj-update="append"` and `temporary_assigns` to avoid re-sending the entire list on every
update:

```python
class ChatView(LiveView):
    def mount(self, request, **kwargs):
        self.messages = Message.objects.order_by('-created_at')[:50]

    def get_context_data(self, **kwargs):
        return {'messages': self.messages}

    @event_handler
    def send_message(self, text: str = "", **kwargs):
        msg = Message.objects.create(text=text, author=request.user)
        self.messages = [msg]  # Only the new message; dj-update="append" adds it

    def temporary_assigns(self):
        return ['messages']   # Reset after each render
```

```html
<ul id="message-list" dj-update="append">
  {% for msg in messages %}
    <li dj-key="{{ msg.pk }}">{{ msg.text }}</li>
  {% endfor %}
</ul>
```

`dj-update="append"` makes the client add new children to the bottom without touching
existing ones. `temporary_assigns` keeps the server-side list small.

## Full Example: Sortable Todo List

```python
class TodoView(LiveView):
    def mount(self, request, **kwargs):
        self.todos = list(Todo.objects.order_by('position'))

    @event_handler
    def move_item(self, item_id: int = 0, new_position: int = 0, **kwargs):
        todo = Todo.objects.get(pk=item_id)
        todo.position = new_position
        todo.save()
        self.todos = list(Todo.objects.order_by('position'))
```

```html
<ul>
  {% for todo in todos %}
    <li dj-key="{{ todo.pk }}"
        dj-click="move_item"
        data-item-id:int="{{ todo.pk }}"
        data-new-position:int="{{ todo.position }}">
      {{ todo.title }}
    </li>
  {% endfor %}
</ul>
```

With `dj-key`, the VDOM differ moves existing `<li>` DOM nodes into their new positions
instead of destroying and recreating them, preserving any attached event listeners and
element state.

## Summary

| Scenario | Recommendation |
|---|---|
| Static order list | No keying needed |
| Items reordered or inserted | `dj-key="{{ item.pk }}"` |
| Append-only feed | `dj-update="append"` + `temporary_assigns` |
| Legacy `data-key` usage | Continues to work; no migration needed |
| Old `id=` implicit key (pre-1.0) | Add explicit `dj-key=` — see MIGRATION.md |
