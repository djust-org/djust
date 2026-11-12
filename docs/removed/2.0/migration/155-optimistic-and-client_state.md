---
table: python
covers: djust.optimistic, djust.decorators.optimistic, djust.client_state, djust.decorators.client_state
removed: `@optimistic` and `@client_state(keys=...)` (`djust.optimistic`, `djust.client_state`, `djust.decorators`)
use: Delete the decorator; the handler behaves as before, because nothing read either one. Importing either raises `ImportError` naming this advice ([#4006](https://github.com/djust-org/djust/issues/4006)). For feedback before the server answers, use `dj-loading.*` attributes or JS Commands.
---
