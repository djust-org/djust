### The legacy exposure and parameter policies → explicit exposure and strict parameters

djust `2.0.0` keeps one exposure policy and one parameter policy (#4199).
`exposure_policy = "legacy"` (the 1.x attribute walk) raises
`ImproperlyConfigured` when the class is created, and again on construction for
a value the class statement did not show.
`@event_handler(parameter_policy="legacy")` and
`@server_function(parameter_policy="legacy")` raise when the decorator applies.
`cancel_async_on_disconnect = True` raises when the class is created, and the
`LIVEVIEW_CONFIG` / `DJUST_CONFIG` keys `event_parameter_policy`,
`reauth_on_event` and `reauth_server_turn_interval` raise at startup. Each
error names the replacement. The system checks `djust.V028`, `djust.V032`,
`djust.C045` and `djust.V036`, which reported those spellings, are retired;
their IDs are not reused.

<!-- doc-snippet-check: anti-pattern -->
```python
# Before (removed: raises ImproperlyConfigured)
class Cart(LiveView):
    exposure_policy = "legacy"

    def mount(self, request, **kwargs):
        self.count = 0

    @event_handler(parameter_policy="legacy")
    def add(self, item_id):
        self.count += 1
```

```python
# After
class Cart(LiveView):
    count = state(0, persist="server")

    @event_handler
    def add(self, item_id: int):
        self.count += 1
```

The template sends `dj-value-item-id` instead of `data-item-id`.
`exposure_policy = "explicit"` and `parameter_policy="strict"` are accepted and
do nothing. `python manage.py djust_exposure_inventory --fix` reads 1.3 code
and makes the mechanical part of the change; it is the only code that may
import a module stating the removed spellings. A module imported while Django
starts (a URLconf, an `AppConfig.ready()`) is refused before the command runs,
so delete its line by hand.
