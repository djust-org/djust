- **BREAKING: `enable_state_snapshot`.** A legacy view could set it to have its
  whole public and private state saved to the session after every WebSocket or
  SSE event (with a coalesced trailing save on teardown) and restored in place
  of `mount()` when the socket reconnected; a sticky child of such a page was
  saved under its sticky key. The attribute, that save, that restore and the
  sticky-child persistence are removed. A view that sets the attribute to a
  true value is refused with `ImproperlyConfigured` when the class is created
  and again on the mount path (an instance attribute, a class attribute
  assigned later, a mixin whose `__init_subclass__` skips `super()`). Declare
  the fields that must survive a reconnect with `state(..., persist="server")`.
  The HTTP page-POST fallback still restores a legacy view's session state.
  See [Migrating to 2.0](docs/website/guides/migrating-to-2.0.md#python-names).
