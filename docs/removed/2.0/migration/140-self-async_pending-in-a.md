---
table: python
covers: _async_pending
removed: `self._async_pending = (fn, args, kwargs)` in a handler
use: `self.start_async(fn, *args, **kwargs)`, or `@background` on the handler. Assigning the tuple raises `ImproperlyConfigured` when the handler runs, on every transport ([#4031](https://github.com/djust-org/djust/issues/4031)); search your code for `_async_pending` to find them before then.
---
