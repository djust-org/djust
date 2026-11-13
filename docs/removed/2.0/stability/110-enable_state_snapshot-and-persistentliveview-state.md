### `enable_state_snapshot` and `PersistentLiveView` → `state(..., persist="server")`

A legacy view that set `enable_state_snapshot = True` (or subclassed
`PersistentLiveView`, which set it) had its whole public and private state
saved to the session after every WebSocket or SSE event, and restored in place
of `mount()` when the socket reconnected; a sticky child that also opted in
was saved under its sticky key. djust `2.0.0` removed the attribute, the class,
that save and restore, and system check `djust.V011`.

A view that still sets `enable_state_snapshot` to a true value is refused with
`ImproperlyConfigured`, when the class is created and again on the mount path
(an instance attribute, a class attribute assigned later, or a mixin whose
`__init_subclass__` skips `super()`). `False` is accepted. Importing
`PersistentLiveView` from `djust` or `djust.live_view` raises `ImportError`
naming the replacement. Declare the fields that must survive a reconnect:

```python
# Before (1.3)
class CartView(PersistentLiveView):
    def mount(self, request, **kwargs):
        self.items = []

# After
class CartView(LiveView):
    items = state([], persist="server")

    def get_context_data(self, **kwargs):
        return {"items": self.items}
```

`mount()` runs on every reconnect, then the declared fields come back and
`handle_restore()` runs. The HTTP page-POST fallback still restores a legacy
view's public and private state from the session between requests. `djust.C304` now warns about a `client=True` field with a
credential-like name.
