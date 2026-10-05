# State Management

> **`@optimistic` is INERT.** It records handler metadata that nothing in the
> shipped client reads, so applying it changes nothing at runtime — a bare
> `@optimistic` declares no DOM change for a client to apply. Examples below
> that use it still work, but without the optimistic-update behaviour they
> describe. Tracked in issue #2699.
>
> `@debounce` and `@throttle` ARE implemented (#2656) — the client gate is
> `static/djust/src/05-handler-rate-limit.js`, configured from the mount
> frame's `handler_config`.


djust's state management decorators replace patterns that traditionally require JavaScript — debouncing, throttling, loading indicators, optimistic updates, caching, and more. All in Python.

## Quick Decision Guide

```
User is typing?           → @debounce(wait=0.5)
Rapid scroll/resize?      → @throttle(interval=0.1)
Need instant UI feedback? → dj-loading.* attributes (@optimistic is INERT, #2699)
Same query repeated?      → @cache(ttl=300)
Coordinating components?  → one handler, one re-render (@client_state is INERT, #2680)
Auto-save forms?          → DraftModeMixin
```

## Debounce

Wait until the user stops typing before querying the server:

```python
from djust.decorators import event_handler, debounce

@event_handler()
@debounce(wait=0.5)  # 500ms after last keystroke
def search(self, value: str = "", **kwargs):
    self.results = Product.objects.filter(name__icontains=value)[:20]
```

`dj-input` already debounces text fields by 300 ms on the client. `@debounce` sets a handler-level delay on top of that (here 500 ms), so only one request fires per typing pause — typical for search boxes.

## Throttle

Limit how often a handler can fire — useful for scroll, resize, or other high-frequency events:

```python
from djust.decorators import event_handler, throttle

@event_handler()
@throttle(interval=0.1)  # Max 10 calls/second
def on_scroll(self, position: int = 0, **kwargs):
    self.scroll_position = position
```

## Loading States

Show feedback while a slow handler is running. This is **not a decorator** —
it is a declarative attribute on the element that fires the event:

```html
<button dj-click="save" dj-loading.disable>Save</button>
```

djust disables the button for the duration of the request. There is no
handler code and no state to manage.

For spinners, show/hide, and CSS classes, see
[Loading States & Background Work](../guides/loading-states.md).

## Optimistic Updates

`@optimistic` is **inert at rc10** (#2699): it is recorded but has no client
implementation, so no optimistic update is applied and nothing is rolled back.
The handler below behaves exactly as it would undecorated — the UI updates
when the server re-render arrives:

```python
from djust.decorators import event_handler, optimistic

@event_handler()
@optimistic  # inert: no effect at runtime
def toggle_like(self, item_id: int = 0, **kwargs):
    item = next(i for i in self.items if i["id"] == item_id)
    item["liked"] = not item["liked"]
```

For instant feedback today, use `dj-loading.*` attributes (see
[Loading States](#loading-states)) or JS Commands.

## Caching

Cache handler results to avoid redundant queries:

```python
from djust.decorators import event_handler, cache

@event_handler()
@cache(ttl=300, key_params=["value"])  # Cache for 5 minutes per unique value
def search(self, value: str = "", **kwargs):
    self.results = Product.objects.filter(name__icontains=value)[:20]
```

A search for "laptop" costs one DB query; subsequent searches for "laptop" within 5 minutes are free.

## Composing Decorators

Decorators compose — apply multiple to one handler. Their order does not change behaviour: each one records configuration that the client reads. If both `@debounce` and `@throttle` are present, `@debounce` wins.

```python
@event_handler()
@debounce(wait=0.5)   # wait for typing to stop
@cache(ttl=60)        # reuse a cached response for a repeated value
def search(self, value: str = "", **kwargs):
    self.results = Product.objects.filter(name__icontains=value)[:20]
```

## Client State

> **`@client_state` is INERT (#2680).** It stamps metadata nothing in the
> shipped client reads, so the handler below behaves exactly as it would
> undecorated. Nothing is stored client-side and nothing is synced; the
> `StateBus` this section used to describe was deleted in #2680.
> To coordinate two values today, set both in one handler — the single
> server re-render carries both.


```python
from djust.decorators import event_handler, client_state

@event_handler()
@client_state(keys=["filter", "sort"])
def update_filter(self, filter: str = "all", **kwargs):
    self.filter = filter
    self._refresh()
```

The decorator publishes nothing. The page updates because the handler's
assignments trigger djust's normal server re-render.

## DraftModeMixin

Auto-save form input to localStorage so users don't lose work on navigation or accidental close:

```python
from djust import DraftModeMixin, LiveView

class ContactFormView(DraftModeMixin, LiveView):
    template_name = "contact.html"
    draft_key = "contact_form"  # optional; defaults to "<classname>_draft"
```

The template opts in: mark a container with `data-draft-enabled` and
`data-draft-key`, and each field to save with `data-draft="true"`:

```html
<form dj-submit="save" data-draft-enabled data-draft-key="{{ draft_key }}" {% if draft_clear %}data-draft-clear{% endif %}>
  <input name="name" data-draft="true">
  <input name="email" data-draft="true">
  <textarea name="message" data-draft="true"></textarea>
  <button type="submit">Send</button>
</form>
```

The draft is restored when the user returns to the page. Drafts have no
expiry; call `self.clear_draft()` after a successful save to remove it.

### Fields that appear later

A field is saved and restored from the moment it enters the page, not only the
ones present at load: a field a patch inserts (an `{% if %}` that turns true, a
wizard step), a field the server replaces with a new element, and a draft root
inside a `dj-lazy` view that hydrates later.

**The saved draft wins over the server-rendered value** on a field's first
appearance, at page load and for a field that appears later, exactly as it does
for a field present at load. After that the draft is never put back: not over a
field the user is typing in (focused, or already edited), not after the user
clears it, and not when an unrelated view mounts or the connection reconnects, so
a value the server sets later is kept. A morph that reuses one input under a new
`name` (a wizard step) counts as a new field. A `dj-lazy` view restores its draft
when it first mounts; its re-mount after a reconnect keeps the server's values.

### What a draft holds

Each draft root saves the fields inside it under its own `data-draft-key`. A save
is the fields on the page now merged over what is already stored for the key, so
a field that is not on the page at the moment, such as an earlier wizard step,
keeps its saved value and is restored when that step shows again. Those values,
and the keys of fields you later remove from the template, stay until
`self.clear_draft()` (a successful submit) removes the whole draft. **Nothing
else clears a draft: not logging out, not signing in as someone else, not
leaving the page.** On a shared browser the next user sees the previous user's
draft unless you call `clear_draft()` or key the draft per user by overriding
`get_draft_key()` (`draft_key` is a fixed class attribute, the same for every
user).

- Two roots with the same `data-draft-key` write the same draft and overwrite
  each other's fields of the same name. Give each root its own key.
- A draft is stored as plain text in `localStorage`. This includes a field you
  mark `data-draft="true"` that is a password: do not mark one.
- A `type="file"` field is neither saved nor restored.

## Debugging Decorators

Enable client-side logging to see decorator behavior:

```javascript
window.djustDebug = true;
```

For example, a `@cache` hit logs:

```
[LiveView:cache] Cache hit: <cache key>
```

## Full Reference

| Decorator                 | Parameters                              | Use case             |
| ------------------------- | --------------------------------------- | -------------------- |
| `@debounce(wait)`         | `wait`: seconds (float)                 | Search, autosave     |
| `@throttle(interval)`     | `interval`: seconds (float)             | Scroll, resize       |
| `@background`             | —                                       | API calls, AI gen    |
| `@optimistic`             | —                                       | *(INERT — no client impl, #2699)* |
| `@cache(ttl, key_params)` | `ttl`: seconds, `key_params`: list[str] | Expensive queries    |
| `@client_state(keys)`     | `keys`: list[str]                       | *(INERT — no client impl, #2680)* |
| `DraftModeMixin`          | `draft_enabled`, `draft_key`            | Auto-save forms      |

For detailed API docs, see [API Reference: Decorators](../api-reference/decorators.md).

## More Resources

- [State Management Tutorial](../../state-management/STATE_MANAGEMENT_TUTORIAL.md) — step-by-step product search example
- [Patterns & Best Practices](../../state-management/STATE_MANAGEMENT_PATTERNS.md) — common patterns and anti-patterns
- [Examples](../../state-management/STATE_MANAGEMENT_EXAMPLES.md) — copy-paste ready examples
