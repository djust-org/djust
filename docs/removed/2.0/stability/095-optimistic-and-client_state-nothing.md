### `@optimistic` and `@client_state` → nothing (they did nothing)

Both decorators only recorded metadata that nothing read: no client code
applied an optimistic update for `@optimistic`, and the client bus that was
to consume `@client_state` was deleted as dead code in #2680. A decorated
handler behaved exactly like an undecorated one. djust `2.0.0` removed both
(#4006). `from djust import optimistic`, `from djust.decorators import
client_state` and the attribute reads raise `ImportError` with a message that
names the replacement and this guide (so `hasattr()` raises rather than
returning `False`, as for the signed snapshot below). Delete the decorator; the
handler keeps working as before:

```python
# Before (removed in 2.0, #4006)
from djust.decorators import client_state, event_handler, optimistic

@optimistic
@event_handler
def increment(self, **kwargs):
    self.count += 1

@client_state(keys=["filter"])  # removed in 2.0, #4006
@event_handler
def update_filter(self, filter: str = "", **kwargs):
    self.filter = filter

# After
from djust.decorators import event_handler

@event_handler
def increment(self, **kwargs):
    self.count += 1

@event_handler
def update_filter(self, filter: str = "", **kwargs):
    self.filter = filter
```

For an update that shows in the browser before the server answers, use the
client-side `JS` commands (`dj-click` with a `JS` chain, or `optimistic_rules`
on a descriptor component). To keep two values in step, update both in one
handler; the one render sends both.

These were not deprecated in 1.3.x: no warning preceded the removal, because
the decorators had no behaviour a project could depend on.
