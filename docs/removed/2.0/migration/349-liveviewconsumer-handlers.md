---
table: python
removed: `LiveViewConsumer.handle_event`, `LiveViewConsumer.handle_url_change`, `LiveViewConsumer.handle_mount` (now the private `_mount_page`) and `PushEventMixin._flush_pending_push_events`
use: events and URL changes reach a view through the runtime (`_dispatch_runtime_owned`); call `flush_push_events` instead of `_flush_pending_push_events`. A `LiveViewConsumer` subclass overriding one of these is not called ([#4207](https://github.com/djust-org/djust/issues/4207))
---
