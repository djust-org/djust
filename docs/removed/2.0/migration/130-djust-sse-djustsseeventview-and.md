---
table: python
removed: `djust.sse.DjustSSEEventView` and the `sse/<session_id>/event/` route
use: `DjustSSEMessageView` at `sse/<session_id>/message/`, which the bundled client already uses
---
