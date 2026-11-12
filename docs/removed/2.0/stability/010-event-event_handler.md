### `@event` → `@event_handler`

`@event` was a deprecated alias for `@event_handler`, with identical behavior.
Migration is a mechanical rename:

```python
# Before
from djust.decorators import event

@event
def increment(self):
    self.count += 1

# After
from djust.decorators import event_handler

@event_handler
def increment(self):
    self.count += 1
```
