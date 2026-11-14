### `event_security` `"open"` and `"warn"` → `@event_handler` on every handler

`"open"` let a client event call any public method of a view; `"warn"` let it
and logged a deprecation warning. Neither applied to the page-POST fallback,
which always refused an undecorated method, so the same event behaved
differently by transport. djust `2.0.0` removed both modes (#4101): every
transport runs one check (`websocket_utils._check_event_security`) and refuses
a method without `@event_handler`, naming the method and the decorator in the
`DEBUG` error. A project that still sets `LIVEVIEW_CONFIG['event_security']`
(or `DJUST_CONFIG['event_security']`) to anything but `"strict"` fails startup
with `ImproperlyConfigured` and the replacement. `"strict"` is accepted and
does nothing; delete it when convenient.

```python
# Before (removed in 2.0, #4101)
LIVEVIEW_CONFIG = {"event_security": "warn"}

class Counter(LiveView):
    def mount(self, request, **kwargs):
        self.count = 0

    def increment(self, **kwargs):  # ran under "open" / "warn"
        self.count += 1

# After
from djust.decorators import event_handler

class Counter(LiveView):
    def mount(self, request, **kwargs):
        self.count = 0

    @event_handler
    def increment(self, **kwargs):
        self.count += 1
```

`strict` has been the default since 0.2.1, so a project that never set the
key is unaffected. System check `djust.T019` now reports every template
binding to an undecorated method, which it used to skip under the two modes.
